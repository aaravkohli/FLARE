import { useState } from 'react';
import type { SwarmSnapshot,SafetyProfile } from './swarm';
import { title } from './swarm';

type Mutate=(path:string,body:Record<string,unknown>,method?:string)=>Promise<unknown>;
export function AutonomySetup({snapshot,busy,mutate}:{snapshot:SwarmSnapshot;busy:boolean;mutate:Mutate}){
 const a=snapshot.autonomy;
 const [draft,setDraft]=useState<SafetyProfile|null>(null),[areas,setAreas]=useState(''),[obstacles,setObstacles]=useState(''),[error,setError]=useState('');
 if(!a)return <p className="swarm-note">Autonomous recovery requires a simulation service update.</p>;
 const p=draft??a.profile;
 const change=(patch:Partial<SafetyProfile>)=>setDraft({...p,...patch});
 function edit(){setDraft(structuredClone(a!.profile));setAreas(JSON.stringify(a!.profile.landing_areas,null,2));setObstacles(JSON.stringify(a!.profile.obstacles,null,2));setError('');}
 async function save(){try{setError('');await mutate('/config',{safety_profile:{...p,landing_areas:JSON.parse(areas),obstacles:JSON.parse(obstacles)}},'PATCH');}catch(e){setError(e instanceof Error?e.message:'Safety profile was not accepted');}}
 return <section className="autonomy-setup" aria-label="Autonomous recovery setup">
  <label>Autonomous recovery<select aria-label="Autonomous recovery" value={a.mode} disabled={busy} onChange={e=>void mutate('/config',{autonomy_mode:e.target.value},'PATCH')}><option value="off">Off</option><option value="recovery_v1">On · local recovery and flight safety</option></select></label>
  <p className="swarm-note">Recovery uses delivered control messages. Flight safety remains latched until a delivered Resume is accepted; landing cannot be cancelled.</p>
  <details><summary>Simulated safety profile</summary><p className="swarm-note">Explicit test-range fixtures. Scenery is not a landing or obstacle sensor. Changes reach UAVs through packet delivery.</p>
   {!draft?<><dl><div><dt>Geofence</dt><dd>±{p.geofence_m} m</dd></div><div><dt>Navigation confidence</dt><dd>{p.navigation_confidence??'Unavailable'}</dd></div><div><dt>Obstacle evidence</dt><dd>{p.obstacle_sensor_available===true?'Simulated available':p.obstacle_sensor_available===false?'Unavailable':'Unknown'}</dd></div></dl><button disabled={busy} onClick={edit}>Edit safety profile</button></>:<div className="autonomy-profile-fields">
   <label>Geofence ± metres<input aria-label="Safety geofence" type="number" min="100" max="10000" value={p.geofence_m} onChange={e=>change({geofence_m:Number(e.target.value)})}/></label>
   <label>Navigation evidence<select aria-label="Navigation evidence" value={p.navigation_confidence==null?'unknown':'known'} onChange={e=>change({navigation_confidence:e.target.value==='unknown'?null:1})}><option value="known">Simulated measurement</option><option value="unknown">Unavailable</option></select></label>
   {p.navigation_confidence!=null&&<label>Navigation confidence<input aria-label="Navigation confidence" type="number" min="0" max="1" step="0.05" value={p.navigation_confidence} onChange={e=>change({navigation_confidence:Number(e.target.value)})}/></label>}
   <label>Obstacle evidence<select aria-label="Obstacle evidence" value={p.obstacle_sensor_available==null?'unknown':String(p.obstacle_sensor_available)} onChange={e=>change({obstacle_sensor_available:e.target.value==='unknown'?null:e.target.value==='true'})}><option value="true">Simulated available</option><option value="false">Unavailable</option><option value="unknown">Unknown</option></select></label>
   <label><input type="checkbox" checked={p.fixture_refresh} onChange={e=>change({fixture_refresh:e.target.checked})}/>Refresh simulated sensor evidence</label>
   <label>Input freshness seconds<input type="number" min="0.5" max="10" step="0.5" value={p.input_freshness_s} onChange={e=>change({input_freshness_s:Number(e.target.value)})}/></label>
   <label>Mission criticality<select value={p.mission_criticality} onChange={e=>change({mission_criticality:e.target.value as SafetyProfile['mission_criticality']})}><option value="routine">Routine</option><option value="critical">Critical · safety still takes priority</option></select></label>
   {(['caution_battery','critical_battery','emergency_battery'] as const).map(k=><label key={k}>{title(k)} fraction<input type="number" min="0" max="1" step="0.01" value={p[k]} onChange={e=>change({[k]:Number(e.target.value)})}/></label>)}
   {(['direct','satellite','mesh'] as const).map(f=><label key={f}>{title(f)} self-test<select value={p.interface_health[f]==null?'unknown':String(p.interface_health[f])} onChange={e=>change({interface_health:{...p.interface_health,[f]:e.target.value==='unknown'?null:e.target.value==='true'}})}><option value="true">Operational fixture</option><option value="false">Failed fixture</option><option value="unknown">Unknown</option></select></label>)}
   <label>Declared landing areas (JSON)<textarea aria-label="Declared landing areas" rows={4} value={areas} onChange={e=>setAreas(e.target.value)}/></label>
   <label>Declared obstacles (JSON)<textarea aria-label="Declared obstacles" rows={4} value={obstacles} onChange={e=>setObstacles(e.target.value)}/></label>
   {error&&<p role="alert" className="swarm-error">{error}</p>}<button disabled={busy} onClick={()=>void save()}>Send safety profile</button><button disabled={busy} onClick={()=>{setDraft(null);setError('');}}>Close profile editor</button>
   </div>}
  </details>
 </section>;
}

