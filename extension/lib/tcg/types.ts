/**
   * Maps Pokémon Showdown type names (18 types from the video games)
   * to TCG energy type names (10 types in the trading card game).
   *
   * The TCG consolidates some video-game types — notably Ice → Water,
   * Ground/Rock → Fighting, Bug → Grass, and Normal/Flying/Dragon → Colorless.
   */

export const TCG_TYPE_MAP: Record<string, string> = {
    Normal:   'colorless',
    Fire:     'fire',
    Water:    'water',
    Electric: 'lightning',
    Grass:    'grass',
    Ice:      'water',
    Fighting: 'fighting',
    Poison:   'darkness',
    Ground:   'fighting',
    Flying:   'colorless',
    Psychic:  'psychic',
    Bug:      'grass',
    Rock:     'fighting',
    Ghost:    'psychic',
    Dragon:   'colorless',
    Dark:     'darkness',
    Steel:    'metal',
    Fairy:    'fairy',
};

export type TcgType =
    | 'colorless' | 'fire' | 'water' | 'lightning' | 'grass'
    | 'fighting' | 'psychic' | 'darkness' | 'metal' | 'fairy';

