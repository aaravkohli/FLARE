import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './src/index.css';
import './src/App.css';
import LiveMissionSimulation from './src/mission-simulation/LiveMissionSimulation';
import type {
  AttackProfile,
  CommunicationRoute,
  MissionFleetDrone,
  MissionRoute,
  MissionTelemetryPayload,
} from './src/mission-simulation/types';
import type { MissionRouteRequest } from './src/mission-simulation/routeTransition';

// Screenshot inputs only. These are controlled UI mocks, not recorded
// decisions, detector outputs, controller responses, or evaluation evidence.
const cases = [
  'nominal', 'normal', 'attack', 'reroute', 'satellite', 'unknown',
  'gps_spoofing', 'hold', 'containment', 'disconnected', 'stale',
] as const;
type Case = typeof cases[number];
const params = new URLSearchParams(location.search);
const requestedCase = params.get('case');
const scenario: Case = cases.find(item => item === requestedCase) || 'nominal';
const profileQuery = params.get('profile');
const attackProfiles: AttackProfile[] = ['spot', 'barrage', 'sweep', 'gps_spoofing', 'dos'];
const auditProfile: AttackProfile = attackProfiles.find(item => item === profileQuery) || 'spot';
const fleet: MissionFleetDrone[] = [1, 2, 3, 4].map(index => ({
  drone_id: `drone_${index}`,
  display_name: `Drone ${index.toString().padStart(2, '0')}`,
  index,
  enabled: true,
}));
const selectedQuery = params.get('drone');
const initialDrone = fleet.find(drone => drone.drone_id === selectedQuery)?.drone_id || 'drone_1';

const scenarioNotes: Record<Case, string> = {
  nominal: 'Four nominal UAV snapshots; each route is a controlled mock acknowledgement.',
  normal: 'Alias for the nominal fleet snapshot.',
  attack: 'Synthetic scenario and RF values only; detector attack_type remains none.',
  reroute: 'Satellite request is awaiting a mock controller response. Direct is the last confirmed route.',
  satellite: 'Satellite is a controlled mock acknowledgement for visual inspection.',
  unknown: 'Satellite request has no confirmed installation or controller success.',
  gps_spoofing: 'Reported GPS drift is an untrusted schematic cue; physical UAV position is unchanged.',
  hold: 'All routes are unsafe; HOLD is shown as a controlled mock acknowledgement.',
  containment: 'Quarantine and HOLD are controlled mock policy inputs, not detector or network evidence.',
  disconnected: 'The socket is disconnected; the displayed Direct route is an aged last snapshot.',
  stale: 'The socket is connected, but this last snapshot is older than the freshness limit.',
};

function baseRoute(droneId: string): CommunicationRoute {
  return droneId === 'drone_2' ? 'satellite' : droneId === 'drone_3' ? 'mesh' : 'direct';
}

function pathMetrics(
  pathId: CommunicationRoute,
  directDegraded: boolean,
  allDegraded: boolean,
  dos: boolean,
) {
  const normal = {
    direct: { rssi: -58, pdr: 0.98, sinr: 22, latency: 32, packet_loss: 0.02 },
    satellite: { rssi: -74, pdr: 0.94, sinr: 15, latency: 145, packet_loss: 0.06 },
    mesh: { rssi: -69, pdr: 0.93, sinr: 14, latency: 84, packet_loss: 0.07 },
  }[pathId];
  if (allDegraded) {
    return { path_id: pathId, rssi: -101, pdr: 0.18, sinr: 1.5, latency: 610, packet_loss: 0.84 };
  }
  if (pathId === 'direct' && directDegraded) {
    return dos
      ? { path_id: pathId, rssi: -58, pdr: 0.14, sinr: 22, latency: 860, packet_loss: 0.86 }
      : { path_id: pathId, rssi: -101, pdr: 0.21, sinr: 1.5, latency: 610, packet_loss: 0.84 };
  }
  return { path_id: pathId, ...normal };
}

function makeMetrics(
  droneId: string,
  timestamp: number,
  attack: AttackProfile,
  directDegraded: boolean,
  allDegraded: boolean,
) {
  return {
    drone_id: droneId,
    timestamp,
    source: 'synthetic' as const,
    paths: (['direct', 'satellite', 'mesh'] as const).map(path =>
      pathMetrics(path, directDegraded, allDegraded, attack === 'dos')),
    ew_status: {
      active_attack: attack === 'none' ? null : attack,
      jammed_paths: allDegraded ? ['direct', 'satellite', 'mesh']
        : directDegraded ? ['direct'] : [],
    },
    // Drift only drives a separate untrusted reported-position schematic.
    // The latitude/longitude and physical UAV marker remain fixed.
    gps: { latitude: 45.1, longitude: -122.3, drift_m: attack === 'gps_spoofing' ? 42 : 0 },
    security_evidence: null,
  } satisfies MissionTelemetryPayload['metrics'];
}

