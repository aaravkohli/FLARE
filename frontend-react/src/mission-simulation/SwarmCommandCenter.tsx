import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import MissionWorldCanvas from './world/MissionWorldCanvas';
import NetworkCanvas from './NetworkCanvas';
import NetworkInspector from './NetworkInspector';
import ExperimentPanel from './ExperimentPanel';
import { DEMONSTRATIONS } from './scenarioMetadata';
import { createClock } from './world/clock';
import { createWorld } from './world/dynamics';
import { adaptMissionSimulationState } from './adapter';
import { FORMATIONS, TOPOLOGIES, title, getMissionSnapshot, subscribeMission, publishMissionSnapshot, getMissionSelection, subscribeSelection, selectMissionTarget } from './swarm';
import type { ExperimentState, NetworkDisplay, TopologyMetadata, SwarmSnapshot, SwarmEvent } from './swarm';
import type { AttackProfile, CommunicationRoute, MissionSimulationState } from './types';
import { MissionClient } from './missionClient';
import MissionControlPanel from './MissionControlPanel';
import WorkspaceEvidence from './WorkspaceEvidence';
import type { WorkspaceEvidenceInput } from './world/workspaceEvidence';
import { Play, Pause, SlidersHorizontal, Maximize2 } from 'lucide-react';
import { profileLabel } from './attackProfiles';
import AttackLab from './AttackLab';
import './mission-simulation.css';
import './swarm.css';

