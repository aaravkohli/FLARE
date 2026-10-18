import { RouteSelector, DecisionEvidence } from './MissionEvidencePanels';
import DecisionPanel from './DecisionPanel';
import { decisionEventKey, decisionPhase, emptyDecisionHistory, reduceDecisionHistory, PHASE_LABELS } from './world/decisionPresentation';
import type { DecisionEventItem as MissionEvent, DecisionPhase } from './world/decisionPresentation';
import { threatVisual } from './world/threatState';
import { telemetryThreatRows } from './world/telemetryThreat';
import React, { lazy, Suspense, memo, useReducer, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  Maximize2,
  Minimize2,
  Pause,
  PanelRightClose,
  PanelRightOpen,
  Play,
} from 'lucide-react';
import { adaptMissionSimulationState, applyMissionRouteRequest } from './adapter';
import type {
  AttackProfile,
  CommunicationRoute,
  MissionFleetDrone,
  MissionRoute,
  MissionRouteState,
  MissionSimulationState,
  MissionTelemetryPayload,
} from './types';
import './mission-simulation.css';
import { createClock } from './world/clock';
import type { VisualClock } from './world/clock';
import { createWorld } from './world/dynamics';
import { missionEvidenceStatus, MISSION_FRESHNESS_LIMIT_S } from './world/evidenceFreshness';
import type { MissionEvidenceStatus, MissionTransportStatus } from './world/evidenceFreshness';
import type { MissionRouteCompletion, MissionRouteRequest, MissionRouteTransition } from './routeTransition';
import { BASE } from './world/projection';
import { networkRouteState } from './world/networkState';
import { packetFlowEnabled } from './world/PacketStream';
import { MISSION_SATELLITE_ORBIT } from './world/satelliteOrbit';
import { useSvgWorld } from './world/useSvgWorld';

class WorldRenderBoundary extends React.Component<{ onFallback: () => void; children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onFallback(); }
  render() { return this.state.failed ? null : this.props.children; }
}
const MissionWorldCanvas = lazy(() => import('./world/MissionWorldCanvas'));

const ROUTES: CommunicationRoute[] = ['direct', 'satellite', 'mesh'];
const ROUTE_LABELS: Record<MissionRoute, string> = {
  direct: 'Direct',
  satellite: 'Satellite',
  mesh: 'Mesh',
  hold: 'Network hold',
};

const ATTACK_OPTIONS: Array<{ value: AttackProfile; label: string }> = [
  { value: 'none', label: 'Normal' },
  { value: 'spot', label: 'Spot jam' },
  { value: 'sweep', label: 'Sweep' },
  { value: 'barrage', label: 'Barrage' },
  { value: 'smart', label: 'Smart jam' },
  { value: 'reactive', label: 'Reactive' },
  { value: 'adaptive', label: 'Adaptive' },
  { value: 'fhss', label: 'FHSS' },
  { value: 'spoofing', label: 'Spoofing' },
  { value: 'gps_spoofing', label: 'GPS spoofing' },
  { value: 'replay', label: 'Replay' },
  { value: 'dos', label: 'DoS' },
];

interface LiveMissionSimulationProps {
  payload: MissionTelemetryPayload | null;
  swarmPayloads: Record<string, MissionTelemetryPayload>;
  activeDrone: string;
  fleetDrones: MissionFleetDrone[];
  activePath: string;
  threatLevel: string;
  reward: number;
  step: number;
  noSafeRoute: boolean;
  availablePaths: string[];
  sdnReady?: boolean;
  sdnController: string;
  runtimeMode?: string;
  transportStatus?: MissionTransportStatus;
  telemetryUnavailable?: string | null;
  telemetrySafetyState?: { status: 'hold_applied' | 'hold_failed' | null; applied: boolean | null } | null;
  telemetryReceivedAtMs?: number | null;
  routeRequest?: MissionRouteRequest | null;
  routeRequestReceivedAtMs?: number | null;
  routeCompletion?: MissionRouteCompletion | null;
  routeTransitionHistory?: MissionRouteTransition[];
  recordedReplay?: boolean;
  trust?: number;
  insiderRisk?: number;
  containmentMode?: string;
  attackSimulationEnabled: boolean;
  jamProfile: string;
  jamDuration: number;
  onJamProfileChange: (profile: string) => void;
  onJamDurationChange: (duration: number) => void;
  onSelectDrone: (drone: string) => void;
  onApplyAttack: (path: CommunicationRoute, profile: AttackProfile) => Promise<void>;
}

function formatPercent(value?: number, digits = 1) {
  return value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;
}

function formatMetric(value: number | undefined, unit: string, digits = 0) {
  return value === undefined ? '—' : `${value.toFixed(digits)} ${unit}`;
}

