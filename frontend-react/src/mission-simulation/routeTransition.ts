import type {
  MissionCanonicalEvent,
  MissionRoute,
  MissionTelemetryPayload,
} from './types';

type MissionMetrics = MissionTelemetryPayload['metrics'];

export interface MissionRouteRequest {
  type: 'route_transition';
  phase: 'requested';
  cursor: number;
  transition_id: string;
  occurred_at: number;
  run_id: string;
  step: number;
  drone_id: string;
  mode: 'simulation' | 'real';
  telemetry: MissionMetrics;
  inference: {
    path_scores: number[];
    confidence: number;
    attack_type: string;
    source: string;
    model_id?: string | null;
  };
  decision: {
    source: string;
    policy_path: MissionRoute;
    requested_path: MissionRoute;
    threat_level: string;
    safety_override: boolean;
    safe_action_mask: boolean[];
    no_safe_route: boolean;
    constraint_reason?: string | null;
    network_action: 'forward' | 'hold';
    safety_threshold?: number;
  };
  last_confirmed_path: MissionRoute | null;
  controller: {
    configured_backend: string;
    status: 'awaiting_response';
    applied: null;
  };
}

export interface MissionCompletedCanonicalEvent extends Omit<MissionCanonicalEvent, 'event_id' | 'timestamp' | 'step' | 'telemetry' | 'inference' | 'decision' | 'sdn' | 'outcome'> {
  event_id: string;
  timestamp: number;
  step: number;
  drone_id: string;
  mode: 'simulation' | 'real';
  telemetry: MissionMetrics;
  inference: NonNullable<MissionCanonicalEvent['inference']>;
  decision: NonNullable<MissionCanonicalEvent['decision']> & {
    requested_path: MissionRoute;
    installed_path: MissionRoute | null;
  };
  sdn: { applied: boolean; error?: string | null; response?: Record<string, unknown> | null };
  outcome: NonNullable<MissionCanonicalEvent['outcome']> & { reward: number };
}

export interface MissionRouteCompletion {
  type: 'route_transition';
  phase: 'completed';
  cursor: number;
  transition_id: string;
  occurred_at: number;
  run_id: string;
  step: number;
  drone_id: string;
  mode: 'simulation' | 'real';
  event: MissionCompletedCanonicalEvent;
  controller: {
    status: 'acknowledged' | 'not_confirmed';
    applied: boolean;
    error?: string | null;
  };
  confirmed_installed_path?: MissionRoute | null;
  last_confirmed_path?: MissionRoute | null;
}

export type MissionRouteTransition = MissionRouteRequest | MissionRouteCompletion;

export interface MissionRouteTransitionState {
  cursor: number;
  pendingByDrone: Record<string, MissionRouteRequest>;
  completedByDrone: Record<string, MissionRouteCompletion>;
  history: MissionRouteTransition[];
}

export const initialMissionRouteTransitionState: MissionRouteTransitionState = {
  cursor: 0,
  pendingByDrone: {},
  completedByDrone: {},
  history: [],
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function route(value: unknown): value is MissionRoute {
  return value === 'direct' || value === 'satellite' || value === 'mesh' || value === 'hold';
}

function validHeader(value: Record<string, unknown>): boolean {
  return value.type === 'route_transition'
    && Number.isSafeInteger(value.cursor) && (value.cursor as number) > 0
    && typeof value.transition_id === 'string' && value.transition_id.length > 0
    && typeof value.run_id === 'string' && value.run_id.length > 0
    && typeof value.drone_id === 'string' && value.drone_id.length > 0
    && Number.isInteger(value.step) && (value.step as number) > 0
    && typeof value.occurred_at === 'number' && Number.isFinite(value.occurred_at)
    && (value.mode === 'simulation' || value.mode === 'real');
}

/** Validate the wire boundary before a request can alter mission presentation. */
export function parseMissionRouteTransition(value: unknown): MissionRouteTransition | null {
  if (!record(value) || !validHeader(value) || !record(value.controller)) return null;
  if (value.phase === 'requested') {
    if (!record(value.telemetry) || value.telemetry.drone_id !== value.drone_id
      || typeof value.telemetry.timestamp !== 'number' || !Array.isArray(value.telemetry.paths)
      || !record(value.inference) || !Array.isArray(value.inference.path_scores)
      || !record(value.decision) || !route(value.decision.policy_path)
      || !route(value.decision.requested_path)
      || !(value.last_confirmed_path === null || route(value.last_confirmed_path))
      || value.controller.status !== 'awaiting_response' || value.controller.applied !== null) return null;
    return value as unknown as MissionRouteRequest;
  }
  if (value.phase === 'completed') {
    if (!record(value.event) || value.event.event_id !== value.transition_id
      || value.event.drone_id !== value.drone_id || !record(value.event.telemetry)
      || !record(value.event.decision) || !record(value.event.sdn)
      || !record(value.event.outcome) || !route(value.event.decision.requested_path)
      || !(value.event.decision.installed_path === null || route(value.event.decision.installed_path))
      || typeof value.event.sdn.applied !== 'boolean'
      || value.controller.applied !== value.event.sdn.applied
      || (value.controller.status === 'acknowledged') !== value.event.sdn.applied) return null;
    if (value.event.sdn.applied === true && (
      !route(value.confirmed_installed_path)
      || value.confirmed_installed_path !== value.event.decision.installed_path
    )) return null;
    if (value.event.sdn.applied === false && value.confirmed_installed_path != null) return null;
    return value as unknown as MissionRouteCompletion;
  }
  return null;
}

/** One cursor is shared by live WebSocket delivery and recorded step replay. */
export function reduceMissionRouteTransition(
  state: MissionRouteTransitionState,
  transition: MissionRouteTransition,
): MissionRouteTransitionState {
  if (transition.cursor <= state.cursor) return state;
  const pendingByDrone = { ...state.pendingByDrone };
  const completedByDrone = { ...state.completedByDrone };
  if (transition.phase === 'requested') {
    pendingByDrone[transition.drone_id] = transition;
  } else {
    if (pendingByDrone[transition.drone_id]?.transition_id === transition.transition_id) {
      delete pendingByDrone[transition.drone_id];
    }
    completedByDrone[transition.drone_id] = transition;
  }
  return {
    cursor: transition.cursor,
    pendingByDrone,
    completedByDrone,
    history: [...state.history, transition].slice(-64),
  };
}

/** Reuse the canonical completed event as a normal mission snapshot. */
export function missionPayloadFromCompletion(transition: MissionRouteCompletion): MissionTelemetryPayload {
  const event = transition.event;
  return {
    type: 'telemetry',
    timestamp: event.timestamp,
    decision: {
      path_name: event.decision.installed_path ?? event.decision.requested_path,
      policy_path: event.decision.policy_path,
      requested_path: event.decision.requested_path,
      threat_level: event.decision.threat_level ?? 'UNKNOWN',
      reward: event.outcome.reward,
      step: event.step,
      no_safe_route: event.decision.no_safe_route,
      safety_override: event.decision.safety_override,
      constraint_reason: event.decision.constraint_reason,
      sdn_applied: event.sdn.applied,
    },
    metrics: event.telemetry,
    event,
    telemetry_age_s: Math.max(0, transition.occurred_at - event.telemetry.timestamp),
  };
}
