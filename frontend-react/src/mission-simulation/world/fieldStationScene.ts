import * as THREE from 'three';
import { createSiteVegetation, drapedRectangle, drapedRing, drapedStrip, FIELD_STATION, SITE_ROADS, siteFooting, woodlandFloor, WOODLANDS } from './fieldStationLayout.ts';
import type { GroundSurface, SiteLot } from './fieldStationLayout.ts';
import { terrainHeight } from './terrain.ts';
import type { RenderQuality } from './renderQuality.ts';

/** Passive scenery only. No pick targets, clock, mission state or random stream. */
export function createFieldStation() {
  const group = new THREE.Group(); group.name = 'Illustrative research field station';
  const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
  const instances: THREE.InstancedMesh[] = [], fineDetails: THREE.Object3D[] = [];
  const geo = <T extends THREE.BufferGeometry>(value: T) => { geometries.add(value); return value; };
  const material = (color: string) => {
    const value = new THREE.MeshStandardMaterial({ color, roughness: .96 }); materials.add(value); return value;
  };
  const gravel = material('#a39c84'), road = material('#797568'), pavement = material('#56625f');
  const marking = material('#c9cbb6'), wall = material('#b6b5a2'), roof = material('#4c6570'), concrete = material('#8d9084');
  const glass = material('#304b57'), steel = material('#707b77'), soil = material('#9f9272');
  const green = material('#7e8a5e'), dry = material('#ad9f70'), pale = material('#a8b08b');
  glass.roughness = .58; glass.metalness = .16;
  const paints: GroundSurface[] = [];
  function surface(data: GroundSurface & { alpha?: number[] }, mat: THREE.Material) {
    const shape = geo(new THREE.BufferGeometry());
    shape.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3)); shape.setIndex(data.indices); shape.computeVertexNormals();
    if (data.alpha) shape.setAttribute('color', new THREE.Float32BufferAttribute(data.alpha.flatMap(a => [1, 1, 1, a]), 4));
    const mesh = new THREE.Mesh(shape, mat); mesh.receiveShadow = true; group.add(mesh); return mesh;
  }
  const forestFloor = material('#59694b'); forestFloor.vertexColors = true; forestFloor.transparent = true; forestFloor.opacity = .72; forestFloor.depthWrite = false;
  WOODLANDS.forEach((wood, index) => surface(woodlandFloor(wood, index + 1), forestFloor));
  for (const [key, lot] of Object.entries(FIELD_STATION.lots)) {
    surface(drapedRectangle({ ...lot, width: lot.width + 6, depth: lot.depth + 6 }, .24), gravel);
    surface(drapedRectangle(lot, .32), key === 'apron' || key === 'forecourt' ? pavement : key === 'plots' ? soil : concrete);
  }
  for (const path of SITE_ROADS) {
    surface(drapedStrip(path.points, path.width + 3, .36), gravel);
    surface(drapedStrip(path.points, path.width, .42), path === FIELD_STATION.circuit || path === FIELD_STATION.hangarAccess ? pavement : road);
  }
  for (const pad of FIELD_STATION.pads) {
    surface(drapedRing(pad, 0, 16, .16), gravel);
    surface(drapedRing(pad, 0, 12, .21), pavement);
    paints.push(drapedRing(pad, 8.4, 8.85, .48));
    const [x, z] = pad;
    for (const line of [[[x - 2.2, z - 3], [x - 2.2, z + 3]], [[x + 2.2, z - 3], [x + 2.2, z + 3]], [[x - 2.2, z], [x + 2.2, z]]] as const) paints.push(drapedStrip(line, .65, .49));
  }
  const boxShape = geo(new THREE.BoxGeometry(1, 1, 1));
  const boxes = new Map<THREE.Material, { matrix: THREE.Matrix4; fine: boolean }[]>(), boxTransform = new THREE.Object3D();
  function box(x: number, y: number, z: number, width: number, height: number, depth: number, mat: THREE.Material, fine = false, tilt = 0) {
    boxTransform.position.set(x, y, z); boxTransform.rotation.set(tilt, 0, 0); boxTransform.scale.set(width, height, depth); boxTransform.updateMatrix();
    const entries = boxes.get(mat) ?? []; entries.push({ matrix: boxTransform.matrix.clone(), fine }); boxes.set(mat, entries);
  }
  function outline(lot: SiteLot, inset = 3) {
    const x0 = lot.x - lot.width / 2 + inset, x1 = lot.x + lot.width / 2 - inset, z0 = lot.z - lot.depth / 2 + inset, z1 = lot.z + lot.depth / 2 - inset;
    paints.push(drapedStrip([[x0, z0], [x1, z0], [x1, z1], [x0, z1], [x0, z0]], .45, .49));
  }
  for (const b of FIELD_STATION.buildings) {
    const footing = siteFooting(b);
    box(b.x, (footing.top + footing.bottom) / 2, b.z, b.width + 1, footing.top - footing.bottom, b.depth + 1, concrete);
    box(b.x, footing.top + b.height / 2, b.z, b.width, b.height, b.depth, wall);
    if (b.role === 'hangar') {
      for (const side of [-1, 1]) box(b.x, footing.top + b.height + .9, b.z + side * b.depth / 4, b.width + 2, .7, b.depth / 2 + 1, roof, false, side * .09);
      box(b.x, footing.top + 3.6, b.z + b.depth / 2 + .1, b.width * .78, 7.2, .3, glass);
      for (const offset of [-16, -8, 0, 8, 16]) box(b.x + offset, footing.top + 3.6, b.z + b.depth / 2 + .3, .3, 7.2, .25, steel, true);
    } else {
      box(b.x, footing.top + b.height + .35, b.z, b.width + 1.4, .7, b.depth + 1.4, roof);
      box(b.x + b.width / 2 + .06, footing.top + 1.9, b.z, .15, 3.8, 4, glass);
      if (b.role === 'lab') box(b.x, footing.top + 4.4, b.z + b.depth / 2 + .1, b.width - 8, 1.8, .2, glass);
    }
    for (const offset of [-7, 0, 7]) box(b.x + offset, footing.top + b.height + 1.7, b.z, 4.5, .3, 8, glass, true);
    box(b.x - 7, footing.top + b.height + 1.2, b.z - 4, 2, 1.1, 2.4, concrete, true);
  }
  // Apron markings, parking and survey pads provide a visible human scale
  // underneath the fleet. They are passive paint, never flight restrictions.
  outline(FIELD_STATION.lots.apron); outline(FIELD_STATION.lots.forecourt, 1.5);
  for (const x of [-125, -65, -5, 55]) {
    paints.push(drapedStrip([[x, -60], [x, 22]], .35, .49));
    paints.push(drapedRing([x + 25, -20], 11.5, 12, .49));
    paints.push(drapedStrip([[x + 18, -20], [x + 32, -20]], .5, .5), drapedStrip([[x + 25, -27], [x + 25, -13]], .5, .5));
  }
  for (let i = 0; i < 5; i++) {
    surface(drapedRectangle({ x: -114 + i * 29, z: 150, width: 24, depth: 54 }, .4), i % 2 ? dry : green);
    for (let row = 0; row < 5; row++) surface(drapedRectangle({ x: -114 + i * 29, z: 128 + row * 11, width: 23, depth: 1 }, .43), i % 2 ? soil : pale);
  }
  // One solar garden, with shared panel/rail geometry rather than individual
  // render objects. All supports stay terrain-relative.
  for (let row = 0; row < 4; row++) for (let column = 0; column < 8; column++) {
    const x = 211.5 + column * 11, z = 136 + row * 16, y = terrainHeight(x, z);
    box(x, y + 2.8, z, 9, .24, 12, glass, false, -.25);
    for (const side of [-1, 1]) box(x + side * 3, y + 1.1, z, .35, 2.2, .35, steel);
    box(x, y + 2.99, z, .1, .1, 11.8, concrete, true, -.25);
  }
  const containerColors = [material('#718387'), material('#a7a99b'), material('#968970')];
  for (let row = 0; row < 3; row++) for (let column = 0; column < 4; column++) {
    const x = -400 + column * 22, z = -78 + row * 10, y = terrainHeight(x, z) + .4;
    box(x, y + 1.5, z, 13, 3, 5, containerColors[(row + column) % 3]);
    for (let rib = 0; rib < 5; rib++) box(x - 5 + rib * 2.5, y + 1.6, z + 2.55, .14, 2.7, .12, steel, true);
  }
  for (let i = 0; i < 8; i++) {
    const x = 120 + i * 12, z = -84, y = terrainHeight(x, z) + .5;
    paints.push(drapedStrip([[x - 5, z - 6], [x - 5, z + 7]], .3, .49));
    if (i % 3 !== 2) {
      box(x, y + 1, z, 3.1, 1.7, 6.2, i % 2 ? wall : steel);
      box(x, y + 1.7, z - .7, 2.9, 1.1, 3.5, glass);
      for (const side of [-1, 1]) for (const dz of [-2, 2]) box(x + side * 1.5, y + .45, z + dz, .4, .9, .9, pavement, true);
    }
  }
  const fencePoints: THREE.Vector3[] = [], yard = FIELD_STATION.lots.service;
  for (const side of [-1, 1]) for (let i = 0; i <= 22; i++) {
    const x = yard.x - yard.width / 2 + i * yard.width / 22, z = yard.z + side * yard.depth / 2;
    const y = terrainHeight(x, z); box(x, y + 1.4, z, .22, 2.8, .22, steel, true);
    if (i) { const px = x - yard.width / 22; for (const h of [1.2, 2.6]) fencePoints.push(new THREE.Vector3(px, terrainHeight(px, z) + h, z), new THREE.Vector3(x, y + h, z)); }
  }
  const fenceMaterial = new THREE.LineBasicMaterial({ color: '#807c6b', transparent: true, opacity: .6 }); materials.add(fenceMaterial);
  const fence = new THREE.LineSegments(geo(new THREE.BufferGeometry().setFromPoints(fencePoints)), fenceMaterial); group.add(fence); fineDetails.push(fence);
  // Merge all paint into a single draw and all architecture into material
  // batches. Larger landmarks stay present in Balanced quality.
  const paint: GroundSurface = { positions: [], indices: [] };
  for (const part of paints) { const offset = paint.positions.length / 3; paint.positions.push(...part.positions); paint.indices.push(...part.indices.map(i => i + offset)); }
  surface(paint, marking);
  for (const [mat, entries] of boxes) for (const fine of [false, true]) {
    const selected = entries.filter(e => e.fine === fine); if (!selected.length) continue;
    const mesh = new THREE.InstancedMesh(boxShape, mat, selected.length); selected.forEach((entry, index) => mesh.setMatrixAt(index, entry.matrix));
    mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere(); mesh.castShadow = !fine; mesh.receiveShadow = true; instances.push(mesh); group.add(mesh); if (fine) fineDetails.push(mesh);
  }
  const vegetation = createSiteVegetation(), transform = new THREE.Object3D(), tint = new THREE.Color();
  function vegetationMesh(kind: 'scrub' | 'rock' | 'tree', shape: THREE.BufferGeometry, dark: string, light: string, balanced: boolean, trunk = false, species?: 'pine' | 'broadleaf') {
    const props = vegetation.filter(p => p.kind === kind && p.balanced === balanced && (!species || !!p.broadleaf === (species === 'broadleaf')));
    if (!props.length) return;
    const mat = material(dark); mat.color.set('#ffffff');
    const mesh = new THREE.InstancedMesh(shape, mat, props.length);
    const low = new THREE.Color(dark), high = new THREE.Color(light);
    props.forEach((p, i) => {
      transform.position.set(p.x, p.y, p.z); transform.rotation.set(0, p.rotation, 0);
      transform.scale.set(trunk ? .32 : p.radiusM, p.heightM, trunk ? .32 : p.radiusM); transform.updateMatrix();
      mesh.setMatrixAt(i, transform.matrix); mesh.setColorAt(i, tint.copy(low).lerp(high, p.tone));
    });
    mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere();
    mesh.castShadow = kind === 'tree'; mesh.receiveShadow = true; instances.push(mesh); group.add(mesh);
    if (!balanced) fineDetails.push(mesh);
  }
  const scrub = geo(new THREE.IcosahedronGeometry(1, 1)); scrub.scale(1, .5, 1); scrub.translate(0, .5, 0);
  const rock = geo(new THREE.DodecahedronGeometry(1, 0)); rock.scale(1, .5, 1); rock.translate(0, .45, 0);
  const crown = geo(new THREE.ConeGeometry(1, .56, 8, 1)); crown.translate(0, .46, 0);
  const upperCrown = geo(new THREE.ConeGeometry(.72, .52, 8, 1)); upperCrown.translate(0, .74, 0);
  const leafyCrown = geo(new THREE.IcosahedronGeometry(1, 1)); leafyCrown.scale(1.08, .36, .94); leafyCrown.translate(0, .63, 0);
  const canopyShapes = new Set<THREE.BufferGeometry>([crown, upperCrown, leafyCrown]);
  const trunk = geo(new THREE.CylinderGeometry(.65, 1, .54, 6)); trunk.translate(0, .27, 0);
  for (const balanced of [true, false]) {
    vegetationMesh('scrub', scrub, '#657059', '#969c76', balanced);
    vegetationMesh('rock', rock, '#8d8c7d', '#b2ac96', balanced);
    vegetationMesh('tree', crown, '#405b47', '#677b52', balanced, false, 'pine');
    vegetationMesh('tree', upperCrown, '#48644b', '#7a8b5c', balanced, false, 'pine');
    vegetationMesh('tree', leafyCrown, '#5b714b', '#8a9860', balanced, false, 'broadleaf');
    vegetationMesh('tree', trunk, '#685e4b', '#8c806b', balanced, true);
  }
  let disposed = false;
  return { group, count: vegetation.length, buildings: FIELD_STATION.buildings.length, pads: FIELD_STATION.pads.length, woodlands: WOODLANDS.length, solarPanels: 32,
    setVisible(visible: boolean) { group.visible = visible; },
    setQuality(quality: RenderQuality) {
      fineDetails.forEach(object => { object.visible = quality === 'high'; });
      instances.forEach(mesh => { mesh.castShadow = mesh.geometry === boxShape ? !fineDetails.includes(mesh) : quality === 'high' && canopyShapes.has(mesh.geometry); });
    },
    dispose() {
      if (disposed) return; disposed = true;
      group.removeFromParent(); instances.forEach(mesh => mesh.dispose()); geometries.forEach(shape => shape.dispose()); materials.forEach(mat => mat.dispose()); group.clear();
    },
  };
}
