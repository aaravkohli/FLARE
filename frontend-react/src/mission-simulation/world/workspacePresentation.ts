import type { ExperimentResult, SwarmCommand, SwarmEvent } from '../swarm.ts';
import { title } from '../swarm.ts';

export const RESULT_METRICS = [
 {key:'pdr',label:'Delivery',unit:'%',scale:100,precision:1},
 {key:'average_latency_ms',label:'Latency',unit:'ms',scale:1,precision:2},
 {key:'jitter_ms',label:'Jitter',unit:'ms',scale:1,precision:2},
 {key:'throughput_mbps',label:'Throughput',unit:'Mb/s',scale:1,precision:4},
 {key:'average_hops',label:'Hops',unit:'',scale:1,precision:1},
 {key:'connected_components',label:'Components',unit:'',scale:1,precision:0},
 {key:'failed_routes',label:'Failed routes',unit:'',scale:1,precision:0},
 {key:'route_convergence_ms',label:'Route convergence',unit:'ms',scale:1,precision:1},
 {key:'service_recovery_ms',label:'Service recovery',unit:'ms',scale:1,precision:1},
] as const;
export type MetricKey=typeof RESULT_METRICS[number]['key'];
export function metricValue(row:ExperimentResult,key:MetricKey):number|null {const value=row[key];return typeof value==='number'&&Number.isFinite(value)?value:null;}
export function formatMetric(row:ExperimentResult,key:MetricKey):string {
 const metric=RESULT_METRICS.find(m=>m.key===key)!,value=metricValue(row,key);
 return value==null?'—':`${(value*metric.scale).toFixed(metric.precision)}${metric.unit?' '+metric.unit:''}`;
}
export function metricDifference(a:ExperimentResult,b:ExperimentResult,key:MetricKey):string {
 if(a.seed!==b.seed)return '—';const av=metricValue(a,key),bv=metricValue(b,key);if(av==null||bv==null)return '—';
 const metric=RESULT_METRICS.find(m=>m.key===key)!,value=(bv-av)*metric.scale;
 return `${value>0?'+':''}${value.toFixed(metric.precision)}${key==='pdr'?' pp':metric.unit?' '+metric.unit:''}`;
}
export function resultRows(rows:ExperimentResult[],seeds:number[],sort:string,direction:'asc'|'desc',topologies:string[]):ExperimentResult[] {
 return rows.filter(row=>seeds.includes(row.seed)).slice().sort((a,b)=>{
  if(sort==='topology')return (topologies.indexOf(a.topology)-topologies.indexOf(b.topology))*(direction==='asc'?1:-1)||a.seed-b.seed;
  const av=sort==='seed'?a.seed:metricValue(a,sort as MetricKey),bv=sort==='seed'?b.seed:metricValue(b,sort as MetricKey);
  if(av==null)return bv==null?0:1;if(bv==null)return -1;
  return (av-bv)*(direction==='asc'?1:-1)||topologies.indexOf(a.topology)-topologies.indexOf(b.topology)||a.seed-b.seed;
 });
}
export function mergeEvents(previous:SwarmEvent[],incoming:SwarmEvent[]):SwarmEvent[]{return [...new Map([...previous,...incoming].map(e=>[e.cursor,e])).values()].sort((a,b)=>a.cursor-b.cursor).slice(-300);}
export function eventText(event:SwarmEvent,name:(id:string)=>string):string {
 const d=event.data,subject=d.node??d.drone_id??d.source,label=subject?name(String(subject)):'';
 if(event.kind.startsWith('packet_'))return `${label||'Packet'}${d.destination?' → '+name(String(d.destination)):''} · ${d.status??title(event.kind)}${d.reason?' · '+d.reason:''}`;
 const descriptions:Record<string,string>={attack_injected:`${title(String(d.profile??'Condition'))} injected`,attack_cleared:'Injected condition cleared',route_proposed:`${label} requested ${d.requested_family??'a route'}`,detector_observation:`${label} · detector reported ${d.attack_type??'an observation'}`,INSTALL:`SDN installed ${d.family??d.network_action??'a forwarding rule'}`,BLOCK:'Link blocked',RESTORE:'Link restored',enforcement_failed:'Controller enforcement failed',command_link_lost:'Command link lost · autonomous networking',command_link_restored:'Command link restored',link_disconnected:'Radio link disconnected',neighbor_discovered:'Neighbor discovered'};
 return descriptions[event.kind]??`${label?label+' · ':''}${title(event.kind)}`;
}
export interface ActivityItem {id:string;time:number|null;category:string;summary:string;targets:string[];events:SwarmEvent[];command?:SwarmCommand}
export function activityItems(session:string,events:SwarmEvent[],commands:SwarmCommand[],name:(id:string)=>string):ActivityItem[]{
 const grouped=new Map<string,SwarmEvent[]>(),items:ActivityItem[]=[];
 for(const e of mergeEvents([],events)){
  const commandId=e.data.command_id??(commands.some(c=>c.command_id===e.data.correlation_id)?e.data.correlation_id:undefined);
  if(typeof commandId==='string'){grouped.set(commandId,[...(grouped.get(commandId)??[]),e]);continue;}
  const d=e.data;items.push({id:`${session}:${e.cursor}`,time:Number.isFinite(e.simulation_time)?e.simulation_time:null,category:e.category,summary:eventText(e,name),targets:[d.node,d.drone_id,d.source,d.destination,...(Array.isArray(d.targets)?d.targets:[]),...(Array.isArray(d.affected_targets)?d.affected_targets:[]),...(Array.isArray(d.link)?d.link:[])].filter((id):id is string=>typeof id==='string'),events:[e]});
 }
 const ids=new Set([...grouped.keys(),...commands.map(c=>c.command_id)]);
 for(const id of ids){
  const command=commands.find(c=>c.command_id===id),history=grouped.get(id)??[],times=history.map(e=>e.simulation_time).filter(Number.isFinite),statuses=Object.values(command?.outcomes??{}).reduce<Record<string,number>>((all,o)=>{all[o.status]=(all[o.status]??0)+1;return all;},{});
  const time=times.length?Math.max(...times):command?.simulation_time??null;
  items.push({id:`${session}:command:${id}`,time,category:'COMMAND',summary:`${title(command?.action??String(history[0]?.data.action??'Command'))} · ${Object.entries(statuses).map(([s,n])=>`${n} ${title(s).toLowerCase()}`).join(', ')||'status unavailable'}`,targets:Object.keys(command?.outcomes??{}),events:history,command});
 }
 return items.sort((a,b)=>(b.time??-Infinity)-(a.time??-Infinity)||b.id.localeCompare(a.id));
}

/** Research size presets plus the authoritative current size, including API values. */
export function fleetSizeOptions(current:number):number[] {
 const sizes=[...Array.from({length:10},(_,index)=>(index+1)*10),75];
 if(Number.isInteger(current)&&current>=10&&current<=100)sizes.push(current);
 return [...new Set(sizes)].sort((a,b)=>a-b);
}
