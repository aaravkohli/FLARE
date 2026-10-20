import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { getMissionSnapshot, subscribeMission } from '../mission-simulation/swarm';
import type { InsiderEvidenceSnapshot, InsiderProfile } from '../App';
import type { ActivityEntry } from './presentation';

export const trafficScenarios: { profile: InsiderProfile; label: string; summary: string }[] = [
  { profile: 'selective_forwarding', label: 'Selective forwarding', summary: 'Drops transit traffic.' },
  { profile: 'telemetry_falsification', label: 'Telemetry falsification', summary: 'Reports forwarding that did not occur.' },
  { profile: 'control_flood', label: 'Control flood', summary: 'Overloads the control plane.' },
  { profile: 'replay', label: 'Replay attack', summary: 'Reuses duplicate sequences.' },
];
export type TrafficRun = { target: string; session: string | null; profile: InsiderProfile; startedAt: number; baseline: InsiderEvidenceSnapshot | null; attack: InsiderEvidenceSnapshot | null };
export function useTrafficValidation(api: string, token: string | null, initialTarget: string, enabled: boolean, log: (message: string, meta?: Partial<ActivityEntry>) => void) {
  const mission = useSyncExternalStore(subscribeMission, getMissionSnapshot);
  const [chosenTarget, setTarget] = useState(''), [profile, setProfile] = useState<InsiderProfile>('control_flood');
  const target = chosenTarget || initialTarget;
  const [run, setRun] = useState<TrafficRun | null>(null);
  const [phase, setPhase] = useState<'idle' | 'capturing_baseline' | 'running_attack' | 'complete' | 'failed'>('idle');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const environment = useRef({ token, session: mission?.session_id ?? null });
  useEffect(() => { environment.current = { token, session: mission?.session_id ?? null }; }, [token, mission?.session_id]);
  const pending = useRef(false), controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); }, []);
  useEffect(() => {
    controller.current?.abort();
    // Authentication boundaries discard prior-user state, independently of page navigation.
    // eslint-disable-next-line react/set-state-in-effect
    setBusy(false); pending.current = false;
    setError(null); setMessage(null); setRun(null); setPhase('idle'); setTarget('');
  }, [token]);
  async function execute(reset = false) {
    if (!token || !enabled || !target || pending.current) return;
    const context: TrafficRun = { target, session: getMissionSnapshot()?.session_id ?? null, profile, startedAt: Date.now(), baseline: null, attack: null };
    const abort = new AbortController(); controller.current = abort; pending.current = true; setBusy(true); setError(null);
    const current = () => {
      if (abort.signal.aborted || environment.current.token !== token || (getMissionSnapshot()?.session_id ?? null) !== context.session) throw new Error('Session changed. Previous evidence retained; request was not replayed.');
    };
    const read = async () => {
      current();
      const response = await fetch(`${api}/swarm/insider/${encodeURIComponent(context.target)}`, { headers: { Authorization: `Bearer ${token}` }, signal: abort.signal });
      current();
      if (!response.ok) throw new Error(`Evidence endpoint returned ${response.status}`);
      return await response.json() as InsiderEvidenceSnapshot;
    };
    const post = async (condition: InsiderProfile) => {
      current();
      let response: Response;
      try { response = await fetch(`${api}/swarm/insider/${encodeURIComponent(context.target)}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ profile: condition }), signal: abort.signal }); }
      catch { throw new Error('Request delivery is uncertain. Inspect current conditions before submitting again.'); }
      current();
      if (!response.ok) throw new Error(`Unable to apply ${condition.replaceAll('_', ' ')} (${response.status})`);
    };
    const wait = async (condition: InsiderProfile, baseline: boolean) => {
      let latest: InsiderEvidenceSnapshot | null = null;
      for (let attempt = 0; attempt < 30; attempt++) {
        latest = await read();
        current();
        const observed = latest.available && latest.profile_synchronized && latest.observed_profile === condition && latest.analysis;
        if (observed) {
          if (baseline) context.baseline = latest; else context.attack = latest;
          setRun({...context});
        }
        if (observed && (baseline ? latest.analysis!.status === 'NORMAL' : latest.analysis!.status !== 'NORMAL' && latest.containment?.mode !== 'normal' && Boolean(latest.containment) && latest.sdn_applied)) return latest;
        await new Promise(resolve => window.setTimeout(resolve, 500));
      }
      if(latest && !latest.available && latest.reason)throw new Error(`Evidence unavailable: ${latest.reason.replaceAll('_',' ')}`);
      throw new Error(baseline ? 'Normal risk did not settle before the validation timeout' : latest?.analysis ? `${condition.replaceAll('_', ' ')} was observed but no containment rule was enforced` : `${condition.replaceAll('_', ' ')} evidence did not reach the control loop`);
    };
    try {
      if (reset) {
        setMessage('Clearing the injected traffic condition…');
        await post('normal');
        setMessage('Normal condition requested. Detector history and containment are retained.');
        log(`Traffic condition cleared for ${context.target}.`, { type: 'validation', source: 'traffic', severity: 'info' });
      } else {
        setRun({...context}); setPhase('capturing_baseline'); setMessage('Establishing a measured normal baseline…');
        log(`Starting traffic-defense validation for ${context.target}: normal baseline → ${profile.replaceAll('_', ' ')}.`, { type: 'validation', source: 'traffic', severity: 'info' });
        await post('normal'); context.baseline = await wait('normal', true); current(); setRun({ ...context });
        setPhase('running_attack'); setMessage('Baseline captured. Waiting for attack evidence and confirmed enforcement…');
        await post(context.profile); context.attack = await wait(context.profile, false); current();
        setRun({ ...context }); setPhase('complete'); setMessage('Validation complete: baseline compared with the enforced response.');
        log(`Traffic-defense validation completed for ${context.target}.`, { type: 'validation', source: 'traffic', severity: 'info' });
      }
    } catch (cause) {
      if (environment.current.token !== token) return;
      const detail = cause instanceof Error ? cause.message : 'Traffic-defense validation failed';
      setError(detail); setMessage(null); if (!reset) setPhase('failed');
      log(`Traffic-defense validation failed: ${detail}`, { type: 'validation', source: 'traffic', severity: 'warning' });
    } finally {
      if (controller.current === abort) { setBusy(false); pending.current = false; }
    }
  }
  return { target, setTarget, profile, setProfile, run, phase, busy, error, message, retained: Boolean(run && run.session !== (mission?.session_id ?? null)), execute };
}
export type TrafficValidation = ReturnType<typeof useTrafficValidation>;