export function AutonomyStatus({snapshot,inspected,current=true}:{snapshot:SwarmSnapshot;inspected:string;current?:boolean}){
 const a=snapshot.autonomy;if(!a)return null;
 const n=a.nodes.find(n=>n.node===inspected),s=a.safety.find(s=>s.node===inspected);
 if(a.mode==='off'&&!s?.latched)return null;
 return <section className="autonomy-status" aria-label="Local recovery and safety" data-recovery-state={n?.state??'Unavailable'}>
  <h4>Local recovery · {current?n?.state.replaceAll('_',' ')??'Awaiting receipts':'Connection stale'}</h4>
  <dl><div><dt>Network health</dt><dd>{n?.health.replaceAll('_',' ')??'Unavailable'}</dd></div><div><dt>Flight safety</dt><dd>{s?.action?title(s.action):'Monitoring'}{s?.latched?' · Resume required':''}</dd></div><div><dt>Diagnosis</dt><dd>{n?.diagnosis.label??'Undetermined'}{n?.diagnosis.confidence!=null?` · ${Math.round(n.diagnosis.confidence*100)}%`:''}</dd></div></dl>
  {s?.unresolved.map(reason=><p role="status" className="swarm-error" key={reason}>{reason}</p>)}
  <details><summary>Recovery evidence and safety inputs</summary>
   <p className="swarm-note">Local receipts and simulated sensor fixtures are separate from detector verdicts and DQN decisions. {a.mode==='off'?'New recovery decisions are off; the existing safety latch remains.':''}</p>
   <dl><div><dt>Attempts</dt><dd>{n?.attempts??'—'}</dd></div><div><dt>Stable recovery</dt><dd>{n?.stable?'Confirmed':'Not confirmed'}</dd></div><div><dt>Disconnection onset</dt><dd>{n?.disconnected_since!=null?`T+${n.disconnected_since.toFixed(1)}s`:'—'}</dd></div><div><dt>Navigation confidence</dt><dd>{s?.navigation_confidence??'Unavailable'}</dd></div><div><dt>Obstacle evidence</dt><dd>{s?.obstacle_sensor_available===true?'Simulated available':'Unavailable / unknown'}</dd></div><div><dt>Sensor freshness</dt><dd>{!current?'Connection stale':!snapshot.playing?'Paused':s?.inputs_current?'Current':'Stale / unavailable'}</dd></div><div><dt>Battery safety</dt><dd>{s?.battery_state?title(s.battery_state):'Unavailable'}</dd></div><div><dt>Landing result</dt><dd>{s?.outcome?title(s.outcome):'No completed landing'}</dd></div></dl>
   {Object.entries(n?.ground_paths??{}).map(([family,r])=><p key={family}>{title(family)} round trip · {r.age_s.toFixed(1)}s old · {r.route.map(id=>snapshot.nodes.find(n=>n.id===id)?.alias??id).join(' → ')}</p>)}
   {n?.diagnosis.hypotheses.map(h=><p key={h.cause}>{h.cause} · {Math.round(h.confidence*100)}% · {h.evidence.join(', ')}</p>)}
   {a.transition&&<p>Verifying {title(a.transition.target)} · {a.transition.verified}/{a.transition.required} endpoints · deadline T+{a.transition.deadline.toFixed(1)}s</p>}
  </details>
 </section>;
}
