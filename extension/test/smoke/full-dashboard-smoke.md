# TCG Dashboard v1A — Manual Smoke Test Checklist

Run this before merging the dashboard redesign to main.

## Setup
- [ ] `npm run dev` is running (extension watcher active)
- [ ] `chrome://extensions` shows the extension loaded from `.output/chrome-mv3-dev/`
- [ ] Showdown Copilot proxy running on :7271
- [ ] Engine running on :7270

## Visual QA — open a Showdown battle replay or live battle

- [ ] **Header** — gold "COPILOT" title visible, Colorless orb logo, format slug, animated LIVE pulse, turn pill
- [ ] **Conflict banner** — red striped, only visible when a conflict rule fires (test with a turn where opp can guaranteed-OHKO)
- [ ] **Main card** — silver foil frame visible, type color matches recommended move type (e.g. blue for Ice, red for Fire)
- [ ] **Card top** — stage badge sprite loads, move name displays, trend arrow + delta + type pip render
- [ ] **Art frame** — Pokémon sprite loads via `/sprites/home/`, no broken-image icon
- [ ] **Type flair** — animated emojis drift around the Pokémon (snowflakes for Ice, etc.)
- [ ] **Sparkline + win%** — sparkline draws across the bottom of art, win% in big bold
- [ ] **Flavor strip** — LLM one-sentence explanation in italics with "AI" tag
- [ ] **Alternatives** — 4 rows max, energy orbs match move types (Water orbs for Ice, etc.), recommended row has star
- [ ] **Bottom strip** — Weakness/From/Retreat shows worst threat with sprite
- [ ] **Threats panel** — gold trim header, ON-FIELD + INCOMING sections, threat sprites load
- [ ] **PV chain** — color-coded pills, me (blue left border) vs opp (red left border), arrows between
- [ ] **Battle notes** — textarea visible, typing auto-saves to localStorage
- [ ] **Footer bar** — sims/depth meta, kbd shortcut pills

## Functional QA

- [ ] Play through 5 turns: card updates each turn, sparkline grows, trend arrow changes direction
- [ ] Open browser devtools: no console errors
- [ ] Press 'N' on a turn: per-turn note modal opens (existing behavior)
- [ ] Type in battle notes: refresh page, notes persist (localStorage)
- [ ] Switch the active Pokémon via the game: card type/sprite updates on next engine response

## Asset QA

- [ ] Energy orbs render for all types you encounter
- [ ] Colorless orb (e.g. on Extreme Speed) is the custom SVG, not a card crop
- [ ] No broken-image icons for any sprite or orb

## Edge cases

- [ ] Battle ends: dashboard handles `gameEnded` state (doesn't crash)
- [ ] Engine 429 from Groq: flavor strip shows "Analyzing turn..." fallback, no error
- [ ] Proxy disconnected: header LIVE indicator goes grey or error state shows

## Performance

- [ ] Dashboard renders within ~200ms of each turn change (no jank)
