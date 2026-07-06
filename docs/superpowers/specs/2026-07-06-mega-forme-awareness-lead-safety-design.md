# Mega + Hidden-Forme Awareness and Lead-Safety for Preview Plans

**Date:** 2026-07-06
**Status:** Approved design, pending implementation plan
**Branch:** `feat/matchup-plan-v2` (continues the matchup-plan reliability+grounding work)

## Problem

Preview plans are blind to hidden opponent formes, and it costs games. Diagnosed live from the QAQyyy loss (postmortem `2026-07-06-1153-qaqyyy-natdex-44910647.json`):

- The plan recommended leading Garchomp ("outspeeds Great Tusk, sets Stealth Rock turn 1"). The opponent led **Mega Diancie** (base speed 110), which outsped Garchomp (102) and Moonblast OHKO'd it **before it could act** — "fainted before action". The game was lost on turn 1.
- Root cause: the grounding fed the planner **base-forme** speed (Diancie 50), not the Mega (110). The speed-context builder (`build_speed_context`) and fact pack (`build_preview_planner_fact_pack`) only ever read `get_pokemon_facts(species)`, which resolves the base dex entry.
- Confirmed the same model on a smarter tier (Sonnet 4.6) made the identical mistake — this is a **grounding** gap, not a model-intelligence gap.
- Related blindspot: team-preview wildcards. "Urshifu-\*" resolves to the base forme only (Fighting/Dark), so the planner never saw Rapid-Strike (Fighting/Water, Surging Strikes) — which swept three Pokémon mid-game.

Verified facts (gen-9 dex via poke-env): 47 opponent species carry a Mega forme; `dex["diancie"]["otherFormes"] == ["Diancie-Mega"]`, and `dex["dianciemega"]` has `spe=110`, `ability=Magic Bounce`, `requiredItem=Diancite`, `forme=Mega`. `dex["urshifu"]["otherFormes"] == ["Urshifu-Rapid-Strike"]` (Fighting/Water, same 97 speed, different typing).

## Decisions (made with user)

- **Forme scope:** Megas **and** team-preview wildcard formes (Urshifu-\*). Both were real loss causes; both are clean deterministic dex lookups. Terastallization is out of scope (much larger modeling problem, not a named loss cause).
- **Lead-safety mechanism:** LLM-reasoned, grounded by real Mega/forme speed + typing data. No damage-calc rework. A deterministic damage-based check is a recorded follow-up.
- **Verifier forme-awareness:** in v1 (user confirmed) — since we deliberately introduce forme talk, the verifier must not false-flag correct forme mechanics claims.

## Design

Deterministic dex data for the *facts*; LLM reasoning for the *lead-safety judgment*. All new grounding degrades to empty and never blocks a plan, matching the existing contract in `preview_grounding.py`.

### 1. `mechanics_facts.get_hidden_formes(species) -> list[dict]` (new, pure dex)

Returns a species' preview-relevant alternate formes. Resolves the base entry (stripping a trailing `-*` wildcard first), reads `otherFormes`, and for each alternate forme classifies it:

- **Mega** — the alternate forme's dex entry has `forme == "Mega"` (or `requiredItem` ending in "ite"). Basis `"mega-evolution"`.
- **Battle forme** — other `otherFormes` reachable in-battle without preview disclosure (Urshifu-Rapid-Strike). Basis `"team-preview-forme"`.

Each entry:
```python
{
  "name": "Diancie-Mega",
  "formeKind": "Mega",            # "Mega" | "Battle"
  "basis": "mega-evolution",       # or "team-preview-forme"
  "types": ["Rock", "Fairy"],
  "abilities": ["Magic Bounce"],
  "spe": 110, "atk": 160, "spa": 160,
  "triggerItem": "Diancite",       # None for battle formes
}
```
A species with no relevant hidden forme returns `[]`. Unknown species returns `[]`. Pure dex lookup; deterministic; no priors, no network.

Excludes cosmetic-only and non-preview-relevant formes (regional formes are their own preview species; only same-base in-battle transformations count).

### 2. `preview_grounding.build_possible_formes(opponent_team) -> list[dict]` (new)

Per opponent species, when `get_hidden_formes` is non-empty:
```python
{"species": "Diancie", "baseTypes": ["Rock","Fairy"], "baseSpeed": 50,
 "formes": [ <get_hidden_formes entries> ]}
```
Empty list when no species has a hidden forme. Wraps forme lookup in try/except and skips a species on failure, matching `build_opponent_likely_sets`.

### 3. `preview_grounding.build_speed_context` (extend)

For each opponent species with hidden formes, append a speed row per hidden forme, tagged so the ordering is honest about uncertainty:
```python
{"species": "Diancie-Mega", "side": "opp", "baseSpeed": 110,
 "forme": "Mega", "guaranteed": False}
```
Base-forme rows keep their exact current shape and add **no** new keys — the *absence* of a `forme` key marks a row as the guaranteed base forme; the *presence* of `forme` (+ `guaranteed: False`) marks a hidden-forme row. This keeps existing base rows byte-compatible while making forme rows unambiguous. The sort by descending speed then places `Diancie-Mega 110` above `Garchomp 102`, which is the exact signal that was missing.

### 4. `preview_plan._preview_user_prompt` (extend)

Add a `possibleFormes` key to the payload from `build_possible_formes(req.opponentTeam)`, only when non-empty and only when `SHOWDOWN_PREVIEW_DISABLE_GROUNDING != "1"` (same gate as `opponentLikelySets`/`speedContext`).

### 5. `PREVIEW_SYSTEM_PROMPT` (extend)

Append forme + lead-safety discipline (keep concise, in the existing prompt voice):

