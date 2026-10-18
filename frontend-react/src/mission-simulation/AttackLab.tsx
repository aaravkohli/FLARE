import { useState } from 'react';
import { Radio, ShieldCheck, X } from 'lucide-react';
import type { SwarmSnapshot } from './swarm';
import { title } from './swarm';
import { ATTACK_GROUPS, FALLBACK_PROFILES, attackAction, attackTargets, pathlessProfile, profileLabel } from './attackProfiles';
import type { AttackGroup, AttackScope } from './attackProfiles';
type Props = { snapshot: SwarmSnapshot; inspected: string; targets: string[]; busy: boolean; stale: boolean;
  mutate: (path: string, body: Record<string, unknown>) => Promise<unknown> };
export default function AttackLab({snapshot, inspected, targets, busy, stale, mutate}: Props) {
  const [group, setGroup] = useState<AttackGroup>('ew'), [profile, setProfile] = useState('spot');
  const [scope, setScope] = useState<AttackScope>('inspected'), [target, setTarget] = useState('direct');
  const [multiple, setMultiple] = useState(false), [paths, setPaths] = useState(['direct']);
  const [duration, setDuration] = useState('15'), [pending, setPending] = useState(false);
  const [error, setError] = useState(''), [feedback, setFeedback] = useState('');
  const profiles = snapshot.attacks?.capabilities.profiles[group] ?? FALLBACK_PROFILES[group];
  const resolved = attackTargets(scope, inspected, targets, snapshot.nodes.map(n => n.id));
  const active = snapshot.attacks?.active ?? [], relevant = active.filter(a => a.affected_targets.some(id => resolved.includes(id)));
  const pathless = pathlessProfile(group, profile), clear = profile === 'none' || profile === 'normal';
  const validDuration = Number.isFinite(Number(duration)) && Number(duration) >= 1 && Number(duration) <= 300;
  const disabled = busy || pending || stale;
  const name = (id: string) => snapshot.nodes.find(n => n.id === id)?.alias ?? id;
  const targetLabel = resolved.length === 1 ? name(resolved[0]) : `${resolved.length} UAVs`;
  const description = clear ? 'Clears this category for the chosen UAVs. Detector history and containment remain.'
    : profile === 'barrage' ? 'Impairs all three communication families.'
    : profile === 'sweep' ? 'Cycles through Direct, Satellite, and Mesh.'
    : profile === 'gps_spoofing' ? 'Alters reported position; physical flight is unchanged.'
    : profile === 'fhss' ? 'Frequency-hopping mitigation can coexist with active interference.'
    : group === 'fl' ? 'Arms the next participating training round. Aggregation determines the verdict.'
    : profile === 'reactive' ? 'Tracks the route currently used by each target.'
    : profile === 'adaptive' ? 'Adapts its interference target as the routing policy responds.'
    : profile === 'dos' ? 'Injects traffic that consumes modeled queues and airtime.'
    : group === 'traffic' ? 'Changes source or relay behavior; detection depends on observed traffic.'
    : ['spoofing', 'replay'].includes(profile) ? 'Injects telemetry-integrity conditions into the selected route.'
    : 'Impairs the selected communication route. Detection is evaluated separately.';
  async function submit(path: string, body: Record<string, unknown>, message: string) {
    setPending(true); setError(''); setFeedback('');
    try { await mutate(path, body); setFeedback(message); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Request failed. Review current conditions before retrying.'); }
    finally { setPending(false); }
  }
  function apply() {
    // Capture target identities once; subsequent selection cannot retarget an in-flight request.
    void submit('/attacks', {session_id: snapshot.session_id, attack_id: crypto.randomUUID(), group, profile,
      targets: resolved.slice(), paths: pathless ? [] : multiple ? paths.slice() : [target], duration_s: clear ? 15 : Number(duration)},
    `${clear ? 'Conditions cleared' : profileLabel(profile) + ' confirmed'} · ${targetLabel}`);
  }
  return <section className="swarm-attack-lab" aria-label="Simulation event">
    <div className="attack-heading"><h3><Radio size={16} aria-hidden="true"/>Simulation event</h3><span className={relevant.length ? 'is-active' : ''}>{relevant.length ? `${relevant.length} active` : 'Normal'}</span></div>
    <div className="attack-category-row"><label>Condition group<select aria-label="Condition group" value={group} disabled={disabled} onChange={e => {
      const next = e.target.value as AttackGroup; setGroup(next); setProfile(next === 'ew' ? 'spot' : next === 'traffic' ? 'selective_forwarding' : 'poisoned'); setFeedback('');
    }}>{Object.entries(ATTACK_GROUPS).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
    <div className="swarm-attack-fields">
      <label>Profile<select aria-label="Attack profile" value={profile} disabled={disabled} onChange={e => {setProfile(e.target.value); setFeedback('');}}>{profiles.map(p => <option key={p} value={p}>{profileLabel(p)}</option>)}</select></label>
      <label>UAV scope<select aria-label="Attack UAV scope" value={scope} disabled={disabled} onChange={e => {setScope(e.target.value as AttackScope); setFeedback('');}}>
        <option value="inspected">Inspected UAV</option><option value="selected">Selected UAVs ({targets.length})</option><option value="fleet">Entire fleet ({snapshot.nodes.length})</option>
      </select></label>
    </div>
    <p className="attack-targets" aria-label="Attack targets"><span>Targets</span><strong>{resolved.length ? targetLabel : 'No UAVs selected'}</strong>{resolved.length > 1 && <small>{resolved.slice(0,4).map(name).join(', ')}{resolved.length > 4 ? ` +${resolved.length-4} more` : ''}</small>}</p>
    <label>Communication target<select aria-label="Communication target" value={target} disabled={disabled || pathless || multiple} onChange={e => {setTarget(e.target.value);setPaths([e.target.value]);}}>
      {['direct','satellite','mesh'].map(p => <option key={p} value={p}>{title(p)}</option>)}
    </select></label>
    {!pathless && <details className="attack-advanced"><summary>Multiple route families</summary><label className="swarm-check"><input type="checkbox" checked={multiple} disabled={disabled} onChange={e => {setMultiple(e.target.checked);setPaths([target]);}}/>Target multiple families</label>
      {multiple && <fieldset><legend>Route families</legend>{['direct','satellite','mesh'].map(p => <label key={p} className="swarm-check"><input type="checkbox" aria-label={`Attack ${p} route`} checked={paths.includes(p)} disabled={disabled} onChange={() => setPaths(old => old.includes(p) ? old.filter(n => n !== p) : [...old,p])}/>{title(p)}</label>)}</fieldset>}
    </details>}
    <div className="attack-duration"><label htmlFor="attack-duration">Duration <span>simulation seconds</span></label><input id="attack-duration" aria-label="Attack duration" type="number" min="1" max="300" step="1" value={duration} disabled={disabled || clear} aria-invalid={!clear && !validDuration} onChange={e => setDuration(e.target.value)}/>
      <input aria-label="Attack duration slider" type="range" min="5" max="60" step="5" value={Math.max(5, Math.min(60, Number(duration) || 5))} disabled={disabled || clear} onChange={e => setDuration(e.target.value)}/>
    </div>
    {!clear && !validDuration && <p className="attack-error" role="alert">Enter a duration from 1 to 300 seconds.</p>}
    <p className="attack-context">{description}</p>
    <button className="attack-apply" data-testid="attack-apply" disabled={disabled || !resolved.length || !profiles.includes(profile) || (!clear && !validDuration) || (!pathless && multiple && !paths.length)} onClick={apply}>{pending ? 'Sending…' : attackAction(group, profile)}</button>
    {error && <p className="attack-error" role="alert">{error}</p>}
    {feedback && <p className="attack-feedback" role="status"><ShieldCheck size={14} aria-hidden="true"/>{feedback}</p>}
    {stale ? <p className="attack-context" role="status">Simulation connection stale. Waiting for current state.</p> : !snapshot.playing && <p className="attack-paused">Mission paused · timers wait for Play.</p>}
    <div className="swarm-active-attacks" aria-label="Active conditions">
      <div className="active-conditions-heading"><span>Injected conditions · {active.length}</span>{active.length > 0 && <button disabled={disabled} onClick={() => void submit('/attacks/clear', {session_id:snapshot.session_id}, 'All injected conditions cleared.')}>Clear all conditions</button>}</div>
      {active.length ? active.map(a => <div className="active-condition" key={a.attack_id}>
        <div><strong>{profileLabel(a.profile)}</strong><button className="condition-clear" disabled={disabled} aria-label={`Clear ${a.profile} condition`} title="Clear condition" onClick={() => void submit('/attacks/clear', {session_id:snapshot.session_id,attack_id:a.attack_id}, `${profileLabel(a.profile)} cleared.`)}><X size={14}/></button></div>
        <span>{a.affected_targets.length === 1 ? name(a.affected_targets[0]) : `${a.affected_targets.length} UAVs`} · {a.effective_paths.map(title).join(', ') || (a.group === 'fl' ? 'Training' : 'Telemetry')}</span>
        <small>{Math.max(0,a.expires_at-snapshot.simulation_time).toFixed(1)}s remaining · {title(a.status)}{a.profile === 'fhss' ? ' · Mitigation' : ''}</small>
        {Object.values(a.outcomes).some(o => (o.generated_packets ?? 0) > 0) && <small>{Object.values(a.outcomes).reduce((sum,o) => sum+(o.generated_packets??0),0).toLocaleString()} injected packets{Object.values(a.outcomes).some(o => o.rate_capped) ? ' · rate capped' : ''}</small>}
      </div>) : <p className="attack-empty">No active conditions</p>}
      {active.length>0&&<p className="attack-retention-note">Clearing keeps detector history and containment.</p>}
    </div>
  </section>;
}
