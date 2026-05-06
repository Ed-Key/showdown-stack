#!/usr/bin/env bash
# Restart the Showdown copilot engine binary.
#
# Default flags reflect the bug-#3-fix configuration shipped 2026-05-01.
# Killswitch: set --heuristic-prior-mix-side2 0.0 --forced-playouts-c-side2 0.0
# to revert to Plan I behavior.

set -euo pipefail

PORT="${ENGINE_PORT:-7270}"
NN_URL="${NN_SIDECAR_URL:-http://localhost:7273}"
LOG="${ENGINE_LOG:-/tmp/engine.log}"

# Auto-detect binary: prefer canonical path, fall back to the symmetric-side2
# worktree (where it lives until feat/symmetric-side2-prior merges to main).
ENGINE_DIR_CANONICAL="$(dirname "$0")/../engine"
ENGINE_DIR_WORKTREE="$ENGINE_DIR_CANONICAL/.worktrees/symmetric-side2"

if [ -x "$ENGINE_DIR_CANONICAL/target/release/server" ]; then
  ENGINE_DIR="$ENGINE_DIR_CANONICAL"
elif [ -x "$ENGINE_DIR_WORKTREE/target/release/server" ]; then
  ENGINE_DIR="$ENGINE_DIR_WORKTREE"
else
  echo "ERROR: no engine binary found at:" >&2
  echo "  $ENGINE_DIR_CANONICAL/target/release/server" >&2
  echo "  $ENGINE_DIR_WORKTREE/target/release/server" >&2
  echo "Run: (cd \"\$ENGINE_DIR\" && cargo build --release --features=gen9,terastallization)" >&2
  exit 1
fi

ENGINE_DIR="$(cd "$ENGINE_DIR" && pwd)"
echo "Using engine dir: $ENGINE_DIR"

EXISTING_PID="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
if [ -n "$EXISTING_PID" ]; then
  echo "Killing existing engine on :$PORT (PID $EXISTING_PID)"
  kill "$EXISTING_PID"
  sleep 1
fi

cd "$ENGINE_DIR"
nohup ./target/release/server \
  --port "$PORT" \
  --nn-eval --nn-url "$NN_URL" \
  --heuristic-prior-mix 0.25 --forced-playouts-c 2.0 \
  --heuristic-prior-mix-side2 0.5 --forced-playouts-c-side2 2.0 \
  > "$LOG" 2>&1 &
disown

sleep 2
NEW_PID="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
if [ -n "$NEW_PID" ]; then
  echo "Engine restarted on :$PORT (PID $NEW_PID), log: $LOG"
else
  echo "ERROR: engine failed to start. Tail of $LOG:" >&2
  tail -20 "$LOG" >&2
  exit 1
fi
