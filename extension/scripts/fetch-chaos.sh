#!/usr/bin/env bash
# Pulls Smogon chaos JSON for a recent Monotype month.
# Re-run periodically to refresh opponent-move priors.
set -euo pipefail
MONTH="${1:-2026-03}"   # YYYY-MM; Smogon publishes lagging by ~1 month
URL="https://www.smogon.com/stats/${MONTH}/chaos/gen9monotype-1630.json"
OUT="$(cd "$(dirname "$0")/.." && pwd)/data/chaos-gen9monotype.json"
echo "Fetching ${URL}"
curl -fsSL "${URL}" -o "${OUT}"
echo "Saved to ${OUT} ($(wc -c < "${OUT}") bytes)"
