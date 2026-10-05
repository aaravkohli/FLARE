import {useState} from 'react';
import type {SwarmSnapshot} from './swarm';
import {title} from './swarm';

const fallbackProfiles={ew:['none','spot','sweep','barrage','smart','reactive','adaptive','fhss','spoofing','gps_spoofing','replay','dos'],traffic:['normal','selective_forwarding','telemetry_falsification','control_flood','replay'],fl:['normal','poisoned']};
export default function AttackLab({snapshot,targets,busy,mutate}:{snapshot:SwarmSnapshot;targets:string[];busy:boolean;mutate:(path:string,body:Record<string,unknown>)=>Promise<unknown>}){
 const [group,setGroup]=useState('ew'),[profile,setProfile]=useState('spot'),[paths,setPaths]=useState(['mesh']),[duration,setDuration]=useState('15');
 const profiles:Record<string,string[]>=snapshot.attacks?.capabilities.profiles??fallbackProfiles;
 const active=snapshot.attacks?.active??[];
 const validDuration=Number(duration)>0&&Number(duration)<=300;
 const pathless=group!=='ew'||['none','barrage','sweep','gps_spoofing'].includes(profile);
 const name=(id:string)=>snapshot.nodes.find(n=>n.id===id)?.alias??id;
 return <section className="swarm-attack-lab" aria-label="Attack lab"><h3>Attack lab</h3>
  <div className="swarm-attack-fields">
   <label>Condition group<select aria-label="Condition group" value={group} disabled={busy} onChange={e=>{const next=e.target.value;setGroup(next);setProfile(next==='ew'?'spot':next==='traffic'?'selective_forwarding':'poisoned');}}><option value="ew">Communication / mitigation</option><option value="traffic">Traffic insider</option><option value="fl">Flower poisoning</option></select></label>
   <label>Attack profile<select aria-label="Attack profile" value={profile} disabled={busy} onChange={e=>setProfile(e.target.value)}>{profiles[group].map(p=><option key={p} value={p}>{p==='none'||p==='normal'?'Normal / clear':p==='fhss'?'FHSS mitigation':p==='poisoned'?'Model poisoning':title(p)}</option>)}</select></label>
   <label>Duration (simulation seconds)<input aria-label="Attack duration" type="number" min="1" max="300" value={duration} onChange={e=>setDuration(e.target.value)}/></label>
  </div>
  {!pathless&&<fieldset><legend>Route families</legend>{['direct','satellite','mesh'].map(p=><label key={p} className="swarm-check"><input type="checkbox" aria-label={`Attack ${p} route`} checked={paths.includes(p)} onChange={()=>setPaths(old=>old.includes(p)?old.filter(n=>n!==p):[...old,p])}/>{title(p)}</label>)}</fieldset>}
  <p className="swarm-note">Targets: {targets.length?targets.map(name).join(', '):'Select UAVs in Central control'}</p>
  {profile==='gps_spoofing'&&<p className="swarm-note">Reported position only · no trusted GPS reference.</p>}
  {group==='fl'&&<p className="swarm-note">Arms the selected client’s next participating round. Verdicts come from actual aggregation.</p>}
  <div className="swarm-selection"><button disabled={busy||!targets.length||!validDuration||(!pathless&&!paths.length)} onClick={()=>void mutate('/attacks',{session_id:snapshot.session_id,attack_id:crypto.randomUUID(),group,profile,targets,paths:pathless?[]:paths,duration_s:Number(duration)})}>Apply condition to {targets.length} UAV{targets.length===1?'':'s'}</button>
   <button disabled={busy||!active.length} onClick={()=>void mutate('/attacks/clear',{session_id:snapshot.session_id})}>Clear all conditions</button></div>
  {!snapshot.playing&&<p role="status" className="swarm-note">Mission paused · condition timers and packet traffic wait for Play.</p>}
  <div className="swarm-active-attacks" aria-label="Active conditions">{active.length?active.map(a=><div key={a.attack_id}><strong>{title(a.profile)}</strong> · {a.affected_targets.map(name).join(', ')}<span>{title(a.status)} · {Math.max(0,a.expires_at-snapshot.simulation_time).toFixed(1)}s remaining · {a.effective_paths.join(', ')}</span><small>{Object.entries(a.outcomes).map(([id,o])=>`${name(id)}: ${o.generated_packets??0} injected packets${o.status?` · ${title(o.status)}`:''}${o.rate_capped?' · traffic budget capped':''}`).join(' / ')}</small><button disabled={busy} aria-label={`Clear ${a.profile} condition`} onClick={()=>void mutate('/attacks/clear',{session_id:snapshot.session_id,attack_id:a.attack_id})}>Clear</button></div>):<p>No active conditions</p>}</div>
  <p className="swarm-note">Clearing conditions preserves detector history and containment. Recovery is a separate management action.</p>
 </section>;
}
