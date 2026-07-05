export type PlanConfidence = 'low' | 'medium' | 'high';
export type PlanFitRating = 'good' | 'risky' | 'violates_plan' | 'uncertain';

export interface PreviewPokemon {
  species: string;
  item?: string | null;
  ability?: string | null;
  moves: string[];
  teraType?: string | null;
}

export interface LeadOption {
  pokemon: string;
  rating: 'safe' | 'situational' | 'risky' | 'avoid';
  reason: string;
}

export interface LeadRule {
  ifOpponentLead: string;
  prefer: string[];
  avoid: string[];
  reason: string;
}

export interface PreserveTarget {
  pokemon: string;
  reason: string;
  priority: PlanConfidence;
}

export interface ThreatItem {
  pokemon: string;
  reason: string;
  priority: PlanConfidence;
}

export interface DangerRule {
  id: string;
  rule: string;
  trigger: Record<string, any>;
  severity: PlanConfidence;
}

export interface MatchupPlan {
  archetype: string;
  confidence: PlanConfidence;
  summary: string;
  winPath: string;
  recommendedLead: LeadOption;
  backupLeads: LeadOption[];
  avoidLeads: LeadOption[];
  leadRules: LeadRule[];
  preserveTargets: PreserveTarget[];
  mainThreats: ThreatItem[];
  dangerRules: DangerRule[];
  earlyPriorities: string[];
  uncertainties: string[];
}

export interface PreviewPlanRequest {
  battleId: string;
  format: string;
  myTeam: PreviewPokemon[];
  opponentTeam: string[];
  teamStats?: Record<string, any>;
  presetId?: string;
  runMode?: 'fake' | 'auto' | 'real';
}

export interface PreviewPlanResponse {
  battleId: string;
  format: string;
  provider: string;
  mode: 'fake' | 'auto' | 'real';
  source: 'model' | 'fallback';
  model?: string | null;
  latencyMs: number;
  usage?: Record<string, any>;
  plan: MatchupPlan;
  rawText?: string | null;
  fallbackReason?: string | null;
}

export interface PlanFitResult {
  rating: PlanFitRating;
  reason: string;
  preferredAlternative?: {
    action: string;
    reason: string;
  };
  strategicFallback?: string;
  matchedRuleId?: string;
}

export function normalizePlanToken(value: any): string {
  return String(value ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function actionMatches(action: string, candidates: string[]): boolean {
  const wanted = normalizePlanToken(action);
  return candidates.some((candidate) => normalizePlanToken(candidate) === wanted);
}
