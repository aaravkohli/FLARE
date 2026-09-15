export type MissionRoute = 'direct' | 'satellite' | 'mesh' | 'hold';

export type CommunicationRoute = Exclude<MissionRoute, 'hold'>;

export type AttackProfile =
  | 'none'
  | 'spot'
  | 'sweep'
  | 'barrage'
  | 'smart'
  | 'reactive'
  | 'adaptive'
  | 'fhss'
  | 'spoofing'
  | 'gps_spoofing'
  | 'replay'
  | 'dos';

export interface MissionPathMetric {
  path_id: string;
  rssi: number;
  pdr: number;
  sinr: number;
  latency: number;
  packet_loss: number;
}

export interface MissionGpsInfo {
  latitude: number;
  longitude: number;
  drift_m: number;
}

export interface MissionEwStatus {
  active_attack: string | null;
  jammed_paths: string[];
}

export interface MissionModelExplanation {
  method: string;
  target: { head: string; index: number };
  prediction: number;
  normalized_completeness_error: number;
  faithfulness_passed: boolean;
  feature_attributions: Array<{
    feature: string;
    importance: number;
    signed_attribution: number;
  }>;
}

export interface MissionCanonicalEvent {
  event_id?: string;
  timestamp?: number;
  step?: number;
  inference?: {
    path_scores?: number[];
    confidence?: number;
    attack_type?: string;
    source?: string;
  };
  insider_analysis?: {
    status: 'NORMAL' | 'SUSPICIOUS' | 'MALICIOUS';
    predicted_class: string;
    risk_score: number;
  } | null;
  containment?: {
    mode: 'normal' | 'restricted' | 'control_only' | 'quarantined';
    reason: string;
    trust_score: number;
    insider_risk: number;
  } | null;
  decision?: {
    model_generation?: number | null;
    policy_path?: string;
    installed_path?: string | null;
    requested_path?: string;
    network_action?: string;
    threat_level?: string;
    constraint_reason?: string | null;
    safety_override?: boolean;
    safe_action_mask?: boolean[];
    safety_threshold?: number;
    no_safe_route?: boolean;
    route_changed?: boolean;
    routing_contract?: string;
  };
  sdn?: {
    applied?: boolean;
    error?: string | null;
    response?: Record<string, unknown> | null;
  };
  outcome?: {
    reward?: number;
    recovery_ms?: number | null;
    packet_loss?: number;
    estimated?: boolean;
  };
  timing?: { tick_elapsed_ms?: number | null };
  telemetry?: {
    security_evidence?: { simulation_profile?: string | null } | null;
  };
}

export interface MissionTelemetryPayload {
  type: string;
  timestamp: number;
  decision: {
    path_name: string;
    policy_path?: string;
    requested_path?: string;
    threat_level: string;
    reward: number;
    step: number;
    no_safe_route?: boolean;
    safety_override?: boolean;
    constraint_reason?: string | null;
    sdn_applied?: boolean;
  };
  metrics: {
    drone_id: string;
    timestamp: number;
    source: 'synthetic' | 'live' | 'synthetic_fallback' | 'legacy_api_fallback';
    paths: MissionPathMetric[];
    gps?: MissionGpsInfo;
    ew_status?: MissionEwStatus;
  };
  event?: MissionCanonicalEvent;
  telemetry_age_s?: number;
  explanation?: MissionModelExplanation | null;
}

export interface MissionFleetDrone {
  drone_id: string;
  display_name: string;
  index: number;
  enabled: boolean;
}

export interface MissionRouteState {
  id: CommunicationRoute;
  metric?: MissionPathMetric;
  threatScore?: number;
  available: boolean;
  active: boolean;
  jammed: boolean;
  safe?: boolean;
}

export interface MissionSimulationState {
  droneId: string;
  timestamp?: number;
  source?: MissionTelemetryPayload['metrics']['source'];
  routes: MissionRouteState[];
  selectedRoute: MissionRoute;
  requestedRoute?: MissionRoute;
  policyRoute?: MissionRoute;
  threatLevel: string;
  flConfidence?: number;
  attackClass?: string;
  activeAttack: AttackProfile;
  jammedPaths: CommunicationRoute[];
  reward: number;
  rlStep: number;
  sdnApplied?: boolean;
  sdnError?: string | null;
  safetyOverride: boolean;
  safeActionMask?: boolean[];
  safetyThreshold?: number;
  noSafeRoute: boolean;
  constraintReason?: string | null;
  routeChanged: boolean;
  networkAction?: string;
  recoveryMs?: number | null;
  tickElapsedMs?: number | null;
  gps?: MissionGpsInfo;
  trust?: number;
  insiderRisk?: number;
  containmentMode?: string;
  explanation?: MissionModelExplanation | null;
}
