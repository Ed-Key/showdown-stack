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
