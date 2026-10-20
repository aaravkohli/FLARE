import { createEnvironmentProps, GROUND_SCALE_CUE } from './environmentProps.ts';
import { BOUNDS } from './dynamics.ts';
import { GROUND_STATION, terrainHeight } from './terrain.ts';

/** Fixed illustrative site, in display metres. Never an input to the simulator. */
export type GroundPoint = readonly [x: number, z: number];
export interface SiteRoad { points: readonly GroundPoint[]; width: number }
export interface SiteBuilding { x: number; z: number; width: number; depth: number; height: number; role?: 'hangar' | 'lab' | 'equipment' }
export interface SiteLot { x: number; z: number; width: number; depth: number }
const local = (x: number, z: number): GroundPoint => [GROUND_STATION.x + x, GROUND_STATION.z + z];
export const FIELD_STATION = {
  approach: { width: 8, points: [local(-620, -550), local(-400, -370), local(-210, -210), local(-104, -68)] },
  loop: { width: 6, points: [local(-104, -68), local(-48, -68), local(58, -48), local(64, 25), local(38, 55), local(-104, 53), local(-104, -68)] },
  apron: { width: 5, points: [local(-72, 54), local(-52, 84), local(78, 116)] },
  circuit: { width: 8, points: [local(-48, -68), [-290, 40], [-290, -170], [-245, -210], [160, -210], [350, -95], [350, 240], [160, 244], [-155, 244], local(64, 25)] },
  hangarAccess: { width: 7, points: [[-290, -100], [-205, -100], [-205, -75], [90, -75], [90, -160], [165, -160]] },
  lots: {
    apron: { x: -35, z: -20, width: 260, depth: 110 },
    forecourt: { x: -120, z: -108, width: 172, depth: 42 },
    operations: { x: -278, z: 164, width: 166, depth: 110 },
    service: { x: -365, z: -87, width: 110, depth: 90 },
    solar: { x: 250, z: 160, width: 100, depth: 74 },
    labs: { x: 176, z: -115, width: 138, depth: 90 },
    plots: { x: -55, z: 150, width: 148, depth: 62 },
  },
  buildings: [
    { x: GROUND_STATION.x - 72, z: GROUND_STATION.z - 43, width: 26, depth: 16, height: 5.5 },
    { x: GROUND_STATION.x - 72, z: GROUND_STATION.z - 10, width: 26, depth: 16, height: 6 },
    { x: -165, z: -146, width: 62, depth: 34, height: 9, role: 'hangar' },
    { x: -88, z: -146, width: 62, depth: 34, height: 9, role: 'hangar' },
    { x: 153, z: -122, width: 42, depth: 26, height: 7, role: 'lab' },
    { x: 212, z: -122, width: 34, depth: 26, height: 6, role: 'lab' },
    { x: 147, z: 106, width: 30, depth: 18, height: 5.5, role: 'equipment' },
    { x: -382, z: -111, width: 32, depth: 18, height: 6, role: 'equipment' },
  ] as readonly SiteBuilding[],
  pads: [local(-7, 92), local(30, 103), local(67, 114)],
} satisfies { approach: SiteRoad; loop: SiteRoad; apron: SiteRoad; circuit: SiteRoad; hangarAccess: SiteRoad; lots: Record<string, SiteLot>; buildings: readonly SiteBuilding[]; pads: readonly GroundPoint[] };

export const WOODLANDS = [
  { x: -275, z: -300, rx: 105, rz: 64, trees: 90 },
  { x: 245, z: -300, rx: 105, rz: 58, trees: 90 },
  { x: 455, z: 140, rx: 76, rz: 138, trees: 105 },
  { x: 125, z: 365, rx: 135, rz: 72, trees: 100 },
  { x: -245, z: 395, rx: 102, rz: 55, trees: 65 },
  { x: -490, z: -45, rx: 75, rz: 135, trees: 95 },
] as const;
export const SITE_ROADS: readonly SiteRoad[] = [FIELD_STATION.approach, FIELD_STATION.loop, FIELD_STATION.apron, FIELD_STATION.circuit, FIELD_STATION.hangarAccess];

export function siteFooting(building: SiteBuilding): { top: number; bottom: number } {
  let low = Infinity, high = -Infinity;
  for (let row = 0; row <= 8; row++) for (let column = 0; column <= 8; column++) {
    const y = terrainHeight(building.x + building.width * (column / 8 - .5), building.z + building.depth * (row / 8 - .5));
    low = Math.min(low, y); high = Math.max(high, y);
  }
  return { top: high + .22, bottom: low - .4 };
}

