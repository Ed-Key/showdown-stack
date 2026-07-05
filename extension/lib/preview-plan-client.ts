import type { PreviewPlanRequest, PreviewPlanResponse } from './matchup-plan';
import {
  isPermanentResponse,
  shouldRequestPlan,
  type PlanCacheEntry,
} from './plan-lifecycle';

export type PlanEntry = PlanCacheEntry & { response: PreviewPlanResponse | null };

const entries = new Map<string, PlanEntry>();

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
  const key = request.battleId;
  if (!key) return null;
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

export function previewPokemonFromSnapshot(snapshot: any) {
  return {
    species: String(snapshot?.species ?? ''),
    item: snapshot?.item && snapshot.item !== 'none' ? String(snapshot.item) : null,
    ability: snapshot?.ability && snapshot.ability !== 'none' ? String(snapshot.ability) : null,
    teraType: snapshot?.teraType ? String(snapshot.teraType) : null,
    moves: (snapshot?.moves ?? [])
      .map((move: any) => String(move?.id ?? move?.move ?? move ?? ''))
      .filter((move: string) => move && move !== 'none'),
  };
}
