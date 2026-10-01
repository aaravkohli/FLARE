import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './src/index.css';
import './src/App.css';
import './mission-demo.css';
import LiveMissionSimulation from './src/mission-simulation/LiveMissionSimulation';
import {
  initialMissionRouteTransitionState,
  missionPayloadFromCompletion,
  parseMissionRouteTransition,
  reduceMissionRouteTransition,
} from './src/mission-simulation/routeTransition';
import type { MissionRouteTransition } from './src/mission-simulation/routeTransition';
import type { MissionFleetDrone, MissionTelemetryPayload } from './src/mission-simulation/types';

interface RecordedMission {
  provenance: Record<string, unknown>;
  events: MissionRouteTransition[];
  fleet: MissionFleetDrone[];
  sourceLimitations: string[];
}

function parseRecordedMission(value: unknown): RecordedMission {
  if (!value || typeof value !== 'object') throw new Error('Recorded mission file is missing.');
  const data = value as Record<string, unknown>;
  if (!Array.isArray(data.events) || !data.provenance || typeof data.provenance !== 'object') {
    throw new Error('Recorded mission schema is incomplete.');
  }
  const events = data.events.map(parseMissionRouteTransition);
  if (events.some(event => event === null)) throw new Error('Recorded mission contains an invalid route event.');
  const validEvents = events as MissionRouteTransition[];
  if (validEvents.length < 2 || !validEvents.some(event => event.phase === 'requested')
    || !validEvents.some(event => event.phase === 'completed')) {
    throw new Error('Recorded mission has no complete request and controller outcome.');
  }
  if (validEvents.some((event, index) => index > 0 && event.cursor <= validEvents[index - 1].cursor)) {
    throw new Error('Recorded mission event cursors are out of order.');
  }
  const ids = [...new Set(validEvents.map(event => event.drone_id))];
  const fleetIds = Array.isArray(data.fleet) && data.fleet.every(item => typeof item === 'string')
    ? data.fleet as string[] : ids;
  const fleet = fleetIds.map((id, index): MissionFleetDrone => ({
    drone_id: id, display_name: id.replaceAll('_', ' '), index: index + 1, enabled: true,
  }));
  const sourceLimitations = Array.isArray(data.source_limitations)
    ? data.source_limitations.filter((item): item is string => typeof item === 'string') : [];
  return { provenance: data.provenance as Record<string, unknown>, events: validEvents, fleet, sourceLimitations };
}

function routeLabel(value: string | null | undefined): string {
  return value ? value[0].toUpperCase() + value.slice(1) : 'Unknown';
}

function frameLabel(event: MissionRouteTransition): string {
  if (event.phase === 'requested') {
    const attack = event.telemetry.ew_status?.active_attack;
    return `${attack && attack !== 'none' ? `${routeLabel(attack)} attack · ` : ''}${routeLabel(event.decision.requested_path)} request recorded · controller outcome in next event`;
  }
  const installed = event.event.decision.installed_path;
  return event.controller.applied && installed
    ? `${routeLabel(installed)} installed · mock controller acknowledged`
    : `${routeLabel(event.event.decision.requested_path)} request · controller installation not confirmed`;
}

