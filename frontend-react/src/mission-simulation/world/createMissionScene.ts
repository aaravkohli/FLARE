import type { NetworkDisplay, SwarmSnapshot } from '../swarm';
import { samplePacketEvents } from './networkPlayback';
import { createThreatLayer } from './ThreatLayer';
import { threatVisual, threatPacketProgress } from './threatState';
import { routeCurve, curvePoint } from './networkGeometry';
import type { RouteCurve } from './networkGeometry';
import { networkRouteState } from './networkState';
import { routeAppearance, routeLegRange } from './routeVisual';
import type { RouteAppearance } from './routeVisual';
import { PACKET_CAPACITY, packetSample, advancePacketPhase, packetFlowEnabled } from './PacketStream';
import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { terrainHeight, GROUND_STATION, TERRAIN_DISCLOSURE, TERRAIN_EXTENT, TERRAIN_PROVENANCE } from './terrain';
import { createEnvironmentProps, GROUND_SCALE_CUE } from './environmentProps';
import { GROUND_STATION_SITE, GROUND_STATION_VISUAL_ENDPOINT, stationFootingTopY, stationGroundY } from './groundStation';
import { DAYLIGHT, groundShadowPosition } from './daylight';
import { UAV_VISUAL, rotorDisplayAngle } from './uavVisual';
import { cameraEye, CAMERA_FOV } from './cameraRig';
import type { CameraRig } from './cameraRig';
import { renderPixelRatio } from './renderQuality';
import type { RenderQuality } from './renderQuality';
import type { DroneMotion, Vector3 } from './dynamics';
import type { AttackProfile, MissionSimulationState } from '../types';

