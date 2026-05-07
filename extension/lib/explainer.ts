// extension/lib/explainer.ts

const cache = new Map<string, string>();

export async function fetchExplanation(opts: {
  proxyUrl: string;
  battleId: string;
  turn: number;
  rqid: number;
  snapshot: any;
  engineResult: any;
  lastSteps: string[];
  matrixSummary?: any;
}): Promise<string | null> {
  const key = `${opts.battleId}:${opts.turn}:${opts.rqid}`;
  if (cache.has(key)) return cache.get(key)!;
  try {
    const body: Record<string, any> = {
      battle_id: opts.battleId,
      turn: opts.turn,
      rqid: opts.rqid,
      snapshot: opts.snapshot,
      engine_result: opts.engineResult,
      last_steps: opts.lastSteps,
    };
    if (opts.matrixSummary !== undefined) {
      body.matrix_summary = opts.matrixSummary;
    }
    const res = await fetch(`${opts.proxyUrl}/explain`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    const respBody = await res.json();
    const text = respBody.explanation as string;
    cache.set(key, text);
    if (cache.size > 200) {
      const firstKey = cache.keys().next().value;
      if (firstKey !== undefined) cache.delete(firstKey);
    }
    return text;
  } catch (err) {
    console.warn('[sc:explain] fetch failed', err);
    return null;
  }
}
