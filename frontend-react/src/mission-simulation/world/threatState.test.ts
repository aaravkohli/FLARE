/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { adaptMissionSimulationState } from '../adapter.ts';
import { threatVisual, threatOpacity, threatPacketProgress, threatQueuePath } from './threatState.ts';
import { networkRouteState } from './networkState.ts';
import { createThreatLayer } from './ThreatLayer.ts';
import type { AttackProfile } from '../types.ts';

const base=adaptMissionSimulationState(null,{droneId:'a',route:'direct',threatLevel:'LOW',reward:0,step:1,noSafeRoute:false,availablePaths:['direct']});
const profiles: AttackProfile[]=['none','spot','sweep','barrage','smart','reactive','adaptive','fhss','spoofing','gps_spoofing','replay','dos'];
const ground=(x:number,z:number)=>.015*x-.01*z+2*Math.sin(x/50);
const selected=Object.freeze({x:30,y:80,z:20});
const satellite={x:200,y:280,z:-180};
const visual=(profile:AttackProfile,drift=120)=>threatVisual({
  ...base,activeAttack:profile,jammedPaths:['direct','satellite','mesh'],
  gps:{latitude:45,longitude:-122,drift_m:drift},
},'a');

for(const profile of profiles) test(`threat profile ${profile} maps without mutating runtime state`,()=>{
  const state={...base,activeAttack:profile,jammedPaths:['direct' as const],gps:{latitude:45,longitude:-122,drift_m:120}};
  const before=JSON.stringify(state), view=threatVisual(state,'a');
  assert.equal(view.profile,profile);assert.ok(view.label.length>0);assert.equal(JSON.stringify(state),before);
  assert.equal(view.sceneNote.length>0,profile!=='none');
  assert.equal(threatVisual(state,'b').profile,'none');
  assert.equal(networkRouteState({...state,installedRoute:'hold'},'direct','a').forwarding,false);
});
test('none creates no effects and clearing decays only existing visuals',()=>{
  assert.equal(threatOpacity(0,false,.1),0);assert.equal(threatOpacity(1,false,0),1);
  let opacity=1;for(let i=0;i<10;i++)opacity=threatOpacity(opacity,false,.1);assert.equal(opacity,0);
  assert.equal(threatOpacity(0,true,.1),1);
});
test('GPS drift must be reported and positive, with an explicitly untrusted schematic marker',()=>{
  for(const drift_m of [NaN,Infinity,-1,0]) assert.equal(visual('gps_spoofing',drift_m).drift,null);
  assert.equal(threatVisual({...base,activeAttack:'gps_spoofing'},'a').drift,null);
  assert.match(visual('gps_spoofing').label,/no trusted position reference/);
  assert.match(visual('gps_spoofing').sceneNote,/DIRECTION SCHEMATIC · REFERENCE UNTRUSTED/);
  assert.match(visual('replay').label,/not established/);
});
test('DoS bunching stays bounded and queue glyphs require a targeted forwarding path',()=>{
  for(let i=0;i<100;i++){const p=i/100;assert.ok(threatPacketProgress(p,'dos')>=0&&threatPacketProgress(p,'dos')<=1);assert.equal(threatPacketProgress(p,'none'),p);}
  const dos=visual('dos');
  assert.equal(threatQueuePath(dos,'direct'),'direct');
  assert.equal(threatQueuePath(dos,null),null);
  assert.equal(threatQueuePath({...dos,paths:['satellite']},'direct'),null);
  assert.equal(threatQueuePath(visual('spot'),'direct'),null);
});
test('spot and barrage affected-area fields follow terrain under the selected UAV projection',()=>{
  const scene=new THREE.Scene(), layer=createThreatLayer(scene,ground);
  for(const [profile,radius] of [['spot',55],['barrage',190]] as const){
    layer.update(visual(profile),'a',selected,satellite,10,.016,false);
    assert.equal(layer.field.visible,true);assert.equal(layer.sector.visible,false);
    assert.equal(layer.field.position.x,selected.x);assert.equal(layer.field.position.z,selected.z);
    const vertices=layer.field.geometry.getAttribute('position');
    let outer=0;
    for(let i=0;i<vertices.count;i++){
      const x=vertices.getX(i),z=vertices.getZ(i),y=vertices.getY(i);
      outer=Math.max(outer,Math.hypot(x,z));
      assert.ok(Number.isFinite(y));
      assert.ok(Math.abs(y-ground(selected.x+x,selected.z+z)-.8)<1e-4);
    }
    assert.ok(Math.abs(outer-radius)<1e-4);
  }
  layer.dispose();assert.equal(scene.children.length,0);
});
test('affected-area fields retain a visible mid-radius tint while feathering to clear terrain',()=>{
  const scene=new THREE.Scene(),layer=createThreatLayer(scene,ground);
  for(const profile of ['spot','barrage','sweep'] as const){
    layer.update(visual(profile),'a',selected,satellite,10,.016,false);
    const mesh=profile==='sweep'?layer.sector:layer.field;
    const radius=profile==='spot'?55:profile==='barrage'?190:125;
    const positions=mesh.geometry.getAttribute('position');
    const fades=mesh.geometry.getAttribute('fade');
    const strength=(mesh.material as THREE.ShaderMaterial).uniforms.strength.value as number;
    const middle:number[]=[],outer:number[]=[];
    for(let i=0;i<positions.count;i++){
      const r=Math.hypot(positions.getX(i),positions.getZ(i))/radius;
      if(r>.48&&r<.55&&fades.getX(i)>0)middle.push(strength*fades.getX(i));
      if(r>.995)outer.push(strength*fades.getX(i));
    }
    assert.ok(middle.some(alpha=>alpha>.12&&alpha<.35),`${profile} mid-radius tint remains visible and translucent`);
    assert.ok(outer.every(alpha=>alpha===0),`${profile} outer edge is transparent`);
  }
  layer.dispose();
});
test('sweep sector remains grounded and freezes its schematic direction in reduced motion',()=>{
  const scene=new THREE.Scene(),layer=createThreatLayer(scene,ground);
  const sweep=visual('sweep');
  layer.update(sweep,'a',selected,satellite,10,.016,true);
  assert.equal(layer.sector.visible,true);assert.equal(layer.field.visible,false);
  const first=Array.from(layer.sector.geometry.getAttribute('position').array);
  layer.update(sweep,'a',selected,satellite,100,.016,true);
  assert.deepEqual(Array.from(layer.sector.geometry.getAttribute('position').array),first);
  layer.update(sweep,'a',selected,satellite,120,.016,false);
  assert.notDeepEqual(Array.from(layer.sector.geometry.getAttribute('position').array),first);
  const p=layer.sector.geometry.getAttribute('position');
  for(let i=0;i<p.count;i++) assert.ok(Math.abs(p.getY(i)-ground(selected.x+p.getX(i),selected.z+p.getZ(i))-.8)<1e-4);
  layer.dispose();
});
test('GPS diamond and connector leave physical aircraft untouched and never derive direction from drift',()=>{
  const scene=new THREE.Scene(),layer=createThreatLayer(scene,ground);
  layer.update(visual('gps_spoofing',120),'a',selected,satellite,10,.016,false);
  assert.equal(layer.ghost.visible,true);assert.equal(layer.connector.visible,true);
  const displayed=layer.ghost.position.clone();
  layer.update(visual('gps_spoofing',25),'a',selected,satellite,10,.016,false);
  assert.ok(layer.ghost.position.equals(displayed));
  assert.deepEqual(selected,{x:30,y:80,z:20});
  const connector=layer.connector.geometry.getAttribute('position');
  assert.deepEqual([connector.getX(0),connector.getY(0),connector.getZ(0)],[selected.x,selected.y,selected.z]);
  assert.deepEqual([connector.getX(1),connector.getY(1),connector.getZ(1)],[displayed.x,displayed.y,displayed.z]);
  layer.dispose();
});
test('DoS uses only a bounded queue on a valid forwarding path, with no RF volume',()=>{
  const scene=new THREE.Scene(),layer=createThreatLayer(scene,ground);
  const dos=visual('dos');
  layer.update(dos,'a',selected,satellite,10,.016,false,'direct');
  assert.equal(layer.field.visible,false);assert.equal(layer.sector.visible,false);
  assert.equal(layer.marks.visible,true);assert.equal(layer.marks.geometry.drawRange.count,12);
  layer.update(dos,'a',selected,satellite,10,.016,false,null);
  assert.equal(layer.marks.visible,false);assert.equal(layer.marks.geometry.drawRange.count,0);
  layer.dispose();
});
test('every profile stays finite, clears on owner change and releases its scene resources',()=>{
  const scene=new THREE.Scene(),layer=createThreatLayer(scene,ground);
  for(const profile of profiles){
    layer.update(visual(profile),'a',selected,satellite,10,.016,false,'direct');
    scene.traverse(object=>{const geometry=(object as THREE.Mesh).geometry;
      if(geometry){const p=geometry.getAttribute('position');assert.ok(Array.from(p.array).every(Number.isFinite));}});
    for(let i=0;i<10;i++) layer.update(visual('none'),'a',selected,satellite,10+i*.1,.1,false,'direct');
    assert.equal(layer.root.visible,false);
  }
  layer.update(visual('gps_spoofing'),'a',selected,satellite,10,.1,false);
  assert.equal(layer.ghost.visible,true);
  layer.update(visual('none'),'b',selected,satellite,10,0,false);
  assert.equal(layer.root.visible,false);
  layer.dispose();assert.equal(scene.children.length,0);
});
