import * as THREE from 'three';
import { threatOpacity, threatQueuePath } from './threatState.ts';
import type { ThreatVisual } from './threatState.ts';
import type { Vector3 } from './dynamics.ts';
import { curvePoint, routeCurve } from './networkGeometry.ts';
import { GROUND_STATION_VISUAL_ENDPOINT } from './groundStation.ts';

type GroundHeight = (x: number, z: number) => number;
const TAU = Math.PI * 2;
const SURFACE_LIFT_M = .8;
const GPS_DISPLAY_OFFSET_M = 70;
const FIELD_RINGS = 20, FIELD_SPOKES = 64;
const SECTOR_RINGS = 12, SECTOR_SPOKES = 24;

function surfaceGeometry(rings: number, spokes: number, sector: boolean) {
  const geometry = new THREE.BufferGeometry();
  const vertices = (rings + 1) * (spokes + 1);
  const positions = new THREE.BufferAttribute(new Float32Array(vertices * 3), 3);
  const fade = new Float32Array(vertices);
  const indices: number[] = [];
  for (let ring = 0; ring <= rings; ring++) {
    const radial = ring / rings;
    const slope = Math.max(0, Math.min(1, (radial - .48) / .5));
    const broadTint = 1 - slope * slope * (3 - 2 * slope);
    const softContour = Math.exp(-(((radial - .78) / .1) ** 2));
    const radialFade = radial === 1 ? 0 : Math.min(1, .78 * broadTint + .3 * softContour);
    for (let spoke = 0; spoke <= spokes; spoke++) {
      const angular = spoke / spokes;
      const edgeFade = sector ? Math.min(1, angular * 7, (1 - angular) * 7) : 1;
      fade[ring * (spokes + 1) + spoke] = radialFade * edgeFade;
      if (ring === rings || spoke === spokes) continue;
      const a = ring * (spokes + 1) + spoke, b = a + spokes + 1;
      indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  geometry.setAttribute('position', positions);
  geometry.setAttribute('fade', new THREE.BufferAttribute(fade, 1));
  geometry.setIndex(indices);
  return geometry;
}

/** Schematic affected areas and offsets only. No attacker coordinate is supplied or inferred. */
export function createThreatLayer(scene: THREE.Scene, groundHeight: GroundHeight) {
  const root = new THREE.Group(); root.userData.asset = 'jammer'; scene.add(root);
  const areaMaterial = new THREE.ShaderMaterial({
    uniforms: { tint: { value: new THREE.Color('#c27a68') }, strength: { value: 0 } },
    vertexShader: `attribute float fade;
      varying float vFade;
      void main() {
        vFade = fade;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `uniform vec3 tint;
      uniform float strength;
      varying float vFade;
      void main() {
        gl_FragColor = vec4(tint, strength * vFade);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
  });
  const fieldGeometry = surfaceGeometry(FIELD_RINGS, FIELD_SPOKES, false);
  const field = new THREE.Mesh(fieldGeometry, areaMaterial); field.frustumCulled = false; root.add(field);
  const sectorGeometry = surfaceGeometry(SECTOR_RINGS, SECTOR_SPOKES, true);
  const sector = new THREE.Mesh(sectorGeometry, areaMaterial); sector.frustumCulled = false; root.add(sector);
  const ghostMaterial = new THREE.MeshBasicMaterial({ color: '#d7bfaa', transparent: true,
    opacity: .12, depthWrite: false, fog: false });
  const wireMaterial = new THREE.LineBasicMaterial({ color: '#dec4aa', transparent: true,
    opacity: .55, depthWrite: false, fog: false });
  const marksMaterial = new THREE.LineBasicMaterial({ color: '#d8ad8c', transparent: true,
    opacity: .42, depthWrite: false, fog: false });
  const ghostGeometry = new THREE.OctahedronGeometry(7);
  const ghost = new THREE.Mesh(ghostGeometry, ghostMaterial); root.add(ghost);
  const ghostEdges = new THREE.EdgesGeometry(ghostGeometry);
  ghost.add(new THREE.LineSegments(ghostEdges, wireMaterial));
  const connectorGeometry = new THREE.BufferGeometry().setAttribute('position',
    new THREE.BufferAttribute(new Float32Array(6), 3));
  const connector = new THREE.Line(connectorGeometry, wireMaterial);
  connector.frustumCulled = false; root.add(connector);
  const marksGeometry = new THREE.BufferGeometry().setAttribute('position',
    new THREE.BufferAttribute(new Float32Array(3 * 96), 3));
  const marks = new THREE.LineSegments(marksGeometry, marksMaterial);
  marks.frustumCulled = false; root.add(marks);
  let opacity = 0, previousOwner = '', previous: ThreatVisual | null = null;
  let anchor: Vector3 = { x: 0, y: 0, z: 0 }, lastSweepAngle = 0;

  function groundSurface(geometry: THREE.BufferGeometry, center: Vector3, radius: number,
    rings: number, spokes: number, startAngle: number, arc: number) {
    const positions = geometry.getAttribute('position');
    for (let ring = 0; ring <= rings; ring++) {
      const distance = radius * ring / rings;
      for (let spoke = 0; spoke <= spokes; spoke++) {
        const angle = startAngle + arc * spoke / spokes;
        const x = Math.cos(angle) * distance, z = Math.sin(angle) * distance;
        positions.setXYZ(ring * (spokes + 1) + spoke, x,
          groundHeight(center.x + x, center.z + z) + SURFACE_LIFT_M, z);
      }
    }
    positions.needsUpdate = true;
    geometry.computeBoundingSphere();
  }

  return {
    root, field, sector, ghost, connector, marks,
    update(visual: ThreatVisual, owner: string, position: Vector3 | undefined, satellite: Vector3,
      seconds: number, dt: number, reduced: boolean, forwardingPath: 'direct' | 'satellite' | null = null) {
      if (owner !== previousOwner) { opacity = 0; previous = null; previousOwner = owner; }
      const validPosition = position && [position.x, position.y, position.z].every(Number.isFinite);
      const active = visual.profile !== 'none' && !!validPosition;
      opacity = reduced && !active ? 0 : threatOpacity(opacity, active, dt);
      if (active) { previous = visual; anchor = { ...position! }; }
      root.visible = opacity > 0 && previous !== null;
      if (!root.visible || !previous) return;
      const effect = previous;
      field.visible = effect.profile === 'spot' || effect.profile === 'barrage';
      sector.visible = effect.profile === 'sweep';
      ghost.visible = effect.profile === 'gps_spoofing' && effect.drift !== null;
      connector.visible = ghost.visible;
      areaMaterial.uniforms.strength.value = opacity * (effect.profile === 'barrage' ? .24 : effect.profile === 'sweep' ? .28 : .32);
      ghostMaterial.opacity = opacity * .13;
      wireMaterial.opacity = opacity * .55;
      marksMaterial.opacity = opacity * .42;
      if (field.visible) {
        const radius = effect.profile === 'barrage' ? 190 : 55;
        field.position.set(anchor.x, 0, anchor.z);
        groundSurface(fieldGeometry, anchor, radius, FIELD_RINGS, FIELD_SPOKES, 0, TAU);
      }
      if (sector.visible) {
        if (active) lastSweepAngle = reduced ? 0 : seconds * .12;
        sector.position.set(anchor.x, 0, anchor.z);
        groundSurface(sectorGeometry, anchor, 125, SECTOR_RINGS, SECTOR_SPOKES,
          lastSweepAngle - Math.PI / 4, Math.PI / 2);
      }
      if (ghost.visible) {
        // No trusted position reference or bearing: the display direction and spacing are fixed and schematic.
        ghost.position.set(anchor.x + GPS_DISPLAY_OFFSET_M, anchor.y, anchor.z);
        const positions = connectorGeometry.getAttribute('position');
        positions.setXYZ(0, anchor.x, anchor.y, anchor.z);
        positions.setXYZ(1, ghost.position.x, ghost.position.y, ghost.position.z);
        positions.needsUpdate = true;
      }

      let count = 0;
      const positions = marksGeometry.getAttribute('position');
      const queuePath = threatQueuePath(effect, forwardingPath);
      if (queuePath) {
        const curve = routeCurve(anchor, GROUND_STATION_VISUAL_ENDPOINT,
          queuePath === 'satellite' ? satellite : undefined);
        // Six fixed hatches near the terminal leg, never a measured queue depth.
        for (let i = 0; i < 6; i++) {
          const t = .76 + i * .035;
          const point = curvePoint(curve, t), ahead = curvePoint(curve, t + .003);
          const dx = ahead.x - point.x, dz = ahead.z - point.z;
          const norm = Math.max(.001, Math.hypot(dx, dz));
          const sideX = -dz / norm * 3, sideZ = dx / norm * 3;
          positions.setXYZ(count++, point.x - sideX, point.y + 1, point.z - sideZ);
          positions.setXYZ(count++, point.x + sideX, point.y + 1, point.z + sideZ);
        }
      } else if (['smart', 'reactive', 'adaptive', 'fhss', 'spoofing', 'replay'].includes(effect.profile)) {
        for (const path of effect.paths) {
          if (path === 'mesh') continue; // No known mesh forwarding hop.
          const curve = routeCurve(anchor, GROUND_STATION_VISUAL_ENDPOINT,
            path === 'satellite' ? satellite : undefined);
          for (let i = 0; i < 6; i++) {
            const point = curvePoint(curve, (i + .5) / 6);
            const hop = effect.profile === 'fhss' && !reduced ? Math.floor(seconds * 2) % 2 * 2 : 0;
            positions.setXYZ(count++, point.x - 2.5, point.y + 1 + hop, point.z);
            positions.setXYZ(count++, point.x + 2.5, point.y + 1 + hop, point.z);
            if (effect.profile === 'replay') {
              positions.setXYZ(count++, point.x - 2.5, point.y + 5, point.z);
              positions.setXYZ(count++, point.x + 2.5, point.y + 5, point.z);
            }
          }
        }
      }
      marksGeometry.setDrawRange(0, count);
      positions.needsUpdate = true;
      if (count > 0) marksGeometry.computeBoundingSphere();
      marks.visible = count > 0;
    },
    dispose() {
      scene.remove(root);
      fieldGeometry.dispose(); sectorGeometry.dispose(); ghostGeometry.dispose(); ghostEdges.dispose();
      connectorGeometry.dispose(); marksGeometry.dispose();
      areaMaterial.dispose(); ghostMaterial.dispose(); wireMaterial.dispose(); marksMaterial.dispose();
    },
  };
}
