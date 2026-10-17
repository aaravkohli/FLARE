import { createThreatLayer } from './ThreatLayer';
import { threatVisual, threatPacketProgress } from './threatState';
import { routeCurve, curvePoint } from './networkGeometry';
import { networkRouteState } from './networkState';
import { PACKET_CAPACITY, packetSample, advancePacketPhase } from './PacketStream';
import * as THREE from 'three';
import { terrainHeight, GROUND_STATION } from './terrain';
import { cameraEye, CAMERA_FOV } from './cameraRig';
import type { CameraRig } from './cameraRig';
import type { DroneMotion, Vector3 } from './dynamics';
import type { AttackProfile, MissionSimulationState } from '../types';

export function createMissionScene(canvas: HTMLCanvasElement) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#0c1118');
  scene.fog = new THREE.Fog('#0c1118', 1000, 3000);
  const camera = new THREE.PerspectiveCamera(CAMERA_FOV, 1.5, 2, 4500);
  scene.add(new THREE.HemisphereLight('#dce5ed', '#28312e', 2));
  const sun = new THREE.DirectionalLight('#e0e6ea', 2); sun.position.set(-200, 600, 300); scene.add(sun);
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const geo = <T extends THREE.BufferGeometry>(g: T) => { geometries.push(g); return g; };
  const mat = <T extends THREE.Material>(m: T) => { materials.push(m); return m; };
  const terrain = geo(new THREE.PlaneGeometry(14000, 14000, 120, 120)); terrain.rotateX(-Math.PI / 2);
  const vertices = terrain.getAttribute('position');
  for (let i = 0; i < vertices.count; i++) vertices.setY(i, terrainHeight(vertices.getX(i), vertices.getZ(i)));
  terrain.computeVertexNormals();
  scene.add(new THREE.Mesh(terrain, mat(new THREE.MeshStandardMaterial({ color: '#192323', roughness: 1, flatShading: false }))));
  const grid: THREE.Vector3[] = [], gridColors: number[] = [];
  const gridColor = new THREE.Color();
  function gridPoint(x:number,z:number) {
    grid.push(new THREE.Vector3(x,terrainHeight(x,z)+.3,z));
    const strength=Math.max(0,1-Math.hypot(x,z)/1500);
    gridColor.set('#263536').lerp(new THREE.Color('#536460'),strength*.5);
    gridColors.push(gridColor.r,gridColor.g,gridColor.b);
  }
  for(let x=-1440;x<=1440;x+=80) for(let z=-1440;z<1440;z+=40){gridPoint(x,z);gridPoint(x,z+40);}
  for(let z=-1440;z<=1440;z+=80) for(let x=-1440;x<1440;x+=40){gridPoint(x,z);gridPoint(x+40,z);}
  const gridGeometry=geo(new THREE.BufferGeometry().setFromPoints(grid));
  gridGeometry.setAttribute('color',new THREE.Float32BufferAttribute(gridColors,3));
  scene.add(new THREE.LineSegments(gridGeometry,mat(new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.2}))));
  const boundary: THREE.Vector3[]=[];
  for(const x of [-360,360]) for(const z of [-240,240]) {
    const insetX=x-Math.sign(x)*35,insetZ=z-Math.sign(z)*35;
    boundary.push(new THREE.Vector3(insetX,terrainHeight(insetX,z)+.6,z),new THREE.Vector3(x,terrainHeight(x,z)+.6,z),
      new THREE.Vector3(x,terrainHeight(x,z)+.6,z),new THREE.Vector3(x,terrainHeight(x,insetZ)+.6,insetZ));
  }
  scene.add(new THREE.LineSegments(geo(new THREE.BufferGeometry().setFromPoints(boundary)),mat(new THREE.LineBasicMaterial({color:'#89958d',transparent:true,opacity:.3}))));
  const bodyGeo = geo(new THREE.BoxGeometry(12, 5, 19));
  const armGeo = geo(new THREE.BoxGeometry(2, 2, 20));
  const rotorGeo = geo(new THREE.CylinderGeometry(6, 6, .15, 24));
  const bladeGeo=geo(new THREE.BoxGeometry(12,.4,1));
  const noseGeo=geo(new THREE.BoxGeometry(5,2,3));
  const blurMat=mat(new THREE.MeshBasicMaterial({color:'#a6b6bf',transparent:true,opacity:.13,depthWrite:false}));
  const noseMat=mat(new THREE.MeshStandardMaterial({color:'#c8d3d9',roughness:.8}));
  const aircraftMat = mat(new THREE.MeshStandardMaterial({ color: '#b2bdc5', roughness: .8 }));
  const darkMat = mat(new THREE.MeshStandardMaterial({ color: '#4d616f', roughness: .9 }));
  const selectMat = mat(new THREE.LineBasicMaterial({ color: '#a6c8df' }));
  const ringGeo = geo(new THREE.BufferGeometry().setFromPoints(Array.from({length: 33}, (_, i) => new THREE.Vector3(Math.cos(i * Math.PI / 16) * 18, 0, Math.sin(i * Math.PI / 16) * 18))));
  const stemMat = mat(new THREE.LineBasicMaterial({ color: '#7d9197', transparent: true, opacity: .3 }));
  const shadowPixels=new Uint8Array(64*64*4);
  for(let y=0;y<64;y++)for(let x=0;x<64;x++){
    const r=Math.hypot((x-31.5)/31.5,(y-31.5)/31.5),i=(y*64+x)*4;
    shadowPixels[i+3]=Math.round(Math.max(0,1-r)**2*90);
  }
  const shadowTexture=new THREE.DataTexture(shadowPixels,64,64);shadowTexture.needsUpdate=true;
  const shadowGeo = geo(new THREE.CircleGeometry(10, 16)); shadowGeo.rotateX(-Math.PI / 2);
  const shadowMat = mat(new THREE.MeshBasicMaterial({ color: '#03080b', transparent: true, map: shadowTexture, opacity: .55, depthWrite: false }));
  const fleet = new Map<string, { group: THREE.Group; body: THREE.Group; ring: THREE.Line; stem: THREE.Line; shadow: THREE.Mesh; blades: THREE.InstancedMesh; rotors: THREE.InstancedMesh }>();
  const base = new THREE.Group(); base.position.copy(GROUND_STATION);
  const building = new THREE.Mesh(geo(new THREE.BoxGeometry(28, 10, 20)), aircraftMat); base.add(building);
  const mast = new THREE.Mesh(geo(new THREE.CylinderGeometry(1, 2, 32, 8)), darkMat); mast.position.y = 17; base.add(mast);
  const dish = new THREE.Mesh(geo(new THREE.SphereGeometry(6, 12, 8)), aircraftMat); dish.scale.set(1,.3,1); dish.position.y = 33; dish.rotation.z = -.4; base.add(dish);
  const pad=new THREE.Mesh(geo(new THREE.CylinderGeometry(28,28,1,32)),mat(new THREE.MeshStandardMaterial({color:'#293638',roughness:1})));
  pad.position.y=-4.5;base.add(pad);
  const roof=new THREE.Mesh(geo(new THREE.BoxGeometry(32,2,24)),darkMat);roof.position.y=6;base.add(roof);
  const windowStrip=new THREE.Mesh(geo(new THREE.BoxGeometry(22,2,1)),mat(new THREE.MeshBasicMaterial({color:'#667b83'})));
  windowStrip.position.set(0,2,10.5);base.add(windowStrip);
  building.position.x=-8;mast.position.x=13;dish.position.x=13;
  base.userData.asset = 'base'; scene.add(base);
  const satellite = new THREE.Group();
  satellite.add(new THREE.Mesh(geo(new THREE.BoxGeometry(12, 10, 10)), aircraftMat));
  for (const x of [-20,20]) { const panel = new THREE.Mesh(geo(new THREE.BoxGeometry(24, 1, 18)), darkMat); panel.position.x = x; satellite.add(panel); }
  satellite.scale.setScalar(.65);
  satellite.userData.asset = 'satellite'; scene.add(satellite);
  const packetGeometry = geo(new THREE.SphereGeometry(2.2, 8, 6));
  const packetMaterial = mat(new THREE.MeshBasicMaterial({ color: '#b4cddd' }));
  const routeLines = (['direct','satellite'] as const).map(id => {
    const geometry = geo(new THREE.BufferGeometry());
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(65 * 3), 3));
    const material = mat(new THREE.LineBasicMaterial({ color: '#4e6476', transparent: true, opacity: .1 }));
    const line = new THREE.Line(geometry, material); line.frustumCulled = false; scene.add(line);
    const packets = new THREE.InstancedMesh(packetGeometry, packetMaterial, PACKET_CAPACITY);
    packets.frustumCulled = false; packets.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(packets);
    return { id, geometry, material, line, packets, phase: 0, owner: '', packetThreat: 'none' as AttackProfile, latency: undefined as number | undefined, loss: undefined as number | undefined };
  });
  const neighborhoodGeometry = geo(new THREE.BufferGeometry());
  const neighborhoodMaterial = mat(new THREE.LineDashedMaterial({ color: '#6a7c85', transparent: true, opacity: .1, dashSize: 3, gapSize: 9 }));
  const neighborhood = new THREE.LineSegments(neighborhoodGeometry, neighborhoodMaterial);
  neighborhood.frustumCulled = false; scene.add(neighborhood);
  const packetObject = new THREE.Object3D();
  const packetColor = new THREE.Color();
  let previousNetworkSeconds = 0;
  const threats = createThreatLayer(scene);
  const raycaster = new THREE.Raycaster();
  const renderSize = new THREE.Vector2();
  const curveScratch = {x:0,y:0,z:0};
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
  function draw(drones: readonly DroneMotion[], selectedId: string, orbit: Vector3, rig: CameraRig, state: MissionSimulationState, seconds: number, reduced = false) {
    const ids = new Set(drones.map(d => d.id));
    for (const [id, entity] of fleet) if (!ids.has(id)) { scene.remove(entity.group, entity.stem, entity.shadow); entity.stem.geometry.dispose(); entity.blades.dispose(); entity.rotors.dispose(); fleet.delete(id); }
    for (const drone of drones) {
      let entity = fleet.get(drone.id);
      if (!entity) {
        const group = new THREE.Group(); group.userData.asset = drone.id;
        const body = new THREE.Group(); group.add(body); body.add(new THREE.Mesh(bodyGeo, aircraftMat));
        const rotors=new THREE.InstancedMesh(rotorGeo,blurMat,4);
        const blades=new THREE.InstancedMesh(bladeGeo,aircraftMat,4);
        body.add(rotors,blades);
        let rotorIndex=0;
        for(const x of [-13,13])for(const z of [-13,13]){
          const arm=new THREE.Mesh(armGeo,darkMat);arm.position.set(x/2,0,z/2);arm.rotation.y=Math.atan2(x,z);body.add(arm);
          packetObject.position.set(x,3,z);packetObject.rotation.set(0,0,0);packetObject.updateMatrix();
          rotors.setMatrixAt(rotorIndex,packetObject.matrix);blades.setMatrixAt(rotorIndex++,packetObject.matrix);
        }
        const nose=new THREE.Mesh(noseGeo,noseMat);nose.position.set(0,1,10);body.add(nose);
        const ring = new THREE.Line(ringGeo, selectMat); group.add(ring);
        const stem = new THREE.Line(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(6),3)), stemMat); stem.frustumCulled = false;
        const shadow = new THREE.Mesh(shadowGeo, shadowMat); scene.add(group, stem, shadow);
        entity = { group, body, ring, stem, shadow, blades, rotors }; fleet.set(drone.id, entity);
      }
      entity.group.position.copy(drone.position); entity.body.rotation.set(drone.pitch, -drone.heading + Math.PI / 2, drone.roll, 'YXZ');
      entity.ring.visible = drone.id === selectedId;
      entity.stem.visible = drone.id === selectedId;
      let rotorIndex=0;
      for(const x of [-13,13])for(const z of [-13,13]){
        packetObject.position.set(x,3,z);packetObject.rotation.set(0,reduced?0:seconds*35*(rotorIndex%2?1:-1),0);packetObject.updateMatrix();
        entity.blades.setMatrixAt(rotorIndex++,packetObject.matrix);
      }
      entity.blades.instanceMatrix.needsUpdate=true;
      packetObject.rotation.set(0,0,0);
      const ground = terrainHeight(drone.position.x, drone.position.z) + .6;
      const shadowX=drone.position.x+drone.position.y*.22, shadowZ=drone.position.z+drone.position.y*.14;
      entity.shadow.position.set(shadowX,terrainHeight(shadowX,shadowZ)+.6,shadowZ);
      entity.shadow.scale.setScalar(1+drone.position.y/90);
      const stem = entity.stem.geometry.getAttribute('position'); stem.setXYZ(0, drone.position.x, ground, drone.position.z); stem.setXYZ(1, drone.position.x, drone.position.y, drone.position.z); stem.needsUpdate = true;
    }
    satellite.position.copy(orbit);
    const selected = drones.find(d => d.id === selectedId);


    const dt = Math.max(0, Math.min(.1, seconds - previousNetworkSeconds));
    previousNetworkSeconds = seconds;
    const threat=threatVisual(state,selectedId);
    threats.update(threat,selectedId,selected?.position,orbit,seconds,dt,reduced);
    const meshState = networkRouteState(state, 'mesh', selectedId);
    const neighbors = drones.filter(d => d.id !== selectedId);
    // All supplied peers are schematic context, not inferred reachability or forwarding hops.
    neighborhood.visible = !!selected && neighbors.length > 0 && state.routes.some(r => r.id === 'mesh' && r.available);
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
      neighborhoodMaterial.opacity = meshState.installed ? .22 : .015;
      neighborhoodMaterial.color.set(meshState.hold ? '#687078' : meshState.unsafe ? '#c38880' : meshState.degraded ? '#ba9b73' : '#758995');
    }
    for (const link of routeLines) {
      const status = networkRouteState(state, link.id, selectedId);
      link.line.visible = !!selected;
      link.material.color.set(status.hold ? '#687078' : status.unsafe ? '#c38880' : status.degraded ? '#ba9b73' : status.installed ? '#95b9cc' : '#536774');
      // Only appearance eases. Forwarding switches in this same frame, with no simulated SDN delay.
      link.material.opacity += (status.opacity-link.material.opacity) * (dt ? 1-Math.exp(-12*dt) : 1);
      link.packets.count = 0;
      if (!selected) continue;
      const curve = routeCurve(selected.position, GROUND_STATION, link.id === 'satellite' ? orbit : undefined);
      const positions = link.geometry.getAttribute('position');
      for (let i=0; i<65; i++) { const p=curvePoint(curve,i/64,curveScratch); positions.setXYZ(i,p.x,p.y,p.z); }
      positions.needsUpdate = true;
      if (!status.forwarding) { link.owner=''; link.phase=0; continue; }
      if (dt > 0 || link.owner !== selectedId) {
        link.latency=status.metric?.latency; link.loss=status.metric?.packet_loss;
        link.packetThreat=threat.paths.includes(link.id)?threat.profile:'none';
      }
      if (link.owner !== selectedId) { link.owner=selectedId; link.phase=0; }
      link.phase=advancePacketPhase(link.phase,dt,link.latency);
      for (let i=0; i<PACKET_CAPACITY; i++) {
        const sample=packetSample(link.phase,i,link.latency,link.loss);
        if (!sample?.visible) continue;
        const p=curvePoint(curve,threatPacketProgress(sample.progress,link.packetThreat),curveScratch); packetObject.position.copy(p); packetObject.updateMatrix();
        const index=link.packets.count++;
        link.packets.setMatrixAt(index,packetObject.matrix);
        link.packets.setColorAt(index,packetColor.set(sample.failed ? '#bd916f' : '#b4cddd'));
      }
      link.packets.instanceMatrix.needsUpdate = true;
      if(link.packets.instanceColor) link.packets.instanceColor.needsUpdate = true;
    }
    canvas.dataset.forwardingRoute = state.droneId === selectedId ? state.installedRoute ?? 'unknown' : 'unknown';
    canvas.dataset.packetCount = String(routeLines.reduce((sum,link)=>sum+link.packets.count,0));
    const phase = routeLines.map(link=>link.phase.toFixed(6)).join(',');
    if (canvas.dataset.packetPhase !== phase) canvas.dataset.packetPhase = phase;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    const size = renderer.getSize(renderSize); if (size.x !== width || size.y !== height) renderer.setSize(width,height,false);
    const aspect = width / height; camera.aspect=aspect; camera.updateProjectionMatrix();
    camera.position.copy(cameraEye(rig,aspect)); camera.lookAt(rig.target.x,rig.target.y,rig.target.z);
    renderer.render(scene,camera);
    const calls = String(renderer.info.render.calls);
    if (canvas.dataset.drawCalls !== calls) canvas.dataset.drawCalls = calls;
    canvas.dataset.geometries = String(renderer.info.memory.geometries);
    canvas.dataset.textures = String(renderer.info.memory.textures);
  }
  const projected = new THREE.Vector3();
  function screenPoint(id: string) {
    const object = id === 'reported-position' ? (threats.root.visible && threats.ghost.visible ? threats.ghost : undefined) : id === 'base' ? base : id === 'satellite' ? satellite : fleet.get(id)?.group;
    if (!object) return null;
    projected.copy(object.position).project(camera);
    return { x: (projected.x + 1) * canvas.clientWidth / 2, y: (1 - projected.y) * canvas.clientHeight / 2,
      visible: Math.abs(projected.x) < .96 && Math.abs(projected.y) < .94 && projected.z > -1 && projected.z < 1 };
  }
  return { draw, pick, screenPoint, dispose() { threats.dispose(); for (const entity of fleet.values()) { entity.stem.geometry.dispose(); entity.blades.dispose(); entity.rotors.dispose(); } shadowTexture.dispose(); geometries.forEach(g=>g.dispose()); materials.forEach(m=>m.dispose()); routeLines.forEach(link=>link.packets.dispose()); renderer.dispose(); } };
}