function titleCase(value?: string | null) {
  if (!value) return '—';
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function attackLabel(profile: AttackProfile) {
  if (profile === 'none') return 'None';
  if (profile === 'gps_spoofing') return 'GPS spoofing';
  if (profile === 'fhss') return 'FHSS';
  if (profile === 'dos') return 'DoS';
  return titleCase(profile);
}

function routeFromPayload(payload: MissionTelemetryPayload | undefined, expectedDroneId: string): MissionRoute | null {
  if (payload?.metrics.drone_id !== expectedDroneId) return null;
  const decision = payload?.event?.decision;
  const value = decision && 'installed_path' in decision
    ? decision.installed_path
    : payload?.decision.sdn_applied === true ? payload.decision.path_name : null;
  return value === 'direct' || value === 'satellite' || value === 'mesh' || value === 'hold' ? value : null;
}

function routeHealth(route: MissionRouteState, noSafeRoute: boolean) {
  if (!route.available) return 'Unavailable';
  if (route.safe === false || (noSafeRoute && route.safe === undefined && route.threatScore !== undefined)) return 'Unsafe';
  if (route.jammed || (route.metric?.packet_loss ?? 0) >= 0.2) return 'Degraded';
  if (route.active) return 'Active';
  return 'Available';
}

function attackTarget(state: MissionSimulationState) {
  if (state.activeAttack === 'barrage') return 'All communication paths';
  if (state.activeAttack === 'sweep') return 'Sweeping across paths';
  if (state.activeAttack === 'gps_spoofing') return 'Navigation telemetry';
  if (state.activeAttack === 'reactive') return `${ROUTE_LABELS[state.selectedRoute]} traffic`;
  if (state.activeAttack === 'adaptive') return 'Policy-selected path';
  if (state.jammedPaths.length) return state.jammedPaths.map(route => ROUTE_LABELS[route]).join(', ');
  if (state.activeAttack === 'none') return 'No active event';
  return 'Telemetry integrity';
}

function eventTime(timestamp?: number) {
  const time = timestamp ? new Date(timestamp > 1e12 ? timestamp : timestamp * 1000) : new Date();
  return time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function threatPresentation(level: string, containmentMode?: string) {
  if (containmentMode && containmentMode !== 'normal') {
    return { label: 'Containment active', tone: 'critical' } as const;
  }
  if (level === 'HIGH') return { label: 'Critical threat', tone: 'critical' } as const;
  if (level === 'MEDIUM') return { label: 'Elevated threat', tone: 'warning' } as const;
  if (level === 'LOW') return { label: 'System nominal', tone: 'nominal' } as const;
  return { label: 'Threat pending', tone: 'neutral' } as const;
}

function sourceLabel(source?: MissionSimulationState['source']) {
  switch (source) {
    case 'synthetic': return 'Synthetic telemetry';
    case 'live': return 'Live adapter';
    case 'cached_live': return 'Cached live snapshot';
    case 'synthetic_fallback': return 'Synthetic fallback';
    case 'legacy_api_fallback': return 'Legacy API fallback';
    default: return 'Source unavailable';
  }
}

function routeRequestEvidenceStatus(
  request: MissionRouteRequest,
  receivedAtMs: number | null | undefined,
  transportStatus: MissionTransportStatus,
  telemetryUnavailable: string | null | undefined,
  nowMs: number,
): MissionEvidenceStatus {
  if (telemetryUnavailable) return { kind: 'unavailable', label: 'Telemetry unavailable', ageSeconds: null, evidenceCurrent: false, lastSnapshot: true };
  if (transportStatus !== 'connected') return { kind: 'disconnected', label: transportStatus === 'connecting' ? 'Reconnecting' : 'Disconnected', ageSeconds: null, evidenceCurrent: false, lastSnapshot: true };
  const ageAtRequest = request.occurred_at - request.telemetry.timestamp;
  if (!Number.isFinite(ageAtRequest) || ageAtRequest < 0 || receivedAtMs == null
    || !Number.isFinite(receivedAtMs) || !Number.isFinite(nowMs)) {
    return { kind: 'unknown-age', label: 'Request evidence age unknown', ageSeconds: null, evidenceCurrent: false, lastSnapshot: true };
  }
  const ageSeconds = ageAtRequest + Math.max(0, nowMs - receivedAtMs) / 1000;
  if (ageSeconds > MISSION_FRESHNESS_LIMIT_S) {
    return { kind: 'stale', label: `Stale request ${ageSeconds.toFixed(1)} s`, ageSeconds, evidenceCurrent: false, lastSnapshot: true };
  }
  return { kind: 'fresh', label: `Fresh request ${ageSeconds.toFixed(1)} s`, ageSeconds, evidenceCurrent: true, lastSnapshot: false };
}

function recordedFrameEvidenceStatus(
  payload: MissionTelemetryPayload | null,
  request: MissionRouteRequest | null,
): MissionEvidenceStatus {
  const sourceAge = request ? request.occurred_at - request.telemetry.timestamp : payload?.telemetry_age_s;
  if (!request && !payload) return { kind: 'awaiting', label: 'Awaiting recorded frame', ageSeconds: null, evidenceCurrent: false, lastSnapshot: false };
  if (sourceAge == null || !Number.isFinite(sourceAge) || sourceAge < 0) {
    return { kind: 'unknown-age', label: 'Recorded frame · source age unavailable', ageSeconds: null, evidenceCurrent: false, lastSnapshot: true };
  }
  const evidenceCurrent = sourceAge <= MISSION_FRESHNESS_LIMIT_S;
  return {
    kind: evidenceCurrent ? 'fresh' : 'stale',
    label: `Recorded frame · source age ${sourceAge.toFixed(1)} s`,
    ageSeconds: sourceAge,
    evidenceCurrent,
    lastSnapshot: !evidenceCurrent,
  };
}

interface TransitionRailEvent extends MissionEvent { droneId: string }

function transitionRailEvent(transition: MissionRouteTransition): TransitionRailEvent {
  const directScore = transition.phase === 'requested' && Number.isFinite(transition.inference.path_scores[0])
    ? ` · Direct threat score ${transition.inference.path_scores[0].toFixed(2)} (${transition.inference.source})`
    : '';
  const label = transition.phase === 'requested'
    ? `${ROUTE_LABELS[transition.decision.requested_path]} requested; controller awaiting response. Last confirmed: ${transition.last_confirmed_path ? ROUTE_LABELS[transition.last_confirmed_path] : 'unknown'}${directScore}`
    : transition.controller.applied
      ? `Controller acknowledged ${ROUTE_LABELS[transition.event.decision.requested_path]}; canonical event carries installation`
      : `Controller did not confirm ${ROUTE_LABELS[transition.event.decision.requested_path]}; last confirmed: ${transition.last_confirmed_path ? ROUTE_LABELS[transition.last_confirmed_path] : 'unknown'}`;
  return {
    id: `route-${transition.phase}:${transition.transition_id}`,
    droneId: transition.drone_id,
    at: transition.occurred_at,
    label,
    tone: transition.phase === 'requested' || transition.controller.applied ? 'info' : 'warning',
  };
}

function timelineEventTime(event: MissionEvent): number {
  return event.at > 1e12 ? event.at : event.at * 1000;
}

function timelineEventOrder(event: MissionEvent): number {
  return event.id.startsWith('route-requested:') ? 0 : event.id.startsWith('route-completed:') ? 1 : 2;
}

const CommunicationPath = memo(function CommunicationPath({
  route,
  active,
  requested,
  degraded,
  latency,
  loss,
  forwarding,
  paused,
  evidenceCurrent,
  onInspect,
}: {
  route: CommunicationRoute;
  active: boolean;
  requested: boolean;
  degraded: boolean;
  latency?: number;
  loss?: number;
  forwarding: boolean;
  paused: boolean;
  evidenceCurrent: boolean;
  onInspect: (route: CommunicationRoute) => void;
}) {
  const packetsVisible = packetFlowEnabled(forwarding, evidenceCurrent) && loss != null && loss < 1;
  return (
    <g className={`mission-route mission-route--${route}${active ? ' mission-route--active' : ''}${requested ? ' mission-route--requested' : ''}${degraded ? ' mission-route--degraded' : ''}`}>
      <path className="mission-route__line" pathLength="100" />
      {packetsVisible && (
        <g>
          <path className="mission-route__packet-track" pathLength="100" />
          <path
            className={`mission-packets${degraded ? ' mission-packets--degraded' : ''}${paused ? ' mission-packets--paused' : ''}`}
            pathLength="100"
            style={{ '--packet-duration': `${Math.min(8, Math.max(0.6, 0.6 + (latency ?? 0) / 1000 * 12))}s` } as React.CSSProperties}
            aria-hidden="true"
          />
        </g>
      )}
      <path
        className="mission-route__hitarea"
        onClick={() => onInspect(route)}
        role="button"
        tabIndex={0}
        aria-label={`Inspect ${route} route`}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') onInspect(route);
        }}
      />
    </g>
  );
});

function DroneMarker({ drone, selected, route, evidenceCurrent, threatState, onSelect }: {
  drone: MissionFleetDrone;
  selected: boolean;
  route: MissionRoute | null;
  evidenceCurrent: boolean;
  threatState?: MissionSimulationState;
  onSelect: (id: string) => void;
}) {
  const label = drone.display_name || drone.drone_id.replaceAll('_', ' ');
  return (
    <g
      className={`mission-drone${selected ? ' mission-drone--selected' : ''}`}
      data-world-drone={drone.drone_id}
      role="button"
      tabIndex={0}
      aria-label={`${label}${selected ? ', selected UAV' : ''}`}
      aria-pressed={selected}
      onClick={() => onSelect(drone.drone_id)}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(drone.drone_id); }
      }}
    >
      <circle className="mission-drone__hitarea" r="34" />
      {selected && threatState && <AttackVisualization state={threatState} />}
      {selected && <circle className="mission-drone__selection" r="25" />}
      <g className="mission-drone__body" aria-hidden="true">
        <path d="M-10-8L10 8M-10 8L10-8M-5-3H5L10 0L5 3H-5Z" />
        <circle cx="-10" cy="-8" r="3.2" />
        <circle cx="10" cy="8" r="3.2" />
        <circle cx="-10" cy="8" r="3.2" />
        <circle cx="10" cy="-8" r="3.2" />
      </g>
      <text className="mission-drone__id" textAnchor="middle" y="41">D{drone.index}</text>
      <text className="mission-drone__status" textAnchor="middle" y="55">
        {selected
          ? `SELECTED · ${route ? `${evidenceCurrent ? '' : 'LAST: '}${ROUTE_LABELS[route]}` : 'Installation unknown'}`
          : route ? `LAST: ${ROUTE_LABELS[route]}` : 'Unknown'}
      </text>
    </g>
  );
}

function AttackVisualization({ state }: { state: MissionSimulationState }) {
  const profile = state.activeAttack;
  if (profile === 'none') return null;
  if (profile === 'gps_spoofing') {
    const drift = threatVisual(state, state.droneId).drift;
    if (drift !== null) return <g className="mission-svg-gps-cue" aria-hidden="true">
      <path className="mission-svg-gps-cue__link" d="M 9 -8 L 52 -43" />
      <path className="mission-svg-gps-cue__diamond" d="M 52 -51 L 60 -43 L 52 -35 L 44 -43 Z" />
      <text x="65" y="-39">REPORTED GPS ◇</text>
    </g>;
  }
  if (profile === 'spoofing' || profile === 'gps_spoofing' || profile === 'replay' || profile === 'dos' || profile === 'fhss') {
    const label = profile === 'gps_spoofing' ? 'GPS REFERENCE UNAVAILABLE'
      : profile === 'replay' ? 'REPLAYED TELEMETRY'
        : profile === 'dos' ? 'DOS REPORTED · COUNT UNMEASURED'
          : profile === 'fhss' ? 'FHSS SIMULATION' : 'TELEMETRY INTEGRITY';
    return (
      <g className="mission-svg-warning" aria-hidden="true">
        <rect x="26" y="-65" width="190" height="25" rx="5" />
        <text x="35" y="-49">{label}</text>
      </g>
    );
  }
  const wide = profile === 'barrage';
  const subtle = profile === 'smart' || profile === 'reactive' || profile === 'adaptive';
  return (
    <g className={`mission-svg-affected-area mission-svg-affected-area--${profile}${subtle ? ' mission-svg-affected-area--subtle' : ''}`} aria-hidden="true">
      <circle className="mission-svg-affected-area__fill" r={wide ? 100 : 61} />
      <circle className="mission-svg-affected-area__boundary" r={wide ? 100 : 61} />
    </g>
  );
}

