/** Circular, two-body orbit in Earth-centred inertial space. SI units.
 * X/Y span the equator; Z points north. This is independent of UAV world axes,
 * fleet membership, RF telemetry, route decisions and render compression.
 */
export interface CircularOrbitConfig {
  earthRadiusM: number;
  gravitationalParameter: number; // m³/s²
  altitudeM: number;
  inclinationRad: number;
  initialPhaseRad: number;
  epochSeconds: number; // phase reference in the simulation time domain
}
export interface OrbitalVector { readonly x: number; readonly y: number; readonly z: number }
export interface CircularOrbit {
  readonly config: Readonly<CircularOrbitConfig>;
  readonly radiusM: number;
  readonly angularVelocityRadS: number;
  readonly periodSeconds: number;
  readonly speedMps: number;
}
export interface SatelliteOrbitState {
  readonly phaseRad: number;
  readonly positionM: OrbitalVector;
  readonly velocityMps: OrbitalVector;
  readonly direction: OrbitalVector;
}

// Earth mean radius: NASA Earth Fact Sheet. GM: NGA WGS 84 reference value.
// https://nssdc.gsfc.nasa.gov/planetary/factsheet/earthfact.html
// https://earth-info.nga.mil/?action=wgs84&dir=wgs84
export const DEFAULT_SATELLITE_CONFIG: Readonly<CircularOrbitConfig> = Object.freeze({
  earthRadiusM: 6_371_000,
  gravitationalParameter: 3.986004418e14,
  altitudeM: 550_000,
  inclinationRad: 53 * Math.PI / 180,
  initialPhaseRad: Math.PI / 3,
  epochSeconds: 0,
});

export function createCircularOrbit(config: Readonly<CircularOrbitConfig>): CircularOrbit {
  if (![config.earthRadiusM, config.gravitationalParameter, config.altitudeM,
    config.inclinationRad, config.initialPhaseRad, config.epochSeconds].every(Number.isFinite)
    || config.earthRadiusM <= 0 || config.gravitationalParameter <= 0 || config.altitudeM <= 0
    || config.inclinationRad < 0 || config.inclinationRad > Math.PI) {
    throw new RangeError('Circular orbit requires finite SI values, positive radius/GM/altitude and inclination in [0, π].');
  }
  const radiusM = config.earthRadiusM + config.altitudeM;
  const angularVelocityRadS = Math.sqrt(config.gravitationalParameter / radiusM ** 3);
  const periodSeconds = 2 * Math.PI / angularVelocityRadS;
  const speedMps = radiusM * angularVelocityRadS;
  if (![radiusM, angularVelocityRadS, periodSeconds, speedMps].every(value => Number.isFinite(value) && value > 0)) {
    throw new RangeError('Orbit parameters exceed the supported numeric range.');
  }
  return Object.freeze({ config: Object.freeze({ ...config }), radiusM, angularVelocityRadS, periodSeconds, speedMps });
}

const TAU = 2 * Math.PI;
const wrap = (angle: number) => ((angle % TAU) + TAU) % TAU;

/** Analytic evaluation, not Euler integration: no accumulating orbital drift.
 * Modulo before multiplication keeps trigonometric arguments small on long runs.
 */
export function satelliteOrbitAt(orbit: CircularOrbit, simulationTimeSeconds: number): SatelliteOrbitState {
  const elapsed = simulationTimeSeconds - orbit.config.epochSeconds;
  if (!Number.isFinite(elapsed)) throw new RangeError('Orbit simulation time must be finite.');
  const phaseRad = wrap(wrap(orbit.config.initialPhaseRad) + orbit.angularVelocityRadS * (elapsed % orbit.periodSeconds));
  const cos = Math.cos(phaseRad);
  const sin = Math.sin(phaseRad);
  const cosI = Math.cos(orbit.config.inclinationRad);
  const sinI = Math.sin(orbit.config.inclinationRad);
  const direction = { x: cos, y: sin * cosI, z: sin * sinI };
  return {
    phaseRad, direction,
    positionM: { x: orbit.radiusM * direction.x, y: orbit.radiusM * direction.y, z: orbit.radiusM * direction.z },
    velocityMps: { x: -orbit.speedMps * sin, y: orbit.speedMps * cos * cosI, z: orbit.speedMps * cos * sinI },
  };
}

export const MISSION_SATELLITE_ORBIT = createCircularOrbit(DEFAULT_SATELLITE_CONFIG);
