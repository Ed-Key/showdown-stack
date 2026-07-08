"""MCTSPlayer and MaxDamagePlayer: poke-env Player subclasses for battle testing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx
from poke_env.player import Player

from battle_testing.adapter import BattleAdapter

# Phase 2: imported lazily inside methods to avoid hard dep when belief
# tracking is off. _SPECTATOR_PROTOCOL_SKIP_REASONS is the set of skip
# tokens we sniff from raw protocol lines (per spec §5.5).
_SPEED_SKIP_REASONS = {
    "cant",
    "confusion",
    "custap",
    "quick_claw",
}


@dataclass
class MCTSConfig:
    """Configuration for the MCTS engine connection."""

    label: str = "default"
    port: int = 7267
    time_limit_ms: int = 5000
    use_pimc: bool = False
    pimc_k: int = 4
    pimc_seed: int | None = None  # for harness reproducibility
    use_belief_tracking: bool = False  # Plan H: per-opp-Pokemon belief tracker

    @classmethod
    def from_file(cls, path: str) -> MCTSConfig:
        with open(path) as f:
            data = json.load(f)
        return cls(
            label=data.get("label", "default"),
            port=data.get("port", 7267),
            time_limit_ms=data.get("time_limit_ms", 5000),
            use_pimc=data.get("use_pimc", False),
            pimc_k=data.get("pimc_k", 4),
            pimc_seed=data.get("pimc_seed", None),
            use_belief_tracking=data.get("use_belief_tracking", False),
        )


@dataclass
class TurnRecord:
    """Telemetry record for a single turn decision."""

    turn: int
    best_move: str
    confidence: float
    simulations: int
    depth: int
    time_ms: int
    decision_time_ms: int  # wall clock for HTTP call
    reasoning: list[dict]
    alternatives: list[dict]
    active_pokemon: str
    opponent_active_pokemon: str
    active_hp_fraction: float
    opponent_hp_fraction: float


class MCTSPlayer(Player):
    """Player that delegates move decisions to a poke-engine MCTS HTTP server.

    Supports two adapter modes:
      - Default: BattleAdapter (battle_testing) — uses /analyze endpoint with
        a fully-known opponent team (passed at construction).
      - PIMC: SpectatorAdapter (showdown_copilot) — uses /analyze/stream
        endpoint with K opponent-team hypotheses sampled from chaos priors.
        Requires config.use_pimc=True. Adapter MUST be a SpectatorAdapter
        (caller's responsibility); this player will lazily call
        adapter.on_team_preview() on the first turn using
        battle.teampreview_opponent_team if _opp_specs is empty.
    """

    def __init__(self, config: MCTSConfig, adapter, *args, order_adapter=None, **kwargs):
        """
        Args:
          config: MCTSConfig (use_pimc selects endpoint + adapter contract).
          adapter: Either BattleAdapter (non-PIMC) or SpectatorAdapter (PIMC).
            Must expose to_engine_format (non-PIMC) or to_engine_json (PIMC).
          order_adapter: Used to convert engine response to a poke-env order.
            If None, falls back to `adapter`. SpectatorAdapter has no
            to_battle_order, so when use_pimc=True the caller MUST pass a
            BattleAdapter here.
        """
        super().__init__(*args, **kwargs)
        self.config = config
        self.adapter = adapter
        self.order_adapter = order_adapter if order_adapter is not None else adapter
        self.turn_records: list[TurnRecord] = []
        self._http_client = httpx.Client(timeout=30.0)
        self._team_preview_called = False
        # Per-battle reset detection for multi-battle sweeps. Reuses
        # poke-env's `battle.battle_tag` which is unique per battle on the
        # connection; when it changes, we reset _team_preview_called so the
        # next battle gets a fresh team-preview pass (which in turn calls
        # SpectatorAdapter.on_team_preview → BeliefTracker.clear()).
        self._last_battle_tag: str | None = None

        # Phase 2 — per-turn move-order capture for speed-range narrowing.
        # Reset on every |turn| boundary. Each entry: (side, species, move_id, priority).
        # `side` is "p1"/"p2" matching the protocol actor token.
        self._turn_move_log: list[tuple[str, str, str, int]] = []
        # Skip reasons sniffed from this turn's protocol lines (Quick Claw,
        # Custap, |cant|, confusion). Non-empty → skip narrowing this turn.
        self._turn_skip_flags: list[str] = []

    def _maybe_reset_per_battle_state(self, battle) -> None:
        """Detect a new battle and reset per-battle MCTSPlayer state.

        MCTSPlayer is constructed once and reused across N battles in a
        sweep (`--battles N`). Without this hook, `_team_preview_called`
        stays True after battle 1, and SpectatorAdapter.on_team_preview()
        is never called again — meaning BeliefTracker._beliefs accumulates
        revealed_moves across unrelated battles. battle.battle_tag is a
        poke-env attribute that's unique per battle; if it's missing on
        some poke-env version, fall back to id(battle) as a stable
        per-instance identifier.
        """
        tag = getattr(battle, "battle_tag", None) or id(battle)
        if tag != self._last_battle_tag:
            self._team_preview_called = False
            self._last_battle_tag = tag

    def _maybe_init_team_preview(self, battle) -> None:
        """For PIMC OR belief-tracking mode: ensure SpectatorAdapter has
        seen team preview (which in turn resets the BeliefTracker)."""
        if not (self.config.use_pimc or self.config.use_belief_tracking):
            return
        if self._team_preview_called:
            return
        # SpectatorAdapter has _opp_specs — only initialize if empty.
        opp_specs = getattr(self.adapter, "_opp_specs", None)
        if opp_specs is None:
            return  # not a SpectatorAdapter; caller misuse, but don't crash
        if opp_specs:
            self._team_preview_called = True
            return
        preview = list(getattr(battle, "teampreview_opponent_team", []) or [])
        # In poke-env, teampreview_opponent_team is a list of Pokemon objects.
        # If empty (e.g. no team preview phase), fall back to opponent_team.
        if not preview:
            preview = list((battle.opponent_team or {}).values())
        species_list = [getattr(mon, "species", str(mon)) for mon in preview]
        # Capitalize first letter for chaos JSON lookup; SpectatorAdapter
        # normalizes internally, so casing only matters for display lookup.
        species_list = [s.capitalize() if s and s[0].islower() else s for s in species_list]
        if not species_list:
            return  # nothing to do yet; will retry next turn
        try:
            self.adapter.on_team_preview(species_list)
        except Exception as e:
            # E.g. chaos JSON 404 for non-standard formats. Mark called so we
            # don't spam the warning every turn; SpectatorAdapter will fall
            # through to BattleAdapter's live opponent_team data.
            print(f"[MCTS] on_team_preview WARN (continuing): {e}", flush=True)
        finally:
            self._team_preview_called = True

    def _update_beliefs_from_battle(self, battle) -> None:
        """Pull poke-env's parsed opp state into the SpectatorAdapter's
        BeliefTracker. Called every turn before constructing the engine
        request.

        Phase 1 ONLY feeds the protocol-asserted fields — revealed_moves,
        revealed_item, revealed_ability. The inference rules R1-R5
        require richer protocol-message context (split_msg for the
        [from]-token guard; switch-in events with weather/generation/our
        ability for R5; hazard-damage events with end-of-turn boundaries
        for R4). poke-env doesn't surface those events natively.

        Implication for Layer 3 A/B: this hook only exercises the
        move-superset filter (since revealed_moves IS fed). R1/R2/R3
        don't fire here — they fire when the protocol-message hook in
        spectator.py (Phase 2) calls on_move(species, move_id, split_msg).
        """
        if not hasattr(self.adapter, "_belief"):
            return  # adapter doesn't have belief tracker (e.g., BattleAdapter)

        belief = self.adapter._belief
        if belief is None:
            return  # adapter has the attribute but no tracker was wired in

        for species_id, mon in (battle.opponent_team or {}).items():
            display = getattr(mon, "species", str(mon))
            display_capitalized = display[0].upper() + display[1:] if display else display

            # Feed revealed moves (skeleton state-recorder; no inference fires)
            for move in (mon.moves or {}).values():
                belief.on_reveal_move(display_capitalized, move.id)

            # Feed revealed item (PROTOCOL-asserted)
            if mon.item:
                belief.on_reveal_item(display_capitalized, mon.item)

            # Feed revealed ability
            if mon.ability:
                belief.on_reveal_ability(display_capitalized, mon.ability)

    # ------------------------------------------------------------------
    # Phase 2 — Path A: per-turn move-order capture from raw protocol
    # ------------------------------------------------------------------

    async def _handle_battle_message(self, split_messages):
        """Override to sniff per-turn move-order BEFORE poke-env mutates state.

        We accumulate |move| events into `self._turn_move_log` and
        skip-flag-triggering events (`|cant|`, Quick Claw, Custap,
        confusion) into `self._turn_skip_flags`. On `|turn|N+1|`, we
        fire `_fire_speed_narrowing(turn=N)` for the just-finished turn
        before delegating to super (so poke-env's parsed state is the
        start-of-turn-N+1 state, but our buffers reflect turn N).
        """
        if self.config.use_belief_tracking:
            for split_message in split_messages:
                if len(split_message) < 2:
                    continue
                self._sniff_for_speed(split_message)
            # Fire AFTER sniffing all events but BEFORE super() mutates.
            # Find the |turn| boundary if any.
            for split_message in split_messages:
                if (
                    len(split_message) >= 3
                    and split_message[1] == "turn"
                ):
                    try:
                        new_turn = int(split_message[2])
                    except (ValueError, TypeError):
                        continue
                    # `new_turn` is the upcoming turn; the just-finished
                    # turn is new_turn - 1.
                    self._fire_speed_narrowing(turn=new_turn - 1)
                    break

        await super()._handle_battle_message(split_messages)

    def _sniff_for_speed(self, split_message: list[str]) -> None:
        """Look at one protocol message and update per-turn move/skip buffers."""
        if len(split_message) < 2:
            return
        kind = split_message[1]

        if kind == "move":
            # Format: ["", "move", "p2a: Garchomp", "Earthquake", ...]
            if len(split_message) < 4:
                return
            actor = split_message[2]  # "p2a: Garchomp"
            move_name = split_message[3]
            # Extract side ("p1"/"p2") and species
            side = actor.split("a:")[0] if "a:" in actor else (
                actor.split("b:")[0] if "b:" in actor else ""
            )
            species = actor.split(": ", 1)[-1].strip() if ": " in actor else ""
            move_id = move_name.lower().replace(" ", "").replace("-", "")
            # Look up priority from move id; default 0 if not findable
            priority = self._lookup_move_priority(move_id)
            self._turn_move_log.append((side, species, move_id, priority))

        elif kind == "cant":
            self._turn_skip_flags.append("cant")
        elif kind == "switch":
            # Switches make this turn uninformative for speed (a switch
            # happens before any move, regardless of speed)
            self._turn_skip_flags.append("switch")
        elif kind == "-activate" and len(split_message) >= 4:
            # confusion: |-activate|p2a: Mon|confusion
            tail = split_message[-1].lower()
            if "confusion" in tail:
                self._turn_skip_flags.append("confusion")
            elif "quick claw" in " ".join(split_message).lower():
                self._turn_skip_flags.append("quick_claw")
            elif "quick draw" in " ".join(split_message).lower():
                self._turn_skip_flags.append("quick_draw")
        elif kind == "-enditem":
            # Custap Berry consumption: |-enditem|p2a: Mon|Custap Berry|[eat]
            joined = " ".join(split_message).lower()
            if "custap berry" in joined or "custapberry" in joined:
                self._turn_skip_flags.append("custap")

    def _lookup_move_priority(self, move_id: str) -> int:
        """Best-effort priority lookup. Returns 0 for unknown moves —
        which means we'd treat priority-mismatch turns as comparable, a
        benign error mode (worst case: speed inference fires when it
        shouldn't, but the skip-list catches |switch|/|cant|/etc.).

        For the canonical priority moves we hardcode the +N values that
        matter most for speed-bracket comparison.
        """
        # Common +1/+2/+3 priority moves in gen 9
        plus_one = {
            "aquajet", "bulletpunch", "iceshard", "machpunch",
            "quickattack", "shadowsneak", "suckerpunch", "vacuumwave",
            "watershuriken", "accelerock", "jetpunch", "icicleshard",
            "manfistfury", "stormthrowexcept", "macherush",
        }
        plus_two = {"extremespeed", "feint"}
        plus_three = {"fakeout", "firstimpression"}
        if move_id in plus_one:
            return 1
        if move_id in plus_two:
            return 2
        if move_id in plus_three:
            return 3
        return 0

    def _fire_speed_narrowing(self, turn: int) -> None:
        """Called from `_handle_battle_message` on each `|turn|` boundary.

        Computes bot's post-modifier speed, derives opp_moved_first from
        the buffered move-log, then fires `tracker.on_turn_boundary_speed`.
        Resets the per-turn buffers regardless of whether narrowing fired.
        """
        # Find the active battle from the player. poke-env's Player keeps
        # a `_battles` dict keyed by battle tag.
        battles = getattr(self, "_battles", None) or {}
        # Pick the first non-finished battle. In test sweeps there's
        # exactly one. In multi-battle parallel sweeps each MCTSPlayer
        # instance only sees one battle at a time anyway (poke-env
        # async serializes per-instance).
        battle = None
        for b in battles.values():
            if not getattr(b, "finished", False):
                battle = b
                break
        if battle is None:
            self._reset_speed_buffers()
            return

        if not hasattr(self.adapter, "_belief"):
            self._reset_speed_buffers()
            return
        belief = self.adapter._belief
        if belief is None:
            self._reset_speed_buffers()
            return

        my_active = battle.active_pokemon
        opp_active = battle.opponent_active_pokemon
        if not my_active or not opp_active:
            self._reset_speed_buffers()
            return

        # Compute my_speed via the canonical modifier chain.
        try:
            from showdown_copilot.stats import apply_bot_speed_modifier_chain
            from poke_env.environment import Field, SideCondition, Status
        except ImportError:
            self._reset_speed_buffers()
            return

        my_speed_base = (my_active.stats or {}).get("spe") or 0
        if my_speed_base <= 0:
            # We don't know our own speed → can't compute threshold
            self._reset_speed_buffers()
            return

        my_speed = apply_bot_speed_modifier_chain(
            base_speed=my_speed_base,
            spe_boost_stage=(my_active.boosts or {}).get("spe", 0),
            has_tailwind=SideCondition.TAILWIND in (battle.side_conditions or {}),
            is_paralyzed=(my_active.status == Status.PAR),
            has_choicescarf=(my_active.item == "choicescarf"),
            has_protosynthesisspe=self._has_protosynthesisspe(my_active),
        )

        # Determine opp_moved_first from the move-log.
        opp_moved_first = self._derive_opp_moved_first(battle)

        # Trick Room state from battle.fields
        in_trick_room = Field.TRICK_ROOM in (battle.fields or {})

        # Weather + terrain strings (poke-env enums → string approximations)
        weather_str = self._weather_string(battle)
        terrain_str = self._terrain_string(battle)

        opp_species = self._capitalize_species(opp_active.species)
        belief.on_turn_boundary_speed(
            species=opp_species,
            turn=turn,
            my_active_speed_post_modifiers=my_speed,
            opp_moved_first=opp_moved_first,
            skip_reasons=list(self._turn_skip_flags),
            in_trick_room=in_trick_room,
            weather=weather_str,
            terrain=terrain_str,
        )

        self._reset_speed_buffers()

    def _has_protosynthesisspe(self, mon) -> bool:
        """Defensive check for the protosynthesis-Speed volatile.

        poke-env's mon.effects is Dict[Effect, int] (enum-keyed); we
        guard against poke-env version drift by trying the enum lookup
        and falling back to the volatile-status string set.
        """
        try:
            from poke_env.environment import Effect
            for e in (mon.effects or {}):
                if "protosynthesisspe" in str(e).lower():
                    return True
        except Exception:
            pass
        vs = getattr(mon, "volatile_statuses", None) or set()
        return any("protosynthesisspe" in str(v).lower() for v in vs)

    def _weather_string(self, battle) -> str | None:
        """Return Showdown-style weather name ('RainDance', 'SunnyDay', ...)
        or None. poke-env represents weather as an Enum dict.
        """
        if not battle.weather:
            return None
        # battle.weather is Dict[Weather, int]; pick first key
        w = next(iter(battle.weather))
        # Map poke-env Weather enum names to Showdown strings.
        name = str(w).split(".")[-1].upper()
        mapping = {
            "RAINDANCE": "RainDance",
            "SUNNYDAY": "SunnyDay",
            "SANDSTORM": "Sandstorm",
            "HAIL": "Hail",
            "SNOW": "Snow",
        }
        return mapping.get(name)

    def _terrain_string(self, battle) -> str | None:
        try:
            from poke_env.environment import Field
            for f in (battle.fields or {}):
                fname = str(f).split(".")[-1]
                if "ELECTRIC" in fname:
                    return "ELECTRIC_TERRAIN"
                if "GRASSY" in fname:
                    return "GRASSY_TERRAIN"
                if "MISTY" in fname:
                    return "MISTY_TERRAIN"
                if "PSYCHIC" in fname:
                    return "PSYCHIC_TERRAIN"
        except Exception:
            pass
        return None

    def _capitalize_species(self, species: str) -> str:
        """Match SpectatorAdapter's expected display form for belief lookups."""
        if not species:
            return species
        if species[0].islower():
            return species[0].upper() + species[1:]
        return species

    def _derive_opp_moved_first(self, battle) -> bool | None:
        """Inspect the move-log buffer to decide if opp's |move| event
        preceded ours. Returns None when ambiguous.

        Rules:
        - If 0 |move| lines in the log: None (probably both switched/can't).
        - If only 1 line: None (we didn't see both moves; don't infer).
        - If priorities differ: None (priority mismatch — uninformative).
        - If both at priority 0 AND log has both sides: True iff opp's
          actor side != our side AND opp's entry comes first.
        """
        if len(self._turn_move_log) < 2:
            return None

        # Take the first 2 move events (the 2 moves of this turn in singles)
        first, second = self._turn_move_log[0], self._turn_move_log[1]
        side_first, _sp_first, _mid_first, prio_first = first
        side_second, _sp_second, _mid_second, prio_second = second

        if prio_first != prio_second:
            return None  # priority mismatch — uninformative

        # Identify our side. battle.player_role is "p1" or "p2" in poke-env.
        my_role = getattr(battle, "player_role", None)
        if my_role is None:
            return None

        # opp_moved_first if first event's side is NOT my role
        return side_first != my_role

    def _reset_speed_buffers(self) -> None:
        self._turn_move_log = []
        self._turn_skip_flags = []

    def choose_move(self, battle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        active_name = active.species if active else "?"
        active_hp = f"{active.current_hp_fraction*100:.0f}%" if active else "?"
        opponent_name = opponent.species if opponent else "?"
        opponent_hp = f"{opponent.current_hp_fraction*100:.0f}%" if opponent else "?"

        moves = [m.id for m in battle.available_moves] if battle.available_moves else []
        switches = [s.species for s in battle.available_switches] if battle.available_switches else []
        print(f"[MCTS] Turn {battle.turn}: {active_name}({active_hp}) vs {opponent_name}({opponent_hp}) | Moves: {moves} | Switches: {switches}", flush=True)

        # Detect new battle in a multi-battle sweep BEFORE any per-battle
        # state work runs. Resets _team_preview_called so the next call
        # to _maybe_init_team_preview re-runs SpectatorAdapter.on_team_preview
        # which clears the BeliefTracker. Without this, state leaks across
        # battles in a `--battles N` sweep.
        self._maybe_reset_per_battle_state(battle)

        # Lazy team-preview init for PIMC OR belief-tracking modes. Must
        # come BEFORE _update_beliefs_from_battle so the tracker is cleared
        # at the start of a new battle before this turn's reveals are fed.
        self._maybe_init_team_preview(battle)

        # Plan H: belief tracking. Lazy init the tracker once, then on every
        # turn pull poke-env's parsed state into it.
        if self.config.use_belief_tracking:
            self._update_beliefs_from_battle(battle)

        # PIMC mode uses SpectatorAdapter (yields {"hypotheses": [...]}) and the
        # streaming endpoint where PIMC dispatch lives. Non-PIMC mode keeps the
        # legacy BattleAdapter + /analyze flow unchanged.
        if self.config.use_pimc:
            state_json = self.adapter.to_engine_json(battle)
            state_json["timeLimitMs"] = self.config.time_limit_ms
            state_json["updateIntervalMs"] = self.config.time_limit_ms  # silent until final
            url = f"http://localhost:{self.config.port}/analyze/stream"
        else:
            state_json = self.adapter.to_engine_format(battle)
            state_json["timeLimit"] = self.config.time_limit_ms
            url = f"http://localhost:{self.config.port}/analyze"

        print(f"[MCTS] Calling {url} (timeout={self.config.time_limit_ms}ms, pimc={self.config.use_pimc})...", flush=True)
        start_time = time.monotonic()

        try:
            if self.config.use_pimc:
                # /analyze/stream returns NDJSON; consume until event:"final".
                final_event = None
                with self._http_client.stream("POST", url, json=state_json) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        ev = json.loads(line)
                        ev_type = ev.get("event")
                        if ev_type == "error":
                            raise RuntimeError(
                                f"PIMC stream returned error: {ev.get('message') or ev}"
                            )
                        if ev_type == "final":
                            final_event = ev
                            break
                if final_event is None:
                    raise RuntimeError("PIMC stream ended without a final event")
                # Map StreamUpdate fields to the AnalyzeResponse-equivalent
                # shape so the rest of the code path is identical.
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                result = {
                    "bestMove": final_event.get("bestMove", ""),
                    "confidence": final_event.get("confidence", 0.0),
                    "simulations": final_event.get("sims", 0),
                    "depth": final_event.get("depth", 0),
                    "timeMs": elapsed_ms,
                    "reasoning": [],  # PV is in stream form ("you=X them=Y"); not parsed
                    "alternatives": final_event.get("alternatives", []),
                }
            else:
                resp = self._http_client.post(url, json=state_json)
                resp.raise_for_status()
                result = resp.json()
        except Exception as e:
            print(f"[MCTS] {url} ERROR: {e}", flush=True)
            return self.choose_random_move(battle)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        best = result.get("bestMove", "?")
        conf = result.get("confidence", 0)
        sims = result.get("simulations", 0)
        alts = result.get("alternatives", [])[:2]
        alt_str = ", ".join(f"{a.get('move','?')}({a.get('confidence',0)*100:.0f}%)" for a in alts)
        print(f"[MCTS] -> {best} (conf={conf*100:.0f}%, sims={sims}, {elapsed_ms}ms) alts=[{alt_str}]", flush=True)

        # Extract active Pokemon info for telemetry
        active_hp_frac = active.current_hp_fraction if active else 0.0
        opponent_hp_frac = opponent.current_hp_fraction if opponent else 0.0

        self.turn_records.append(
            TurnRecord(
                turn=battle.turn,
                best_move=result.get("bestMove", "unknown"),
                confidence=result.get("confidence", 0.0),
                simulations=result.get("simulations", 0),
                depth=result.get("depth", 0),
                time_ms=result.get("timeMs", 0),
                decision_time_ms=elapsed_ms,
                reasoning=result.get("reasoning", []),
                alternatives=result.get("alternatives", []),
                active_pokemon=active_name,
                opponent_active_pokemon=opponent_name,
                active_hp_fraction=active_hp_frac,
                opponent_hp_fraction=opponent_hp_frac,
            )
        )

        # Use order_adapter (BattleAdapter) for the response→order step.
        # SpectatorAdapter does not implement to_battle_order; in PIMC mode
        # the caller must pass a BattleAdapter as order_adapter.
        return self.order_adapter.to_battle_order(result, battle)

    def reset_telemetry(self):
        """Clear all recorded turn data."""
        self.turn_records = []


class MaxDamagePlayer(Player):
    """Simple baseline player that always picks the highest base power move."""

    def choose_move(self, battle):
        if battle.available_moves:
            best = max(
                battle.available_moves,
                key=lambda m: m.base_power if m.base_power else 0,
            )
            return self.create_order(best)
        return self.choose_random_move(battle)