function AirspaceRouteAnnotation({ state, evidenceCurrent, requestOutcomeUnknown }: { state: MissionSimulationState; evidenceCurrent: boolean; requestOutcomeUnknown: boolean }) {
  const isHold = state.installedRoute === 'hold';
  const holdRequested = !isHold && (state.requestedRoute === 'hold' || state.networkAction === 'hold');
  const routeRequested = !holdRequested && !isHold && state.requestedRoute != null && state.requestedRoute !== state.installedRoute;
  const containment = state.constraintReason === 'containment_required'
    || Boolean(state.containmentMode && state.containmentMode !== 'normal');
  const width = !evidenceCurrent ? 270 : isHold ? 218 : holdRequested || routeRequested ? 246 : state.sdnApplied === false ? 216 : 164;
  const height = isHold ? 112 : holdRequested || routeRequested ? 66 : 50;
  return (
    <g className={`mission-airspace-note${isHold || holdRequested ? ' mission-airspace-note--hold' : ''}`} transform="translate(24 54)">
      <rect width={width} height={height} rx="12" />
      <text className="mission-airspace-note__title" x="14" y="20">
        {!evidenceCurrent ? 'LAST SNAPSHOT · ROUTE STATE' : isHold ? state.sdnApplied === false ? 'HOLD LAST CONFIRMED' : 'FORWARDING SUSPENDED' : holdRequested ? 'HOLD REQUESTED · NOT INSTALLED' : routeRequested ? `${ROUTE_LABELS[state.requestedRoute!].toUpperCase()} REQUESTED · NOT INSTALLED` : state.installedRoute ? state.sdnApplied === false ? 'LAST CONFIRMED ROUTE' : 'INSTALLED ROUTE' : 'INSTALLATION UNKNOWN'}
      </text>
      {isHold ? (
        <>
          {state.routes.map((route, index) => (
            <g key={route.id} transform={`translate(14 ${39 + index * 17})`}>
              <text>{ROUTE_LABELS[route.id]}</text>
              <text x="178" textAnchor="end">{route.threatScore?.toFixed(2) ?? '—'}</text>
            </g>
          ))}
          <text className="mission-airspace-note__result" x="14" y="101">{!evidenceCurrent || state.sdnApplied === false ? 'HOLD last confirmed installed' : containment ? 'Containment policy → HOLD' : 'Safety gate → HOLD'}</text>
        </>
      ) : holdRequested || routeRequested ? (
        <>
          <text className="mission-airspace-note__value" x="14" y="39">{state.installedRoute ? `Last confirmed: ${ROUTE_LABELS[state.installedRoute]}` : 'Installation unknown'}</text>
          <text className="mission-airspace-note__result" x="14" y="56">{requestOutcomeUnknown ? 'Controller outcome unknown' : state.sdnError ? 'Controller update failed' : 'Controller apply not confirmed'}</text>
        </>
      ) : (
        <text className="mission-airspace-note__value" x="14" y="39">
          {state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Awaiting confirmation'}
        </text>
      )}
    </g>
  );
}

function AirspaceCanvas({
  state,
  drones,
  droneRoutes,
  selectedDroneId,
  paused,
  evidenceCurrent,
  requestOutcomeUnknown,
  onSelectDrone,
  onInspectRoute,
  onSelectAsset,
  worldClock,
}: {
  state: MissionSimulationState;
  drones: MissionFleetDrone[];
  droneRoutes: Record<string, MissionRoute | null>;
  selectedDroneId: string;
  paused: boolean;
  evidenceCurrent: boolean;
  requestOutcomeUnknown: boolean;
  onSelectDrone: (id: string) => void;
  onInspectRoute: (route: CommunicationRoute) => void;
  onSelectAsset: (asset: 'satellite' | 'base' | 'jammer') => void;
  worldClock: React.RefObject<VisualClock>;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  useSvgWorld(svgRef, worldClock, drones.map(drone => drone.drone_id), selectedDroneId, paused);
  const selectedEvidence = state.droneId === selectedDroneId && state.timestamp != null;
  const threat = selectedEvidence ? threatVisual(state, selectedDroneId) : null;
  const threatVisible = threat !== null && threat.profile !== 'none';
  const selectedInstalledRoute = selectedEvidence ? state.installedRoute ?? null : null;
  const visibleFlow = state.routes.some(route => {
    const routeState = networkRouteState(state, route.id, selectedDroneId);
    return packetFlowEnabled(routeState.forwarding, evidenceCurrent)
      && (routeState.metric?.packet_loss ?? 1) < 1;
  });
  const orderedDrones = [
    ...drones.filter(drone => drone.drone_id !== selectedDroneId),
    ...drones.filter(drone => drone.drone_id === selectedDroneId),
  ];

  return (
    <>
    <svg
      ref={svgRef}
      viewBox="0 0 900 440"
      className="mission-airspace"
      role="img"
      aria-label={`${selectedDroneId.replaceAll('_', ' ')} mission airspace. ${evidenceCurrent ? '' : 'Last snapshot. '}${state.installedRoute ? `${ROUTE_LABELS[state.installedRoute]} ${state.sdnApplied === false ? 'last confirmed' : evidenceCurrent ? 'installed' : 'last reported installed'}` : 'route installation unknown'}. Simulated patrol motion, not flight control. Network links are schematic; mesh peer identity is not measured.`}
    >
      <defs>
        <pattern id="mission-grid" width="52" height="52" patternUnits="userSpaceOnUse">
          <path d="M 52 0 L 0 0 0 52" className="mission-grid-line" />
        </pattern>
        <linearGradient id="mission-horizon" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#111927" />
          <stop offset="0.54" stopColor="#0c1119" />
          <stop offset="1" stopColor="#090c10" />
        </linearGradient>
      </defs>
      <rect width="900" height="470" fill="url(#mission-horizon)" />
      <rect width="900" height="470" fill="url(#mission-grid)" />
      <path className="mission-terrain" d="M0 386L110 348L190 370L290 338L360 369L470 329L560 366L660 334L760 370L900 322V470H0Z" />
      <text className="mission-coordinate" x="24" y="30">AIRSPACE / SIMULATED PATROL</text>
      <text className="mission-coordinate" x="876" y="30" textAnchor="end">NOT FLIGHT CONTROL</text>

      <path className="mission-patrol" aria-hidden="true" />
      <circle className="mission-waypoint" r="3" aria-hidden="true" />
      {drones.map(drone => <ellipse key={drone.drone_id} data-world-shadow={drone.drone_id} className="mission-drone-shadow" rx="10" ry="3" aria-hidden="true" />)}
      {state.routes.map(route => {
        const routeState = networkRouteState(state, route.id, selectedDroneId);
        return <CommunicationPath
          key={route.id}
          route={route.id}
          active={routeState.installed}
          requested={routeState.candidate}
          degraded={routeState.degraded}
          latency={routeState.metric?.latency}
          loss={routeState.metric?.packet_loss}
          forwarding={routeState.forwarding}
          paused={paused}
          evidenceCurrent={evidenceCurrent}
          onInspect={onInspectRoute}
        />;
      })}

      <g
        className="mission-satellite"
        role="button"
        tabIndex={0}
        aria-label="Inspect satellite relay"
        onClick={() => onSelectAsset('satellite')}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelectAsset('satellite'); }
        }}
      >
        <circle className="mission-asset-hitarea" r="30" />
        <path d="M-9-5L9 5M-14-9L-6-5L-10 2L-18-2ZM14 9L6 5L10-2L18 2ZM-4-4L4-9L9-1L1 4Z" />
        <path className="mission-satellite__signal" d="M-2 10Q0 16 6 18M3 7Q8 11 13 11" />
        <text textAnchor="middle" y="31">SAT RELAY · 1×</text>
      </g>

      <g
        className="mission-base"
        transform={`translate(${BASE.x} ${BASE.y})`}
        role="button"
        tabIndex={0}
        aria-label="Inspect base station and SDN controller"
        onClick={() => onSelectAsset('base')}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') onSelectAsset('base');
        }}
      >
        <circle className="mission-asset-hitarea" r="34" />
        <path d="M0-23L-14 18H14ZM-9 5H9M-6-7H6M-18 18H18" />
        <circle cx="0" cy="-25" r="3" />
        <text textAnchor="middle" y="38">GROUND CONTROL</text>
      </g>

      <AirspaceRouteAnnotation state={state} evidenceCurrent={evidenceCurrent} requestOutcomeUnknown={requestOutcomeUnknown} />

      {orderedDrones.map(drone => (
        <DroneMarker
          key={drone.drone_id}
          drone={drone}
          selected={drone.drone_id === selectedDroneId}
          route={drone.drone_id === selectedDroneId ? selectedInstalledRoute : droneRoutes[drone.drone_id] ?? null}
          evidenceCurrent={evidenceCurrent && state.sdnApplied !== false && drone.drone_id === selectedDroneId}
          threatState={drone.drone_id === selectedDroneId && threatVisible ? state : undefined}
          onSelect={onSelectDrone}
        />
      ))}
    </svg>
    {(threatVisible || visibleFlow || selectedInstalledRoute === 'mesh' || state.requestedRoute === 'mesh') &&
      <div className="mission-svg-scene-notes">
        {threatVisible && <button type="button" className="mission-svg-threat-note" onClick={() => onSelectAsset('jammer')}>{evidenceCurrent ? '' : 'Last snapshot · '}{threat.profile === 'dos' ? 'DoS scenario · traffic volume unmeasured' : threat.label}{threat.paths.length ? ` · Affected: ${threat.paths.join(', ')}` : ''}<span>{threat.profile === 'dos' ? 'No measured packet count or source location' : threat.sceneNote.replaceAll('\n', ' · ')}</span></button>}
        {selectedInstalledRoute === 'mesh' || state.requestedRoute === 'mesh' ? <span>Mesh neighborhood · peer hops unavailable</span> : null}
        {visibleFlow && <span>Metric-derived flow · not packet capture</span>}
      </div>}
    <span className="mission-world-label">Schematic 2D · simulated patrol · 1× · compressed orbit</span>
    </>
  );
}

