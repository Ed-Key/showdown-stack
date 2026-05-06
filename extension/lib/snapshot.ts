// Pure state-snapshot helpers extracted from content.ts. Used by the
// post-mortem recorder and any consumer that wants a JSON-friendly view
// of the live Showdown battle object without poking at it directly.

export function snapshotSide(s: any) {
  const active = s?.active?.[0];
  const mvTrack = (active?.moveTrack || []).map((m: [string, number]) => m[0]);
  return {
    activeSpecies: active?.species?.name || active?.speciesForme || null,
    activeHp: active?.hp ?? null,
    activeMaxhp: active?.maxhp ?? null,
    activeHpPct: active?.maxhp
      ? Math.round(((active.hp || 0) / active.maxhp) * 100)
      : null,
    status: active?.status || null,
    item: active?.item || null,
    ability: active?.ability || active?.baseAbility || null,
    boosts: active?.boosts || {},
    revealedMoves: mvTrack,
    team: (s?.pokemon || []).map((p: any) => ({
      species: p.species?.name || p.speciesForme || p.species,
      fainted: !!p.fainted,
      hpPct: p.maxhp ? Math.round(((p.hp || 0) / p.maxhp) * 100) : null,
      status: p.status || null,
    })),
    sideConditions: s?.sideConditions || {},
  };
}

export function snapshotState(b: any) {
  return {
    turn: b.turn,
    weather: b.weather || 'none',
    pseudoWeather: (b.pseudoWeather || []).map((pw: any) => pw[0]),
    myActive: b.mySide?.active?.[0]?.species?.name
      || b.mySide?.active?.[0]?.speciesForme || null,
    oppActive: b.farSide?.active?.[0]?.species?.name
      || b.farSide?.active?.[0]?.speciesForme || null,
    my: snapshotSide(b.mySide),
    opp: snapshotSide(b.farSide),
  };
}
