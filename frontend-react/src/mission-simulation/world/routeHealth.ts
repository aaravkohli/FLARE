import type { MissionRouteState } from '../types.ts';

/** The cause is explicit: low detector risk does not erase measured RF loss. */
export function routeHealth(route: MissionRouteState, noSafeRoute: boolean) {
  if (!route.available) return {health:'Unavailable',label:route.unavailableLabel??'Unavailable',reason:route.availabilityReason??'This communication family is unavailable.'};
  if (route.recoveryPending) return {health:'Pending',label:'Updating',reason:'The condition ended. Waiting for a post-condition observation; risk and measurements shown are retained.'};
  const loss=route.metric?.packet_loss;
  if (route.safe === false || noSafeRoute && route.safe === undefined && route.threatScore !== undefined)
    return {health:'Unsafe',label:'Safety blocked',reason:`Excluded by the latest safety decision.${loss!==undefined&&Number.isFinite(loss)&&loss>=.2?` The reported path also has ${(loss*100).toFixed(1)}% packet loss.`:''} See Decisions for the detector and policy evidence.`};
  if (route.jammed) return {health:'Degraded',label:'Interference',reason:'An injected interference condition is active on this family.'};
  if (loss !== undefined && Number.isFinite(loss) && loss >= .2)
    return {health:'Degraded',label:`Packet loss ${(loss*100).toFixed(0)}%`,reason:`Reported path packet loss is ${(loss*100).toFixed(1)}%. No injected RF interference is active on this family.`};
  return {health:route.active?'Active':'Available',label:route.active?'Active':'Available',reason:route.active?'Confirmed installed family.':'Available communication family.'};
}
