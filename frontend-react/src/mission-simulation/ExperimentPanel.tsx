import { useEffect, useRef, useState } from 'react';
import { MissionClient } from './missionClient';
import NetworkCanvas from './NetworkCanvas';
import type { ExperimentState, NetworkCamera, NetworkDisplay, TopologyMetadata } from './swarm';
import { title } from './swarm';
import { DEMONSTRATIONS } from './scenarioMetadata';

export default function ExperimentPanel({mode,client,topologies,storedJob,onJob}:{mode:'compare'|'benchmark';client:MissionClient;topologies:TopologyMetadata[];storedJob?:ExperimentState;onJob:(job:ExperimentState)=>void}){
 const [left,setLeft]=useState(storedJob?.configuration.topologies[0]??'partial_mesh'),[right,setRight]=useState(storedJob?.configuration.topologies[1]??'self_healing_mesh'),[scenario,setScenario]=useState(storedJob?.configuration.scenario??'normal'),[count,setCount]=useState(storedJob?.configuration.count??30),[range,setRange]=useState(storedJob?.configuration.range_m??350),[seeds,setSeeds]=useState(storedJob?.configuration.seeds.join(',')??'7,42,99'),[duration,setDuration]=useState(storedJob?.configuration.duration_s??60);
 const [job,setJob]=useState<ExperimentState|null>(storedJob??null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState('');
 const [display,setDisplay]=useState<NetworkDisplay>({packets:true,inactive:true,ranges:false,hops:[]});
 useEffect(()=>{if(job)onJob(job);},[job,onJob]);
 const camera=useRef<NetworkCamera>({x:0,z:0,span:0});
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
 async function control(action:string){try{await client.experiment('/'+job!.id+'/control',{action});setJob(await client.experiment('/'+job!.id));}catch(reason){setError(reason instanceof Error?reason.message:'Control failed');}}
 async function download(format:string){try{await client.download(`/${job!.id}/export?format=${format}`,`flare-${mode}-${job!.id}.${format}`);}catch(reason){setError(reason instanceof Error?reason.message:'Export failed');}}
 const active=job&&!['completed','cancelled','failed'].includes(job.status);
 const current=job?.runs?.[0],inspected=selected||current?.nodes[0]?.id||'';
 return <div className="network-experiment" aria-label={mode==='compare'?'Topology comparison':'Topology benchmark'}>
  <div className="experiment-heading"><div><p className="swarm-eyebrow">REPRODUCIBLE EXPERIMENTS</p><h3>{mode==='compare'?'Two topologies. One set of conditions.':'Ten topologies. Repeatable results.'}</h3><p>Mesh-only reference routing · shared geometry, traffic and conditions · FL/DQN inference excluded</p></div>{job&&<span className={`experiment-status status-${job.status}`}>{title(job.status)}</span>}</div>
  <div className="experiment-controls">{mode==='compare'&&<>{[[left,setLeft,'Topology A'],[right,setRight,'Topology B']].map(([value,setValue,label])=><label key={label as string}>{label as string}<select aria-label={label as string} value={value as string} disabled={!!active} onChange={e=>(setValue as (v:string)=>void)(e.target.value)}>{topologies.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>)}</>}
   <label>Scenario<select aria-label="Experiment scenario" value={scenario} disabled={!!active} onChange={e=>setScenario(e.target.value)}>{DEMONSTRATIONS.map(t=><option key={t} value={t}>{title(t)}</option>)}</select></label>
   <label>UAVs<select aria-label="Experiment fleet size" value={count} disabled={!!active} onChange={e=>setCount(Number(e.target.value))}>{[10,20,30,50,75,100].map(n=><option key={n}>{n}</option>)}</select></label>
   <label>Range (m)<input aria-label="Experiment radio range" type="number" min="50" max="1000" value={range} disabled={!!active} onChange={e=>setRange(Number(e.target.value))}/></label>
   <label>{mode==='compare'?'Seed (first value)':'Seeds'}<input aria-label="Experiment seeds" value={seeds} disabled={!!active} onChange={e=>setSeeds(e.target.value)}/></label>
   <label>Duration (s)<input aria-label="Experiment duration" type="number" min="10" max="300" value={duration} disabled={!!active} onChange={e=>setDuration(Number(e.target.value))}/></label>
   <button className="swarm-primary" disabled={busy||!!active||mode==='compare'&&left===right} onClick={()=>void create()}>{mode==='compare'?'Create comparison':'Run all ten'}</button>
  </div>
  {error&&<p className="swarm-error" role="alert">{error}</p>}{job?.error&&<p className="swarm-error" role="alert">{job.error}</p>}
  {!job&&<div className="experiment-empty"><span>01 / {mode==='compare'?'COMPARE':'BENCHMARK'}</span><h4>{mode==='compare'?'See how structure changes resilience.':'Measure every topology under identical conditions.'}</h4><p>{mode==='compare'?'Create a comparison, then play both networks together.':'Five seconds of warmup, a shared traffic cohort, and a final packet TTL drain. Results include all configured seeds.'}</p></div>}
  {job&&<><div className="experiment-progress"><progress aria-label="Experiment progress" max="1" value={job.progress}/><span>{(job.progress*100).toFixed(0)}%</span>{active&&mode==='compare'&&<><button onClick={()=>void control(job.status==='running'?'pause':'play')}>{job.status==='running'?'Pause comparison':'Play comparison'}</button><button onClick={()=>void control('reset')}>Reset comparison</button></>}{active&&<button onClick={()=>void control('cancel')}>Cancel experiment</button>}</div>
   {mode==='compare'&&job.runs&&<><div className="comparison-layers">{([['packets','Packets'],['inactive','Idle links'],['ranges','Selected range']] as const).map(([key,label])=><label key={key}><input type="checkbox" checked={display[key]} onChange={e=>setDisplay({...display,[key]:e.target.checked})}/>{label}</label>)}<label>Inspect UAV<select aria-label="Comparison drone" value={inspected} onChange={e=>setSelected(e.target.value)}>{current?.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}</select></label></div>
    <div className="comparison-panes">{job.runs.map((run,index)=>{const destination=run.nodes[(run.nodes.findIndex(n=>n.id===inspected)+Math.floor(run.nodes.length/2))%run.nodes.length]?.id;const flow=run.flows.find(f=>f.source===inspected&&f.destination===destination&&f.traffic_class==='sensor');return <div className="comparison-pane" key={run.session_id}><header><span>{index?'B':'A'}</span><h4>{topologies.find(t=>t.id===run.topology)?.label??title(run.topology)}</h4><time>T+{run.simulation_time.toFixed(1)}s</time></header><NetworkCanvas snapshot={run} selected={inspected} display={{...display,hops:flow?.hops??[]}} camera={camera} onSelect={setSelected}/><div className="comparison-metrics"><span>PDR <strong>{run.statistics.delivered+run.statistics.dropped?(run.statistics.pdr*100).toFixed(1)+'%':'—'}</strong></span><span>Latency <strong>{run.statistics.delivered?run.statistics.average_latency_ms.toFixed(1)+' ms':'—'}</strong></span><span>Components <strong>{run.statistics.connected_components}</strong></span><span>Hops <strong>{flow?flow.hops.length-1:'—'}</strong></span></div></div>;})}</div>
   </>}
   {job.results.length>0&&<div className="experiment-results"><div className="experiment-result-heading"><h4>Packet cohort results <small>{job.results.length} runs · modeled simulation</small></h4><div><button onClick={()=>void download('json')}>Export JSON</button><button onClick={()=>void download('csv')}>Export CSV</button></div></div><div className="experiment-table-scroll"><table><thead><tr>{['Topology','Seed','Delivery','Latency','Jitter','Throughput','Hops','Components','Recovery'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{job.results.map(r=><tr key={`${r.topology}-${r.seed}`}><th>{topologies.find(t=>t.id===r.topology)?.label??title(r.topology)}</th><td>{r.seed}</td><td><div className="pdr-bar"><span style={{width:`${(r.pdr??0)*100}%`}}/><strong>{r.pdr==null?'—':(r.pdr*100).toFixed(1)+'%'}</strong></div></td><td>{r.average_latency_ms?.toFixed(2)??'—'} ms</td><td>{r.jitter_ms?.toFixed(2)??'—'} ms</td><td>{r.throughput_mbps.toFixed(4)} Mb/s</td><td>{r.average_hops?.toFixed(1)??'—'}</td><td>{r.connected_components}</td><td>{r.service_recovery_ms==null?r.unrecovered_flows?'Unrecovered':'—':r.service_recovery_ms.toFixed(1)+' ms'}{r.unrecovered_flows?` · ${r.unrecovered_flows} unrecovered`:''}</td></tr>)}</tbody></table></div></div>}
   {!!job.trace_files?.length&&<details className="experiment-traces"><summary>Replayable packet traces ({job.trace_files.length})</summary>{job.trace_files.map(name=><button key={name} onClick={()=>void client.download(`/${job.id}/traces/${name}`,name).catch((e:Error)=>setError(e.message))}>{name}</button>)}</details>}
  </>}
 </div>;
}
