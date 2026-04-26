"""SpectatorAdapter — composes over battle_testing.BattleAdapter."""
from __future__ import annotations

import logging
import random
from typing import Any

from battle_testing.adapter import BattleAdapter
from battle_testing.team_parser import PokemonSpec, parse_team_file

from showdown_copilot.belief import BeliefTracker
from showdown_copilot.priors import PriorsSource

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


class SpectatorAdapter:
    """Builds engine JSON from a poke-env Battle object, filling unrevealed
    opponent fields with modal sets from PriorsSource."""

    def __init__(
        self,
        own_paste: str,
        format: str,
        team_type: str | None,
        priors: PriorsSource,
        use_pimc: bool = False,
        pimc_k: int = 4,
        pimc_seed: int | None = None,
        belief_tracker: BeliefTracker | None = None,
        known_opp_specs: list[PokemonSpec] | None = None,
    ):
        """SpectatorAdapter — composes over BattleAdapter.

        When `known_opp_specs` is provided (harness path with team paste), the
        engine sees these real specs as the opp foundation; belief tracking
        only overlays moves/item/ability. When None (live extension / TUI
        without team paste), falls back to chaos-modal foundation per
        `priors.get_set`.
        """
        self._own_team: list[PokemonSpec] = parse_team_file(own_paste)
        self._format = format
        self._team_type = team_type
        self._priors = priors
        self._opp_specs: dict[str, PokemonSpec] = {}
        self._use_pimc = use_pimc
        self._pimc_k = pimc_k
        self._pimc_seed = pimc_seed
        # Display-cased species names, indexed by normalized key.
        # Needed to preserve the casing Smogon's chaos JSON uses for lookup.
        self._opp_display_names: dict[str, str] = {}
        # NEW (Plan H Task 3): belief tracker (defaults to a fresh one).
        # Replaces the freestanding _revealed dict from Plan G' Task 4.
        self._belief = belief_tracker if belief_tracker is not None else BeliefTracker()
        # NEW (Plan H Task 11 fix): real opp specs from harness team paste.
        # When set, on_team_preview / _build_belief_aware_battle_adapter use
        # these directly instead of synthesizing modal sets from chaos. This
        # eliminates the placeholder-stats / empty-types / weight=0 artifact
        # introduced when the harness routed through SpectatorAdapter without
        # a way to inject the real team.
        self._known_opp_specs: list[PokemonSpec] | None = known_opp_specs

    def on_team_preview(self, opponent_species: list[str]) -> None:
        """Called with the 6 species names revealed at team preview."""
        self._opp_specs.clear()
        self._opp_display_names.clear()
        # Reset belief tracker — fresh battle, no prior observations.
        # Clear in place rather than replacing the instance, so that any
        # external code holding a reference (e.g., the harness's live
        # message hook in Task 9) stays connected to the same tracker.
        self._belief.clear()

        # NEW (Plan H Task 11 fix): if known_opp_specs was injected by the
        # harness, populate _opp_specs directly from the REAL team specs.
        # Skip the chaos modal lookup entirely — those are placeholders that
        # discard real stats / types / weight / item.
        if self._known_opp_specs is not None:
            for spec in self._known_opp_specs:
                norm = _normalize(spec.species)
                # Display name fallback: spec.species is normalized id form
                # (e.g. "lopunnymega"). For chaos lookups in the non-harness
                # path we want display casing; here we just preserve what
                # we have so downstream code that consults display names
                # still works.
                self._opp_display_names[norm] = spec.species
                self._opp_specs[norm] = spec
            logger.info(
                "team preview: loaded %d KNOWN opponent specs from harness "
                "(format=%s, team_type=%s)",
                len(self._opp_specs), self._format, self._team_type,
            )
            return

        for species in opponent_species:
            norm = _normalize(species)
            self._opp_display_names[norm] = species
            modal = self._priors.get_set(
                species=species, format=self._format, team_type=self._team_type,
            )
            spec = modal.to_pokemon_spec()
            self._opp_specs[norm] = spec
        logger.info(
            "team preview: loaded modal sets for %d opponents (format=%s, team_type=%s)",
            len(self._opp_specs), self._format, self._team_type,
        )

    def on_reveal(
        self,
        species: str,
        revealed_move: str | None = None,
        revealed_item: str | None = None,
        revealed_ability: str | None = None,
    ) -> None:
        """Update assumption for `species` with newly-revealed info.

        Records into BOTH self._opp_specs (modal mutation, kept for the
        non-belief code path) AND self._belief (the new BeliefTracker).
        """
        norm = _normalize(species)
        spec = self._opp_specs.get(norm)
        if spec is None:
            return

        # Existing modal-spec mutation (kept for backwards compat with
        # callers / tests that inspect _opp_specs directly).
        if revealed_item:
            spec.item = _normalize(revealed_item)
        if revealed_ability:
            spec.ability = _normalize(revealed_ability)
        if revealed_move:
            rm = _normalize(revealed_move)
            if rm not in [_normalize(m) for m in spec.moves]:
                if spec.moves:
                    spec.moves[-1] = rm
                else:
                    spec.moves = [rm]

        # NEW (Plan H Task 3): delegate to BeliefTracker.
        if revealed_move:
            self._belief.on_reveal_move(species, revealed_move)
        if revealed_item:
            self._belief.on_reveal_item(species, revealed_item)
        if revealed_ability:
            self._belief.on_reveal_ability(species, revealed_ability)

    def _sample_one_hypothesis(self, rng) -> dict[str, "PokemonSpec"]:
        """Sample one team-wide hypothesis. Each opp species is sampled
        independently; revealed info is merged via belief-aware sample_set.

        Note: passes the display-cased species name (not the normalized key) to
        the priors API because Smogon's chaos JSON is keyed by display name.
        Falls back to current_spec.species if display name was somehow lost.
        """
        out: dict[str, "PokemonSpec"] = {}
        for norm_species, current_spec in self._opp_specs.items():
            display_name = self._opp_display_names.get(norm_species, current_spec.species)
            sampled = self._priors.sample_set(
                species=display_name,
                format=self._format,
                team_type=self._team_type,
                rng=rng,
                belief=self._belief.get(norm_species),
            )
            out[norm_species] = sampled.to_pokemon_spec()
        return out

    def _build_belief_aware_battle_adapter(self) -> BattleAdapter:
        """Construct a BattleAdapter whose opponent_team is the belief-aware
        modal set for each opp species. Shared by `to_engine_format` and the
        non-PIMC branch of `to_engine_json`.

        Two paths:
        1. Known-specs path (Plan H Task 11 fix): when `self._known_opp_specs`
           is set (harness with real team paste), the BattleAdapter is built
           from the REAL specs. Belief overlays only revealed item / ability /
           moves on top. No chaos lookup. This is the path that fixes the
           Plan H Task 11 measurement artifact (HP=100 placeholder, weight=0,
           dropped Lopunnite, etc.).
        2. Chaos-modal path (live extension / TUI): the modal lookup passes
           `belief=self._belief.get(norm_species)` so the priors filter
           (Plan H Task 2) consults revealed_moves / impossible_items /
           impossible_abilities. Falls back to the species modal if the belief
           filter eliminates every candidate.
        """
        if self._known_opp_specs is not None:
            # Build from real specs, overlaying revealed info from belief.
            # Mutating the originals would persist across battles, so we make
            # shallow copies via dataclasses.replace.
            from dataclasses import replace
            opp_specs_overlaid: list[PokemonSpec] = []
            for spec in self._known_opp_specs:
                norm = _normalize(spec.species)
                belief = self._belief.get(spec.species)
                new_item = spec.item
                new_ability = spec.ability
                new_moves = list(spec.moves)
                if belief.revealed_item:
                    new_item = _normalize(belief.revealed_item)
                if belief.revealed_ability:
                    new_ability = _normalize(belief.revealed_ability)
                # Note: revealed_moves are usually a subset of the real moves,
                # so we don't need to overlay — the real spec already has the
                # truth. Skip move overlay for known-specs path.
                opp_specs_overlaid.append(replace(
                    spec,
                    item=new_item,
                    ability=new_ability,
                    moves=new_moves,
                ))
            return BattleAdapter(
                own_team=self._own_team,
                opponent_team=opp_specs_overlaid,
            )

        opp_specs_with_belief: dict[str, PokemonSpec] = {}
        for norm_species, current_spec in self._opp_specs.items():
            display_name = self._opp_display_names.get(
                norm_species, current_spec.species,
            )
            modal = self._priors.get_set(
                species=display_name,
                format=self._format,
                team_type=self._team_type,
                belief=self._belief.get(norm_species),
            )
            opp_specs_with_belief[norm_species] = modal.to_pokemon_spec()
        return BattleAdapter(
            own_team=self._own_team,
            opponent_team=list(opp_specs_with_belief.values()),
        )

    def to_engine_format(self, battle: Any) -> dict[str, Any]:
        """Produce a single BattleRequest dict matching BattleAdapter's
        contract, using belief-aware modal sets for the opponent team.

        This is the entry point used by MCTSPlayer's non-PIMC code path
        (Plan H Task 9 fix). PIMC's K-hypothesis fan-out lives on
        `to_engine_json` only — this method always returns a single
        BattleRequest, never `{"hypotheses": [...]}`.
        """
        return self._build_belief_aware_battle_adapter().to_engine_format(battle)

    def to_engine_json(self, battle: Any) -> dict[str, Any]:
        """Produce the BattleRequest JSON that poke-engine /analyze[/stream] consumes.

        When use_pimc=True, returns {"hypotheses": [BattleRequest, ...]} of length pimc_k.
        Otherwise returns a single BattleRequest with belief-aware modal sets
        (Plan H Task 3).
        """
        if self._use_pimc:
            rng = random.Random(self._pimc_seed) if self._pimc_seed is not None else random.Random()
            hypotheses = []
            for _ in range(self._pimc_k):
                sampled_specs = self._sample_one_hypothesis(rng)
                inner = BattleAdapter(
                    own_team=self._own_team,
                    opponent_team=list(sampled_specs.values()),
                )
                hypotheses.append(inner.to_engine_format(battle))
            return {"hypotheses": hypotheses}
        else:
            # Belief-aware modal selection per opp species (Plan H Task 3).
            return self._build_belief_aware_battle_adapter().to_engine_format(battle)
