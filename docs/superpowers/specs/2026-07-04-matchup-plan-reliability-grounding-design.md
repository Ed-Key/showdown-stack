# Matchup Plan: Reliability + Grounding

**Date:** 2026-07-04
**Status:** Approved design, pending implementation plan
**Scope decision:** Reliability + grounding (evals and mid-battle plan revision deferred)

## Problem

The team-preview matchup plan almost never appears in real games, and when it does it is often the deterministic fallback. Root causes, verified in code:

1. **Render race.** Generation uses the `anthropic-sonnet-46-high` preset (Sonnet 4.6, 6000 max output tokens, 120s timeout; `dashboard_config.py`) plus up to 2 verifier-repair LLM round-trips (`preview_plan.py:615-640`). End-to-end latency is 20s-minutes. Rendering is gated to team preview or turn ≤ 1 (`content.ts:324-328`, `MATCHUP_PLAN_RENDER_TURN_LIMIT = 1`). Plans routinely arrive after the window and are discarded (`skipped stale render`, `content.ts:430-433`).
2. **Sticky silent fallback.** Any model failure (timeout, missing key, validation failure after repairs) returns `_fallback_plan()` — hardcoded to the user's own team (Garchomp, Ogerpon-Wellspring, Volcarona; `preview_plan.py:199-294`). Both the client cache (`preview-plan-client.ts:23`) and `matchupPlansByBattle` cache it for the whole battle, so one transient failure means deterministic-forever.
3. **Plan-level rejection.** The regex-based verifier (`preview_verifier.py`) flags claims; each flagged claim costs a repair round-trip, and unresolved issues after 2 repairs discard the entire plan for the fallback. One bad sentence kills four good ones.
4. **Weak grounding.** The model receives: user team (full sets), opponent species names, a typing fact-pack. It does not receive the belief-aware damage matrix the extension already computes at preview (`content.ts:1859-1882`), the Smogon usage priors the proxy already loads (`priors.py`), or speed context (`stats.py`). Plans are generic because their inputs are generic.

## Decisions (made with user)

- **Plan lifetime:** persistent + progressive. Card appears at preview as loading, upgrades in place when the plan lands, stays mounted (collapsible) all game.
- **Scope:** reliability + grounding in this pass. Eval-harness fixtures, mid-battle plan revision ("Deep Battle Coach"), server-side damage calc, and streaming output are recorded follow-ups, not in scope.
- **Approach:** one grounded LLM call assembled at the seam — extension contributes the damage summary it already computes; proxy contributes priors/speed/mechanics facts; sanitize-first verification.

## Design

### 1. Plan lifecycle (extension)

- Replace `shouldRenderMatchupPlan` with `canRenderMatchupPlan(battleId)`: `sameBattle && !ended`. No turn/preview condition. Delete the stale-render removal branch (`content.ts:430-433`) and the render uses of `MATCHUP_PLAN_RENDER_TURN_LIMIT`.
- Request trigger unchanged in spirit: once per battle, whenever both team lists are non-empty (team-preview branch `content.ts:1841-1847`, or first decision point for late joins via `requestMatchupPlanFromBattle`, which loses its `turn > limit` early-return).
- Card gains the PV-chain-style collapse toggle (`attachPanelToggle`), persisted at localStorage key `showdownCopilot.matchupPlanCollapsed`.
- Pure decision logic extracted to **`extension/lib/plan-lifecycle.ts`** (new):
  - `canRender(state: {sameBattle, ended}): boolean`
  - `shouldRequest(entry: PlanCacheEntry | undefined, state, nowMs): boolean`
  - `PlanCacheEntry = { status: 'inflight' | 'model' | 'fallback' | 'error'; attempts: number; lastAttemptMs: number; permanent: boolean }`
  - Constants: `MAX_ATTEMPTS = 3` (1 initial + 2 retries), `RETRY_SPACING_MS = 15_000`.
  - This extraction also removes ~80 lines of lifecycle logic from `content.ts`.

### 2. Cache and retry (preview-plan-client.ts)

- Cache entries become `PlanCacheEntry & { response: PreviewPlanResponse | null }`.
- **Permanent** (never re-request this battle): `source === 'model'`, or `fallbackReason` contains "not configured".
- **Transient** (retry per budget at later decision points): everything else — network error, `!response.ok`, and any other fallback `source`/`fallbackReason`.
- Retry decisions delegate to `plan-lifecycle.ts` so policy is unit-testable.

### 3. Grounding pack

