#!/usr/bin/env node
// Reduce chaos JSON to just {speciesNorm: [top4Moves]} for bundling.
// Run after scripts/fetch-chaos.sh to produce data/chaos-priors-flat.json,
// which is what utils/chaos-priors.ts actually imports. The flat file is
// ~30 KB vs the 3.9 MB raw, so Vite inlines it into content.js without
// inflating the bundle.
import { readFileSync, writeFileSync } from 'node:fs';

const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const raw = JSON.parse(readFileSync('./data/chaos-gen9monotype.json', 'utf8'));
const out = {};
for (const [sp, info] of Object.entries(raw.data || {})) {
  const moves = Object.entries(info.Moves || {})
    .filter(([m]) => m && m !== 'Nothing')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([m]) => norm(m));
  out[norm(sp)] = moves;
}
writeFileSync('./data/chaos-priors-flat.json', JSON.stringify(out));
console.log('wrote', Object.keys(out).length, 'species to data/chaos-priors-flat.json');
