# Mega + Hidden-Forme Awareness and Lead-Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed hidden opponent-forme facts (Mega evolutions + team-preview wildcards like Urshifu-\*) into the preview-plan grounding, prompt-discipline the planner to check lead safety against the fastest plausible forme, and make the verifier forme-aware so correct forme claims aren't false-flagged.

**Architecture:** Deterministic gen-9 dex lookups (`get_hidden_formes`) produce forme facts; `preview_grounding` assembles them into a `possibleFormes` block and adds Mega speed rows to `speedContext`; `preview_plan` wires them into the prompt with lead-safety discipline; `preview_verifier` resolves forme names (with longest-match-wins) so a correct "Diancie-Mega is Rock/Fairy" claim isn't checked against the base forme. All grounding degrades to empty and never blocks a plan.

**Tech Stack:** Python 3, uv-managed; `poke_env.data.GenData` gen-9 pokedex; pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-06-mega-forme-awareness-lead-safety-design.md`

## Global Constraints

- Repo: `/Users/edkiboma/Projects/pokemon-ai/showdown-stack` (parent `pokemon-ai` is NOT a git repo). Branch `feat/matchup-plan-v2`. Run git as `git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack …` or cd there first. Python: `uv run pytest …` from the repo root; never `pip install`.
- Baseline suite is green at branch start: **pytest 418 passed / 8 skipped**. Each task's full-suite step must stay green (counts grow as tests are added).
- **Mega detection rule (exact):** an `otherFormes` entry is a Mega iff its dex entry's `forme` string **startswith "Mega"** (catches "Mega", "Mega-X", "Mega-Y"). Do NOT match on the literal `== "Mega"` (Charizard's are "Mega-X"/"Mega-Y").
- **Battle-forme rule (exact):** non-Mega `otherFormes` are enumerated **only when the input species string is a team-preview wildcard** (ends with `*`, e.g. "Urshifu-\*"), and the forme name does **not** contain "Tera". This prevents over-enumerating Ogerpon's Tera/regional formes (Ogerpon is never a wildcard at preview).
- All new grounding functions **degrade to empty** (`[]` / `{}`) and never raise — wrap dex lookups in try/except and skip on failure, matching `build_opponent_likely_sets` in `preview_grounding.py`.
- Grounding gate: forme grounding is added to the prompt only when `os.environ.get("SHOWDOWN_PREVIEW_DISABLE_GROUNDING") != "1"`, same as `opponentLikelySets`/`speedContext`.
- Commit after every task with the message in its final step. Do not push.

---

### Task 1: `get_hidden_formes` — pure dex forme enumeration

**Files:**
- Modify: `src/showdown_copilot/mechanics_facts.py` (add function after `get_pokemon_facts`, ~line 76)
- Test: `tests/test_mechanics_facts.py` (append)

**Interfaces:**
- Consumes: `_gen9()`, `resolve_species_id`, `get_pokemon_facts` (existing in the same module).
- Produces (used by Tasks 2 and 4):
  - `get_hidden_formes(species: str) -> list[dict]` — each dict: `{"name": str, "formeKind": "Mega"|"Battle", "basis": "mega-evolution"|"team-preview-forme", "types": list[str], "abilities": list[str], "spe": int, "atk": int, "spa": int, "triggerItem": str|None}`. Empty list for a forme-less or unknown species.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mechanics_facts.py`:

