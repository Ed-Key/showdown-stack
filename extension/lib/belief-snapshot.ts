// extension/lib/belief-snapshot.ts

export type ModalEntry = { name: string; pct: number };
export type OpponentBeliefSnapshot = {
  revealed: { moves: string[]; item: string | null; ability: string | null; tera_type: string | null };
  modal: { moves: ModalEntry[]; items: ModalEntry[]; abilities: ModalEntry[]; spreads: ModalEntry[]; tera_types: ModalEntry[] };
  speed_range: [number, number] | null;
  item_inferred_choicescarf: boolean;
};
export type BeliefSnapshot = {
  battle_id: string;
  format: string;
  opponents: Record<string, OpponentBeliefSnapshot>;
};

export async function fetchBeliefSnapshot(
  proxyUrl: string,
  battleId: string,
): Promise<BeliefSnapshot | null> {
  try {
    const res = await fetch(`${proxyUrl}/belief/${encodeURIComponent(battleId)}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('[sc:belief] fetch failed', err);
    return null;
  }
}
