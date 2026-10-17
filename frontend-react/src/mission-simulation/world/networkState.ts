import type { CommunicationRoute, MissionSimulationState } from '../types.ts';
export function networkRouteState(state: MissionSimulationState, id: CommunicationRoute, selectedId: string) {
  const route = state.routes.find(r => r.id === id);
  const matches = state.droneId === selectedId;
  const hold = state.installedRoute === 'hold' || state.selectedRoute === 'hold' || state.networkAction === 'hold';
  const installed = matches && state.installedRoute === id && !hold;
  const candidate = matches && !hold && state.requestedRoute === id && !installed;
  return { hold, installed, candidate, forwarding: installed && id !== 'mesh',
    unsafe: route?.safe === false, degraded: !!route?.jammed || (route?.metric?.packet_loss ?? 0) > .2,
    opacity: installed ? .8 : candidate ? .32 : .025, metric: matches ? route?.metric : undefined };
}