```python
from showdown_copilot.mechanics_facts import get_hidden_formes


def test_get_hidden_formes_diancie_mega():
    formes = get_hidden_formes("Diancie")
    assert len(formes) == 1
    m = formes[0]
    assert m["name"] == "Diancie-Mega"
    assert m["formeKind"] == "Mega"
    assert m["basis"] == "mega-evolution"
    assert m["types"] == ["Rock", "Fairy"]
    assert m["abilities"] == ["Magic Bounce"]
    assert m["spe"] == 110
    assert m["triggerItem"] == "Diancite"


def test_get_hidden_formes_charizard_two_megas():
    names = {f["name"] for f in get_hidden_formes("Charizard")}
    assert names == {"Charizard-Mega-X", "Charizard-Mega-Y"}
    by_name = {f["name"]: f for f in get_hidden_formes("Charizard")}
    assert by_name["Charizard-Mega-X"]["types"] == ["Fire", "Dragon"]
    assert by_name["Charizard-Mega-Y"]["types"] == ["Fire", "Flying"]
    assert all(f["formeKind"] == "Mega" for f in get_hidden_formes("Charizard"))


def test_get_hidden_formes_urshifu_wildcard_battle_forme():
    formes = get_hidden_formes("Urshifu-*")
    names = {f["name"] for f in formes}
    assert "Urshifu-Rapid-Strike" in names
    rapid = next(f for f in formes if f["name"] == "Urshifu-Rapid-Strike")
    assert rapid["formeKind"] == "Battle"
    assert rapid["basis"] == "team-preview-forme"
    assert rapid["types"] == ["Fighting", "Water"]
    assert rapid["triggerItem"] is None


def test_get_hidden_formes_none_for_plain_species():
    # Kingambit is genuinely forme-less (no Mega, no battle forme). NOTE: do NOT
    # use Garchomp here — Mega Garchomp exists in the NatDex dex.
    assert get_hidden_formes("Kingambit") == []


def test_get_hidden_formes_no_battle_formes_without_wildcard():
    # Ogerpon-Wellspring is a concrete preview species (not a wildcard) and has
    # no Mega; its Tera/other formes must NOT be enumerated.
    assert get_hidden_formes("Ogerpon-Wellspring") == []


def test_get_hidden_formes_unknown_species():
    assert get_hidden_formes("Notarealmon") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mechanics_facts.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_hidden_formes'`.

- [ ] **Step 3: Implement**

In `src/showdown_copilot/mechanics_facts.py`, add after `get_pokemon_facts` (after ~line 76):

```python
def get_hidden_formes(species: str) -> list[dict[str, Any]]:
    """Preview-relevant alternate formes of a species: Mega evolutions (always)
    and hidden battle formes reachable behind a team-preview wildcard (e.g.
    "Urshifu-*" -> Urshifu-Rapid-Strike). Pure gen-9 dex lookup; returns [] for
    a forme-less or unknown species.
    """
    raw = str(species or "").strip()
    is_wildcard = raw.endswith("*")
    base_id = resolve_species_id(raw)
    if not base_id:
        return []
    base_entry = _gen9().pokedex.get(base_id) or {}
    formes: list[dict[str, Any]] = []
    for forme_name in base_entry.get("otherFormes") or []:
        forme_id = resolve_species_id(str(forme_name))
        if not forme_id:
            continue
        entry = _gen9().pokedex.get(forme_id) or {}
        forme_tag = str(entry.get("forme") or "")
        is_mega = forme_tag.startswith("Mega")
        if is_mega:
            forme_kind, basis = "Mega", "mega-evolution"
        elif is_wildcard and "Tera" not in str(forme_name):
            forme_kind, basis = "Battle", "team-preview-forme"
        else:
            continue
        stats = entry.get("baseStats") if isinstance(entry.get("baseStats"), dict) else {}
        abilities = entry.get("abilities") if isinstance(entry.get("abilities"), dict) else {}
        formes.append({
            "name": entry.get("name") or str(forme_name),
            "formeKind": forme_kind,
            "basis": basis,
            "types": [str(t) for t in (entry.get("types") or [])],
            "abilities": [str(a) for a in abilities.values()],
            "spe": int(stats.get("spe") or 0),
            "atk": int(stats.get("atk") or 0),
            "spa": int(stats.get("spa") or 0),
            "triggerItem": entry.get("requiredItem"),
        })
    return formes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mechanics_facts.py -q`
Expected: PASS (all new tests green).

- [ ] **Step 5: Commit**

```bash
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack add src/showdown_copilot/mechanics_facts.py tests/test_mechanics_facts.py
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack commit -m "feat(mechanics): get_hidden_formes — Mega + wildcard battle-forme dex lookup"
```

---

### Task 2: `preview_grounding` forme enrichment (possibleFormes + Mega speed rows)

**Files:**
- Modify: `src/showdown_copilot/preview_grounding.py` (add `build_possible_formes`; extend `build_speed_context`)
- Test: `tests/test_preview_grounding.py` (append)

