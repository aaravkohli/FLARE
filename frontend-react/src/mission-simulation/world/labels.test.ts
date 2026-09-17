/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactDroneId, placeLabel } from './labels.ts';
import { cameraEye, cameraPreset, CAMERA_FOV, clampCamera } from './cameraRig.ts';
import * as THREE from 'three';
test('labels use compact identity and alternate collision-free positions',()=>{
 assert.equal(compactDroneId('drone_12',0),'D12');
 const first=placeLabel(200,200,100,22,768,450,[])!;
 const second=placeLabel(200,200,100,22,768,450,[first])!;
 assert.ok(first&&second);assert.notDeepEqual(first,second);
 assert.equal(placeLabel(0,0,900,22,768,450,[]),null);
});
test('label boxes remain inside viewport and suppress overcrowded labels',()=>{
 const occupied=[];
 for(let i=0;i<30;i++){
   const box=placeLabel(250,180,80,22,768,450,occupied);
   if(box){assert.ok(box.x>=8&&box.y>=8&&box.x+box.width<=760);occupied.push(box);}
 }
 assert.ok(occupied.length<=4);
});
test('perspective camera clips terrain before any outer board edge, across allowed views',()=>{
 for(const aspect of [1,1.5,2.5])for(const elevation of [.52,1.38])for(const span of [560,1350]){
   const rig=clampCamera({...cameraPreset('tactical'),elevation,span});
   const eye=cameraEye(rig,aspect);
   assert.ok(Math.abs(eye.x)+4500<7000&&Math.abs(eye.z)+4500<7000);
   const camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
   camera.position.copy(eye);camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
   const center=new THREE.Vector3(rig.target.x,rig.target.y,rig.target.z).project(camera);
   assert.ok(Math.abs(center.x)<1e-8&&Math.abs(center.y)<1e-8);
 }
});
test('overview and tactical frame fleet, ground control and compressed satellite at requested widths',()=>{
 for(const aspect of [1150/510,990/510,976/480,720/450])for(const mode of ['overview','tactical'] as const){
  const rig=cameraPreset(mode), camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
  camera.position.copy(cameraEye(rig,aspect));camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
  for(const [x,y,z] of [[-230,35,-150],[230,130,150],[-280,5,170],[0,350,-400]]){
   const p=new THREE.Vector3(x,y,z).project(camera);
   assert.ok(Math.abs(p.x)<1&&Math.abs(p.y)<1&&p.z<1,`${mode}: ${x},${y},${z}`);
  }
 }
});