function RecordedMissionApp() {
  const [recording, setRecording] = useState<RecordedMission | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [receivedAtMs, setReceivedAtMs] = useState(() => performance.now());
  const [activeDrone, setActiveDrone] = useState('drone_1');
  const [jamProfile, setJamProfile] = useState('spot');
  const [jamDuration, setJamDuration] = useState(15);

  useEffect(() => {
    const abort = new AbortController();
    fetch(`${import.meta.env.BASE_URL}mission-demo/recorded-route-transitions.json`, { signal: abort.signal })
      .then(response => {
        if (!response.ok) throw new Error(`Recorded mission unavailable (${response.status}).`);
        return response.json() as Promise<unknown>;
      })
      .then(data => {
        const parsed = parseRecordedMission(data);
        setRecording(parsed);
        const firstCompletion = parsed.events.findIndex(event => event.phase === 'completed');
        setFrame(Math.max(0, firstCompletion));
        setActiveDrone(parsed.events[Math.max(0, firstCompletion)].drone_id);
      })
      .catch(error => { if (!abort.signal.aborted) setLoadError(String(error)); });
    return () => abort.abort();
  }, []);

  useEffect(() => {
    if (!playing || !recording) return;
    const timer = window.setInterval(() => {
      setFrame(previous => Math.min(previous + 1, recording.events.length - 1));
      setReceivedAtMs(performance.now());
    }, 4200);
    return () => window.clearInterval(timer);
  }, [playing, recording]);

  useEffect(() => {
    if (recording && frame >= recording.events.length - 1) setPlaying(false);
  }, [recording, frame]);

  useEffect(() => {
    if (!recording) return;
    const stepWithKeyboard = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
      event.preventDefault();
      setPlaying(false);
      setFrame(previous => Math.max(0, Math.min(recording.events.length - 1,
        previous + (event.key === 'ArrowRight' ? 1 : -1))));
      setReceivedAtMs(performance.now());
    };
    window.addEventListener('keydown', stepWithKeyboard);
    return () => window.removeEventListener('keydown', stepWithKeyboard);
  }, [recording]);

  const transitionState = useMemo(() => {
    if (!recording) return initialMissionRouteTransitionState;
    return recording.events.slice(0, frame + 1).reduce(reduceMissionRouteTransition, initialMissionRouteTransitionState);
  }, [recording, frame]);
  const payloads = useMemo(() => {
    const output: Record<string, MissionTelemetryPayload> = {};
    for (const event of transitionState.history) {
      if (event.phase === 'completed') output[event.drone_id] = missionPayloadFromCompletion(event);
    }
    return output;
  }, [transitionState]);
  const payload = payloads[activeDrone] ?? null;
  const current = recording?.events[frame];
  const pending = transitionState.pendingByDrone[activeDrone] ?? null;
  const completed = transitionState.completedByDrone[activeDrone] ?? null;
  const activePath = payload?.event?.sdn?.applied === true
    ? payload.event.decision?.installed_path || 'unknown'
    : pending?.last_confirmed_path || 'unknown';

  function seek(index: number) {
    if (!recording) return;
    setPlaying(false);
    setFrame(Math.max(0, Math.min(recording.events.length - 1, index)));
    setReceivedAtMs(performance.now());
  }

  if (loadError) return <main className="recorded-mission-error"><h1>Recorded mission unavailable</h1><p>{loadError}</p><a href="/">Return to FLARE</a></main>;
  if (!recording || !current) return <main className="recorded-mission-error"><h1>Loading recorded mission…</h1></main>;

  return <main className="recorded-mission-page">
    <div className="recorded-mission-toolbar">
      <div className="recorded-mission-toolbar__identity">
        <strong>FLARE / Recorded mission</strong>
        <span>Single-UAV controlled synthetic simulation · mock SDN · replayed canonical events · ← → step in presentation</span>
      </div>
      <div className="recorded-mission-toolbar__controls" role="group" aria-label="Recorded mission playback">
        <button type="button" onClick={() => seek(0)}>Restart</button>
        <button type="button" onClick={() => seek(frame - 1)} disabled={frame === 0}>Previous event</button>
        <button type="button" onClick={() => setPlaying(value => !value)} disabled={frame === recording.events.length - 1}>{playing ? 'Pause replay' : 'Play replay'}</button>
        <button type="button" onClick={() => seek(frame + 1)} disabled={frame === recording.events.length - 1}>Next event</button>
      </div>
      <a href="/">Live command interface</a>
    </div>
    <div className="recorded-mission-frame" role="status">
      <span>Recorded event {frame + 1} / {recording.events.length}</span>
      <strong>{frameLabel(current)}</strong>
      <span>Journal cursor {current.cursor} · {current.drone_id.replaceAll('_', ' ')}</span>
    </div>
    <LiveMissionSimulation
      payload={payload} swarmPayloads={payloads} activeDrone={activeDrone} fleetDrones={recording.fleet}
      activePath={activePath} threatLevel={payload?.decision.threat_level ?? pending?.decision.threat_level ?? 'UNKNOWN'}
      reward={payload?.decision.reward ?? 0} step={current.step} noSafeRoute={Boolean(pending?.decision.no_safe_route ?? payload?.decision.no_safe_route)}
      availablePaths={['direct', 'satellite', 'mesh']} sdnReady sdnController="mock"
      runtimeMode="RECORDED CONTROLLED SIMULATION" transportStatus="connected" telemetryReceivedAtMs={receivedAtMs}
      routeRequest={pending} routeCompletion={completed} routeTransitionHistory={transitionState.history}
      routeRequestReceivedAtMs={receivedAtMs} recordedReplay
      attackSimulationEnabled={false} jamProfile={jamProfile} jamDuration={jamDuration}
      onJamProfileChange={setJamProfile} onJamDurationChange={setJamDuration}
      onSelectDrone={setActiveDrone} onApplyAttack={async () => {}}
    />
    <details className="recorded-mission-provenance">
      <summary>Recording provenance and limitations</summary>
      <pre>{JSON.stringify(recording.provenance, null, 2)}</pre>
      <ul>{recording.sourceLimitations.map(item => <li key={item}>{item}</li>)}</ul>
      <p>This replay visualizes recorded canonical FLARE events. It does not re-run the controller, prove packet forwarding, or represent field telemetry.</p>
    </details>
  </main>;
}

createRoot(document.getElementById('root')!).render(<RecordedMissionApp />);
