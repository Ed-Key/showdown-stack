import { useEffect, useMemo, useState } from 'react';
import ClickSpark from './components/reactbits/ClickSpark';
import {
  getCoachPresets,
  getDashboardArchive,
  getTeamCoachBrief,
  streamBattleCoachAI,
  streamTeamCoachAI,
} from './api/dashboard';
import type {
  BattleSummary,
  CoachFocusMode,
  CoachPreset,
  CoachRunMode,
  CoachStreamEvent,
  DashboardArchive,
  TeamCoachBrief,
  TeamCoachRun,
} from './types';
import { AgentActivityRail } from './components/AgentActivityRail';
import { BattleList } from './components/BattleList';
import { DashboardBackground } from './components/DashboardBackground';
import { Header } from './components/Header';
import { MetricStrip } from './components/MetricStrip';
import { TeamCoachPanel } from './components/TeamCoachPanel';
import { TeamPerformance } from './components/TeamPerformance';

export function App() {
  const [archive, setArchive] = useState<DashboardArchive | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [coachBrief, setCoachBrief] = useState<TeamCoachBrief | null>(null);
  const [coachLoading, setCoachLoading] = useState(false);
  const [coachError, setCoachError] = useState<string | null>(null);
  const [coachPresets, setCoachPresets] = useState<CoachPreset[]>([]);
  const [coachFocusMode, setCoachFocusMode] = useState<CoachFocusMode>('team');
  const [coachPresetId, setCoachPresetId] = useState('openai-gpt-54-mini-balanced');
  const [coachRunMode, setCoachRunMode] = useState<CoachRunMode>('auto');
  const [coachRun, setCoachRun] = useState<TeamCoachRun | null>(null);
  const [coachRunLoading, setCoachRunLoading] = useState(false);
  const [coachRunError, setCoachRunError] = useState<string | null>(null);
  const [coachStreamEvents, setCoachStreamEvents] = useState<CoachStreamEvent[]>([]);

  useEffect(() => {
    getDashboardArchive(7)
      .then((data) => {
        setArchive(data);
        setArchiveError(null);
      })
      .catch((error: unknown) => {
        setArchiveError(error instanceof Error ? error.message : 'Could not load dashboard data.');
      });
  }, []);

  useEffect(() => {
    getCoachPresets()
      .then((data) => {
        setCoachPresets(data.presets || []);
        if ((data.presets || []).some((preset) => preset.id === 'openai-gpt-55-pro-xhigh')) {
          setCoachPresetId('openai-gpt-55-pro-xhigh');
        }
      })
      .catch(() => {
        setCoachPresets([]);
      });
  }, []);

  useEffect(() => {
    setCoachRun(null);
    setCoachRunError(null);
    setCoachStreamEvents([]);
  }, [coachFocusMode]);

  const activeTeam = archive?.teamProfiles?.[0] || null;
  const latestBattle: BattleSummary | null = archive?.latestRecordedBattle || archive?.battles?.[0] || null;
  const anchorBattleId = useMemo(() => {
    if (!archive || !activeTeam) return null;
    const teamKey = activeTeam.team.join(' / ');
    return archive.battles.find((battle) => battle.team.join(' / ') === teamKey)?.battleId || null;
  }, [archive, activeTeam]);

  useEffect(() => {
    if (!anchorBattleId) return;
    let cancelled = false;
    setCoachLoading(true);
    setCoachError(null);
    setCoachBrief(null);
    setCoachRun(null);
    setCoachRunError(null);
    setCoachStreamEvents([]);
    getTeamCoachBrief(anchorBattleId)
      .then((brief) => {
        if (!cancelled) setCoachBrief(brief);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCoachError(error instanceof Error ? error.message : 'Could not load team coach context.');
        }
      })
      .finally(() => {
        if (!cancelled) setCoachLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [anchorBattleId]);

  async function loadCoachContext(): Promise<TeamCoachBrief | null> {
    if (!anchorBattleId) return null;
    setCoachLoading(true);
    setCoachError(null);
    try {
      const brief = await getTeamCoachBrief(anchorBattleId);
      setCoachBrief(brief);
      return brief;
    } catch (error) {
      setCoachError(error instanceof Error ? error.message : 'Could not load team coach context.');
      return null;
    } finally {
      setCoachLoading(false);
    }
  }

  async function runCoachAgent() {
    const targetBattleId = coachFocusMode === 'recent' ? latestBattle?.battleId : anchorBattleId;
    if (!targetBattleId) return;
    setCoachRunLoading(true);
    setCoachRunError(null);
    setCoachRun(null);
    setCoachStreamEvents([]);
    try {
      if (coachFocusMode === 'team' && !coachBrief) {
        if (!anchorBattleId) return;
        const brief = await getTeamCoachBrief(anchorBattleId);
        setCoachBrief(brief);
      }
      const streamRunner = coachFocusMode === 'recent' ? streamBattleCoachAI : streamTeamCoachAI;
      const run = await streamRunner(
        targetBattleId,
        coachPresetId,
        coachRunMode,
        (event) => setCoachStreamEvents((events) => [...events, event]),
      );
      setCoachRun(run);
    } catch (error) {
      setCoachRunError(error instanceof Error ? error.message : 'Could not run team coach.');
    } finally {
      setCoachRunLoading(false);
    }
  }

  return (
    <div className="dashboard-root">
      <DashboardBackground />
      <ClickSpark sparkColor="#ffd83d" sparkSize={9} sparkRadius={18} sparkCount={8} duration={450}>
        <main className="dashboard-main">
        <Header archive={archive} team={activeTeam} />
        {archiveError ? <div className="error-state">{archiveError}</div> : null}
        <MetricStrip summary={archive?.summary || null} />
        <div className="content-grid">
          <div className="primary-stack">
            <TeamCoachPanel
              team={activeTeam}
              latestBattle={latestBattle}
              focusMode={coachFocusMode}
              brief={coachBrief}
              run={coachRun}
              loading={coachLoading}
              runLoading={coachRunLoading}
              error={coachError}
              runError={coachRunError}
              presets={coachPresets}
              presetId={coachPresetId}
              runMode={coachRunMode}
              onFocusModeChange={setCoachFocusMode}
              onPresetChange={setCoachPresetId}
              onRunModeChange={setCoachRunMode}
              onRunCoach={loadCoachContext}
              onRunAgent={runCoachAgent}
            />
            <TeamPerformance team={activeTeam} />
            <BattleList battles={archive?.battles || []} />
          </div>
          <AgentActivityRail
            brief={coachBrief}
            loading={coachLoading}
            run={coachRun}
            runLoading={coachRunLoading}
            runError={coachRunError}
            streamEvents={coachStreamEvents}
          />
        </div>
        </main>
      </ClickSpark>
    </div>
  );
}