**Request schema** (`PreviewPlanRequest` gains optional field; `teamStats` kept for back-compat):

```python
class DamageCell(BaseModel):
    attacker: str; defender: str; move: str
    pct: str            # "68-81"
    ohko: bool
    direction: Literal["mine", "opp"]

class MonSummary(BaseModel):
    species: str; survives: int; threatens: int

class PreviewGrounding(BaseModel):
    damageCells: list[DamageCell] = []      # capped at 24
    monSummaries: list[MonSummary] = []
    source: str = "extension-damage-matrix"
```

**Extension builder** — **`extension/lib/preview-grounding.ts`** (new): built from the `myAtk`/`oppAtk` matrices and leaderboard rows already computed at preview. Cell selection: all OHKO cells first, then remaining cells by pct descending with pct ≥ 50, both directions, cap 24 total. Omitted entirely if the matrix is unavailable (belief fetch failed) — generation proceeds without it.

**Proxy enrichment** — **`src/showdown_copilot/preview_grounding.py`** (new):

- `build_opponent_likely_sets(species: list[str]) -> list[dict]`: per opponent species from `priors.py` chaos data — top 4 moves (usage ≥ 20%), top 2 items (≥ 20%), abilities, top Tera types. Each entry labeled `"basis": "usage-statistics"`. Species without data are omitted.
- `build_speed_context(my_team, opp_species) -> dict`: base-speed ordering of all 12, plus `scarfPlausible` flags where Choice Scarf item prior ≥ 15%.

**Prompt changes** (`preview_plan.py`): user payload gains `damageSummary` (from `req.grounding`), `opponentLikelySets`, `speedContext`. `PREVIEW_SYSTEM_PROMPT` gains: *"Damage percentages and usage statistics are supplied. Cite the supplied numbers; never invent numbers. If a needed number is not supplied, phrase the claim qualitatively. Usage statistics describe likely sets, not revealed information — attribute them as such."*

### 4. Verification: sanitize-first

- `LIST_FIELDS = {dangerRules, mainThreats, preserveTargets, leadRules, backupLeads, avoidLeads, earlyPriorities, uncertainties}`; `CORE_FIELDS = {archetype, summary, winPath, recommendedLead}`.
- New `sanitize_preview_plan(plan, issues) -> (plan, removed: list[str], core_issues: list[PreviewPlanIssue])` in `preview_verifier.py`: parses `issue.path` (e.g. `plan.dangerRules[2].rule`), drops flagged list items (indexes deduped, removed descending), appends one aggregated uncertainty: `"N generated claim(s) removed by the mechanics checker."`
- New flow in `build_preview_plan`: generate → verify → sanitize list items → if core issues: **one** repair call scoped to core issues → re-verify → sanitize → if core issues remain: fallback with reason `"core mechanics validation failed"`.
- `SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS` default changes 2 → 1 and applies only to core issues.
- `PreviewPlanResponse` gains `sanitizedClaims: list[str] = []`.
- Preview token clamp: `min(preset maxOutputTokens, 2500)`.

### 5. Honest, de-personalized fallback

- Archetype detection (rain/sun/sand/stall constants) stays — it keys on opponent species, which is legitimately deterministic.
- Remove all hardcoded user-team logic (Garchomp lead, Ogerpon/Volcarona rules, backup-lead species list).
- `recommendedLead`: highest `survives + threatens` from `grounding.monSummaries` when present, else first team slot; generic reason.
- `preserveTargets`: up to 2 mons with `threatens ≥ 2` from monSummaries; none otherwise.
- Danger rules: ≤ 2 generic archetype-derived rules (e.g. rain → "avoid trading your primary Water answer for chip").
- UI: fallback renders an amber chip **"heuristic · model unavailable"** (title = `fallbackReason`); model responses render a neutral model-name chip. If `sanitizedClaims.length > 0`, a subtle "N claims removed by checker" line appears.

### 6. Error handling matrix

| Condition | Behavior |
|---|---|
| Proxy unreachable / network error | Loading card → "plan unavailable — retrying"; retry budget applies |
| Provider key missing | Labeled heuristic fallback + config hint; permanent, no retry |
| Provider timeout / 5xx | Labeled heuristic fallback; transient, retry budget applies |
| Core issues persist after 1 repair | Fallback, reason "core mechanics validation failed"; transient |
| List-item issues only | Items dropped, plan ships, `sanitizedClaims` populated |
| Damage matrix / priors unavailable | Section omitted from prompt; generation proceeds |
| Battle ended / different battle | No render; cache untouched |

