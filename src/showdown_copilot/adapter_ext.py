"""SpectatorAdapter — composes over battle_testing.BattleAdapter."""
from __future__ import annotations

import logging
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
    ):
        self._own_team: list[PokemonSpec] = parse_team_file(own_paste)
        self._format = format
        self._team_type = team_type
        self._priors = priors
        self._opp_specs: dict[str, PokemonSpec] = {}

    def on_team_preview(self, opponent_species: list[str]) -> None:
        """Called with the 6 species names revealed at team preview."""
        self._opp_specs.clear()
        for species in opponent_species:
            modal = self._priors.get_set(
                species=species, format=self._format, team_type=self._team_type,
            )
            spec = modal.to_pokemon_spec()
            self._opp_specs[_normalize(species)] = spec
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
        """Update our assumption for this species with newly-revealed info."""
        norm = _normalize(species)
        spec = self._opp_specs.get(norm)
        if spec is None:
            return
        if revealed_item:
            spec.item = _normalize(revealed_item)
        if revealed_ability:
            spec.ability = _normalize(revealed_ability)
        if revealed_move:
            rm = _normalize(revealed_move)
            if rm not in [_normalize(m) for m in spec.moves]:
                # swap out the least-confident (last) assumed move
                if spec.moves:
                    spec.moves[-1] = rm
                else:
                    spec.moves = [rm]

    def to_engine_json(self, battle: Any) -> dict[str, Any]:
        """Produce the BattleRequest JSON that poke-engine /analyze consumes."""
        inner = BattleAdapter(
            own_team=self._own_team,
            opponent_team=list(self._opp_specs.values()),
        )
        return inner.to_engine_format(battle)