**Interfaces:**
- Consumes (Task 1): `get_hidden_formes(species) -> list[dict]` (import from `.mechanics_facts`).
- Produces (used by Task 3):
  - `build_possible_formes(opponent_team: list[str]) -> list[dict]` — per species with hidden formes: `{"species": str, "baseTypes": list[str], "baseSpeed": int, "formes": [<get_hidden_formes entries>]}`; `[]` when none.
  - `build_speed_context(...)` unchanged signature; opponent speed rows now include one extra row per hidden forme: `{"species": <forme name>, "side": "opp", "baseSpeed": <forme spe>, "forme": <formeKind>, "guaranteed": False}`. Base rows keep their exact current shape (no new keys).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preview_grounding.py`:

```python
from showdown_copilot.preview_grounding import build_possible_formes


def test_build_possible_formes_surfaces_mega_and_wildcard():
    rows = build_possible_formes(["Diancie", "Urshifu-*", "Kingambit"])
    by_species = {r["species"]: r for r in rows}
    assert "Diancie" in by_species and "Urshifu-*" in by_species
    assert "Kingambit" not in by_species  # genuinely forme-less (Mega Garchomp exists — don't use Garchomp)
    diancie_formes = {f["name"] for f in by_species["Diancie"]["formes"]}
    assert diancie_formes == {"Diancie-Mega"}
    assert by_species["Diancie"]["baseSpeed"] == 50


def test_build_possible_formes_empty_for_forme_less_team():
    assert build_possible_formes(["Kingambit", "Great Tusk"]) == []


def test_speed_context_includes_mega_row_above_base():
    ctx = build_speed_context(["Garchomp"], ["Diancie"])
    order = ctx["baseSpeedOrder"]
    mega = next((r for r in order if r["species"] == "Diancie-Mega"), None)
    assert mega is not None
    assert mega["baseSpeed"] == 110
    assert mega["forme"] == "Mega"
    assert mega["guaranteed"] is False
    # Mega Diancie (110) must sort above Garchomp (102) — the signal that was missing.
    names = [r["species"] for r in order]
    assert names.index("Diancie-Mega") < names.index("Garchomp")
    # base Diancie row still present and unchanged (no 'forme' key)
    base = next(r for r in order if r["species"] == "Diancie")
    assert "forme" not in base
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_preview_grounding.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_possible_formes'`, and `test_speed_context_includes_mega_row_above_base` fails (no Mega row).

- [ ] **Step 3: Implement**

In `src/showdown_copilot/preview_grounding.py`, add `get_hidden_formes` to the existing mechanics_facts import:

```python
from .mechanics_facts import get_hidden_formes, get_pokemon_facts
```

Add `build_possible_formes` after `build_opponent_likely_sets`:

```python
def build_possible_formes(opponent_team: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for species in opponent_team:
        if not species:
            continue
        try:
            formes = get_hidden_formes(species)
        except Exception:  # noqa: BLE001 - a dex hiccup skips the species only
            logger.warning("preview grounding: get_hidden_formes failed for %s", species, exc_info=True)
            continue
        if not formes:
            continue
        base = get_pokemon_facts(species)
        rows.append({
            "species": species,
            "baseTypes": base.get("types") or [],
            "baseSpeed": int((base.get("baseStats") or {}).get("spe") or 0),
            "formes": formes,
        })
    return rows
```

Extend `build_speed_context`: inside the `for side, names in (("mine", my_species), ("opp", opponent_species))` loop, after appending the base row, add hidden-forme rows for the opponent side only:

```python
    for side, names in (("mine", my_species), ("opp", opponent_species)):
        for name in names:
            facts = get_pokemon_facts(name)
            if not facts.get("found"):
                continue
            base_speed = int((facts.get("baseStats") or {}).get("spe") or 0)
            rows.append({"species": str(facts.get("name") or name), "side": side, "baseSpeed": base_speed})
            if side == "opp":
                try:
                    hidden = get_hidden_formes(name)
                except Exception:  # noqa: BLE001 - never block speed context on a forme lookup
                    hidden = []
                for forme in hidden:
                    rows.append({
                        "species": forme["name"],
                        "side": "opp",
                        "baseSpeed": int(forme.get("spe") or 0),
                        "forme": forme.get("formeKind"),
                        "guaranteed": False,
                    })
```

