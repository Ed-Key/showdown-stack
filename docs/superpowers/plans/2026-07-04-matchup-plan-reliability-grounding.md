# Matchup Plan Reliability + Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the team-preview matchup plan always appear (persistent, progressive card), stop silently degrading to a canned plan, and feed the LLM planner the deterministic facts the system already computes (damage matrix, usage priors, speed tiers).

**Architecture:** Extension-side lifecycle/retry state moves into pure modules (`plan-lifecycle.ts`, rewritten `preview-plan-client.ts`) consumed by `content.ts`; the card renders whenever a response exists for the current battle and persists all game. The proxy's `build_preview_plan` gains a grounding pack (extension-supplied damage cells + proxy-side usage priors and speed context) and switches from repair-loop-or-fallback to sanitize-first verification (drop flagged list items, one repair for core fields only).

**Tech Stack:** WXT/TypeScript extension (vitest), FastAPI/Pydantic proxy (pytest + pytest-asyncio), `@smogon/calc` damage matrices, Smogon chaos usage data via `priors.PriorsSource`.

**Spec:** `docs/superpowers/specs/2026-07-04-matchup-plan-reliability-grounding-design.md`

## Global Constraints

- Repo root: `/Users/edkiboma/Projects/pokemon-ai/showdown-stack`. Python commands run from repo root via `uv run …`; extension commands run from `extension/` via `npx vitest run …` (never bare `vitest`, which watches).
- Both suites are fully green at the starting commit (`de374cf`): pytest 400 passed / 8 skipped, vitest 282/282. Every "full vitest suite" expectation below means: **all tests pass** (the formerly failing flavor-strip test was deleted before execution began).
- Never modify `engine/`, `engine-doubles/`, `sidecar/` (separate repos, gitignored).
- Constants (from spec, exact values): retry budget `MAX_PLAN_ATTEMPTS = 3` (1 initial + 2 retries), `PLAN_RETRY_SPACING_MS = 15_000`; grounding cell cap **24**, notable-damage floor **50%**; usage floors: moves/items/abilities/tera **20%**, scarf-plausible **15%**; preview output-token clamp **2500**; `SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS` default changes **2 → 1** and applies to core issues only.
- localStorage keys (exact): `showdownCopilot.matchupPlanCollapsed`, `showdownCopilot.previewPlanRunMode` (existing: `showdownCopilot.previewPlanPresetId`).
- Env vars introduced: `SHOWDOWN_PREVIEW_LOG_PROMPT=1` (log assembled prompt), `SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1` (skip proxy-side grounding enrichment; used by the evaluator's `--grounding off`).
- Commit after every task with the message given in its final step.
- Stage gates: after Tasks 3, 5, 6, 8, 12, and 15, STOP and have the user run the corresponding manual check from spec section 9 before continuing.

---

### Task 1: `plan-lifecycle.ts` — pure render/request/retry decisions

**Files:**
- Create: `extension/lib/plan-lifecycle.ts`
- Test: `extension/test/lib/plan-lifecycle.test.ts`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces (used by Tasks 2 and 3):
  - `type PlanStatus = 'inflight' | 'model' | 'fallback' | 'error'`
  - `interface PlanCacheEntry { status: PlanStatus; attempts: number; lastAttemptMs: number; permanent: boolean }`
  - `canRenderPlan(state: { sameBattle: boolean; ended: boolean }): boolean`
  - `isPermanentResponse(source: string, fallbackReason?: string | null): boolean`
  - `shouldRequestPlan(entry: PlanCacheEntry | undefined, nowMs: number): boolean`
  - `const MAX_PLAN_ATTEMPTS = 3`, `const PLAN_RETRY_SPACING_MS = 15_000`

- [ ] **Step 1: Write the failing test**

Create `extension/test/lib/plan-lifecycle.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  MAX_PLAN_ATTEMPTS,
  PLAN_RETRY_SPACING_MS,
  canRenderPlan,
  isPermanentResponse,
  shouldRequestPlan,
  type PlanCacheEntry,
} from '../../lib/plan-lifecycle';

const T0 = 1_000_000;

function entry(overrides: Partial<PlanCacheEntry>): PlanCacheEntry {
  return { status: 'error', attempts: 1, lastAttemptMs: T0, permanent: false, ...overrides };
}

describe('canRenderPlan', () => {
  it('renders for the same, unfinished battle regardless of turn', () => {
    expect(canRenderPlan({ sameBattle: true, ended: false })).toBe(true);
  });
  it('does not render for a different battle or an ended one', () => {
    expect(canRenderPlan({ sameBattle: false, ended: false })).toBe(false);
    expect(canRenderPlan({ sameBattle: true, ended: true })).toBe(false);
  });
});

describe('isPermanentResponse', () => {
  it('model responses are permanent', () => {
    expect(isPermanentResponse('model', null)).toBe(true);
  });
  it('provider-not-configured fallbacks are permanent', () => {
    expect(isPermanentResponse('fallback', 'model provider not configured or fake mode selected')).toBe(true);
  });
  it('other fallbacks are transient', () => {
    expect(isPermanentResponse('fallback', 'model preview failed: timeout')).toBe(false);
    expect(isPermanentResponse('fallback', null)).toBe(false);
  });
});

describe('shouldRequestPlan', () => {
  it('requests when no entry exists', () => {
    expect(shouldRequestPlan(undefined, T0)).toBe(true);
  });
  it('never requests while inflight or permanent', () => {
    expect(shouldRequestPlan(entry({ status: 'inflight' }), T0 + 60_000)).toBe(false);
    expect(shouldRequestPlan(entry({ status: 'model', permanent: true }), T0 + 60_000)).toBe(false);
  });
  it('waits out the retry spacing, then retries', () => {
    const e = entry({ status: 'error', attempts: 1 });
    expect(shouldRequestPlan(e, T0 + PLAN_RETRY_SPACING_MS - 1)).toBe(false);
    expect(shouldRequestPlan(e, T0 + PLAN_RETRY_SPACING_MS)).toBe(true);
  });
  it('stops after the attempt budget', () => {
    const e = entry({ status: 'error', attempts: MAX_PLAN_ATTEMPTS });
    expect(shouldRequestPlan(e, T0 + 10 * PLAN_RETRY_SPACING_MS)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npx vitest run test/lib/plan-lifecycle.test.ts`
Expected: FAIL — cannot resolve `../../lib/plan-lifecycle`.

- [ ] **Step 3: Write the implementation**

Create `extension/lib/plan-lifecycle.ts`:

```ts
// Pure decision logic for the matchup-plan card lifecycle: when to render,
// when to (re)request, and which responses end the retry loop. Kept free of
// DOM/fetch so the retry policy is unit-testable (spec §1-2).

export type PlanStatus = 'inflight' | 'model' | 'fallback' | 'error';

export interface PlanCacheEntry {
  status: PlanStatus;
  attempts: number;
  lastAttemptMs: number;
  permanent: boolean;
}

export const MAX_PLAN_ATTEMPTS = 3;
export const PLAN_RETRY_SPACING_MS = 15_000;

export function canRenderPlan(state: { sameBattle: boolean; ended: boolean }): boolean {
  return state.sameBattle && !state.ended;
}

export function isPermanentResponse(source: string, fallbackReason?: string | null): boolean {
  if (source === 'model') return true;
  return /not configured/i.test(fallbackReason ?? '');
}

export function shouldRequestPlan(entry: PlanCacheEntry | undefined, nowMs: number): boolean {
  if (!entry) return true;
  if (entry.status === 'inflight') return false;
  if (entry.permanent) return false;
  if (entry.attempts >= MAX_PLAN_ATTEMPTS) return false;
  return nowMs - entry.lastAttemptMs >= PLAN_RETRY_SPACING_MS;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd extension && npx vitest run test/lib/plan-lifecycle.test.ts`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add extension/lib/plan-lifecycle.ts extension/test/lib/plan-lifecycle.test.ts
git commit -m "feat(extension): pure plan-lifecycle decisions (render gate, retry budget)"
```

---

### Task 2: Rewrite `preview-plan-client.ts` — entry cache + retry policy

**Files:**
- Modify: `extension/lib/preview-plan-client.ts` (full rewrite of the fetch/cache section; keep `previewPokemonFromSnapshot` unchanged)
- Test: `extension/test/lib/preview-plan-client.test.ts` (create)

**Interfaces:**
- Consumes (Task 1): `shouldRequestPlan`, `isPermanentResponse`, `PlanCacheEntry`.
- Produces (used by Task 3):
  - `type PlanEntry = PlanCacheEntry & { response: PreviewPlanResponse | null }`
  - `cachedPreviewPlan(battleId: string): PreviewPlanResponse | null`
  - `previewPlanEntry(battleId: string): PlanEntry | undefined`
  - `requestPreviewPlan(proxyUrl: string, request: PreviewPlanRequest, nowMs?: number): Promise<PreviewPlanResponse | null>` — safe to call every poll; no-ops when `shouldRequestPlan` says no.
  - `resetPreviewPlanState(): void` (tests only)
  - `previewPokemonFromSnapshot(snapshot: any)` (unchanged export)

- [ ] **Step 1: Write the failing test**

Create `extension/test/lib/preview-plan-client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cachedPreviewPlan,
  previewPlanEntry,
  requestPreviewPlan,
  resetPreviewPlanState,
} from '../../lib/preview-plan-client';
import { PLAN_RETRY_SPACING_MS } from '../../lib/plan-lifecycle';

const REQUEST = {
  battleId: 'battle-x',
  format: 'gen9nationaldex',
  myTeam: [],
  opponentTeam: ['Pelipper'],
} as any;

function jsonResponse(body: any, ok = true) {
  return { ok, json: async () => body } as any;
}

const MODEL_BODY = {
  battleId: 'battle-x', format: 'gen9nationaldex', provider: 'anthropic',
  mode: 'auto', source: 'model', model: 'claude-sonnet-4-6', latencyMs: 12,
  plan: { archetype: 'rain offense' },
};
const TRANSIENT_FALLBACK_BODY = {
  ...MODEL_BODY, source: 'fallback', model: null,
  fallbackReason: 'model preview failed: timeout',
};
const PERMANENT_FALLBACK_BODY = {
  ...MODEL_BODY, source: 'fallback', model: null,
  fallbackReason: 'model provider not configured or fake mode selected',
};

afterEach(() => {
  resetPreviewPlanState();
  vi.unstubAllGlobals();
});

