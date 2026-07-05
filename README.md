# Pokemon Showdown Copilot

Human-in-the-loop AI copilot for Pokemon Showdown battles.

The system watches a live battle, translates the page state into a battle
request, tracks hidden opponent information, and streams real-time move
recommendations from a Rust MCTS engine.

## Architecture

```text
Pokemon Showdown page
        |
        v
TypeScript WXT extension
        |
        v
Python FastAPI proxy (:7271)
  - belief tracking
  - Smogon usage priors
  - speed / Choice Scarf inference
  - PIMC hidden-information hypotheses
        |
        v
Rust Axum engine (:7270)
  - MCTS / PUCT
  - heuristic prior blending
  - forced playouts
  - structured telemetry
        |
        v
Python Metamon sidecar (:7273)
  - Kakuna neural policy prior
```

The project is intentionally a copilot rather than an autoplay bot. The engine
is treated as a signal source for a human player, with telemetry, annotations,
postmortems, and conflict checks around the model.

## Demo

See [docs/DEMO.md](docs/DEMO.md).

Quick start on the original development machine:

```bash
scripts/start-demo-stack.sh
```

Then load the unpacked Chrome extension from:

```text
extension/.output/chrome-mv3-dev
```

## Key Technical Ideas

- Rust search engine for the hot path.
- Python proxy for stateful belief tracking and hidden-information sampling.
- TypeScript extension for live Showdown integration and UI.
- Neural policy prior at the root of search, not at every leaf.
- Heuristic prior dampening and forced playouts to keep the neural prior from
  starving alternatives.
- Side2 opponent-prior fix for better opponent move prediction.
- PIMC aggregation over sampled opponent hypotheses.
- Replay logs, postmortems, annotations, and structured engine telemetry.

## Public Release Note

This repository should be cleaned before publication. Do not publish `.env`,
local caches, generated battle postmortems, personal notes, cloned Showdown
servers, build artifacts, or model weights.
