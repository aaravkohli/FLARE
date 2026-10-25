import {fleetSizeOptions} from './world/workspacePresentation';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import MissionWorldCanvas from './world/MissionWorldCanvas';
import NetworkCanvas from './NetworkCanvas';
import NetworkInspector from './NetworkInspector';
import ExperimentPanel from './ExperimentPanel';
import { DEMONSTRATIONS } from './scenarioMetadata';
import { createClock } from './world/clock';
import { createWorld } from './world/dynamics';
import { adaptMissionSimulationState } from './adapter';
import { FORMATIONS, TOPOLOGIES, title, getMissionSnapshot, subscribeMission, publishMissionSnapshot, getMissionSelection, subscribeSelection, inspectMissionTarget, toggleMissionTarget } from './swarm';
import type { ExperimentState, NetworkDisplay, TopologyMetadata, SwarmSnapshot, SwarmEvent } from './swarm';
import type { AttackProfile, CommunicationRoute, MissionSimulationState } from './types';
import { MissionClient } from './missionClient';
import MissionControlPanel from './MissionControlPanel';
import WorkspaceEvidence from './WorkspaceEvidence';
import type { WorkspaceEvidenceInput } from './world/workspaceEvidence';
import { Play, Pause, Maximize2, PanelRightClose } from 'lucide-react';
import { mergeEvents } from './world/workspacePresentation';
import { ToolPopover, WorkspaceTabs } from './WorkspaceControls';
import type { CanvasControls } from './WorkspaceControls';
import type { NetworkCamera } from './swarm';
import type { CameraRig, CameraMode } from './world/cameraRig';
import WorkspaceBottomPanel from './WorkspaceBottomPanel';
import type { BottomTab } from './WorkspaceBottomPanel';
import ActivityPanel from './ActivityPanel';
import AttackLab from './AttackLab';
import { ActiveTopology, TopologyDiagnostics } from './TopologySelection';
import { topologyControlValue } from './world/topologySelection';
import './mission-simulation.css';
import './swarm.css';

