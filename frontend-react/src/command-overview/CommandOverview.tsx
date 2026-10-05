import { useSyncExternalStore, type ReactNode } from 'react';
import {
  AlertTriangle,
  FileText,
  LockKeyhole,
  Pencil,
  Server,
  UserPlus,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
import type { MissionTelemetryPayload } from '../mission-simulation/types';
import { getMissionSnapshot, subscribeMission, getMissionSelection, subscribeSelection, selectMissionTarget } from '../mission-simulation/swarm';
import './command-overview.css';

type Severity = 'info' | 'warning' | 'critical';

interface FleetDrone {
  drone_id: string;
  display_name: string;
  index: number;
  access_port: number;
  enabled: boolean;
}

interface ReadinessEvent {
  timestamp: number;
  severity: Severity;
  message: string;
}

interface SdnReadiness {
  ready: boolean;
  status: string;
  connected_switches: number;
  expected_switches: number;
  available_paths: Record<string, string[]>;
  error?: string | null;
  alert?: { message: string };
}

interface CommandOverviewProps {
  systemMode: string;
  wsStatus: 'connecting' | 'connected' | 'disconnected';
  sdnController: string;
  sdnReadiness: SdnReadiness | null;
  readinessHistory: ReadinessEvent[];
  fleetDrones: FleetDrone[];
  inactiveDrones?: FleetDrone[];
  activeDrone: string;
  threatLevel: string;
  noSafeRoute: boolean;
  swarmPayloads: Record<string, MissionTelemetryPayload>;
  unavailablePaths: string[];
  activeAvailablePaths: string[];
  showEnrollment: boolean;
  enrollment: ReactNode;
  onToggleEnrollment: () => void;
  onSelectDrone: (drone: string) => void;
  onEditDrone?: (droneId: string) => void;
  onExportReport: () => void;
  onLock: () => void;
}

const titleCase = (value: string) => value
  .replaceAll('_', ' ')
  .replace(/\b\w/g, character => character.toUpperCase());

const isKnownRoute = (value?: string) => value === 'direct' || value === 'satellite' || value === 'mesh' || value === 'hold';

function reportedInstalledRoute(payload?: MissionTelemetryPayload): string | null {
  const decision = payload?.event?.decision;
  const value = decision && 'installed_path' in decision
    ? decision.installed_path
    : payload?.decision.sdn_applied === true ? payload.decision.path_name : null;
  return isKnownRoute(value ?? undefined) ? value! : null;
}

function formatPdr(value?: number) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}% PDR` : 'PDR unavailable';
}

function formatLatency(value?: number) {
  return typeof value === 'number' ? `${Math.round(value)} ms` : 'Latency unavailable';
}

function linkLabel(status: CommandOverviewProps['wsStatus']) {
  if (status === 'connected') return 'Online';
  if (status === 'connecting') return 'Connecting';
  return 'Offline';
}

export default function CommandOverview({
  systemMode,
  wsStatus,
  sdnController,
  sdnReadiness,
  readinessHistory,
  fleetDrones,
  inactiveDrones = [],
  activeDrone,
  threatLevel,
  noSafeRoute,
  swarmPayloads,
  unavailablePaths,
  activeAvailablePaths,
  showEnrollment,
  enrollment,
  onToggleEnrollment,
  onSelectDrone,
  onEditDrone,
  onExportReport,
  onLock,
}: CommandOverviewProps) {
  const mission = useSyncExternalStore(subscribeMission, getMissionSnapshot);
  const selection = useSyncExternalStore(subscribeSelection, getMissionSelection);
  const inspected = mission ? selection.inspected : activeDrone;
  const installedRoute = (id: string) => mission
    ? mission.flows.find(flow => flow.source === id && flow.destination === 'base' && flow.traffic_class === 'telemetry')?.family ?? null
    : reportedInstalledRoute(swarmPayloads[id]);
  const activeName = mission?.nodes.find(node => node.id === inspected)?.alias || fleetDrones.find(drone => drone.drone_id === inspected)?.display_name || titleCase(inspected);
  const activeInstalledRoute = installedRoute(inspected);
  const latestEvent = readinessHistory[0];
  const readinessNotice = !sdnReadiness?.ready
    ? sdnReadiness?.alert?.message || (sdnReadiness
      ? `Routing changes are unavailable while the controller reports ${sdnReadiness.error || sdnReadiness.status.replaceAll('_', ' ')}${sdnReadiness.expected_switches > 0 ? ` (${sdnReadiness.connected_switches}/${sdnReadiness.expected_switches} switches connected).` : '.'}`
      : null)
    : unavailablePaths.length > 0
      ? `${unavailablePaths.map(route => route.toUpperCase()).join(', ')} unavailable for ${activeDrone.toUpperCase()}. Available routes: ${activeAvailablePaths.map(route => route.toUpperCase()).join(', ') || 'none'}.`
      : null;

  return (
    <section className="command-overview" aria-labelledby="command-overview-title" data-testid="command-overview">
      <header className="command-overview__header">
        <div className="command-overview__identity">
          <span>FLARE</span>
          <h1 id="command-overview-title">Network overview</h1>
        </div>

        <div className="command-overview__actions">
          <span className="command-overview__mode">{titleCase(systemMode)} mode</span>
          <button type="button" className="command-overview__report" onClick={onExportReport}>
            <FileText aria-hidden="true" /> Export report
          </button>
          <button type="button" className="command-overview__lock" onClick={onLock}>
            <LockKeyhole aria-hidden="true" /> Lock
          </button>
        </div>
      </header>

      <div className="command-overview__status" aria-label="Operational status">
        <div className={`command-overview__link command-overview__link--${wsStatus}`}>
          {wsStatus === 'disconnected' ? <WifiOff aria-hidden="true" /> : <Wifi aria-hidden="true" />}
          <strong>{linkLabel(wsStatus)}</strong>
        </div>
        <div><span>SDN</span><strong>{mission ? 'Logical simulation' : sdnController} {sdnReadiness?.ready ? 'ready' : sdnReadiness ? 'not ready' : 'checking'}</strong></div>
        <div><span>{mission ? 'Installed mission route' : 'Last reported route'}</span><strong>{activeInstalledRoute ? titleCase(activeInstalledRoute) : 'Unconfirmed'}</strong></div>
        <div className="command-overview__focus"><span>Asset</span><strong>{activeName}</strong></div>
      </div>

      {readinessNotice && (
        <div className="command-overview__notice" role={!sdnReadiness?.ready ? 'alert' : 'status'}>
          <AlertTriangle aria-hidden="true" />
          <span>{readinessNotice}</span>
        </div>
      )}

      <div className={`command-overview__controller command-overview__controller--${latestEvent?.severity || 'info'}`} aria-label="Most recent SDN state change">
        <span className="command-overview__controller-label"><Server aria-hidden="true" /> Controller</span>
        <span className="command-overview__controller-message">
          <i aria-hidden="true" />
          {latestEvent?.message || 'Waiting for controller state'}
        </span>
        {latestEvent && <time>{new Date(latestEvent.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>}
      </div>

      <div className="command-overview__fleet-header">
        <div className="command-overview__fleet-title">
          <h2>Fleet</h2>
          <span>{fleetDrones.length} UAVs</span>
        </div>
        <button type="button" className="command-overview__enroll" onClick={onToggleEnrollment} aria-expanded={showEnrollment}>
          {showEnrollment ? <X aria-hidden="true" /> : <UserPlus aria-hidden="true" />}
          {showEnrollment ? 'Close enrollment' : 'Enroll UAV'}
        </button>
      </div>

      {showEnrollment && <div className="command-overview__enrollment">{enrollment}</div>}

      <div className="command-overview__fleet" aria-label="Swarm drone selection">
        {fleetDrones.map(drone => {
          const node = mission?.nodes.find(node => node.id === drone.drone_id);
          const selected = mission ? selection.targets.includes(drone.drone_id) : drone.drone_id === activeDrone;
          const candidate = swarmPayloads[drone.drone_id];
          const payload = !mission || candidate?.event?.mission_context?.session_id === mission.session_id ? candidate : undefined;
          const route = installedRoute(drone.drone_id);
          const metric = payload?.metrics.paths.find(path => path.path_id === route);
          const hasAttention = selected && (threatLevel === 'HIGH' || threatLevel === 'MEDIUM' || noSafeRoute);

          return (
            <div
              key={drone.drone_id}
              className={`cvo-drone${selected ? ' cvo-drone--selected' : ''}${hasAttention ? ' cvo-drone--attention' : ''}${!drone.enabled ? ' cvo-drone--disabled' : ''}`}
            >
              <button type="button" className="cvo-drone__select" onClick={event => { if(mission)selectMissionTarget(drone.drone_id,event.shiftKey || event.ctrlKey || event.metaKey);onSelectDrone(drone.drone_id); }} aria-pressed={selected} aria-label={`Select ${node?.alias || drone.display_name}`}>
              <div className="cvo-drone__header">
                <div>
                  <h3>{node?.alias || drone.display_name}</h3>
                  <p>{drone.drone_id}<span aria-hidden="true">/</span>{node ? `Radio range ${Math.round(node.range_m)} m` : `SDN port ${drone.access_port}`}</p>
                </div>
                {hasAttention && <span className="cvo-drone__alert">{noSafeRoute ? route === 'hold' ? 'HOLD reported' : 'No safe route reported' : `${titleCase(threatLevel)} threat reported`}</span>}
                {!drone.enabled && <span className="cvo-drone__alert">Disabled</span>}
              </div>
              <div className="cvo-drone__route">{route ? `${mission ? 'Installed' : 'Last reported'}: ${titleCase(route)}` : 'Installed route unconfirmed'}</div>
              <div className="cvo-drone__metrics">
                <span>{formatPdr(metric?.pdr)}</span>
                <span>{formatLatency(metric?.latency)}</span>
              </div>
              </button>
              {onEditDrone && <button type="button" className="cvo-drone__edit" onClick={() => onEditDrone(drone.drone_id)} aria-label={`Edit ${drone.display_name}`}><Pencil aria-hidden="true" /> Edit</button>}
            </div>
          );
        })}
      </div>
      {onEditDrone && inactiveDrones.length > 0 && (
        <div className="command-overview__inactive" aria-label="Disabled UAVs">
          <h3>Disabled UAVs</h3>
          {inactiveDrones.map(drone => <div key={drone.drone_id} className="command-overview__inactive-row"><span>{drone.display_name} <small>{drone.drone_id}</small></span><button type="button" onClick={() => onEditDrone(drone.drone_id)}><Pencil aria-hidden="true" /> Edit</button></div>)}
        </div>
      )}
    </section>
  );
}
