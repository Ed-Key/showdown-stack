// Pre-computed top-4 opponent-move priors per species, derived from Smogon
// chaos JSON for Gen 9 Monotype. The flat lookup table is generated offline
// by scripts/flatten-chaos.mjs (rerun after scripts/fetch-chaos.sh) so we
// bundle ~22 KB of pre-sorted IDs instead of the 3.9 MB raw chaos dump.
// Move IDs are normalized (lowercase, alphanumeric) to match poke-engine.
import flatPriors from '../data/chaos-priors-flat.json';

const priors = flatPriors as Record<string, string[]>;

const norm = (s: string) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

/** Returns up to 4 likely move IDs (normalized) for a species, or [] if unknown. */
export function priorMovesForSpecies(speciesDisplay: string): string[] {
  return priors[norm(speciesDisplay)] || [];
}

/** Exposed for tests / debugging. */
export const _priorsTable = priors;