describe('requestPreviewPlan', () => {
  it('caches model responses permanently (no second fetch)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(MODEL_BODY));
    vi.stubGlobal('fetch', fetchMock);
    await requestPreviewPlan('http://p', REQUEST, 1000);
    await requestPreviewPlan('http://p', REQUEST, 1000 + 10 * PLAN_RETRY_SPACING_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(cachedPreviewPlan('battle-x')?.source).toBe('model');
  });

  it('retries transient fallbacks after the spacing, up to the budget', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(TRANSIENT_FALLBACK_BODY));
    vi.stubGlobal('fetch', fetchMock);
    await requestPreviewPlan('http://p', REQUEST, 1000);
    await requestPreviewPlan('http://p', REQUEST, 1000 + 1);                            // too soon
    await requestPreviewPlan('http://p', REQUEST, 1000 + PLAN_RETRY_SPACING_MS);        // retry 1
    await requestPreviewPlan('http://p', REQUEST, 1000 + 2 * PLAN_RETRY_SPACING_MS);    // retry 2
    await requestPreviewPlan('http://p', REQUEST, 1000 + 9 * PLAN_RETRY_SPACING_MS);    // over budget
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(previewPlanEntry('battle-x')?.attempts).toBe(3);
  });

  it('does not retry provider-not-configured fallbacks', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(PERMANENT_FALLBACK_BODY));
    vi.stubGlobal('fetch', fetchMock);
    await requestPreviewPlan('http://p', REQUEST, 1000);
    await requestPreviewPlan('http://p', REQUEST, 1000 + 10 * PLAN_RETRY_SPACING_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(previewPlanEntry('battle-x')?.permanent).toBe(true);
  });

  it('keeps the last fallback response visible while a retry fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(TRANSIENT_FALLBACK_BODY))
      .mockRejectedValueOnce(new Error('net down'));
    vi.stubGlobal('fetch', fetchMock);
    await requestPreviewPlan('http://p', REQUEST, 1000);
    await requestPreviewPlan('http://p', REQUEST, 1000 + PLAN_RETRY_SPACING_MS);
    expect(previewPlanEntry('battle-x')?.status).toBe('error');
    expect(cachedPreviewPlan('battle-x')?.source).toBe('fallback');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npx vitest run test/lib/preview-plan-client.test.ts`
Expected: FAIL — `cachedPreviewPlan` / `previewPlanEntry` / `requestPreviewPlan` / `resetPreviewPlanState` are not exported.

- [ ] **Step 3: Rewrite the client**

Replace the fetch/cache portion of `extension/lib/preview-plan-client.ts` (keep the existing `previewPokemonFromSnapshot` function at the bottom, unchanged):

```ts
import type { PreviewPlanRequest, PreviewPlanResponse } from './matchup-plan';
import {
  isPermanentResponse,
  shouldRequestPlan,
  type PlanCacheEntry,
} from './plan-lifecycle';

export type PlanEntry = PlanCacheEntry & { response: PreviewPlanResponse | null };

const entries = new Map<string, PlanEntry>();

function keyFor(request: PreviewPlanRequest): string {
  return request.battleId || JSON.stringify(request.opponentTeam);
}

export function cachedPreviewPlan(battleId: string): PreviewPlanResponse | null {
  return entries.get(battleId)?.response ?? null;
}

export function previewPlanEntry(battleId: string): PlanEntry | undefined {
  return entries.get(battleId);
}

export function resetPreviewPlanState(): void {
  entries.clear();
}

export async function requestPreviewPlan(
  proxyUrl: string,
  request: PreviewPlanRequest,
  nowMs: number = Date.now(),
): Promise<PreviewPlanResponse | null> {
  const key = keyFor(request);
  const prev = entries.get(key);
  if (!shouldRequestPlan(prev, nowMs)) return prev?.response ?? null;

  const attempts = (prev?.attempts ?? 0) + 1;
  const keepResponse = prev?.response ?? null;
  entries.set(key, { status: 'inflight', attempts, lastAttemptMs: nowMs, permanent: false, response: keepResponse });

  try {
    const response = await fetch(`${proxyUrl}/preview-plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    const body = response.ok ? (await response.json() as PreviewPlanResponse) : null;
    if (!body?.plan) {
      entries.set(key, { status: 'error', attempts, lastAttemptMs: nowMs, permanent: false, response: keepResponse });
      return null;
    }
    entries.set(key, {
      status: body.source === 'model' ? 'model' : 'fallback',
      attempts,
      lastAttemptMs: nowMs,
      permanent: isPermanentResponse(body.source, body.fallbackReason),
      response: body,
    });
    return body;
  } catch (err) {
    console.warn('[sc:preview-plan] fetch failed', err);
    entries.set(key, { status: 'error', attempts, lastAttemptMs: nowMs, permanent: false, response: keepResponse });
    return null;
  }
}
```

- [ ] **Step 4: Run tests**

Run: `cd extension && npx vitest run test/lib/preview-plan-client.test.ts test/lib/plan-lifecycle.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add extension/lib/preview-plan-client.ts extension/test/lib/preview-plan-client.test.ts
git commit -m "feat(extension): preview-plan client with retry budget and honest caching"
```

---

### Task 3: `content.ts` — persistent card, client delegation, collapse toggle

**Files:**
- Modify: `extension/entrypoints/content.ts` (the matchup-plan block, currently lines ~228-231, ~302-345, ~360-466, and the call at ~1847)
- Modify: `extension/styles/tcg.css` (collapse rule)
- Test: `extension/test/mount-order.test.ts` (add one persistence case)

**Interfaces:**
- Consumes (Tasks 1-2): `canRenderPlan`, `MAX_PLAN_ATTEMPTS`, `cachedPreviewPlan`, `previewPlanEntry`, `requestPreviewPlan`.
- Produces: `renderPlanForBattle(battleId: string): void` and a simplified `requestMatchupPlan(b, br, mySnaps, oppSnaps)` inside `content.ts`'s `main()` closure (Task 8 extends `requestMatchupPlan` with grounding).

- [ ] **Step 1: Add the mount-order persistence test**

In `extension/test/mount-order.test.ts`, add (adjust imports to the file's existing style — it already imports `renderMatchupPlanCard` helpers or mounts panels into a root):

```ts
it('matchup plan card stays mounted when re-rendered mid-battle', () => {
  const root = document.createElement('div');
  const card1 = renderMatchupPlanCard(FIXTURE_RESPONSE);
  mountOrReplace(root, {
    newEl: card1,
    replaceTargets: ['.sc-matchup-plan-card'],
    anchors: [],
    fallback: (r, el) => r.prepend(el),
  });
  // Simulate a turn-5 re-render of the same battle: replaces in place, never removes.
  const card2 = renderMatchupPlanCard(FIXTURE_RESPONSE);
  mountOrReplace(root, {
    newEl: card2,
    replaceTargets: ['.sc-matchup-plan-card'],
    anchors: [],
    fallback: (r, el) => r.prepend(el),
  });
  expect(root.querySelectorAll('.sc-matchup-plan-card').length).toBe(1);
});
```

If `mount-order.test.ts` has no matchup-plan fixture yet, define `FIXTURE_RESPONSE` locally with the minimal shape `renderMatchupPlanCard` needs (see `test/lib/plan-fit.test.ts` `basePlan` for a complete `MatchupPlan` literal to reuse; wrap it as `{ source: 'model', model: 'claude-sonnet-4-6', provider: 'anthropic', plan: basePlan } as any`).

Run: `cd extension && npx vitest run test/mount-order.test.ts` — the new case should PASS already (mountOrReplace semantics); it exists to lock the invariant.

- [ ] **Step 2: Rewire content.ts**

In `extension/entrypoints/content.ts`:

**(a)** Update imports:

```ts
import { cachedPreviewPlan, previewPlanEntry, requestPreviewPlan, previewPokemonFromSnapshot } from '../lib/preview-plan-client';
import { canRenderPlan, MAX_PLAN_ATTEMPTS } from '../lib/plan-lifecycle';
```

Remove `fetchPreviewPlan` from the old import.

**(b)** Delete these declarations (~lines 228-231): `matchupPlansByBattle`, `matchupPlanRequests`, `lastPlanFit` stays, `MATCHUP_PLAN_RENDER_TURN_LIMIT`.

**(c)** Replace `currentMatchupPlan` (~302-305):

```ts
function currentMatchupPlan(battleId?: string): MatchupPlan | null {
  if (!battleId) return null;
  return cachedPreviewPlan(battleId)?.plan ?? null;
}
```

**(d)** Replace `shouldRenderMatchupPlan` (~324-328):

```ts
function canRenderMatchupPlan(battleId: string): boolean {
  return canRenderPlan(currentBattlePreviewState(battleId));
}
```

**(e)** Keep `mountMatchupPlanCard` and `removeMatchupPlanCard` as-is, and add:

```ts
function renderPlanForBattle(battleId: string): void {
  if (!canRenderMatchupPlan(battleId)) return;
  const cached = cachedPreviewPlan(battleId);
  if (cached) {
    const card = renderMatchupPlanCard(cached);
    mountMatchupPlanCard(card);
    attachPanelToggle(card, '.sc-plan-topline', 'showdownCopilot.matchupPlanCollapsed');
    return;
  }
  const entry = previewPlanEntry(battleId);
  if (!entry) return;
  if (entry.status === 'inflight') {
    mountMatchupPlanCard(renderMatchupPlanLoading('Building matchup plan...'));
  } else {
    mountMatchupPlanCard(renderMatchupPlanLoading(
      entry.attempts >= MAX_PLAN_ATTEMPTS
        ? 'Matchup plan unavailable.'
        : 'Matchup plan unavailable — will retry.',
    ));
  }
}
```

**(f)** Replace the whole `requestMatchupPlan` (~360-445) with:

```ts
function requestMatchupPlan(b: any, br: any, mySnaps: any[], oppSnaps: any[]): void {
  const battleId = String(br?.id ?? '');
  if (!battleId || !mySnaps.length || !oppSnaps.length) return;
  const startedAtMs = Date.now();
  void requestPreviewPlan(PROXY_BASE_URL, {
    battleId,
    format: String(b?.tier || 'gen9nationaldex'),
    myTeam: mySnaps.map(previewPokemonFromSnapshot),
    opponentTeam: oppSnaps.map((p: any) => String(p?.species ?? '')).filter(Boolean),
    teamStats: { source: 'live-team-preview' },
    presetId: configuredPreviewPlanPresetId(),
    runMode: 'auto',
  }).then((response) => {
    if (response) {
      console.log('[sc:preview-plan] response', {
        battleId,
        latencyMs: Date.now() - startedAtMs,
        source: response.source,
        model: response.model,
        fallbackReason: response.fallbackReason,
      });
    }
    renderPlanForBattle(battleId);
  });
  renderPlanForBattle(battleId); // paint loading/cached state immediately
}
```

**(g)** Replace `requestMatchupPlanFromBattle` (~447-466) with (turn-limit gone; dedupe now lives in the client):

```ts
function requestMatchupPlanFromBattle(b: any, br: any): void {
  if (!b || !br?.id) return;
  const myActiveLive = b.mySide?.active?.[0] ?? null;
  const mySnaps = (b.myPokemon || []).map((p: any) => buildMyPokemon(p, null, win, myActiveLive));
  const oppSnaps = (b.farSide?.pokemon || []).map((p: any) => buildOppPokemon(p, win));
  if (!mySnaps.length || !oppSnaps.length) return;
  requestMatchupPlan(b, br, mySnaps, oppSnaps);
}
```

**(h)** The team-preview call site (~1847) and decision call site (~1977) stay as-is (`requestMatchupPlan(b, br, mySnaps, oppSnaps)` / `requestMatchupPlanFromBattle(b, br)`); with the client-side dedupe, calling every poll tick is now safe and is what drives retries.

- [ ] **Step 3: Add collapse CSS**

In `extension/styles/tcg.css`, append:

```css
/* Matchup plan card: persistent + collapsible (header click toggles). */
.sc-matchup-plan-card.collapsed > :not(.sc-plan-topline) { display: none; }
.sc-matchup-plan-card .sc-plan-topline { user-select: none; }
```

- [ ] **Step 4: Typecheck + full suite**

Run: `cd extension && npx tsc --noEmit -p tsconfig.json` (if the repo has no standalone tsc script, `npx vitest run` compiles enough to surface type errors)
Run: `cd extension && npx vitest run`
Expected: all pass except the known `tcg-card` flavor-strip failure.

- [ ] **Step 5: Commit**

```bash
git add extension/entrypoints/content.ts extension/styles/tcg.css extension/test/mount-order.test.ts
git commit -m "feat(extension): matchup plan card persists all game with retry-aware states"
```

**STAGE 1 GATE — user check:** live battle; card appears at preview, still present at turn 3+; console shows `[sc:preview-plan] response` and never `skipped stale render`; header click collapses and the state survives.

---

### Task 4: Matchup-plan card source chips (model vs heuristic)

**Files:**
- Modify: `extension/panels/matchup-plan-card.ts`
- Modify: `extension/styles/tcg.css`
- Test: `extension/test/panels/matchup-plan-card.test.ts` (create; there is no existing card test)

**Interfaces:**
- Consumes: `PreviewPlanResponse` (existing).
- Produces: unchanged signatures; DOM adds `.sc-plan-chip.model` / `.sc-plan-chip.heuristic` inside `.sc-plan-topline`.

- [ ] **Step 1: Write the failing test**

Create `extension/test/panels/matchup-plan-card.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { renderMatchupPlanCard } from '../../panels/matchup-plan-card';
import type { MatchupPlan, PreviewPlanResponse } from '../../lib/matchup-plan';

const plan: MatchupPlan = {
  archetype: 'rain offense', confidence: 'high',
  summary: 'Rain setter plus Water sweepers.', winPath: 'Preserve the Water answer.',
  recommendedLead: { pokemon: 'Garchomp', rating: 'safe', reason: 'Info lead.' },
  backupLeads: [], avoidLeads: [], leadRules: [],
  preserveTargets: [], mainThreats: [], dangerRules: [],
  earlyPriorities: [], uncertainties: [],
};

function response(overrides: Partial<PreviewPlanResponse>): PreviewPlanResponse {
  return {
    battleId: 'b', format: 'gen9nationaldex', provider: 'anthropic', mode: 'auto',
    source: 'model', model: 'claude-sonnet-4-6', latencyMs: 10, plan,
    ...overrides,
  } as PreviewPlanResponse;
}

describe('renderMatchupPlanCard source labeling', () => {
  it('shows a model chip for model plans', () => {
    const el = renderMatchupPlanCard(response({ source: 'model' }));
    const chip = el.querySelector('.sc-plan-chip.model');
    expect(chip?.textContent).toContain('claude-sonnet-4-6');
    expect(el.querySelector('.sc-plan-chip.heuristic')).toBeNull();
  });

  it('shows an amber heuristic chip with the reason for fallbacks', () => {
    const el = renderMatchupPlanCard(response({
      source: 'fallback', model: null,
      fallbackReason: 'model preview failed: timeout',
    }));
    const chip = el.querySelector<HTMLElement>('.sc-plan-chip.heuristic');
    expect(chip?.textContent).toContain('heuristic');
    expect(chip?.title).toContain('timeout');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npx vitest run test/panels/matchup-plan-card.test.ts`
Expected: FAIL — `.sc-plan-chip.model` not found (current markup renders `.sc-plan-source`).

- [ ] **Step 3: Implement**

In `extension/panels/matchup-plan-card.ts`, replace the `sc-plan-source` div inside the template. Current line:

```ts
      <div class="sc-plan-source">${escapeHtml(response.source)} · ${escapeHtml(plan.confidence)}</div>
```

New code (compute `sourceHtml` above the `el.innerHTML =` assignment, then interpolate it):

```ts
  const sourceHtml = response.source === 'model'
    ? `<span class="sc-plan-chip model">${escapeHtml(response.model || response.provider)} · ${escapeHtml(plan.confidence)}</span>`
    : `<span class="sc-plan-chip heuristic" title="${escapeHtml(response.fallbackReason || 'model unavailable')}">heuristic · model unavailable</span>`;
```

and in the template:

```ts
      <div class="sc-plan-source">${sourceHtml}</div>
```

In `extension/styles/tcg.css`, append (match the existing `.sc-plan-chip` block's font sizing):

```css
.sc-plan-chip.model { background: #0c2b1c; color: #7ae8b0; border: 1px solid #16543a; }
.sc-plan-chip.heuristic { background: #4a3000; color: #ffd97a; border: 1px solid #8a5c00; }
```

- [ ] **Step 4: Run tests**

Run: `cd extension && npx vitest run test/panels/matchup-plan-card.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add extension/panels/matchup-plan-card.ts extension/styles/tcg.css extension/test/panels/matchup-plan-card.test.ts
git commit -m "feat(extension): visible model vs heuristic source chips on matchup plan card"
```

**STAGE 2 GATE — user check:** kill proxy at preview → "retrying" text; restart → model plan next decision. Start proxy without `ANTHROPIC_API_KEY` → amber chip, exactly one request in console.

---

### Task 5: `previewPlanRunMode` localStorage override

**Files:**
- Modify: `extension/entrypoints/content.ts` (next to `configuredPreviewPlanPresetId`, ~line 66-76, and the `runMode: 'auto'` line inside `requestMatchupPlan`)

**Interfaces:**
- Produces: `configuredPreviewPlanRunMode(): 'auto' | 'fake' | 'real'` inside `main()`.

- [ ] **Step 1: Implement**

Below `configuredPreviewPlanPresetId()` add:

```ts
const PREVIEW_PLAN_RUNMODE_STORAGE_KEY = 'showdownCopilot.previewPlanRunMode';

function configuredPreviewPlanRunMode(): 'auto' | 'fake' | 'real' {
  try {
    const stored = (localStorage.getItem(PREVIEW_PLAN_RUNMODE_STORAGE_KEY) || '').trim();
    if (stored === 'fake' || stored === 'real' || stored === 'auto') return stored;
  } catch { /* localStorage unavailable */ }
  return 'auto';
}
```

In `requestMatchupPlan`, change `runMode: 'auto',` to `runMode: configuredPreviewPlanRunMode(),`.

- [ ] **Step 2: Verify**

Run: `cd extension && npx vitest run`
Expected: all pass except the known flavor-strip failure. (No new unit test: this is a two-branch localStorage read exercised by the Stage 3 manual gate; the fake path itself is covered by pytest fallback tests.)

- [ ] **Step 3: Commit**

```bash
git add extension/entrypoints/content.ts
git commit -m "feat(extension): localStorage runMode override for preview plans (demo/fake mode)"
```

---

### Task 6: De-personalize `_fallback_plan`

**Files:**
- Modify: `src/showdown_copilot/preview_plan.py` (`_fallback_plan`, ~lines 159-336; `preview_plan_quality_checks` rain_preserve line, ~670)
- Modify: `tests/test_preview_plan.py` (rewrite the two fake-mode tests; add species-containment test)

**Interfaces:**
- Consumes: `req.grounding` does NOT exist yet (added in Task 11) — this task's lead/preserve selection reads only `req.myTeam` order. Task 11 upgrades selection to use `grounding.monSummaries` when present; write the helper now with an optional `mon_summaries` parameter defaulting to `None`.
- Produces: `_fallback_plan(req, reason)` same signature; output references only species present in the request.

- [ ] **Step 1: Rewrite the failing tests first**

In `tests/test_preview_plan.py`:

Replace `test_fake_preview_plan_identifies_rain_and_preserve_target` with:

```python
@pytest.mark.asyncio
async def test_fake_preview_plan_identifies_rain(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-rain",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Pelipper", "Basculegion", "Kingdra", "Ferrothorn", "Zapdos", "Barraskewda"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    assert result.source == "fallback"
    assert result.plan.archetype == "rain offense"
    assert result.plan.recommendedLead.pokemon == "Garchomp"  # first team slot, not hardcoded
```

Replace `test_fake_preview_plan_adds_gliscor_lead_rule` with:

```python
@pytest.mark.asyncio
async def test_fake_preview_plan_detects_stall_without_team_specific_rules(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-stall",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Gliscor", "Alomomola", "Claydol", "Heatran", "Garganacl", "Toxapex"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    assert result.plan.archetype == "bulky stall/control"
    # No fabricated per-species rules: everything mentioned must exist in the request.
    plan_text = json.dumps(result.plan.model_dump()).lower()
    for absent in ("ogerpon", "volcarona", "gholdengo"):
        assert absent not in plan_text
```

Add:

```python
@pytest.mark.asyncio
async def test_fallback_plan_only_references_request_species(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = PreviewPlanRequest(
        battleId="battle-test-generic",
        format="gen9nationaldex",
        myTeam=[
            PreviewPokemon(species="Skarmory", moves=["Spikes", "Roost"]),
            PreviewPokemon(species="Blissey", moves=["Seismic Toss", "Soft-Boiled"]),
        ],
        opponentTeam=["Pelipper", "Kingdra", "Ferrothorn"],
        runMode="fake",
    )

    result = await build_preview_plan(req)

    plan_text = json.dumps(result.plan.model_dump()).lower()
    for absent in ("garchomp", "ogerpon", "volcarona", "gholdengo", "iron valiant", "terapagos"):
        assert absent not in plan_text
    assert result.plan.recommendedLead.pokemon == "Skarmory"
```

Run: `uv run pytest tests/test_preview_plan.py -q` → the two rewritten tests FAIL against the current hardcoded fallback (e.g. `recommendedLead.pokemon == "Garchomp"` passes only by accident of the hardcode; the containment assertions fail).

- [ ] **Step 2: Rewrite `_fallback_plan`**

In `src/showdown_copilot/preview_plan.py`, keep the archetype-detection block (rain/sun/sand/stall inference from `opp_norms`, lines ~165-197) unchanged, then replace everything from the `garchomp = my_norms.get("garchomp")` line through the `danger_rules` block with:

```python
    def _pick_lead(mon_summaries: list[dict[str, Any]] | None = None) -> PreviewPokemon:
        if mon_summaries:
            ranked = sorted(
                mon_summaries,
                key=lambda row: int(row.get("survives") or 0) + int(row.get("threatens") or 0),
                reverse=True,
            )
            best = _first_my_species(req, str(ranked[0].get("species") or ""))
            if best:
                return best
        return req.myTeam[0] if req.myTeam else PreviewPokemon(species="Unknown")

    mon_summaries = None  # Task 11 threads req.grounding.monSummaries through here.
    recommended = _pick_lead(mon_summaries)
    recommended_lead = LeadOption(
        pokemon=recommended.species,
        rating="situational",
        reason="Heuristic pick: no model plan available; chosen from preview matchup counts."
        if mon_summaries
        else "Heuristic pick: no model plan available; first team slot by default.",
    )

    backup_leads: list[LeadOption] = []
    avoid_leads: list[LeadOption] = []
    lead_rules: list[LeadRule] = []

    preserve_targets: list[PreserveTarget] = []
    for row in (mon_summaries or [])[:6]:
        if int(row.get("threatens") or 0) >= 2 and len(preserve_targets) < 2:
            mon = _first_my_species(req, str(row.get("species") or ""))
            if mon and _norm(mon.species) != _norm(recommended.species):
                preserve_targets.append(PreserveTarget(
                    pokemon=mon.species,
                    priority="medium",
                    reason="Threatens multiple preview opponents; avoid trading it away early.",
                ))

    main_threats: list[ThreatItem] = []
    for norm_name in sorted(opp_norms & (RAIN_ABUSERS | SUN_ABUSERS | SETUP_THREATS)):
        main_threats.append(ThreatItem(
            pokemon=opp_names.get(norm_name, norm_name),
            priority="high" if rain and norm_name in RAIN_ABUSERS else "medium",
            reason="Preview suggests this Pokemon can become a major tempo or cleanup threat.",
        ))
    for norm_name in sorted(opp_norms & STATUS_SUPPORT):
        main_threats.append(ThreatItem(
            pokemon=opp_names.get(norm_name, norm_name),
            priority="medium",
            reason="Can create status, recovery, or disruption loops that change the value of setup turns.",
        ))

    danger_rules: list[DangerRule] = []
    if rain:
        danger_rules.append(DangerRule(
            id="rain_preserve_water_answer",
            severity="high",
            rule="Do not trade away your best answer to the rain attackers for early chip damage.",
            trigger={"oppArchetype": "rain"},
        ))
    if stall_count >= 3:
        danger_rules.append(DangerRule(
            id="stall_no_passive_turns",
            severity="medium",
            rule="Avoid giving free turns to recovery/status loops; make progress before they stabilize.",
            trigger={"oppArchetype": "stall"},
        ))
```

Keep the `early_priorities` / `uncertainties` / `plan = MatchupPlan(...)` / return blocks as they are (they reference only the variables defined above). Delete the now-unused hardcoded blocks (Garchomp/Gliscor lead rule, Ogerpon/Gholdengo preserve logic, Ogerpon-Alomomola and Volcarona danger rules, and the `("Iron Valiant", "Ogerpon-Wellspring", "Gholdengo", "Terapagos")` backup-lead loop).

- [ ] **Step 3: Fix the offline rubric**

In `preview_plan_quality_checks`, the rain check currently asserts Ogerpon is discussed regardless of the player's team. Replace:

```python
    if "pelipper" in opp_norms and opp_norms & RAIN_ABUSERS:
        add("rain_frame", "rain" in plan.archetype.lower() or "rain" in plan_text, "identify rain pressure")
        add("rain_preserve", "ogerpon" in plan_text, "preserve or discuss Ogerpon as rain answer")
```

with:

```python
    if "pelipper" in opp_norms and opp_norms & RAIN_ABUSERS:
        add("rain_frame", "rain" in plan.archetype.lower() or "rain" in plan_text, "identify rain pressure")
        add(
            "rain_preserve",
            "preserve" in plan_text or bool(plan.preserveTargets) or any(
                rule.id == "rain_preserve_water_answer" for rule in plan.dangerRules
            ),
            "flag preservation pressure against rain",
        )
```

Note `preview_plan_quality_checks` currently only receives `opponent_team`; the rewritten check uses only plan content, so the signature stays.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preview_plan.py -q`
Expected: PASS. Then `uv run pytest -q` — full suite green (400+ passed; if other suites assert old fallback specifics, update them the same way: no species outside the request).

- [ ] **Step 5: Commit**

```bash
git add src/showdown_copilot/preview_plan.py tests/test_preview_plan.py
git commit -m "refactor(preview): de-personalize fallback plan; derive lead/preserve from request"
```

**STAGE 3 GATE — user check:** in the battle console set `localStorage.setItem('showdownCopilot.previewPlanRunMode','fake')`, new battle → amber heuristic chip, plan references only actual team members; remove the key → model plans return.

---

### Task 7: `preview-grounding.ts` — compact damage pack builder

**Files:**
- Create: `extension/lib/preview-grounding.ts`
- Test: `extension/test/lib/preview-grounding.test.ts`

**Interfaces:**
- Consumes: `DamageMatrix`, `MatrixCell` from `extension/lib/damage-matrix.ts` (`cells: MatrixCell[]` with `attacker`, `defender`, `move`, `dmgPctMin`, `dmgPctMax`, `ohko`, `immune`).
- Produces (used by Tasks 8 and 11):
  - `interface GroundingCell { attacker: string; defender: string; move: string; pct: string; ohko: boolean; direction: 'mine' | 'opp' }`
  - `interface MonSummary { species: string; survives: number; threatens: number }`
  - `interface PreviewGrounding { damageCells: GroundingCell[]; monSummaries: MonSummary[]; source: string }`
  - `buildPreviewGrounding(myAtk: DamageMatrix | null, oppAtk: DamageMatrix | null): PreviewGrounding | null`
  - `const MAX_GROUNDING_CELLS = 24`

- [ ] **Step 1: Write the failing test**

Create `extension/test/lib/preview-grounding.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { buildPreviewGrounding, MAX_GROUNDING_CELLS } from '../../lib/preview-grounding';
import type { DamageMatrix, MatrixCell } from '../../lib/damage-matrix';

function cell(overrides: Partial<MatrixCell>): MatrixCell {
  return {
    attacker: 'A', defender: 'B', move: 'Move', moveSource: 'revealed',
    dmgPctMin: 40, dmgPctMax: 55, ohko: false, twoHko: true, immune: false,
    ...overrides,
  };
}

function matrix(cells: MatrixCell[], attackerSide: 'mine' | 'opp'): DamageMatrix {
  return { cells, attackerSide, computedAt: 0 };
}

describe('buildPreviewGrounding', () => {
  it('returns null with no matrices', () => {
    expect(buildPreviewGrounding(null, null)).toBeNull();
  });

  it('keeps only the best move per attacker/defender pair and formats pct', () => {
    const my = matrix([
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', move: 'Ivy Cudgel', dmgPctMin: 68, dmgPctMax: 81 }),
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', move: 'Horn Leech', dmgPctMin: 40, dmgPctMax: 48 }),
    ], 'mine');
    const g = buildPreviewGrounding(my, null)!;
    expect(g.damageCells).toHaveLength(1);
    expect(g.damageCells[0]).toMatchObject({ attacker: 'Ogerpon', move: 'Ivy Cudgel', pct: '68-81', direction: 'mine' });
  });

  it('prioritizes OHKO cells, then >=50% hits, and drops weak/immune cells', () => {
    const my = matrix([
      cell({ attacker: 'A1', defender: 'D1', ohko: true, dmgPctMin: 100, dmgPctMax: 120 }),
      cell({ attacker: 'A2', defender: 'D2', dmgPctMin: 51, dmgPctMax: 62 }),
      cell({ attacker: 'A3', defender: 'D3', dmgPctMin: 10, dmgPctMax: 20 }),   // below floor
      cell({ attacker: 'A4', defender: 'D4', immune: true }),                    // immune
    ], 'mine');
    const g = buildPreviewGrounding(my, null)!;
    expect(g.damageCells.map((c) => c.attacker)).toEqual(['A1', 'A2']);
    expect(g.damageCells[0].ohko).toBe(true);
  });

  it('caps at MAX_GROUNDING_CELLS across both directions', () => {
    const many = (side: 'mine' | 'opp') => matrix(
      Array.from({ length: 20 }, (_, i) =>
        cell({ attacker: `${side}-atk-${i}`, defender: `${side}-def-${i}`, ohko: true, dmgPctMin: 100, dmgPctMax: 110 })),
      side,
    );
    const g = buildPreviewGrounding(many('mine'), many('opp'))!;
    expect(g.damageCells.length).toBe(MAX_GROUNDING_CELLS);
  });

  it('summarizes survives/threatens per my mon', () => {
    const my = matrix([
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', ohko: true, dmgPctMin: 100, dmgPctMax: 110 }),
      cell({ attacker: 'Ogerpon', defender: 'Pelipper', ohko: true, dmgPctMin: 100, dmgPctMax: 105 }),
    ], 'mine');
    const opp = matrix([
      cell({ attacker: 'Kingdra', defender: 'Ogerpon', ohko: true, dmgPctMin: 100, dmgPctMax: 130 }),
      cell({ attacker: 'Pelipper', defender: 'Ogerpon', dmgPctMin: 30, dmgPctMax: 40 }),
    ], 'opp');
    const g = buildPreviewGrounding(my, opp)!;
    const row = g.monSummaries.find((m) => m.species === 'Ogerpon')!;
    expect(row.threatens).toBe(2);      // OHKOs Kingdra + Pelipper
    expect(row.survives).toBe(1);       // 2 opp attackers, 1 OHKOs it
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npx vitest run test/lib/preview-grounding.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `extension/lib/preview-grounding.ts`:

```ts
// Compacts the team-preview damage matrices into a prompt-sized grounding
// pack for the preview planner: notable cells (OHKOs + hits >= 50%) and
// per-mon survives/threatens counts. Spec §3 caps this at 24 cells so the
// pack stays ~small in the prompt.

import type { DamageMatrix, MatrixCell } from './damage-matrix';

export interface GroundingCell {
  attacker: string;
  defender: string;
  move: string;
  pct: string; // "68-81"
  ohko: boolean;
  direction: 'mine' | 'opp';
}

export interface MonSummary {
  species: string;
  survives: number;
  threatens: number;
}

export interface PreviewGrounding {
  damageCells: GroundingCell[];
  monSummaries: MonSummary[];
  source: string;
}

export const MAX_GROUNDING_CELLS = 24;
const MIN_NOTABLE_PCT = 50;

function bestCellPerPair(cells: MatrixCell[]): MatrixCell[] {
  const best = new Map<string, MatrixCell>();
  for (const cell of cells) {
    if (cell.immune) continue;
    const key = `${cell.attacker}|${cell.defender}`;
    const cur = best.get(key);
    if (!cur || cell.dmgPctMax > cur.dmgPctMax) best.set(key, cell);
  }
  return [...best.values()];
}

function toGroundingCell(cell: MatrixCell, direction: 'mine' | 'opp'): GroundingCell {
  return {
    attacker: cell.attacker,
    defender: cell.defender,
    move: cell.move,
    pct: `${Math.round(cell.dmgPctMin)}-${Math.round(cell.dmgPctMax)}`,
    ohko: cell.ohko,
    direction,
  };
}

function buildMonSummaries(myAtk: DamageMatrix | null, oppAtk: DamageMatrix | null): MonSummary[] {
  const myBest = bestCellPerPair(myAtk?.cells ?? []);
  const oppBest = bestCellPerPair(oppAtk?.cells ?? []);
  const mySpecies = [...new Set(myBest.map((c) => c.attacker))];
  const oppSpecies = new Set(oppBest.map((c) => c.attacker));
  if (!mySpecies.length && oppSpecies.size === 0) return [];
  return mySpecies.map((species) => {
    const threatens = new Set(myBest.filter((c) => c.attacker === species && c.ohko).map((c) => c.defender)).size;
    const koMe = new Set(oppBest.filter((c) => c.defender === species && c.ohko).map((c) => c.attacker)).size;
    return { species, threatens, survives: Math.max(0, oppSpecies.size - koMe) };
  });
}

export function buildPreviewGrounding(
  myAtk: DamageMatrix | null,
  oppAtk: DamageMatrix | null,
): PreviewGrounding | null {
  if (!myAtk && !oppAtk) return null;
  const notable = [
    ...bestCellPerPair(myAtk?.cells ?? []).map((c) => ({ cell: c, direction: 'mine' as const })),
    ...bestCellPerPair(oppAtk?.cells ?? []).map((c) => ({ cell: c, direction: 'opp' as const })),
  ];
  const ohkos = notable.filter((n) => n.cell.ohko);
  const strong = notable
    .filter((n) => !n.cell.ohko && n.cell.dmgPctMax >= MIN_NOTABLE_PCT)
    .sort((a, b) => b.cell.dmgPctMax - a.cell.dmgPctMax);
  const damageCells = [...ohkos, ...strong]
    .slice(0, MAX_GROUNDING_CELLS)
    .map((n) => toGroundingCell(n.cell, n.direction));
  return {
    damageCells,
    monSummaries: buildMonSummaries(myAtk, oppAtk),
    source: 'extension-damage-matrix',
  };
}
```

- [ ] **Step 4: Run tests**

Run: `cd extension && npx vitest run test/lib/preview-grounding.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add extension/lib/preview-grounding.ts extension/test/lib/preview-grounding.test.ts
git commit -m "feat(extension): compact damage-matrix grounding pack for preview plans"
```

---

### Task 8: Wire grounding into the request + `__scPreviewGrounding()`

**Files:**
- Modify: `extension/lib/matchup-plan.ts` (request type)
- Modify: `extension/entrypoints/content.ts` (team-preview branch ~1841-1908; `requestMatchupPlan`; debug helpers block)

**Interfaces:**
- Consumes (Task 7): `buildPreviewGrounding`, `PreviewGrounding`.
- Produces: `PreviewPlanRequest.grounding?: PreviewGrounding | null` (Task 11's Pydantic model mirrors this shape exactly: `damageCells[].{attacker,defender,move,pct,ohko,direction}`, `monSummaries[].{species,survives,threatens}`, `source`).

- [ ] **Step 1: Extend the request type**

In `extension/lib/matchup-plan.ts` add at the top:

```ts
import type { PreviewGrounding } from './preview-grounding';
```

and inside `PreviewPlanRequest`:

```ts
  grounding?: PreviewGrounding | null;
```

- [ ] **Step 2: Build grounding at team preview and thread it through**

In `content.ts`:

**(a)** Near the other per-battle maps (top of `main()` closure state), add:

```ts
const previewGroundingByBattle = new Map<string, PreviewGrounding>();
```

with import `import { buildPreviewGrounding, type PreviewGrounding } from '../lib/preview-grounding';`.

**(b)** In the team-preview branch, the belief `.then` already computes `myAtk` and `oppAtk` (~lines 1859-1868). Immediately after `const oppAtk = buildDamageMatrix({...});` add:

```ts
            const grounding = buildPreviewGrounding(myAtk, oppAtk);
            if (grounding) previewGroundingByBattle.set(String(br?.id ?? ''), grounding);
            requestMatchupPlan(b, br, mySnaps, oppSnaps);
```

and **remove** the existing `requestMatchupPlan(b, br, mySnaps, oppSnaps);` call at ~line 1847 (it fires before matrices exist). In the belief-fetch `.catch` branch (~1894), add `requestMatchupPlan(b, br, mySnaps, oppSnaps);` so a failed belief fetch still requests an ungrounded plan (spec: grounding degrades, never blocks).

**(c)** In `requestMatchupPlan`, add to the request object:

```ts
    grounding: previewGroundingByBattle.get(battleId) ?? null,
```

**(d)** In the debug-helpers section (near the existing `__scDebug` / `__scPostMortem*` assignments — search for `__scDebug`), add:

```ts
    (win as any).__scPreviewGrounding = () =>
      previewGroundingByBattle.get(String(win.app?.curRoom?.id ?? '')) ?? null;
```

- [ ] **Step 3: Verify**

Run: `cd extension && npx vitest run`
Expected: all pass except the known flavor-strip failure.

- [ ] **Step 4: Commit**

```bash
git add extension/lib/matchup-plan.ts extension/entrypoints/content.ts
git commit -m "feat(extension): send damage grounding with preview-plan requests + __scPreviewGrounding()"
```

**STAGE 4 GATE — user check:** at team preview run `__scPreviewGrounding()` in the console; spot-check 2-3 OHKO cells against the damage-matrix panel.

---

### Task 9: `PriorsSource.usage_summary()`

**Files:**
- Modify: `src/showdown_copilot/priors.py` (add one method to `PriorsSource`)
- Test: `tests/test_priors.py` (append)

**Interfaces:**
- Consumes: existing `PriorsSource._lookup_entry(species, fmt)`, module-level `_normalize_dist`, `_normalize`.
- Produces (used by Task 10): `usage_summary(species: str, format: str) -> dict | None` returning `{"topMoves": [{"name","pct"}...], "topItems": [...], "topAbilities": [...], "topTera": [...], "scarfPct": int}` with pct as ints (0-100), floors: 20% for lists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_priors.py`:

```python
def test_usage_summary_formats_and_filters(monkeypatch):
    from showdown_copilot.priors import PriorsSource

    entry = {
        "Moves": {"toxic": 780, "protect": 710, "earthquake": 400, "spikes": 90, "uturn": 20},
        "Items": {"toxicorb": 940, "choicescarf": 60},
        "Abilities": {"poisonheal": 970, "hypercutter": 30},
        "Tera Types": {"Water": 500, "Normal": 300, "Ghost": 100},
    }
    source = PriorsSource.__new__(PriorsSource)  # skip __init__ (no network/cache)
    monkeypatch.setattr(source, "_lookup_entry", lambda species, fmt, team_type=None: entry)

    summary = source.usage_summary("Gliscor", "gen9nationaldex")

    move_names = [row["name"] for row in summary["topMoves"]]
    assert move_names[:2] == ["toxic", "protect"]
    assert all(row["pct"] >= 20 for row in summary["topMoves"])
    assert "spikes" not in move_names  # below the 20% floor
    assert summary["topItems"][0]["name"] == "toxicorb"
    assert summary["scarfPct"] == 6
    assert summary["topAbilities"][0] == {"name": "poisonheal", "pct": 97}


def test_usage_summary_returns_none_for_unknown_species(monkeypatch):
    from showdown_copilot.priors import PriorsSource

    source = PriorsSource.__new__(PriorsSource)
    monkeypatch.setattr(source, "_lookup_entry", lambda species, fmt, team_type=None: None)
    assert source.usage_summary("Missingno", "gen9nationaldex") is None
```

Run: `uv run pytest tests/test_priors.py -q` → the two new tests FAIL (`usage_summary` missing).

- [ ] **Step 2: Implement**

Add to `PriorsSource` in `src/showdown_copilot/priors.py`:

```python
    def usage_summary(self, species: str, format: str) -> dict[str, Any] | None:
        """Display-ready usage stats for the preview planner's grounding pack.

        Returns None when the species has no chaos entry (or loading fails);
        the caller omits that species rather than blocking plan generation.
        """
        try:
            entry = self._lookup_entry(species, format)
        except Exception:
            return None
        if entry is None:
            return None

        def top(dist: dict[str, float], n: int, floor_pct: int) -> list[dict[str, Any]]:
            normalized = _normalize_dist(dict(dist or {}))
            rows = sorted(normalized.items(), key=lambda kv: -kv[1])[:n]
            return [
                {"name": name, "pct": round(weight * 100)}
                for name, weight in rows
                if round(weight * 100) >= floor_pct
            ]

        items = _normalize_dist(dict(entry.get("Items", {}) or {}))
        scarf_pct = round(sum(
            weight for name, weight in items.items() if _normalize(name) == "choicescarf"
        ) * 100)
        return {
            "topMoves": top(entry.get("Moves", {}), 4, 20),
            "topItems": top(entry.get("Items", {}), 2, 20),
            "topAbilities": top(entry.get("Abilities", {}), 2, 20),
            "topTera": top(entry.get("Tera Types", {}), 2, 20),
            "scarfPct": scarf_pct,
        }
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_priors.py -q`
Expected: PASS (all existing + 2 new).

- [ ] **Step 4: Commit**

```bash
git add src/showdown_copilot/priors.py tests/test_priors.py
git commit -m "feat(priors): usage_summary() for preview-plan grounding"
```

---

### Task 10: `preview_grounding.py` — proxy-side enrichment

**Files:**
- Create: `src/showdown_copilot/preview_grounding.py`
- Test: `tests/test_preview_grounding.py`

**Interfaces:**
- Consumes: `PriorsSource.usage_summary` (Task 9), `mechanics_facts.get_pokemon_facts` (returns `{"found": bool, "name": str, "baseStats": {"spe": int, ...}}`).
- Produces (used by Task 11):
  - `build_opponent_likely_sets(opponent_team: list[str], fmt: str) -> list[dict]` — entries `{"species", "basis": "usage-statistics", "topMoves", "topItems", "topAbilities", "topTera", "scarfPct"}`; empty list when priors unavailable.
  - `build_speed_context(my_species: list[str], opponent_species: list[str], likely_sets: list[dict] | None = None) -> dict` — `{"baseSpeedOrder": [{"species","side","baseSpeed"}...desc], "scarfPlausible": [...] }` (`scarfPlausible` only when non-empty); `{}` when nothing resolves.

- [ ] **Step 1: Write the failing test**

Create `tests/test_preview_grounding.py`:

```python
import showdown_copilot.preview_grounding as pg
from showdown_copilot.preview_grounding import build_opponent_likely_sets, build_speed_context


def test_likely_sets_skips_species_without_data(monkeypatch):
    class FakePriors:
        def usage_summary(self, species, fmt):
            if species == "Gliscor":
                return {"topMoves": [{"name": "toxic", "pct": 78}], "topItems": [],
                        "topAbilities": [{"name": "poisonheal", "pct": 97}], "topTera": [], "scarfPct": 0}
            return None

    monkeypatch.setattr(pg, "_priors_source", lambda: FakePriors())
    rows = build_opponent_likely_sets(["Gliscor", "Missingno"], "gen9nationaldex")
    assert len(rows) == 1
    assert rows[0]["species"] == "Gliscor"
    assert rows[0]["basis"] == "usage-statistics"


def test_likely_sets_empty_when_priors_unavailable(monkeypatch):
    monkeypatch.setattr(pg, "_priors_source", lambda: None)
    assert build_opponent_likely_sets(["Gliscor"], "gen9nationaldex") == []


def test_speed_context_orders_and_flags_scarf():
    ctx = build_speed_context(
        ["Garchomp"],                      # base spe 102
        ["Kingdra"],                       # base spe 85
        likely_sets=[{"species": "Kingdra", "scarfPct": 30}],
    )
    order = [(row["species"], row["side"]) for row in ctx["baseSpeedOrder"]]
    assert order == [("Garchomp", "mine"), ("Kingdra", "opp")]
    assert ctx["scarfPlausible"] == ["Kingdra"]


def test_speed_context_empty_for_unknown_species():
    assert build_speed_context(["Missingno"], ["Fakemon"]) == {}
```

Run: `uv run pytest tests/test_preview_grounding.py -q` → FAIL (module missing).

- [ ] **Step 2: Implement**

Create `src/showdown_copilot/preview_grounding.py`:

```python
"""Proxy-side grounding enrichment for live preview plans.

Assembles the facts the planner prompt cites: opponent likely sets from
Smogon usage priors and a base-speed ordering. Every function degrades to
an empty result instead of raising — grounding must never block a plan.
"""
from __future__ import annotations

import logging
from typing import Any

from .mechanics_facts import get_pokemon_facts
from .priors import PriorsSource

logger = logging.getLogger(__name__)

SCARF_PLAUSIBLE_MIN_PCT = 15

_priors: PriorsSource | None = None
_priors_failed = False


def _priors_source() -> PriorsSource | None:
    global _priors, _priors_failed
    if _priors is None and not _priors_failed:
        try:
            _priors = PriorsSource()
        except Exception:  # noqa: BLE001 - cache dir/env issues must not break planning
            logger.warning("preview grounding: priors source unavailable", exc_info=True)
            _priors_failed = True
    return _priors


def build_opponent_likely_sets(opponent_team: list[str], fmt: str) -> list[dict[str, Any]]:
    source = _priors_source()
    if source is None:
        return []
    rows: list[dict[str, Any]] = []
    for species in opponent_team:
        if not species:
            continue
        try:
            summary = source.usage_summary(species, fmt)
        except Exception:  # noqa: BLE001 - a fetch/parse failure skips the species only
            logger.warning("preview grounding: usage_summary failed for %s", species, exc_info=True)
            continue
        if not summary:
            continue
        rows.append({"species": species, "basis": "usage-statistics", **summary})
    return rows


def build_speed_context(
    my_species: list[str],
    opponent_species: list[str],
    likely_sets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for side, names in (("mine", my_species), ("opp", opponent_species)):
        for name in names:
            facts = get_pokemon_facts(name)
            if not facts.get("found"):
                continue
            base_speed = int((facts.get("baseStats") or {}).get("spe") or 0)
            rows.append({"species": str(facts.get("name") or name), "side": side, "baseSpeed": base_speed})
    if not rows:
        return {}
    rows.sort(key=lambda row: -row["baseSpeed"])
    context: dict[str, Any] = {"baseSpeedOrder": rows}
    scarf = [
        str(item.get("species"))
        for item in (likely_sets or [])
        if int(item.get("scarfPct") or 0) >= SCARF_PLAUSIBLE_MIN_PCT
    ]
    if scarf:
        context["scarfPlausible"] = scarf
    return context
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_preview_grounding.py -q`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add src/showdown_copilot/preview_grounding.py tests/test_preview_grounding.py
git commit -m "feat(preview): proxy-side grounding (usage priors + speed context)"
```

---

### Task 11: Grounded prompt in `preview_plan.py` + prompt logging

**Files:**
- Modify: `src/showdown_copilot/preview_plan.py`
- Test: `tests/test_preview_plan.py` (append)

**Interfaces:**
- Consumes: Task 10 functions; extension request field `grounding` (Task 8 shape).
- Produces:
  - Pydantic: `GroundingCell`, `MonSummary`, `PreviewGrounding`; `PreviewPlanRequest.grounding: PreviewGrounding | None = None`.
  - `_preview_user_prompt(req)` payload gains `damageSummary`, `opponentLikelySets`, `speedContext` keys (each omitted when empty / when `SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1` for the proxy-side two).
  - `_fallback_plan` lead/preserve now reads `req.grounding.monSummaries` (fills the Task 6 `mon_summaries` hook).
  - `SHOWDOWN_PREVIEW_LOG_PROMPT=1` logs the assembled user prompt.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preview_plan.py`:

```python
def _grounded_request(**overrides):
    from showdown_copilot.preview_plan import GroundingCell, MonSummary, PreviewGrounding

    base = dict(
        battleId="battle-grounded",
        format="gen9nationaldex",
        myTeam=default_team(),
        opponentTeam=["Pelipper", "Kingdra"],
        runMode="fake",
        grounding=PreviewGrounding(
            damageCells=[GroundingCell(
                attacker="Kingdra", defender="Ogerpon-Wellspring", move="Draco Meteor",
                pct="24-29", ohko=False, direction="opp",
            )],
            monSummaries=[
                MonSummary(species="Ogerpon-Wellspring", survives=2, threatens=2),
                MonSummary(species="Garchomp", survives=1, threatens=0),
            ],
        ),
    )
    base.update(overrides)
    return PreviewPlanRequest(**base)


def test_prompt_includes_grounding_sections(monkeypatch):
    monkeypatch.setattr(
        "showdown_copilot.preview_plan.build_opponent_likely_sets",
        lambda team, fmt: [{"species": "Kingdra", "basis": "usage-statistics", "scarfPct": 30,
                            "topMoves": [], "topItems": [], "topAbilities": [], "topTera": []}],
    )
    monkeypatch.setattr(
        "showdown_copilot.preview_plan.build_speed_context",
        lambda mine, opp, likely_sets=None: {"baseSpeedOrder": [], "scarfPlausible": ["Kingdra"]},
    )
    prompt = _preview_user_prompt(_grounded_request())
    payload = json.loads(prompt)
    assert payload["damageSummary"]["damageCells"][0]["pct"] == "24-29"
    assert payload["opponentLikelySets"][0]["species"] == "Kingdra"
    assert payload["speedContext"]["scarfPlausible"] == ["Kingdra"]


def test_prompt_omits_grounding_when_absent_or_disabled(monkeypatch):
    monkeypatch.setenv("SHOWDOWN_PREVIEW_DISABLE_GROUNDING", "1")
    req = PreviewPlanRequest(
        battleId="b", format="gen9nationaldex", myTeam=default_team(),
        opponentTeam=["Pelipper"], runMode="fake",
    )
    payload = json.loads(_preview_user_prompt(req))
    assert "damageSummary" not in payload
    assert "opponentLikelySets" not in payload
    assert "speedContext" not in payload
    monkeypatch.delenv("SHOWDOWN_PREVIEW_DISABLE_GROUNDING")


@pytest.mark.asyncio
async def test_fallback_lead_uses_grounding_summaries(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await build_preview_plan(_grounded_request())
    # Ogerpon-Wellspring has the best survives+threatens, so it beats slot order.
    assert result.plan.recommendedLead.pokemon == "Ogerpon-Wellspring"
```

Run: `uv run pytest tests/test_preview_plan.py -q` → new tests FAIL (models/keys missing).

- [ ] **Step 2: Implement in `preview_plan.py`**

**(a)** Add imports and logger near the top:

```python
import logging
from .preview_grounding import build_opponent_likely_sets, build_speed_context

logger = logging.getLogger(__name__)
```

**(b)** Add models above `PreviewPlanRequest` (shape mirrors Task 8's TS interfaces exactly):

```python
class GroundingCell(BaseModel):
    attacker: str
    defender: str
    move: str
    pct: str
    ohko: bool = False
    direction: Literal["mine", "opp"] = "mine"


class MonSummary(BaseModel):
    species: str
    survives: int = 0
    threatens: int = 0


class PreviewGrounding(BaseModel):
    damageCells: list[GroundingCell] = Field(default_factory=list)
    monSummaries: list[MonSummary] = Field(default_factory=list)
    source: str = "extension-damage-matrix"
```

and to `PreviewPlanRequest`:

```python
    grounding: PreviewGrounding | None = None
```

**(c)** In `_preview_user_prompt`, after the existing `payload = {...}` dict literal, insert before `return`:

```python
    if req.grounding and (req.grounding.damageCells or req.grounding.monSummaries):
        payload["damageSummary"] = req.grounding.model_dump()
    if os.environ.get("SHOWDOWN_PREVIEW_DISABLE_GROUNDING") != "1":
        likely_sets = build_opponent_likely_sets(req.opponentTeam, req.format)
        if likely_sets:
            payload["opponentLikelySets"] = likely_sets
        speed_context = build_speed_context(
            [mon.species for mon in req.myTeam], req.opponentTeam, likely_sets or None,
        )
        if speed_context:
            payload["speedContext"] = speed_context
```

**(d)** Append to `PREVIEW_SYSTEM_PROMPT` (inside the existing triple-quoted string, after the "Mechanics discipline" block):

```
Grounding discipline:
- damageSummary cells, opponentLikelySets percentages, and speedContext are supplied evidence. Cite these numbers; never invent numbers of your own.
- If a claim needs a number that is not supplied, phrase it qualitatively instead.
- opponentLikelySets are usage statistics for likely sets, not revealed information — attribute them as likelihoods ("usually", "78% of sets"), never as facts about this opponent.
```

**(e)** In `build_preview_plan`, compute the prompt once and log it when asked. Before the provider dispatch (`if provider == "openai":`) add:

```python
    user_prompt = _preview_user_prompt(req)
    if os.environ.get("SHOWDOWN_PREVIEW_LOG_PROMPT") == "1":
        logger.info("preview-plan prompt for %s:\n%s", req.battleId, user_prompt)
```

Change `_openai_preview_plan(req, preset)` / `_anthropic_preview_plan(req, preset)` signatures to `(req, preset, user_prompt: str)` and replace their internal `_preview_user_prompt(req)` calls with `user_prompt`; update the two call sites.

**(f)** In `_fallback_plan`, replace the Task 6 placeholder line `mon_summaries = None  # Task 11 ...` with:

```python
    mon_summaries = (
        [row.model_dump() for row in req.grounding.monSummaries]
        if req.grounding and req.grounding.monSummaries
        else None
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_preview_plan.py tests/test_preview_grounding.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/showdown_copilot/preview_plan.py tests/test_preview_plan.py
git commit -m "feat(preview): grounded planner prompt (damage cells, likely sets, speed) + prompt logging"
```

---

### Task 12: Evaluator flags `--battle` and `--grounding`

**Files:**
- Modify: `scripts/evaluate-preview-plans.py`

**Interfaces:**
- Consumes: existing `_load_recent_postmortems`, `build_preview_plan`; env `SHOWDOWN_PREVIEW_DISABLE_GROUNDING` (Task 11); `response.sanitizedClaims` exists only after Task 14 — print it defensively via `getattr`.

- [ ] **Step 1: Implement**

In `scripts/evaluate-preview-plans.py`:

**(a)** Add `import os` to the imports.

**(b)** Add arguments after the existing ones in `main()`:

```python
    parser.add_argument("--battle", default=None,
                        help="Path to a single postmortem JSON to replay (overrides --limit scanning)")
    parser.add_argument("--grounding", choices=["on", "off"], default="on",
                        help="off sets SHOWDOWN_PREVIEW_DISABLE_GROUNDING=1 for this run")
```

**(c)** After `args = parser.parse_args()`:

```python
    if args.grounding == "off":
        os.environ["SHOWDOWN_PREVIEW_DISABLE_GROUNDING"] = "1"
    else:
        os.environ.pop("SHOWDOWN_PREVIEW_DISABLE_GROUNDING", None)

    if args.battle:
        data = json.loads(Path(args.battle).read_text(encoding="utf-8"))
        postmortems = [data]
    else:
        postmortems = _load_recent_postmortems(Path(args.postmortem_dir), args.limit, args.min_schema)
```

(replacing the existing `postmortems = _load_recent_postmortems(...)` line).

**(d)** In the per-battle print block, after the `print("Win path:", ...)` line add:

```python
        sanitized = list(getattr(response, "sanitizedClaims", None) or [])
        if sanitized:
            print(f"Sanitized claims ({len(sanitized)}):")
            for message in sanitized:
                print(f"  - {message}")
```

- [ ] **Step 2: Verify offline (fake mode, no API cost)**

Run: `uv run python scripts/evaluate-preview-plans.py --run-mode fake --limit 2 --grounding off`
Expected: two battle blocks print with `Source: fallback`; no crash.
Run: `uv run python scripts/evaluate-preview-plans.py --run-mode fake --limit 1 --grounding on`
Expected: same shape (grounding only affects prompts on model runs, but the flag must not error).

- [ ] **Step 3: Commit**

```bash
git add scripts/evaluate-preview-plans.py
git commit -m "feat(scripts): evaluator --battle single-replay and --grounding on/off"
```

**STAGE 5 GATE — user check:** with `SHOWDOWN_PREVIEW_LOG_PROMPT=1` and a real preset, replay one saved battle `--grounding on` vs `--grounding off`; read both plans; the grounded one cites supplied numbers.

---

### Task 13: `sanitize_preview_plan` in the verifier

**Files:**
- Modify: `src/showdown_copilot/preview_verifier.py`
- Test: `tests/test_preview_sanitize.py` (create)

**Interfaces:**
- Consumes: `PreviewPlanIssue` (existing; `path` looks like `plan.dangerRules[2].rule`).
- Produces (used by Task 14): `sanitize_preview_plan(plan: Any, issues: list[PreviewPlanIssue]) -> tuple[dict, list[str], list[PreviewPlanIssue]]` — returns (plan dict with flagged list items removed + aggregated uncertainty appended, removed-claim messages, remaining core issues). Returns a **dict** (not `MatchupPlan`) to avoid a circular import; `preview_plan.py` re-validates.

- [ ] **Step 1: Write the failing test**

Create `tests/test_preview_sanitize.py`:

```python
from showdown_copilot.preview_verifier import PreviewPlanIssue, sanitize_preview_plan


def make_plan() -> dict:
    return {
        "archetype": "rain offense",
        "confidence": "medium",
        "summary": "Rain team.",
        "winPath": "Preserve the Water answer.",
        "recommendedLead": {"pokemon": "Skarmory", "rating": "safe", "reason": "Info lead."},
        "backupLeads": [],
        "avoidLeads": [],
        "leadRules": [],
        "preserveTargets": [],
        "mainThreats": [
            {"pokemon": "Kingdra", "reason": "Bad claim about immunity.", "priority": "high"},
            {"pokemon": "Pelipper", "reason": "Fine claim.", "priority": "medium"},
        ],
        "dangerRules": [
            {"id": "a", "rule": "Bad rule.", "trigger": {}, "severity": "high"},
            {"id": "b", "rule": "Good rule.", "trigger": {}, "severity": "medium"},
        ],
        "earlyPriorities": [],
        "uncertainties": ["Sets unknown."],
    }


def issue(path: str, reason: str = "wrong") -> PreviewPlanIssue:
    return PreviewPlanIssue(
        id="type_relation_mismatch", path=path, severity="high",
        badClaim="x", reason=reason, repairInstruction="fix",
    )


def test_drops_flagged_list_items_and_appends_uncertainty():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.mainThreats[0].reason"), issue("plan.dangerRules[0].rule")],
    )
    assert [t["pokemon"] for t in plan["mainThreats"]] == ["Pelipper"]
    assert [r["id"] for r in plan["dangerRules"]] == ["b"]
    assert len(removed) == 2
    assert core == []
    assert plan["uncertainties"][-1] == "2 generated claim(s) removed by the mechanics checker."


def test_core_field_issues_pass_through():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.winPath"), issue("plan.mainThreats[1].reason")],
    )
    assert len(core) == 1 and core[0].path == "plan.winPath"
    assert [t["pokemon"] for t in plan["mainThreats"]] == ["Kingdra"]
    assert plan["winPath"] == "Preserve the Water answer."  # untouched; caller repairs


def test_duplicate_indexes_removed_once():
    plan, removed, core = sanitize_preview_plan(
        make_plan(),
        [issue("plan.dangerRules[0].rule"), issue("plan.dangerRules[0].id")],
    )
    assert [r["id"] for r in plan["dangerRules"]] == ["b"]
    assert plan["uncertainties"][-1].startswith("2 generated claim(s)")
```

Run: `uv run pytest tests/test_preview_sanitize.py -q` → FAIL (`sanitize_preview_plan` missing).

- [ ] **Step 2: Implement**

In `src/showdown_copilot/preview_verifier.py` add (after `verify_preview_plan`):

```python
_SANITIZE_LIST_FIELDS = {
    "dangerRules", "mainThreats", "preserveTargets", "leadRules",
    "backupLeads", "avoidLeads", "earlyPriorities", "uncertainties",
}
_PATH_ITEM_RE = re.compile(r"^plan\.(?P<field>[A-Za-z]+)\[(?P<index>\d+)\]")


def sanitize_preview_plan(
    plan: Any,
    issues: list[PreviewPlanIssue],
) -> tuple[dict[str, Any], list[str], list[PreviewPlanIssue]]:
    """Drop flagged list items instead of rejecting the whole plan.

    Issues whose path points into a list field are resolved by removing that
    item; everything else (summary, winPath, recommendedLead, archetype) is
    returned as a core issue for the caller's single repair pass. Returns a
    plain dict so preview_plan.py can re-validate without a circular import.
    """
    data = dict(_plan_to_dict(plan))  # shallow copy; list fields replaced below
    removals: dict[str, set[int]] = {}
    removed_messages: list[str] = []
    core_issues: list[PreviewPlanIssue] = []

    for issue in issues:
        match = _PATH_ITEM_RE.match(issue.path or "")
        field = match.group("field") if match else None
        if match and field in _SANITIZE_LIST_FIELDS:
            removals.setdefault(field, set()).add(int(match.group("index")))
            removed_messages.append(issue.reason)
        else:
            core_issues.append(issue)

    for field, indexes in removals.items():
        items = list(data.get(field) or [])
        data[field] = [item for position, item in enumerate(items) if position not in indexes]

    if removed_messages:
        data["uncertainties"] = [
            *(data.get("uncertainties") or []),
            f"{len(removed_messages)} generated claim(s) removed by the mechanics checker.",
        ]
    return data, removed_messages, core_issues
```

Semantics: every flagged claim's `reason` is preserved in `removed_messages` (so two issues on the same item yield two messages), while the item itself is removed once via the `set` of indexes.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_preview_sanitize.py tests/test_legality.py -q` (legality shares the module's imports)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/showdown_copilot/preview_verifier.py tests/test_preview_sanitize.py
git commit -m "feat(preview): sanitize_preview_plan drops flagged claims instead of rejecting plans"
```

---

### Task 14: Sanitize-first flow in `build_preview_plan` + `sanitizedClaims` + token clamp

**Files:**
- Modify: `src/showdown_copilot/preview_plan.py`
- Test: `tests/test_preview_plan.py` (append + update any repair-flow tests)

**Interfaces:**
- Consumes: `sanitize_preview_plan` (Task 13), existing `repair_preview_plan_json`.
- Produces: `PreviewPlanResponse.sanitizedClaims: list[str] = []`; behavior deltas (update any existing tests that encode the old flow):
  1. List-item issues never trigger a repair call; the plan ships with items dropped and `sanitizedClaims` populated.
  2. Core issues get exactly one repair call when `SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS` (default now `1`) is > 0.
  3. Fallback happens only when core issues survive the repair (`"core mechanics validation failed"`) or generation/repair itself throws.
  4. Model calls clamp `max_tokens` to `min(preset, 2500)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preview_plan.py`:

```python
def _plan_with_bad_threat() -> MatchupPlan:
    return MatchupPlan(
        archetype="rain offense", confidence="medium",
        summary="Rain team.", winPath="Preserve the Water answer.",
        recommendedLead=LeadOption(pokemon="Skarmory", rating="safe", reason="Info lead."),
        backupLeads=[], avoidLeads=[], leadRules=[], preserveTargets=[],
        mainThreats=[
            # Ferrothorn is Grass/Steel: claiming it "resists Fire" is false (4x weak).
            preview_plan_module.ThreatItem(pokemon="Ferrothorn", reason="Ferrothorn resists Fire moves.", priority="high"),
        ],
        dangerRules=[], earlyPriorities=[], uncertainties=[],
    )


@pytest.mark.asyncio
async def test_list_item_issue_sanitizes_without_repair_or_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def fake_model(req, preset, user_prompt):
        return _plan_with_bad_threat(), {"inputTokens": 1}, "raw"

    repair_calls = []

    async def fake_repair(**kwargs):
        repair_calls.append(kwargs)
        raise AssertionError("repair must not be called for list-item issues")

    monkeypatch.setattr(preview_plan_module, "_openai_preview_plan", fake_model)
    monkeypatch.setattr(preview_plan_module, "repair_preview_plan_json", fake_repair)

    req = PreviewPlanRequest(
        battleId="b-sanitize", format="gen9nationaldex", myTeam=default_team(),
        opponentTeam=["Ferrothorn", "Pelipper"], presetId="openai-gpt-54-mini-balanced",
        runMode="real",
    )
    result = await build_preview_plan(req)

    assert result.source == "model"
    assert result.plan.mainThreats == []
    assert result.sanitizedClaims and "Ferrothorn" in result.sanitizedClaims[0]
    assert repair_calls == []


@pytest.mark.asyncio
async def test_core_issue_gets_one_repair_then_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS", "1")

    bad_core = _plan_with_bad_threat().model_copy(update={
        "mainThreats": [],
        "winPath": "Win because Ferrothorn resists Fire moves.",
    })

    async def fake_model(req, preset, user_prompt):
        return bad_core, {"inputTokens": 1}, "raw"

    repair_calls = []

    async def fake_repair(**kwargs):
        repair_calls.append(kwargs)
        return bad_core.model_dump(), {"inputTokens": 1}, "raw-repair"  # still bad

    monkeypatch.setattr(preview_plan_module, "_openai_preview_plan", fake_model)
    monkeypatch.setattr(preview_plan_module, "repair_preview_plan_json", fake_repair)

    req = PreviewPlanRequest(
        battleId="b-core", format="gen9nationaldex", myTeam=default_team(),
        opponentTeam=["Ferrothorn", "Pelipper"], presetId="openai-gpt-54-mini-balanced",
        runMode="real",
    )
    result = await build_preview_plan(req)

    assert len(repair_calls) == 1
    assert result.source == "fallback"
    assert "core mechanics validation failed" in (result.fallbackReason or "")
```

Run: `uv run pytest tests/test_preview_plan.py -q` → the two new tests FAIL (`sanitizedClaims` missing; old flow repairs list items).

- [ ] **Step 2: Implement**

In `src/showdown_copilot/preview_plan.py`:

**(a)** Import: `from .preview_verifier import issue_messages, sanitize_preview_plan, verify_preview_plan`.

**(b)** Add to `PreviewPlanResponse`:

```python
    sanitizedClaims: list[str] = Field(default_factory=list)
```

**(c)** Token clamp: in `_anthropic_preview_plan` change `"max_tokens": int(preset.get("maxOutputTokens") or 2200),` to `"max_tokens": min(int(preset.get("maxOutputTokens") or 2200), 2500),` and in `_openai_preview_plan` change `"max_output_tokens": int(preset.get("maxOutputTokens") or 1800),` to `"max_output_tokens": min(int(preset.get("maxOutputTokens") or 1800), 2500),`.

**(d)** Replace the verification/repair block in `build_preview_plan` (everything from `my_species = [...]` through the `if issues: return _fallback_plan(...)` line) with:

```python
    my_species = [mon.species for mon in req.myTeam]
    sanitized_claims: list[str] = []

    issues = verify_preview_plan(plan, req.opponentTeam, my_species)
    if issues:
        data, removed, core_issues = sanitize_preview_plan(plan, issues)
        plan = _coerce_plan(data)
        sanitized_claims.extend(removed)

        repair_attempts = max(0, int(os.environ.get("SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS", "1")))
        if core_issues and repair_attempts > 0:
            try:
                repaired_json, repair_usage, repair_raw_text = await repair_preview_plan_json(
                    provider=provider,
                    preset=preset,
                    plan=plan.model_dump(),
                    issues=[issue.model_dump() for issue in core_issues],
                    schema=_matchup_plan_json_schema(),
                )
                plan = _coerce_plan(repaired_json)
                usage = merge_plan_and_repair_usage(usage, repair_usage)
                raw_text = f"{raw_text}\n\n[repair]\n{repair_raw_text or ''}".strip()
                data, removed, core_issues = sanitize_preview_plan(
                    plan, verify_preview_plan(plan, req.opponentTeam, my_species),
                )
                plan = _coerce_plan(data)
                sanitized_claims.extend(removed)
            except Exception as exc:  # noqa: BLE001 - keep live preview degradable.
                return _fallback_plan(req, reason=f"model preview repair failed: {exc}")

        if core_issues:
            return _fallback_plan(
                req,
                reason=f"core mechanics validation failed: {'; '.join(issue_messages(core_issues))}",
            )
```

and add `sanitizedClaims=sanitized_claims,` to the final `PreviewPlanResponse(...)` construction.

**(e)** If existing tests exercise the old two-repair loop (search `tests/` for `SHOWDOWN_PREVIEW_REPAIR_ATTEMPTS` and `repair_preview_plan_json`), update them to the behavior deltas listed in this task's Interfaces block — list-item issues no longer repair; the env default is 1.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_preview_plan.py tests/test_preview_sanitize.py -q` then `uv run pytest -q`
Expected: PASS (full suite green apart from unrelated suites' pre-existing skips).

- [ ] **Step 4: Commit**

```bash
git add src/showdown_copilot/preview_plan.py tests/test_preview_plan.py
git commit -m "feat(preview): sanitize-first verification, single core repair, sanitizedClaims, token clamp"
```

---

### Task 15: Surface `sanitizedClaims` on the card

**Files:**
- Modify: `extension/lib/matchup-plan.ts` (response type)
- Modify: `extension/panels/matchup-plan-card.ts`
- Test: `extension/test/panels/matchup-plan-card.test.ts` (append)

**Interfaces:**
- Consumes: `PreviewPlanResponse.sanitizedClaims` (Task 14).
- Produces: `.sc-plan-sanitized` line on the card when claims were removed.

- [ ] **Step 1: Write the failing test**

Append to `extension/test/panels/matchup-plan-card.test.ts`:

```ts
  it('notes removed claims when the checker sanitized the plan', () => {
    const el = renderMatchupPlanCard(response({ sanitizedClaims: ['bad claim A', 'bad claim B'] } as any));
    const note = el.querySelector<HTMLElement>('.sc-plan-sanitized');
    expect(note?.textContent).toContain('2 claims removed');
    expect(note?.title).toContain('bad claim A');
  });

  it('omits the sanitized note when nothing was removed', () => {
    const el = renderMatchupPlanCard(response({}));
    expect(el.querySelector('.sc-plan-sanitized')).toBeNull();
  });
```

Run: `cd extension && npx vitest run test/panels/matchup-plan-card.test.ts` → new cases FAIL.

- [ ] **Step 2: Implement**

In `extension/lib/matchup-plan.ts`, add to `PreviewPlanResponse`:

```ts
  sanitizedClaims?: string[];
```

In `extension/panels/matchup-plan-card.ts`, above the `el.innerHTML =` assignment add:

```ts
  const sanitized = response.sanitizedClaims ?? [];
  const sanitizedHtml = sanitized.length
    ? `<div class="sc-plan-sanitized" title="${escapeHtml(sanitized.join('\n'))}">${sanitized.length} claim${sanitized.length === 1 ? '' : 's'} removed by mechanics checker</div>`
    : '';
```

and append `${sanitizedHtml}` at the end of the template (after the danger-rules block).

In `extension/styles/tcg.css` append:

```css
.sc-plan-sanitized { font-size: 10px; opacity: 0.65; margin-top: 4px; }
```

- [ ] **Step 3: Run the full extension suite**

Run: `cd extension && npx vitest run`
Expected: all pass except the known flavor-strip failure.

- [ ] **Step 4: Commit**

```bash
git add extension/lib/matchup-plan.ts extension/panels/matchup-plan-card.ts extension/styles/tcg.css extension/test/panels/matchup-plan-card.test.ts
git commit -m "feat(extension): show sanitized-claims count on matchup plan card"
```

**STAGE 6 GATE — user check:** replay several saved previews via the evaluator; flagged claims appear under "Sanitized claims" with the plan intact; live card shows the removed-claims note when it fires.

---

### Task 16: Final acceptance sweep

**Files:** none (verification only).

- [ ] **Step 1: Full automated sweep**

```bash
cd /Users/edkiboma/Projects/pokemon-ai/showdown-stack && uv run pytest -q
cd extension && npx vitest run
```
Expected: pytest fully green; vitest green except the pre-existing flavor-strip failure.

- [ ] **Step 2: Run the spec §8 acceptance checklist with the user (live stack)**

1. Plan card appears at preview, full model plan visible by turn 2-3, persists collapsibly to battle end.
2. Kill provider mid-preview → labeled heuristic; restore → model plan within the retry budget.
3. Evaluator replay shows a plan shipping with a dropped rule instead of falling back.
4. Fake-mode plan references only request species.
5. `SHOWDOWN_PREVIEW_LOG_PROMPT=1` shows damage summary + likely sets + speed context in the prompt.
6. Both suites as in Step 1.

- [ ] **Step 3: Commit any checklist fixes, then tag**

```bash
git tag matchup-plan-v2
```
