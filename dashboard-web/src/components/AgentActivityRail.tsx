import type { CSSProperties } from 'react';
import type { CoachStreamEvent, TeamCoachBrief, TeamCoachRun } from '../types';
import { ShinyStatus } from './ShinyStatus';

interface AgentActivityRailProps {
  brief: TeamCoachBrief | null;
  loading: boolean;
  run: TeamCoachRun | null;
  runLoading: boolean;
  runError: string | null;
  streamEvents: CoachStreamEvent[];
}

export function AgentActivityRail({
  brief,
  loading,
  run,
  runLoading,
  runError,
  streamEvents,
}: AgentActivityRailProps) {
  const status = runLoading ? 'Running' : run ? 'Complete' : brief ? 'Ready' : loading ? 'Loading' : 'Idle';
  const calls = run?.toolCalls || [];
  const liveCalls = streamEvents
    .filter((event) => event.type === 'tool_completed' && event.toolCall)
    .map((event) => event.toolCall!);
  const completedTools = streamEvents.filter((event) => event.type === 'tool_completed').length;
  const runningTool = [...streamEvents].reverse().find((event) => event.type === 'tool_started');
  const lastCompletedTool = [...streamEvents].reverse().find((event) => event.type === 'tool_completed');
  const currentToolName = runLoading
    ? runningTool?.name || lastCompletedTool?.toolCall?.name || 'waiting for model'
    : null;
  const waitingSteps = loading || runLoading
    ? ['Connecting to dashboard API', 'Reading team overview', 'Waiting for model tool calls']
    : brief
      ? ['Team overview loaded', 'Evidence buckets available', 'Ready for model run']
      : ['Waiting for team context'];

  return (
    <aside className="activity-rail" aria-label="Agent activity">
      <div className="activity-head">
        <span>Agent Activity</span>
        <strong><ShinyStatus active={runLoading}>{status}</ShinyStatus></strong>
      </div>
      {runLoading ? (
        <div className="activity-run-summary live">
          <strong>{currentToolName ? `Now: ${currentToolName}` : 'Preparing run'}</strong>
          <span>{completedTools} tool{completedTools === 1 ? '' : 's'} completed</span>
          <span>{streamEvents.length} stream event{streamEvents.length === 1 ? '' : 's'}</span>
        </div>
      ) : run ? (
        <div className="activity-run-summary">
          <strong>{run.preset?.label || run.model || 'Team Coach'}</strong>
          <span>{run.provider} / {run.mode}</span>
          <span>{run.latencyMs ?? 'n/a'}ms</span>
          <span>{run.usage?.totalTokens == null ? 'tokens n/a' : `${run.usage.totalTokens} tokens`}</span>
        </div>
      ) : null}
      {runError ? <div className="activity-error">{runError}</div> : null}
      <div className="activity-list">
        {streamEvents.length ? streamEvents.map((event, index) => (
          <LiveEventRow
            event={event}
            index={index}
            key={`${event.type}-${event.callId || event.responseId || event.name || index}`}
          />
        )) : calls.length || liveCalls.length ? (calls.length ? calls : liveCalls).map((call, index) => (
          <details className="activity-step tool-call-step active" key={`${call.name}-${index}`}>
            <summary>
              <span className="activity-dot" />
              <span className="tool-call-title">
                <strong>{index + 1}. {call.name}</strong>
                <small>{call.durationMs ?? 'n/a'}ms</small>
              </span>
            </summary>
            <p>{call.outputSummary || 'Tool call completed.'}</p>
            <ToolPayload label="Arguments" value={formatJson(call.args || {})} />
          </details>
        )) : waitingSteps.map((step, index) => (
          <div className={`activity-step ${index === waitingSteps.length - 1 ? 'active' : ''}`} key={step}>
            <span className="activity-dot" />
            <div>
              <strong>{step}</strong>
              <small>{index === 0 ? 'dashboard' : 'team coach'}</small>
            </div>
          </div>
        ))}
      </div>
      <div className="activity-note">
        Coach answers stay in the main panel. Tool calls, timings, args, and usage live here.
      </div>
    </aside>
  );
}

