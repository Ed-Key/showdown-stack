// Pre-computed top-4 opponent-move priors per species, derived from Smogon
// chaos JSON for Gen 9 Monotype at module load. Returns move IDs in the
// same normalized form (lowercase, alphanumeric) as poke-engine expects.
import chaosRaw from '../data/chaos-gen9monotype.json';

const norm = (s: string) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

type ChaosSpecies = { Moves?: Record<string, number> };
type ChaosDump = { data: Record<string, ChaosSpecies> };

const TOP_N = 4;
const priors: Record<string, string[]> = (() => {
  const out: Record<string, string[]> = {};
  const data = (chaosRaw as ChaosDump).data || {};
  for (const [species, info] of Object.entries(data)) {
    const moves = info.Moves || {};
    const sorted = Object.entries(moves)
      .filter(([m]) => m !== '' && m !== 'Nothing')
      .sort((a, b) => b[1] - a[1])
      .slice(0, TOP_N)
      .map(([m]) => norm(m));
    out[norm(species)] = sorted;
  }
  return out;
})();

/** Returns top-N likely move IDs (normalized) for a species, or [] if unknown. */
export function priorMovesForSpecies(speciesDisplay: string): string[] {
  return priors[norm(speciesDisplay)] || [];
}

/** Exposed for tests / debugging. */
export const _priorsTable = priors;
