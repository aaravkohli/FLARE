import type { ReactNode } from 'react';
import {
  AlertTriangle,
  FileText,
  LockKeyhole,
  Server,
  UserPlus,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
import type { MissionTelemetryPayload } from '../mission-simulation/types';
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
  activeDrone: string;
  activePath: string;
  threatLevel: string;
  noSafeRoute: boolean;
  swarmPayloads: Record<string, MissionTelemetryPayload>;
  unavailablePaths: string[];
  activeAvailablePaths: string[];
  showEnrollment: boolean;
  enrollment: ReactNode;
  onToggleEnrollment: () => void;
  onSelectDrone: (drone: string) => void;
  onExportReport: () => void;
  onLock: () => void;
}

const titleCase = (value: string) => value
  .replaceAll('_', ' ')
  .replace(/\b\w/g, character => character.toUpperCase());

const isKnownRoute = (value?: string) => value === 'direct' || value === 'satellite' || value === 'mesh' || value === 'hold';

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
  activeDrone,
  activePath,
  threatLevel,
  noSafeRoute,
  swarmPayloads,
  unavailablePaths,
  activeAvailablePaths,
  showEnrollment,
  enrollment,
  onToggleEnrollment,
  onSelectDrone,
  onExportReport,
  onLock,
}: CommandOverviewProps) {
  const activeName = fleetDrones.find(drone => drone.drone_id === activeDrone)?.display_name || titleCase(activeDrone);
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
        <div><span>SDN</span><strong>{sdnController} {sdnReadiness?.ready ? 'ready' : sdnReadiness ? 'not ready' : 'checking'}</strong></div>
        <div><span>Route</span><strong>{noSafeRoute || activePath === 'hold' ? 'Network hold' : titleCase(activePath)}</strong></div>
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
          const selected = drone.drone_id === activeDrone;
          const payload = swarmPayloads[drone.drone_id];
          const installed = payload?.event?.decision?.installed_path || payload?.decision.path_name;
          const route = isKnownRoute(installed) ? installed : selected && isKnownRoute(activePath) ? activePath : undefined;
          const metric = payload?.metrics.paths.find(path => path.path_id === route);
          const hasAttention = selected && (threatLevel === 'HIGH' || threatLevel === 'MEDIUM' || noSafeRoute);

          return (
            <button
              type="button"
              key={drone.drone_id}
              className={`cvo-drone${selected ? ' cvo-drone--selected' : ''}${hasAttention ? ' cvo-drone--attention' : ''}${!drone.enabled ? ' cvo-drone--disabled' : ''}`}
              onClick={() => onSelectDrone(drone.drone_id)}
              aria-pressed={selected}
            >
              <div className="cvo-drone__header">
                <div>
                  <h3>{drone.display_name}</h3>
                  <p>{drone.drone_id}<span aria-hidden="true">/</span>SDN port {drone.access_port}</p>
                </div>
                {hasAttention && <span className="cvo-drone__alert">{noSafeRoute ? 'Hold' : `${titleCase(threatLevel)} threat`}</span>}
                {!drone.enabled && <span className="cvo-drone__alert">Disabled</span>}
              </div>
              <div className="cvo-drone__route">{route ? titleCase(route) : 'Route unavailable'}</div>
              <div className="cvo-drone__metrics">
                <span>{formatPdr(metric?.pdr)}</span>
                <span>{formatLatency(metric?.latency)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
