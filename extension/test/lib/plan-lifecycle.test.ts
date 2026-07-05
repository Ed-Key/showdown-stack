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