Leave the rest of `build_speed_context` (the `rows.sort(...)`, `scarfPlausible`) unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_preview_grounding.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack add src/showdown_copilot/preview_grounding.py tests/test_preview_grounding.py
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack commit -m "feat(preview): build_possible_formes + Mega speed rows in speed context"
```

---

### Task 3: Wire `possibleFormes` + lead-safety discipline into the prompt

**Files:**
- Modify: `src/showdown_copilot/preview_plan.py` (`_preview_user_prompt` ~lines 386-394; `PREVIEW_SYSTEM_PROMPT` ~line 351-355)
- Test: `tests/test_preview_plan.py` (append)

**Interfaces:**
- Consumes (Task 2): `build_possible_formes(opponent_team) -> list[dict]` (import from `.preview_grounding`).
- Produces: `_preview_user_prompt` payload gains `possibleFormes` key when non-empty and grounding enabled; `PREVIEW_SYSTEM_PROMPT` gains a forme/lead-safety discipline block.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preview_plan.py`:

```python
def test_prompt_includes_possible_formes(monkeypatch):
    monkeypatch.delenv("SHOWDOWN_PREVIEW_DISABLE_GROUNDING", raising=False)
    req = PreviewPlanRequest(
        battleId="b-formes", format="gen9nationaldex", myTeam=default_team(),
        opponentTeam=["Diancie", "Garchomp"], runMode="fake",
    )
    payload = json.loads(_preview_user_prompt(req))
    assert "possibleFormes" in payload
    species = {row["species"] for row in payload["possibleFormes"]}
    assert "Diancie" in species
    formes = {f["name"] for row in payload["possibleFormes"] for f in row["formes"]}
    assert "Diancie-Mega" in formes


def test_prompt_omits_possible_formes_when_grounding_disabled(monkeypatch):
    monkeypatch.setenv("SHOWDOWN_PREVIEW_DISABLE_GROUNDING", "1")
    req = PreviewPlanRequest(
        battleId="b-formes-off", format="gen9nationaldex", myTeam=default_team(),
        opponentTeam=["Diancie"], runMode="fake",
    )
    payload = json.loads(_preview_user_prompt(req))
    assert "possibleFormes" not in payload
    monkeypatch.delenv("SHOWDOWN_PREVIEW_DISABLE_GROUNDING")


def test_system_prompt_has_lead_safety_discipline():
    from showdown_copilot.preview_plan import PREVIEW_SYSTEM_PROMPT
    lowered = PREVIEW_SYSTEM_PROMPT.lower()
    assert "possibleformes" in lowered
    assert "lead" in lowered and "outsped" in lowered
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_preview_plan.py -q -k "possible_formes or lead_safety"`
Expected: FAIL — `possibleFormes` not in payload; discipline strings absent.

- [ ] **Step 3: Implement**

Add the import near the other preview_grounding import in `preview_plan.py`:

```python
from .preview_grounding import build_opponent_likely_sets, build_possible_formes, build_speed_context
```

(If the existing line is `from .preview_grounding import build_opponent_likely_sets, build_speed_context`, replace it with the above.)

In `_preview_user_prompt`, inside the `if os.environ.get("SHOWDOWN_PREVIEW_DISABLE_GROUNDING") != "1":` block, after the `speedContext` assignment (after ~line 394), add:

```python
        possible_formes = build_possible_formes(req.opponentTeam)
        if possible_formes:
            payload["possibleFormes"] = possible_formes
```

Append to `PREVIEW_SYSTEM_PROMPT`, just before the closing `"""` (after the "Grounding discipline" block at ~line 354):

```
Forme & lead safety:
- Opponents may Mega-evolve or reveal a hidden battle forme — see possibleFormes and the forme rows (guaranteed=false) in speedContext. Treat these as possibilities, not confirmed facts.
- A forme can change speed and typing; a Mega often outspeeds and outguns its base forme.
- Before committing recommendedLead, verify it is not outsped-and-threatened by the fastest plausible opposing forme, Megas included. If your recommended lead is outsped or cleanly OHKO'd by a plausible forme, do not hide it — state the risk and prefer a safer lead.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_preview_plan.py -q -k "possible_formes or lead_safety"` then `uv run pytest tests/test_preview_plan.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack add src/showdown_copilot/preview_plan.py tests/test_preview_plan.py
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack commit -m "feat(preview): possibleFormes in prompt + lead-safety discipline"
```

---

### Task 4: Verifier forme-awareness (register forme names + longest-match-wins)

