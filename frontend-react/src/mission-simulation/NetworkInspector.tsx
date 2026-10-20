import type { Dispatch, ReactNode, RefObject, SetStateAction } from 'react';
import { useEffect, useState } from 'react';
import type { NetworkDisplay, RouteInspection, SwarmSnapshot } from './swarm';
import { MissionClient } from './missionClient';
import { CameraMenu, ToolPopover } from './WorkspaceControls';
import { TERRAIN_DISCLOSURE } from './world/terrain';
import type { CanvasControls } from './WorkspaceControls';

type Props={active?:boolean;busy?:boolean;stale?:boolean;world?:boolean;controls:RefObject<CanvasControls|null>;quality:'high'|'balanced';onQuality:(quality:'high'|'balanced')=>void;children?:ReactNode;snapshot:SwarmSnapshot;client:MissionClient;display:NetworkDisplay;onDisplay:Dispatch<SetStateAction<NetworkDisplay>>;mutate:(path:string,body:Record<string,unknown>,method?:string)=>Promise<unknown>};
export default function NetworkInspector({snapshot:s,client,display,onDisplay,mutate,active=true,busy=false,stale=false,world=false,controls,quality,onQuality,children}:Props){
 const [source,setSource]=useState(s.nodes[0]?.id??''),[destination,setDestination]=useState(s.nodes.at(-1)?.id??'base');
 const [route,setRoute]=useState<RouteInspection|null>(null),[error,setError]=useState(''),[packetId,setPacketId]=useState(''),[replayTime,setReplayTime]=useState(0),[replaying,setReplaying]=useState(false);
 useEffect(()=>{setRoute(null);setPacketId('');setReplaying(false);onDisplay(current=>({...current,replay:undefined}));},[s.session_id,onDisplay]);
 const validSource=s.nodes.some(n=>n.id===source)?source:s.nodes[0]?.id??'';
 const validDestination=s.nodes.some(n=>n.id===destination)||destination==='base'?destination:s.nodes.at(-1)?.id??'base';
 useEffect(()=>{
  let disposed=false;
  if(!active)return;
  if(!validSource||validSource===validDestination){setRoute(null);onDisplay(current=>({...current,hops:[]}));return;}
  void client.read(`/routes?source=${encodeURIComponent(validSource)}&destination=${encodeURIComponent(validDestination)}`).then((next:RouteInspection)=>{
   if(!disposed){setRoute(next);setError('');onDisplay(current=>({...current,hops:next.installed_hops.length?next.installed_hops:next.candidate_hops,candidate:!next.installed_hops.length}));}
  }).catch((reason:Error)=>{if(!disposed){setRoute(null);setError(reason.message);onDisplay(current=>({...current,hops:[]}));}});
  return ()=>{disposed=true;};
 // Display flags do not change route computation.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[active,client,validSource,validDestination,s.session_id,s.revision,s.network_revision]);
 const completed=(s.packet_events??[]).filter(e=>e.kind==='packet_result').sort((a,b)=>Number(b.data.correlation_id==='probe')-Number(a.data.correlation_id==='probe')||b.cursor-a.cursor).slice(0,80);
 const recovery=s.recovery?.find(r=>r.source===validSource&&r.destination===validDestination);
 const selected=completed.find(e=>e.data.id===packetId);
 const start=selected?.data.created_at??0,end=selected?.simulation_time??start;
 useEffect(()=>{
  if(!active||stale||!replaying||!selected)return;
  let raf=0,last=0,value=replayTime;
  function step(now:number){if(last)value+=(now-last)/1000*.1;last=now;value=Math.min(end,value);setReplayTime(value);onDisplay(current=>({...current,replay:{packetId,time:value}}));if(value<end)raf=requestAnimationFrame(step);else setReplaying(false);}
  raf=requestAnimationFrame(step);return ()=>cancelAnimationFrame(raf);
 // A replay has its own clock and follows recorded event times at 0.1×.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[active,stale,replaying,packetId,end]);
 return <div className="network-inspection" data-event-count={s.packet_events?.length??0} data-result-count={completed.length}>
  <CameraMenu controls={controls} world={world}/><ToolPopover label="Layers"><div className="workspace-options">{([['inactive','Idle links'],['packets','Packets'],['ranges','Selected range'],...(world?[['alternatives','Alternative routes']]:[])] as [keyof Pick<NetworkDisplay,'inactive'|'packets'|'ranges'|'alternatives'>,string][]).map(([key,label])=><label key={key}><input type="checkbox" aria-label={label} checked={!!display[key]} onChange={e=>onDisplay({...display,[key]:e.target.checked})}/>{label}</label>)}{world&&<label><input type="checkbox" aria-label="Site details" checked={display.siteDetails!==false} onChange={e=>onDisplay({...display,siteDetails:e.target.checked})}/>Site details</label>}</div><div className="swarm-legend"><span>Blue · FLARE telemetry route</span><span>Green · inspected installed route</span><span>Dashed · candidate / disconnected</span><span>Amber · degraded / jammed</span></div></ToolPopover>
  <ToolPopover label="Route probes" compactLabel="Probes"><div className="network-probe-tools"><div className="network-route-controls"><label>Source<select aria-label="Route source" value={validSource} onChange={e=>setSource(e.target.value)}>{s.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}</select></label>
   <span className="network-route-arrow" aria-hidden="true">→</span><label>Destination<select aria-label="Route destination" value={validDestination} onChange={e=>setDestination(e.target.value)}>{s.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}<option value="base">Ground control</option></select></label>
   <button disabled={busy||stale||!route?.reachable} onClick={()=>void mutate('/probes',{session_id:s.session_id,source:validSource,destination:validDestination,size_bytes:512})}>Send probe</button>
   <div className="network-route-summary" aria-live="polite"><strong>{validSource===validDestination?'Choose distinct drones':route?.reachable?`${Math.max(0,(route.installed_hops.length?route.installed_hops:route.candidate_hops).length-1)} hops · ${route.installed_hops.length?'installed':'candidate'}`:route?'No reachable route':error?'Route unavailable':'Inspecting route…'}</strong><span>{route&&(route.installed_hops.length?route.installed_hops:route.candidate_hops).map(id=>s.nodes.find(n=>n.id===id)?.alias??id).join(' → ')}</span>{recovery&&<small className="network-recovery">Route: {recovery.route_convergence_ms==null?'unrecovered':recovery.route_convergence_ms.toFixed(1)+' ms'} · Service: {recovery.service_recovery_ms==null?'awaiting delivery':recovery.service_recovery_ms.toFixed(1)+' ms'}</small>}</div>
  </div>
  <div className="probe-outcomes"><strong>Recent probe outcomes</strong>{completed.filter(e=>e.data.correlation_id==='probe'&&e.data.source===validSource&&e.data.destination===validDestination).slice(0,5).map(e=><p key={e.cursor}><time>T+{e.simulation_time.toFixed(3)}s</time> {e.data.status} · {e.data.reason??e.data.id}</p>)}{!completed.some(e=>e.data.correlation_id==='probe'&&e.data.source===validSource&&e.data.destination===validDestination)&&<p>No recorded probe outcome for this pair.</p>}</div>
  {error&&<p role="alert" className="swarm-error">{error}</p>}</div></ToolPopover>
  <ToolPopover label="View settings" compactLabel="View">{world&&<label>Render quality<select aria-label="Mission visual quality" value={quality} onChange={e=>onQuality(e.target.value as 'high'|'balanced')}><option value="balanced">Balanced</option><option value="high">High</option></select></label>}
   <details><summary>Packet replay</summary><div className="network-replay"><label>Recorded packet<select aria-label="Recorded packet" value={packetId} onChange={e=>{setReplaying(false);setPacketId(e.target.value);const p=completed.find(p=>p.data.id===e.target.value);const time=p?.data.created_at??0;setReplayTime(time);onDisplay({...display,replay:p?{packetId:p.data.id,time}:undefined});}}><option value="">Live events</option>{completed.map(e=><option key={e.cursor} value={e.data.id}>{e.data.id} · {e.data.status} · {e.data.category}</option>)}</select></label>
    {selected&&<><input aria-label="Replay time" type="range" min={start} max={Math.max(start+.0001,end)} step="0.0001" value={replayTime} onChange={e=>{const time=Number(e.target.value);setReplaying(false);setReplayTime(time);onDisplay({...display,replay:{packetId,time}});}}/><button onClick={()=>{setReplayTime(start);onDisplay({...display,replay:{packetId,time:start}});setReplaying(true);}}>Replay 0.1×</button><span>{((replayTime-start)*1000).toFixed(1)} ms · {selected.data.attempt??0} attempts · {selected.data.reason??'delivered'}</span><small className="network-replay-route">Recorded hops · current positions: {selected.data.route.map(id=>s.nodes.find(n=>n.id===id)?.alias??id).join(' → ')||'No route'}</small></>}
   </div></details>
   <details><summary>Simulation scope</summary><p className="swarm-note">Modeled radio, movement, encryption policy and forwarding. Terrain: {String(s.terrain.category||s.terrain.source||'representative extension')}. No RF calibration, real OpenFlow forwarding or UAV field validation. Orbital scale is compressed; alternative routes are schematic.</p>{world&&<p className="swarm-note">{TERRAIN_DISCLOSURE}.</p>}</details>
   {children}</ToolPopover>
 </div>;
}
