export const PACKET_CAPACITY = 8;
/** Illustrative timing: a visible 0.6s baseline plus authoritative latency × 12.
 * This is not a packet capture or a displayed runtime duration. No metric, no stream.
 */
export function advancePacketPhase(phase: number, deltaSeconds: number, latencyMs: number | undefined): number {
  if (latencyMs == null || !Number.isFinite(latencyMs) || latencyMs < 0) return phase;
  return phase + Math.max(0, Math.min(.1, deltaSeconds)) / (.6 + latencyMs / 1000 * 12);
}
export function packetSample(phase: number, slot: number, latencyMs: number | undefined, loss: number | undefined) {
  if (latencyMs == null || !Number.isFinite(latencyMs) || latencyMs < 0) return null;
  const age = phase + slot / PACKET_CAPACITY;
  const cycle = Math.floor(age), progress = age - cycle;
  const hash = Math.imul((cycle ^ (slot * 2654435761)) >>> 0, 1597334677) >>> 0;
  const validLoss = loss != null && Number.isFinite(loss) && loss >= 0 && loss <= 1;
  const failed = validLoss && hash / 4294967296 < loss;
  return { progress, failed, visible: !failed || progress < .55 };
}