### 7. Testing

**Extension (vitest):**
- `plan-lifecycle.test.ts`: canRender; shouldRequest across attempts/spacing/permanent/inflight.
- `preview-grounding.test.ts`: cap at 24, OHKO priority, ≥50% threshold, both directions, empty-matrix omission.
- `matchup-plan-card.test.ts`: fallback chip, model chip, sanitized-claims line.
- `mount-order.test.ts`: card persists when rendered at turn 5 of the same battle.

**Python (pytest):**
- `test_preview_sanitize.py`: path parsing, list-item drop + aggregated uncertainty, core-repair-once, fallback when core issues persist.
- `test_preview_grounding.py`: likely-sets thresholds/formatting, missing-species omission, speed context + scarf flags.
- `test_preview_plan.py` updates: `sanitizedClaims` in response, token clamp, de-personalized fallback (no Garchomp for a Garchomp-less team), grounding keys present in prompt payload.

### 8. Acceptance criteria

1. In a live battle with the stack running, the plan card appears at team preview and the full model plan is visible by turn 2-3 and remains visible (collapsible) until battle end.
2. Killing the provider mid-preview yields a visibly labeled heuristic plan; restoring it and reaching the next decision point within the retry budget yields a model plan.
3. A plan with one invalid danger rule ships with that rule removed and the rest intact (no fallback).
4. `_fallback_plan` output contains no species absent from the request.
5. Prompt payload includes damage summary, likely sets, and speed context when available.
6. All new/updated vitest + pytest suites pass.

### 9. Build stages and human-in-the-loop verification

Build proceeds in six independently verifiable stages. Each stage ships with its automated tests **and** a hands-on check the user runs before the next stage starts.

**Verification tooling added by this work (small, permanent):**

- `SHOWDOWN_PREVIEW_LOG_PROMPT=1` — proxy logs the fully assembled prompt payload for `/preview-plan`, so the exact model input is inspectable.
- `scripts/evaluate-preview-plans.py` gains `--battle <postmortem-file>` (replay one real saved preview offline) and `--grounding on|off` (A/B the grounding pack on the same input), printing plan + `sanitizedClaims`.
- localStorage override `showdownCopilot.previewPlanRunMode` (`auto`|`fake`|`real`), read beside the existing preset override — forces the fallback path on demand for zero-cost UI testing and demos.
- `__scPreviewGrounding()` console helper (same pattern as `__scDebug`) — prints the compact grounding pack for the current battle.

| Stage | Ships | Automated | User check |
|---|---|---|---|
| 1 | Persistent card + `plan-lifecycle.ts` extraction | plan-lifecycle tests | Live battle: card appears at preview and is still there at turn 3+; console filter `sc:preview-plan` shows `response` and never `skipped stale render`; collapse toggle survives |
| 2 | Cache/retry policy + fallback chip UI | client retry tests, card render tests | Kill the proxy at preview → "retrying" state; restart → model plan by next decision. Unset `ANTHROPIC_API_KEY` → amber heuristic chip, exactly one request in console |
| 3 | De-personalized fallback | fallback pytest | Set `previewPlanRunMode='fake'` in console → heuristic plan references only the actual team; toggle back → model plan returns |
| 4 | Extension grounding builder + `__scPreviewGrounding()` | preview-grounding tests | Run `__scPreviewGrounding()` at preview; spot-check its OHKO cells against the damage-matrix panel |
| 5 | Proxy enrichment + prompt logging + replay flags | preview-grounding pytest | `SHOWDOWN_PREVIEW_LOG_PROMPT=1`, replay one saved battle with `--grounding on` vs `off`; read both plans side by side — grounded one must cite supplied numbers |
| 6 | Sanitize-first flow + `sanitizedClaims` UI line | sanitize pytest | Replay several saved previews; confirm plans ship with dropped claims listed instead of collapsing to fallback; live card shows "N claims removed" when it happens |

Final acceptance = the criteria in section 8 run as one ~15-minute live-battle checklist session.

### 10. Out of scope / recorded follow-ups

- Eval harness: extend `scripts/evaluate-preview-plans.py` with ~30 real-preview fixtures from the postmortem archive, rubric + LLM-judge scoring across model × grounding variants.
- Mid-battle plan revision on revealed info ("Deep Battle Coach" tier).
- Server-side damage calculation (engine endpoint or node sidecar) making `/preview-plan` self-contained and eval-reusable.
- Streaming plan output.
