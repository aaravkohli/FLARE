/** Original display-mesh dimensions; these never enter flight or RF logic. */
export const UAV_VISUAL = Object.freeze({
  bodyWidthM: 12,
  bodyHeightM: 5,
  bodyLengthM: 19,
  motorOffsetM: 13,
  rotorRadiusM: 6,
  selectionRingRadiusM: 18,
  rotorDisplayRadiansPerSecond: 11,
});

/** A low sampled blade rate beneath a faint disc suggests rotation without RPM claims. */
export function rotorDisplayAngle(seconds: number, index: number, reducedMotion: boolean): number {
  return reducedMotion ? 0 : seconds * UAV_VISUAL.rotorDisplayRadiansPerSecond * (index % 2 ? 1 : -1);
}
