#!/usr/bin/env bash
# Start the demo stack:
#   metamon-sidecar :7273 -> Rust engine :7270 -> proxy :7271 -> WXT extension.
#
# This uses the last known strong live config:
#   - Kakuna/Metamon NN root policy prior
#   - Plan I heuristic prior dampening + forced playouts
#   - Bug #3 Side2 heuristic prior + forced playouts
#   - Proxy PIMC K=4

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_DIR="${ENGINE_DIR:-$ROOT/engine}"
SIDECAR_DIR="${SIDECAR_DIR:-$ROOT/sidecar}"

ENGINE_PORT="${ENGINE_PORT:-7270}"
PROXY_PORT="${PROXY_PORT:-7271}"
SIDECAR_PORT="${SIDECAR_PORT:-7273}"
PIMC_K="${POKE_PROXY_PIMC_K:-4}"

ENGINE_BIN="${ENGINE_BIN:-$ENGINE_DIR/target/release/server}"
SIDECAR_PY="${SIDECAR_PY:-$SIDECAR_DIR/metamon/.venv-py310/bin/python}"
PROXY_PY="${PROXY_PY:-$ROOT/.venv/bin/python}"

RUN_DIR="${RUN_DIR:-/tmp/showdown-copilot-demo}"
mkdir -p "$RUN_DIR"

SIDECAR_LOG="$RUN_DIR/sidecar.log"
ENGINE_LOG="$RUN_DIR/engine.log"
PROXY_LOG="$RUN_DIR/proxy.log"

SIDECAR_SESSION="${SIDECAR_SESSION:-sc-demo-sidecar}"
ENGINE_SESSION="${ENGINE_SESSION:-sc-demo-engine}"
PROXY_SESSION="${PROXY_SESSION:-sc-demo-proxy}"

kill_port() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "[demo] stopping $label on :$port ($pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
  fi
}

wait_http() {
  local url="$1"
  local label="$2"
  local tries="${3:-30}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then
      echo "[demo] $label ready"
      return 0
    fi
    sleep 1
  done
  echo "[demo] ERROR: $label did not become ready at $url" >&2
  return 1
}

require_file() {
  local path="$1"
  local hint="$2"
  if [[ ! -e "$path" ]]; then
    echo "[demo] ERROR: missing $path" >&2
    echo "[demo] $hint" >&2
    exit 1
  fi
}

require_file "$ENGINE_BIN" "Build it with: cd '$ENGINE_DIR' && cargo build --release --features=gen9,terastallization --bin server"
require_file "$SIDECAR_PY" "Expected the Metamon Python 3.10 venv at '$SIDECAR_DIR/metamon/.venv-py310'."
require_file "$PROXY_PY" "Install proxy deps with: cd '$ROOT' && uv pip install -e '.[proxy]'"

stop_screen() {
  local session="$1"
  if screen -ls 2>/dev/null | grep -qE "[0-9]+\\.$session\\b"; then
    echo "[demo] stopping screen session $session"
    screen -X -S "$session" quit || true
    sleep 1
  fi
}

echo "[demo] logs: $RUN_DIR"
stop_screen "$PROXY_SESSION"
stop_screen "$ENGINE_SESSION"
stop_screen "$SIDECAR_SESSION"
kill_port "$PROXY_PORT" "proxy"
kill_port "$ENGINE_PORT" "engine"
kill_port "$SIDECAR_PORT" "sidecar"

echo "[demo] starting metamon-sidecar on :$SIDECAR_PORT"
screen -dmS "$SIDECAR_SESSION" bash -lc "
  cd '$SIDECAR_DIR' &&
  echo \$\$ > '$RUN_DIR/sidecar.pid' &&
  exec env KAKUNA_DEVICE='${KAKUNA_DEVICE:-cpu}' SIDECAR_PORT='$SIDECAR_PORT' '$SIDECAR_PY' -m sidecar.nn_sidecar > '$SIDECAR_LOG' 2>&1
"
wait_http "http://127.0.0.1:$SIDECAR_PORT/healthz" "sidecar"

if [[ "${WARM_SIDECAR:-1}" != "0" ]]; then
  echo "[demo] warming Kakuna model with Iron Crown fixture"
  curl -fsS --max-time 120 \
    -H 'Content-Type: application/json' \
    --data-binary @<(jq -c '{state: ., perspective: "p2"}' "$ENGINE_DIR/tests/fixtures/iron_crown_t5.json") \
    "http://127.0.0.1:$SIDECAR_PORT/policy" >/dev/null
fi

echo "[demo] starting Rust engine on :$ENGINE_PORT"
screen -dmS "$ENGINE_SESSION" bash -lc "
  cd '$ENGINE_DIR' &&
  echo \$\$ > '$RUN_DIR/engine.pid' &&
  exec '$ENGINE_BIN' \
    --port "$ENGINE_PORT" \
    --nn-eval --nn-url "http://localhost:$SIDECAR_PORT" \
    --heuristic-prior-mix 0.25 --forced-playouts-c 2.0 \
    --heuristic-prior-mix-side2 0.5 --forced-playouts-c-side2 2.0 \
    > '$ENGINE_LOG' 2>&1
"
wait_http "http://127.0.0.1:$ENGINE_PORT/status" "engine"

echo "[demo] starting proxy on :$PROXY_PORT with PIMC K=$PIMC_K"
screen -dmS "$PROXY_SESSION" bash -lc "
  cd '$ROOT' &&
  set -a &&
  if [[ -f .env ]]; then source .env; fi &&
  set +a &&
  export POKE_PROXY_PIMC_K='$PIMC_K' &&
  echo \$\$ > '$RUN_DIR/proxy.pid' &&
  exec '$PROXY_PY' -m showdown_copilot.proxy > '$PROXY_LOG' 2>&1
"
wait_http "http://127.0.0.1:$PROXY_PORT/healthz" "proxy"

echo "[demo] starting WXT extension dev server"
kill_port 3000 "WXT dev server"
"$ROOT/scripts/start-wxt.sh" --restart

echo
echo "[demo] ready"
echo "  sidecar health: http://127.0.0.1:$SIDECAR_PORT/healthz"
echo "  engine status:  http://127.0.0.1:$ENGINE_PORT/status"
echo "  proxy health:   http://127.0.0.1:$PROXY_PORT/healthz"
echo "  extension path: $ROOT/extension/.output/chrome-mv3-dev"
echo "  logs:           $RUN_DIR"
