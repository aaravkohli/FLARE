import { Fragment, useState } from 'react';
import type { ExperimentState, TopologyMetadata } from './swarm';
import { title } from './swarm';
import type { MissionClient } from './missionClient';
import { RESULT_METRICS, formatMetric, metricDifference, metricValue, resultRows } from './world/workspacePresentation';
import type { MetricKey } from './world/workspacePresentation';
export default function ExperimentResults({job,topologies,client,onError}:{job:ExperimentState;topologies:TopologyMetadata[];client:MissionClient;onError:(error:string)=>void}) {
 const [metric,setMetric]=useState<MetricKey>('pdr'),[seeds,setSeeds]=useState<number[]>(job.configuration.seeds),[sort,setSort]=useState('topology'),[direction,setDirection]=useState<'asc'|'desc'>('asc'),[expanded,setExpanded]=useState<string[]>([]);
 const order=job.configuration.topologies,label=(id:string)=>topologies.find(t=>t.id===id)?.label??title(id);
 const rows=resultRows(job.results,seeds,sort,direction,order),chartRows=resultRows(job.results,seeds,'topology','asc',order);
 const definition=RESULT_METRICS.find(m=>m.key===metric)!,values=chartRows.map(r=>metricValue(r,metric)).filter((v):v is number=>v!=null);
 const maximum=metric==='pdr'?1:Math.max(0,...values)||1;
 const partial=job.status!=='completed',missingLabel=['starting','running','paused'].includes(job.status)?'Pending':['failed','cancelled'].includes(job.status)?'Not run':'—';
 async function download(format:string){try{await client.download(`/${job.id}/export?format=${format}`,`flare-${job.mode}-${job.id}.${format}`);}catch(error){onError(error instanceof Error?error.message:'Export failed');}}
 function sorting(key:string){if(sort===key)setDirection(direction==='asc'?'desc':'asc');else{setSort(key);setDirection('asc');}}
 const header=(key:string,text:string)=><th key={key} aria-sort={sort===key?direction==='asc'?'ascending':'descending':'none'}><button onClick={()=>sorting(key)}>{text}{sort===key?(direction==='asc'?' ↑':' ↓'):''}</button></th>;
 return <section className="experiment-results" aria-label="Packet cohort results">
 <div className="experiment-result-heading"><div><h3>Packet cohort results</h3><span>{partial?'Partial results':'Final results'} · {job.results.length} / {order.length*job.configuration.seeds.length} completed runs · modeled packet simulation</span></div><div><button onClick={()=>void download('csv')}>Export CSV</button><button onClick={()=>void download('json')}>Export JSON</button></div></div>
 {job.mode==='benchmark'&&<><div className="benchmark-chart-controls"><label>Compare metric<select aria-label="Comparison metric" value={metric} onChange={e=>setMetric(e.target.value as MetricKey)}>{RESULT_METRICS.map(m=><option key={m.key} value={m.key}>{m.label}{m.unit?` (${m.unit})`:''}</option>)}</select></label><fieldset><legend>Seeds</legend>{job.configuration.seeds.map((seed,index)=><label key={seed} className="swarm-check"><input aria-label={`Show seed ${seed}`} type="checkbox" checked={seeds.includes(seed)} onChange={e=>setSeeds(old=>e.target.checked?[...old,seed]:old.filter(n=>n!==seed))}/><i style={{background:`var(--seed-${index%3})`}}/>{seed}</label>)}</fieldset><span>Individual seed values · no aggregation</span></div>
 <div className="benchmark-chart" aria-label={`${definition.label} by topology and seed`}>
 {order.map(topology=><div className="benchmark-chart-row" key={topology}><strong>{label(topology)}</strong><div>{job.configuration.seeds.filter(s=>seeds.includes(s)).map(seed=>{
  const row=chartRows.find(r=>r.topology===topology&&r.seed===seed),value=row?metricValue(row,metric):null,index=job.configuration.seeds.indexOf(seed);
  return <div className="seed-bar" key={seed} role="img" aria-label={`${label(topology)}, seed ${seed}: ${row?formatMetric(row,metric):missingLabel}`}><span>S{seed}</span><div className="seed-bar-track" data-unavailable={value==null}>{value!=null&&<i style={{width:`${Math.max(0,Math.min(100,value/maximum*100))}%`,background:`var(--seed-${index%3})`}}/>}</div><output>{row?formatMetric(row,metric):missingLabel}</output></div>;
 })}</div></div>)}{!seeds.length&&<p>Select a seed to display results.</p>}</div>
 <p className="swarm-note">Absent measurements have no bar. Recovery averages describe recovered flows; unrecovered counts remain in the table and row details.</p></>}
 {job.mode==='compare'&&job.results.length===2&&<div className="comparison-differences"><h4>Final cohort differences · B − A</h4><dl>{RESULT_METRICS.map(m=><div key={m.key}><dt>{m.label}</dt><dd>{metricDifference(job.results[0],job.results[1],m.key)}</dd></div>)}</dl></div>}
 <div className="experiment-table-scroll"><table><caption>{partial?'Partial':'Final'} packet cohort measurements</caption><thead><tr>{header('topology','Topology')}{header('seed','Seed')}{RESULT_METRICS.map(m=>header(m.key,m.label))}<th>Details</th></tr></thead><tbody>{rows.map(row=>{
 const key=`${row.topology}-${row.seed}`,open=expanded.includes(key);
 return <Fragment key={key}><tr><th scope="row">{label(row.topology)}</th><td>{row.seed}</td>{RESULT_METRICS.map(m=><td key={m.key}>{formatMetric(row,m.key)}{m.key==='service_recovery_ms'&&!!row.unrecovered_flows&&<small className="unrecovered">{row.unrecovered_flows} unrecovered</small>}</td>)}<td><button aria-label={`Details ${label(row.topology)} seed ${row.seed}`} aria-expanded={open} onClick={()=>setExpanded(old=>open?old.filter(id=>id!==key):[...old,key])}>{open?'Hide':'Details'}</button></td></tr>
 {open&&<tr className="result-detail-row"><td colSpan={12}><dl>{Object.entries({Generated:row.generated,Delivered:row.delivered,Dropped:row.dropped,'Packet conservation':row.conservation?'Confirmed':'Failed','Unrecovered flows':row.unrecovered_flows??'—','Simulator version':job.simulator_version??'—','Routing policy':job.routing_policy??'—'}).map(([name,value])=><div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl><details><summary>Per-flow recovery and provenance</summary><pre>{JSON.stringify({recovery:row.recovery,configuration:job.configuration,source_sha256:job.source_sha256,terrain:job.terrain_provenance},null,2)}</pre></details></td></tr>}</Fragment>;
 })}</tbody></table>{!rows.length&&<p className="swarm-note">No completed runs match the seed filter.</p>}</div>
 </section>;
}
