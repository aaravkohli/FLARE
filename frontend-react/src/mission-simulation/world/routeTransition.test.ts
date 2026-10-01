import assert from 'node:assert/strict';
import test from 'node:test';
import { adaptMissionSimulationState, applyMissionRouteRequest } from '../adapter.ts';
import {
  initialMissionRouteTransitionState,
  missionPayloadFromCompletion,
  parseMissionRouteTransition,
  reduceMissionRouteTransition,
} from '../routeTransition.ts';
import type { MissionRouteCompletion, MissionRouteRequest } from '../routeTransition.ts';

const metrics = {
  drone_id: 'drone_1', timestamp: 100, source: 'synthetic' as const,
  paths: [
    { path_id: 'direct', rssi: -89, pdr: 0.4, sinr: 2, latency: 220, packet_loss: 0.6 },
    { path_id: 'satellite', rssi: -71, pdr: 0.97, sinr: 16, latency: 640, packet_loss: 0.03 },
    { path_id: 'mesh', rssi: -77, pdr: 0.85, sinr: 11, latency: 80, packet_loss: 0.15 },
  ],
  ew_status: { active_attack: 'spot', jammed_paths: ['direct'] },
};
const request: MissionRouteRequest = {
  type: 'route_transition', phase: 'requested', cursor: 7, transition_id: 'run:2:drone_1',
  occurred_at: 101, run_id: 'run', step: 2, drone_id: 'drone_1', mode: 'simulation',
  telemetry: metrics,
  inference: { path_scores: [0.9, 0.04, 0.25], confidence: 0.9, attack_type: 'jammed', source: 'checkpoint' },
  decision: { source: 'dqn', policy_path: 'satellite', requested_path: 'satellite', threat_level: 'HIGH',
    safety_override: false, safe_action_mask: [false, true, true], no_safe_route: false, network_action: 'forward' },
  last_confirmed_path: 'direct',
  controller: { configured_backend: 'mock', status: 'awaiting_response', applied: null },
};
const base = adaptMissionSimulationState({
  type: 'telemetry', timestamp: 99,
  metrics: { ...metrics, timestamp: 99, ew_status: { active_attack: null, jammed_paths: [] } },
  decision: { path_name: 'direct', requested_path: 'direct', threat_level: 'LOW', reward: 0.8, step: 1, sdn_applied: true },
}, { droneId: 'drone_1', route: 'direct', threatLevel: 'LOW', reward: 0.8, step: 1,
  noSafeRoute: false, availablePaths: ['direct', 'satellite', 'mesh'] });

function completion(applied: boolean): MissionRouteCompletion {
  return {
    type: 'route_transition', phase: 'completed', cursor: 8, transition_id: request.transition_id,
    occurred_at: 102, run_id: 'run', step: 2, drone_id: 'drone_1', mode: 'simulation',
    event: {
      event_id: request.transition_id, timestamp: 100, step: 2, drone_id: 'drone_1', mode: 'simulation',
      telemetry: metrics, inference: request.inference,
      decision: { ...request.decision, installed_path: applied ? 'satellite' : null, route_changed: applied },
      sdn: { applied, error: applied ? null : 'controller timeout' }, outcome: { reward: 0.2 },
    },
    controller: { status: applied ? 'acknowledged' : 'not_confirmed', applied },
    ...(applied ? { confirmed_installed_path: 'satellite' as const } : { last_confirmed_path: 'direct' as const }),
  };
}

test('request displays same-cycle degradation without installing the requested route', () => {
  const pending = applyMissionRouteRequest(base, request);
  assert.equal(pending.installedRoute, 'direct');
  assert.equal(pending.requestedRoute, 'satellite');
  assert.equal(pending.selectedRoute, 'direct');
  assert.equal(pending.routes.find(route => route.id === 'direct')?.active, true);
  assert.equal(pending.routes.find(route => route.id === 'satellite')?.active, false);
  assert.equal(pending.routes.find(route => route.id === 'direct')?.metric?.packet_loss, 0.6);
  assert.equal(pending.activeAttack, 'spot');
  assert.equal(pending.sdnApplied, undefined);
  assert.equal(pending.outcomeCurrent, false);
  assert.equal(pending.eventId, undefined);
  assert.equal(pending.networkDetection, undefined);
  assert.equal(pending.explanation, undefined);
});

test('ordered journal retains a brief request even after acknowledged completion', () => {
  const parsedRequest = parseMissionRouteTransition(request);
  const parsedCompletion = parseMissionRouteTransition(completion(true));
  assert.ok(parsedRequest && parsedCompletion);
  const pending = reduceMissionRouteTransition(initialMissionRouteTransitionState, parsedRequest);
  const finished = reduceMissionRouteTransition(pending, parsedCompletion);
  assert.equal(finished.pendingByDrone.drone_1, undefined);
  assert.equal(finished.history.length, 2);
  assert.deepEqual(finished.history.map(item => item.phase), ['requested', 'completed']);
  assert.equal(missionPayloadFromCompletion(parsedCompletion as MissionRouteCompletion).event?.decision?.installed_path, 'satellite');
  assert.strictEqual(reduceMissionRouteTransition(finished, parsedRequest), finished);
});

test('uncertain controller outcome cannot be parsed as an acknowledged installation', () => {
  const uncertain = completion(false);
  assert.equal(uncertain.controller.status, 'not_confirmed');
  assert.equal(uncertain.event.decision.installed_path, null);
  assert.equal(parseMissionRouteTransition({ ...uncertain, controller: { status: 'acknowledged', applied: true } }), null);
  assert.equal(parseMissionRouteTransition({ ...uncertain, confirmed_installed_path: 'satellite' }), null);
  const otherDrone = { ...request, drone_id: 'drone_2' };
  assert.equal(parseMissionRouteTransition(otherDrone), null);
});
