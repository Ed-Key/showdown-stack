"""Stat math for Plan H Phase 2 speed inference.

Pure functions, no I/O. Imported by belief.py (for forced-scarf check),
priors.py (for spread filter), mcts_player.py and spectator.py (for
bot-side modifier chain at observation time).

Canonical Speed formula (gen 3+):
    inner = (2*base + iv + ev//4) * level // 100 + 5
    speed = floor(inner * nature_multiplier)

Reference: Bulbapedia / Showdown calc.
"""
from __future__ import annotations

# All 25 gen-3+ natures and their Speed-stat multipliers.
# +Spe: Hasty, Jolly, Naive, Timid (×1.1)
# -Spe: Brave, Quiet, Relaxed, Sassy (×0.9)
# Neutral: Hardy, Docile, Serious, Bashful, Quirky (×1.0)
# Other natures (Adamant, Bold, etc.) modify Atk/Def/SpA/SpD, neutral on Speed.
_NATURE_TO_SPE_MULT: dict[str, float] = {
    "Adamant": 1.0, "Bashful": 1.0, "Bold": 1.0, "Brave": 0.9,
    "Calm": 1.0, "Careful": 1.0, "Docile": 1.0, "Gentle": 1.0,
    "Hardy": 1.0, "Hasty": 1.1, "Impish": 1.0, "Jolly": 1.1,
    "Lax": 1.0, "Lonely": 1.0, "Mild": 1.0, "Modest": 1.0,
    "Naive": 1.1, "Naughty": 1.0, "Quiet": 0.9, "Quirky": 1.0,
    "Rash": 1.0, "Relaxed": 0.9, "Sassy": 0.9, "Serious": 1.0,
    "Timid": 1.1,
}


# Canonical stat-stage boost multipliers (×N/2 for positive, ×2/N for negative).
_BOOST_MULT: dict[int, float] = {
    -6: 2 / 8, -5: 2 / 7, -4: 2 / 6, -3: 2 / 5, -2: 2 / 4, -1: 2 / 3,
    0: 1.0,
    1: 3 / 2, 2: 4 / 2, 3: 5 / 2, 4: 6 / 2, 5: 7 / 2, 6: 8 / 2,
}


def compute_speed_stat(
    base: int, ev: int, iv: int, nature_mult: float, level: int = 100
) -> int:
    """Gen 3+ canonical Speed stat formula.

    stat = floor((floor((2*base + iv + floor(ev/4)) * level/100) + 5) * nature_mult)

    Args:
      base: species base Speed (e.g., 102 for Garchomp)
      ev: Speed EVs, 0-252
      iv: Speed IVs, 0-31
      nature_mult: 0.9, 1.0, or 1.1 (lookup via _NATURE_TO_SPE_MULT)
      level: 1-100

    Returns the final Speed stat as int (truncated).
    """
    inner = (2 * base + iv + ev // 4) * level // 100 + 5
    return int(inner * nature_mult)


def apply_bot_speed_modifier_chain(
    base_speed: int,
    *,
    spe_boost_stage: int,
    has_tailwind: bool,
    is_paralyzed: bool,
    has_choicescarf: bool,
    has_protosynthesisspe: bool,
    generation: int = 9,
) -> int:
    """Apply the bot's modifier chain to its known speed stat. Result is
    the threshold the opp's unknown Speed must beat (or be beaten by) to
    explain the observed move ordering this turn.

    Order matches foul-play's chain (paraphrased from check_speed_ranges):
        boost_stage → paralysis → tailwind → choicescarf → protosynthesis_spe

    Each stage truncates via int() per gen-9 mechanics.

    Args:
      base_speed: bot's actual Speed stat at level (already includes nature/EV/IV).
      spe_boost_stage: -6..+6 from Showdown's |-boost|/|-unboost| events.
      has_tailwind: bool from battle.side_conditions[SideCondition.TAILWIND].
      is_paralyzed: True iff bot's status == Status.PAR.
      has_choicescarf: True iff bot's revealed item == "choicescarf".
      has_protosynthesisspe: True iff bot has the "protosynthesisspe"
        volatile (Booster Energy / Quark Drive selecting Speed).
      generation: 9 by default; foul-play exposes this for gen-4-6 paralysis
        which halves to ÷4 instead of ÷2.

    Returns the post-modifier bot Speed (the speed_threshold).
    """
    s = int(base_speed * _BOOST_MULT[spe_boost_stage])
    if is_paralyzed:
        s = s // 4 if generation <= 6 else s // 2
    if has_tailwind:
        s = s * 2
    if has_choicescarf:
        s = int(s * 1.5)
    if has_protosynthesisspe:
        s = int(s * 1.5)
    return s
