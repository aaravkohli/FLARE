import type { SatelliteOrbitState } from './satelliteOrbit.ts';

/** Compressed, always-visible orbital band in SVG render units, NOT metres.
 * This is a schematic projection of geocentric direction, not a local observer's
 * measured azimuth/elevation. Earth occlusion, LOS and coverage are not modelled.
 * Literal orbital positions must never enter the local-world/camera bounds.
 */
export const SATELLITE_SKY_BAND = Object.freeze({ centerX: 620, centerY: 54, halfWidth: 155, halfHeight: 12 });
export function satelliteRenderPosition(state: Pick<SatelliteOrbitState, 'direction'>): { x: number; y: number } {
  const { x, y, z } = state.direction;
  const norm = Math.hypot(x, y, z);
  if (![x, y, z, norm].every(Number.isFinite) || norm <= 0) {
    throw new RangeError('Satellite render direction must be finite and nonzero.');
  }
  return {
    x: SATELLITE_SKY_BAND.centerX + SATELLITE_SKY_BAND.halfWidth * x / norm,
    y: SATELLITE_SKY_BAND.centerY - SATELLITE_SKY_BAND.halfHeight * z / norm,
  };
}
