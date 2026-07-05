import type {
  BattleSummary,
  CoachFocusMode,
  CoachPreset,
  CoachRunMode,
  TeamCoachBrief,
  TeamCoachRun,
  TeamProfile,
} from '../types';
import { fmtPct } from '../lib/format';
import { Markdown } from './Markdown';
import { ShinyStatus } from './ShinyStatus';

interface TeamCoachPanelProps {
  team: TeamProfile | null;
  latestBattle: BattleSummary | null;
  focusMode: CoachFocusMode;
  brief: TeamCoachBrief | null;
  run: TeamCoachRun | null;
  loading: boolean;
  runLoading: boolean;
  error: string | null;
  runError: string | null;
  presets: CoachPreset[];
  presetId: string;
  runMode: CoachRunMode;
  onFocusModeChange: (value: CoachFocusMode) => void;
  onPresetChange: (value: string) => void;
  onRunModeChange: (value: CoachRunMode) => void;
  onRunCoach: () => void;
  onRunAgent: () => void;
}

function Bucket({ label, count, note }: { label: string; count: number; note: string }) {
  return (
    <div className="coach-bucket">
      <span>{label}</span>
      <strong>{count}</strong>
      <small>{note}</small>
    </div>
  );
}

export function TeamCoachPanel({
  team,
  latestBattle,
  focusMode,
  brief,
  run,
  loading,
  runLoading,
  error,
  runError,
  presets,
  presetId,
  runMode,
  onFocusModeChange,
  onPresetChange,
  onRunModeChange,
  onRunCoach,
  onRunAgent,
}: TeamCoachPanelProps) {
  const buckets = brief?.evidenceBuckets;
  const isRecentMode = focusMode === 'recent';
  const recentTimeLabel = latestBattle?.endedAtLabel && latestBattle.endedAtLabel !== 'Unknown'
    ? latestBattle.endedAtLabel
    : 'latest captured postmortem';
  const title = isRecentMode ? 'Recent Battle Coach' : 'Team Coach';
  const runLabel = isRecentMode ? 'Run Battle Coach' : 'Run Team Coach';
  const description = isRecentMode
    ? latestBattle
      ? `Latest battle review vs ${latestBattle.opponent || 'opponent'} from ${recentTimeLabel}.`
      : 'No recent battle is available yet.'
    : team
      ? `Latest team read for ${team.teamName || team.team.join(' / ')}.`
      : 'Select a tracked team to generate a coach read.';

  return (
    <section className="section-panel coach-panel">
      <div className="section-head">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>

      <div className="coach-command-bar">
        <div className="coach-mode-switch" aria-label="Coach focus">
          <button
            className={isRecentMode ? 'coach-mode-button' : 'coach-mode-button active'}
            type="button"
            onClick={() => onFocusModeChange('team')}
          >
            Team Pattern
          </button>
          <button
            className={isRecentMode ? 'coach-mode-button active' : 'coach-mode-button'}
            type="button"
            onClick={() => onFocusModeChange('recent')}
          >
            Recent Battle
          </button>
        </div>
        <div className="coach-command-controls">
          <label className="coach-field">
            <span>Model</span>
            <select
              className="coach-select"
              value={presetId}
              onChange={(event) => onPresetChange(event.target.value)}
            >
              {presets.length ? presets.map((preset) => (
                <option value={preset.id} key={preset.id}>{preset.label}</option>
              )) : (
                <option value={presetId}>{presetId}</option>
              )}
            </select>
          </label>
          <label className="coach-field coach-field-small">
            <span>Run mode</span>
            <select
              className="coach-select"
              value={runMode}
              onChange={(event) => onRunModeChange(event.target.value as CoachRunMode)}
            >
              <option value="fake">Fake trace</option>
              <option value="auto">Auto real if configured</option>
              <option value="real">Real provider</option>
            </select>
          </label>
        </div>
        <div className="coach-command-actions">
          {!isRecentMode ? (
            <button
              className={loading ? 'secondary-button is-running' : 'secondary-button'}
              type="button"
              onClick={onRunCoach}
              disabled={!team || loading}
            >
              {loading ? <ShinyStatus active>Loading</ShinyStatus> : 'Load Context'}
            </button>
          ) : null}
          <button
            className={runLoading ? 'primary-button is-running' : 'primary-button'}
            type="button"
            onClick={onRunAgent}
            disabled={runLoading || (isRecentMode ? !latestBattle : !team)}
          >
            {runLoading ? <ShinyStatus active>Running</ShinyStatus> : runLabel}
          </button>
        </div>
      </div>

      {error ? <div className="error-state">{error}</div> : null}
      {runError ? <div className="error-state">{runError}</div> : null}
      {!isRecentMode && !brief && !loading && !error ? (
        <div className="empty-state">Coach context has not loaded yet.</div>
      ) : null}
      {isRecentMode && latestBattle ? (
        <div className="coach-summary-line">
          <strong>{latestBattle.result === 'unknown' ? 'result missing' : latestBattle.result || 'result n/a'}</strong>
          <span>vs {latestBattle.opponent || 'opponent'}</span>
          <span>{latestBattle.totalTurns} turns</span>
          <span>{latestBattle.metrics.criticalTurns} critical</span>
          <span>{fmtPct(latestBattle.metrics.followRate)} follow</span>
          <span>{fmtPct(latestBattle.metrics.pvHitRate)} PV</span>
          {latestBattle.dataIssues?.length ? <span>{latestBattle.dataIssues.join(', ')}</span> : null}
        </div>
      ) : null}
      {!isRecentMode && brief ? (
        <>
          <div className="coach-summary-line">
            <strong>{brief.summary.battles} battles</strong>
            <span>{brief.summary.wins}W / {brief.summary.losses}L</span>
            <span>{fmtPct(brief.summary.followRate)} follow</span>
            <span>{brief.summary.performanceBattles} performance-tracked</span>
          </div>
          <div className="coach-evidence-layout">
            <div className="coach-bucket-grid">
              <Bucket
                label="Clean calibration"
                count={buckets?.robustIgnoredAdvice.count || 0}
                note="Strong consensus, recommendation ignored"
              />
              <Bucket
                label="Hidden-info splits"
                count={buckets?.engineUncertainty.pimcSplits.count || 0}
                note="Engine uncertainty, not automatic misplay"
              />
              <Bucket
                label="PV misses"
                count={buckets?.engineUncertainty.pvMisses.count || 0}
                note="Belief and reality diverged"
              />
              <Bucket
                label="Field pressure"
                count={buckets?.fieldPressure.count || 0}
                note="Hazards, status, contact, residual"
              />
            </div>
            <div className="priority-list">
              <h3>Review Priorities</h3>
              {brief.reviewPriorities.slice(0, 4).map((item, index) => (
                <div className="priority-row" key={`${item.battleId}-${item.turn}-${index}`}>
                  <strong>T{item.turn ?? '?'}</strong>
                  <span>{item.opponent ? `vs ${item.opponent}` : item.battleId || 'battle'}</span>
                  <em>{item.reason || 'Review this team-level case.'}</em>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
      {run ? (
        <div className="coach-answer-card">
          <div className="coach-answer-head">
            <div>
              <h3>{run.preset?.label || run.model || 'Team Coach Run'}</h3>
              <p>{run.provider} / {run.mode} / {run.startedAtLabel || 'latest run'}</p>
            </div>
            <div className="coach-answer-meta">
              <span>{run.latencyMs ?? 'n/a'}ms</span>
              <span>{run.comparisonMetrics?.toolCallCount ?? run.toolCalls?.length ?? 0} tools</span>
              <span>{run.usage?.totalTokens == null ? 'tokens n/a' : `${run.usage.totalTokens} tokens`}</span>
            </div>
          </div>
          <Markdown value={run.answer || ''} />
        </div>
      ) : null}
    </section>
  );
}
