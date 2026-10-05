import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import MissionWorldCanvas from './world/MissionWorldCanvas';
import { createClock } from './world/clock';
import { createWorld } from './world/dynamics';
import { adaptMissionSimulationState } from './adapter';
import { FORMATIONS, TOPOLOGIES, title, getMissionSnapshot, subscribeMission, publishMissionSnapshot, getMissionSelection, subscribeSelection, selectMissionTarget, selectAllMissionTargets, clearMissionTargets } from './swarm';
import type { SwarmSnapshot, SwarmEvent } from './swarm';
import type { AttackProfile, CommunicationRoute, MissionSimulationState } from './types';
import { MissionClient } from './missionClient';
import { COMMANDS, ROLES, commandParameters } from './commandParameters';
import AttackLab from './AttackLab';
import './mission-simulation.css';
import './swarm.css';

export default function SwarmCommandCenter({snapshot:initialSnapshot,api,token,onSnapshot,onInspect}:{snapshot:SwarmSnapshot;api:string;token:string;onSnapshot:(s:SwarmSnapshot)=>void;onInspect:(id:string)=>void}){
 const snapshot=useSyncExternalStore(subscribeMission,getMissionSnapshot)??initialSnapshot;
 const selection=useSyncExternalStore(subscribeSelection,getMissionSelection);
 const targets=selection.targets,first=selection.inspected||snapshot.nodes[0]?.id||'';
 const [query,setQuery]=useState(''),[fallback,setFallback]=useState(false),[presentation,setPresentation]=useState(false);
 const [category,setCategory]=useState('ALL'),[events,setEvents]=useState<SwarmEvent[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [command,setCommand]=useState('hold'),[linkId,setLinkId]=useState(''),[asset,setAsset]=useState('');
 const [fields,setFields]=useState<Record<string,string|boolean>>({altitude:'120',x:'0',y:'100',z:'0',points:'[[0,100,0],[100,100,100]]',loop:false,family:'mesh',role:'relay',formation:'v'});
 const [runtime,setRuntime]=useState<Record<string,unknown>>({}),[now,setNow]=useState(()=>Date.now());
 const clock=useRef(createClock(createWorld([]))),panel=useRef<HTMLElement>(null);
 const client=useMemo(()=>new MissionClient(api,token,publishMissionSnapshot),[api,token]);
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[]);
 useEffect(()=>{
  let disposed=false;
  const poll=()=>client.read('/runtime').then(data=>{if(!disposed)setRuntime(data);}).catch(()=>{if(!disposed)setRuntime({readiness:'Unavailable'});});
  void poll();const timer=setInterval(poll,3000);return ()=>{disposed=true;clearInterval(timer);};
 },[client,snapshot.session_id]);
 const stale=now>snapshot.timestamp*1000+3000;
 const drone=snapshot.nodes.find(n=>n.id===first),link=snapshot.links.find(l=>l.id===linkId);
 const fleet=useMemo(()=>snapshot.nodes.map((n,index)=>({drone_id:n.id,display_name:n.alias,index:index+1,enabled:true})),[snapshot.nodes]);
 const state=useMemo<MissionSimulationState>(()=>{
  const confirmed=snapshot.flows.find(f=>f.source===first&&f.destination==='base'&&f.traffic_class==='telemetry');
  const conditions=drone?.active_attacks?.filter(a=>a.group==='ew')??[];
  const jammedPaths=[...new Set(conditions.filter(a=>['spot','sweep','barrage','smart','reactive','adaptive'].includes(a.profile)).flatMap(a=>a.effective_paths))] as CommunicationRoute[];
  const base=adaptMissionSimulationState(null,{droneId:first,route:drone?.selected_route||'unknown',threatLevel:drone?.threat==null?'UNKNOWN':drone.threat>.5?'HIGH':'LOW',reward:0,step:0,noSafeRoute:!confirmed,availablePaths:[]});
  return {...base,installedRoute:confirmed?.family??null,sdnApplied:!!confirmed,source:'synthetic',inferenceSource:drone?.security?.source,
   activeAttack:(conditions.find(a=>a.profile!=='fhss')?.profile??conditions[0]?.profile??'none') as AttackProfile,jammedPaths,
   routes:base.routes.map((r,i)=>({...r,active:confirmed?.family===r.id,jammed:jammedPaths.includes(r.id),threatScore:drone?.security?.scores?.[i]})),
   flConfidence:drone?.security?.confidence,attackClass:drone?.security?.attack_type,trust:drone?.trust??undefined,containmentMode:drone?.containment,
   decisionSource:drone?.security?.routing_source,safetyOverride:drone?.security?.safety_override??false,insiderRisk:drone?.security?.insider_risk,
   gps:conditions.some(a=>a.profile==='gps_spoofing')?{latitude:0,longitude:0,drift_m:Math.sqrt(28800)}:undefined};
 },[first,drone,snapshot.flows]);
 const lastSession=useRef(snapshot.session_id);
 useEffect(()=>{setEvents(old=>{const same=lastSession.current===snapshot.session_id;lastSession.current=snapshot.session_id;const byId=new Map((same?old:[]).map(e=>[e.cursor,e]));for(const e of snapshot.events)byId.set(e.cursor,e);return [...byId.values()].sort((a,b)=>a.cursor-b.cursor).slice(-300);});},[snapshot.events,snapshot.session_id]);
 function select(id:string,additive=false){selectMissionTarget(id,additive);onInspect(id);setAsset('');}
 function focusGround(){setAsset('Ground control');panel.current?.scrollIntoView({block:'nearest'});panel.current?.focus();}
 async function mutate(path:string,body:Record<string,unknown>,method='POST'){
  setBusy(true);setError('');
  try{const result=await client.mutate(path,body,method);const current=getMissionSnapshot();if(current)onSnapshot(current);return result;}
  catch(reason){setError(reason instanceof Error?reason.message:'Request failed');return undefined;}
  finally{setBusy(false);}
 }
 function send(){try{
  const parameters=commandParameters(command,fields);
  if(command==='formation'&&targets.length!==snapshot.nodes.length)throw new Error('Formation commands require the entire fleet. Choose Select all.');
  void mutate('/commands',{session_id:snapshot.session_id,command_id:crypto.randomUUID(),targets,action:command,parameters,ttl_s:10});
 }catch(reason){setError(reason instanceof Error?reason.message:'Invalid parameters');}}
 const field=(name:string,value:string|boolean)=>setFields(old=>({...old,[name]:value}));
 const visible=snapshot.nodes.filter(n=>`${n.alias} ${n.id} ${n.role}`.toLowerCase().includes(query.toLowerCase()));
 const span=Math.max(400,...snapshot.nodes.map(n=>Math.max(Math.abs(n.position[0]),Math.abs(n.position[2]))))*1.15;
 const position=new Map(snapshot.nodes.map(n=>[n.id,n.position]));position.set('base',snapshot.base_position);
 const clients=runtime.clients as {server_available?:boolean;status?:string}|undefined;
 return <section className={`swarm-center${presentation?' swarm-presentation':''}`} aria-label="Swarm command center">
  <header><div><p className="swarm-eyebrow">FLARE / CONTROLLED RESEARCH SIMULATION</p><h2>Swarm command center</h2><p>Packet simulation · logical SDN · {snapshot.controller_connected?'command link online':'autonomous flight / command link lost'}</p></div><div className="swarm-clock">T+{snapshot.simulation_time.toFixed(1)}s <span>SEED {snapshot.seed}</span><button onClick={()=>setPresentation(!presentation)}>{presentation?'Exit presentation':'Presentation'}</button></div></header>
  <div className="swarm-toolbar">
   <button disabled={busy} onClick={()=>void mutate('/control',{action:snapshot.playing?'pause':'play'})}>{snapshot.playing?'Pause mission':'Play mission'}</button>
   <button disabled={busy} onClick={()=>void mutate('/control',{action:'reset'})}>Reset</button>
   <label>UAVs<select aria-label="Fleet size" value={snapshot.nodes.length} disabled={busy} onChange={e=>void mutate('/config',{count:Number(e.target.value)},'PATCH')}>{[10,20,30,50,75,100].map(n=><option key={n}>{n}</option>)}</select></label>
   <label>Topology<select aria-label="Topology" value={snapshot.topology} disabled={busy} onChange={e=>void mutate('/config',{topology:e.target.value},'PATCH')}>{TOPOLOGIES.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
   <label>Formation<select aria-label="Formation" value={snapshot.formation} disabled={busy} onChange={e=>void mutate('/config',{formation:e.target.value},'PATCH')}>{FORMATIONS.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
   <label>Speed<select aria-label="Speed" value={snapshot.speed} disabled={busy} onChange={e=>void mutate('/config',{speed:Number(e.target.value)},'PATCH')}>{[.25,.5,1,2,4].map(n=><option key={n} value={n}>{n}×</option>)}</select></label>
   <label>Scenario<select aria-label="Scenario" value={snapshot.scenario} disabled={busy} onChange={e=>void mutate(`/scenarios/${e.target.value}`,{action:'reset'})}>{['normal','large_manet','jamming','poisoning','failures','controller_loss','rerouting'].map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
   <button onClick={()=>setFallback(!fallback)}>{fallback?'3D world':'2D fallback'}</button><button onClick={focusGround}>Ground control</button>
  </div>
  {error&&<p className="swarm-error" role="alert">{error}</p>}
  {stale&&<p className="swarm-error" role="status">Snapshot stale · awaiting the authoritative simulation service. This is separate from command-link loss.</p>}
  {snapshot.event_gap&&<p className="swarm-note">Event display was sampled; use the paginated mission events API for the retained trace.</p>}
  <div className="swarm-runtime" aria-label="Service readiness">{Object.entries(snapshot.runtime??{}).filter(([key])=>key!=='available_paths').map(([key,value])=><span key={key}>{title(key)}: {title(value.status||'Unavailable')}</span>)}<span>BiLSTM: {snapshot.runtime?.orchestrator?.fl_model_loaded?'loaded':'Unavailable'}</span><span>DQN source: {snapshot.runtime?.orchestrator?.model_source??'awaiting inference'}</span><span>Client pool: {clients?.status??(clients?.server_available?'server reachable':'Unavailable / not started')}</span><span>Installed rules: {snapshot.flows.length}</span>{runtime.readiness==='Unavailable'&&<span>Readiness unavailable</span>}</div>
  <div className="swarm-statistics">{[['Active UAVs',snapshot.statistics.active],['Links',snapshot.statistics.active_links],['Packet delivery',`${(snapshot.statistics.pdr*100).toFixed(1)}%`],['Latency',`${snapshot.statistics.average_latency_ms.toFixed(0)}ms`],['Routes',snapshot.statistics.active_routes],['Reroutes',snapshot.statistics.reroutes],['Quarantined',snapshot.statistics.quarantined]].map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
  <div className="swarm-layout"><div className="swarm-viewport">
   {!fallback?<MissionWorldCanvas mission={snapshot} state={state} drones={fleet} selectedDroneId={first} selectedDroneIds={targets} paused={!snapshot.playing} evidenceCurrent={!stale} worldClock={clock} onSelectDrone={select} onSelectAsset={a=>a==='base'?focusGround():setAsset(title(a))} onFallback={()=>setFallback(true)} onDismiss={()=>setAsset('')}/>:<svg role="img" aria-label="Authoritative 2D mission network" viewBox={`${-span} ${-span} ${span*2} ${span*2}`}>
    {snapshot.links.map(l=>{const a=position.get(l.a),b=position.get(l.b);return a&&b?<line key={l.id} x1={a[0]} y1={a[2]} x2={b[0]} y2={b[2]} stroke={l.status==='blocked'?'#cd826c':'#527380'} strokeWidth={linkId===l.id?5:1} onClick={()=>setLinkId(l.id)}><title>{l.id} · {l.status}</title></line>:null;})}
    <g role="button" aria-label="2D Ground control" tabIndex={0} onClick={focusGround} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();focusGround();}}}><rect x={snapshot.base_position[0]-8} y={snapshot.base_position[2]-8} width="16" height="16" fill="#efd69a"/></g>
    {snapshot.nodes.map(n=><g key={n.id} role="button" aria-label={`2D ${n.alias}`} aria-pressed={targets.includes(n.id)} tabIndex={0} onClick={e=>select(n.id,e.shiftKey||e.ctrlKey||e.metaKey)} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select(n.id,e.shiftKey||e.ctrlKey||e.metaKey);}}}><circle cx={n.position[0]} cy={n.position[2]} r={targets.includes(n.id)?9:6} stroke={targets.includes(n.id)?'#efd69a':'transparent'} strokeWidth={3} fill={n.containment==='quarantined'?'#cd826c':!n.available?'#647078':n.active_attacks?.length?'#d4ad72':'#91d4c4'}/>{(targets.includes(n.id)||snapshot.nodes.length<=30)&&<text x={n.position[0]+10} y={n.position[2]} fontSize="12" fill="#cbd9dc">{n.alias}</text>}<title>{n.alias} {n.id}</title></g>)}
   </svg>}
   <div className="swarm-legend"><span>○ UAV · gold: command target</span><span>— radio link</span><span>green: installed hops</span><span>cyan: telemetry</span><span>gold: command / acknowledgment</span><span>violet: FL update</span></div>
   <AttackLab snapshot={snapshot} targets={targets} busy={busy} mutate={mutate}/>
   </div><aside className="swarm-inspector" aria-label="Central control" tabIndex={-1} ref={panel}>
    <h3>Central control <span>{targets.length} selected</span></h3><p className="swarm-note">Inspected: {drone?.alias||'None'} · command targets: {targets.length}. Shift / Ctrl / ⌘ adds or removes a target.</p>
    {asset&&<p role="status">Inspecting {asset}</p>}
    <input aria-label="Search fleet" placeholder="Search UAV, identity, role" value={query} onChange={e=>setQuery(e.target.value)}/>
    <div className="swarm-selection"><button onClick={selectAllMissionTargets}>Select all</button><button onClick={clearMissionTargets}>Clear</button></div>
    <div className="swarm-fleet" role="group" aria-label="Fleet selection">{visible.map(n=><button key={n.id} aria-pressed={targets.includes(n.id)} onClick={e=>select(n.id,e.shiftKey||e.ctrlKey||e.metaKey)}><span>{n.alias}</span><small>{n.available?n.role:'OFFLINE'} · {Math.round(n.battery*100)}%</small></button>)}</div>
    <label>Command<select aria-label="Command" value={command} onChange={e=>{setCommand(e.target.value);setError('');}}>{COMMANDS.map(n=><option key={n} value={n}>{n==='hold'?'Flight HOLD':title(n)}</option>)}</select></label>
    {command==='altitude'&&<label>Altitude (local m, 35–300)<input aria-label="Command altitude" type="number" min="35" max="300" value={String(fields.altitude)} onChange={e=>field('altitude',e.target.value)}/></label>}
    {command==='move'&&<div className="swarm-coordinate-fields">{['x','y','z'].map(k=><label key={k}>{k.toUpperCase()} (local m)<input aria-label={`Command ${k}`} type="number" min={k==='y'?35:-10000} max={k==='y'?300:10000} value={String(fields[k])} onChange={e=>field(k,e.target.value)}/></label>)}</div>}
    {command==='waypoints'&&<><label>Waypoints ([x,y,z], local m)<textarea aria-label="Command waypoints" value={String(fields.points)} onChange={e=>field('points',e.target.value)}/></label><label className="swarm-check"><input aria-label="Loop waypoints" type="checkbox" checked={fields.loop===true} onChange={e=>field('loop',e.target.checked)}/>Loop waypoints</label></>}
    {command==='route'&&<label>Route preference<select aria-label="Command route" value={String(fields.family)} onChange={e=>field('family',e.target.value)}>{['direct','satellite','mesh','hold'].map(n=><option key={n} value={n}>{n==='hold'?'Network HOLD':title(n)}</option>)}</select></label>}
    {command==='role'&&<label>Role<select aria-label="Command role" value={String(fields.role)} onChange={e=>field('role',e.target.value)}>{ROLES.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>}
    {command==='formation'&&<><label>Fleet formation<select aria-label="Command formation" value={String(fields.formation)} onChange={e=>field('formation',e.target.value)}>{FORMATIONS.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label><p className="swarm-note">Select all for formation commands. Only receiving UAVs transition; failed deliveries keep their current formation.</p></>}
    <button className="swarm-primary" disabled={busy||!targets.length} onClick={send}>Send to {targets.length} UAV{targets.length===1?'':'s'}</button>
    {!snapshot.playing&&<p className="swarm-note">Paused flight commands remain queued: Waiting for mission to play. Management actions remain available.</p>}
    <button disabled={busy} onClick={()=>void mutate('/control',{action:snapshot.controller_connected?'disconnect_controller':'reconnect_controller'})}>{snapshot.controller_connected?'Disconnect command link':'Restore command link'}</button>
    <p className="swarm-note">Flight HOLD stops movement. Network HOLD suspends data forwarding. Route preferences remain safety constrained. During command-link loss, UAV networking continues autonomously; undelivered commands expire without replay.</p>
    {drone&&<div className="swarm-detail"><h3>Inspected {drone.alias} <small>{drone.id}</small></h3><dl>{Object.entries({Flight:drone.mode,Role:drone.role,Position:drone.position.map(v=>v.toFixed(1)).join(', '),'Reported position':drone.reported_position?.map(v=>v.toFixed(1)).join(', ')??'Unavailable',Battery:`${Math.round(drone.battery*100)}%`,Health:`${Math.round(drone.health*100)}%`,Neighbors:drone.neighbors.length,Cluster:drone.cluster||'None','Radio range':`${drone.range_m.toFixed(0)}m`,'Packets sent':drone.sent,'Packets received':drone.received,'Relay forwards':drone.forwarded,'Source drops':drone.dropped,'Current command':drone.current_command||'None',Trust:drone.trust?.toFixed(2)??'Unavailable',Containment:drone.containment,'Model policy':drone.security?.policy_path??'Awaiting inference','Installed hops':drone.current_path.join(' → ')||'Suspended',Inference:drone.security?.source??'Unavailable','Path risk (D/S/M)':drone.security?.scores?.map(n=>n.toFixed(2)).join(' / ')??'Unavailable','Model class':drone.security?.attack_type??'Unavailable','Traffic risk':drone.security?.insider_risk?.toFixed(2)??'Unavailable',FL:drone.fl_status}).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></div>}
    <label>Inspect link<select aria-label="Inspect link" value={linkId} onChange={e=>setLinkId(e.target.value)}><option value="">Choose link</option>{snapshot.links.map(l=><option key={l.id} value={l.id}>{l.a} ↔ {l.b}</option>)}</select></label>
    {link&&<p className="swarm-note">{link.status} · {link.distance_m.toFixed(0)}m · RSSI {link.rssi.toFixed(1)}dBm · SINR {link.sinr.toFixed(1)}dB · loss {(link.loss*100).toFixed(1)}% · {link.bandwidth_mbps.toFixed(2)}Mbps · {link.latency_ms.toFixed(1)}ms</p>}
   </aside></div>
  <div className="swarm-bottom"><div><h3>Command outcomes</h3><div aria-live="polite">{snapshot.commands.slice(-8).reverse().map(c=><div className="swarm-command-outcome" key={c.command_id}><strong>{title(c.action)}</strong><small>{c.command_id}</small>{Object.entries(c.outcomes).map(([id,o])=><p key={id}>{snapshot.nodes.find(n=>n.id===id)?.alias||id}: {o.status}<span>{o.reason}{o.result?.reason?` · ${o.result.reason}`:''}</span></p>)}</div>)}</div></div><div><h3>Mission event log <select aria-label="Event category" value={category} onChange={e=>setCategory(e.target.value)}>{['ALL','NETWORK','SDN','SECURITY','COMMAND','FL'].map(n=><option key={n}>{n}</option>)}</select></h3><div className="swarm-event-log">{events.filter(e=>category==='ALL'||e.category===category).slice(-40).reverse().map(e=><p key={e.cursor}><time>{e.simulation_time.toFixed(1)}s</time><span>{e.category}</span> {e.kind} <small>{JSON.stringify(e.data).slice(0,180)}</small></p>)}</div></div></div>
  <footer>Radio, movement, encryption policy and forwarding are modeled. Terrain: {String(snapshot.terrain.category||snapshot.terrain.source||'representative extension')}. Real OpenFlow and UAV field behavior remain unvalidated.</footer>
 </section>;
}
