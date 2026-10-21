import type { SwarmAttack, SwarmSnapshot } from '../swarm.ts';

export type AttackReceiptStatus = 'sending' | 'active' | 'expired' | 'cleared' | 'replaced' | 'ended' | 'awaiting' | 'unavailable' | 'failed';
export interface AttackReceipt { status: AttackReceiptStatus; at: number | null }
export interface AttackSubmission {
  session: string; id: string; operation: 'inject' | 'clear'; profile: string; targets: string[]; attackIds: string[];
  transport: 'sending' | 'accepted' | 'unconfirmed' | 'failed';
  acknowledgedAt?: number; clearAcknowledged?: boolean; error?: string; confirmed?: AttackReceipt;
}
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const terminal = (status: string): AttackReceiptStatus => status === 'expired' || status === 'cleared' || status === 'replaced' ? status : 'ended';
export const receiptLabels: Record<AttackReceiptStatus, string> = {
  sending: 'Sending', active: 'Active', expired: 'Expired', cleared: 'Cleared', replaced: 'Replaced', ended: 'Ended',
  awaiting: 'Awaiting confirmation', unavailable: 'Status unavailable', failed: 'Request failed',
};

/** Uses mission time only. Missing start information never manufactures progress. */
export function attackCountdown(attack: SwarmAttack, simulationTime: number, playing: boolean, stale: boolean) {
  const remaining = finite(attack.expires_at) && finite(simulationTime) ? Math.max(0, attack.expires_at - simulationTime) : null;
  const span = finite(attack.created_at) && finite(attack.expires_at) ? attack.expires_at - attack.created_at : null;
  return {
    remaining,
    fraction: remaining !== null && span !== null && span > 0 ? Math.max(0, Math.min(1, remaining / span)) : null,
    label: stale ? 'Connection stale' : !playing ? 'Paused' : remaining === 0 ? 'Awaiting expiry confirmation' : 'Simulation time',
  };
}

/** Submission identity is captured once; inspection and command selections are irrelevant. */
export function attackReceipt(submission: AttackSubmission | null, snapshot: SwarmSnapshot): AttackReceipt | null {
  if (!submission || submission.session !== snapshot.session_id) return null;
  if (submission.operation === 'inject') {
    const active = snapshot.attacks?.active.find(a => a.attack_id === submission.id);
    if (active) return {status: 'active', at: finite(active.created_at) ? active.created_at : null};
    const history = snapshot.attacks?.history.find(a => a.attack_id === submission.id);
    if (history) return {status: terminal(history.status), at: finite(history.completed_at) ? history.completed_at : null};
    const event = snapshot.events?.findLast(e => e.kind === 'attack_cleared' && e.data.attack_id === submission.id);
    if (event) return {status: terminal(String(event.data.reason)), at: finite(event.simulation_time) ? event.simulation_time : null};
  } else {
    // Partial clears leave the original condition active for its other UAVs.
    const events = snapshot.events?.filter(e => e.kind === 'attack_cleared' && e.data.reason === 'cleared' && submission.attackIds.includes(String(e.data.attack_id))
      && Array.isArray(e.data.targets) && e.data.targets.some(id => submission.targets.includes(String(id)))) ?? [];
    const cleared = submission.attackIds.every(id => !snapshot.attacks?.active.some(a => a.attack_id === id && a.affected_targets.some(n => submission.targets.includes(n))));
    const history = submission.attackIds.map(id => snapshot.attacks?.history.find(a => a.attack_id === id));
    if (cleared && history.length && history.every(a => a && a.status !== 'active')) {
      const statuses = new Set(history.map(a => terminal(a!.status))), times = history.map(a => a!.completed_at).filter(finite);
      return {status: statuses.size === 1 ? [...statuses][0] : 'ended', at: times.length === history.length ? Math.max(...times) : null};
    }
    if (submission.clearAcknowledged || events.length && cleared && snapshot.attacks) {
      const at = events.map(e => e.simulation_time).filter(finite);
      return {status: 'cleared', at: at.length ? Math.max(...at) : finite(submission.acknowledgedAt) ? submission.acknowledgedAt : null};
    }
  }
  if (submission.confirmed && submission.confirmed.status !== 'active') return submission.confirmed;
  if (submission.transport === 'failed') return {status: 'failed', at: null};
  if (submission.confirmed?.status === 'active') return {status: 'unavailable', at: null};
  return {status: submission.transport === 'sending' ? 'sending' : 'awaiting', at: null};
}

export function retainAttackReceipt(submission: AttackSubmission, receipt: AttackReceipt | null): AttackSubmission {
  if (!receipt || !['active', 'expired', 'cleared', 'replaced', 'ended'].includes(receipt.status)
    || submission.confirmed?.status === receipt.status && submission.confirmed.at === receipt.at) return submission;
  return {...submission, confirmed: receipt};
}