**Files:**
- Modify: `src/showdown_copilot/preview_verifier.py` (`_known_species_names` ~lines 100-119; `_type_multiplier_issues` ~lines 171-259; add a `_species_applies` helper and a `_hidden_forme_names` helper; import `get_hidden_formes`)
- Test: `tests/test_preview_verifier.py` (create) — the module currently has no dedicated test file; verification lives in `tests/test_preview_plan.py` / sanitize tests. A focused file is warranted here.

**Interfaces:**
- Consumes (Task 1): `get_hidden_formes` from `.mechanics_facts`.
- Produces: `verify_preview_plan` no longer false-flags a correct forme-typing claim; base-forme checks unchanged. No signature changes.

**Why longest-match-wins:** a forme name normalizes to a superstring of its base ("charizardmegax" contains "charizard"), so a "Charizard-Mega-X is Fire/Dragon" mention would match BOTH the Mega (correct) and base Charizard (Fire/Flying → false flag). We suppress a species name in a text when a longer registered name that contains it also matches that text.

- [ ] **Step 1: Write the failing test**

Create `tests/test_preview_verifier.py`:

```python
from showdown_copilot.preview_plan import (
    LeadOption,
    MatchupPlan,
    ThreatItem,
)
from showdown_copilot.preview_verifier import verify_preview_plan


def _plan_with_threat_reason(reason: str, threat_species: str = "Charizard") -> MatchupPlan:
    return MatchupPlan(
        archetype="offense", confidence="medium", summary="s.", winPath="w.",
        recommendedLead=LeadOption(pokemon="Garchomp", rating="safe", reason="lead."),
        backupLeads=[], avoidLeads=[], leadRules=[], preserveTargets=[],
        mainThreats=[ThreatItem(pokemon=threat_species, reason=reason, priority="high")],
        dangerRules=[], earlyPriorities=[], uncertainties=[],
    )


def test_mega_x_typing_claim_not_flagged():
    # Charizard-Mega-X is Fire/Dragon; Rock is 2x. Correct — must NOT be flagged
    # even though base Charizard (Fire/Flying) would make Rock 4x.
    plan = _plan_with_threat_reason("Stealth Rock is 2x into Charizard-Mega-X.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert issues == []


def test_wrong_mega_typing_claim_still_flagged():
    # Charizard-Mega-X is Fire/Dragon; Rock is 2x, not 4x. Wrong — must be flagged.
    plan = _plan_with_threat_reason("Stealth Rock is 4x into Charizard-Mega-X.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert any("multiplier" in i.id or "type" in i.id for i in issues)


def test_base_forme_claim_unchanged():
    # Base Charizard is Fire/Flying; Rock is 4x. Correct base claim, no flag.
    plan = _plan_with_threat_reason("Stealth Rock is 4x into Charizard.")
    issues = verify_preview_plan(plan, ["Charizard"])
    assert issues == []
    # And a wrong base claim is still flagged.
    bad = _plan_with_threat_reason("Stealth Rock is 2x into Charizard.")
    assert verify_preview_plan(bad, ["Charizard"]) != []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_preview_verifier.py -q`
Expected: FAIL — `test_mega_x_typing_claim_not_flagged` fails (base Charizard Fire/Flying makes Rock 4x, so the 2x claim gets flagged against the base).

- [ ] **Step 3: Implement**

In `src/showdown_copilot/preview_verifier.py`, add the import:

```python
from .mechanics_facts import get_hidden_formes, get_pokemon_facts, normalize_key, type_multiplier
```

Add two helpers above `_known_species_names`:

```python
def _hidden_forme_names(known_species: list[str]) -> list[str]:
    names: list[str] = []
    for species in known_species:
        try:
            for forme in get_hidden_formes(species):
                name = str(forme.get("name") or "")
                if name:
                    names.append(name)
        except Exception:  # noqa: BLE001 - forme lookup must never break verification
            continue
    return names


def _species_applies(species: str, text: str, all_names: list[str]) -> bool:
    """True if `species` matches `text` and is the most specific match — i.e. no
    longer registered name that contains it also matches this text. Prevents a
    base name ('Charizard') from matching inside a forme mention ('Charizard-Mega-X')."""
    sn = normalize_key(species)
    nt = normalize_key(text)
    if not sn or sn not in nt:
        return False
    for other in all_names:
        on = normalize_key(other)
        if len(on) > len(sn) and sn in on and on in nt:
            return False
    return True
```