function makePayload(droneId: string, kind: Case, now: number): MissionTelemetryPayload {
  const currentCase: Case = droneId === 'drone_1' ? kind : 'nominal';
  const priorSnapshot = currentCase === 'disconnected' || currentCase === 'stale';
  const timestamp = now - (priorSnapshot ? 14 : currentCase === 'reroute' ? 1 : 0);
  const profile: AttackProfile = currentCase === 'attack' ? auditProfile
    : currentCase === 'gps_spoofing' ? 'gps_spoofing'
      : currentCase === 'hold' ? 'barrage'
        : currentCase === 'satellite' || currentCase === 'unknown' ? 'spot' : 'none';
  const pathAttack = currentCase === 'attack'
    && !['gps_spoofing', 'spoofing', 'replay', 'fhss'].includes(profile);
  const allDegraded = currentCase === 'hold' || (pathAttack && profile === 'barrage');
  const directDegraded = (pathAttack && !allDegraded)
    || currentCase === 'satellite' || currentCase === 'unknown';
  const installed: MissionRoute | null = currentCase === 'unknown' ? null
    : allDegraded || currentCase === 'containment' ? 'hold'
      : currentCase === 'satellite' ? 'satellite' : baseRoute(droneId);
  // The reroute payload is the prior Direct confirmation. Its pending
  // Satellite intent lives only in makeRouteRequest(), without an outcome.
  const requested: MissionRoute = currentCase === 'unknown'
    || (currentCase === 'attack' && directDegraded) ? 'satellite' : installed || 'direct';
  const policy: MissionRoute = installed === 'hold' ? 'direct' : requested;
  const hold = installed === 'hold';
  const noSafeRoute = allDegraded;
  const nominalScores = installed === 'satellite' ? [0.14, 0.04, 0.16]
    : installed === 'mesh' ? [0.16, 0.14, 0.04] : [0.04, 0.08, 0.09];
  const scores = allDegraded ? [0.94, 0.92, 0.96]
    : directDegraded ? [0.91, 0.08, 0.09] : nominalScores;
  const safeMask = scores.map(score => score <= 0.8);
  const confirmed = installed !== null && !(currentCase === 'attack' && directDegraded);
  const reward = hold ? -0.72 : directDegraded ? -0.3 : 0.8;
  const metrics = makeMetrics(droneId, timestamp, profile, directDegraded, allDegraded);
  const threat = directDegraded || allDegraded || currentCase === 'containment' ? 'HIGH' : 'LOW';
  const step = currentCase === 'reroute' ? 41 : 42;

  return {
    type: 'telemetry',
    timestamp,
    telemetry_age_s: priorSnapshot ? 14 : 0,
    decision: {
      path_name: installed || requested,
      requested_path: requested,
      policy_path: policy,
      threat_level: threat,
      reward,
      step,
      no_safe_route: noSafeRoute,
      safety_override: hold,
      constraint_reason: noSafeRoute ? 'all_routes_above_threshold'
        : currentCase === 'containment' ? 'containment_required' : null,
      sdn_applied: confirmed,
    },
    metrics,
    event: {
      event_id: `visual-qa-mock:${currentCase}:${droneId}:${step}`,
      timestamp,
      step,
      inference: {
        source: 'heuristic_fallback',
        path_scores: scores,
        confidence: 0.4,
        // Scenario ground truth is never copied into a detector output.
        attack_type: 'none',
      },
      decision: {
        source: 'greedy_fallback',
        policy_path: policy,
        requested_path: requested,
        installed_path: installed,
        network_action: hold ? 'hold' : 'forward',
        threat_level: threat,
        no_safe_route: noSafeRoute,
        safety_override: hold,
        safe_action_mask: safeMask,
        safety_threshold: 0.8,
        route_changed: currentCase === 'satellite' || hold,
        constraint_reason: noSafeRoute ? 'all_routes_above_threshold'
          : currentCase === 'containment' ? 'containment_required' : null,
        routing_contract: hold ? 'routing_state_v3' : 'routing_state_v2',
      },
      sdn: {
        // True describes only a labelled UI-only mock acknowledgement.
        applied: confirmed,
        error: confirmed ? null : installed === null
          ? 'Mock controller outcome unavailable' : 'Mock route update not confirmed',
        response: confirmed ? {
          route: { status: 'ok', installed_path: installed, evidence: 'ui_only_controlled_mock' },
        } : null,
      },
      outcome: {
        reward,
        packet_loss: hold ? 1 : directDegraded ? 0.84 : 0.02,
        estimated: installed === null,
      },
      telemetry: { security_evidence: null },
      containment: currentCase === 'containment' ? {
        mode: 'quarantined',
        reason: 'Controlled UI fixture: low trust policy input',
        trust_score: 0.23,
        insider_risk: 0.89,
      } : null,
    },
  };
}

