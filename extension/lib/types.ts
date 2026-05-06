// Shared type shapes for translate.ts and downstream modules.
// These describe the engine payload as seen by the proxy / poke-engine.

export type PokemonSnapshot = {
  species: string;
  level: number;
  types: string[];
  hp: number;
  maxhp: number;
  ability: string;
  item: string;
  attack: number;
  defense: number;
  specialAttack: number;
  specialDefense: number;
  speed: number;
  status: string;
  moves: { id: string; pp: number; disabled?: boolean }[];
  terastallized: boolean;
  teraType: string;
};

export type SidePayload = {
  pokemon: PokemonSnapshot[];
  active: number;
  sideConditions: Record<string, number>;
  boosts: { atk: number; def: number; spa: number; spd: number; spe: number; accuracy: number; evasion: number };
};

export type EnginePayload = {
  sideOne: SidePayload;
  sideTwo: SidePayload;
  weather: string;
  terrain: string;
  trickRoom: boolean;
  _planH?: PlanHMeta;
};

export type PlanHMeta = {
  battleId: string;
  format: string;
  oppRevealedMoves: Record<string, string[]>;
  // … plus Phase 2 speed-inference fields, copy from current buildPlanHMeta
};

export type DexLike = {
  species: { get(name: string): { types: string[]; baseStats: any; abilities: { 0: string } } };
};
