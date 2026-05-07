import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchExplanation } from '../../lib/explainer';

describe('fetchExplanation', () => {
  beforeEach(() => { (global as any).fetch = vi.fn(); });

  it('caches on (battleId, turn, rqid)', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true, json: async () => ({ explanation: 'first call' }),
    });
    const r1 = await fetchExplanation({
      proxyUrl: 'http://x', battleId: 'b', turn: 1, rqid: 1,
      snapshot: {}, engineResult: {}, lastSteps: [],
    });
    const r2 = await fetchExplanation({
      proxyUrl: 'http://x', battleId: 'b', turn: 1, rqid: 1,
      snapshot: {}, engineResult: {}, lastSteps: [],
    });
    expect(r1).toBe('first call');
    expect(r2).toBe('first call');
    expect((global.fetch as any).mock.calls).toHaveLength(1);
  });
});