> Forme & lead safety: opponents may Mega-evolve or reveal a hidden battle forme — see `possibleFormes` and the `forme` rows in `speedContext`. Treat these as possibilities, not confirmed. A forme can change speed and typing (e.g. a Mega often outspeeds its base forme). Before you commit to `recommendedLead`, verify it is not outsped-and-threatened by the fastest plausible opposing forme, Megas included. If your recommended lead is outsped or cleanly OHKO'd by a plausible forme, do not hide it — state the risk and prefer a safer lead.

### 6. `preview_verifier` forme-awareness (extend, small)

The type-multiplier / type-relation checks resolve species names found in plan text against the opponent team, then look up base typing. Extend the resolution so a forme name mentioned in the plan ("Diancie-Mega", "Mega Charizard X", "Urshifu-Rapid-Strike") resolves to **that forme's** dex entry for typing, not the base. Concretely: when building the species-name → facts map (`_known_species_names` and the per-species facts lookups in `_type_multiplier_issues`), also register each opponent species' hidden-forme names (from `get_hidden_formes`) mapped to the forme's own types. This prevents false positives such as flagging "Charizard-Mega-X is Fire/Dragon" (correct for Mega-X; wrong if checked against base Fire/Flying). Sanitize-first remains the backstop for anything missed.

Scope guard: only opponent-team species' formes are registered (the plan's threat/lead claims are about the opponent). Keep the existing base-forme checks intact.

## Data flow

`opponent_team (incl. "-*")` → `build_possible_formes` + Mega-aware `build_speed_context` → `_preview_user_prompt` `possibleFormes` + `speedContext` forme rows → LLM reasons lead-safety under the new prompt discipline → plan. `preview_verifier` resolves forme names when checking the resulting plan's mechanics claims. Every step deterministic except the LLM reasoning; every grounding step degrades to empty.

## Error handling

- All forme lookups wrapped or guarded; a failed lookup skips the species (never raises).
- Unknown/misspelled species → `[]` (existing `get_pokemon_facts` "found: False" path).
- `-*` wildcard stripped to base before dex resolution.
- Disabled grounding (`SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1`) omits `possibleFormes` (speed-context forme rows follow the existing speedContext gate).

## Testing

**Unit (pytest):**
- `test_mechanics_facts`: `get_hidden_formes("Diancie")` → one Mega entry (name Diancie-Mega, spe 110, ability Magic Bounce, triggerItem Diancite); `get_hidden_formes("Urshifu-*")` → Rapid-Strike battle forme (Fighting/Water, spe 97); `get_hidden_formes("Garchomp")` → `[]`; unknown → `[]`; a species with two Megas (Charizard) → both X and Y.
- `test_preview_grounding`: `build_possible_formes(["Diancie","Urshifu-*","Garchomp"])` surfaces Diancie + Urshifu, omits Garchomp; empty team → `[]`. `build_speed_context` includes a `Diancie-Mega` row at 110 tagged as non-guaranteed and sorted above a 102 mon.
- `test_preview_plan`: prompt payload includes `possibleFormes` when opponents have formes; omitted when `SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1`.
- `test_preview_verifier` (or sanitize tests): a plan claiming "Charizard-Mega-X is Fire/Dragon" is NOT flagged; base-forme claims still checked; a genuinely wrong forme-typing claim still flagged.

**Live (manual, one run):** regenerate the QAQyyy plan via `scripts/evaluate-preview-plans.py --battle <qaqyyy postmortem> --run-mode real --preset anthropic-haiku-45-balanced`; expect `speedContext`/`possibleFormes` to carry Diancie-Mega at 110 and the plan to either not lead Garchomp or explicitly flag "Garchomp is outsped by Mega Diancie."

## Acceptance criteria

1. `get_hidden_formes` returns correct Mega and battle-forme data for Diancie, Urshifu-\*, Charizard (two Megas), and `[]` for a forme-less mon and unknown input.
2. The preview prompt payload carries a `possibleFormes` block and Mega speed rows in `speedContext` for a team with Mega-capable opponents; both omitted under disabled grounding.
3. The system prompt instructs a lead-safety check against the fastest plausible forme.
4. The verifier does not false-flag a correct Mega/forme typing claim; still flags a wrong one; base checks unchanged.
5. All new/updated pytest suites pass; full suite green.
6. Live regen of the QAQyyy preview shows Mega-Diancie speed present and lead-safety reasoning.

## Latency note (on the record)

This is a **quality** change, not a latency change. It slightly *increases* the prompt (a `possibleFormes` block + a few speed rows: ~a few hundred input tokens for a Mega-heavy team), which is negligible against the ~2,500–2,900 output tokens that dominate latency. It does **not** reduce the ~29s Haiku / ~58s Sonnet numbers. Its latency value is *indirect and real*: by fixing the grounding so the **fast** model (Haiku) produces a **correct** plan, it justifies keeping the Haiku default (~29s) instead of reaching for Sonnet (~58s) for quality — it defends the latency win rather than adding to it. An actual further latency reduction (output-schema slimming) is a separate, deliberately-decoupled follow-up.

## Out of scope / recorded follow-ups

- **Deterministic damage-based lead-safety** — extend the extension's `@smogon/calc` matrix to compute Mega-forme damage against your lead, and emit a hard "lead X is OHKO'd by Mega Y" grounding flag. The rigorous version of §5; needs extension work.
- **Output-schema slimming** for latency (fewer plan fields / shorter reasons → fewer output tokens).
- **Terastallization** typing/stat modeling.
- **Verifier weather/ability-immunity awareness** (carried from the previous spec's §9b) — still relevant, still deferred.
