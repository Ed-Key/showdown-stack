"""SpectatorAdapter — composes over battle_testing.BattleAdapter."""
from __future__ import annotations

import logging
import random
from copy import copy
from typing import Any

from battle_testing.adapter import BattleAdapter
from battle_testing.team_parser import PokemonSpec, parse_team_file

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
    ):
        self._own_team: list[PokemonSpec] = parse_team_file(own_paste)
        self._format = format
        self._team_type = team_type
        self._priors = priors
        self._opp_specs: dict[str, PokemonSpec] = {}
        self._use_pimc = use_pimc
        self._pimc_k = pimc_k
        self._pimc_seed = pimc_seed
        # Per-species record of what info has been revealed by on_reveal.
        # Used to override sampled values during PIMC hypothesis construction.
        self._revealed: dict[str, dict] = {}  # species_norm -> {item, ability, moves}
        # Display-cased species names, indexed by normalized key.
        # Needed to preserve the casing Smogon's chaos JSON uses for lookup.
        self._opp_display_names: dict[str, str] = {}

    def on_team_preview(self, opponent_species: list[str]) -> None:
        """Called with the 6 species names revealed at team preview."""
        self._opp_specs.clear()
        self._revealed.clear()
        self._opp_display_names.clear()
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
        """Update our assumption for this species with newly-revealed info.
        Also records into self._revealed so PIMC hypotheses respect known info."""
        norm = _normalize(species)
        spec = self._opp_specs.get(norm)
        if spec is None:
            return

        # Existing modal-spec mutations (kept for non-PIMC path):
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

        # New: record for PIMC override.
        rec = self._revealed.setdefault(norm, {"moves": set(), "item": None, "ability": None})
        if revealed_item:
            rec["item"] = _normalize(revealed_item)
        if revealed_ability:
            rec["ability"] = _normalize(revealed_ability)
        if revealed_move:
            rec["moves"].add(_normalize(revealed_move))

    def _merge_revealed_into_sample(
        self,
        norm_species: str,
        sampled,
    ):
        """Apply revealed-info (from on_reveal) on top of a sampled ModalSet.
        Returns a new ModalSet with revealed fields forced in."""
        rec = self._revealed.get(norm_species)
        if not rec:
            return sampled
        merged = copy(sampled)
        if rec["item"]:
            merged.item = rec["item"]
        if rec["ability"]:
            merged.ability = rec["ability"]
        if rec["moves"]:
            # Insert revealed moves first; fill remaining slots with sampled moves
            # that aren't already in the revealed set.
            revealed_moves = list(rec["moves"])
            sampled_moves = list(sampled.moves)
            kept = []
            for m in revealed_moves:
                if len(kept) >= 4:
                    break
                if m not in kept:
                    kept.append(m)
            for m in sampled_moves:
                if len(kept) >= 4:
                    break
                if m not in kept:
                    kept.append(m)
            merged.moves = kept
        return merged

    def _sample_one_hypothesis(self, rng) -> dict[str, "PokemonSpec"]:
        """Sample one team-wide hypothesis. Each opp species is sampled
        independently; revealed info is merged in via _merge_revealed_into_sample.

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
            )
            merged = self._merge_revealed_into_sample(norm_species, sampled)
            out[norm_species] = merged.to_pokemon_spec()
        return out

    def to_engine_json(self, battle: Any) -> dict[str, Any]:
        """Produce the BattleRequest JSON that poke-engine /analyze[/stream] consumes.

        When use_pimc=True, returns {"hypotheses": [BattleRequest, ...]} of length pimc_k.
        Otherwise returns a single BattleRequest (current behavior)."""
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
            inner = BattleAdapter(
                own_team=self._own_team,
                opponent_team=list(self._opp_specs.values()),
            )
            return inner.to_engine_format(battle)