export interface GroundSurface { positions: number[]; indices: number[] }
function point(surface: GroundSurface, x: number, z: number, lift: number) {
  surface.positions.push(x, terrainHeight(x, z) + lift, z);
}
/** Short subdivisions follow relief; the source heightfield is never modified. */
export function drapedStrip(points: readonly GroundPoint[], width: number, lift = .12): GroundSurface {
  const surface: GroundSurface = { positions: [], indices: [] };
  const samples: GroundPoint[] = [];
  for (let segment = 1; segment < points.length; segment++) {
    const [ax, az] = points[segment - 1], [bx, bz] = points[segment];
    const steps = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / 3));
    for (let step = 0; step < steps; step++) samples.push([ax + (bx - ax) * step / steps, az + (bz - az) * step / steps]);
  }
  if (points.length) samples.push(points[points.length - 1]);
  const closed = points.length > 2 && points[0][0] === points[points.length - 1][0] && points[0][1] === points[points.length - 1][1];
  samples.forEach(([x, z], index) => {
    const before = samples[index === 0 && closed ? samples.length - 2 : Math.max(0, index - 1)];
    const after = samples[index === samples.length - 1 && closed ? 1 : Math.min(samples.length - 1, index + 1)];
    const length = Math.hypot(after[0] - before[0], after[1] - before[1]) || 1;
    const nx = -(after[1] - before[1]) / length * width / 2, nz = (after[0] - before[0]) / length * width / 2;
    point(surface, x + nx, z + nz, lift); point(surface, x - nx, z - nz, lift);
    if (index) {
      const a = (index - 1) * 2;
      for (const triangle of [[a, a + 2, a + 1], [a + 1, a + 2, a + 3]]) {
        const [p, q, r] = triangle.map(i => i * 3), v = surface.positions;
        const up = (v[q + 2] - v[p + 2]) * (v[r] - v[p]) - (v[q] - v[p]) * (v[r + 2] - v[p + 2]);
        // An acute join can fold a tiny inside-corner triangle. Keep its face
        // upward rather than losing it to culling on one side of the road.
        surface.indices.push(...(up < 0 ? [triangle[0], triangle[2], triangle[1]] : triangle));
      }
    }
  });
  return surface;
}

export function drapedRing(center: GroundPoint, inner: number, outer: number, lift = .16): GroundSurface {
  const surface: GroundSurface = { positions: [], indices: [] };
  const rings = Math.max(1, Math.ceil((outer - inner) / 3)), segments = 48;
  for (let ring = 0; ring <= rings; ring++) for (let segment = 0; segment <= segments; segment++) {
    const radius = inner + (outer - inner) * ring / rings, angle = segment / segments * Math.PI * 2;
    point(surface, center[0] + Math.cos(angle) * radius, center[1] + Math.sin(angle) * radius, lift);
    if (ring && segment) {
      const a = (ring - 1) * (segments + 1) + segment - 1, b = a + segments + 1;
      surface.indices.push(a, a + 1, b, a + 1, b + 1, b);
    }
  }
  return surface;
}

export function drapedRectangle(lot: SiteLot, lift = .28): GroundSurface {
  const surface: GroundSurface = { positions: [], indices: [] };
  const columns = Math.ceil(lot.width / 3), rows = Math.ceil(lot.depth / 3);
  for (let row = 0; row <= rows; row++) for (let column = 0; column <= columns; column++) {
    point(surface, lot.x + lot.width * (column / columns - .5), lot.z + lot.depth * (row / rows - .5), lift);
    if (row && column) { const a = (row - 1) * (columns + 1) + column - 1, b = a + columns + 1; surface.indices.push(a, b, a + 1, a + 1, b, b + 1); }
  }
  return surface;
}

/** Soft, irregular ground-cover masses remain legible when trees are distant. */
export function woodlandFloor(wood: typeof WOODLANDS[number], seed: number): GroundSurface & { alpha: number[] } {
  const surface = { positions: [] as number[], indices: [] as number[], alpha: [] as number[] };
  const rings = 16, segments = 80;
  for (let ring = 0; ring <= rings; ring++) for (let segment = 0; segment <= segments; segment++) {
    const t = ring / rings, angle = segment / segments * Math.PI * 2;
    const edge = 1 + .09 * Math.sin(angle * 3 + seed) + .07 * Math.cos(angle * 5 - seed);
    const x = wood.x + Math.cos(angle) * wood.rx * t * edge, z = wood.z + Math.sin(angle) * wood.rz * t * edge;
    point(surface, x, z, .2);
    surface.alpha.push(siteVegetationAllowed(x, z) ? Math.min(1, (1 - t) * 5) * (.8 + noise(segment + seed * 100, 27) * .2) : 0);
    if (ring && segment) { const a = (ring - 1) * (segments + 1) + segment - 1, b = a + segments + 1; surface.indices.push(a, a + 1, b, a + 1, b + 1, b); }
  }
  return surface;
}

