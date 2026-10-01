import type {
  AttackProfile,
  CommunicationRoute,
  MissionRoute,
  MissionSimulationState,
  MissionTelemetryPayload,
} from './types';
import type { MissionRouteRequest } from './routeTransition';

const COMMUNICATION_ROUTES: CommunicationRoute[] = ['direct', 'satellite', 'mesh'];
const ATTACK_PROFILES: AttackProfile[] = [
  'none', 'spot', 'sweep', 'barrage', 'smart', 'reactive', 'adaptive',
  'fhss', 'spoofing', 'gps_spoofing', 'replay', 'dos',
];

function asRoute(value: string | null | undefined): MissionRoute | undefined {
  const route = value?.toLowerCase();
  return route === 'direct' || route === 'satellite' || route === 'mesh' || route === 'hold'
    ? route
    : undefined;
}

function asAttack(value: string | null | undefined): AttackProfile {
  const profile = value?.toLowerCase() as AttackProfile | undefined;
  return profile && ATTACK_PROFILES.includes(profile) ? profile : 'none';
}

export function adaptMissionSimulationState(
  payload: MissionTelemetryPayload | null,
  fallback: {
    droneId: string;
    route: string;
    threatLevel: string;
    reward: number;
    step: number;
    noSafeRoute: boolean;
    availablePaths: string[];
    sdnReady?: boolean;
    trust?: number;
    insiderRisk?: number;
    containmentMode?: string;
  },
): MissionSimulationState {
  const event = payload?.event;
  // Canonical installed_path is authoritative even when explicitly null. The
  // legacy summary can supply an installation only after a successful apply.
  const installedRoute = event?.decision && 'installed_path' in event.decision
    ? asRoute(event.decision.installed_path) ?? null
    : payload?.decision.sdn_applied === true ? asRoute(payload.decision.path_name) ?? null : null;
  // This is a presentation/inspection focus, never evidence of installation.
  const selectedRoute = asRoute(
    installedRoute
      || event?.decision?.requested_path
      || payload?.decision.path_name
      || fallback.route,
  ) || 'direct';
  const scoreByPath = new Map(
    COMMUNICATION_ROUTES.map((route, index) => [route, event?.inference?.path_scores?.[index]]),
  );
  const metricByPath = new Map(
    (payload?.metrics.paths || [])
      .filter(metric => COMMUNICATION_ROUTES.includes(metric.path_id as CommunicationRoute))
      .map(metric => [metric.path_id as CommunicationRoute, metric]),
  );
  const jammedPaths = (payload?.metrics.ew_status?.jammed_paths || [])
    .filter((route): route is CommunicationRoute => COMMUNICATION_ROUTES.includes(route as CommunicationRoute));
  const available = new Set(fallback.availablePaths);
  const noSafeRoute = Boolean(event?.decision?.no_safe_route ?? payload?.decision.no_safe_route ?? fallback.noSafeRoute);

  return {
    eventId: event?.event_id,
    inferenceSource: event?.inference?.source,
    decisionSource: event?.decision?.source,
    networkDetection: event?.network_security_analysis,
    reportedSafetyOverride: event?.decision?.safety_override ?? payload?.decision.safety_override,
    reportedThreatLevel: event?.decision?.threat_level ?? payload?.decision.threat_level,
    installedRoute,
    droneId: payload?.metrics.drone_id || fallback.droneId,
    timestamp: payload?.metrics.timestamp || payload?.timestamp,
    source: payload?.metrics.source,
    routes: COMMUNICATION_ROUTES.map((id, index) => ({
      id,
      metric: metricByPath.get(id),
      threatScore: scoreByPath.get(id),
      available: available.size === 0 ? fallback.sdnReady !== false : available.has(id),
      active: installedRoute === id,
      jammed: jammedPaths.includes(id),
      safe: event?.decision?.safe_action_mask?.[index],
    })),
    selectedRoute,
    requestedRoute: asRoute(event?.decision?.requested_path || payload?.decision.requested_path),
    policyRoute: asRoute(event?.decision?.policy_path || payload?.decision.policy_path),
    threatLevel: event?.decision?.threat_level || payload?.decision.threat_level || fallback.threatLevel,
    flConfidence: event?.inference?.confidence,
    attackClass: event?.inference?.attack_type,
    activeAttack: asAttack(payload?.metrics.ew_status?.active_attack),
    jammedPaths,
    reward: event?.outcome?.reward ?? payload?.decision.reward ?? fallback.reward,
    outcomeCurrent: payload != null && Number.isFinite(event?.outcome?.reward ?? payload.decision.reward),
    rlStep: event?.step ?? payload?.decision.step ?? fallback.step,
    sdnApplied: event?.sdn?.applied ?? payload?.decision.sdn_applied,
    sdnError: event?.sdn?.error,
    safetyOverride: Boolean(event?.decision?.safety_override ?? payload?.decision.safety_override),
    safeActionMask: event?.decision?.safe_action_mask,
    safetyThreshold: event?.decision?.safety_threshold,
    noSafeRoute,
    constraintReason: event?.decision?.constraint_reason ?? payload?.decision.constraint_reason,
    routeChanged: Boolean(event?.decision?.route_changed),
    networkAction: event?.decision?.network_action,
    recoveryMs: event?.outcome?.recovery_ms,
    tickElapsedMs: event?.timing?.tick_elapsed_ms,
    gps: payload?.metrics.gps,
    trust: event?.containment?.trust_score ?? fallback.trust,
    insiderRisk: event?.containment?.insider_risk ?? fallback.insiderRisk,
    containmentMode: event?.containment?.mode ?? fallback.containmentMode,
    explanation: payload?.explanation,
  };
}