Extend `_known_species_names` to register forme names. Replace its body's second loop registration so forme names present in plan text are included. Change the return so forme names are candidates — insert, before `return sorted(...)`:

```python
    for forme_name in _hidden_forme_names(known_species):
        for _path, text in iter_plan_strings(plan_dict):
            if normalize_key(forme_name) and normalize_key(forme_name) in normalize_key(text):
                names[normalize_key(forme_name)] = forme_name
                break
    return sorted(names.values(), key=len, reverse=True)
```

In `_type_multiplier_issues`, replace the two membership checks that currently read
`if not facts.get("found") or normalize_key(species) not in normalize_key(text):`
(there are two such guards — one in the multiplier-token branch ~line 184, one in the relation branch ~line 219) with the specificity-aware check. For each, change to:

```python
                facts = get_pokemon_facts(species)
                if not facts.get("found") or not _species_applies(species, text, species_names):
                    continue
```

(The relation branch uses `clause`/`text` — pass whichever variable that branch already iterates as the `text` argument. In the relation branch at ~line 219, the outer `text` is in scope; use `_species_applies(species, text, species_names)`.)

Note `get_pokemon_facts(forme_name)` already resolves forme typing (e.g. `get_pokemon_facts("Charizard-Mega-X")` → Fire/Dragon), so no additional resolution is needed — registering the forme name and preferring the most-specific match is sufficient.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_preview_verifier.py -q`
Expected: PASS. Then `uv run pytest tests/test_preview_plan.py tests/test_preview_sanitize.py -q` to confirm no regression in the verifier's existing consumers.

- [ ] **Step 5: Commit**

```bash
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack add src/showdown_copilot/preview_verifier.py tests/test_preview_verifier.py
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack commit -m "feat(preview): verifier forme-awareness (register forme names, longest-match-wins)"
```

---

### Task 5: Full sweep + live regeneration check

**Files:** none (verification only), plus a doc note.

- [ ] **Step 1: Full automated sweep**

Run: `uv run pytest -q`
Expected: green (418 baseline + all tests added in Tasks 1-4; report the count).

- [ ] **Step 2: Restart the proxy on the new code and live-regen the QAQyyy plan**

The proxy runs the branch code from a screen session; restart it so it picks up Tasks 1-4:

```bash
cd /Users/edkiboma/Projects/pokemon-ai/showdown-stack
screen -S sc-demo-proxy -X quit 2>/dev/null; sleep 1; lsof -ti tcp:7271 | xargs kill 2>/dev/null; sleep 1
screen -dmS sc-demo-proxy bash -lc "cd '$PWD' && set -a && [[ -f .env ]] && source .env; set +a && export POKE_PROXY_PIMC_K=4 && exec .venv/bin/python -m showdown_copilot.proxy >> /tmp/showdown-copilot-demo/proxy.log 2>&1"
for i in $(seq 1 25); do curl -sS -m 2 http://127.0.0.1:7271/healthz >/dev/null 2>&1 && break; sleep 1; done
```

Then regenerate the plan for the exact losing battle (with `.env` sourced for the API key):

```bash
set -a; source .env; set +a
uv run python scripts/evaluate-preview-plans.py \
  --battle /Users/edkiboma/Projects/pokemon-ai/workspace/analysis/battle-postmortems/2026-07-06-1153-qaqyyy-natdex-44910647.json \
  --run-mode real --preset anthropic-haiku-45-balanced --grounding on
```

Expected: `source: model`; the plan either does **not** lead Garchomp, or explicitly flags that Garchomp is outsped/threatened by Mega Diancie. (Latency ~30s Haiku.)

- [ ] **Step 3: Record the outcome and tag**

Append the observed plan behavior (did it avoid/flag the Garchomp-into-Mega-Diancie trap?) to `docs/superpowers/specs/2026-07-06-mega-forme-awareness-lead-safety-design.md` under a new `## Live validation` section, commit it, then tag:

```bash
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack add docs/superpowers/specs/2026-07-06-mega-forme-awareness-lead-safety-design.md
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack commit -m "docs: live validation of Mega-forme awareness on the QAQyyy battle"
git -C /Users/edkiboma/Projects/pokemon-ai/showdown-stack tag mega-forme-awareness-v1
```
