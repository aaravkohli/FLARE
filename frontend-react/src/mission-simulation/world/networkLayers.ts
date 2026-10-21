import type { NetworkDisplay, RouteInspection } from '../swarm.ts';

/** Inspection is read-only until its overlay is explicitly enabled. */
export function probeRouteHops(route:RouteInspection|null,visible:boolean):string[]{
 return visible&&route?.reachable?(route.installed_hops.length?route.installed_hops:route.candidate_hops):[];
}
/** Ground-control availability is separate from the UAV topology. */
export function showGroundLink(a:string,b:string,display:NetworkDisplay,emphasized=false){
 return a!=='base'&&b!=='base'||display.groundLinks!==false||emphasized;
}