function LogicalTopologyView({ state, droneName, evidenceCurrent, requestOutcomeUnknown }: { state: MissionSimulationState; droneName: string; evidenceCurrent: boolean; requestOutcomeUnknown: boolean }) {
  const branches: Array<{ route: CommunicationRoute; switchId: string; y: number }> = [
    { route: 'direct', switchId: 's2', y: 105 },
    { route: 'satellite', switchId: 's3', y: 200 },
    { route: 'mesh', switchId: 's4', y: 295 },
  ];
  return (
    <div className="mission-logical-view" role="img" aria-label={`${evidenceCurrent ? '' : 'Last snapshot of '}logical SDN topology for ${droneName}`}>
      <div className="mission-logical-view__legend">
        <span>Logical SDN topology</span>
        <span>{!evidenceCurrent ? 'Last snapshot · ' : ''}{state.installedRoute === 'hold' ? state.sdnApplied === false ? 'Last confirmed HOLD' : 'Installed HOLD' : state.installedRoute ? `${ROUTE_LABELS[state.installedRoute]} ${state.sdnApplied === false ? 'last confirmed' : 'installed'}` : 'Installation unknown'}{state.requestedRoute && state.requestedRoute !== state.installedRoute ? ` · ${ROUTE_LABELS[state.requestedRoute]} ${requestOutcomeUnknown ? 'outcome unknown' : state.sdnApplied === false ? 'not confirmed' : 'requested'}` : ''}</span>
      </div>
      <svg viewBox="0 0 900 400" aria-hidden="true">
        {branches.map(branch => {
          const route = state.routes.find(item => item.id === branch.route)!;
          const active = state.installedRoute === branch.route;
          const requested = state.requestedRoute === branch.route && !active;
          return (
            <g key={branch.route} className={`logical-branch${active ? ' logical-branch--active' : ''}${requested ? ' logical-branch--requested' : ''}${route.safe === false ? ' logical-branch--unsafe' : ''}`}>
              <path d={`M290 200 Q370 ${branch.y} 450 ${branch.y}`} />
              <path d={`M490 ${branch.y} Q565 ${branch.y} 635 200`} />
              <g className="logical-node" transform={`translate(470 ${branch.y})`}>
                <rect x="-28" y="-21" width="56" height="42" rx="8" />
                <text textAnchor="middle" y="-1">{branch.switchId}</text>
                <text className="logical-node__label" textAnchor="middle" y="35">{ROUTE_LABELS[branch.route]}</text>
                <text className="logical-node__health" textAnchor="middle" y="49">{routeHealth(route, state.noSafeRoute)}</text>
              </g>
            </g>
          );
        })}
        <path className="logical-trunk" d="M116 200H222M278 200H290M635 200H662M718 200H790" />
        <g className="logical-endpoint" transform="translate(88 200)">
          <rect x="-28" y="-24" width="56" height="48" rx="10" />
          <text textAnchor="middle" y="3">UAV</text>
          <text className="logical-node__label" textAnchor="middle" y="40">{droneName}</text>
        </g>
        <g className="logical-node logical-node--core" transform="translate(250 200)">
          <rect x="-28" y="-21" width="56" height="42" rx="8" />
          <text textAnchor="middle" y="4">s1</text>
          <text className="logical-node__label" textAnchor="middle" y="36">Ingress</text>
        </g>
        <g className="logical-node logical-node--core" transform="translate(690 200)">
          <rect x="-28" y="-21" width="56" height="42" rx="8" />
          <text textAnchor="middle" y="4">s5</text>
          <text className="logical-node__label" textAnchor="middle" y="36">Egress</text>
        </g>
        <g className="logical-endpoint" transform="translate(818 200)">
          <rect x="-28" y="-24" width="56" height="48" rx="10" />
          <text textAnchor="middle" y="3">BASE</text>
          <text className="logical-node__label" textAnchor="middle" y="40">Ground control</text>
        </g>
        {state.installedRoute === 'hold' && <text className="logical-hold-label" x="450" y="374" textAnchor="middle">{!evidenceCurrent ? 'LAST SNAPSHOT · HOLD REPORTED INSTALLED' : state.sdnApplied === false ? 'HOLD LAST CONFIRMED · NEW APPLY UNCONFIRMED' : 'FORWARDING SUSPENDED BY INSTALLED HOLD'}</text>}
        {state.installedRoute !== 'hold' && state.requestedRoute === 'hold' && <text className="logical-hold-label" x="450" y="374" textAnchor="middle">{evidenceCurrent ? 'HOLD REQUESTED · CONTROLLER APPLY NOT CONFIRMED' : 'LAST SNAPSHOT · HOLD REQUESTED'}</text>}
      </svg>
    </div>
  );
}