function distanceToRoad(x: number, z: number, road: SiteRoad): number {
  let distance = Infinity;
  for (let i = 1; i < road.points.length; i++) {
    const [ax, az] = road.points[i - 1], [bx, bz] = road.points[i];
    const dx = bx - ax, dz = bz - az;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
    distance = Math.min(distance, Math.hypot(x - ax - t * dx, z - az - t * dz));
  }
  return distance;
}
export function siteVegetationAllowed(x: number, z: number): boolean {
  if (x > -195 && x < 105 && z > -90 && z < 60) return false;
  if (Math.hypot(x - GROUND_STATION.x, z - GROUND_STATION.z) < 100) return false;
  if (Math.hypot(x - (GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.eastX) / 2, z - GROUND_SCALE_CUE.z) < 75) return false;
  if (FIELD_STATION.pads.some(([px, pz]) => Math.hypot(x - px, z - pz) < 28)) return false;
  if (SITE_ROADS.some(road => distanceToRoad(x, z, road) < road.width / 2 + 7)) return false;
  if (Object.values(FIELD_STATION.lots).some(lot => Math.abs(x - lot.x) < lot.width / 2 + 8 && Math.abs(z - lot.z) < lot.depth / 2 + 8)) return false;
  return !FIELD_STATION.buildings.some(b => Math.abs(x - b.x) < b.width / 2 + 10 && Math.abs(z - b.z) < b.depth / 2 + 10);
}

export interface SiteVegetation {
  x: number; y: number; z: number; radiusM: number; heightM: number; rotation: number; tone: number;
  kind: 'scrub' | 'rock' | 'tree'; balanced: boolean; broadleaf?: boolean;
}
function noise(index: number, salt: number): number {
  let value = Math.imul(index + 12289, 374761393) ^ salt;
  value = Math.imul(value ^ value >>> 13, 1274126177);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}
export function createSiteVegetation(): readonly SiteVegetation[] {
  const props: SiteVegetation[] = createEnvironmentProps().filter(p => siteVegetationAllowed(p.x, p.z)).map((p, i) => ({ ...p, heightM: p.radiusM * (p.kind === 'rock' ? .96 : 1.3), balanced: i % 2 === 0 }));
  WOODLANDS.forEach((wood, cluster) => {
    for (let index = 0; index < wood.trees; index++) {
      const key = cluster * 1000 + index;
      const angle = index * 2.39996323 + noise(key, 1) * .55, radius = Math.sqrt((index + .5) / wood.trees) * .94;
      const x = wood.x + Math.cos(angle) * wood.rx * radius, z = wood.z + Math.sin(angle) * wood.rz * radius;
      if (!siteVegetationAllowed(x, z)) continue;
      const slope = Math.hypot((terrainHeight(x + 3, z) - terrainHeight(x - 3, z)) / 6, (terrainHeight(x, z + 3) - terrainHeight(x, z - 3)) / 6);
      if (slope > .45) continue;
      const y = terrainHeight(x, z), heightM = Math.min(12 + noise(key, 4) * 7, BOUNDS.minAltitude - 8 - y);
      if (heightM < 7) continue;
      props.push({ x, z, y, kind: 'tree', radiusM: 3.8 + noise(key, 3) * 2.3,
        heightM, rotation: noise(key, 5) * Math.PI * 2, tone: noise(key, 6), balanced: index % 4 !== 0, broadleaf: index % 3 === 0 });
      if (index % 3 === 0) {
        const sx = x + 6, sz = z + 2;
        if (siteVegetationAllowed(sx, sz)) props.push({ x: sx, z: sz, y: terrainHeight(sx, sz), kind: index % 2 ? 'rock' : 'scrub', radiusM: 2 + noise(key, 7) * 3, heightM: 1.6 + noise(key, 8) * 2.5, rotation: key, tone: noise(key, 9), balanced: index % 2 === 0 });
      }
    }
  });
  return props;
}