export function createMissionScene(canvas: HTMLCanvasElement) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  let quality: RenderQuality = 'high';
  renderer.setPixelRatio(renderPixelRatio(quality, window.devicePixelRatio));
  canvas.dataset.quality = quality;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  canvas.dataset.terrainSource = TERRAIN_PROVENANCE;
  canvas.title = TERRAIN_DISCLOSURE;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#b7c8c8');
  scene.fog = new THREE.Fog('#b7c8c8', DAYLIGHT.fogNearM, DAYLIGHT.fogFarM);
  const camera = new THREE.PerspectiveCamera(CAMERA_FOV, 1.5, 2, 4500);
  scene.add(new THREE.HemisphereLight('#dce9ef', '#657363', 1.7));
  const sun = new THREE.DirectionalLight('#fff0db', 2.4);
  sun.position.set(DAYLIGHT.sun.x, DAYLIGHT.sun.y, DAYLIGHT.sun.z);
  sun.castShadow = true;
  sun.shadow.mapSize.set(DAYLIGHT.shadowMapSize, DAYLIGHT.shadowMapSize);
  sun.shadow.camera.left = -DAYLIGHT.shadowHalfSpanM;
  sun.shadow.camera.right = DAYLIGHT.shadowHalfSpanM;
  sun.shadow.camera.top = DAYLIGHT.shadowHalfSpanM;
  sun.shadow.camera.bottom = -DAYLIGHT.shadowHalfSpanM;
  sun.shadow.camera.near = 50;
  sun.shadow.camera.far = DAYLIGHT.shadowFarM;
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.bias = -.00008;
  sun.shadow.normalBias = .035;
  sun.shadow.radius = 2;
  scene.add(sun);
  function refreshPixelRatio() {
    const next = renderPixelRatio(quality, window.devicePixelRatio);
    if (renderer.getPixelRatio() !== next) renderer.setPixelRatio(next);
  }
  function setQuality(next: RenderQuality) {
    if (next !== quality) {
      quality = next;
      const shadowSize = next === 'high' ? DAYLIGHT.shadowMapSize : DAYLIGHT.shadowMapSize / 2;
      if (sun.shadow.mapSize.x !== shadowSize) {
        // Three otherwise resizes the target on the next shadow pass. Releasing its
        // depth/color allocations here keeps the old 1024² target out of memory.
        if (sun.shadow.map) {
          sun.shadow.map.depthTexture?.dispose();
          sun.shadow.map.dispose();
          sun.shadow.map = null;
        }
        sun.shadow.mapPass?.dispose();
        sun.shadow.mapPass = null;
        sun.shadow.mapSize.set(shadowSize, shadowSize);
        sun.shadow.needsUpdate = true;
        renderer.shadowMap.needsUpdate = true;
      }
    }
    refreshPixelRatio();
    canvas.dataset.quality = quality;
    canvas.dataset.shadowMapSize = String(sun.shadow.mapSize.x);
  }
  canvas.dataset.shadowMapSize = String(sun.shadow.mapSize.x);
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const geo = <T extends THREE.BufferGeometry>(g: T) => { geometries.push(g); return g; };
  const mat = <T extends THREE.Material>(m: T) => { materials.push(m); return m; };
  const sky = new THREE.Mesh(geo(new THREE.SphereGeometry(1, 32, 16)), mat(new THREE.ShaderMaterial({
    uniforms: {
      zenithColor: { value: new THREE.Color('#7da4be') },
      horizonColor: { value: new THREE.Color('#bdced2') },
      lowColor: { value: new THREE.Color('#b4beb8') },
      hazeColor: { value: new THREE.Color('#ced9d4') },
    },
    vertexShader: `varying vec3 vDirection;
      void main() {
        vDirection = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `varying vec3 vDirection;
      uniform vec3 zenithColor;
      uniform vec3 horizonColor;
      uniform vec3 lowColor;
      uniform vec3 hazeColor;
      void main() {
        float elevation = normalize(vDirection).y;
        vec3 color = mix(lowColor, horizonColor, smoothstep(-0.28, 0.025, elevation));
        color = mix(color, zenithColor, smoothstep(0.01, 0.82, elevation));
        float haze = 1.0 - smoothstep(0.01, 0.22, abs(elevation));
        color = mix(color, hazeColor, haze * 0.22);
        gl_FragColor = vec4(color, 1.0);
        #include <colorspace_fragment>
      }`,
    side: THREE.BackSide, depthTest: false, depthWrite: false, fog: false, toneMapped: false,
  })));
  sky.scale.setScalar(4300);
  sky.frustumCulled = false;
  sky.renderOrder = -1000;
  scene.add(sky);
  const horizonMaterial = mat(new THREE.MeshStandardMaterial({ color: '#ffffff', vertexColors: true, roughness: .94 }));
  const missionMaterial = mat(new THREE.MeshStandardMaterial({ color: '#ffffff', vertexColors: true, roughness: .94 }));
  const terrainTextures: THREE.Texture[] = [];
  let terrainDisposed = false;
  const lowland = new THREE.Color('#4d604c'), highland = new THREE.Color('#848476');
  const stone = new THREE.Color('#999588'), surfaceColor = new THREE.Color();
  const surfaceNormal = new THREE.Vector3();
  function terrainTile(x0: number, x1: number, z0: number, z1: number, xSegments: number, zSegments: number, central: boolean) {
    const tile = geo(new THREE.PlaneGeometry(x1 - x0, z1 - z0, xSegments, zSegments));
    tile.rotateX(-Math.PI / 2);
    tile.translate((x0 + x1) / 2, 0, (z0 + z1) / 2);
    const positions = tile.getAttribute('position');
    const normals = tile.getAttribute('normal');
    const colors = new Float32Array(positions.count * 3);
    for (let i = 0; i < positions.count; i++) {
      const x = positions.getX(i), z = positions.getZ(i);
      const height = terrainHeight(x, z);
      positions.setY(i, height);
      const eastSlope = (terrainHeight(x + 5, z) - terrainHeight(x - 5, z)) / 10;
      const southSlope = (terrainHeight(x, z + 5) - terrainHeight(x, z - 5)) / 10;
      surfaceNormal.set(-eastSlope, 1, -southSlope).normalize();
      normals.setXYZ(i, surfaceNormal.x, surfaceNormal.y, surfaceNormal.z);
      surfaceColor.copy(lowland).lerp(highland, Math.max(0, Math.min(1, (height + 52) / 70)));
      surfaceColor.lerp(stone, Math.max(0, Math.min(.48, (1 - surfaceNormal.y) * 2.8)));
      colors[i * 3] = surfaceColor.r;
      colors[i * 3 + 1] = surfaceColor.g;
      colors[i * 3 + 2] = surfaceColor.b;
    }
    positions.needsUpdate = true;
    normals.needsUpdate = true;
    tile.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const mesh = new THREE.Mesh(tile, central ? missionMaterial : horizonMaterial);
    mesh.receiveShadow = true;
    mesh.castShadow = central;
    scene.add(mesh);
  }
  // Nine stitched tiles keep the source's ~10 m detail around the mission and
  // use fewer triangles for the horizon. Shared edge subdivisions avoid cracks.
  const halfWidth = TERRAIN_EXTENT.widthM / 2, halfDepth = TERRAIN_EXTENT.depthM / 2;
  const xEdges = [-7000, -halfWidth, halfWidth, 7000];
  const zEdges = [-7000, -halfDepth, halfDepth, 7000];
  for (let x = 0; x < 3; x++) for (let z = 0; z < 3; z++) {
    terrainTile(xEdges[x], xEdges[x + 1], zEdges[z], zEdges[z + 1],
      x === 1 ? TERRAIN_EXTENT.columns - 1 : 48,
      z === 1 ? TERRAIN_EXTENT.rows - 1 : 48,
      x === 1 && z === 1);
  }
  if (TERRAIN_PROVENANCE === 'usgs-3dep') {
    const loader = new THREE.TextureLoader();
    const loaded = (texture: THREE.Texture, attach: () => void) => {
      if (terrainDisposed) { texture.dispose(); return; }
      texture.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 4);
      terrainTextures.push(texture);
      attach();
      missionMaterial.needsUpdate = true;
    };
    loader.load(new URL('../assets/terrain/terrain-albedo.png', import.meta.url).href, texture => loaded(texture, () => {
      texture.colorSpace = THREE.SRGBColorSpace;
      missionMaterial.map = texture;
      canvas.dataset.terrainMaterial = 'dem-derived-maps';
    }), undefined, () => { canvas.dataset.terrainMaterial = 'vertex-color'; });
    loader.load(new URL('../assets/terrain/terrain-normal.png', import.meta.url).href, texture => loaded(texture, () => {
      missionMaterial.normalMap = texture;
      missionMaterial.normalScale.set(.35, .35);
    }), undefined, () => {});
    loader.load(new URL('../assets/terrain/terrain-roughness.png', import.meta.url).href, texture => loaded(texture, () => {
      missionMaterial.roughnessMap = texture;
    }), undefined, () => {});
  }
  canvas.dataset.terrainMaterial = 'vertex-color';
  const boundary: THREE.Vector3[]=[];
  for(const x of [-360,360]) for(const z of [-240,240]) {
    const insetX=x-Math.sign(x)*35,insetZ=z-Math.sign(z)*35;
    boundary.push(new THREE.Vector3(insetX,terrainHeight(insetX,z)+.6,z),new THREE.Vector3(x,terrainHeight(x,z)+.6,z),
      new THREE.Vector3(x,terrainHeight(x,z)+.6,z),new THREE.Vector3(x,terrainHeight(x,insetZ)+.6,insetZ));
  }
  scene.add(new THREE.LineSegments(geo(new THREE.BufferGeometry().setFromPoints(boundary)),mat(new THREE.LineBasicMaterial({color:'#89958d',transparent:true,opacity:.3}))));
  const environmentProps = createEnvironmentProps();
  canvas.dataset.environmentProps = String(environmentProps.length);
  const instanceObject = new THREE.Object3D(), instanceColor = new THREE.Color();
  function addGroundInstances(kind: 'scrub' | 'rock') {
    const props = environmentProps.filter(prop => prop.kind === kind);
    const shape = geo(kind === 'scrub' ? new THREE.IcosahedronGeometry(1, 0) : new THREE.DodecahedronGeometry(1, 0));
    shape.scale(1, kind === 'scrub' ? .65 : .48, 1);
    shape.translate(0, kind === 'scrub' ? .65 : .48, 0);
    const material = mat(new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 1, flatShading: true }));
    const instances = new THREE.InstancedMesh(shape, material, props.length);
    const dark = new THREE.Color(kind === 'scrub' ? '#53684e' : '#777c72');
    const light = new THREE.Color(kind === 'scrub' ? '#879474' : '#a39e8b');
    props.forEach((prop, index) => {
      instanceObject.position.set(prop.x, prop.y - (kind === 'rock' ? .3 : .1), prop.z);
      instanceObject.rotation.set(0, prop.rotation, 0);
      instanceObject.scale.setScalar(prop.radiusM);
      instanceObject.updateMatrix();
      instances.setMatrixAt(index, instanceObject.matrix);
      instances.setColorAt(index, instanceColor.copy(dark).lerp(light, prop.tone));
    });
    instances.instanceMatrix.needsUpdate = true;
    if (instances.instanceColor) instances.instanceColor.needsUpdate = true;
    instances.castShadow = true;
    instances.receiveShadow = true;
    instances.computeBoundingSphere();
    scene.add(instances);
    return instances;
  }
  const scrubInstances = addGroundInstances('scrub');
  const rockInstances = addGroundInstances('rock');
  const scaleLines: THREE.Vector3[] = [];
  function scalePoint(x: number, z: number) { return new THREE.Vector3(x, terrainHeight(x, z) + .8, z); }
  for (let i = 0; i < 8; i++) {
    const startX = GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.lengthM * i / 8;
    const endX = GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.lengthM * (i + 1) / 8;
    scaleLines.push(scalePoint(startX, GROUND_SCALE_CUE.z), scalePoint(endX, GROUND_SCALE_CUE.z));
  }
  for (const x of [GROUND_SCALE_CUE.westX, GROUND_SCALE_CUE.eastX]) {
    scaleLines.push(scalePoint(x, GROUND_SCALE_CUE.z - 4), scalePoint(x, GROUND_SCALE_CUE.z + 4));
  }
  scene.add(new THREE.LineSegments(geo(new THREE.BufferGeometry().setFromPoints(scaleLines)),
    mat(new THREE.LineBasicMaterial({ color: '#c5cebf', transparent: true, opacity: .65 }))));
  const scaleCanvas = document.createElement('canvas');
  scaleCanvas.width = 256; scaleCanvas.height = 96;
  const scaleContext = scaleCanvas.getContext('2d');
  if (scaleContext) {
    scaleContext.fillStyle = 'rgba(16, 24, 32, .82)';
    scaleContext.fillRect(18, 10, 220, 76);
    scaleContext.strokeStyle = 'rgba(190, 207, 188, .64)';
    scaleContext.strokeRect(18.5, 10.5, 219, 75);
    scaleContext.font = '700 72px ui-monospace, monospace';
    scaleContext.textAlign = 'center';
    scaleContext.fillStyle = '#f0f4e8';
    scaleContext.shadowColor = '#0c1118';
    scaleContext.shadowBlur = 5;
    scaleContext.fillText('50 m', 128, 76);
    const scaleTexture = new THREE.CanvasTexture(scaleCanvas);
    terrainTextures.push(scaleTexture);
    const scaleLabel = new THREE.Sprite(mat(new THREE.SpriteMaterial({ map: scaleTexture, transparent: true, depthWrite: false })));
    const middleX = (GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.eastX) / 2;
    scaleLabel.position.set(middleX, terrainHeight(middleX, GROUND_SCALE_CUE.z) + 19, GROUND_SCALE_CUE.z);
    scaleLabel.scale.set(100, 36, 1);
    scene.add(scaleLabel);
  }
  const bodyGeo = geo(new THREE.BoxGeometry(UAV_VISUAL.bodyWidthM, UAV_VISUAL.bodyHeightM, UAV_VISUAL.bodyLengthM));
  const armGeo = geo(new THREE.BoxGeometry(2, 2, 20));
  const rotorGeo = geo(new THREE.CylinderGeometry(UAV_VISUAL.rotorRadiusM, UAV_VISUAL.rotorRadiusM, .15, 24));
  const bladeGeo = geo(new THREE.BoxGeometry(UAV_VISUAL.rotorRadiusM * 2, .4, 1));
  const noseGeo = geo(new THREE.BoxGeometry(5, 2, 3));
  const blurMat = mat(new THREE.MeshBasicMaterial({ color: '#a6b6bf', transparent: true, opacity: .13, depthWrite: false }));
  const noseMat = mat(new THREE.MeshStandardMaterial({ color: '#c8d3d9', roughness: .8 }));
  const droneAircraftMat = mat(new THREE.MeshStandardMaterial({ color: '#b2bdc5', roughness: .8 }));
  const aircraftMat = mat(new THREE.MeshStandardMaterial({ color: '#abbcc0', roughness: .66, metalness: .16 }));
  const darkMat = mat(new THREE.MeshStandardMaterial({ color: '#4d616f', roughness: .9 }));
  const selectMat = mat(new THREE.LineBasicMaterial({ color: '#c2e5ef', transparent: true, opacity: .9, fog: false }));
  const hoverMat = mat(new THREE.LineBasicMaterial({ color: '#9db8c2', transparent: true, opacity: .55, fog: false }));
  const markerPoints = Array.from({ length: 33 }, (_, index) => new THREE.Vector3(
    Math.cos(index * Math.PI / 16) * UAV_VISUAL.selectionRingRadiusM, 0,
    Math.sin(index * Math.PI / 16) * UAV_VISUAL.selectionRingRadiusM));
  const selectionMarkerGeo = geo(new THREE.BufferGeometry().setFromPoints(markerPoints));
  const stemMat = mat(new THREE.LineBasicMaterial({ color: '#aacbd8', transparent: true, opacity: .55, fog: false }));
  const hoverStemMat = mat(new THREE.LineBasicMaterial({ color: '#8ea7b2', transparent: true, opacity: .34, fog: false }));
  const shadowPixels=new Uint8Array(64*64*4);
  for(let y=0;y<64;y++)for(let x=0;x<64;x++){
    const r=Math.hypot((x-31.5)/31.5,(y-31.5)/31.5),i=(y*64+x)*4;
    shadowPixels[i+3]=Math.round(Math.max(0,1-r)**2*90);
  }
  const shadowTexture=new THREE.DataTexture(shadowPixels,64,64);shadowTexture.needsUpdate=true;
  const shadowGeo = geo(new THREE.CircleGeometry(10, 16)); shadowGeo.rotateX(-Math.PI / 2);
  const shadowMat = mat(new THREE.MeshBasicMaterial({ color: '#03080b', transparent: true, map: shadowTexture, opacity: .3, depthWrite: false }));
  const fleet = new Map<string, { group: THREE.Group; body: THREE.Group; marker: THREE.Line; stem: THREE.Line; shadow: THREE.Mesh; blades: THREE.InstancedMesh; rotors: THREE.InstancedMesh }>();
  const base = new THREE.Group(); base.position.copy(GROUND_STATION);
  const stationWall = mat(new THREE.MeshStandardMaterial({ color: '#a7b1ad', roughness: .88, metalness: .08 }));
  const stationRoof = mat(new THREE.MeshStandardMaterial({ color: '#485d65', roughness: .8, metalness: .2 }));
  const stationConcrete = mat(new THREE.MeshStandardMaterial({ color: '#777e77', roughness: 1 }));
  const stationPavement = mat(new THREE.MeshStandardMaterial({ color: '#485250', roughness: 1 }));
  const stationSteel = mat(new THREE.MeshStandardMaterial({ color: '#677d83', roughness: .63, metalness: .33 }));
  const stationGlass = mat(new THREE.MeshStandardMaterial({ color: '#273d49', roughness: .3, metalness: .22 }));
  const stationMark = mat(new THREE.MeshStandardMaterial({ color: '#afb8aa', roughness: 1, side: THREE.DoubleSide }));
  const stationDish = mat(new THREE.MeshStandardMaterial({ color: '#c4ccc6', roughness: .55, metalness: .22, side: THREE.DoubleSide }));
  const stationEndpoint = mat(new THREE.MeshBasicMaterial({ color: '#83adba' }));
  function stationBox(width: number, height: number, depth: number, material: THREE.Material, x: number, y: number, z: number) {
    const mesh = new THREE.Mesh(geo(new THREE.BoxGeometry(width, height, depth)), material);
    mesh.position.set(x, y, z); base.add(mesh);
    return mesh;
  }
  function stationSlab(site: { x: number; z: number; width: number; depth: number; slabDepth: number }, material: THREE.Material) {
    const top = stationFootingTopY(site.x, site.z, site.width, site.depth);
    stationBox(site.width, site.slabDepth, site.depth, material, site.x, top - site.slabDepth / 2, site.z);
    return top;
  }
  const shelter = GROUND_STATION_SITE.shelter;
  const shelterTop = stationSlab(shelter, stationConcrete);
  stationBox(shelter.width - 3, shelter.height, shelter.depth - 3, stationWall,
    shelter.x, shelterTop + shelter.height / 2, shelter.z);
  stationBox(shelter.width + 2, 1.4, shelter.depth + 2, stationRoof,
    shelter.x, shelterTop + shelter.height + .7, shelter.z);
  const shelterFront = shelter.z + (shelter.depth - 3) / 2 + .12;
  stationBox(5, 7, .35, stationRoof, shelter.x + 8, shelterTop + 3.5, shelterFront);
  stationBox(9, .55, 4, stationRoof, shelter.x + 8, shelterTop + 8.2, shelterFront + 1.6);
  for (const x of [shelter.x - 9, shelter.x - 1]) {
    stationBox(5.4, 2.4, .3, stationGlass, x, shelterTop + 6.1, shelterFront);
  }
  stationBox(1.1, 10.3, .45, stationRoof, shelter.x - 14.7, shelterTop + 5.15, shelterFront);

  const mastSite = GROUND_STATION_SITE.mast;
  const mastTop = stationSlab(mastSite, stationConcrete);
  const mast = new THREE.Mesh(geo(new THREE.CylinderGeometry(.55, .92, mastSite.height, 10)), stationSteel);
  mast.position.set(mastSite.x, mastTop + mastSite.height / 2, mastSite.z); base.add(mast);
  const mastCollarGeo = geo(new THREE.CylinderGeometry(1.15, 1.15, .5, 10));
  for (const level of [4, 15, 25]) {
    const collar = new THREE.Mesh(mastCollarGeo, stationRoof);
    collar.position.set(mastSite.x, mastTop + level, mastSite.z); base.add(collar);
  }
  const antennaHead = new THREE.Mesh(geo(new THREE.SphereGeometry(.7, 10, 8)), stationEndpoint);
  antennaHead.position.set(mastSite.x, mastTop + mastSite.height, mastSite.z); base.add(antennaHead);
  stationBox(8, .45, .55, stationSteel, mastSite.x, mastTop + 19, mastSite.z);
  stationBox(.55, .45, 8, stationSteel, mastSite.x, mastTop + 24, mastSite.z);
  const dishMount = new THREE.Group();
  dishMount.position.set(mastSite.x, mastTop + mastSite.height - 5, mastSite.z);
  dishMount.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(.83, .42, -.36).normalize());
  base.add(dishMount);
  const dishProfile = [
    new THREE.Vector2(0, 0), new THREE.Vector2(1, .08), new THREE.Vector2(2, .32),
    new THREE.Vector2(3, .72), new THREE.Vector2(4, 1.28), new THREE.Vector2(5, 2),
  ];
  dishMount.add(new THREE.Mesh(geo(new THREE.LatheGeometry(dishProfile, 32)), stationDish));
  const dishRim = new THREE.Mesh(geo(new THREE.TorusGeometry(5, .2, 6, 32)), stationSteel);
  dishRim.rotation.x = -Math.PI / 2; dishRim.position.y = 2; dishMount.add(dishRim);
  const feed = new THREE.Mesh(geo(new THREE.CylinderGeometry(.42, .3, 1, 8)), stationRoof);
  feed.position.y = 4; dishMount.add(feed);
  const dishStrutGeo = geo(new THREE.CylinderGeometry(.09, .09, 1, 6));
  const feedPoint = new THREE.Vector3(0, 3.8, 0);
  const vertical = new THREE.Vector3(0, 1, 0);
  for (let index = 0; index < 3; index++) {
    const angle = index * Math.PI * 2 / 3;
    const rimPoint = new THREE.Vector3(Math.cos(angle) * 4.5, 1.65, Math.sin(angle) * 4.5);
    const strut = new THREE.Mesh(dishStrutGeo, stationSteel);
    strut.position.copy(rimPoint).add(feedPoint).multiplyScalar(.5);
    strut.quaternion.setFromUnitVectors(vertical, feedPoint.clone().sub(rimPoint).normalize());
    strut.scale.y = rimPoint.distanceTo(feedPoint);
    dishMount.add(strut);
  }

  const equipment = GROUND_STATION_SITE.equipment;
  const equipmentTop = stationSlab(equipment, stationConcrete);
  stationBox(8, equipment.height, 7, stationRoof, equipment.x, equipmentTop + equipment.height / 2, equipment.z);
  stationBox(5.5, 4, .22, stationSteel, equipment.x, equipmentTop + 3.2, equipment.z + 3.62);
  for (const x of [-2.3, 0, 2.3]) stationBox(1.1, .18, .25, stationGlass, x, equipmentTop + 5.8, equipment.z + 3.77);
  stationBox(1.5, .7, 3.1, stationSteel, 0, 0, -1.55);
  stationBox(2.1, 1.1, 1.2, stationSteel, 0, 0, 0);

  const servicePad = GROUND_STATION_SITE.servicePad;
  const serviceTop = stationSlab(servicePad, stationPavement);
  const guideY = serviceTop + .09;
  for (const side of [-1, 1]) {
    stationBox(servicePad.width - 2, .13, .6, stationMark,
      servicePad.x, guideY, servicePad.z + side * (servicePad.depth / 2 - 1));
    stationBox(.6, .13, servicePad.depth - 2, stationMark,
      servicePad.x + side * (servicePad.width / 2 - 1), guideY, servicePad.z);
  }
  const serviceRing = new THREE.Mesh(geo(new THREE.RingGeometry(5.6, 6, 32)), stationMark);
  serviceRing.rotation.x = -Math.PI / 2;
  serviceRing.position.set(servicePad.x, guideY + .03, servicePad.z);
  base.add(serviceRing);
  for (const x of [servicePad.x - 9, servicePad.x + 9]) {
    stationBox(.8, .13, 5, stationMark, x, guideY, servicePad.z);
  }
  const markerGeo = geo(new THREE.CylinderGeometry(.42, .55, 2.2, 8));
  const markerCapGeo = geo(new THREE.CylinderGeometry(.55, .55, .3, 8));
  for (const [x, z] of [[-42, -29], [42, -29], [-42, 35], [42, 35]]) {
    const ground = stationGroundY(x, z);
    const marker = new THREE.Mesh(markerGeo, stationRoof);
    marker.position.set(x, ground + 1.1, z); base.add(marker);
    const cap = new THREE.Mesh(markerCapGeo, stationMark);
    cap.position.set(x, ground + 2.25, z); base.add(cap);
  }
  base.traverse(object => {
    if (object instanceof THREE.Mesh) { object.castShadow = true; object.receiveShadow = true; }
  });
  base.userData.asset = 'base'; scene.add(base);
  const satellite = new THREE.Group();
  satellite.add(new THREE.Mesh(geo(new THREE.BoxGeometry(12, 10, 10)), aircraftMat));
  for (const x of [-20,20]) { const panel = new THREE.Mesh(geo(new THREE.BoxGeometry(24, 1, 18)), darkMat); panel.position.x = x; satellite.add(panel); }
  satellite.scale.setScalar(.65);
  satellite.userData.asset = 'satellite'; scene.add(satellite);
  // Tapered, metric-driven visual pulses; dimensions are illustrative scene metres.
  const packetGeometry = geo(new THREE.ConeGeometry(.95, 6, 6));
  const trailGeometry = geo(new THREE.ConeGeometry(.5, 10, 6));
  const packetMaterial = mat(new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true,
    opacity: .92, depthWrite: false, fog: false, toneMapped: false }));
  const trailMaterial = mat(new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true,
    opacity: .18, depthWrite: false, fog: false, toneMapped: false }));
  const routeMaterial = (color: string, linewidth: number, opacity: number, dashed = false) =>
    mat(new LineMaterial({ color, linewidth, transparent: true, opacity, dashed,
      dashSize: 9, gapSize: 9, depthWrite: false, fog: false, toneMapped: false }));
  const routeMaterials = {
    direct: routeMaterial('#c3e3eb', 2.7, .92),
    uplink: routeMaterial('#c3e3eb', 2.6, .92),
    downlink: routeMaterial('#91c4d5', 2.4, .88),
    degraded: routeMaterial('#d9b57d', 2.7, .94),
    unsafe: routeMaterial('#d49a90', 2.7, .94),
    requested: routeMaterial('#abc0c7', 1.7, .48, true),
    requestedWarning: routeMaterial('#c5a57f', 1.7, .48, true),
    alternative: routeMaterial('#78919a', 1.15, .17),
    inactive: routeMaterial('#879198', 1.2, .16, true),
    halo: routeMaterial('#9fd8e8', 6, .12),
    haloWarning: routeMaterial('#d0ac77', 6, .1),
  };
  const routeMaterialList = Object.values(routeMaterials);
  function routeLeg() {
    const geometry = geo(new LineGeometry());
    geometry.setPositions(new Float32Array(33 * 3));
    const core = new Line2(geometry, routeMaterials.direct);
    core.computeLineDistances();
    const halo = new Line2(geometry, routeMaterials.halo);
    core.frustumCulled = false; halo.frustumCulled = false;
    core.visible = false; halo.visible = false;
    scene.add(halo, core);
    return { geometry, core, halo };
  }
  const routeLines = (['direct','satellite'] as const).map(id => {
    const legs = Array.from({ length: id === 'satellite' ? 2 : 1 }, routeLeg);
    const packets = new THREE.InstancedMesh(packetGeometry, packetMaterial, PACKET_CAPACITY);
    const trails = new THREE.InstancedMesh(trailGeometry, trailMaterial, PACKET_CAPACITY);
    for (const mesh of [packets, trails]) {
      mesh.frustumCulled = false;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      scene.add(mesh);
    }
    trails.renderOrder = 2; packets.renderOrder = 3;
    return { id, legs, packets, trails, phase: 0, owner: '', appearance: 'hidden' as RouteAppearance,
      packetThreat: 'none' as AttackProfile, latency: undefined as number | undefined, loss: undefined as number | undefined };
  });
  const neighborhoodGeometry = geo(new THREE.BufferGeometry());
  const neighborhoodMaterial = mat(new THREE.LineDashedMaterial({ color: '#6a7c85', transparent: true, opacity: .1, dashSize: 3, gapSize: 9, fog: false }));
  const neighborhood = new THREE.LineSegments(neighborhoodGeometry, neighborhoodMaterial);
  neighborhood.frustumCulled = false; scene.add(neighborhood);
  const packetObject = new THREE.Object3D();
  const packetPose = new THREE.Object3D();
  const packetColor = new THREE.Color();
  const packetDirection = new THREE.Vector3();
  const packetAxis = new THREE.Vector3(0, 1, 0);
  let previousNetworkSeconds = 0;
  const threats = createThreatLayer(scene, terrainHeight);
  const raycaster = new THREE.Raycaster();
  const renderSize = new THREE.Vector2();
  const curveScratch = {x:0,y:0,z:0};
  const tangentBefore = {x:0,y:0,z:0}, tangentAfter = {x:0,y:0,z:0};
  let routeDrone: DroneMotion | undefined;
  function routeDirection(curve: RouteCurve, progress: number) {
    // Satellite has a real visual bend: each pulse follows its current leg.
    const from = curve.points.length === 3 && progress >= .5 ? .5 : 0;
    const to = curve.points.length === 3 && progress < .5 ? .5 : 1;
    const before = curvePoint(curve, Math.max(from, progress - .004), tangentBefore);
    const after = curvePoint(curve, Math.min(to, progress + .004), tangentAfter);
    packetDirection.set(after.x - before.x, after.y - before.y, after.z - before.z);
    return packetDirection.lengthSq() > 1e-8 ? packetDirection.normalize() : packetDirection.set(0, 1, 0);
  }
  function updateRouteLeg(geometry: LineGeometry, curve: RouteCurve, from: number, to: number) {
    const starts = geometry.getAttribute('instanceStart') as THREE.InterleavedBufferAttribute;
    const ends = geometry.getAttribute('instanceEnd') as THREE.InterleavedBufferAttribute;
    const distanceStarts = geometry.getAttribute('instanceDistanceStart') as THREE.InterleavedBufferAttribute;
    const distanceEnds = geometry.getAttribute('instanceDistanceEnd') as THREE.InterleavedBufferAttribute;
    const first = curvePoint(curve, from, curveScratch);
    let px = first.x, py = first.y, pz = first.z, distance = 0;
    for (let index = 0; index < 32; index++) {
      const next = curvePoint(curve, from + (to - from) * (index + 1) / 32, curveScratch);
      starts.setXYZ(index, px, py, pz);
      ends.setXYZ(index, next.x, next.y, next.z);
      distanceStarts.setX(index, distance);
      distance += Math.hypot(next.x - px, next.y - py, next.z - pz);
      distanceEnds.setX(index, distance);
      px = next.x; py = next.y; pz = next.z;
    }
    starts.data.needsUpdate = true;
    distanceStarts.data.needsUpdate = true;
  }
  function pick(x: number, y: number): string | undefined {
    raycaster.setFromCamera(new THREE.Vector2(x, y), camera);
    const hits = raycaster.intersectObjects([...fleet.values()].map(d => d.group).concat([base, satellite]), true);
    for (const hit of hits) { let object: THREE.Object3D | null = hit.object; while (object) { if (object.userData.asset) return object.userData.asset as string; object = object.parent; } }
    // Screen-space tolerance makes small, moving UAVs selectable without precision clicking.
    const px=(x+1)*canvas.clientWidth/2, py=(1-y)*canvas.clientHeight/2;
    let nearest: string | undefined, distance=18;
    for (const id of fleet.keys()) {
      const point=screenPoint(id);
      if (!point?.visible) continue;
      const delta=Math.hypot(point.x-px,point.y-py);
      if (delta<distance) { nearest=id; distance=delta; }
    }
    if (nearest) return nearest;
    for (const hit of raycaster.intersectObject(threats.root,true)) {
      let object: THREE.Object3D | null=hit.object;let visible=true;
      while(object){if(!object.visible)visible=false;object=object.parent;}
      if(visible)return 'jammer';
    }
  }
  const swarmParts=[new THREE.InstancedMesh(bodyGeo,droneAircraftMat,100),new THREE.InstancedMesh(armGeo,darkMat,400),
    new THREE.InstancedMesh(noseGeo,noseMat,100),new THREE.InstancedMesh(rotorGeo,blurMat,400),
    new THREE.InstancedMesh(bladeGeo,droneAircraftMat,400),new THREE.InstancedMesh(shadowGeo,shadowMat,100)];
  const bodyMatrix=new THREE.Matrix4(),localMatrix=new THREE.Matrix4();
  const localPose=new THREE.Object3D();
  for(const part of swarmParts){part.frustumCulled=false;part.visible=false;scene.add(part);}
  const swarmGeometry=geo(new THREE.BufferGeometry());
  const swarmPositions=new Float32Array(60000), swarmColors=new Float32Array(60000);
  swarmGeometry.setAttribute('position',new THREE.BufferAttribute(swarmPositions,3));
  swarmGeometry.setAttribute('color',new THREE.BufferAttribute(swarmColors,3));
  const swarmLines=new THREE.LineSegments(swarmGeometry,mat(new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.6})));
  swarmLines.frustumCulled=false; scene.add(swarmLines);
  const inspectedRouteMaterial=mat(new LineMaterial({color:'#9ce1c1',linewidth:2.5,transparent:true,opacity:.95,depthTest:false}));
  const inspectedRoute=new Line2(geo(new LineGeometry()),inspectedRouteMaterial);inspectedRoute.frustumCulled=false;inspectedRoute.renderOrder=4;scene.add(inspectedRoute);
  const swarmPackets=new THREE.InstancedMesh(packetGeometry,packetMaterial,200);
  const virtualStart={x:0,y:0,z:0},virtualEnd={x:0,y:0,z:0};
  swarmPackets.frustumCulled=false; scene.add(swarmPackets);
  const rangeSphere=new THREE.LineSegments(geo(new THREE.EdgesGeometry(new THREE.SphereGeometry(1,24,12))),mat(new THREE.LineBasicMaterial({color:'#91d4c4',transparent:true,opacity:.12})));
  rangeSphere.visible=false;scene.add(rangeSphere);
  const interferenceMaterial=mat(new THREE.LineBasicMaterial({color:'#ddb078',transparent:true,opacity:.18}));
  const interferenceRegions=Array.from({length:8},()=>{const mesh=new THREE.LineSegments(rangeSphere.geometry,interferenceMaterial);mesh.visible=false;scene.add(mesh);return mesh;});
  function draw(drones: readonly DroneMotion[], selectedId: string, orbit: Vector3, rig: CameraRig, state: MissionSimulationState, seconds: number, reduced = false, hoveredId?: string, showAlternatives = false, evidenceCurrent = true, mission?: SwarmSnapshot, selectedIds: string[] = [], display?: NetworkDisplay) {
    const ids = new Set(drones.map(d => d.id));
    for (const [id, entity] of fleet) if (!ids.has(id)) { scene.remove(entity.group, entity.stem, entity.shadow); entity.stem.geometry.dispose(); entity.blades.dispose(); entity.rotors.dispose(); fleet.delete(id); }
    for (const drone of drones) {
      let entity = fleet.get(drone.id);
      if (!entity) {
        const group = new THREE.Group(); group.userData.asset = drone.id;
        const body = new THREE.Group(); group.add(body);
        body.add(new THREE.Mesh(bodyGeo, droneAircraftMat));
        const rotors = new THREE.InstancedMesh(rotorGeo, blurMat, 4);
        const blades = new THREE.InstancedMesh(bladeGeo, droneAircraftMat, 4);
        body.add(rotors, blades);
        let rotorIndex=0;
        for (const x of [-UAV_VISUAL.motorOffsetM, UAV_VISUAL.motorOffsetM])
          for (const z of [-UAV_VISUAL.motorOffsetM, UAV_VISUAL.motorOffsetM]) {
            const arm = new THREE.Mesh(armGeo, darkMat);
            arm.position.set(x / 2, 0, z / 2);
            arm.rotation.y = Math.atan2(x, z);
            body.add(arm);
            packetObject.position.set(x, 3, z);
            packetObject.rotation.set(0, 0, 0); packetObject.updateMatrix();
            rotors.setMatrixAt(rotorIndex, packetObject.matrix);
            blades.setMatrixAt(rotorIndex++, packetObject.matrix);
        }
        const nose = new THREE.Mesh(noseGeo, noseMat); nose.position.set(0, 1, 10); body.add(nose);
        rotors.instanceMatrix.needsUpdate = true;
        blades.instanceMatrix.needsUpdate = true;
        rotors.computeBoundingSphere(); blades.computeBoundingSphere();
        body.traverse(object => {
          if (object instanceof THREE.Mesh && !(object instanceof THREE.InstancedMesh)) object.castShadow = true;
        });
        const marker = new THREE.Line(selectionMarkerGeo, selectMat); group.add(marker);
        const stem = new THREE.Line(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(6),3)), stemMat); stem.frustumCulled = false;
        const shadow = new THREE.Mesh(shadowGeo, shadowMat); scene.add(group, stem, shadow);
        entity = { group, body, marker, stem, shadow, blades, rotors }; fleet.set(drone.id, entity);
      }
      entity.body.visible=!mission; entity.shadow.visible=!mission;
      entity.group.position.copy(drone.position); entity.body.rotation.set(drone.pitch, -drone.heading + Math.PI / 2, drone.roll, 'YXZ');
      const selected = drone.id === selectedId, hovered = drone.id === hoveredId && !selected;
      entity.marker.visible = selected || hovered;
      entity.marker.material = selected ? selectMat : hoverMat;
      entity.stem.visible = selected || hovered;
      entity.stem.material = selected ? stemMat : hoverStemMat;
      entity.rotors.visible = !reduced;
      let rotorIndex=0;
      for (const x of [-UAV_VISUAL.motorOffsetM, UAV_VISUAL.motorOffsetM])
        for (const z of [-UAV_VISUAL.motorOffsetM, UAV_VISUAL.motorOffsetM]) {
          packetObject.position.set(x, 3, z);
          packetObject.rotation.set(0, rotorDisplayAngle(seconds, rotorIndex, reduced), 0);
          packetObject.updateMatrix();
          entity.blades.setMatrixAt(rotorIndex++, packetObject.matrix);
      }
      entity.blades.instanceMatrix.needsUpdate=true;
      packetObject.rotation.set(0,0,0);
      const ground = terrainHeight(drone.position.x, drone.position.z) + .6;
      const shadowPosition = groundShadowPosition(drone.position, terrainHeight);
      entity.shadow.position.copy(shadowPosition);
      entity.shadow.scale.setScalar(1 + Math.max(0, drone.position.y - shadowPosition.y) / 120);
      const stem = entity.stem.geometry.getAttribute('position'); stem.setXYZ(0, drone.position.x, ground, drone.position.z); stem.setXYZ(1, drone.position.x, drone.position.y, drone.position.z); stem.needsUpdate = true;
    }
    satellite.position.copy(orbit);
    const selected = drones.find(d => d.id === selectedId);
    routeDrone = selected;


    const dt = Math.max(0, Math.min(.1, seconds - previousNetworkSeconds));
    previousNetworkSeconds = seconds;
    const threat=threatVisual(state,selectedId);
    const forwardingPath = (['direct','satellite'] as const).find(path =>
      packetFlowEnabled(networkRouteState(state,path,selectedId).forwarding,evidenceCurrent)) ?? null;
    threats.update(threat,selectedId,selected?.position,orbit,seconds,dt,reduced,forwardingPath);
    const meshState = networkRouteState(state, 'mesh', selectedId);
    const matchingDrone = state.droneId === selectedId;
    const meshAvailable = state.routes.find(route => route.id === 'mesh')?.available ?? false;
    const meshAppearance = routeAppearance(meshState, matchingDrone, meshAvailable, showAlternatives);
    const neighbors = drones.filter(d => d.id !== selectedId);
    // All supplied peers are schematic context, not inferred reachability or forwarding hops.
    neighborhood.visible = !mission && !!selected && neighbors.length > 0 && meshAppearance !== 'hidden';
    if (selected && neighborhood.visible) {
      if (neighborhoodGeometry.getAttribute('position')?.count !== neighbors.length * 2) {
        // Release old GPU buffers before replacing attributes after fleet resizing.
        neighborhoodGeometry.dispose();
        neighborhoodGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(neighbors.length * 6), 3));
        neighborhoodGeometry.setAttribute('lineDistance', new THREE.BufferAttribute(new Float32Array(neighbors.length * 2), 1));
      }
      const positions = neighborhoodGeometry.getAttribute('position');
      neighbors.forEach((peer, i) => {
        positions.setXYZ(i*2, selected.position.x, selected.position.y, selected.position.z);
        positions.setXYZ(i*2+1, peer.position.x, peer.position.y, peer.position.z);
      });
      positions.needsUpdate = true;
      const distances = neighborhoodGeometry.getAttribute('lineDistance');
      neighbors.forEach((peer,i) => { distances.setX(i*2,0); distances.setX(i*2+1,Math.hypot(peer.position.x-selected.position.x,peer.position.y-selected.position.y,peer.position.z-selected.position.z)); });
      distances.needsUpdate = true;
      neighborhoodMaterial.opacity = meshAppearance === 'installed' ? .34 : meshAppearance === 'requested' ? .23 : meshAppearance === 'alternative' ? .11 : .08;
      neighborhoodMaterial.color.set(meshState.hold ? '#7e898e' : meshState.unsafe ? '#c38880' : meshState.degraded ? '#ba9b73' : '#9bb5c0');
    }
    for (const link of routeLines) {
      const status = networkRouteState(state, link.id, selectedId);
      const available = state.routes.find(route => route.id === link.id)?.available ?? false;
      link.appearance = routeAppearance(status, matchingDrone, available, showAlternatives);
      for (const [index, leg] of link.legs.entries()) {
        const visible = !mission && !!selected && link.appearance !== 'hidden';
        leg.core.visible = visible;
        leg.halo.visible = visible && link.appearance === 'installed';
        leg.core.material = link.appearance === 'installed'
          ? status.unsafe ? routeMaterials.unsafe : status.degraded ? routeMaterials.degraded
            : link.id === 'direct' ? routeMaterials.direct : index === 0 ? routeMaterials.uplink : routeMaterials.downlink
          : link.appearance === 'requested'
            ? status.unsafe || status.degraded ? routeMaterials.requestedWarning : routeMaterials.requested
            : link.appearance === 'alternative' ? routeMaterials.alternative : routeMaterials.inactive;
        leg.halo.material = status.unsafe || status.degraded ? routeMaterials.haloWarning : routeMaterials.halo;
      }
      link.packets.count = 0;
      link.trails.count = 0;
      if (!selected || mission) continue;
      const curve = routeCurve(selected.position, GROUND_STATION_VISUAL_ENDPOINT, link.id === 'satellite' ? orbit : undefined);
      for (const [index, leg] of link.legs.entries()) {
        const [from, to] = routeLegRange(link.id, index);
        updateRouteLeg(leg.geometry, curve, from, to);
      }
      if (!packetFlowEnabled(status.forwarding, evidenceCurrent)) { link.owner=''; link.phase=0; continue; }
      if (dt > 0 || link.owner !== selectedId) {
        link.latency=status.metric?.latency; link.loss=status.metric?.packet_loss;
        link.packetThreat=threat.paths.includes(link.id)?threat.profile:'none';
      }
      if (link.owner !== selectedId) { link.owner=selectedId; link.phase=0; }
      link.phase=advancePacketPhase(link.phase,dt,link.latency);
      for (let i=0; i<PACKET_CAPACITY; i++) {
        const sample=packetSample(link.phase,i,link.latency,link.loss);
        if (!sample?.visible) continue;
        const progress = threatPacketProgress(sample.progress,link.packetThreat);
        const p=curvePoint(curve,progress,curveScratch);
        const direction=routeDirection(curve,progress);
        packetPose.position.set(p.x,p.y,p.z);
        packetPose.quaternion.setFromUnitVectors(packetAxis,direction);
        packetPose.updateMatrix();
        const index=link.packets.count++;
        link.packets.setMatrixAt(index,packetPose.matrix);
        packetPose.position.addScaledVector(direction,-6);
        packetPose.updateMatrix();
        link.trails.setMatrixAt(index,packetPose.matrix);
        packetColor.set(status.unsafe ? '#d5a69d' : status.degraded ? '#d8bf9c' : '#c9e5eb');
        link.packets.setColorAt(index,packetColor);
        link.trails.setColorAt(index,packetColor);
      }
      link.trails.count = link.packets.count;
      link.packets.instanceMatrix.needsUpdate = true;
      link.trails.instanceMatrix.needsUpdate = true;
      if(link.packets.instanceColor) link.packets.instanceColor.needsUpdate = true;
      if(link.trails.instanceColor) link.trails.instanceColor.needsUpdate = true;
    }
    canvas.dataset.forwardingRoute = state.droneId === selectedId ? state.installedRoute ?? 'unknown' : 'unknown';
    canvas.dataset.directAppearance = routeLines[0].appearance;
    canvas.dataset.satelliteAppearance = routeLines[1].appearance;
    canvas.dataset.meshAppearance = meshAppearance;
    const packetCount = String(routeLines.reduce((sum,link)=>sum+link.packets.count,0));
    if (canvas.dataset.packetCount !== packetCount) canvas.dataset.packetCount = packetCount;
    canvas.dataset.packetEvidenceCurrent = String(evidenceCurrent);
    canvas.dataset.packetVisual = mission?'authoritative-packet-events':'illustrative-reported-metrics';
    const phase = routeLines.map(link=>link.phase.toFixed(6)).join(',');
    if (canvas.dataset.packetPhase !== phase) canvas.dataset.packetPhase = phase;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return false;
    // Device DPR can change after mount, including when a viewport is overridden.
    refreshPixelRatio();
    const size = renderer.getSize(renderSize); if (size.x !== width || size.y !== height) renderer.setSize(width,height,false);
    renderer.getDrawingBufferSize(renderSize);
    const viewportWidth = String(width), viewportHeight = String(height);
    const bufferWidth = String(renderSize.x), bufferHeight = String(renderSize.y);
    const renderDpr = String(renderer.getPixelRatio());
    if (canvas.dataset.viewportWidth !== viewportWidth) canvas.dataset.viewportWidth = viewportWidth;
    if (canvas.dataset.viewportHeight !== viewportHeight) canvas.dataset.viewportHeight = viewportHeight;
    if (canvas.dataset.bufferWidth !== bufferWidth) canvas.dataset.bufferWidth = bufferWidth;
    if (canvas.dataset.bufferHeight !== bufferHeight) canvas.dataset.bufferHeight = bufferHeight;
    if (canvas.dataset.renderDpr !== renderDpr) canvas.dataset.renderDpr = renderDpr;
    routeMaterialList.forEach(material => material.resolution.copy(renderSize));
    const aspect = width / height; camera.aspect=aspect; camera.updateProjectionMatrix();
    camera.position.copy(cameraEye(rig,aspect)); camera.lookAt(rig.target.x,rig.target.y,rig.target.z);
    sky.position.copy(camera.position);
    const rangeNode=mission?.nodes.find(n=>n.id===selectedId);
    rangeSphere.visible=!!display?.ranges&&!!rangeNode;
    if(rangeNode){rangeSphere.position.set(...rangeNode.position as [number,number,number]);rangeSphere.scale.setScalar(rangeNode.range_m);}
    for(const [index,mesh] of interferenceRegions.entries()){const region=mission?.interference_regions?.[index];mesh.visible=!!region;if(region){mesh.position.set(...region.position as [number,number,number]);mesh.scale.setScalar(region.radius_m);}}
    canvas.dataset.interferenceRegionCount=String(mission?.interference_regions?.length??0);
    swarmLines.visible=!!mission; swarmPackets.visible=!!mission;inspectedRoute.visible=false;
    for(const part of swarmParts){part.visible=!!mission;part.count=0;}
    if(mission){
      const nodes=new Map(mission.nodes.map(n=>[n.id,n]));
      for(const drone of drones){
        packetPose.position.copy(drone.position);packetPose.rotation.set(drone.pitch,-drone.heading+Math.PI/2,drone.roll,'YXZ');packetPose.updateMatrix();bodyMatrix.copy(packetPose.matrix);
        const node=nodes.get(drone.id);
        packetColor.set(selectedIds.includes(drone.id)?'#efd69a':node?.containment==='quarantined'?'#dc8e78':!node?.available?'#647078':node?.active_attacks?.length?'#e4a37b':node?.containment!=='normal'?'#d4ad72':drone.id===selectedId?'#91e1c1':'#d8e7ec');
        swarmParts[0].setColorAt(swarmParts[0].count,packetColor);
        swarmParts[0].setMatrixAt(swarmParts[0].count++,bodyMatrix);
        localPose.position.set(0,1,10);localPose.rotation.set(0,0,0);localPose.scale.setScalar(1);localPose.updateMatrix();localMatrix.multiplyMatrices(bodyMatrix,localPose.matrix);
        swarmParts[2].setMatrixAt(swarmParts[2].count++,localMatrix);
        for(const x of [-UAV_VISUAL.motorOffsetM,UAV_VISUAL.motorOffsetM])for(const z of [-UAV_VISUAL.motorOffsetM,UAV_VISUAL.motorOffsetM]){
          localPose.position.set(x/2,0,z/2);localPose.rotation.set(0,Math.atan2(x,z),0);localPose.updateMatrix();localMatrix.multiplyMatrices(bodyMatrix,localPose.matrix);swarmParts[1].setMatrixAt(swarmParts[1].count++,localMatrix);
          localPose.position.set(x,3,z);localPose.rotation.set(0,0,0);localPose.updateMatrix();localMatrix.multiplyMatrices(bodyMatrix,localPose.matrix);swarmParts[3].setMatrixAt(swarmParts[3].count++,localMatrix);
          localPose.rotation.y=rotorDisplayAngle(seconds,swarmParts[4].count%4,reduced);localPose.updateMatrix();localMatrix.multiplyMatrices(bodyMatrix,localPose.matrix);swarmParts[4].setMatrixAt(swarmParts[4].count++,localMatrix);
        }
        const shadow=groundShadowPosition(drone.position,terrainHeight);localPose.position.copy(shadow);localPose.rotation.set(0,0,0);localPose.scale.setScalar(1+Math.max(0,drone.position.y-shadow.y)/120);localPose.updateMatrix();swarmParts[5].setMatrixAt(swarmParts[5].count++,localPose.matrix);
      }
      packetPose.scale.setScalar(1);packetPose.rotation.set(0,0,0);
      for(const part of swarmParts)part.instanceMatrix.needsUpdate=true;
      if(swarmParts[0].instanceColor)swarmParts[0].instanceColor.needsUpdate=true;
      swarmParts[3].visible=!reduced;
    }
    if(mission){
      const positions=new Map(drones.map(d=>[d.id,d.position]));
      positions.set('base',{x:mission.base_position[0],y:mission.base_position[1],z:mission.base_position[2]});
      const installed=mission.flows.find(f=>f.source===selectedId&&f.destination==='base'&&f.traffic_class==='telemetry');
      const chosen=display?.hops.length?display.hops:installed?.family==='satellite'?[]:installed?.hops||[];
      const routeEdges=new Set(chosen.slice(1).map((b,i)=>[chosen[i],b].sort().join(':')));
      const routePoints=chosen.flatMap(id=>{const p=positions.get(id);return p?[p.x,p.y,p.z]:[];});
      if(routePoints.length>=6&&routePoints.length===chosen.length*3){inspectedRoute.geometry.setPositions(routePoints);inspectedRouteMaterial.color.set(display?.candidate?'#b5a1ea':'#9ce1c1');inspectedRouteMaterial.resolution.set(canvas.clientWidth,canvas.clientHeight);inspectedRoute.visible=true;}
      let vertices=0;
      for(const link of mission.links){
        if(display&&!display.inactive&&!routeEdges.has(link.id)&&link.a!==selectedId&&link.b!==selectedId)continue;
        const a=positions.get(link.a),b=positions.get(link.b); if(!a||!b||vertices>=20000)continue;
        packetColor.set(link.status==='blocked'?'#c67869':routeEdges.has(link.id)?display?.candidate?'#b5a1ea':'#91e1c1':['jammed','degraded'].includes(link.status)?'#d8b780':mission.links.length>1000?'#243b46':'#527380');
        for(const p of [a,b]){swarmPositions.set([p.x,p.y,p.z],vertices*3);swarmColors.set([packetColor.r,packetColor.g,packetColor.b],vertices*3);vertices++;}
      }
      // Satellite is a distinct modeled transport; never highlight a radio
      // edge merely because the logical satellite policy shares its endpoints.
      if(installed?.family==='satellite'){
        const source=positions.get(selectedId),destination=positions.get('base');
        if(source&&destination){
          const curve=routeCurve(source,destination,orbit);packetColor.set('#91e1c1');
          for(let i=0;i<32;i++){
            curvePoint(curve,i/32,virtualStart);curvePoint(curve,(i+1)/32,virtualEnd);
            for(const p of [virtualStart,virtualEnd]){swarmPositions.set([p.x,p.y,p.z],vertices*3);swarmColors.set([packetColor.r,packetColor.g,packetColor.b],vertices*3);vertices++;}
          }
        }
      }
      swarmGeometry.setDrawRange(0,vertices);
      swarmGeometry.getAttribute('position').needsUpdate=true;swarmGeometry.getAttribute('color').needsUpdate=true;
      swarmPackets.count=0;
      for(const visual of display?.packets===false?[]:samplePacketEvents(mission.packet_events??[],seconds,selectedId,200,display?.replay?.packetId)){
        const packet=visual.packet;
        const a=positions.get(visual.from),b=positions.get(visual.to);if(!a||!b)continue;
        const t=reduced?.5:visual.progress;
        if(packet.family==='satellite'&&visual.kind==='flight'){
          const from=t<.5?a:orbit,to=t<.5?orbit:b,f=t<.5?t*2:t*2-1;
          packetPose.position.set(from.x+(to.x-from.x)*f,from.y+(to.y-from.y)*f,from.z+(to.z-from.z)*f);
        }else packetPose.position.set(a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,a.z+(b.z-a.z)*t);
        packetDirection.subVectors(b,a).normalize();
        packetPose.quaternion.setFromUnitVectors(packetAxis,packetDirection.lengthSq()?packetDirection:packetAxis);
        packetPose.updateMatrix();swarmPackets.setMatrixAt(swarmPackets.count,packetPose.matrix);
        packetColor.set(visual.kind==='dropped'?'#e4988a':visual.kind==='retry'?'#e7be79':packet.category==='fl_update'?'#b9a0d7':packet.category==='command'||packet.category==='ack'?'#eed49d':'#8fd8e3');
        swarmPackets.setColorAt(swarmPackets.count++,packetColor);
      }
      canvas.dataset.packetCount=String(swarmPackets.count);
      swarmPackets.instanceMatrix.needsUpdate=true;if(swarmPackets.instanceColor)swarmPackets.instanceColor.needsUpdate=true;
    }
    renderer.render(scene,camera);
    const calls = String(renderer.info.render.calls);
    if (canvas.dataset.drawCalls !== calls) canvas.dataset.drawCalls = calls;
    const geometriesCount = String(renderer.info.memory.geometries);
    const texturesCount = String(renderer.info.memory.textures);
    if (canvas.dataset.geometries !== geometriesCount) canvas.dataset.geometries = geometriesCount;
    if (canvas.dataset.textures !== texturesCount) canvas.dataset.textures = texturesCount;
    return true;
  }
  const projected = new THREE.Vector3();
  function routeScreenPoint(id: 'direct' | 'satellite', progress: number) {
    const link = routeLines.find(route => route.id === id);
    if (!routeDrone || !link || link.appearance === 'hidden') return null;
    const curve = routeCurve(routeDrone.position, GROUND_STATION_VISUAL_ENDPOINT, id === 'satellite' ? satellite.position : undefined);
    const point = curvePoint(curve, progress, curveScratch);
    projected.set(point.x, point.y, point.z).project(camera);
    return { x: (projected.x + 1) * canvas.clientWidth / 2, y: (1 - projected.y) * canvas.clientHeight / 2,
      visible: Math.abs(projected.x) < .96 && Math.abs(projected.y) < .94 && projected.z > -1 && projected.z < 1 };
  }
  function screenPoint(id: string) {
    const object = id === 'reported-position' ? (threats.root.visible && threats.ghost.visible ? threats.ghost : undefined) : id === 'base' ? base : id === 'satellite' ? satellite : fleet.get(id)?.group;
    if (!object) return null;
    projected.copy(id === 'base' ? GROUND_STATION_VISUAL_ENDPOINT : object.position).project(camera);
    return { x: (projected.x + 1) * canvas.clientWidth / 2, y: (1 - projected.y) * canvas.clientHeight / 2,
      visible: Math.abs(projected.x) < .96 && Math.abs(projected.y) < .94 && projected.z > -1 && projected.z < 1 };
  }
  return { draw, pick, screenPoint, routeScreenPoint, setQuality, dispose() { swarmParts.forEach(p=>p.dispose()); swarmPackets.dispose(); terrainDisposed = true; terrainTextures.forEach(texture => texture.dispose()); scrubInstances.dispose(); rockInstances.dispose(); threats.dispose(); for (const entity of fleet.values()) { entity.stem.geometry.dispose(); entity.blades.dispose(); entity.rotors.dispose(); } sun.shadow.dispose(); shadowTexture.dispose(); geometries.forEach(g=>g.dispose()); materials.forEach(m=>m.dispose()); routeLines.forEach(link=>{ link.packets.dispose(); link.trails.dispose(); }); renderer.dispose(); } };
}
