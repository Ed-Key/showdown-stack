#!/usr/bin/env bash
# Run poke-engine with the engine-prior-tuning sweep config (2026-05-08).
#
# Activates four convergent fixes from 4-agent analysis:
#   1. prior_cap=0.5         clip top-1 NN spike to <=50% before MCTS sees it
#   2. dirichlet_alpha=0.3   AlphaZero-style root noise (concentration)
#   3. dirichlet_eps=0.25    AlphaZero default mixing fraction
#   4. eval_slope=0.005      desaturate leaf sigmoid (was 0.0125)
#   + c_puct=3.0             allow MCTS value evidence to overcome prior
#
# To run BASELINE (defaults, bit-identical pre-branch), pass --baseline.

set -euo pipefail

ENGINE_DIR="${ENGINE_DIR:-/Users/edkiboma/Projects/pokemon-ai/showdown-stack/engine}"
BIN="${BIN:-${ENGINE_DIR}/target/release/server}"
LOG="${LOG:-/tmp/engine.log}"
PORT="${PORT:-7270}"
NN_URL="${NN_URL:-http://localhost:7273}"

if [[ "${1:-}" == "--baseline" ]]; then
  echo "[run-engine-tuned] BASELINE mode (defaults — bit-identical pre-branch)"
  PRIOR_CAP=1.0
  DIRICHLET_ALPHA=0.0
  DIRICHLET_EPS=0.0
  EVAL_SLOPE=0.0125
  C_PUCT=1.25
else
  echo "[run-engine-tuned] TUNED mode (sweep config)"
  PRIOR_CAP="${PRIOR_CAP:-0.5}"
  DIRICHLET_ALPHA="${DIRICHLET_ALPHA:-0.3}"
  DIRICHLET_EPS="${DIRICHLET_EPS:-0.25}"
  EVAL_SLOPE="${EVAL_SLOPE:-0.005}"
  C_PUCT="${C_PUCT:-3.0}"
fi

# Build if missing
if [[ ! -x "$BIN" ]]; then
  echo "[run-engine-tuned] building server binary..."
  (cd "$ENGINE_DIR" && cargo build --release --features=gen9,terastallization --bin server)
fi

# Snapshot existing log before overwrite (so we can A/B compare)
if [[ -s "$LOG" ]]; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  cp "$LOG" "${LOG%.log}-baseline-${STAMP}.log"
  echo "[run-engine-tuned] snapshotted existing log → ${LOG%.log}-baseline-${STAMP}.log"
fi

# Kill any existing engine on $PORT
PIDS="$(lsof -ti :"$PORT" 2>/dev/null || true)"
if [[ -n "$PIDS" ]]; then
  echo "[run-engine-tuned] killing existing engine pids: $PIDS"
  kill -TERM $PIDS 2>/dev/null || true
  sleep 1
  # Force-kill if still alive
  PIDS2="$(lsof -ti :"$PORT" 2>/dev/null || true)"
  [[ -n "$PIDS2" ]] && kill -9 $PIDS2 2>/dev/null || true
fi

echo "[run-engine-tuned] config:"
echo "  prior_cap        = $PRIOR_CAP"
echo "  dirichlet_alpha  = $DIRICHLET_ALPHA"
echo "  dirichlet_eps    = $DIRICHLET_EPS"
echo "  eval_slope       = $EVAL_SLOPE"
echo "  c_puct           = $C_PUCT"
echo "  log              = $LOG"

# Heuristic-prior-mix and forced-playouts left at production values
# (already-shipped tuning from prior plans).
nohup "$BIN" \
  --port "$PORT" \
  --nn-eval \
  --nn-url "$NN_URL" \
  --c-puct "$C_PUCT" \
  --heuristic-prior-mix 0.25 \
  --forced-playouts-c 2.0 \
  --heuristic-prior-mix-side2 0.5 \
  --forced-playouts-c-side2 2.0 \
  --prior-cap "$PRIOR_CAP" \
  --dirichlet-alpha "$DIRICHLET_ALPHA" \
  --dirichlet-eps "$DIRICHLET_EPS" \
  --eval-slope "$EVAL_SLOPE" \
  > "$LOG" 2>&1 &

PID=$!
sleep 2
if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "[run-engine-tuned] engine UP on :$PORT (pid=$PID, log=$LOG)"
else
  echo "[run-engine-tuned] engine FAILED to start. tail of log:"
  tail -20 "$LOG"
  exit 1
fi
