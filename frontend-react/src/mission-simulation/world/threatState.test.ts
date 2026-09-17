/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import { threatVisual, threatOpacity, threatPacketProgress } from './threatState.ts';
import { networkRouteState } from './networkState.ts';
import type { AttackProfile } from '../types.ts';
const base=adaptMissionSimulationState(null,{droneId:'a',route:'direct',threatLevel:'LOW',reward:0,step:1,noSafeRoute:false,availablePaths:['direct']});
const profiles: AttackProfile[]=['none','spot','sweep','barrage','smart','reactive','adaptive','fhss','spoofing','gps_spoofing','replay','dos'];
for(const profile of profiles) test(`threat profile ${profile} maps without mutating runtime state`,()=>{
  const state={...base,activeAttack:profile,jammedPaths:['direct' as const],gps:{latitude:45,longitude:-122,drift_m:120}};
  const before=JSON.stringify(state);const visual=threatVisual(state,'a');
  assert.equal(visual.profile,profile);assert.ok(visual.label.length>0);assert.equal(JSON.stringify(state),before);
  assert.equal(threatVisual(state,'b').profile,'none');
  assert.equal(networkRouteState({...state,installedRoute:'hold'},'direct','a').forwarding,false);
});
test('none creates no effects and clearing decays only existing visuals',()=>{
  assert.equal(threatOpacity(0,false,.1),0);assert.equal(threatOpacity(1,false,0),1);
  let opacity=1;for(let i=0;i<10;i++)opacity=threatOpacity(opacity,false,.1);assert.equal(opacity,0);
  assert.equal(threatOpacity(0,true,.1),1);
});
test('GPS drift must be reported, finite and positive; replay never claims a stale location',()=>{
  for(const drift_m of [NaN,Infinity,-1,0]) assert.equal(threatVisual({...base,activeAttack:'gps_spoofing',gps:{latitude:0,longitude:0,drift_m}},'a').drift,null);
  assert.equal(threatVisual({...base,activeAttack:'gps_spoofing'},'a').drift,null);
  assert.match(threatVisual({...base,activeAttack:'replay'},'a').label,/not established/);
});
test('DoS bunching stays bounded without increasing packet count; other profiles preserve progress',()=>{
  for(let i=0;i<100;i++){const p=i/100;assert.ok(threatPacketProgress(p,'dos')>=0&&threatPacketProgress(p,'dos')<=1);assert.equal(threatPacketProgress(p,'none'),p);}
});

import * as THREE from 'three';
import { createThreatLayer } from './ThreatLayer.ts';
test('every profile builds finite Three geometry, clears, changes owner and disposes',()=>{
  const scene=new THREE.Scene();const layer=createThreatLayer(scene);
  const position=Object.freeze({x:30,y:80,z:20}),satellite={x:200,y:280,z:-180};
  for(const profile of profiles){
    const visual=threatVisual({...base,activeAttack:profile,jammedPaths:['direct','satellite','mesh'],gps:{latitude:45,longitude:-122,drift_m:120}},'a');
    layer.update(visual,'a',position,satellite,10,.016,false);
    scene.traverse(object=>{const geometry=(object as THREE.Mesh).geometry;if(geometry){const p=geometry.getAttribute('position');assert.ok(Array.from(p.array).every(Number.isFinite));}});
    const none=threatVisual(base,'a');
    for(let i=0;i<10;i++) layer.update(none,'a',position,satellite,10+i*.1,.1,false);
    assert.equal(layer.root.visible,false);
  }
  const gps=threatVisual({...base,activeAttack:'gps_spoofing',gps:{latitude:45,longitude:-122,drift_m:120}},'a');
  layer.update(gps,'a',position,satellite,10,.1,false);assert.equal(layer.ghost.visible,true);assert.equal(position.x,30);
  layer.update(threatVisual(base,'b'),'b',position,satellite,10,0,false);assert.equal(layer.root.visible,false);
  layer.dispose();assert.equal(scene.children.length,0);
});