function makeRouteRequest(now: number): MissionRouteRequest {
  return {
    type: 'route_transition',
    phase: 'requested',
    cursor: 1,
    transition_id: 'visual-qa-mock:42:drone_1',
    occurred_at: now,
    run_id: 'visual-qa-mock',
    step: 42,
    drone_id: 'drone_1',
    mode: 'simulation',
    telemetry: makeMetrics('drone_1', now, 'spot', true, false),
    inference: {
      source: 'heuristic_fallback',
      path_scores: [0.91, 0.08, 0.09],
      confidence: 0.4,
      attack_type: 'none',
    },
    decision: {
      source: 'greedy_fallback',
      policy_path: 'satellite',
      requested_path: 'satellite',
      threat_level: 'HIGH',
      safety_override: false,
      safe_action_mask: [false, true, true],
      no_safe_route: false,
      constraint_reason: null,
      network_action: 'forward',
      safety_threshold: 0.8,
    },
    last_confirmed_path: 'direct',
    controller: { configured_backend: 'mock', status: 'awaiting_response', applied: null },
  };
}

function AuditApp() {
  const [clock, setClock] = useState(() => ({
    receivedAtMs: performance.now(),
    now: Math.floor(Date.now() / 1000),
  }));
  const [activeDrone, setActiveDrone] = useState(initialDrone);
  const [jamProfile, setJamProfile] = useState('spot');
  const [jamDuration, setJamDuration] = useState(20);
  useEffect(() => {
    const timer = window.setInterval(() => setClock({
      receivedAtMs: performance.now(),
      now: Math.floor(Date.now() / 1000),
    }), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const { now, receivedAtMs } = clock;
  const payloads = Object.fromEntries(fleet.map(drone => [
    drone.drone_id, makePayload(drone.drone_id, scenario, now),
  ])) as Record<string, MissionTelemetryPayload>;
  const payload = payloads[activeDrone];
  const routeRequest = scenario === 'reroute' && activeDrone === 'drone_1'
    ? makeRouteRequest(now) : null;
  const transportStatus = scenario === 'disconnected' ? 'disconnected' : 'connected';

  return <main style={{ padding: '24px', maxWidth: '1600px', margin: '0 auto' }}>
    <aside role="note" style={{ marginBottom: 16, padding: '10px 14px', border: '1px solid #53667a', color: '#dbe4ee', background: '#17212d', fontSize: 13 }}>
      <strong>VISUAL QA ONLY · CONTROLLED UI MOCK INPUTS</strong>
      <span style={{ marginLeft: 12 }}>Case: {scenario.replaceAll('_', ' ')} · {scenarioNotes[scenario]} No replay or evaluation evidence. Controls are disabled.</span>
    </aside>
    <LiveMissionSimulation
      payload={payload}
      swarmPayloads={payloads}
      activeDrone={activeDrone}
      fleetDrones={fleet}
      activePath={payload.decision.path_name}
      threatLevel={payload.decision.threat_level}
      reward={payload.decision.reward}
      step={payload.decision.step}
      noSafeRoute={Boolean(payload.decision.no_safe_route)}
      availablePaths={['direct', 'satellite', 'mesh']}
      sdnReady
      sdnController="UI-only mock"
      runtimeMode="VISUAL QA / CONTROLLED MOCK"
      transportStatus={transportStatus}
      telemetryReceivedAtMs={receivedAtMs}
      routeRequest={routeRequest}
      routeRequestReceivedAtMs={receivedAtMs}
      routeTransitionHistory={routeRequest ? [routeRequest] : []}
      attackSimulationEnabled={false}
      jamProfile={jamProfile}
      jamDuration={jamDuration}
      onJamProfileChange={setJamProfile}
      onJamDurationChange={setJamDuration}
      onSelectDrone={setActiveDrone}
      onApplyAttack={async () => {}}
    />
  </main>;
}

createRoot(document.getElementById('root')!).render(<AuditApp />);

export default AuditApp;
