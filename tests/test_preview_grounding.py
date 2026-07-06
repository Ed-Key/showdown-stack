import showdown_copilot.preview_grounding as pg
from showdown_copilot.preview_grounding import build_opponent_likely_sets, build_speed_context


def test_likely_sets_skips_species_without_data(monkeypatch):
    class FakePriors:
        def usage_summary(self, species, fmt):
            if species == "Gliscor":
                return {"topMoves": [{"name": "toxic", "pct": 78}], "topItems": [],
                        "topAbilities": [{"name": "poisonheal", "pct": 97}], "topTera": [], "scarfPct": 0}
            return None

    monkeypatch.setattr(pg, "_priors_source", lambda: FakePriors())
    rows = build_opponent_likely_sets(["Gliscor", "Missingno"], "gen9nationaldex")
    assert len(rows) == 1
    assert rows[0]["species"] == "Gliscor"
    assert rows[0]["basis"] == "usage-statistics"


def test_likely_sets_empty_when_priors_unavailable(monkeypatch):
    monkeypatch.setattr(pg, "_priors_source", lambda: None)
    assert build_opponent_likely_sets(["Gliscor"], "gen9nationaldex") == []


def test_speed_context_orders_and_flags_scarf():
    ctx = build_speed_context(
        ["Garchomp"],                      # base spe 102
        ["Kingdra"],                       # base spe 85
        likely_sets=[{"species": "Kingdra", "scarfPct": 30}],
    )
    order = [(row["species"], row["side"]) for row in ctx["baseSpeedOrder"]]
    assert order == [("Garchomp", "mine"), ("Kingdra", "opp")]
    assert ctx["scarfPlausible"] == ["Kingdra"]


def test_speed_context_empty_for_unknown_species():
    assert build_speed_context(["Notarealmon"], ["Fakemon"]) == {}


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
