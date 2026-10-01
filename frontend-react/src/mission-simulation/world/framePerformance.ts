export const FRAME_RATE_WINDOW_MS = 5000;
export const FRAME_RATE_WARMUP_MS = 1000;
const REPORT_INTERVAL_MS = 1000;
const MAX_FRAME_SAMPLES = 1200;

export interface FrameRateReport {
  /** Rendered frames divided by elapsed foreground frame time in the recent window. */
  averageFps: number;
  /** Fifth percentile of per-frame FPS; a low-frame-rate indicator, not GPU time. */
  lowFps: number;
  sampleCount: number;
}

/** RAF cadence only. The simulation clock and its fixed step are independent. */
export function createFrameRateSampler() {
  let eligibleSince: number | null = null;
  let previousFrame: number | null = null;
  let warmed = false;
  let lastReport: number | null = null;
  const frames: Array<{ at: number; delta: number }> = [];

  function reset() {
    eligibleSince = null;
    previousFrame = null;
    warmed = false;
    lastReport = null;
    frames.length = 0;
  }

  function record(now: number, eligible: boolean): FrameRateReport | null {
    if (!eligible || !Number.isFinite(now)) { reset(); return null; }
    if (previousFrame !== null && now < previousFrame) { reset(); }
    if (eligibleSince === null) {
      eligibleSince = now;
      previousFrame = now;
      return null;
    }
    if (!warmed) {
      previousFrame = now;
      if (now - eligibleSince >= FRAME_RATE_WARMUP_MS) {
        warmed = true;
        lastReport = now;
      }
      return null;
    }
    const delta = now - previousFrame!;
    previousFrame = now;
    if (delta <= 0) return null;
    frames.push({ at: now, delta });
    while (frames.length && (now - frames[0].at > FRAME_RATE_WINDOW_MS || frames.length > MAX_FRAME_SAMPLES)) frames.shift();
    if (lastReport !== null && now - lastReport < REPORT_INTERVAL_MS) return null;
    lastReport = now;
    const elapsed = frames.reduce((sum, frame) => sum + frame.delta, 0);
    const rates = frames.map(frame => 1000 / frame.delta).sort((a, b) => a - b);
    return {
      averageFps: frames.length * 1000 / elapsed,
      lowFps: rates[Math.floor((rates.length - 1) * .05)],
      sampleCount: frames.length,
    };
  }

  return { record, reset };
}
