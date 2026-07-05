# Demo Runbook

This is the safest way to demo the Pokemon Showdown copilot live.
Use a private or unrated battle. Do not present it as ranked-ladder automation;
the project is a human-in-the-loop AI systems demo.

## Start The Stack

From `showdown-stack`:

```bash
scripts/start-demo-stack.sh
```

The launcher starts:

| Service | Port | Purpose |
|---|---:|---|
| `metamon-sidecar` | 7273 | Kakuna/Metamon neural policy prior |
| Rust `poke-engine` server | 7270 | MCTS/PUCT search |
| `showdown_copilot.proxy` | 7271 | belief tracking, PIMC, annotations, explanations |
| WXT extension dev server | 3000 | Chrome extension build/reload |

The engine flags are the important part:

```bash
--nn-eval --nn-url http://localhost:7273
--heuristic-prior-mix 0.25 --forced-playouts-c 2.0
--heuristic-prior-mix-side2 0.5 --forced-playouts-c-side2 2.0
```

The proxy runs with:

```bash
POKE_PROXY_PIMC_K=4
```

Logs are written to `/tmp/showdown-copilot-demo/`.

## Load The Chrome Extension

1. Open `chrome://extensions`.
2. Enable Developer Mode.
3. Click **Load unpacked**.
4. Select:

```text
showdown-stack/extension/.output/chrome-mv3-dev
```

5. Open `https://play.pokemonshowdown.com/`.

The extension injects a floating `Copilot` panel into active battles. It talks
only to the local proxy at `http://localhost:7271`.

## Quick Health Checks

```bash
curl -s http://127.0.0.1:7273/healthz | jq
curl -s http://127.0.0.1:7270/status | jq
curl -s http://127.0.0.1:7271/healthz | jq
```

Optional full-stack smoke test:

```bash
curl -sS -N \
  -H 'Content-Type: application/json' \
  --data-binary @<(jq '. + {
    timeLimitMs: 1000,
    updateIntervalMs: 250,
    battleId: "demo-proxy-pimc",
    turn: 5,
    _planH: {battleId: "demo-proxy-pimc", turn: 5, format: "gen9nationaldex"}
  }' engine/tests/fixtures/iron_crown_t5.json) \
  http://127.0.0.1:7271/analyze/stream
```

Expected: one streamed JSON response with `bestMove`, `pv`, `alternatives`,
and `pimcBreakdown`.

## Recording Plan

Keep the video short: 60-90 seconds.

1. Show the architecture README or a diagram for 5 seconds.
2. Show health checks for the three local services.
3. Show Chrome with the extension loaded.
4. Open a private/unrated Showdown battle.
5. Pause on one decision turn and show:
   - recommended move
   - principal variation
   - alternatives
   - PIMC hypothesis breakdown
   - threat/conflict panels if they appear
6. Show a terminal tail of `/tmp/showdown-copilot-demo/engine.log` with
   `[ENGINE-INSTRUMENT]` fields like `policy_entropy`,
   `s2_heuristic_pick_dmg`, and `forced_playouts_triggered_s2`.

Do not show `.env`, API keys, browser passwords, or personal battle notes.

## Talk Track

> This is a live Pokemon Showdown copilot. The TypeScript extension reads the
> current battle state and streams it to a Python proxy. The proxy maintains
> hidden-information beliefs and fans out multiple opponent hypotheses. A Rust
> engine runs MCTS/PUCT, using a Metamon/Kakuna neural policy as a root prior,
> then blends in deterministic heuristics and forced playouts so the model
> cannot over-dominate the search. The important lesson was not that the model
> is always right; it was building the system around it: fallbacks, telemetry,
> postmortems, annotations, and human review.

## Public GitHub Checklist

Do not publish the whole `pokemon-ai` workspace as-is. It contains local
experiments, cloned servers, generated caches, postmortems, and private notes.

Public-safe material:

- `showdown-stack/src/showdown_copilot/`
- `showdown-stack/extension/`
- your modified `showdown-stack/engine/` fork
- `showdown-stack/sidecar/sidecar/`
- focused tests and fixtures that do not contain private usernames/notes
- this runbook and the README

Keep private or scrub first:

- `.env`
- `.venv/`, `node_modules/`, `target/`, `.output/`, cache folders
- `workspace/analysis/battle-postmortems/`
- `workspace/analysis/play-notes/`
- `workspace/analysis/engine-replay/`
- local Pokemon Showdown server clones
- any API keys, usernames, auth cookies, or personal notes

Good public repo shape:

```text
pokemon-showdown-copilot/
  README.md
  docs/DEMO.md
  extension/
  proxy/
  engine/
  sidecar/
  tests/
```

For the video, upload `demo.mp4` to the GitHub release, a README asset, or a
LinkedIn/YouTube unlisted link. Keep the README focused on architecture,
tradeoffs, and what you learned.
