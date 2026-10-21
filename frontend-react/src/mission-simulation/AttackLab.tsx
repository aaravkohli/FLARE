import { useEffect, useState } from 'react';
import { Radio, ShieldCheck, X } from 'lucide-react';
import type { SwarmSnapshot } from './swarm';
import { title } from './swarm';
import { ATTACK_GROUPS, FALLBACK_PROFILES, attackAction, attackTargets, pathlessProfile, profileLabel } from './attackProfiles';
import type { AttackGroup, AttackScope } from './attackProfiles';
import { MissionRequestError } from './missionClient';
import { attackCountdown, attackReceipt, receiptLabels, retainAttackReceipt } from './world/attackLifecycle';
import type { AttackSubmission } from './world/attackLifecycle';
type Props = { snapshot: SwarmSnapshot; inspected: string; targets: string[]; busy: boolean; stale: boolean;
  mutate: (path: string, body: Record<string, unknown>) => Promise<unknown> };
export default function AttackLab({snapshot, inspected, targets, busy, stale, mutate}: Props) {
  const [group, setGroup] = useState<AttackGroup>('ew'), [profile, setProfile] = useState('spot');
  const [scope, setScope] = useState<AttackScope>('inspected'), [target, setTarget] = useState('direct');
  const [multiple, setMultiple] = useState(false), [paths, setPaths] = useState(['direct']);
  const [duration, setDuration] = useState('15'), [pending, setPending] = useState(false);
  const [submission, setSubmission] = useState<AttackSubmission | null>(null);
  const profiles = snapshot.attacks?.capabilities.profiles[group] ?? FALLBACK_PROFILES[group];
  const resolved = attackTargets(scope, inspected, targets, snapshot.nodes.map(n => n.id));
  const active = snapshot.attacks?.active ?? [], relevant = active.filter(a => a.affected_targets.some(id => resolved.includes(id)));
  const receipt = attackReceipt(submission, snapshot);
  // Retain confirmed terminal evidence even after the backend's bounded history rolls over.
  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- persist transitions from the external mission snapshot stream
    setSubmission(current => current ? retainAttackReceipt(current, attackReceipt(current, snapshot)) : current);
  }, [snapshot]);
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
  async function submit(path: string, body: Record<string, unknown>, captured: AttackSubmission) {
    setPending(true); setSubmission(captured);
    function accepted(value: unknown) {
      const data = value && typeof value === 'object' ? value as Record<string, unknown> : {};
      setSubmission(current => current?.id === captured.id ? {...current, transport: 'accepted',
        clearAcknowledged: captured.operation === 'clear' && (data.status === 'cleared' || Array.isArray(data.active)),
        acknowledgedAt: typeof data.simulation_time === 'number' ? data.simulation_time : undefined} : current);
    }
    try { accepted(await mutate(path, body)); }
    catch (reason) {
      if (reason instanceof MissionRequestError && reason.outcome === 'accepted') accepted(reason.response);
      setSubmission(current => current?.id === captured.id ? {...current,
        transport: reason instanceof MissionRequestError ? reason.outcome === 'unconfirmed' ? 'unconfirmed' : reason.outcome === 'accepted' ? 'accepted' : 'failed' : 'failed',
        error: reason instanceof Error ? reason.message : 'Request failed. Review current conditions before retrying.'} : current);
    }
    finally { setPending(false); }
  }
  function clearConditions(attackId?: string) {
    const conditions = active.filter(a => !attackId || a.attack_id === attackId);
    const captured: AttackSubmission = {session: snapshot.session_id, id: crypto.randomUUID(), operation: 'clear', profile: attackId ? conditions[0]?.profile ?? '' : '',
      targets: [...new Set(conditions.flatMap(a => a.affected_targets))], attackIds: conditions.map(a => a.attack_id), transport: 'sending'};
    void submit('/attacks/clear', {session_id: snapshot.session_id, ...(attackId ? {attack_id: attackId} : {})}, captured);
  }
  function apply() {
    // Capture target identities once; subsequent selection cannot retarget an in-flight request.
    const id = crypto.randomUUID(), captured: AttackSubmission = {session: snapshot.session_id, id, operation: clear ? 'clear' : 'inject', profile,
      targets: resolved.slice(), attackIds: clear ? active.filter(a => a.group === group && a.affected_targets.some(n => resolved.includes(n))).map(a => a.attack_id) : [id], transport: 'sending'};
    void submit('/attacks', {session_id: snapshot.session_id, attack_id: id, group, profile,
      targets: captured.targets, paths: pathless ? [] : multiple ? paths.slice() : [target], duration_s: clear ? 15 : Number(duration)}, captured);
  }
  return <section className="swarm-attack-lab" aria-label="Simulation event">
    <div className="attack-heading"><h3><Radio size={16} aria-hidden="true"/>Simulation event</h3><span className={active.length ? 'is-active' : ''}>{active.length ? `${relevant.length} on target · ${active.length} total` : snapshot.attacks ? 'No active injections' : 'Conditions unavailable'}</span></div>
    <div className="attack-category-row"><label>Condition group<select aria-label="Condition group" value={group} disabled={disabled} onChange={e => {
      const next = e.target.value as AttackGroup; setGroup(next); setProfile(next === 'ew' ? 'spot' : next === 'traffic' ? 'selective_forwarding' : 'poisoned');
    }}>{Object.entries(ATTACK_GROUPS).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
    <div className="swarm-attack-fields">
      <label>Profile<select aria-label="Attack profile" value={profile} disabled={disabled} onChange={e => setProfile(e.target.value)}>{profiles.map(p => <option key={p} value={p}>{profileLabel(p)}</option>)}</select></label>
      <label>UAV scope<select aria-label="Attack UAV scope" value={scope} disabled={disabled} onChange={e => setScope(e.target.value as AttackScope)}>
        <option value="inspected">Inspected UAV</option><option value="selected">Selected UAVs ({targets.length})</option><option value="fleet">Entire fleet ({snapshot.nodes.length})</option>
      </select></label>
    </div>
    <p className="attack-targets" aria-label="Attack targets"><span>Targets</span><strong>{resolved.length ? targetLabel : 'No UAVs selected'}</strong>{resolved.length > 1 && <small>{resolved.slice(0,4).map(name).join(', ')}{resolved.length > 4 ? ` +${resolved.length-4} more` : ''}</small>}</p>
    <label>Communication target<select aria-label="Communication target" value={target} disabled={disabled || pathless || multiple} onChange={e => {setTarget(e.target.value);setPaths([e.target.value]);}}>
      {['direct','satellite','mesh'].map(p => <option key={p} value={p}>{title(p)}</option>)}
    </select></label>
    <div className="attack-duration"><label htmlFor="attack-duration">Injection duration <span>simulation seconds · next injection</span></label><input id="attack-duration" aria-label="Attack duration" type="number" min="1" max="300" step="1" value={duration} disabled={disabled || clear} aria-invalid={!clear && !validDuration} onChange={e => setDuration(e.target.value)}/>
      <input aria-label="Attack duration slider" type="range" min="5" max="60" step="5" value={Math.max(5, Math.min(60, Number(duration) || 5))} disabled={disabled || clear} onChange={e => setDuration(e.target.value)}/>
    </div>
    {!clear && !validDuration && <p className="attack-error" role="alert">Enter a duration from 1 to 300 seconds.</p>}
    <p className="attack-context">{description}</p>
    <button className="attack-apply" data-testid="attack-apply" disabled={disabled || !resolved.length || !profiles.includes(profile) || (!clear && !validDuration) || (!pathless && multiple && !paths.length)} onClick={apply}>{pending ? 'Sending…' : attackAction(group, profile)}</button>
    {receipt && submission && <div className={`attack-receipt receipt-${receipt.status}${receipt.status==='active'?' attack-receipt--quiet':''}`} role="status" data-testid="attack-receipt">
      <div><ShieldCheck size={14} aria-hidden="true"/><strong>{receiptLabels[receipt.status]}{submission.profile && !['normal','none'].includes(submission.profile) ? ` · ${profileLabel(submission.profile)}` : ''}</strong><time>{receipt.at === null ? '—' : `T+${receipt.at.toFixed(1)}s`}</time></div>
      <span>{submission.targets.length > 4 ? `${submission.targets.length} UAVs · targets at submission` : submission.targets.map(name).join(', ') || 'No affected UAVs'}</span>
      {receipt.status==='expired' && <small className="attack-expiry-note">Condition ended automatically.</small>}
      {submission.targets.length > 4 && <details className="condition-targets"><summary>View submitted UAVs</summary><span>{submission.targets.map(name).join(', ')}</span></details>}
    </div>}
    {receipt && submission?.error && ['failed','awaiting','unavailable'].includes(receipt.status) && <p className="attack-error" role="alert">{submission.error}</p>}
    {stale ? <p className="attack-context" role="status">Simulation connection stale. Waiting for current state.</p> : !snapshot.playing && <p className="attack-paused">Mission paused · timers wait for Play.</p>}
    <section className="swarm-active-attacks" aria-label="Active conditions">
      <div className="active-conditions-heading"><span>{active.length ? `Active conditions · ${active.length}` : snapshot.attacks ? 'No active conditions' : 'Condition state unavailable'}</span>{active.length > 0 && <button disabled={disabled} onClick={() => clearConditions()}>Clear all conditions</button>}</div>
      {active.length ? active.map(a => {
        const timer = attackCountdown(a, snapshot.simulation_time, snapshot.playing, stale);
        return <div className="active-condition" key={a.attack_id} data-attack-id={a.attack_id}>
          <div className="condition-row">
            <div className="condition-identity"><strong>{profileLabel(a.profile)}{a.profile === 'fhss' ? ' · Mitigation' : ''}</strong><span className="condition-meta">{a.affected_targets.length > 4 ? `${a.affected_targets.length} UAVs` : a.affected_targets.map(name).join(', ')} · {a.effective_paths.map(title).join(', ') || (a.group === 'fl' ? 'Training' : 'Telemetry')}{a.status!=='active' ? ` · ${title(a.status)}` : ''}</span></div>
            <div className="condition-countdown"><output aria-label="Remaining injection time" aria-live="off" data-testid="attack-remaining">{timer.remaining === null ? '—' : timer.remaining.toFixed(1)}<small>s<span className="condition-remaining-label"> remaining</span></small></output><span>{timer.label==='Simulation time'?'Auto ends':timer.label}</span></div>
            <button className="condition-clear" disabled={disabled} aria-label={`Clear ${a.profile} condition`} title="End condition now" onClick={() => clearConditions(a.attack_id)}><X size={16}/></button>
          </div>
          {timer.fraction !== null && <progress max={1} value={timer.fraction} aria-label="Remaining injection duration" aria-valuetext={`${timer.remaining!.toFixed(1)} simulation seconds remaining`}/>}
          <details className="condition-targets"><summary>{a.affected_targets.length > 4 ? `${a.affected_targets.length} affected UAVs` : 'Condition details'}</summary>
            <span className="condition-target-list">{a.affected_targets.map(name).join(', ')}</span><span>{title(a.status)} · Expires {Number.isFinite(a.expires_at) ? `T+${a.expires_at.toFixed(1)}s` : '—'}</span>
            {Object.values(a.outcomes).some(o => (o.generated_packets ?? 0) > 0) && <span>{Object.values(a.outcomes).reduce((sum,o) => sum+(o.generated_packets??0),0).toLocaleString()} injected packets{Object.values(a.outcomes).some(o => o.rate_capped) ? ' · rate capped' : ''}</span>}
          </details>
        </div>;
      }) : null}
      {active.length>0&&<p className="attack-retention-note">Ends automatically. Detector history and containment are retained.</p>}
    </section>
    {!pathless && <details className="attack-advanced"><summary>Advanced settings</summary><label className="swarm-check"><input type="checkbox" checked={multiple} disabled={disabled} onChange={e => {setMultiple(e.target.checked);setPaths([target]);}}/>Target multiple families</label>
      {multiple && <fieldset><legend>Route families</legend>{['direct','satellite','mesh'].map(p => <label key={p} className="swarm-check"><input type="checkbox" aria-label={`Attack ${p} route`} checked={paths.includes(p)} disabled={disabled} onChange={() => setPaths(old => old.includes(p) ? old.filter(n => n !== p) : [...old,p])}/>{title(p)}</label>)}</fieldset>}
    </details>}
  </section>;
}
