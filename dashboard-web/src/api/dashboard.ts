import type {
  CoachPreset,
  CoachRunMode,
  CoachStreamEvent,
  DashboardArchive,
  TeamCoachBrief,
  TeamCoachRun,
} from '../types';

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getDashboardArchive(minSchemaVersion = 7): Promise<DashboardArchive> {
  return readJson<DashboardArchive>(
    `/dashboard/data?min_schema_version=${encodeURIComponent(minSchemaVersion)}`,
  );
}

export function getTeamCoachBrief(anchorBattleId: string): Promise<TeamCoachBrief> {
  return readJson<TeamCoachBrief>(`/dashboard/team-coach/${encodeURIComponent(anchorBattleId)}`);
}

export function getCoachPresets(): Promise<{ presets: CoachPreset[] }> {
  return readJson<{ presets: CoachPreset[] }>('/dashboard/coach-ai/presets');
}

export function runTeamCoachAI(
  anchorBattleId: string,
  presetId: string,
  runMode: CoachRunMode,
): Promise<TeamCoachRun> {
  return fetch(`/dashboard/team-coach-ai/${encodeURIComponent(anchorBattleId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ presetId, runMode }),
  }).then(async (response) => {
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        if (typeof payload?.detail === 'string') detail = payload.detail;
      } catch {
        // Keep HTTP detail when the backend response is not JSON.
      }
      throw new Error(detail);
    }
    return response.json() as Promise<TeamCoachRun>;
  });
}

const STREAM_EVENT_TYPES = [
  'run_started',
  'model_request_started',
  'model_response_received',
  'tool_started',
  'tool_completed',
  'answer_ready',
  'completed',
  'error',
];

export function streamTeamCoachAI(
  anchorBattleId: string,
  presetId: string,
  runMode: CoachRunMode,
  onEvent: (event: CoachStreamEvent) => void,
): Promise<TeamCoachRun> {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({ presetId, runMode });
    const source = new EventSource(
      `/dashboard/team-coach-ai/${encodeURIComponent(anchorBattleId)}/stream?${params.toString()}`,
    );
    let settled = false;

    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      source.close();
      callback();
    };

    const handleEvent = (message: MessageEvent<string>) => {
      let event: CoachStreamEvent;
      try {
        event = JSON.parse(message.data) as CoachStreamEvent;
      } catch {
        settle(() => reject(new Error('Team coach stream returned malformed JSON.')));
        return;
      }
      onEvent(event);
      if (event.type === 'completed' && event.run) {
        settle(() => resolve(event.run as TeamCoachRun));
      } else if (event.type === 'error') {
        settle(() => reject(new Error(event.message || 'Team coach stream failed.')));
      }
    };

    STREAM_EVENT_TYPES.forEach((type) => {
      source.addEventListener(type, handleEvent as EventListener);
    });
    source.onerror = () => {
      settle(() => reject(new Error('Team coach stream connection failed.')));
    };
  });
}

export function streamBattleCoachAI(
  battleId: string,
  presetId: string,
  runMode: CoachRunMode,
  onEvent: (event: CoachStreamEvent) => void,
): Promise<TeamCoachRun> {
  return streamCoachEndpoint(
    `/dashboard/coach-ai/${encodeURIComponent(battleId)}/stream`,
    presetId,
    runMode,
    onEvent,
  );
}

function streamCoachEndpoint(
  path: string,
  presetId: string,
  runMode: CoachRunMode,
  onEvent: (event: CoachStreamEvent) => void,
): Promise<TeamCoachRun> {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({ presetId, runMode });
    const source = new EventSource(`${path}?${params.toString()}`);
    let settled = false;

    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      source.close();
      callback();
    };

    const handleEvent = (message: MessageEvent<string>) => {
      let event: CoachStreamEvent;
      try {
        event = JSON.parse(message.data) as CoachStreamEvent;
      } catch {
        settle(() => reject(new Error('Coach stream returned malformed JSON.')));
        return;
      }
      onEvent(event);
      if (event.type === 'completed' && event.run) {
        settle(() => resolve(event.run as TeamCoachRun));
      } else if (event.type === 'error') {
        settle(() => reject(new Error(event.message || 'Coach stream failed.')));
      }
    };

    STREAM_EVENT_TYPES.forEach((type) => {
      source.addEventListener(type, handleEvent as EventListener);
    });
    source.onerror = () => {
      settle(() => reject(new Error('Coach stream connection failed.')));
    };
  });
}
