import * as THREE from 'three';
import { threatOpacity } from './threatState.ts';
import type { ThreatVisual } from './threatState.ts';
import type { Vector3 } from './dynamics.ts';
import { curvePoint, routeCurve } from './networkGeometry.ts';
import { GROUND_STATION } from './terrain.ts';

/** Schematic effects only. This layer never writes motion or network state. */
export function createThreatLayer(scene: THREE.Scene) {
  const root = new THREE.Group(); scene.add(root);
  const material = new THREE.MeshBasicMaterial({ color: '#b39577', transparent: true, opacity: .1, depthWrite: false, side: THREE.DoubleSide });
  const wireMaterial = new THREE.LineBasicMaterial({ color: '#bba38e', transparent: true, opacity: .6 });
  const volumeGeometry = new THREE.SphereGeometry(1, 20, 12);
  const volume = new THREE.Mesh(volumeGeometry, material); root.add(volume);
  const sectorGeometry = new THREE.CircleGeometry(90, 24, 0, Math.PI / 3); sectorGeometry.rotateX(-Math.PI / 2);
  const sector = new THREE.Mesh(sectorGeometry, material); root.add(sector);
  const ghostGeometry = new THREE.OctahedronGeometry(9);
  const ghost = new THREE.Mesh(ghostGeometry, material); root.add(ghost);
  const ghostEdges = new THREE.EdgesGeometry(ghostGeometry); ghost.add(new THREE.LineSegments(ghostEdges,wireMaterial));
  const connectorGeometry = new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
  const connector = new THREE.Line(connectorGeometry, wireMaterial); connector.frustumCulled = false; root.add(connector);
  const marksGeometry = new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(3*8*12), 3));
  const marks = new THREE.LineSegments(marksGeometry, wireMaterial); marks.frustumCulled = false; root.add(marks);
  let opacity = 0, previousOwner = '', previous: ThreatVisual | null = null;
  let anchor: Vector3 = {x:0,y:0,z:0};
  root.userData.asset = 'jammer';
  return {
    root, ghost,
    update(visual: ThreatVisual, owner: string, position: Vector3 | undefined, satellite: Vector3, seconds: number, dt: number, reduced: boolean) {
      if (owner !== previousOwner) { opacity=0;previous=null;previousOwner=owner; }
      const active = visual.profile !== 'none' && !!position;
      opacity = reduced && !active ? 0 : threatOpacity(opacity,active,dt);
      if (active) { previous=visual;anchor={...position!}; }
      root.visible=opacity>0 && previous!==null;
      if (!root.visible || !previous) return;
      const effect=previous;
      material.opacity=opacity*(effect.profile==='gps_spoofing' ? .28 : effect.profile==='sweep' ? .18 : .12);
      wireMaterial.opacity=opacity*.55;
      volume.visible=['spot','barrage'].includes(effect.profile);
      sector.visible=effect.profile==='sweep';
      ghost.visible=effect.profile==='gps_spoofing' && effect.drift!==null;
      connector.visible=ghost.visible;
      marks.visible=!volume.visible&&!sector.visible&&!ghost.visible && effect.profile!=='gps_spoofing';
      volume.position.copy(anchor); volume.scale.setScalar(effect.profile==='barrage'?190:55); volume.scale.y*=.5;
      sector.position.copy(anchor); sector.rotation.y=reduced?0:seconds*.28;
      if (ghost.visible) {
        // No trusted georeference: magnitude only, bounded and explicitly schematic.
        ghost.position.set(anchor.x+Math.min(effect.drift!,120),anchor.y,anchor.z);
        const p=connectorGeometry.getAttribute('position');p.setXYZ(0,anchor.x,anchor.y,anchor.z);p.setXYZ(1,ghost.position.x,ghost.position.y,ghost.position.z);p.needsUpdate=true;
      }
      let count=0; const p=marksGeometry.getAttribute('position');
      for (const path of effect.paths) {
        // Mesh has no identified forwarding hop. Its annotation remains HTML only.
        if(path==='mesh') continue;
        const curve=routeCurve(anchor,GROUND_STATION,path==='satellite'?satellite:undefined);
        for(let i=0;i<8;i++) {
          const t=(i+.5)/8; const point=curvePoint(curve,t);
          const hopping=effect.profile==='fhss'&&!reduced ? Math.floor(seconds*2)%2 : 0;
          const size=effect.profile==='dos'?5:3;
          p.setXYZ(count++,point.x-size,point.y+2+hopping*3,point.z);
          p.setXYZ(count++,point.x+size,point.y+2+hopping*3,point.z);
          if(effect.profile==='replay') {
            p.setXYZ(count++,point.x-size,point.y+7,point.z);
            p.setXYZ(count++,point.x+size,point.y+7,point.z);
          }
        }
      }
      marksGeometry.setDrawRange(0,count);p.needsUpdate=true;
    },
    dispose() { scene.remove(root);volumeGeometry.dispose();sectorGeometry.dispose();ghostGeometry.dispose();ghostEdges.dispose();connectorGeometry.dispose();marksGeometry.dispose();material.dispose();wireMaterial.dispose(); },
  };
}
