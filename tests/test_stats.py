"""Tests for showdown_copilot.stats — Plan H Phase 2 stat math.

All test values verified against gen 9 canonical Speed formula:
  inner = (2*base + iv + ev//4) * level // 100 + 5
  speed = floor(inner * nature_multiplier)
"""
from __future__ import annotations

import pytest

from showdown_copilot.stats import (
    _BOOST_MULT,
    _NATURE_TO_SPE_MULT,
    apply_bot_speed_modifier_chain,
    compute_speed_stat,
)


# ----- compute_speed_stat (T-S1, T-S2) -----


def test_compute_speed_garchomp_jolly_max():
    """T-S1: Garchomp base 102, Jolly 252+, 31 IV, level 100 → 333.

    Canonical reference: Showdown calc, Smogon analysis tables.
    Garchomp's actual base Speed is 102 (NOT 108 as some older notes show).
    """
    assert compute_speed_stat(102, 252, 31, 1.1, 100) == 333


def test_compute_speed_garchomp_relaxed_min():
    """T-S2: Garchomp base 102, Relaxed (-Spe nature), 0 EV, 0 IV → 184.

    inner = (204 + 0 + 0) * 100 // 100 + 5 = 209
    209 * 0.9 = 188.1 → floor → 188
    """
    assert compute_speed_stat(102, 0, 0, 0.9, 100) == 188


def test_compute_speed_iron_bundle_max():
    """Iron Bundle base 136, Timid 252+, 31 IV → 394.

    inner = (272 + 31 + 63) + 5 = 371
    371 * 1.1 = 408.1 → 408 ⚠️ but Showdown calc says 394?
    Re-derive: (2*136 + 31 + 63) * 100/100 + 5 = (272+31+63)+5 = 371
    371 * 1.1 = 408.1 → floor 408. Showdown calc: 394.
    Hmm. Let me check formula. Actually the +5 is added BEFORE nature
    multiplier in some sources. Let me compute both:
      Path A (+5 inside nature): floor((366) * 1.1) = floor(402.6) = 402
      Path B (+5 outside nature): floor(366 * 1.1) + 5 = 402 + 5 = 407
      Path C: floor((371) * 1.1) = floor(408.1) = 408
    The canonical Bulbapedia formula:
      stat = floor((((2*B + I + floor(E/4)) * L / 100) + 5) * N)
    So nature multiplies AFTER +5 → matches our formula → 408.
    But Showdown calc reports 394 for Iron Bundle Timid 252+ Spe.
    Re-checking: Iron Bundle base Spe might not be 136.
    Per pokedex: Iron Bundle 56/80/114/124/60/136 → base 136 confirmed.
    Manual Showdown calc result: max(2*136 + 252/4 + 31) * 1 + 5 = ... let me redo.
    base 136, level 100, ev 252, iv 31, +nature:
      inner = floor((2*136 + 31 + floor(252/4)) * 100 / 100) + 5
            = floor((272 + 31 + 63)) + 5
            = 366 + 5 = 371
      stat = floor(371 * 1.1) = 408
    OK 408 it is. The "394" I cited from memory was wrong.

    So this test asserts 408 (the canonical formula result).
    """
    assert compute_speed_stat(136, 252, 31, 1.1, 100) == 408


def test_compute_speed_neutral_nature():
    """Neutral nature on a base 100 → max stat 309 at 252 EVs / 31 IV / level 100.
    inner = (200 + 31 + 63) + 5 = 299
    299 * 1.0 = 299
    """
    assert compute_speed_stat(100, 252, 31, 1.0, 100) == 299


def test_compute_speed_low_level():
    """Level 50 should produce roughly half the level-100 result. Common
    competitive cap for level-50 metagames (e.g., VGC).
    Garchomp base 102, Jolly 252+, 31 IV, level 50:
      inner = floor((204+31+63) * 50/100) + 5 = floor(149) + 5 = 154
      154 * 1.1 = 169.4 → floor → 169
    """
    assert compute_speed_stat(102, 252, 31, 1.1, 50) == 169


# ----- nature multiplier table (T-S3) -----


def test_nature_table_has_25_entries():
    """Gen 3+ has exactly 25 natures (5 neutral × 5 ±-stat pairs)."""
    assert len(_NATURE_TO_SPE_MULT) == 25


def test_nature_table_plus_spe_natures():
    """The 4 +Spe natures are Hasty, Jolly, Naive, Timid."""
    plus = {n for n, m in _NATURE_TO_SPE_MULT.items() if m == 1.1}
    assert plus == {"Hasty", "Jolly", "Naive", "Timid"}


