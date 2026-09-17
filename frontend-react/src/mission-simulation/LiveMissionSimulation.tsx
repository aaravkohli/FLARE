import DecisionPanel from './DecisionPanel';
import { emptyDecisionHistory, reduceDecisionHistory, PHASE_LABELS } from './world/decisionPresentation';
import type { DecisionEventItem as MissionEvent } from './world/decisionPresentation';
import { threatVisual } from './world/threatState';
import React, { lazy, Suspense, memo, useReducer, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  Pause,
  PanelRightClose,
  PanelRightOpen,
  Play,
} from 'lucide-react';
import { adaptMissionSimulationState } from './adapter';
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
import { BASE } from './world/projection';
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

const TELEMETRY_LABELS: Record<string, string> = {
  rssi: 'RSSI',
  pdr: 'PDR',
  sinr: 'SINR',
  latency: 'Latency',
  packet_loss: 'Packet loss',
};

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

function routeFromPayload(payload?: MissionTelemetryPayload): MissionRoute {
  const value = payload?.event?.decision?.installed_path
    || payload?.decision.path_name
    || payload?.event?.decision?.requested_path;
  return value === 'satellite' || value === 'mesh' || value === 'hold' ? value : 'direct';
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

const CommunicationPath = memo(function CommunicationPath({
  route,
  active,
  degraded,
  latency,
  paused,
  onInspect,
}: {
  route: CommunicationRoute;
  active: boolean;
  degraded: boolean;
  latency?: number;
  paused: boolean;
  onInspect: (route: CommunicationRoute) => void;
}) {
  return (
    <g className={`mission-route mission-route--${route}${active ? ' mission-route--active' : ''}${degraded ? ' mission-route--degraded' : ''}`}>
      <path className="mission-route__line" pathLength="100" />
      {active && (
        <g>
          <path className="mission-route__packet-track" pathLength="100" />
          <path
            className={`mission-packets${degraded ? ' mission-packets--degraded' : ''}${paused ? ' mission-packets--paused' : ''}`}
            pathLength="100"
            style={{ '--packet-duration': `${latency === undefined ? 2.8 : Math.min(5.8, Math.max(1.5, latency / 105))}s` } as React.CSSProperties}
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

function DroneMarker({ drone, selected, route, onSelect }: {
  drone: MissionFleetDrone;
  selected: boolean;
  route: MissionRoute;
  onSelect: (id: string) => void;
}) {
  const label = drone.display_name || drone.drone_id.replaceAll('_', ' ');
  return (
    <g
      className={`mission-drone${selected ? ' mission-drone--selected' : ''}`}
      data-world-drone={drone.drone_id}
      role="button"
      tabIndex={0}
      aria-label={`${label}${selected ? ', selected and active' : ''}`}
      onClick={() => onSelect(drone.drone_id)}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(drone.drone_id); }
      }}
    >
      <circle className="mission-drone__hitarea" r="34" />
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
        {selected ? `ACTIVE · ${ROUTE_LABELS[route]}` : ROUTE_LABELS[route]}
      </text>
    </g>
  );
}

function AttackVisualization({ state }: { state: MissionSimulationState }) {
  const profile = state.activeAttack;
  if (profile === 'none') return null;
  const isTelemetryAttack = profile === 'spoofing' || profile === 'gps_spoofing' || profile === 'replay';
  if (isTelemetryAttack) {
    return (
      <g className="mission-anomaly">
        <rect x="-8" y="-15" width="150" height="30" rx="8" />
        <text x="6" y="4">{profile === 'gps_spoofing' ? 'GPS INTEGRITY WARNING' : profile === 'replay' ? 'STALE TELEMETRY WARNING' : 'SIGNAL INTEGRITY WARNING'}</text>
      </g>
    );
  }
  if (profile === 'dos') {
    return (
      <g className="mission-congestion" transform="translate(170 350)">
        {Array.from({ length: 11 }, (_, index) => (
          <circle key={index} cx={index * 13} cy={(index % 3) * 8} r="2.2" />
        ))}
        <text x="0" y="38">NETWORK CONGESTION</text>
      </g>
    );
  }
  const wide = profile === 'barrage';
  const subtle = profile === 'smart' || profile === 'fhss';
  return (
    <g className={`mission-jammer mission-jammer--${profile}${subtle ? ' mission-jammer--subtle' : ''}`} transform={wide ? 'translate(430 246)' : 'translate(244 236)'}>
      <circle className="mission-jammer__source" r="6" />
      <circle className="mission-jammer__ring mission-jammer__ring--one" r={wide ? 62 : 34} />
      <circle className="mission-jammer__ring mission-jammer__ring--two" r={wide ? 106 : 58} />
      <circle className="mission-jammer__ring mission-jammer__ring--three" r={wide ? 154 : 82} />
      <text x="12" y="-12">{titleCase(profile)}</text>
    </g>
  );
}

function AirspaceRouteAnnotation({ state }: { state: MissionSimulationState }) {
  const isHold = state.selectedRoute === 'hold';
  const containment = state.constraintReason === 'containment_required'
    || Boolean(state.containmentMode && state.containmentMode !== 'normal');
  const width = isHold ? 218 : 164;
  const height = isHold ? 112 : 50;
  return (
    <g className={`mission-airspace-note${isHold ? ' mission-airspace-note--hold' : ''}`} transform="translate(24 54)">
      <rect width={width} height={height} rx="12" />
      <text className="mission-airspace-note__title" x="14" y="20">
        {isHold ? (containment ? 'FORWARDING SUSPENDED' : 'NO SAFE FORWARDING PATH') : state.installedRoute ? 'INSTALLED ROUTE' : 'INSTALLATION UNKNOWN'}
      </text>
      {isHold ? (
        <>
          {state.routes.map((route, index) => (
            <g key={route.id} transform={`translate(14 ${39 + index * 17})`}>
              <text>{ROUTE_LABELS[route.id]}</text>
              <text x="178" textAnchor="end">{route.threatScore?.toFixed(2) ?? '—'}</text>
            </g>
          ))}
          <text className="mission-airspace-note__result" x="14" y="101">{containment ? 'Containment policy → HOLD' : 'Safety gate → HOLD'}</text>
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
  onSelectDrone,
  onInspectRoute,
  onSelectAsset,
  worldClock,
}: {
  state: MissionSimulationState;
  drones: MissionFleetDrone[];
  droneRoutes: Record<string, MissionRoute>;
  selectedDroneId: string;
  paused: boolean;
  onSelectDrone: (id: string) => void;
  onInspectRoute: (route: CommunicationRoute) => void;
  onSelectAsset: (asset: 'satellite' | 'base' | 'jammer') => void;
  worldClock: React.RefObject<VisualClock>;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  useSvgWorld(svgRef, worldClock, drones.map(drone => drone.drone_id), selectedDroneId, paused);

  return (
    <>
    <svg
      ref={svgRef}
      viewBox="0 0 900 440"
      className="mission-airspace"
      role="img"
      aria-label={`${selectedDroneId.replaceAll('_', ' ')} mission airspace. ${ROUTE_LABELS[state.selectedRoute]} communication route active. Simulated patrol motion, not flight control. Network links are schematic; mesh peer identity is not measured.`}
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
      {state.routes.map(route => (
        <CommunicationPath
          key={route.id}
          route={route.id}
          active={state.installedRoute === route.id && state.selectedRoute !== 'hold' && state.networkAction !== 'hold'}
          degraded={route.jammed || (route.active && (route.metric?.packet_loss ?? 0) > 0.2)}
          latency={route.metric?.latency}
          paused={paused}
          onInspect={onInspectRoute}
        />
      ))}

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

      {state.activeAttack !== 'none' && (
        <g
          role="button"
          tabIndex={0}
          aria-label={`Inspect ${titleCase(state.activeAttack)} simulation event`}
          onClick={() => onSelectAsset('jammer')}
          onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') onSelectAsset('jammer');
          }}
        >
          <AttackVisualization state={state} />
        </g>
      )}

      <AirspaceRouteAnnotation state={state} />

      {drones.map(drone => (
        <DroneMarker
          key={drone.drone_id}
          drone={drone}
          selected={drone.drone_id === selectedDroneId}
          route={droneRoutes[drone.drone_id] || 'direct'}
          onSelect={onSelectDrone}
        />
      ))}
    </svg>
    <span className="mission-world-label">Simulation time · 1× · compressed orbital scale · Mesh hops unavailable</span>
    </>
  );
}

function LogicalTopologyView({ state, droneName }: { state: MissionSimulationState; droneName: string }) {
  const branches: Array<{ route: CommunicationRoute; switchId: string; y: number }> = [
    { route: 'direct', switchId: 's2', y: 105 },
    { route: 'satellite', switchId: 's3', y: 200 },
    { route: 'mesh', switchId: 's4', y: 295 },
  ];
  return (
    <div className="mission-logical-view" role="img" aria-label={`Logical SDN topology for ${droneName}`}>
      <div className="mission-logical-view__legend">
        <span>Logical SDN topology</span>
        <span>{state.selectedRoute === 'hold' ? 'Data plane suspended' : `${ROUTE_LABELS[state.selectedRoute]} installed`}</span>
      </div>
      <svg viewBox="0 0 900 400" aria-hidden="true">
        {branches.map(branch => {
          const route = state.routes.find(item => item.id === branch.route)!;
          const active = state.selectedRoute === branch.route;
          return (
            <g key={branch.route} className={`logical-branch${active ? ' logical-branch--active' : ''}${route.safe === false ? ' logical-branch--unsafe' : ''}`}>
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
        {state.selectedRoute === 'hold' && <text className="logical-hold-label" x="450" y="374" textAnchor="middle">FORWARDING SUSPENDED BY SAFETY POLICY</text>}
      </svg>
    </div>
  );
}

function MissionStatus({ state }: { state: MissionSimulationState }) {
  const status = threatPresentation(state.reportedThreatLevel ?? "UNKNOWN", state.containmentMode);
  return (
    <div className={`mission-status mission-status--${status.tone}`} role="status">
      <span className="mission-status__dot" aria-hidden="true" />
      <span>{status.label}</span>
    </div>
  );
}

function MissionStateSummary({ state, clearedAttack }: {
  state: MissionSimulationState;
  clearedAttack: AttackProfile | null;
}) {
  const network = state.containmentMode && state.containmentMode !== 'normal'
    ? 'Containment'
    : state.reportedThreatLevel === 'LOW' ? 'Low reported threat' : state.reportedThreatLevel === 'MEDIUM' ? 'Elevated' : state.reportedThreatLevel === 'HIGH' ? 'Critical' : 'Unavailable';
  const attack = state.activeAttack !== 'none'
    ? `${attackLabel(state.activeAttack)} · Active`
    : clearedAttack ? `${attackLabel(clearedAttack)} · Cleared` : 'None';
  return (
    <dl className="mission-state-summary" aria-label="Mission state summary">
      <div><dt>Network</dt><dd>{network}</dd></div>
      <div><dt>Attack</dt><dd>{attack}</dd></div>
      <div><dt>Installed</dt><dd>{state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Unknown'}</dd></div>
    </dl>
  );
}

function RouteSelector({ state, inspectedRoute, onInspect }: {
  state: MissionSimulationState;
  inspectedRoute: CommunicationRoute;
  onInspect: (route: CommunicationRoute) => void;
}) {
  return (
    <div className="mission-route-strip" aria-label="Communication routes">
      {state.routes.map(route => (
        <button
          type="button"
          key={route.id}
          className={`mission-route-option${route.active ? ' mission-route-option--active' : ''}${inspectedRoute === route.id ? ' mission-route-option--inspected' : ''}`}
          onClick={() => onInspect(route.id)}
          aria-pressed={inspectedRoute === route.id}
          disabled={!route.available}
        >
          <span className="mission-route-option__name">{ROUTE_LABELS[route.id]}</span>
          <span className="mission-route-option__latency">{formatMetric(route.metric?.latency, 'ms')}</span>
          <span className="mission-route-option__threat">Threat {formatPercent(route.threatScore, 0)}</span>
          <span className={`mission-route-option__state mission-route-option__state--${routeHealth(route, state.noSafeRoute).toLowerCase()}`}>
            {route.active && state.selectedRoute !== 'hold' ? '● ' : ''}{routeHealth(route, state.noSafeRoute)}
          </span>
        </button>
      ))}
    </div>
  );
}

function NetworkStateStrip({ state }: { state: MissionSimulationState }) {
  if (state.installedRoute !== 'hold' && state.networkAction !== 'hold') return null;
  const containment = state.containmentMode && state.containmentMode !== 'normal';
  return (
    <div className="mission-hold" role="status">
      <span className="mission-hold__mark" aria-hidden="true"><Pause /></span>
      <div className="mission-hold__copy">
        <strong>{state.installedRoute === 'hold' ? 'Network hold' : 'HOLD requested · apply unconfirmed'}</strong>
        <span>{containment ? 'Forwarding suspended by containment policy.' : state.noSafeRoute ? 'No safe communication route reported. Visual forwarding suspended.' : 'Runtime HOLD reported. Visual forwarding suspended.'}</span>
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

function SelectedDroneTelemetry({ state, inspectedRoute, onCollapse }: {
  state: MissionSimulationState;
  inspectedRoute: CommunicationRoute;
  onCollapse: () => void;
}) {
  const route = state.routes.find(item => item.id === inspectedRoute) || state.routes[0];
  const metric = route.metric;
  const primary: Array<[string, string]> = [
    ['Installed route', state.installedRoute ? ROUTE_LABELS[state.installedRoute] : 'Unknown'],
    ['Threat', route.threatScore === undefined ? state.reportedThreatLevel ?? 'Unavailable' : `${route.threatScore.toFixed(2)} · ${state.reportedThreatLevel ?? 'Unavailable'}`],
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
    <aside id="mission-live-telemetry" className="mission-telemetry" aria-label={`${ROUTE_LABELS[inspectedRoute]} telemetry`}>
      <div className="mission-telemetry__header">
        <span>Live telemetry</span>
        <div className="mission-telemetry__header-actions">
          <span>{ROUTE_LABELS[inspectedRoute]}</span>
          <button
            type="button"
            className="mission-telemetry__collapse"
            onClick={onCollapse}
            aria-label="Collapse live telemetry"
            title="Collapse live telemetry"
          >
            <PanelRightClose aria-hidden="true" />
          </button>
        </div>
      </div>
      <dl className="mission-telemetry__list">
        {primary.map(([label, value], index) => (
          <div key={label} className={index < 2 ? 'mission-telemetry__primary' : ''}>
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

function DecisionEvidence({ state }: { state: MissionSimulationState }) {
  if (!state.explanation) return null;
  return (
    <details className="mission-evidence">
      <summary>
        <ChevronDown aria-hidden="true" />
        <span>Decision evidence <small>Integrated Gradients · model attribution</small></span>
        <b>{formatPercent(state.explanation.normalized_completeness_error, 2)} error</b>
      </summary>
      <div className="mission-evidence__grid">
        {state.explanation.feature_attributions.map(item => (
          <div key={item.feature}>
            <span>{TELEMETRY_LABELS[item.feature.toLowerCase()] || titleCase(item.feature)}</span>
            <strong>{formatPercent(item.importance)}</strong>
            <i
              className={item.signed_attribution >= 0 ? 'is-positive' : 'is-negative'}
              style={{ '--evidence-width': `${Math.min(100, item.importance * 100)}%` } as React.CSSProperties}
            />
          </div>
        ))}
      </div>
      <p>Attribution indicates model sensitivity, not causal proof. {state.explanation.method} · target {state.explanation.target.head}[{state.explanation.target.index}]</p>
    </details>
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

function EventTimeline({ events }: { events: MissionEvent[] }) {
  const visible = events.slice(-5);
  return (
    <div className="mission-timeline" aria-live="polite" aria-label="Latest mission events">
      <div className="mission-section-label">Live event rail</div>
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
  const [renderer, setRenderer] = useState(import.meta.env.VITE_MISSION_RENDERER === 'svg' ? 'svg' : 'three');
  const state = useMemo(() => adaptMissionSimulationState(props.payload, {
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
    props.insiderRisk, props.noSafeRoute, props.payload, props.reward, props.sdnReady,
    props.step, props.threatLevel, props.trust,
  ]);
  const [paused, setPaused] = useState(false);
  const [viewMode, setViewMode] = useState<'mission' | 'topology'>('mission');
  const [telemetryOpen, setTelemetryOpen] = useState(true);
  const [inspectedRoute, setInspectedRoute] = useState<CommunicationRoute>(
    state.selectedRoute === 'hold' ? 'direct' : state.selectedRoute,
  );
  const [selectedAsset, setSelectedAsset] = useState<'satellite' | 'base' | 'jammer' | null>(null);
  const [routeNotice, setRouteNotice] = useState<{ from: MissionRoute; to: MissionRoute } | null>(null);
  const [clearedAttack, setClearedAttack] = useState<AttackProfile | null>(null);
  const [history, dispatchDecision] = useReducer(reduceDecisionHistory, undefined, () => emptyDecisionHistory(props.activeDrone));
  const events = history.droneId === props.activeDrone ? history.events : [];
  const previous = useRef<MissionSimulationState | null>(null);

  useEffect(() => {
    if (state.selectedRoute !== 'hold') setInspectedRoute(state.selectedRoute);
  }, [state.droneId, state.selectedRoute]);

  useEffect(() => {
    dispatchDecision({ state, selectedId: props.activeDrone });
    const prior = previous.current;
    if (!prior || prior.droneId !== props.activeDrone || state.droneId !== props.activeDrone) {
      setRouteNotice(null); setClearedAttack(null); setSelectedAsset(null);
    } else {
      if (prior.activeAttack !== 'none' && state.activeAttack === 'none') setClearedAttack(prior.activeAttack);
      if (state.activeAttack !== 'none') setClearedAttack(null);
      if (state.installedRoute != null && prior.installedRoute != null && state.installedRoute !== prior.installedRoute && state.sdnApplied === true) {
        setRouteNotice({from: prior.installedRoute,to: state.installedRoute});
      }
    }
    previous.current = state;
  }, [state, props.activeDrone]);

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
    props.fleetDrones.map(drone => [drone.drone_id, routeFromPayload(props.swarmPayloads[drone.drone_id])]),
  ), [props.fleetDrones, props.swarmPayloads]);
  const inspectedState = state.routes.find(route => route.id === inspectedRoute) as MissionRouteState | undefined;
  const selectedAssetCopy = selectedAsset === 'base'
    ? `Controller · ${props.sdnController} · ${state.sdnApplied ? 'route applied' : 'awaiting route state'}`
    : selectedAsset === 'satellite'
      ? `Simulated circular orbit · ${(MISSION_SATELLITE_ORBIT.config.altitudeM / 1000).toFixed(0)} km altitude · ${(MISSION_SATELLITE_ORBIT.config.inclinationRad * 180 / Math.PI).toFixed(0)}° inclination · ${(MISSION_SATELLITE_ORBIT.periodSeconds / 60).toFixed(1)} min period · 1× time · compressed sky position, not observed coverage · Link latency ${formatMetric(state.routes.find(route => route.id === 'satellite')?.metric?.latency, 'ms')}`
      : selectedAsset === 'jammer'
        ? `${threatVisual(state, props.activeDrone).label} · ${attackTarget(state)} · Visual regions are schematic; no measured source location. Physical UAV motion is unchanged.`
        : null;
  const missionPhase = history.droneId === props.activeDrone && state.droneId === props.activeDrone ? history.phase : 'awaiting-evidence';

  return (
    <section className="content-section mission-simulation" data-paused={paused} data-phase={missionPhase} aria-labelledby="mission-title">
      <header className="mission-simulation__header">
        <div>
          <div className="mission-eyebrow"><span>FLARE / Mission network</span><span>{state.source ? titleCase(state.source) : 'Awaiting source'}</span></div>
          <h2 id="mission-title">Live Mission Network</h2>
          <p>{activeDroneName}</p>
          <p className="mission-action-summary">{PHASE_LABELS[missionPhase]}{state.droneId === props.activeDrone && state.installedRoute ? ` · Installed: ${ROUTE_LABELS[state.installedRoute]}` : ''}</p>
        </div>
        <div className="mission-simulation__header-actions">
          <MissionStatus state={state} />
          <button type="button" className="mission-pause" onClick={() => setPaused(current => !current)} aria-pressed={paused}>
            {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            {paused ? 'Resume visuals' : 'Pause visuals'}
          </button>
        </div>
      </header>

      <div className="mission-context-bar">
        <MissionStateSummary state={state} clearedAttack={clearedAttack} />
        <div className="mission-view-toggle" role="group" aria-label="Mission visualization">
          <button type="button" className={viewMode === 'mission' ? 'is-active' : ''} aria-pressed={viewMode === 'mission'} onClick={() => setViewMode('mission')}>World</button>
          <button type="button" className={viewMode === 'topology' ? 'is-active' : ''} aria-pressed={viewMode === 'topology'} onClick={() => setViewMode('topology')} title="Logical topology">Network</button>
        </div>
      </div>

      <div className="mission-stage" data-world-renderer={viewMode === 'mission' ? renderer : 'topology'} data-telemetry-open={telemetryOpen}>
        <div className="mission-stage__canvas">
          {viewMode === 'mission' && renderer === 'svg' && <div className="mission-renderer-fallback" role="status">2D mission view <button type="button" onClick={() => setRenderer('three')}>Try 3D view</button></div>}
          {viewMode === 'mission' && renderer === 'three' ? (
            <WorldRenderBoundary onFallback={() => setRenderer('svg')}><Suspense fallback={<div className="mission-world-loading">Loading mission world…</div>}>
              <MissionWorldCanvas state={state} worldClock={worldClock} drones={props.fleetDrones} selectedDroneId={props.activeDrone} paused={paused} onDismiss={() => { setSelectedAsset(null); setTelemetryOpen(false); }} onSelectDrone={id => { props.onSelectDrone(id); setTelemetryOpen(true); setSelectedAsset(null); }} onSelectAsset={setSelectedAsset} onFallback={() => setRenderer('svg')} />
            </Suspense></WorldRenderBoundary>
          ) : viewMode === 'mission' ? (
            <AirspaceCanvas
              state={state}
              worldClock={worldClock}
              drones={props.fleetDrones}
              droneRoutes={droneRoutes}
              selectedDroneId={props.activeDrone}
              paused={paused}
              onSelectDrone={props.onSelectDrone}
              onInspectRoute={setInspectedRoute}
              onSelectAsset={setSelectedAsset}
            />
          ) : <LogicalTopologyView state={state} droneName={activeDroneName} />}
          {routeNotice && routeNotice.to === state.installedRoute && state.sdnApplied === true && (
            <div className="mission-route-notice" role="status">
              <span>Route updated</span>
              <strong>{ROUTE_LABELS[routeNotice.from]} → {ROUTE_LABELS[routeNotice.to]}</strong>
              <small>{state.installedRoute === 'hold' ? 'SDN hold policy applied' : 'SDN rule installed'}</small>
            </div>
          )}
          {selectedAssetCopy && (
            <button type="button" className="mission-asset-popover" onClick={() => setSelectedAsset(null)} aria-label="Close asset details">
              <strong>{selectedAsset === 'jammer' ? 'Threat details' : selectedAsset ? titleCase(selectedAsset) : ''}</strong><span>{selectedAssetCopy}</span>
            </button>
          )}
          {inspectedState?.jammed && <div className="mission-path-warning"><AlertTriangle aria-hidden="true" /> {titleCase(inspectedRoute)} path degraded</div>}
        </div>
        {viewMode === 'mission' && telemetryOpen && (
          <SelectedDroneTelemetry
            state={state}
            inspectedRoute={inspectedRoute}
            onCollapse={() => setTelemetryOpen(false)}
          />
        )}
        {viewMode === 'mission' && !telemetryOpen && (
          <button
            type="button"
            className="mission-telemetry-toggle"
            onClick={() => setTelemetryOpen(true)}
            aria-expanded="false"
            title="Show live telemetry"
            data-testid="show-live-telemetry"
          >
            <PanelRightOpen aria-hidden="true" />
            <span>Show telemetry</span>
          </button>
        )}
      </div>

      <RouteSelector state={state} inspectedRoute={inspectedRoute} onInspect={setInspectedRoute} />
      <NetworkStateStrip state={state} />

      <DecisionPanel state={state.droneId === props.activeDrone ? state : adaptMissionSimulationState(null, { droneId: props.activeDrone, route: "direct", threatLevel: "UNKNOWN", reward: 0, step: 0, noSafeRoute: false, availablePaths: [] })} phase={missionPhase} controller={props.sdnController} />

      <div className="mission-lower-grid">
        <div className="mission-evidence-wrap"><DecisionEvidence state={state} /></div>
        <SimulationEventControl
          profile={(ATTACK_OPTIONS.some(option => option.value === props.jamProfile) ? props.jamProfile : 'spot') as AttackProfile}
          duration={props.jamDuration}
          activeAttack={state.activeAttack}
          droneName={activeDroneName}
          enabled={props.attackSimulationEnabled}
          onProfileChange={props.onJamProfileChange}
          onDurationChange={props.onJamDurationChange}
          onApply={props.onApplyAttack}
        />
      </div>

      <EventTimeline events={events} />
    </section>
  );
}