/** A pre-controller decision updates evidence while preserving the last confirmed installation. */
export function applyMissionRouteRequest(
  baseState: MissionSimulationState,
  request: MissionRouteRequest,
): MissionSimulationState {
  if (request.drone_id !== baseState.droneId) return baseState;
  const metrics = new Map(request.telemetry.paths.map(path => [path.path_id, path]));
  const jammedPaths = (request.telemetry.ew_status?.jammed_paths ?? [])
    .filter((path): path is CommunicationRoute => COMMUNICATION_ROUTES.includes(path as CommunicationRoute));
  const installedRoute = request.last_confirmed_path;
  return {
    ...baseState,
    eventId: undefined,
    networkDetection: undefined,
    explanation: undefined,
    droneId: request.drone_id,
    timestamp: request.telemetry.timestamp,
    source: request.telemetry.source,
    routes: baseState.routes.map((route, index) => ({
      ...route,
      metric: metrics.get(route.id),
      threatScore: request.inference.path_scores[index],
      active: installedRoute === route.id,
      jammed: jammedPaths.includes(route.id),
      safe: request.decision.safe_action_mask[index],
    })),
    selectedRoute: installedRoute ?? request.decision.requested_path,
    installedRoute,
    requestedRoute: request.decision.requested_path,
    policyRoute: request.decision.policy_path,
    threatLevel: request.decision.threat_level,
    reportedThreatLevel: request.decision.threat_level,
    inferenceSource: request.inference.source,
    decisionSource: request.decision.source,
    flConfidence: request.inference.confidence,
    attackClass: request.inference.attack_type,
    activeAttack: asAttack(request.telemetry.ew_status?.active_attack),
    jammedPaths,
    outcomeCurrent: false,
    rlStep: request.step,
    sdnApplied: undefined,
    sdnError: null,
    safetyOverride: request.decision.safety_override,
    reportedSafetyOverride: request.decision.safety_override,
    safeActionMask: request.decision.safe_action_mask,
    safetyThreshold: request.decision.safety_threshold,
    noSafeRoute: request.decision.no_safe_route,
    constraintReason: request.decision.constraint_reason,
    routeChanged: false,
    networkAction: request.decision.network_action,
    recoveryMs: null,
    tickElapsedMs: null,
    gps: request.telemetry.gps,
    trust: undefined,
    insiderRisk: undefined,
    containmentMode: undefined,
  };
}