def test_nature_table_minus_spe_natures():
    """The 4 -Spe natures are Brave, Quiet, Relaxed, Sassy."""
    minus = {n for n, m in _NATURE_TO_SPE_MULT.items() if m == 0.9}
    assert minus == {"Brave", "Quiet", "Relaxed", "Sassy"}


def test_nature_table_includes_docile_and_bashful():
    """Re-verifier IMP-12: chaos JSON sometimes emits Docile/Bashful;
    they're neutral natures (×1.0)."""
    assert _NATURE_TO_SPE_MULT["Docile"] == 1.0
    assert _NATURE_TO_SPE_MULT["Bashful"] == 1.0


# ----- _BOOST_MULT canonical values -----


def test_boost_mult_zero_stage():
    assert _BOOST_MULT[0] == 1.0


def test_boost_mult_plus_one_stage():
    assert _BOOST_MULT[1] == 3 / 2


def test_boost_mult_max_positive():
    assert _BOOST_MULT[6] == 4.0


def test_boost_mult_minus_one_stage():
    assert _BOOST_MULT[-1] == pytest.approx(2 / 3)


def test_boost_mult_max_negative():
    assert _BOOST_MULT[-6] == pytest.approx(2 / 8)


# ----- apply_bot_speed_modifier_chain (T-S4 + T-M tests) -----


def test_modifier_chain_no_modifiers():
    """No modifiers → speed unchanged."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=False,
        is_paralyzed=False,
        has_choicescarf=False,
        has_protosynthesisspe=False,
    ) == 333


def test_modifier_chain_tailwind():
    """Tailwind doubles speed."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=True,
        is_paralyzed=False,
        has_choicescarf=False,
        has_protosynthesisspe=False,
    ) == 666


def test_modifier_chain_paralysis_gen7_plus():
    """T-S4: paralysis halves speed in gen 7+. 333 // 2 = 166."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=False,
        is_paralyzed=True,
        has_choicescarf=False,
        has_protosynthesisspe=False,
        generation=9,
    ) == 166


def test_modifier_chain_paralysis_gen6():
    """T-S4: paralysis quarters speed in gen 4-6. 333 // 4 = 83."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=False,
        is_paralyzed=True,
        has_choicescarf=False,
        has_protosynthesisspe=False,
        generation=6,
    ) == 83


def test_modifier_chain_choicescarf():
    """Choice Scarf multiplies speed by 1.5 (truncated). 333 * 1.5 = 499."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=False,
        is_paralyzed=False,
        has_choicescarf=True,
        has_protosynthesisspe=False,
    ) == 499


def test_modifier_chain_protosynthesis_spe():
    """Booster Energy / Quark Drive selecting Speed: ×1.5."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=False,
        is_paralyzed=False,
        has_choicescarf=False,
        has_protosynthesisspe=True,
    ) == 499


def test_modifier_chain_boost_plus_one():
    """+1 Speed boost stage → ×1.5. 333 * 1.5 = 499."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=1,
        has_tailwind=False,
        is_paralyzed=False,
        has_choicescarf=False,
        has_protosynthesisspe=False,
    ) == 499


def test_modifier_chain_boost_plus_six():
    """+6 Speed boost → ×4. 333 * 4 = 1332."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=6,
        has_tailwind=False,
        is_paralyzed=False,
        has_choicescarf=False,
        has_protosynthesisspe=False,
    ) == 1332


def test_modifier_chain_paralysis_then_tailwind():
    """T-M5 partial: gen-7 paralysis (÷2) THEN Tailwind (×2) cancels.
    Order matches foul-play's chain: boost → paralysis → tailwind → scarf → proto.
    333 → 333//2 = 166 → 166*2 = 332. Note rounding loss at the par step."""
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=0,
        has_tailwind=True,
        is_paralyzed=True,
        has_choicescarf=False,
        has_protosynthesisspe=False,
        generation=9,
    ) == 332  # 333 // 2 = 166, then * 2 = 332 (lost 1 to truncation)


def test_modifier_chain_full_stack():
    """All modifiers stacked: boost+1 (×1.5), paralysis (÷2), tailwind (×2),
    scarf (×1.5), protosynthesis-Spe (×1.5).
    333 → int(333 * 1.5) = 499 → 499//2 = 249 → 249*2 = 498
        → int(498 * 1.5) = 747 → int(747 * 1.5) = 1120
    """
    assert apply_bot_speed_modifier_chain(
        333,
        spe_boost_stage=1,
        has_tailwind=True,
        is_paralyzed=True,
        has_choicescarf=True,
        has_protosynthesisspe=True,
        generation=9,
    ) == 1120
