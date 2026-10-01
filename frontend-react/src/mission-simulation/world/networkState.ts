import type { CommunicationRoute, MissionSimulationState } from '../types.ts';
export function networkRouteState(state: MissionSimulationState, id: CommunicationRoute, selectedId: string) {
  const route = state.routes.find(r => r.id === id);
  const matches = state.droneId === selectedId;
  // A HOLD request is an intent; only the installed state suspends forwarding.
  const hold = state.installedRoute === 'hold';
  const installed = matches && state.installedRoute === id && !hold;
  const candidate = matches && !hold && state.requestedRoute === id && !installed;
  const latency = route?.metric?.latency;
  const loss = route?.metric?.packet_loss;
  const validFlowMetrics = latency != null && Number.isFinite(latency) && latency >= 0
    && loss != null && Number.isFinite(loss) && loss >= 0 && loss <= 1;
  return { hold, installed, candidate, forwarding: installed && state.sdnApplied !== false && id !== 'mesh' && validFlowMetrics,
    unsafe: route?.safe === false, degraded: !!route?.jammed || (route?.metric?.packet_loss ?? 0) > .2,
    opacity: installed ? .8 : candidate ? .32 : .025, metric: matches ? route?.metric : undefined };
}
