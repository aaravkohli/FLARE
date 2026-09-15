import type {
  AttackProfile,
  CommunicationRoute,
  MissionRoute,
  MissionSimulationState,
  MissionTelemetryPayload,
} from './types';

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
  const selectedRoute = asRoute(
    event?.decision?.installed_path
      || payload?.decision.path_name
      || event?.decision?.requested_path
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
    droneId: payload?.metrics.drone_id || fallback.droneId,
    timestamp: payload?.metrics.timestamp || payload?.timestamp,
    source: payload?.metrics.source,
    routes: COMMUNICATION_ROUTES.map((id, index) => ({
      id,
      metric: metricByPath.get(id),
      threatScore: scoreByPath.get(id),
      available: available.size === 0 ? fallback.sdnReady !== false : available.has(id),
      active: selectedRoute === id,
      jammed: jammedPaths.includes(id),
      safe: event?.decision?.safe_action_mask?.[index],
    })),
    selectedRoute: noSafeRoute && event?.decision?.network_action === 'hold' ? 'hold' : selectedRoute,
    requestedRoute: asRoute(event?.decision?.requested_path || payload?.decision.requested_path),
    policyRoute: asRoute(event?.decision?.policy_path || payload?.decision.policy_path),
    threatLevel: event?.decision?.threat_level || payload?.decision.threat_level || fallback.threatLevel,
    flConfidence: event?.inference?.confidence,
    attackClass: event?.inference?.attack_type,
    activeAttack: asAttack(payload?.metrics.ew_status?.active_attack),
    jammedPaths,
    reward: event?.outcome?.reward ?? payload?.decision.reward ?? fallback.reward,
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
