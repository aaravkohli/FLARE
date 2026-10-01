export type RenderQuality = 'high' | 'balanced';

export const AUTO_BALANCED_FPS = 45;
export const AUTO_BALANCED_REPORTS = 8;
const MAX_REPORT_GAP_MS = 1750;

/** Pixel ratio changes only raster cost; world geometry and simulation stay fixed. */
export function renderPixelRatio(quality: RenderQuality, devicePixelRatio: number): number {
  const device = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  return quality === 'high' ? Math.min(device, 1.5) : Math.min(device, device <= 1 ? .85 : 1);
}

/** Consumes the approximately 1 Hz visible-frame reports after sampler warmup. */
export function createAutoQualityDecision() {
  let consecutiveLow = 0;
  let previousReportAt: number | null = null;

  function reset() { consecutiveLow = 0; previousReportAt = null; }

  function observe(reportAt: number, averageFps: number, quality: RenderQuality, manualHighOverride: boolean): boolean {
    if (quality !== 'high' || manualHighOverride || !Number.isFinite(reportAt) || !Number.isFinite(averageFps)) {
      reset();
      return false;
    }
    if (previousReportAt !== null && (reportAt <= previousReportAt || reportAt - previousReportAt > MAX_REPORT_GAP_MS)) {
      consecutiveLow = 0;
    }
    previousReportAt = reportAt;
    consecutiveLow = averageFps < AUTO_BALANCED_FPS ? consecutiveLow + 1 : 0;
    if (consecutiveLow < AUTO_BALANCED_REPORTS) return false;
    reset();
    return true;
  }

  return { observe, reset };
}