function formatJson(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function eventTitle(event: CoachStreamEvent): string {
  if (event.type === 'run_started') return 'Run started';
  if (event.type === 'model_request_started') return 'Asking model';
  if (event.type === 'model_response_received') return 'Model responded';
  if (event.type === 'tool_started') return `Running ${event.name || 'tool'}`;
  if (event.type === 'tool_completed') return `Completed ${event.toolCall?.name || 'tool'}`;
  if (event.type === 'answer_ready') return 'Answer ready';
  if (event.type === 'completed') return 'Run complete';
  if (event.type === 'error') return 'Run failed';
  return event.type;
}

function eventDetail(event: CoachStreamEvent): string {
  if (event.type === 'run_started') {
    return `${event.provider || 'provider'} / ${event.mode || 'mode'}`;
  }
  if (event.type === 'model_request_started') {
    return `round ${event.toolRound || 1} / ${event.model || 'model'}`;
  }
  if (event.type === 'model_response_received') {
    return `${event.toolCallCount ?? 0} tool call${event.toolCallCount === 1 ? '' : 's'} requested`;
  }
  if (event.type === 'tool_started') {
    return JSON.stringify(event.args || {});
  }
  if (event.type === 'tool_completed') {
    return event.toolCall?.outputSummary || `${event.toolCall?.durationMs ?? 'n/a'}ms`;
  }
  if (event.type === 'answer_ready') {
    return event.usage?.totalTokens == null ? 'final text received' : `${event.usage.totalTokens} tokens`;
  }
  if (event.type === 'completed') return 'final payload stored';
  if (event.type === 'error') return event.message || 'stream error';
  return '';
}

function LiveEventRow({ event, index }: { event: CoachStreamEvent; index: number }) {
  const expandable = event.type === 'tool_started' || event.type === 'tool_completed';
  const detail = eventDetail(event);
  const args = event.type === 'tool_completed'
    ? event.toolCall?.args
    : event.args;
  const eventClass = [
    'activity-event',
    `event-${event.type.replaceAll('_', '-')}`,
    event.type === 'error' ? 'failed' : 'active',
  ].join(' ');
  const style = { '--activity-index': Math.min(index, 8) } as CSSProperties;

  if (expandable) {
    return (
      <details
        className={`activity-step tool-call-step live-event-step activity-event event-${event.type.replaceAll('_', '-')} ${event.type === 'tool_started' ? 'running' : 'active'}`}
        style={style}
      >
        <summary>
          <span className="activity-dot" />
          <span className="tool-call-title">
            <strong>
              <ShinyStatus active={event.type === 'tool_started'}>
                {eventTitle(event)}
              </ShinyStatus>
            </strong>
            <small>{event.type === 'tool_completed' ? `completed in ${event.toolCall?.durationMs ?? 'n/a'}ms` : 'running tool'}</small>
          </span>
        </summary>
        <p>{detail}</p>
        <ToolPayload label="Arguments" value={formatJson(args || {})} />
        {event.type === 'tool_completed' && event.toolOutputPreview ? (
          <ToolPayload
            label={`Response${event.toolOutputTruncated ? ' (truncated)' : ''}`}
            value={event.toolOutputPreview}
          />
        ) : null}
      </details>
    );
  }
  return (
    <div className={`activity-step live-event-step ${eventClass}`} style={style}>
      <span className="activity-dot" />
      <div>
        <strong>
          <ShinyStatus active={event.type === 'model_request_started'}>
            {eventTitle(event)}
          </ShinyStatus>
        </strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function ToolPayload({ label, value }: { label: string; value: string }) {
  return (
    <div className="tool-payload">
      <span>{label}</span>
      <code>{value}</code>
    </div>
  );
}
