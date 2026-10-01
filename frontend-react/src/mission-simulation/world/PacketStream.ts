export const PACKET_CAPACITY = 8;
/** Illustrative timing: a visible 0.6s baseline plus reported latency × 12.
 * This is neither a packet capture nor a measured traffic rate. No metric, no stream.
 */
export function advancePacketPhase(phase: number, deltaSeconds: number, latencyMs: number | undefined): number {
  if (!Number.isFinite(phase) || latencyMs == null || !Number.isFinite(latencyMs) || latencyMs < 0) return phase;
  return phase + Math.max(0, Math.min(.1, deltaSeconds)) / (.6 + latencyMs / 1000 * 12);
}
/** Receipt freshness is decided upstream; Phase 9 can pass false without changing route semantics. */
export function packetFlowEnabled(forwarding: boolean, evidenceCurrent = true): boolean {
  return forwarding && evidenceCurrent;
}
function hashCycleSlot(cycle: number, slot: number): number {
  let value = Math.imul((cycle ^ Math.imul(slot + 1, 0x9e3779b1)) >>> 0, 0x85ebca6b) >>> 0;
  value ^= value >>> 13;
  value = Math.imul(value, 0xc2b2ae35) >>> 0;
  return (value ^ value >>> 16) >>> 0;
}
export function packetSample(phase: number, slot: number, latencyMs: number | undefined, loss: number | undefined) {
  if (!Number.isFinite(phase) || !Number.isInteger(slot) || slot < 0 || slot >= PACKET_CAPACITY
    || latencyMs == null || !Number.isFinite(latencyMs) || latencyMs < 0
    || loss == null || !Number.isFinite(loss) || loss < 0 || loss > 1) return null;
  // Fixed, small slot offsets vary visual spacing without inventing packet-rate evidence.
  const jitter = slot === 0 ? 0 : (hashCycleSlot(0, slot + 17) / 4294967296 - .5) * .2 / PACKET_CAPACITY;
  const age = phase + slot / PACKET_CAPACITY + jitter;
  const cycle = Math.floor(age), progress = age - cycle;
  const failed = hashCycleSlot(cycle, slot) / 4294967296 < loss;
  // Aggregate loss has no known position along a link: omit the entire failed pulse.
  return { progress, failed, visible: !failed };
}
