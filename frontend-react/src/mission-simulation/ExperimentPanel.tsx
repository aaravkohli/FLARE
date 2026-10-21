import {fleetSizeOptions} from './world/workspacePresentation';
import { useEffect, useRef, useState } from 'react';
import { MissionClient } from './missionClient';
import NetworkCanvas from './NetworkCanvas';
import type { ExperimentState, NetworkCamera, NetworkDisplay, TopologyMetadata } from './swarm';
import { title } from './swarm';
import ExperimentResults from './ExperimentResults';
import { CameraMenu, ToolPopover } from './WorkspaceControls';
import type { CanvasControls } from './WorkspaceControls';
import { DEMONSTRATIONS } from './scenarioMetadata';

export default function ExperimentPanel({mode,client,topologies,storedJob,onJob,visible=true}:{visible?:boolean;mode:'compare'|'benchmark';client:MissionClient;topologies:TopologyMetadata[];storedJob?:ExperimentState;onJob:(job:ExperimentState)=>void}){
 const [left,setLeft]=useState(storedJob?.configuration.topologies[0]??'partial_mesh'),[right,setRight]=useState(storedJob?.configuration.topologies[1]??'self_healing_mesh'),[scenario,setScenario]=useState(storedJob?.configuration.scenario??'normal'),[count,setCount]=useState(storedJob?.configuration.count??30),[range,setRange]=useState(storedJob?.configuration.range_m??350),[seeds,setSeeds]=useState(storedJob?.configuration.seeds.join(',')??'7,42,99'),[duration,setDuration]=useState(storedJob?.configuration.duration_s??60);
 const [job,setJob]=useState<ExperimentState|null>(storedJob??null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState('');
 const [display,setDisplay]=useState<NetworkDisplay>({packets:true,inactive:true,ranges:false,hops:[]});
 useEffect(()=>{if(job)onJob(job);},[job,onJob]);
 const camera=useRef<NetworkCamera>({x:0,z:0,span:0}),controls=useRef<CanvasControls|null>(null);
 useEffect(()=>{
  if(!job?.id||['completed','cancelled','failed'].includes(job.status))return;
  let disposed=false,running=false;
  async function poll(){if(running)return;running=true;try{const next=await client.experiment('/'+job!.id);if(!disposed){setJob(next);setError('');}}catch(reason){if(!disposed)setError(reason instanceof Error?reason.message:'Experiment unavailable');}finally{running=false;}}
  const timer=setInterval(()=>void poll(),mode==='compare'?200:1000);void poll();return ()=>{disposed=true;clearInterval(timer);};
 },[client,job?.id,job?.status,mode]);
 async function create(){
  setBusy(true);setError('');
  try{
   const parsed=seeds.split(',').map(n=>Number(n.trim()));if(parsed.some(n=>!Number.isInteger(n)||n<0)||!seeds.trim())throw new Error('Enter comma-separated integer seeds.');
   const next=await client.experiment('',{mode,topologies:mode==='benchmark'?topologies.map(t=>t.id):[left,right],scenario,count,range_m:range,seeds:mode==='compare'?[parsed[0]]:parsed,duration_s:duration,warmup_s:5});
   camera.current={x:0,z:0,span:0};setSelected('');setJob(next);
  }catch(reason){setError(reason instanceof Error?reason.message:'Could not start experiment');}finally{setBusy(false);}
 }
 async function control(action:string){setBusy(true);try{await client.experiment('/'+job!.id+'/control',{action});setJob(await client.experiment('/'+job!.id));}catch(reason){setError(reason instanceof Error?reason.message:'Control failed');}finally{setBusy(false);}}
 const active=job&&!['completed','cancelled','failed'].includes(job.status);
 const current=job?.runs?.[0],inspected=selected||current?.nodes[0]?.id||'';
 const configuration=<div className="experiment-shared-fields">
   <label>Scenario<select aria-label="Experiment scenario" value={scenario} disabled={!!active} onChange={e=>setScenario(e.target.value)}>{DEMONSTRATIONS.map(t=><option key={t} value={t}>{title(t)}</option>)}</select></label>
   <label>UAVs<select aria-label="Experiment fleet size" value={count} disabled={!!active} onChange={e=>setCount(Number(e.target.value))}>{fleetSizeOptions(count).map(n=><option key={n}>{n}</option>)}</select></label>
   <label>Range (m)<input aria-label="Experiment radio range" type="number" min="50" max="1000" value={range} disabled={!!active} onChange={e=>setRange(Number(e.target.value))}/></label>
   <label>{mode==='compare'?'Seed (first value)':'Seeds'}<input aria-label="Experiment seeds" value={seeds} disabled={!!active} onChange={e=>setSeeds(e.target.value)}/></label>
   <label>Duration (s)<input aria-label="Experiment duration" type="number" min="10" max="300" value={duration} disabled={!!active} onChange={e=>setDuration(Number(e.target.value))}/></label>
  </div>;
 return <div className="network-experiment" aria-label={mode==='compare'?'Topology comparison':'Topology benchmark'}>
  <div className="experiment-configuration">{mode==='compare'?<>{[[left,setLeft,'Topology A'],[right,setRight,'Topology B']].map(([value,setValue,label])=><label key={label as string}>{label as string}<select aria-label={label as string} value={value as string} disabled={!!active} onChange={e=>(setValue as (v:string)=>void)(e.target.value)}>{topologies.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>)}<ToolPopover label="Shared settings">{configuration}</ToolPopover><span className="configuration-summary">{count} UAVs · {range} m · {duration}s</span></>:configuration}</div>
  <div className="experiment-action-toolbar"><button className="swarm-primary" disabled={busy||!!active||mode==='compare'&&left===right} onClick={()=>void create()}>{busy?'Working…':mode==='compare'?'Create comparison':'Run all ten'}</button>
   {job&&<><span className={`experiment-status status-${job.status}`}>{error?'Last reported: ':''}{title(job.status)}</span><progress aria-label="Experiment progress" max="1" value={job.progress}/><span>{(job.progress*100).toFixed(0)}%</span>{mode==='compare'&&<time>T+{current?.simulation_time.toFixed(1)??'—'}s</time>}
   {active&&mode==='compare'&&<><button disabled={busy} onClick={()=>void control(job.status==='running'?'pause':'play')}>{job.status==='running'?'Pause comparison':'Play comparison'}</button><button disabled={busy} onClick={()=>void control('reset')}>Reset comparison</button></>}{active&&<button disabled={busy} onClick={()=>void control('cancel')}>Cancel experiment</button>}
   {mode==='benchmark'&&<span>{job.results.length} / {job.configuration.topologies.length*job.configuration.seeds.length} runs{job.current_topology&&active?` · ${title(job.current_topology)} · seed ${job.current_seed??'—'}`:''}</span>}</>}
  </div>
  {error&&<p className="swarm-error" role="alert">{error}</p>}{job?.error&&<p className="swarm-error" role="alert">{job.error}</p>}
  {!job&&<div className="experiment-empty"><h3>{mode==='compare'?'Compare network topologies':'Benchmark all ten topologies'}</h3><p>{mode==='compare'?'Choose two topologies and create a synchronized experiment.':'Run identical traffic and conditions across the selected seeds.'}</p><span>Modeled packet simulation · mesh-only reference routing</span></div>}
  {job&&<>
   {mode==='compare'&&job.runs&&<><div className="comparison-tools"><CameraMenu controls={controls}/><ToolPopover label="Layers"><div className="workspace-options"><label><input type="checkbox" aria-label="Color links by RF quality" checked={display.qualityColors===true} onChange={e=>setDisplay({...display,qualityColors:e.target.checked})}/>Color links by RF quality</label>{([['packets','Packets'],['inactive','Idle links'],['ranges','Selected range']] as const).map(([key,label])=><label key={key}><input type="checkbox" checked={display[key]} onChange={e=>setDisplay({...display,[key]:e.target.checked})}/>{label}</label>)}</div></ToolPopover><label>Inspect UAV<select aria-label="Comparison drone" value={inspected} onChange={e=>setSelected(e.target.value)}>{current?.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}</select></label><span>Synchronized camera · shared clock</span></div>
    <div className="comparison-panes">{job.runs.map((run,index)=>{const destination=run.nodes[(run.nodes.findIndex(n=>n.id===inspected)+Math.floor(run.nodes.length/2))%run.nodes.length]?.id;const flow=run.flows.find(f=>f.source===inspected&&f.destination===destination&&f.traffic_class==='sensor');return <div className="comparison-pane" key={run.session_id}><header><span>{index?'B':'A'}</span><h4>{topologies.find(t=>t.id===run.topology)?.label??title(run.topology)}</h4><time>T+{run.simulation_time.toFixed(1)}s</time></header>{visible&&<NetworkCanvas externalTools controls={index===0?controls:undefined} snapshot={run} selected={inspected} display={{...display,hops:flow?.hops??[]}} camera={camera} onSelect={setSelected}/>}<div className="comparison-metrics" aria-label="Live graph statistics"><span>Delivery <strong>{run.statistics.delivered+run.statistics.dropped?(run.statistics.pdr*100).toFixed(1)+'%':'—'}</strong></span><span>Latency <strong>{run.statistics.delivered?run.statistics.average_latency_ms.toFixed(1)+' ms':'—'}</strong></span><span>Components <strong>{run.statistics.connected_components}</strong></span><span>Selected flow hops <strong>{flow?flow.hops.length-1:'—'}</strong></span></div></div>;})}</div><p className="swarm-note">Live graph statistics. Final packet-cohort measurements appear after the traffic and TTL drain complete.</p>
   </>}
   <ExperimentResults key={job.id} job={job} topologies={topologies} client={client} onError={setError}/>
   {!!job.trace_files?.length&&<details className="experiment-traces"><summary>Replayable packet traces ({job.trace_files.length})</summary>{job.trace_files.map(name=><button key={name} onClick={()=>void client.download(`/${job.id}/traces/${name}`,name).catch((e:Error)=>setError(e.message))}>{name}</button>)}</details>}
  </>}
  <details className="experiment-methodology"><summary>Methodology</summary><p>Modeled packet simulation using deterministic, trust-aware mesh-only reference routing. Shared geometry, motion, traffic, radio configuration and scenario schedule; satellite and ground-control transit excluded. FL/DQN inference is excluded from experiments and remains available in Live Mission.</p><p>{job?.configuration.warmup_s??5}s warmup, {job?.configuration.duration_s??duration}s offered traffic, then final packet TTL drain. Cohort delivery, latency, jitter, throughput and recovery retain the simulator’s definitions. Unrecovered flows stay unrecovered. Radio propagation and terrain are approximations, without hardware validation.</p>{job&&<dl><div><dt>Simulator</dt><dd>{job.simulator_version??'—'}</dd></div><div><dt>Source fingerprint</dt><dd>{job.source_sha256??'—'}</dd></div></dl>}</details>
 </div>;
}
