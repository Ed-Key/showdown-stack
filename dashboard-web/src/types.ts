export type BattleResult = 'win' | 'loss' | 'unknown' | string;

export interface DashboardSummary {
  finishedBattles: number;
  wins: number;
  losses: number;
  unknownResults: number;
  winRate: number | null;
  followed: number;
  followable: number;
  followRate: number | null;
  pvHits: number;
  pvKnown: number;
  pvHitRate: number | null;
  switchRecommendations: number;
  switchRecommendationRate: number | null;
  criticalTurns: number;
  avgConfidence: number | null;
  residualEvents: number;
  hazardResidualEvents: number;
  statusResidualEvents: number;
  hazardsAdded: number;
  hazardsRemoved: number;
}

export interface BattleSummary {
  battleId: string;
  opponent: string;
  result: BattleResult;
  teamName: string | null;
  endedAtLabel: string;
  format: string;
  totalTurns: number;
  schemaVersion: number | null;
  replayUrl: string | null;
  team: string[];
  metrics: {
    followRate: number | null;
    pvHitRate: number | null;
    switchRecommendations: number;
    criticalTurns: number;
    residualEvents: number;
    hazardResidualEvents: number;
    statusResidualEvents: number;
  };
  dataIssues: string[];
}

export interface PokemonProfile {
  species: string;
  battles: number;
  winRate: number | null;
  leadRate: number | null;
  leadWinRate: number | null;
  survivalRate: number | null;
  winWhenAlive: number | null;
  avgFaintTurn: number | null;
  switchIns: number;
  actionPreventedCount: number;
  avgDamageTakenPct: number | null;
  avgDamageDealtPct: number | null;
  kos: number;
  avgKos: number | null;
  koCredit: Record<string, number>;
  koCreditTotal: number;
  koShare: number | null;
  engineDisagreements: number;
  highConfidenceDisagreements: number;
  engineWantedSwitchIntoCount: number;
  engineWantedSwitchOutCount: number;
  fieldPressure: Record<string, number>;
  avgFieldPressureTakenPct: number | null;
  fieldPressureBucket: string;
}

export interface TeamProfile {
  team: string[];
  teamName: string | null;
  battles: number;
  wins: number;
  losses: number;
  winRate: number | null;
  followRate: number | null;
  performanceBattles: number;
  hasPerformance: boolean;
  topLead: {
    species: string;
    count: number;
    rate: number | null;
    winRate: number | null;
  } | null;
  pokemon: PokemonProfile[];
}

export interface PatternPanel {
  id: string;
  title: string;
  lens?: string;
  instances: number;
  affectedBattles: number;
}

export interface DashboardArchive {
  generatedAt: string;
  sourceDir: string;
  filters: { minSchemaVersion: number | null };
  summary: DashboardSummary;
  latestRecordedBattle: BattleSummary | null;
  battles: BattleSummary[];
  teamProfiles: TeamProfile[];
  patternPanels: PatternPanel[];
}

export interface TeamCoachBrief {
  purpose: 'team_coach_brief';
  team: {
    key: string;
    name: string | null;
    roster: string[];
    battleIds: string[];
  };
  summary: {
    battles: number;
    wins: number;
    losses: number;
    performanceBattles: number;
    followRate: number | null;
    topLead: TeamProfile['topLead'];
  };
  pokemonProfiles: PokemonProfile[];
  evidenceBuckets: {
    robustIgnoredAdvice: { count: number; examples: unknown[] };
    engineUncertainty: {
      pimcSplits: { count: number; examples: unknown[] };
      pvMisses: { count: number; examples: unknown[] };
    };
    noStableLines: { count: number; examples: unknown[] };
    fieldPressure: { count: number; examples: unknown[] };
  };
  reviewPriorities: Array<{
    battleId?: string;
    opponent?: string;
    turn?: number;
    forceSwitch?: boolean;
    result?: BattleResult;
    reason?: string;
  }>;
  agentUsageNotes: string[];
}

export interface CoachPreset {
  id: string;
  label: string;
  provider: string;
  model?: string;
  effort?: string;
  toolDepth?: string;
}

export type CoachRunMode = 'fake' | 'auto' | 'real';
export type CoachFocusMode = 'team' | 'recent';

export interface CoachToolCall {
  name: string;
  args: Record<string, unknown>;
  durationMs?: number;
  outputSummary?: string;
  callId?: string;
  modelOutputBytes?: number;
}

export interface TeamCoachRun {
  runId?: string;
  battleId: string;
  mode: CoachRunMode | string;
  provider: string;
  preset?: CoachPreset;
  model?: string;
  startedAtMs?: number;
  startedAtLabel?: string;
  latencyMs?: number;
  settings?: Record<string, unknown>;
  toolCalls: CoachToolCall[];
  answer: string;
  comparisonMetrics?: {
    requiredToolsCalled?: boolean;
    toolCallCount?: number;
    teamContextFirst?: boolean;
    pokemonProfiles?: number;
    reviewPriorities?: number;
    hasModelUncertaintySeparation?: boolean;
    hallucinationGuard?: string;
  };
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
    totalTokens?: number;
    reasoningTokens?: number;
    costUsd?: number | null;
    note?: string;
  };
  responseIds?: string[];
}

export interface CoachStreamEvent {
  type: string;
  provider?: string;
  mode?: CoachRunMode | string;
  preset?: CoachPreset;
  model?: string;
  battleId?: string;
  toolRound?: number;
  toolChoice?: unknown;
  responseId?: string;
  toolCallCount?: number;
  hasText?: boolean;
  stopReason?: string;
  name?: string;
  args?: Record<string, unknown>;
  callId?: string;
  toolCall?: CoachToolCall;
  toolOutputPreview?: string;
  toolOutputBytes?: number;
  toolOutputTruncated?: boolean;
  answer?: string;
  usage?: TeamCoachRun['usage'];
  run?: TeamCoachRun;
  message?: string;
  statusCode?: number;
}