export default function SwarmCommandCenter({snapshot:initialSnapshot,api,token,evidence,onSnapshot,onInspect}:{snapshot:SwarmSnapshot;api:string;token:string;evidence:WorkspaceEvidenceInput;onSnapshot:(s:SwarmSnapshot)=>void;onInspect:(id:string)=>void}){
 const snapshot=useSyncExternalStore(subscribeMission,getMissionSnapshot)??initialSnapshot;
 const selection=useSyncExternalStore(subscribeSelection,getMissionSelection);
 const targets=selection.targets,first=selection.inspected||snapshot.nodes[0]?.id||'';
 const [experiments,setExperiments]=useState<Partial<Record<'compare'|'benchmark',ExperimentState>>>({});
 const rememberExperiment=useMemo(()=>(job:ExperimentState)=>setExperiments(old=>({...old,[job.mode]:job})),[]);
 const [workspace,setWorkspace]=useState<'mission'|'compare'|'benchmark'>('mission'),[inspector,setInspector]=useState(true);
 const [display,setDisplay]=useState<NetworkDisplay>({packets:true,inactive:true,ranges:false,groundLinks:false,qualityColors:false,linkEmphasis:'standard',hops:[]});
 const [topologies,setTopologies]=useState<TopologyMetadata[]>(TOPOLOGIES.map(id=>({id,label:title(id),description:'',suggested_formation:'distributed'})));
 const [fallback,setFallback]=useState(true),[presentation,setPresentation]=useState(false);
 const [eventArchive,setEventArchive]=useState({session:snapshot.session_id,events:[] as SwarmEvent[]}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [linkId,setLinkId]=useState(''),[asset,setAsset]=useState('');
 const [toolTab,setToolTab]=useState<'events'|'inspect'|'commands'>('events');
 const [bottomTab,setBottomTab]=useState<BottomTab>('telemetry'),[quality,setQuality]=useState<'high'|'balanced'>('balanced');
 const canvasControls=useRef<CanvasControls|null>(null),camera2d=useRef<NetworkCamera>({x:0,z:0,span:0}),camera3d=useRef<{rig:CameraRig;mode:CameraMode|'manual';session?:string}|null>(null);
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
   if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();setPresentation(false);return;}
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
 const requestedTopology=snapshot.topology_selection?.requested_target??snapshot.topology;
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
 const events=eventArchive.session===snapshot.session_id?eventArchive.events:[];
 // Retain a bounded journal from external snapshot batches, separately for each session.
 // eslint-disable-next-line react/set-state-in-effect
 useEffect(()=>{setEventArchive(old=>({session:snapshot.session_id,events:mergeEvents(old.session===snapshot.session_id?old.events:[],snapshot.events)}));},[snapshot.events,snapshot.session_id]);
 function select(id:string,additive=false){inspectMissionTarget(id);if(additive)toggleMissionTarget(id);setAsset('');}
 function inspectLink(id:string){const link=snapshot.links.find(l=>l.id===id);if(link&&link.a!==first&&link.b!==first)select(link.a==='base'?link.b:link.a);setLinkId(id);setInspector(true);setToolTab('inspect');}
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
 const clients=runtime.clients as {server_available?:boolean;status?:string}|undefined;
 useEffect(()=>{camera2d.current={x:0,z:0,span:0};},[snapshot.session_id]);
 const workspaceLabels={mission:'Live mission',compare:'A/B comparison',benchmark:'Topology benchmark'};
 return <section className={`swarm-center live-workspace workspace-${workspace}${presentation?' swarm-presentation':''}`} aria-label="Swarm command center" ref={root}>
  <header className="workspace-header"><div className="workspace-brand">FLARE <span>Mission workspace</span></div>
   <nav className="network-workspace-tabs" aria-label="Simulation workspace">{(['mission','compare','benchmark'] as const).map(tab=><button key={tab} aria-pressed={workspace===tab} onClick={()=>setWorkspace(tab)}>{workspaceLabels[tab]}</button>)}</nav>
   <div className="workspace-mission-status"><span title="Live mission status" className={stale?'is-stale':snapshot.controller_connected?'is-connected':'is-stale'}>{stale?'Connection stale':snapshot.controller_connected?snapshot.playing?workspace==='mission'?'Running':'Live running':workspace==='mission'?'Paused':'Live paused':'Command link lost'}</span><time title="Live mission clock" className="swarm-clock">{workspace!=='mission'?'Live ':''}T+{snapshot.simulation_time.toFixed(1)}s</time><button aria-label={presentation?'Exit presentation':'Presentation'} onClick={()=>setPresentation(!presentation)}><Maximize2 size={16}/></button></div>
  </header>
  <div className="mission-workspace-page" hidden={workspace!=='mission'}>
   <div className="swarm-toolbar">
    <button className="mission-play" disabled={busy||stale} onClick={()=>void mutate('/control',{action:snapshot.playing?'pause':'play'})}>{snapshot.playing?<Pause size={15}/>:<Play size={15}/>} {snapshot.playing?'Pause mission':'Play mission'}</button>
    <label>Speed<select aria-label="Speed" value={snapshot.speed} disabled={busy} onChange={e=>void mutate('/config',{speed:Number(e.target.value)},'PATCH')}>{[.1,.25,.5,1,2,4].map(n=><option key={n} value={n}>{n}×</option>)}</select></label>
    <label>Topology<select aria-label="Topology" title={topologies.find(t=>t.id===snapshot.topology)?.description} value={topologyControlValue(snapshot.topology,snapshot.topology_selection)} disabled={busy||stale} onChange={e=>void mutate('/config',e.target.value==='auto'?{topology_mode:'auto'}:{topology_mode:'manual',topology:e.target.value},'PATCH')}><option value="auto">Auto (balanced)</option>{!TOPOLOGIES.includes(requestedTopology)&&<option value={requestedTopology}>{title(requestedTopology)} (legacy)</option>}{topologies.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
    <div className="workspace-view-switch" aria-label="Network view"><button aria-pressed={fallback} onClick={()=>setFallback(true)}>2D network</button><button aria-pressed={!fallback} onClick={()=>setFallback(false)}>3D world</button></div>
    <ToolPopover label="Mission setup" drawer><div className="mission-setup-fields">
     <label>UAVs<select aria-label="Fleet size" value={snapshot.nodes.length} disabled={busy} onChange={e=>void mutate('/config',{count:Number(e.target.value)},'PATCH')}>{fleetSizeOptions(snapshot.nodes.length).map(n=><option key={n}>{n}</option>)}</select></label>
     <label>Formation<select aria-label="Formation" value={snapshot.formation} disabled={busy} onChange={e=>void mutate('/config',{formation:e.target.value},'PATCH')}>{FORMATIONS.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
     <label>Scenario<select aria-label="Scenario" value={snapshot.scenario} disabled={busy} onChange={e=>void mutate(`/scenarios/${e.target.value}`,{action:'reset'})}>{[...DEMONSTRATIONS,'large_manet','jamming','poisoning','failures','controller_loss','rerouting'].map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>
     <label>Radio range<select aria-label="Radio range" value={snapshot.range_m??350} disabled={busy} onChange={e=>void mutate('/config',{range_m:Number(e.target.value)},'PATCH')}>{[...new Set([snapshot.range_m??350,100,200,350,500,750,1000])].sort((a,b)=>a-b).map(n=><option key={n} value={n}>{n} m</option>)}</select></label>
     <button disabled={busy} onClick={()=>void mutate('/commands',{session_id:snapshot.session_id,command_id:crypto.randomUUID(),targets:snapshot.nodes.map(n=>n.id),action:'formation',parameters:{formation:topologies.find(t=>t.id===snapshot.topology)?.suggested_formation??'distributed'},ttl_s:10})}>Apply suggested formation</button>
     <button disabled={busy} onClick={()=>void mutate('/control',{action:'reset'})}>Reset</button>
     <p className="swarm-note">Seed {snapshot.seed}{snapshot.pending_seed!=null?` · next reset ${snapshot.pending_seed}`:''}. Topology changes preserve positions.</p>
     <TopologyDiagnostics snapshot={snapshot}/>
    </div></ToolPopover>
   </div>
   <ActiveTopology snapshot={snapshot} stale={stale}/>
   {(error||stale||snapshot.packet_event_gap)&&<div className="workspace-notices">{error&&<p className="swarm-error" role="alert">{error}</p>}{stale&&<p role="status">Snapshot stale · awaiting the simulation service.</p>}{snapshot.packet_event_gap&&<p role="status">Packet trace retention gap · earlier playback unavailable.</p>}</div>}
   <div className="swarm-statistics">{[['Active UAVs',`${snapshot.statistics.active} / ${snapshot.statistics.total}`],['Active links',snapshot.statistics.active_links],['Packet delivery',snapshot.statistics.delivered+snapshot.statistics.dropped?`${(snapshot.statistics.pdr*100).toFixed(1)}%`:'—'],['Latency',snapshot.statistics.delivered?`${snapshot.statistics.average_latency_ms.toFixed(1)} ms`:'—'],['Throughput',`${snapshot.statistics.throughput_mbps.toFixed(3)} Mb/s`],['Components',snapshot.statistics.connected_components??'—']].map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
   <div className={`swarm-layout${inspector?' has-sidebar':''}`}><div className="swarm-viewport">
    <div className="canvas-toolbar"><NetworkInspector key={snapshot.session_id} active={workspace==='mission'} busy={busy} stale={stale} world={!fallback} controls={canvasControls} quality={quality} onQuality={value=>{setQuality(value);canvasControls.current?.quality?.(value);}} snapshot={snapshot} client={client} display={display} onDisplay={setDisplay} mutate={mutate}>
     <details><summary>Mission assets</summary><div className="workspace-menu"><button onClick={focusGround}>Ground control</button><button onClick={()=>{setAsset('Satellite');setInspector(true);setToolTab('inspect');}}>Satellite</button>{state.activeAttack!=='none'&&<button onClick={()=>{setAsset(`Threat details · ${title(state.activeAttack)}`);setInspector(true);setToolTab('inspect');}}>Threat details</button>}</div></details>
     <details className="workspace-services"><summary>Service readiness</summary><div className="swarm-runtime" aria-label="Service readiness">{Object.entries(snapshot.runtime??{}).filter(([key])=>key!=='available_paths').map(([key,value])=><span key={key}>{title(key)}: {title(value.status||'Unavailable')}</span>)}<span>BiLSTM: {snapshot.runtime?.orchestrator?.fl_model_loaded?'loaded':'Unavailable'}</span><span>DQN source: {snapshot.runtime?.orchestrator?.model_source??'awaiting inference'}</span><span>Client pool: {clients?.status??(clients?.server_available?'server reachable':'Unavailable / not started')}</span><span>Installed rules: {snapshot.flows.length}</span>{runtime.readiness==='Unavailable'&&<span>Readiness unavailable</span>}</div></details>
    </NetworkInspector><button aria-label={inspector?'Hide tools':'Show tools'} aria-expanded={inspector} onClick={()=>setInspector(!inspector)}><PanelRightClose size={16}/><span>{inspector?'Hide tools':'Show tools'}</span></button></div>
    {workspace==='mission'&&(!fallback?<MissionWorldCanvas key={snapshot.session_id} externalTools controls={canvasControls} cameraState={camera3d} quality={quality} onQualityChange={setQuality} mission={snapshot} visualization={display} state={state} drones={fleet} selectedDroneId={first} selectedDroneIds={targets} paused={!snapshot.playing} evidenceCurrent={!stale} worldClock={clock} onSelectDrone={select} onInspectLink={inspectLink} onSelectAsset={a=>a==='base'?focusGround():setAsset(title(a))} onFallback={()=>setFallback(true)} onDismiss={()=>setAsset('')}/>:<NetworkCanvas key={snapshot.session_id} externalTools controls={canvasControls} camera={camera2d} snapshot={snapshot} selected={first} targets={targets} display={display} current={!stale} onSelect={select} onLink={inspectLink} onGround={focusGround}/>)}
   </div><aside className="swarm-side-panel" hidden={!inspector} tabIndex={-1} ref={panel} aria-label="Mission tools">
    <WorkspaceTabs label="UAV tools" value={toolTab} onChange={setToolTab} items={[{id:'events',label:'Events'},{id:'inspect',label:'Inspect'},{id:'commands',label:'Commands'}]}/>
    <div className="sidebar-content"><div role="tabpanel" id="uav-tools-panel-events" aria-label="Events" hidden={toolTab!=='events'}><AttackLab key={snapshot.session_id} snapshot={snapshot} inspected={first} targets={targets} busy={busy} stale={stale} mutate={mutateAttack}/></div>
     {asset&&toolTab!=='events'&&<p className="asset-notice" role="status">Inspecting {asset}</p>}
     <MissionControlPanel snapshot={snapshot} first={first} targets={targets} busy={busy} tab={toolTab} linkId={linkId} onLink={setLinkId} select={id=>{select(id);setToolTab('inspect');}} mutate={mutate}/>
    </div></aside></div>
   <WorkspaceBottomPanel tab={bottomTab} onTab={setBottomTab}><div hidden={bottomTab==='activity'}><WorkspaceEvidence key={snapshot.session_id} input={evidence} snapshot={snapshot} inspected={first} nowMs={nowMs} current={!stale} tab={bottomTab}/></div><div role="tabpanel" id="research-details-panel-activity" aria-label="Activity" hidden={bottomTab!=='activity'}><ActivityPanel snapshot={snapshot} events={events} inspected={first}/></div></WorkspaceBottomPanel>
  </div>
  {(['compare','benchmark'] as const).map(mode=><div key={mode} className="experiment-workspace-page" hidden={workspace!==mode}><ExperimentPanel visible={workspace===mode} mode={mode} client={client} topologies={topologies} storedJob={experiments[mode]} onJob={rememberExperiment}/></div>)}
 </section>;
}