function MissionStatus({ state, evidence, recordedReplay }: { state: MissionSimulationState; evidence: MissionEvidenceStatus; recordedReplay: boolean }) {
  const status = threatPresentation(state.reportedThreatLevel ?? "UNKNOWN", state.containmentMode);
  const label = evidence.evidenceCurrent ? `${recordedReplay ? 'Recorded · ' : ''}${status.label}`
    : evidence.lastSnapshot ? `Last snapshot · ${status.label}` : 'Selected evidence unavailable';
  return (
    <div className={`mission-status mission-status--${evidence.evidenceCurrent ? status.tone : 'neutral'}`} role="status">
      <span className="mission-status__dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function MissionStateSummary({ state, clearedAttack, evidence, requestPending, recordedReplay }: {
  state: MissionSimulationState;
  clearedAttack: AttackProfile | null;
  evidence: MissionEvidenceStatus;
  requestPending: boolean;
  recordedReplay: boolean;
}) {
  const hasEvidence = evidence.evidenceCurrent || evidence.lastSnapshot;
  const network = !hasEvidence ? 'Unavailable' : state.containmentMode && state.containmentMode !== 'normal'
    ? 'Containment'
    : state.reportedThreatLevel === 'LOW' ? 'Low reported threat' : state.reportedThreatLevel === 'MEDIUM' ? 'Elevated' : state.reportedThreatLevel === 'HIGH' ? 'Critical' : 'Unavailable';
  const attack = !hasEvidence ? 'Unavailable' : state.activeAttack !== 'none'
    ? `${attackLabel(state.activeAttack)} · ${recordedReplay ? 'Recorded' : evidence.evidenceCurrent ? 'Active' : 'Reported'}`
    : clearedAttack ? `${attackLabel(clearedAttack)} · Cleared` : 'None';
  return (
    <dl className="mission-state-summary" aria-label={`${evidence.lastSnapshot ? 'Last snapshot ' : ''}mission state summary`}>
      <div><dt>{evidence.lastSnapshot ? 'Last threat' : 'Threat'}</dt><dd>{network}</dd></div>
      <div><dt>{evidence.lastSnapshot ? 'Last attack' : 'Attack'}</dt><dd>{attack}</dd></div>
      <div><dt>{requestPending || state.sdnApplied === false ? 'Last confirmed' : evidence.lastSnapshot ? 'Last installed' : 'Installed'}</dt><dd>{hasEvidence && state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Unknown'}</dd></div>
    </dl>
  );
}

function NetworkStateStrip({ state, evidenceCurrent }: { state: MissionSimulationState; evidenceCurrent: boolean }) {
  const appliedHold = state.installedRoute === 'hold';
  const requestedHold = state.requestedRoute === 'hold' || state.networkAction === 'hold';
  if (!appliedHold && !requestedHold) return null;
  const containment = state.containmentMode && state.containmentMode !== 'normal';
  return (
    <div className="mission-hold" role="status">
      <span className="mission-hold__mark" aria-hidden="true"><Pause /></span>
      <div className="mission-hold__copy">
        <strong>{!evidenceCurrent ? 'Last snapshot · ' : ''}{appliedHold ? state.sdnApplied === false ? 'HOLD last confirmed' : 'Network hold installed' : state.sdnError ? 'HOLD request failed' : 'HOLD requested · not installed'}</strong>
        <span>{appliedHold
          ? !evidenceCurrent ? 'HOLD was reported installed in the retained snapshot.' : state.sdnApplied === false ? 'HOLD remains the last confirmed route; the new controller apply was not confirmed.' : containment ? 'Forwarding suspended by installed containment policy.' : 'Forwarding suspended by installed HOLD.'
          : `Last installed route: ${state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'unknown'}. ${state.sdnError ? 'Controller update failed.' : 'Controller apply is not confirmed.'}`}</span>
      </div>
      <div className="mission-hold__scores">
        {state.routes.map(route => (
          <span key={route.id}>
            {ROUTE_LABELS[route.id]} {formatPercent(route.threatScore, 0)} {route.safe === false ? 'unsafe' : 'threat'}
          </span>
        ))}
      </div>
    </div>
  );
}

function SelectedDroneTelemetry({ state, inspectedRoute, evidence, requestPending, recordedReplay, onCollapse }: {
  state: MissionSimulationState;
  inspectedRoute: CommunicationRoute;
  evidence: MissionEvidenceStatus;
  requestPending: boolean;
  recordedReplay: boolean;
  onCollapse: () => void;
}) {
  const route = state.routes.find(item => item.id === inspectedRoute) || state.routes[0];
  const metric = route.metric;
  const primary: Array<[string, string]> = [
    [requestPending || state.sdnApplied === false ? 'Last confirmed route' : 'Installed route', state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Unknown'],
    ...telemetryThreatRows(ROUTE_LABELS[route.id], route.threatScore, state.reportedThreatLevel),
    ['PDR', formatPercent(metric?.pdr)],
    ['Latency', formatMetric(metric?.latency, 'ms')],
    ['Loss', formatPercent(metric?.packet_loss)],
  ];
  const secondary: Array<[string, string]> = [
    ['RSSI', formatMetric(metric?.rssi, 'dBm')],
    ['SINR', formatMetric(metric?.sinr, 'dB', 1)],
    ['FL confidence', formatPercent(state.flConfidence, 0)],
    ['Client trust', state.trust?.toFixed(2) ?? '—'],
    ['Insider risk', state.insiderRisk?.toFixed(2) ?? '—'],
  ];
  if (state.attackClass && state.attackClass !== 'none') secondary.push(['Detected class', titleCase(state.attackClass)]);
  if ((state.gps?.drift_m ?? 0) > 0) secondary.push(['GPS drift', formatMetric(state.gps?.drift_m, 'm', 1)]);
  return (
    <aside id="mission-live-telemetry" className="mission-telemetry" aria-label={`${evidence.lastSnapshot ? 'Last snapshot of ' : ''}${ROUTE_LABELS[inspectedRoute]} telemetry`}>
      <div className="mission-telemetry__header">
        <span>{recordedReplay ? 'Recorded frame · telemetry' : evidence.evidenceCurrent ? 'Selected telemetry · fresh' : evidence.lastSnapshot ? 'Last snapshot · telemetry' : 'Telemetry unavailable'}</span>
        <div className="mission-telemetry__header-actions">
          <span>{ROUTE_LABELS[inspectedRoute]}</span>
          <button
            type="button"
            className="mission-telemetry__collapse"
            onClick={onCollapse}
            aria-label="Collapse telemetry"
            title="Collapse telemetry"
          >
            <PanelRightClose aria-hidden="true" />
          </button>
        </div>
      </div>
      <dl className="mission-telemetry__list">
        {primary.map(([label, value], index) => (
          <div key={label} className={index < 3 ? 'mission-telemetry__primary' : ''}>
            <dt>{label}</dt><dd>{value}</dd>
          </div>
        ))}
      </dl>
      <details className="mission-telemetry__more">
        <summary><ChevronDown aria-hidden="true" /> More telemetry</summary>
        <dl>
          {secondary.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
      </details>
    </aside>
  );
}

function SimulationEventControl({
  profile,
  duration,
  activeAttack,
  droneName,
  enabled,
  onProfileChange,
  onDurationChange,
  onApply,
}: {
  profile: AttackProfile;
  duration: number;
  activeAttack: AttackProfile;
  droneName: string;
  enabled: boolean;
  onProfileChange: (profile: string) => void;
  onDurationChange: (duration: number) => void;
  onApply: (path: CommunicationRoute, profile: AttackProfile) => Promise<void>;
}) {
  const [target, setTarget] = useState<CommunicationRoute>('direct');
  const [pending, setPending] = useState(false);
  const profileIsPathless = ['none', 'barrage', 'sweep', 'gps_spoofing'].includes(profile);
  const profileLabel = ATTACK_OPTIONS.find(option => option.value === profile)?.label || attackLabel(profile);
  const description = profile === 'none'
    ? `Clear the active communication impairment for ${droneName}.`
    : profile === 'barrage'
      ? 'Barrage jamming will degrade all available communication paths.'
      : profile === 'sweep'
        ? 'Sweep jamming will move dynamically across communication paths.'
        : profile === 'gps_spoofing'
          ? `GPS spoofing will alter ${droneName} position telemetry.`
          : profile === 'dos'
            ? `DoS will congest the ${ROUTE_LABELS[target]} communication path.`
            : profile === 'replay' || profile === 'spoofing'
              ? `${profileLabel} will affect ${ROUTE_LABELS[target]} telemetry integrity.`
              : profile === 'fhss'
                ? `FHSS mitigation will exercise frequency hopping on ${ROUTE_LABELS[target]}.`
                : `${profileLabel} will impair the ${ROUTE_LABELS[target]} route for ${droneName}.`;
  const cta = profile === 'none'
    ? activeAttack === 'none' ? 'Return to normal' : 'Clear attack'
    : profile === 'spot' ? 'Inject spot jam'
    : profile === 'barrage' ? 'Start barrage jam'
    : profile === 'sweep' ? 'Start sweep jam'
    : profile === 'smart' ? 'Run smart jam'
    : profile === 'fhss' ? 'Run FHSS mitigation'
    : `Start ${profileLabel}`;
  const run = async () => {
    setPending(true);
    try { await onApply(target, profile); } finally { setPending(false); }
  };
  return (
    <div className="mission-event-control">
      <div className="mission-event-control__title">
        <span>Simulation event</span>
        <span>{activeAttack === 'none' ? 'Normal' : `${titleCase(activeAttack)} active`}</span>
      </div>
      <label>
        <span>Profile</span>
        <select value={profile} onChange={event => onProfileChange(event.target.value)} disabled={!enabled || pending}>
          {ATTACK_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      <label>
        <span>Target</span>
        <select value={target} onChange={event => setTarget(event.target.value as CommunicationRoute)} disabled={!enabled || pending || profileIsPathless}>
          {ROUTES.map(route => <option key={route} value={route}>{ROUTE_LABELS[route]}</option>)}
        </select>
      </label>
      <label className="mission-event-control__duration">
        <span>Duration · {duration}s</span>
        <input type="range" min="5" max="60" step="5" value={duration} onChange={event => onDurationChange(Number(event.target.value))} disabled={!enabled || pending || profile === 'none'} />
      </label>
      <p className="mission-event-control__context">{description}</p>
      <button type="button" onClick={run} disabled={!enabled || pending}>
        {pending ? 'Sending…' : cta}
      </button>
      {!enabled && <p>Attack controls are available in simulation mode only.</p>}
    </div>
  );
}

function EventTimeline({ events, evidenceCurrent, recordedReplay }: { events: MissionEvent[]; evidenceCurrent: boolean; recordedReplay: boolean }) {
  const visible = events.slice(-12);
  return (
    <div className="mission-timeline" aria-live={evidenceCurrent ? 'polite' : 'off'} aria-label="Latest mission events">
      <div className="mission-section-label">{recordedReplay ? 'Recorded event rail' : evidenceCurrent ? 'Event rail' : 'Last snapshot · events'}</div>
      <ol>
        {visible.map((event, index) => (
          <li key={event.id} className={`mission-timeline__event mission-timeline__event--${event.tone}${index === visible.length - 1 ? ' mission-timeline__event--latest' : ''}`}>
            <time>{eventTime(event.at)}</time><span>{event.label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function LiveMissionSimulation(props: LiveMissionSimulationProps) {
  const worldClock = useRef(createClock(createWorld([])));
  const sectionRef = useRef<HTMLElement>(null);
  const presentationButtonRef = useRef<HTMLButtonElement>(null);
  const viewBeforePresentation = useRef<'mission' | 'topology'>('mission');
  const [renderer, setRenderer] = useState(import.meta.env.VITE_MISSION_RENDERER === 'svg' ? 'svg' : 'three');
  const [nowMs, setNowMs] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const selectedPayload = props.payload?.type === 'telemetry' && props.payload.metrics.drone_id === props.activeDrone
    ? props.payload : null;
  const selectedCompletion = props.routeCompletion?.drone_id === props.activeDrone ? props.routeCompletion : null;
  const selectedRequest = props.routeRequest?.drone_id === props.activeDrone
    && props.routeRequest.transition_id !== selectedCompletion?.transition_id ? props.routeRequest : null;
  const requestEvidence = selectedRequest ? routeRequestEvidenceStatus(
    selectedRequest,
    props.routeRequestReceivedAtMs,
    props.transportStatus ?? 'disconnected',
    props.telemetryUnavailable,
    nowMs,
  ) : null;
  const evidence = props.recordedReplay ? recordedFrameEvidenceStatus(selectedPayload, selectedRequest)
    : requestEvidence ?? missionEvidenceStatus({
    payload: selectedPayload,
    selectedDroneId: props.activeDrone,
    transportStatus: props.transportStatus ?? 'disconnected',
    telemetryUnavailable: props.telemetryUnavailable,
    receivedAtMs: props.telemetryReceivedAtMs,
    nowMs,
    });
  const evidenceCurrent = evidence.evidenceCurrent;
  const canonicalState = useMemo(() => adaptMissionSimulationState(selectedPayload, {
    droneId: props.activeDrone,
    route: props.activePath,
    threatLevel: props.threatLevel,
    reward: props.reward,
    step: props.step,
    noSafeRoute: props.noSafeRoute,
    availablePaths: props.availablePaths,
    sdnReady: props.sdnReady,
    trust: props.trust,
    insiderRisk: props.insiderRisk,
    containmentMode: props.containmentMode,
  }), [
    props.activeDrone, props.activePath, props.availablePaths, props.containmentMode,
    props.insiderRisk, props.noSafeRoute, props.reward, props.sdnReady, selectedPayload,
    props.step, props.threatLevel, props.trust,
  ]);
  const state = useMemo(() => selectedRequest
    ? applyMissionRouteRequest(canonicalState, selectedRequest) : canonicalState,
  [canonicalState, selectedRequest]);
  const [paused, setPaused] = useState(false);
  const [presentation, setPresentation] = useState(false);
  const [viewMode, setViewMode] = useState<'mission' | 'topology'>('mission');
  const [telemetryOpen, setTelemetryOpen] = useState(true);
  const [inspectedRoute, setInspectedRoute] = useState<CommunicationRoute>(
    state.selectedRoute === 'hold' ? 'direct' : state.selectedRoute,
  );
  const [selectedAsset, setSelectedAsset] = useState<'satellite' | 'base' | 'jammer' | null>(null);
  const [routeNotice, setRouteNotice] = useState<{ from: MissionRoute; to: MissionRoute } | null>(null);
  const [clearedAttack, setClearedAttack] = useState<AttackProfile | null>(null);
  const [history, dispatchDecision] = useReducer(reduceDecisionHistory, undefined, () => emptyDecisionHistory(props.activeDrone));
  const selectedHistory = history.droneId === props.activeDrone ? history : null;
  const selectedTransitions = (props.routeTransitionHistory ?? [props.routeRequest, props.routeCompletion].filter(
    (transition): transition is MissionRouteTransition => transition != null,
  )).filter(transition => transition.drone_id === props.activeDrone);
  const completionTimes = selectedTransitions.filter(
    (transition): transition is MissionRouteCompletion => transition.phase === 'completed',
  );
  const canonicalEvents = selectedHistory?.events.filter(event => !props.recordedReplay || completionTimes.some(
    item => event.id.startsWith(`${props.activeDrone}:${item.event.event_id}:`),
  )) ?? [];
  const timedCanonicalEvents = canonicalEvents.map(event => {
    const completion = completionTimes.find(item => event.id.startsWith(`${props.activeDrone}:${item.event.event_id}:`));
    return completion ? { ...event, at: completion.occurred_at } : event;
  });
  const events = [...timedCanonicalEvents, ...selectedTransitions.map(transitionRailEvent)].sort((a, b) =>
    timelineEventTime(a) - timelineEventTime(b) || timelineEventOrder(a) - timelineEventOrder(b));
  const previous = useRef<MissionSimulationState | null>(null);

  function exitPresentation() {
    setPresentation(false);
    setViewMode(viewBeforePresentation.current);
    window.requestAnimationFrame(() => presentationButtonRef.current?.focus());
  }

  function togglePresentation() {
    if (presentation) {
      exitPresentation();
      return;
    }
    viewBeforePresentation.current = viewMode;
    setViewMode('mission');
    setSelectedAsset(null);
    setPresentation(true);
  }

  useEffect(() => {
    if (!presentation) return;
    const bodyOverflow = document.body.style.overflow;
    const documentOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setPresentation(false);
        setViewMode(viewBeforePresentation.current);
        window.requestAnimationFrame(() => presentationButtonRef.current?.focus());
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(sectionRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
      ) ?? []).filter(element => !element.hasAttribute('disabled') && element.getClientRects().length > 0);
      if (focusable.length === 0) return;
      const current = focusable.indexOf(document.activeElement as HTMLElement);
      if (event.shiftKey && current <= 0) {
        event.preventDefault();
        focusable[focusable.length - 1].focus();
      } else if (!event.shiftKey && (current === -1 || current === focusable.length - 1)) {
        event.preventDefault();
        focusable[0].focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = bodyOverflow;
      document.documentElement.style.overflow = documentOverflow;
    };
  }, [presentation]);

  useEffect(() => {
    if (state.selectedRoute !== 'hold') setInspectedRoute(state.selectedRoute);
  }, [state.droneId, state.selectedRoute]);

  useEffect(() => {
    dispatchDecision({ state: canonicalState, selectedId: props.activeDrone });
    const prior = previous.current;
    const replayRewind = props.recordedReplay && prior?.timestamp != null
      && (canonicalState.timestamp == null || canonicalState.timestamp < prior.timestamp);
    if (!prior || prior.droneId !== props.activeDrone || canonicalState.droneId !== props.activeDrone || replayRewind) {
      setRouteNotice(null); setClearedAttack(null); setSelectedAsset(null);
    } else {
      if (prior.activeAttack !== 'none' && canonicalState.activeAttack === 'none') setClearedAttack(prior.activeAttack);
      if (canonicalState.activeAttack !== 'none') setClearedAttack(null);
      const canonicalApplied = selectedPayload?.event?.event_id != null
        && selectedPayload.event.sdn?.applied === true
        && selectedPayload.event.decision?.installed_path === canonicalState.installedRoute;
      if (canonicalApplied && canonicalState.installedRoute != null && prior.installedRoute != null
        && canonicalState.installedRoute !== prior.installedRoute) {
        setRouteNotice({from: prior.installedRoute,to: canonicalState.installedRoute});
      }
    }
    previous.current = canonicalState;
  }, [canonicalState, selectedPayload, props.activeDrone, props.recordedReplay]);

  useEffect(() => {
    if (!routeNotice) return;
    const timer = window.setTimeout(() => setRouteNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [routeNotice]);

  useEffect(() => {
    if (!clearedAttack) return;
    const timer = window.setTimeout(() => setClearedAttack(null), 5000);
    return () => window.clearTimeout(timer);
  }, [clearedAttack]);

  const activeDroneName = props.fleetDrones.find(drone => drone.drone_id === props.activeDrone)?.display_name
    || titleCase(props.activeDrone);
  const droneRoutes = useMemo(() => Object.fromEntries(
    props.fleetDrones.map(drone => [drone.drone_id, routeFromPayload(props.swarmPayloads[drone.drone_id], drone.drone_id)]),
  ), [props.fleetDrones, props.swarmPayloads]);
  const inspectedState = state.routes.find(route => route.id === inspectedRoute) as MissionRouteState | undefined;
  const selectedAssetCopy = selectedAsset === 'base'
    ? `Controller · ${props.sdnController} · ${selectedRequest ? 'request awaiting response' : state.sdnApplied ? 'route applied' : 'awaiting route state'}`
    : selectedAsset === 'satellite'
      ? `Simulated circular orbit · ${(MISSION_SATELLITE_ORBIT.config.altitudeM / 1000).toFixed(0)} km altitude · ${(MISSION_SATELLITE_ORBIT.config.inclinationRad * 180 / Math.PI).toFixed(0)}° inclination · ${(MISSION_SATELLITE_ORBIT.periodSeconds / 60).toFixed(1)} min period · 1× time · compressed sky position, not observed coverage · Link latency ${formatMetric(state.routes.find(route => route.id === 'satellite')?.metric?.latency, 'ms')}`
      : selectedAsset === 'jammer'
        ? `${threatVisual(state, props.activeDrone).label} · ${attackTarget(state)} · Visual regions are schematic; no measured source location. Physical UAV motion is unchanged.`
        : null;
  const canonicalKey = decisionEventKey(canonicalState);
  const historyMatchesFrame = canonicalKey != null && canonicalKey === decisionEventKey(selectedHistory?.last ?? canonicalState)
    && selectedHistory?.last != null;
  const priorForFrame = selectedHistory?.last && selectedHistory.last.timestamp != null
    && canonicalState.timestamp != null && selectedHistory.last.timestamp <= canonicalState.timestamp
    ? selectedHistory.last : null;
  const missionPhase = canonicalState.droneId !== props.activeDrone ? 'awaiting-evidence'
    : historyMatchesFrame ? selectedHistory!.phase : decisionPhase(canonicalState, priorForFrame, props.activeDrone);
  const snapshotStep = selectedPayload?.event?.step ?? selectedPayload?.decision.step ?? -1;
  const completionRepresentedOrSuperseded = Boolean(selectedCompletion && selectedPayload && (
    selectedCompletion.event.event_id === selectedPayload.event?.event_id
    || (snapshotStep >= selectedCompletion.step && selectedPayload.timestamp >= selectedCompletion.event.timestamp)
  ));
  const controllerProgress: 'awaiting' | 'outcome-unknown' | 'acknowledged' | 'not-confirmed' | null = selectedRequest
    ? evidenceCurrent ? 'awaiting' : 'outcome-unknown'
    : selectedCompletion && !completionRepresentedOrSuperseded
      ? selectedCompletion.controller.applied ? 'acknowledged' : 'not-confirmed'
      : null;
  const visiblePhase: DecisionPhase = selectedRequest
    ? evidenceCurrent ? 'request-not-installed' : 'awaiting-evidence'
    : controllerProgress === 'not-confirmed' ? 'apply-failed' : missionPhase;
  const snapshotAvailable = evidenceCurrent || evidence.lastSnapshot;
  const installedSummary = snapshotAvailable && state.droneId === props.activeDrone && state.installedRoute
    ? ` · ${selectedRequest || state.sdnApplied === false ? 'Last confirmed' : evidenceCurrent ? 'Installed' : 'Last installed'}: ${ROUTE_LABELS[state.installedRoute]}` : '';
  const safetyHoldApplied = props.telemetrySafetyState?.status === 'hold_applied'
    && props.telemetrySafetyState.applied === true;
  const transitionHeading = selectedRequest
    ? evidenceCurrent ? `${ROUTE_LABELS[selectedRequest.decision.requested_path]} requested` : 'Request outcome unknown'
    : controllerProgress === 'acknowledged' ? 'Controller acknowledged request'
      : controllerProgress === 'not-confirmed' ? 'Controller did not confirm route' : null;
  const transitionDetail = selectedRequest
    ? `${state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Unknown'} last confirmed · ${evidenceCurrent ? 'Awaiting controller response' : 'Request evidence is stale; installation is unconfirmed'}`
    : selectedCompletion && !completionRepresentedOrSuperseded
      ? `${ROUTE_LABELS[selectedCompletion.event.decision.requested_path]} requested · canonical controller result pending presentation`
      : null;

  return (
    <section ref={sectionRef} className="content-section mission-simulation" data-paused={paused} data-phase={visiblePhase} data-evidence-current={evidenceCurrent} data-presentation={presentation} data-recorded-replay={Boolean(props.recordedReplay)} aria-labelledby="mission-title">
      <header className="mission-simulation__header">
        <div className="mission-simulation__identity">
          <div className="mission-eyebrow"><span>FLARE / Mission network</span><span>{props.recordedReplay ? 'RECORDED CONTROLLED SIMULATION · MOCK SDN' : props.runtimeMode?.toUpperCase() || 'MODE UNKNOWN'}</span><span>{sourceLabel(state.source)}</span><span>Controller · {props.sdnController}</span></div>
          <h2 id="mission-title">{props.recordedReplay ? 'Recorded Mission Network' : 'Live Mission Network'}</h2>
          <p className="mission-action-summary">{activeDroneName} · {selectedRequest ? transitionHeading : evidenceCurrent ? PHASE_LABELS[visiblePhase] : evidence.lastSnapshot ? `Last snapshot · ${PHASE_LABELS[visiblePhase]}` : evidence.label}{installedSummary}</p>
        </div>
        <div className="mission-simulation__header-actions">
          <span className={`mission-evidence-status mission-evidence-status--${evidence.kind}`} role="status">{evidence.label}</span>
          <MissionStatus state={state} evidence={evidence} recordedReplay={Boolean(props.recordedReplay)} />
          <button type="button" className="mission-pause" onClick={() => setPaused(current => !current)} aria-pressed={paused}>
            {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            {paused ? 'Resume visuals' : 'Pause visuals'}
          </button>
          <button ref={presentationButtonRef} type="button" className="mission-presentation-button" onClick={togglePresentation} aria-pressed={presentation}>
            {presentation ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
            {presentation ? 'Exit presentation' : 'Presentation mode'}
          </button>
        </div>
      </header>

      {props.telemetryUnavailable && (
        <div className="mission-operational-safety" role="alert">
          <strong>Telemetry unavailable · operational safety</strong>
          <span>{props.telemetryUnavailable} · {safetyHoldApplied ? 'HOLD controller reported applied' : 'HOLD requested / installation unconfirmed or failed'}</span>
        </div>
      )}

      <div className="mission-context-bar">
        <MissionStateSummary state={state} clearedAttack={clearedAttack} evidence={evidence} requestPending={Boolean(selectedRequest)} recordedReplay={Boolean(props.recordedReplay)} />
        <label className="mission-presentation-fleet">
          <span>Selected UAV</span>
          <select value={props.activeDrone} onChange={event => { props.onSelectDrone(event.target.value); setTelemetryOpen(true); }}>
            {!props.fleetDrones.some(drone => drone.drone_id === props.activeDrone) && <option value={props.activeDrone}>{activeDroneName}</option>}
            {props.fleetDrones.map(drone => <option key={drone.drone_id} value={drone.drone_id}>{drone.display_name || drone.drone_id.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <div className="mission-view-toggle" role="group" aria-label="Mission visualization">
          <button type="button" className={viewMode === 'mission' ? 'is-active' : ''} aria-pressed={viewMode === 'mission'} onClick={() => setViewMode('mission')}>World</button>
          <button type="button" className={viewMode === 'topology' ? 'is-active' : ''} aria-pressed={viewMode === 'topology'} onClick={() => setViewMode('topology')} title="Logical network topology">Topology</button>
        </div>
      </div>

      {(props.recordedReplay || !evidenceCurrent) && <div className="mission-evidence-banner" role="status">{props.recordedReplay ? 'RECORDED CONTROLLED SIMULATION · MOCK SDN · Metric-based visual replay' : evidence.lastSnapshot ? 'Last snapshot · Retained mission and network evidence' : evidence.label}</div>}
      {transitionHeading && transitionDetail && <div className={`mission-route-transition mission-route-transition--${controllerProgress ?? 'awaiting'}`} role="status"><strong>{transitionHeading}</strong><span>{transitionDetail}</span></div>}

      <div className="mission-stage" data-world-renderer={viewMode === 'mission' ? renderer : 'topology'} data-quality={viewMode === 'mission' && renderer === 'svg' ? 'fallback' : undefined} data-telemetry-open={telemetryOpen}>
        <div className="mission-stage__canvas">
          {viewMode === 'mission' && renderer === 'svg' && <div className="mission-renderer-fallback" role="status"><strong>FALLBACK · SCHEMATIC 2D</strong><span>Simulation and route evidence retained</span><button type="button" onClick={() => setRenderer('three')}>Try 3D view</button></div>}
          {viewMode === 'mission' && renderer === 'three' ? (
            <WorldRenderBoundary onFallback={() => setRenderer('svg')}><Suspense fallback={<div className="mission-world-loading">Loading mission world…</div>}>
              <MissionWorldCanvas state={state} worldClock={worldClock} drones={props.fleetDrones} selectedDroneId={props.activeDrone} paused={paused} evidenceCurrent={evidenceCurrent} onDismiss={() => { setSelectedAsset(null); setTelemetryOpen(false); }} onSelectDrone={id => { props.onSelectDrone(id); setTelemetryOpen(true); setSelectedAsset(null); }} onSelectAsset={setSelectedAsset} onFallback={() => setRenderer('svg')} />
            </Suspense></WorldRenderBoundary>
          ) : viewMode === 'mission' ? (
            <AirspaceCanvas
              state={state}
              worldClock={worldClock}
              drones={props.fleetDrones}
              droneRoutes={droneRoutes}
              selectedDroneId={props.activeDrone}
              paused={paused}
              evidenceCurrent={evidenceCurrent}
              requestOutcomeUnknown={controllerProgress === 'outcome-unknown'}
              onSelectDrone={props.onSelectDrone}
              onInspectRoute={setInspectedRoute}
              onSelectAsset={setSelectedAsset}
            />
          ) : <LogicalTopologyView state={state} droneName={activeDroneName} evidenceCurrent={evidenceCurrent} requestOutcomeUnknown={controllerProgress === 'outcome-unknown'} />}
          {evidenceCurrent && routeNotice && routeNotice.to === state.installedRoute && state.sdnApplied === true && (
            <div className="mission-route-notice" role="status">
              <span>Route updated</span>
              <strong>{ROUTE_LABELS[routeNotice.from]} → {ROUTE_LABELS[routeNotice.to]}</strong>
              <small>{props.recordedReplay || props.sdnController.toLowerCase().includes('mock')
                ? state.installedRoute === 'hold' ? 'Mock SDN HOLD acknowledged' : 'Mock SDN route acknowledged'
                : state.installedRoute === 'hold' ? 'Controller reported HOLD applied' : 'Controller reported route applied'}</small>
            </div>
          )}
          {selectedAssetCopy && (
            <button type="button" className="mission-asset-popover" onClick={() => setSelectedAsset(null)} aria-label="Close asset details">
              <strong>{selectedAsset === 'jammer' ? 'Threat details' : selectedAsset ? titleCase(selectedAsset) : ''}</strong><span>{evidenceCurrent ? '' : 'Last snapshot · '}{selectedAssetCopy}</span>
            </button>
          )}
          {inspectedState?.jammed && <div className="mission-path-warning"><AlertTriangle aria-hidden="true" /> {evidenceCurrent ? '' : 'Last snapshot · '}{titleCase(inspectedRoute)} path degraded</div>}
        </div>
        {viewMode === 'mission' && telemetryOpen && (
          <SelectedDroneTelemetry
            state={state}
            inspectedRoute={inspectedRoute}
            evidence={evidence}
            requestPending={Boolean(selectedRequest)}
            recordedReplay={Boolean(props.recordedReplay)}
            onCollapse={() => setTelemetryOpen(false)}
          />
        )}
        {viewMode === 'mission' && !telemetryOpen && (
          <button
            type="button"
            className="mission-telemetry-toggle"
            onClick={() => setTelemetryOpen(true)}
            aria-expanded="false"
            title="Show telemetry"
            data-testid="show-live-telemetry"
          >
            <PanelRightOpen aria-hidden="true" />
            <span>Show telemetry</span>
          </button>
        )}
      </div>

      <RouteSelector state={state} inspectedRoute={inspectedRoute} evidence={evidence} onInspect={setInspectedRoute} />
      <NetworkStateStrip state={state} evidenceCurrent={evidenceCurrent} />

      <DecisionPanel state={state.droneId === props.activeDrone ? state : adaptMissionSimulationState(null, { droneId: props.activeDrone, route: "direct", threatLevel: "UNKNOWN", reward: 0, step: 0, noSafeRoute: false, availablePaths: [] })} phase={visiblePhase} controller={props.sdnController} evidenceCurrent={evidenceCurrent} lastSnapshot={evidence.lastSnapshot} controllerProgress={controllerProgress} recordedReplay={Boolean(props.recordedReplay)} />

      {(!props.recordedReplay || state.explanation) && <div className={`mission-lower-grid${props.recordedReplay ? ' mission-lower-grid--recorded' : ''}`}>
        <div className="mission-evidence-wrap"><DecisionEvidence state={state} evidenceCurrent={evidenceCurrent} /></div>
        {!props.recordedReplay && <SimulationEventControl
          profile={(ATTACK_OPTIONS.some(option => option.value === props.jamProfile) ? props.jamProfile : 'spot') as AttackProfile}
          duration={props.jamDuration}
          activeAttack={state.activeAttack}
          droneName={activeDroneName}
          enabled={props.attackSimulationEnabled}
          onProfileChange={props.onJamProfileChange}
          onDurationChange={props.onJamDurationChange}
          onApply={props.onApplyAttack}
        />}
      </div>}

      <EventTimeline events={events} evidenceCurrent={evidenceCurrent} recordedReplay={Boolean(props.recordedReplay)} />
    </section>
  );
}
