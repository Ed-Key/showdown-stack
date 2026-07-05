"""Team-level aggregation helpers for dashboard and agent contexts."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 1)


def _round_one(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 1)


def _as_number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _sum_dict_values(target: dict[str, float], source: dict[str, Any] | None) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if isinstance(value, (int, float)):
            target[key] = target.get(key, 0.0) + float(value)


def _empty_pokemon_row(species: str) -> dict[str, Any]:
    return {
        "species": species,
        "battles": 0,
        "wins": 0,
        "losses": 0,
        "leadCount": 0,
        "leadWins": 0,
        "survivedCount": 0,
        "survivedWins": 0,
        "faintCount": 0,
        "faintTurnSum": 0.0,
        "switchIns": 0,
        "forcedSwitchIns": 0,
        "activeTurns": 0,
        "actionPreventedCount": 0,
        "directDamageTakenPct": 0.0,
        "directDamageDealtPct": 0.0,
        "hpHealedPct": 0.0,
        "kos": 0,
        "timesTargeted": 0,
        "decisionTurns": 0,
        "engineDisagreements": 0,
        "highConfidenceDisagreements": 0,
        "engineWantedSwitchIntoCount": 0,
        "engineWantedSwitchOutCount": 0,
        "fieldPressure": defaultdict(float),
        "koCredit": defaultdict(float),
    }


def _field_pressure_bucket(avg_hp_lost: float | None) -> str:
    if avg_hp_lost is None:
        return "n/a"
    if avg_hp_lost >= 30:
        return "high"
    if avg_hp_lost >= 14:
        return "medium"
    if avg_hp_lost > 0:
        return "low"
    return "none"


def _finalize_pokemon_row(row: dict[str, Any], team_ko_credit_total: float) -> dict[str, Any]:
    battles = row["battles"]
    wins = row["wins"]
    survived = row["survivedCount"]
    faint_count = row["faintCount"]
    field_pressure = dict(row["fieldPressure"])
    ko_credit = dict(row["koCredit"])
    ko_credit_total = sum(float(value) for value in ko_credit.values())
    avg_field_pressure = (
        float(field_pressure.get("totalHpLost", 0.0)) / battles
        if battles
        else None
    )

    return {
        "species": row["species"],
        "battles": battles,
        "wins": wins,
        "losses": row["losses"],
        "winRate": _round_pct(_rate(wins, wins + row["losses"])),
        "leadCount": row["leadCount"],
        "leadRate": _round_pct(_rate(row["leadCount"], battles)),
        "leadWinRate": _round_pct(_rate(row["leadWins"], row["leadCount"])),
        "survivedCount": survived,
        "survivalRate": _round_pct(_rate(survived, battles)),
        "winWhenAlive": _round_pct(_rate(row["survivedWins"], survived)),
        "avgFaintTurn": _round_one(row["faintTurnSum"] / faint_count) if faint_count else None,
        "switchIns": row["switchIns"],
        "forcedSwitchIns": row["forcedSwitchIns"],
        "activeTurns": row["activeTurns"],
        "actionPreventedCount": row["actionPreventedCount"],
        "directDamageTakenPct": _round_one(row["directDamageTakenPct"]),
        "directDamageDealtPct": _round_one(row["directDamageDealtPct"]),
        "avgDamageTakenPct": _round_one(row["directDamageTakenPct"] / battles) if battles else None,
        "avgDamageDealtPct": _round_one(row["directDamageDealtPct"] / battles) if battles else None,
        "hpHealedPct": _round_one(row["hpHealedPct"]),
        "kos": row["kos"],
        "avgKos": _round_one(row["kos"] / battles) if battles else None,
        "koCredit": {key: int(value) if float(value).is_integer() else _round_one(value) for key, value in ko_credit.items()},
        "koCreditTotal": int(ko_credit_total) if ko_credit_total.is_integer() else _round_one(ko_credit_total),
        "koShare": _round_pct(_rate(int(ko_credit_total), int(team_ko_credit_total))) if team_ko_credit_total else None,
        "timesTargeted": row["timesTargeted"],
        "decisionTurns": row["decisionTurns"],
        "engineDisagreements": row["engineDisagreements"],
        "highConfidenceDisagreements": row["highConfidenceDisagreements"],
        "engineWantedSwitchIntoCount": row["engineWantedSwitchIntoCount"],
        "engineWantedSwitchOutCount": row["engineWantedSwitchOutCount"],
        "fieldPressure": {key: _round_one(value) for key, value in field_pressure.items()},
        "avgFieldPressureTakenPct": _round_one(avg_field_pressure),
        "fieldPressureBucket": _field_pressure_bucket(avg_field_pressure),
    }


def build_team_profiles(
    battles: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    team_keys: Counter[str] = Counter()
    team_results: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "battles": 0,
        "wins": 0,
        "losses": 0,
        "followed": 0,
        "followable": 0,
        "performanceBattles": 0,
        "teamNames": Counter(),
        "leads": Counter(),
        "leadWins": Counter(),
        "pokemon": {},
    })

    for battle in battles:
        key = " / ".join(battle.get("team") or ["unknown"])
        team_keys[key] += 1
        row = team_results[key]
        metrics = battle.get("metrics") or {}
        row["battles"] += 1
        row["wins"] += 1 if battle.get("result") == "win" else 0
        row["losses"] += 1 if battle.get("result") == "loss" else 0
        row["followed"] += metrics.get("followed") or 0
        row["followable"] += metrics.get("followable") or 0
        team_name = battle.get("teamName")
        if isinstance(team_name, str) and team_name.strip():
            row["teamNames"][team_name.strip()] += 1
        team_performance = battle.get("teamPerformance")
        mine = team_performance.get("mine") if isinstance(team_performance, dict) else None
        pokemon = mine.get("pokemon") if isinstance(mine, dict) else None
        if not isinstance(pokemon, dict) or not pokemon:
            continue

        row["performanceBattles"] += 1
        lead = mine.get("lead")
        if isinstance(lead, str) and lead:
            row["leads"][lead] += 1
            if battle.get("result") == "win":
                row["leadWins"][lead] += 1

        for species, raw_stats in pokemon.items():
            if not isinstance(raw_stats, dict):
                continue
            species_name = str(raw_stats.get("species") or species)
            mon = row["pokemon"].setdefault(species_name, _empty_pokemon_row(species_name))
            mon["battles"] += 1
            mon["wins"] += 1 if battle.get("result") == "win" else 0
            mon["losses"] += 1 if battle.get("result") == "loss" else 0
            if raw_stats.get("led"):
                mon["leadCount"] += 1
                if battle.get("result") == "win":
                    mon["leadWins"] += 1
            if raw_stats.get("survived"):
                mon["survivedCount"] += 1
                if battle.get("result") == "win":
                    mon["survivedWins"] += 1
            if raw_stats.get("fainted"):
                mon["faintCount"] += 1
                mon["faintTurnSum"] += _as_number(raw_stats.get("faintTurn"))
            for field in (
                "switchIns",
                "forcedSwitchIns",
                "activeTurns",
                "actionPreventedCount",
                "kos",
                "timesTargeted",
                "decisionTurns",
                "engineDisagreements",
                "highConfidenceDisagreements",
                "engineWantedSwitchIntoCount",
                "engineWantedSwitchOutCount",
            ):
                mon[field] += int(_as_number(raw_stats.get(field)))
            for field in (
                "directDamageTakenPct",
                "directDamageDealtPct",
                "hpHealedPct",
            ):
                mon[field] += _as_number(raw_stats.get(field))
            _sum_dict_values(mon["fieldPressure"], raw_stats.get("fieldPressure"))
            _sum_dict_values(mon["koCredit"], raw_stats.get("koCredit"))

    profiles = []
    for key, count in team_keys.most_common(limit):
        row = team_results[key]
        team_ko_credit_total = sum(
            sum(float(value) for value in mon["koCredit"].values())
            for mon in row["pokemon"].values()
        )
        pokemon_rows = [
            _finalize_pokemon_row(mon, team_ko_credit_total)
            for mon in row["pokemon"].values()
        ]
        roster_order = {species: idx for idx, species in enumerate(key.split(" / "))}
        pokemon_rows.sort(key=lambda mon: roster_order.get(mon["species"], 999))
        lead_rows = []
        for species, lead_count in row["leads"].most_common():
            lead_rows.append({
                "species": species,
                "count": lead_count,
                "rate": _round_pct(_rate(lead_count, row["performanceBattles"])),
                "winRate": _round_pct(_rate(row["leadWins"][species], lead_count)),
            })
        profiles.append({
            "team": key.split(" / "),
            "teamName": row["teamNames"].most_common(1)[0][0] if row["teamNames"] else None,
            "battles": count,
            "wins": row["wins"],
            "losses": row["losses"],
            "winRate": _round_pct(_rate(row["wins"], row["wins"] + row["losses"])),
            "followRate": _round_pct(_rate(row["followed"], row["followable"])),
            "performanceBattles": row["performanceBattles"],
            "hasPerformance": row["performanceBattles"] > 0,
            "leads": lead_rows,
            "topLead": lead_rows[0] if lead_rows else None,
            "pokemon": pokemon_rows,
        })
    return profiles
