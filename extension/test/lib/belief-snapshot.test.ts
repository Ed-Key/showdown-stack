import { describe, it, expect, vi } from 'vitest';
import { fetchBeliefSnapshot } from '../../lib/belief-snapshot';

describe('fetchBeliefSnapshot', () => {
  it('returns parsed snapshot on 200', async () => {
    const fake = { battle_id: 'b1', format: 'gen9nationaldexag', opponents: {} };
    global.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => fake })) as any;
    const result = await fetchBeliefSnapshot('http://localhost:7271', 'b1');
    expect(result).toEqual(fake);
  });

  it('returns null on 404', async () => {
    global.fetch = vi.fn(async () => ({ ok: false, status: 404 })) as any;
    const result = await fetchBeliefSnapshot('http://localhost:7271', 'unknown');
    expect(result).toBeNull();
  });

  it('returns null on network error', async () => {
    global.fetch = vi.fn(async () => { throw new Error('network down'); }) as any;
    const result = await fetchBeliefSnapshot('http://localhost:7271', 'b1');
    expect(result).toBeNull();
  });
});
