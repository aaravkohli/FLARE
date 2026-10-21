import type { MissionPathMetric, MissionSimulationState } from '../types.ts';
import type { SwarmSnapshot } from '../swarm.ts';

/** The Python model's complete no-route tuple is an input sentinel, not a measurement. */
export function isNoRouteSample(metric:MissionPathMetric|undefined,source:MissionSimulationState['source']){
 return source==='synthetic'&&metric?.rssi===-125&&metric.sinr===-10&&metric.pdr===0&&metric.packet_loss===1&&metric.latency===1000;
}

export function withMissionPathEvidence(state:MissionSimulationState,snapshot:SwarmSnapshot,identity:string,current:boolean){
 if(state.droneId!==identity)return state;
 if(!current)return {...state,routes:state.routes.map(route=>isNoRouteSample(route.metric,state.source)?{...route,measurementUnavailable:true}:route)};
 const node=snapshot.nodes?.find(n=>n.id===identity);
 if(!node)return state;
 const distance=Math.hypot(...node.position.map((v,i)=>v-snapshot.base_position[i]));
 const direct=snapshot.links.some(l=>l.a===identity&&l.b==='base'||l.b===identity&&l.a==='base');
 return {...state,routes:state.routes.map(route=>{
  const noSample=isNoRouteSample(route.metric,state.source);
  if(route.id==='direct'&&!direct){
   const outside=distance>node.range_m;
   return {...route,available:false,measurementUnavailable:true,unavailableLabel:outside?'Out of range':'No radio link',
    availabilityReason:outside?`Direct radio distance ${distance.toFixed(0)} m exceeds the ${node.range_m.toFixed(0)} m range.`:'No eligible Direct radio link to ground control in the current mission snapshot.'};
  }
  if(noSample)return {...route,available:false,measurementUnavailable:true,unavailableLabel:route.id==='direct'?'Awaiting sample':`No ${route.id} path`,
   availabilityReason:route.id==='direct'?'A radio link is now present; the latest observation still reports no route. Waiting for a new measurement.':`The latest observation reports no ${route.id} route to ground control. Placeholder values are not measured latency or RF loss.`};
  return {...route,measurementUnavailable:false};
 })};
}
