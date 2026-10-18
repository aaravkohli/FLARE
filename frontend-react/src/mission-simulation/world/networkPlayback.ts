import type { PacketEvent, SwarmPacket } from '../swarm.ts';

export const PLAYBACK_BUFFER_SECONDS = .25;
export function playbackTime(simulationTime:number,speed:number,elapsedMs:number,playing:boolean,current=true){
 return Math.max(0,simulationTime-(playing?PLAYBACK_BUFFER_SECONDS*speed:0)+(playing&&current?Math.min(200,Math.max(0,elapsedMs))/1000*speed:0));
}
export interface PacketVisual {packet:SwarmPacket;from:string;to:string;progress:number;kind:'flight'|'delivered'|'dropped'|'retry';at:number}
interface Timeline {flights:PacketEvent[];outcomes:PacketEvent[]}
const timelines=new WeakMap<PacketEvent[],Timeline>();
export function packetTimeline(events:PacketEvent[]):Timeline{
 let timeline=timelines.get(events);
 if(!timeline){timeline={flights:events.filter(e=>e.kind==='packet_hop'),outcomes:events.filter(e=>['packet_result','packet_retry','packet_attempt_lost','packet_rerouted'].includes(e.kind))};timelines.set(events,timeline);}
 return timeline;
}
export function samplePacketEvents(events:PacketEvent[],seconds:number,focus='',limit=200,replayPacket?:string):PacketVisual[]{
 const timeline=packetTimeline(events),frames:PacketVisual[]=[];
 const relevant=(p:SwarmPacket)=>!replayPacket||p.id===replayPacket;
 for(const e of timeline.flights){
  const p=e.data;
  if(!relevant(p)||seconds<p.start_at||seconds>=p.end_at)continue;
  // A later loss/repair may cancel this reservation before its scheduled end.
  if(timeline.outcomes.some(o=>o.data.id===p.id&&o.simulation_time>=p.start_at&&o.simulation_time<=seconds&&['packet_result','packet_rerouted'].includes(o.kind)))continue;
  const from=e.data.from_node??p.route[p.hop],to=e.data.to_node??p.route[p.hop+1];
  if(from&&to)frames.push({packet:p,from,to,progress:(seconds-p.start_at)/Math.max(1e-9,p.end_at-p.start_at),kind:'flight',at:p.start_at});
 }
 for(const e of timeline.outcomes){
  if(!relevant(e.data)||seconds<e.simulation_time||seconds-e.simulation_time>.2)continue;
  const p=e.data,node=e.kind==='packet_attempt_lost'?p.route[p.hop+1]:p.reason==='radio_loss'?p.route[p.hop+1]:p.route[Math.min(p.hop,p.route.length-1)]??p.source;
  if(node)frames.push({packet:p,from:node,to:node,progress:1,kind:['packet_retry','packet_rerouted'].includes(e.kind)?'retry':p.status==='delivered'?'delivered':'dropped',at:e.simulation_time});
 }
 return frames.sort((a,b)=>Number(b.packet.route.includes(focus))-Number(a.packet.route.includes(focus))||b.at-a.at||a.packet.id.localeCompare(b.packet.id)).slice(0,limit);
}
export function edgePoint(a:{x:number;z:number},b:{x:number;z:number},progress:number,from:string,to:string){
 const dx=b.x-a.x,dz=b.z-a.z,length=Math.hypot(dx,dz)||1;
 const bend=Math.min(18,length*.06)*(from<to?1:-1);
 const cx=(a.x+b.x)/2-dz/length*bend,cz=(a.z+b.z)/2+dx/length*bend;
 const t=progress,u=1-t;
 return {x:u*u*a.x+2*u*t*cx+t*t*b.x,z:u*u*a.z+2*u*t*cz+t*t*b.z,cx,cz};
}
