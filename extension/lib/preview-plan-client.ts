import type { PreviewPlanRequest, PreviewPlanResponse } from './matchup-plan';

const cache = new Map<string, PreviewPlanResponse>();
const inFlight = new Map<string, Promise<PreviewPlanResponse | null>>();

export async function fetchPreviewPlan(
  proxyUrl: string,
  request: PreviewPlanRequest,
): Promise<PreviewPlanResponse | null> {
  const key = request.battleId || JSON.stringify(request.opponentTeam);
  if (cache.has(key)) return cache.get(key)!;
  if (inFlight.has(key)) return inFlight.get(key)!;

  const promise = (async () => {
    try {
      const response = await fetch(`${proxyUrl}/preview-plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });
      if (!response.ok) return null;
      const body = await response.json() as PreviewPlanResponse;
      if (body?.plan) cache.set(key, body);
      return body?.plan ? body : null;
    } catch (err) {
      console.warn('[sc:preview-plan] fetch failed', err);
      return null;
    } finally {
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, promise);
  return promise;
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
