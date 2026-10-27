import type {SwarmSnapshot} from './swarm';
import {useState} from 'react';
import {title} from './swarm';
import {assistanceStatus,assistanceMeasurement} from './world/uavAssistance';
import {simulationTimestamp} from './world/topologySelection';
type Mutate=(path:string,body:Record<string,unknown>,method?:string)=>Promise<unknown>;
export function AssistanceSetup({snapshot,busy,mutate}:{snapshot:SwarmSnapshot;busy:boolean;mutate:Mutate}){
 const a=snapshot.topology_selection?.assistance;
 const [pending,setPending]=useState<boolean|null>(null);
 async function change(value:boolean){setPending(value);try{await mutate('/config',{affected_uav_assist:value},'PATCH');}finally{setPending(null);}}
 return <section className="assistance-setup" aria-label="Affected UAV assistance setup">
  <label className="swarm-check"><input type="checkbox" aria-label="Prioritize affected UAVs" checked={pending??a?.enabled??false} disabled={busy||!a||pending!==null} aria-busy={pending!==null} onChange={e=>void change(e.target.checked)}/>Prioritize affected UAVs{pending!==null?' · Applying…':''}</label>
  <p className="swarm-note">Uses legitimate Mesh receipts and delivered verification probes. Independent of flight safety; manual topology stays pinned.</p>
  {!a?<p>Simulation service update required.</p>:a.enabled&&<p role="status">{a.reason}</p>}
 </section>;
}
export function AssistanceStatus({snapshot,inspected,current,detailed=false}:{snapshot:SwarmSnapshot;inspected:string;current:boolean;detailed?:boolean}){
 const selection=snapshot.topology_selection,a=selection?.assistance;
 if(!a?.enabled)return null;
 const n=a.nodes.find(n=>n.node===inspected),attempt=[a.attempt,...a.history.slice().reverse()].find(v=>v?.node===inspected);
 return <section className="autonomy-status assistance-status" aria-label="Affected UAV assistance">
  <h4>Mesh assistance · {assistanceStatus(selection,inspected,current)}</h4>
  <dl><div><dt>Observed delivery / latency</dt><dd>{assistanceMeasurement(n?.pdr,'delivery')} / {assistanceMeasurement(n?.latency_ms,'latency')}</dd></div><div><dt>Eligible completed packets</dt><dd>{n?.completed??'—'}</dd></div></dl>
  {n?.feasibility&&<p>{n.feasibility}</p>}
  {attempt&&<p role="status">{attempt.status} · {title(attempt.target)} · {attempt.committed_at!=null?'switch':'requested'} {simulationTimestamp(attempt.committed_at??attempt.started_at)}{attempt.reason?` · ${attempt.reason}`:''}</p>}
  <details open={detailed}><summary>Assistance decisions and limits</summary>
   <p>{a.reason}{!current?' · Connection stale':!snapshot.playing?' · Paused':''}</p>
   <p className="swarm-note">Mesh only. Transport fallback and route repair are separate. Probes confirm reachability; quality improvement requires later mission measurements. Estimates permit at most 2 percentage points less fleet delivery, 10% more latency, and 0.02 less fleet score.</p>
   <p>Cooldown {selection!.cooldown_remaining_s.toFixed(1)} simulation seconds · {a.counters.switches??0} verified assisted switches</p>
   {a.verification&&<p>Verification · {a.verification.verified}/{a.verification.required} routes · deadline {simulationTimestamp(a.verification.deadline)}</p>}
   {a.candidates.filter(c=>c.node===inspected).map(c=><p key={c.topology}>{title(c.topology)} · estimated service gain {c.utility_gain.toFixed(3)} · {c.reason??'Within modeled cost limits'}</p>)}
  </details>
 </section>;
}
