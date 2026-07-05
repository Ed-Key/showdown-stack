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

  it('does not fetch or cache when battleId is empty', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(MODEL_BODY));
    vi.stubGlobal('fetch', fetchMock);
    const result = await requestPreviewPlan('http://p', { ...REQUEST, battleId: '' }, 1000);
    expect(result).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('treats non-ok responses as transient errors', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, false));
    vi.stubGlobal('fetch', fetchMock);
    const result = await requestPreviewPlan('http://p', REQUEST, 1000);
    expect(result).toBeNull();
    expect(previewPlanEntry('battle-x')?.status).toBe('error');
    expect(previewPlanEntry('battle-x')?.permanent).toBe(false);
  });
});
