import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useState } from 'react';
import type { NetworkDisplay, RouteInspection, SwarmSnapshot } from './swarm';
import { MissionClient } from './missionClient';

type Props={snapshot:SwarmSnapshot;client:MissionClient;display:NetworkDisplay;onDisplay:Dispatch<SetStateAction<NetworkDisplay>>;mutate:(path:string,body:Record<string,unknown>,method?:string)=>Promise<unknown>};
export default function NetworkInspector({snapshot:s,client,display,onDisplay,mutate}:Props){
 const [source,setSource]=useState(s.nodes[0]?.id??''),[destination,setDestination]=useState(s.nodes.at(-1)?.id??'base');
 const [route,setRoute]=useState<RouteInspection|null>(null),[error,setError]=useState(''),[packetId,setPacketId]=useState(''),[replayTime,setReplayTime]=useState(0),[replaying,setReplaying]=useState(false);
 useEffect(()=>{setPacketId('');setReplaying(false);onDisplay(current=>({...current,replay:undefined}));},[s.session_id,onDisplay]);
 const validSource=s.nodes.some(n=>n.id===source)?source:s.nodes[0]?.id??'';
 const validDestination=s.nodes.some(n=>n.id===destination)||destination==='base'?destination:s.nodes.at(-1)?.id??'base';
 useEffect(()=>{
  let disposed=false;
  if(!validSource||validSource===validDestination){setRoute(null);onDisplay(current=>({...current,hops:[]}));return;}
  void client.read(`/routes?source=${encodeURIComponent(validSource)}&destination=${encodeURIComponent(validDestination)}`).then((next:RouteInspection)=>{
   if(!disposed){setRoute(next);setError('');onDisplay(current=>({...current,hops:next.installed_hops.length?next.installed_hops:next.candidate_hops,candidate:!next.installed_hops.length}));}
  }).catch((reason:Error)=>{if(!disposed){setRoute(null);setError(reason.message);onDisplay(current=>({...current,hops:[]}));}});
  return ()=>{disposed=true;};
 // Display flags do not change route computation.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[client,validSource,validDestination,s.session_id,s.revision,s.network_revision]);
 const completed=(s.packet_events??[]).filter(e=>e.kind==='packet_result').sort((a,b)=>Number(b.data.correlation_id==='probe')-Number(a.data.correlation_id==='probe')||b.cursor-a.cursor).slice(0,80);
 const recovery=s.recovery?.find(r=>r.source===validSource&&r.destination===validDestination);
 const selected=completed.find(e=>e.data.id===packetId);
 const start=selected?.data.created_at??0,end=selected?.simulation_time??start;
 useEffect(()=>{
  if(!replaying||!selected)return;
  let raf=0,last=0,value=replayTime;
  function step(now:number){if(last)value+=(now-last)/1000*.1;last=now;value=Math.min(end,value);setReplayTime(value);onDisplay(current=>({...current,replay:{packetId,time:value}}));if(value<end)raf=requestAnimationFrame(step);else setReplaying(false);}
  raf=requestAnimationFrame(step);return ()=>cancelAnimationFrame(raf);
 // A replay has its own clock and follows recorded event times at 0.1×.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[replaying,packetId,end]);
 return <div className="network-inspection" data-event-count={s.packet_events?.length??0} data-result-count={completed.length}>
  <details className="network-probe-tools"><summary>Route probes</summary><div className="network-route-controls"><label>Source<select aria-label="Route source" value={validSource} onChange={e=>setSource(e.target.value)}>{s.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}</select></label>
   <span className="network-route-arrow" aria-hidden="true">→</span><label>Destination<select aria-label="Route destination" value={validDestination} onChange={e=>setDestination(e.target.value)}>{s.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}<option value="base">Ground control</option></select></label>
   <button disabled={!route?.reachable} onClick={()=>void mutate('/probes',{session_id:s.session_id,source:validSource,destination:validDestination,size_bytes:512})}>Send probe</button>
   <div className="network-route-summary" aria-live="polite"><strong>{validSource===validDestination?'Choose distinct drones':route?.reachable?`${Math.max(0,(route.installed_hops.length?route.installed_hops:route.candidate_hops).length-1)} hops · ${route.installed_hops.length?'installed':'candidate'}`:'No reachable route'}</strong><span>{route&&(route.installed_hops.length?route.installed_hops:route.candidate_hops).map(id=>s.nodes.find(n=>n.id===id)?.alias??id).join(' → ')}</span>{recovery&&<small className="network-recovery">Route: {recovery.route_convergence_ms==null?'unrecovered':recovery.route_convergence_ms.toFixed(1)+' ms'} · Service: {recovery.service_recovery_ms==null?'awaiting delivery':recovery.service_recovery_ms.toFixed(1)+' ms'}</small>}</div>
  </div>
  </details><div className="network-layer-controls">{([['inactive','Idle links'],['packets','Packets'],['ranges','Selected range']] as const).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} checked={display[key]} onChange={e=>onDisplay({...display,[key]:e.target.checked})}/>{label}</label>)}
   <details><summary>Packet replay</summary><div className="network-replay"><label>Recorded packet<select aria-label="Recorded packet" value={packetId} onChange={e=>{setReplaying(false);setPacketId(e.target.value);const p=completed.find(p=>p.data.id===e.target.value);const time=p?.data.created_at??0;setReplayTime(time);onDisplay({...display,replay:p?{packetId:p.data.id,time}:undefined});}}><option value="">Live events</option>{completed.map(e=><option key={e.cursor} value={e.data.id}>{e.data.id} · {e.data.status} · {e.data.category}</option>)}</select></label>
    {selected&&<><input aria-label="Replay time" type="range" min={start} max={Math.max(start+.0001,end)} step="0.0001" value={replayTime} onChange={e=>{const time=Number(e.target.value);setReplaying(false);setReplayTime(time);onDisplay({...display,replay:{packetId,time}});}}/><button onClick={()=>{setReplayTime(start);onDisplay({...display,replay:{packetId,time:start}});setReplaying(true);}}>Replay 0.1×</button><span>{((replayTime-start)*1000).toFixed(1)} ms · {selected.data.attempt??0} attempts · {selected.data.reason??'delivered'}</span><small className="network-replay-route">Recorded hops · current positions: {selected.data.route.map(id=>s.nodes.find(n=>n.id===id)?.alias??id).join(' → ')||'No route'}</small></>}
   </div></details>
   <details className="network-stat-details"><summary>Packet statistics</summary><dl>{Object.entries({'Generated':s.statistics.generated,'Delivered':s.statistics.delivered,'Dropped':s.statistics.dropped,'Pending':s.statistics.pending??s.packets.length,'Failed routes':s.statistics.failed_routes,'Reroutes':s.statistics.reroutes,'Jitter':s.statistics.delivered>1?`${s.statistics.jitter_ms?.toFixed(2)??'—'} ms`:'—','Ground mesh reachability':`${(s.statistics.availability*100).toFixed(1)}%`}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></details>
   <span className="network-provenance">SIMULATED PACKET EVENTS</span>
  </div>{error&&<p role="alert" className="swarm-error">{error}</p>}
 </div>;
}
