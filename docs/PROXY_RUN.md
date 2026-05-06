# Plan H Proxy — How to Run

Path B-lite from `project_plan_h_phase2_extension.md`. The proxy sits between
the Chrome extension and poke-engine, applying belief tracking to opp Pokemon
predictions before they reach the engine.

```
extension :7271 (proxy)  →  :7267 (poke-engine)
                ↑
        per-battle BeliefTracker
        replaces moves / item / ability / teraType
        on each opp Pokemon using priors.get_set(belief=...)
```

## One-time setup

```bash
cd ~/Projects/showdown-copilot
uv pip install -e '.[proxy]'   # adds fastapi + uvicorn
```

## Start order each session

1. **Engine** (existing — must be running on `:7267`):

   ```bash
   cd ~/Projects/poke-engine && cargo run --release --bin server
   ```

2. **Proxy** (new):

   ```bash
   cd ~/Projects/showdown-copilot
   .venv/bin/python -m showdown_copilot.proxy
   # or: .venv/bin/sc-proxy
   ```

   First start downloads the chaos JSON for the current month (Smogon stats)
   to `~/.showdown-copilot/cache/`. Subsequent starts are instant.

3. **Extension** (rebuild required since `content.ts` changed):

   ```bash
   cd ~/Projects/showdown-copilot/extension
   npm run dev    # WXT dev mode — auto-rebuilds on save
   ```

   Then in Chrome at `chrome://extensions`, reload the extension. Confirm
   "Loaded from" path matches `extension/.output/chrome-mv3-dev` (the
   stale-build trap — see `project_extension_build_trap.md`).

## Verify it's working

Open a Showdown battle. In the proxy terminal you should see, per turn:

```
2026-04-28 ... [INFO] [battle-gen9ou-12345][turn-applied] fmt=gen9ou opp_mons=6 trackers=1
2026-04-28 ... [INFO]   greatkleavor: rev_moves=['stoneedge'] rev_item=None rev_abil=sharpness ... → modal_moves=['stoneedge', 'closecombat', 'knockoff', 'swordsdance']
```

Per-Pokemon log lines only fire once that Pokemon has at least one reveal
or impossibility. Unrevealed reserve mons are silent (correct — modal
overlay is identical to the extension's existing chaos top-4 padding).

## Bypass the proxy (engine-only mode)

To compare Plan H proxy on/off:

- **With proxy** (default): `ENGINE_URL = 'http://localhost:7271/...'` in
  `extension/entrypoints/content.ts:16`. Proxy must be running.
- **Without proxy**: change to `'http://localhost:7267/...'`. Skips belief
  overlay entirely; extension talks directly to engine like before Plan H.

Save, WXT auto-rebuilds, reload extension at `chrome://extensions`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Panel shows "Copilot — error (engine down?)" | Proxy not running | Start `python -m showdown_copilot.proxy` |
| Panel shows "Copilot — HTTP 500" + proxy logs `engine forward failed` | Engine not running on `:7267` | Start poke-engine server |
| Proxy logs `no chaos entry for X in gen9ou` | Species not in current month's chaos | Returns neutral default — non-fatal, just no belief lift for that mon |
| Proxy logs `_planH missing battleId` | Old extension build cached | Reload extension at `chrome://extensions`; confirm WXT dev server is running |
| `pytest tests/test_proxy.py` passes but live battle shows no log lines | Loaded extension is stale | Check chrome://extensions "Loaded from" path matches your dev output |

## What it actually does

- **Replaces** `pokemon.moves` always with belief-aware modal top-4 (modal
  guarantees a superset of revealed_moves, so observed moves never get
  dropped).
- **Backfills** `pokemon.item` only when extension sent `'none'`
  (unrevealed). Real reveals are preserved.
- **Backfills** `pokemon.ability` only when extension sent `'none'`.
- **Backfills** `pokemon.teraType` only when extension sent `''`
  (extension hardcodes empty for opp at content.ts:215).

Stats (atk/def/spa/spd/spe/hp/maxhp), level, types, weightKg, status
counters, side conditions, weather, terrain — all left untouched. Stats
overlay is deferred until Phase 2 ships speed-range narrowing.

## Coverage note

Path B-lite exercises the move-superset filter and the impossible_*
indirect filter (via item/ability backfill), which is exactly what
Layer 3 A/B sweep validated (PLAN-H 28-4 vs BASELINE 31-1). It does
NOT fire R1-R5 inference rules — those need raw protocol messages,
not the parsed BattleRequest. Path B+ adds that capability later.