export default function SwarmCommandCenter({snapshot:initialSnapshot,api,token,evidence,onSnapshot,onInspect}:{snapshot:SwarmSnapshot;api:string;token:string;evidence:WorkspaceEvidenceInput;onSnapshot:(s:SwarmSnapshot)=>void;onInspect:(id:string)=>void}){
 const snapshot=useSyncExternalStore(subscribeMission,getMissionSnapshot)??initialSnapshot;
 const selection=useSyncExternalStore(subscribeSelection,getMissionSelection);
 const targets=selection.targets,first=selection.inspected||snapshot.nodes[0]?.id||'';
 const [experiments,setExperiments]=useState<Partial<Record<'compare'|'benchmark',ExperimentState>>>({});
 const rememberExperiment=useMemo(()=>(job:ExperimentState)=>setExperiments(old=>({...old,[job.mode]:job})),[]);
 const [workspace,setWorkspace]=useState<'mission'|'compare'|'benchmark'>('mission'),[inspector,setInspector]=useState(true);
 const [display,setDisplay]=useState<NetworkDisplay>({packets:true,inactive:true,ranges:false,hops:[]});
 const [topologies,setTopologies]=useState<TopologyMetadata[]>(TOPOLOGIES.map(id=>({id,label:title(id),description:'',suggested_formation:'distributed'})));
 const [fallback,setFallback]=useState(true),[presentation,setPresentation]=useState(false);
 const [category,setCategory]=useState('ALL'),[events,setEvents]=useState<SwarmEvent[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [linkId,setLinkId]=useState(''),[asset,setAsset]=useState('');
 const [toolTab,setToolTab]=useState<'inspect'|'commands'>('inspect');
 const [runtime,setRuntime]=useState<Record<string,unknown>>({}),[now,setNow]=useState(()=>Date.now()),[nowMs,setNowMs]=useState(()=>performance.now());
 const clock=useRef(createClock(createWorld([]))),panel=useRef<HTMLElement>(null),root=useRef<HTMLElement>(null);
 const client=useMemo(()=>new MissionClient(api,token,publishMissionSnapshot),[api,token]);
 useEffect(()=>{let alive=true;void client.read('/topologies').then((value:TopologyMetadata[])=>{if(alive)setTopologies(value);}).catch(()=>{});return ()=>{alive=false;};},[client]);
 useEffect(()=>{const timer=setInterval(()=>{setNow(Date.now());setNowMs(performance.now());},1000);return ()=>clearInterval(timer);},[]);
 useEffect(()=>{
  let disposed=false;
  const poll=()=>client.read('/runtime').then(data=>{if(!disposed)setRuntime(data);}).catch(()=>{if(!disposed)setRuntime({readiness:'Unavailable'});});
  void poll();const timer=setInterval(poll,3000);return ()=>{disposed=true;clearInterval(timer);};
 },[client,snapshot.session_id]);
 useEffect(()=>{
  if(!presentation)return;
  const previousFocus=document.activeElement as HTMLElement|null;
  const overflow=document.body.style.overflow;document.body.style.overflow='hidden';
  const keyboard=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){event.preventDefault();setPresentation(false);return;}
   if(event.key!=='Tab')return;
   const elements=Array.from(root.current?.querySelectorAll<HTMLElement>('button,input,select,textarea,summary,[tabindex="0"]')??[]).filter(e=>!e.hasAttribute('disabled')&&e.getClientRects().length);
   const current=elements.indexOf(document.activeElement as HTMLElement);
   if(event.shiftKey&&current<=0){event.preventDefault();elements.at(-1)?.focus();}
   else if(!event.shiftKey&&(current<0||current===elements.length-1)){event.preventDefault();elements[0]?.focus();}
  };
  document.addEventListener('keydown',keyboard);
  return ()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',keyboard);previousFocus?.focus();};
 },[presentation]);
 const stale=now>snapshot.timestamp*1000+3000;
 const drone=snapshot.nodes.find(n=>n.id===first);
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
 function select(id:string,additive=false){selectMissionTarget(id,additive);setAsset('');}
 const inspectCallback=useRef(onInspect);
 useEffect(()=>{inspectCallback.current=onInspect;},[onInspect]);
 useEffect(()=>{if(first)inspectCallback.current(first);},[first,snapshot.session_id]);
 function focusGround(){setInspector(true);setToolTab('commands');setAsset('Ground control');requestAnimationFrame(()=>{panel.current?.scrollIntoView({block:'nearest'});panel.current?.focus();});}
 async function mutate(path:string,body:Record<string,unknown>,method='POST'){
  setBusy(true);setError('');
  try{const result=await client.mutate(path,body,method);const current=getMissionSnapshot();if(current)onSnapshot(current);return result;}
  catch(reason){setError(reason instanceof Error?reason.message:'Request failed');return undefined;}
  finally{setBusy(false);}
 }
 async function mutateAttack(path:string,body:Record<string,unknown>){
  setBusy(true);
  try{return await client.mutate(path,body);}finally{setBusy(false);}
 }
 function describeEvent(event:SwarmEvent){
  const d=event.data,id=String(d.node??d.source??''),name=snapshot.nodes.find(n=>n.id===id)?.alias;
  if(event.kind==='attack_injected')return `${profileLabel(String(d.profile))} injected`;
  if(event.kind==='attack_cleared')return 'Injected condition cleared';
  if(event.kind==='route_proposed')return `${name??'Policy'} requested ${d.requested_family??'route'}`;
  if(event.kind==='detector_observation')return `${name??'Detector'} · ${d.attack_type??'observation received'}`;
  if(event.kind==='INSTALL')return `SDN installed ${d.family??d.network_action??'rule'}`;
  if(event.kind==='BLOCK')return 'Link blocked';
  if(event.kind==='command_executed')return `${name??'UAV'} · ${title(String(d.action))}`;
  return title(event.kind);
 }
 const clients=runtime.clients as {server_available?:boolean;status?:string}|undefined;
 return <section className={`swarm-center live-workspace${presentation?' swarm-presentation':''}`} aria-label="Swarm command center" ref={root}>
  <header><div><p className="swarm-eyebrow">FLARE / CONTROLLED RESEARCH SIMULATION</p><h2>Mission workspace</h2><p>{snapshot.controller_connected?'command link online':'autonomous flight / command link lost'}</p></div><div className="swarm-clock">T+{snapshot.simulation_time.toFixed(1)}s <span>SEED {snapshot.seed}{snapshot.pending_seed!=null?` · NEXT RESET ${snapshot.pending_seed}`:''}</span><button onClick={()=>setPresentation(!presentation)}><Maximize2 size={14}/>{presentation?'Exit presentation':'Presentation'}</button></div></header>
  <nav className="network-workspace-tabs" aria-label="Simulation workspace">{(['mission','compare','benchmark'] as const).map(tab=><button key={tab} aria-pressed={workspace===tab} onClick={()=>setWorkspace(tab)}>{tab==='mission'?'Live mission':tab==='compare'?'A/B comparison':'Topology benchmark'}</button>)}</nav>
  {workspace==='mission'&&<>
   <div className="swarm-toolbar">
    <button className="mission-play" disabled={busy||stale} onClick={()=>void mutate('/control',{action:snapshot.playing?'pause':'play'})}>{snapshot.playing?<Pause size={15}/>:<Play size={15}/>} {snapshot.playing?'Pause mission':'Play mission'}</button>
   <label>Speed<select aria-label="Speed" value={snapshot.speed} disabled={busy} onChange={e=>void mutate('/config',{speed:Number(e.target.value)},'PATCH')}>{[.1,.25,.5,1,2,4].map(n=><option key={n} value={n}>{n}×</option>)}</select></label>
   <label>Topology<select aria-label="Topology" title={topologies.find(t=>t.id===snapshot.topology)?.description} value={snapshot.topology} disabled={busy} onChange={e=>void mutate('/config',{topology:e.target.value},'PATCH')}>{!TOPOLOGIES.includes(snapshot.topology)&&<option value={snapshot.topology}>{title(snapshot.topology)} (legacy)</option>}{topologies.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
    <div className="workspace-view-switch" aria-label="Network view"><button aria-pressed={fallback} onClick={()=>setFallback(true)}>2D network</button><button aria-pressed={!fallback} onClick={()=>setFallback(false)}>3D world</button></div>
    <details className="mission-setup"><summary><SlidersHorizontal size={15}/>Mission setup</summary><div className="mission-setup-fields">
   <label>UAVs<select aria-label="Fleet size" value={snapshot.nodes.length} disabled={busy} onChange={e=>void mutate('/config',{count:Number(e.target.value)},'PATCH')}>{[10,20,30,50,75,100].map(n=><option key={n}>{n}</option>)}</select></label>
   <label>Formation<select aria-label="Formation" value={snapshot.formation} disabled={busy} onChange={e=>void mutate('/config',{formation:e.target.value},'PATCH')}>{FORMATIONS.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
   <label>Scenario<select aria-label="Scenario" value={snapshot.scenario} disabled={busy} onChange={e=>void mutate(`/scenarios/${e.target.value}`,{action:'reset'})}>{[...DEMONSTRATIONS,'large_manet','jamming','poisoning','failures','controller_loss','rerouting'].map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
   <label>Radio range<select aria-label="Radio range" value={snapshot.range_m??350} disabled={busy} onChange={e=>void mutate('/config',{range_m:Number(e.target.value)},'PATCH')}>{[...new Set([snapshot.range_m??350,100,200,350,500,750,1000])].sort((a,b)=>a-b).map(n=><option key={n} value={n}>{n} m</option>)}</select></label>
   <button disabled={busy} onClick={()=>void mutate('/commands',{session_id:snapshot.session_id,command_id:crypto.randomUUID(),targets:snapshot.nodes.map(n=>n.id),action:'formation',parameters:{formation:topologies.find(t=>t.id===snapshot.topology)?.suggested_formation??'distributed'},ttl_s:10})}>Apply suggested formation</button>
   <button disabled={busy} onClick={()=>void mutate('/control',{action:'reset'})}>Reset</button>
     <p>Seed {snapshot.seed}{snapshot.pending_seed!=null?` · next reset ${snapshot.pending_seed}`:''}. Topology changes preserve positions.</p>
    </div></details>
   </div>
  </>}
  {workspace==='mission'?<>
  {error&&<p className="swarm-error" role="alert">{error}</p>}
  {stale&&<p className="swarm-error" role="status">Snapshot stale · awaiting the authoritative simulation service. This is separate from command-link loss.</p>}
  {snapshot.packet_event_gap&&<p className="swarm-note" role="status">Packet trace retention gap · earlier playback is unavailable.</p>}
  <div className="swarm-statistics">{[['Active UAVs',`${snapshot.statistics.active} / ${snapshot.statistics.total}`],['Active links',snapshot.statistics.active_links],['Packet delivery',snapshot.statistics.delivered+snapshot.statistics.dropped?`${(snapshot.statistics.pdr*100).toFixed(1)}%`:'—'],['Latency',snapshot.statistics.delivered?`${snapshot.statistics.average_latency_ms.toFixed(1)} ms`:'—'],['Throughput',`${snapshot.statistics.throughput_mbps.toFixed(3)} Mb/s`],['Components',snapshot.statistics.connected_components??'—']].map(([label,value])=><div key={label}><span>{label}</span><strong>{typeof value==='string'&&value.endsWith(' Mb/s')?<>{value.slice(0,-5)}<small> Mb/s</small></>:value}</strong></div>)}</div>
  <div className="swarm-layout"><div className="swarm-viewport">
   <div className="canvas-toolbar"><span>NETWORK CANVAS</span><div><button onClick={focusGround}>Ground control</button><button aria-expanded={inspector} onClick={()=>setInspector(!inspector)}>{inspector?'Hide inspector':'Show inspector'}</button></div></div>
   <NetworkInspector snapshot={snapshot} client={client} display={display} onDisplay={setDisplay} mutate={mutate}/>
   {!fallback?<MissionWorldCanvas mission={snapshot} visualization={display} state={state} drones={fleet} selectedDroneId={first} selectedDroneIds={targets} paused={!snapshot.playing} evidenceCurrent={!stale} worldClock={clock} onSelectDrone={select} onSelectAsset={a=>a==='base'?focusGround():setAsset(title(a))} onFallback={()=>setFallback(true)} onDismiss={()=>setAsset('')}/>:<NetworkCanvas snapshot={snapshot} selected={first} targets={targets} display={display} current={!stale} onSelect={select} onLink={id=>{setLinkId(id);setInspector(true);setToolTab('inspect');}} onGround={focusGround}/>}
   <div className="swarm-legend"><span><i className="legend-policy"/> FLARE telemetry route</span><span><i className="legend-installed"/> Inspected route</span><span><i className="legend-candidate"/> Candidate route</span><span><i className="legend-degraded"/> Degraded / jammed</span><span><i className="legend-blocked"/> Blocked / disconnected</span><span>▸ Packet event</span></div>
   </div><aside className="swarm-side-panel" tabIndex={-1} ref={panel} aria-label="Mission tools">
    <AttackLab key={snapshot.session_id} snapshot={snapshot} inspected={first} targets={targets} busy={busy} stale={stale} mutate={mutateAttack}/>
    {asset&&<p className="asset-notice" role="status">Inspecting {asset}</p>}
    <div hidden={!inspector}><MissionControlPanel snapshot={snapshot} first={first} targets={targets} busy={busy} tab={toolTab} onTab={setToolTab} linkId={linkId} onLink={setLinkId} select={select} mutate={mutate}/></div>
   </aside></div>
  <WorkspaceEvidence input={evidence} snapshot={snapshot} inspected={first} nowMs={nowMs} current={!stale}/>
  <div className="workspace-event-rail" aria-label="Recent mission events"><span>RECENT EVENTS</span><ol>{events.filter(e=>['SECURITY','SDN','COMMAND'].includes(e.category)).slice(-3).map(e=><li key={e.cursor}><time>T+{e.simulation_time.toFixed(1)}s</time><strong>{describeEvent(e)}</strong></li>)}</ol></div>
  <details className="network-timeline"><summary>Mission timeline & command outcomes</summary><div className="swarm-bottom"><div><h3>Command outcomes</h3><div aria-live="polite">{snapshot.commands.slice(-8).reverse().map(c=><div className="swarm-command-outcome" key={c.command_id}><strong>{title(c.action)}</strong><small>{c.command_id}</small>{Object.entries(c.outcomes).map(([id,o])=><p key={id}>{snapshot.nodes.find(n=>n.id===id)?.alias||id}: {o.status}<span>{o.reason}{o.result?.reason?` · ${o.result.reason}`:''}</span></p>)}</div>)}</div></div><div><h3>Mission event log <select aria-label="Event category" value={category} onChange={e=>setCategory(e.target.value)}>{['ALL','NETWORK','SDN','SECURITY','COMMAND','FL'].map(n=><option key={n}>{n}</option>)}</select></h3><div className="swarm-event-log">{events.filter(e=>category==='ALL'||e.category===category).slice(-40).reverse().map(e=><p key={e.cursor}><time>{e.simulation_time.toFixed(1)}s</time><span>{e.category}</span> {e.kind} <small>{JSON.stringify(e.data).slice(0,180)}</small></p>)}</div></div></div>
  </details>
  <details className="workspace-services"><summary>Service readiness</summary>  <div className="swarm-runtime" aria-label="Service readiness">{Object.entries(snapshot.runtime??{}).filter(([key])=>key!=='available_paths').map(([key,value])=><span key={key}>{title(key)}: {title(value.status||'Unavailable')}</span>)}<span>BiLSTM: {snapshot.runtime?.orchestrator?.fl_model_loaded?'loaded':'Unavailable'}</span><span>DQN source: {snapshot.runtime?.orchestrator?.model_source??'awaiting inference'}</span><span>Client pool: {clients?.status??(clients?.server_available?'server reachable':'Unavailable / not started')}</span><span>Installed rules: {snapshot.flows.length}</span>{runtime.readiness==='Unavailable'&&<span>Readiness unavailable</span>}</div>
</details>
  <footer>Radio, movement, encryption policy and forwarding are modeled. Terrain: {String(snapshot.terrain.category||snapshot.terrain.source||'representative extension')}. Real OpenFlow and UAV field behavior remain unvalidated.</footer>
 </>:<ExperimentPanel key={workspace} mode={workspace} client={client} topologies={topologies} storedJob={experiments[workspace]} onJob={rememberExperiment}/>}
 </section>;
}
