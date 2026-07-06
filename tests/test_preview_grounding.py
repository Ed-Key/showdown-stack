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
