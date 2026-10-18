import { adaptMissionSimulationState, applyMissionRouteRequest } from '../adapter.ts';
import type { MissionTelemetryPayload } from '../types.ts';
import type { MissionRouteRequest, MissionRouteCompletion } from '../routeTransition.ts';
import { missionEvidenceStatus, MISSION_FRESHNESS_LIMIT_S } from './evidenceFreshness.ts';
import type { MissionEvidenceStatus, MissionTransportStatus } from './evidenceFreshness.ts';
import { decisionPhase } from './decisionPresentation.ts';
export interface WorkspaceEvidenceInput {
  payload: MissionTelemetryPayload | null;
  receivedAtMs: number | null;
  transportStatus: MissionTransportStatus;
  unavailable?: string | null;
  request?: MissionRouteRequest | null;
  completion?: MissionRouteCompletion | null;
  requestReceivedAtMs?: number | null;
  controller: string;
}
export function workspaceEvidence(input: WorkspaceEvidenceInput, sessionId: string, droneId: string, nowMs: number) {
  // A connected socket, another UAV, or an earlier session cannot establish current evidence.
  const payload = input.payload?.metrics.drone_id === droneId && input.payload.event?.mission_context?.session_id === sessionId ? input.payload : null;
  const completion = input.completion?.drone_id === droneId && input.completion.event.mission_context?.session_id === sessionId ? input.completion : null;
  const request = payload && input.request?.drone_id === droneId && input.request.transition_id !== completion?.transition_id
    && input.request.step > (payload.event?.step ?? payload.decision.step)
    && input.request.occurred_at >= payload.timestamp ? input.request : null;
  let evidence: MissionEvidenceStatus = missionEvidenceStatus({payload, selectedDroneId: droneId, transportStatus: input.transportStatus,
    telemetryUnavailable: input.unavailable, receivedAtMs: input.receivedAtMs, nowMs});
  const canonical = adaptMissionSimulationState(payload, {droneId, route:'unknown', threatLevel:'UNKNOWN', reward:0, step:0,
    noSafeRoute:false, availablePaths:payload?.metrics.paths.map(p=>p.path_id)??[], sdnReady:!!payload});
  if (request) {
    const age = request.occurred_at-request.telemetry.timestamp;
    const validAge = Number.isFinite(age) && age >= 0 && input.requestReceivedAtMs != null && Number.isFinite(input.requestReceivedAtMs);
    if (input.transportStatus === 'connected' && !input.unavailable) {
      const seconds = validAge ? age+Math.max(0,nowMs-input.requestReceivedAtMs!)/1000 : null;
      const current = seconds != null && seconds <= MISSION_FRESHNESS_LIMIT_S;
      evidence = {kind:seconds == null?'unknown-age':current?'fresh':'stale', label:seconds == null?'Request evidence age unknown':current?'Fresh request':`Stale request ${seconds.toFixed(1)} s`,
        ageSeconds:seconds, evidenceCurrent:current, lastSnapshot:!current};
    }
  }
  const state = request ? applyMissionRouteRequest(canonical, request) : canonical;
  const controllerProgress = request ? evidence.evidenceCurrent ? 'awaiting' : 'outcome-unknown' : null;
  const phase = request ? evidence.evidenceCurrent ? 'request-not-installed' : 'awaiting-evidence' : decisionPhase(state,null,droneId);
  return {state,evidence,controllerProgress,phase} as const;
}
