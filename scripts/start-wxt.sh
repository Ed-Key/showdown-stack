#!/bin/bash
# Durable WXT dev-server launcher.
#
# Plain `nohup … &` and `(nohup … &)` subshell-detach don't actually survive
# the Claude Code harness's per-turn shell teardown on macOS — WXT was dying
# 3-4× per session. This script runs WXT inside a detached `screen` session
# instead, which is truly orphaned from the spawning shell and lives until
# explicitly killed.
#
# Usage:
#   scripts/start-wxt.sh              # start if not running (no-op if up)
#   scripts/start-wxt.sh --restart    # kill existing session + start fresh
#   scripts/start-wxt.sh --status     # report status only
#   scripts/start-wxt.sh --stop       # kill the session
#   screen -r sc-wxt-dev              # attach (Ctrl-A D to detach again)
#   screen -X -S sc-wxt-dev quit      # equivalent to --stop
set -e

SESSION="sc-wxt-dev"
EXT_DIR="/Users/edkiboma/Projects/pokemon-ai/showdown-stack/extension"
LOG="/tmp/wxt-dev.log"

is_running() {
  screen -ls 2>/dev/null | grep -qE "\b\d+\.$SESSION\b"
}

start_session() {
  if is_running; then
    echo "✓ WXT already running in screen session '$SESSION'"
    echo "  Attach with:  screen -r $SESSION  (Ctrl-A D to detach)"
    return 0
  fi
  # macOS ships an ancient `screen` (4.00.03, 2006) that lacks -Logfile —
  # redirect stdout/stderr inside the bash -c command instead.
  screen -dmS "$SESSION" bash -c "cd '$EXT_DIR' && exec npm run dev > '$LOG' 2>&1"
  sleep 4
  if is_running; then
    echo "✓ WXT started in screen session '$SESSION' (log: $LOG)"
  else
    echo "✗ Failed to start WXT — check $LOG"
    exit 1
  fi
}

stop_session() {
  if is_running; then
    screen -X -S "$SESSION" quit
    sleep 1
    echo "✓ Stopped WXT session '$SESSION'"
  else
    echo "  No WXT session named '$SESSION' to stop"
  fi
}

case "${1:-}" in
  --status)
    if is_running; then
      echo "✓ WXT running (session: $SESSION)"
      screen -ls 2>/dev/null | grep "$SESSION" || true
    else
      echo "✗ WXT NOT running"
      exit 1
    fi
    ;;
  --stop)
    stop_session
    ;;
  --restart)
    stop_session
    start_session
    ;;
  ""|--start)
    start_session
    ;;
  *)
    echo "Usage: $0 [--start|--restart|--status|--stop]" >&2
    exit 2
    ;;
esac
