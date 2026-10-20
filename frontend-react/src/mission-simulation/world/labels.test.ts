/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactDroneId, craftMarkerBox, placeLabel, worldDroneLabel } from './labels.ts';
import { cameraEye, cameraPreset, CAMERA_FOV, clampCamera, rangeCamera } from './cameraRig.ts';
import { GROUND_STATION_VISUAL_ENDPOINT } from './groundStation.ts';
import * as THREE from 'three';
import { terrainHeight } from './terrain.ts';

test('mission range camera frames ground landmarks and the full aircraft volume together', () => {
 const aircraft=[];
 for(const x of [-360,360])for(const z of [-240,240])for(const y of [35,130])aircraft.push({x,y,z});
 const original=structuredClone(aircraft);
 const landmarks=[[-420,-210],[350,-210],[-420,300],[350,300],[-280,170],[250,160]].map(([x,z])=>({x,y:terrainHeight(x,z),z}));
 for(const aspect of [.75,1.5,2.5]){
  const rig=rangeCamera(aircraft,aspect);
  const camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
  camera.position.copy(cameraEye(rig,aspect));camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
  for(const point of [...aircraft,...landmarks]){
   const projected=new THREE.Vector3(point.x,point.y,point.z).project(camera);
   assert.ok(Math.abs(projected.x)<.95&&Math.abs(projected.y)<.95&&projected.z<1,JSON.stringify({point,aspect,projected}));
  }
 }
 assert.deepEqual(aircraft,original);assert.ok(rangeCamera(aircraft).target.y<70);
});
test('labels use compact identity and alternate collision-free positions',()=>{
 assert.equal(compactDroneId('drone_12',0),'D12');
 const first=placeLabel(200,200,100,22,768,450,[])!;
 const second=placeLabel(200,200,100,22,768,450,[first])!;
 assert.ok(first&&second);assert.notDeepEqual(first,second);
 assert.equal(placeLabel(0,0,900,22,768,450,[]),null);
});
test('selected and hovered drone labels identify local simulation altitude and only selected installed route',()=>{
 assert.equal(worldDroneLabel('D3',84.4,true,false,'direct'),'D3 · INST DIRECT\nSIM ALT 84 m');
 assert.equal(worldDroneLabel('D4',92.6,false,true,'satellite'),'D4\nSIM ALT 93 m');
 assert.equal(worldDroneLabel('D5',75,false,false,'mesh'),'D5');
 assert.equal(worldDroneLabel('D6',80,true,false,'hold'),'D6 · HOLD\nSIM ALT 80 m');
 assert.equal(worldDroneLabel('D7',70,true,false,null),'D7\nSIM ALT 70 m');
});
test('screen-size UAV identification markers stay compact and clear of their altitude label',()=>{
 const selected=craftMarkerBox(200,200,true,false);
 const hovered=craftMarkerBox(200,200,false,true);
 const ordinary=craftMarkerBox(200,200,false,false);
 assert.deepEqual([selected.width,hovered.width,ordinary.width],[14,12,10]);
 for(const marker of [selected,hovered,ordinary]){
  assert.equal(marker.x+marker.width/2,200);
  assert.equal(marker.y+marker.height/2,200);
 }
 const label=placeLabel(200,200,170,42,768,450,[selected])!;
 assert.ok(label);
 assert.ok(label.y>=selected.y+selected.height+8);
});
test('label boxes remain inside viewport and suppress overcrowded labels',()=>{
 const occupied=[];
 for(let i=0;i<30;i++){
   const box=placeLabel(250,180,80,22,768,450,occupied);
   if(box){assert.ok(box.x>=8&&box.y>=8&&box.x+box.width<=760);occupied.push(box);}
 }
 assert.ok(occupied.length<=4);
});
test('selected drone label finds a clear position in the clustered satellite Overview fixture',()=>{
 const occupied=[
  craftMarkerBox(546,328,true,false), craftMarkerBox(508,323,false,false),
  craftMarkerBox(465,271,false,false), craftMarkerBox(631,344,false,false),
 ];
 for(const [x,y,text] of [[623,159,'UPLINK'],[562,162,'DOWNLINK']] as const){
  const annotation=placeLabel(x,y,text.length*6.2+14,21,1150,510,occupied);
  if(annotation) occupied.push(annotation);
 }
 assert.equal(placeLabel(546,328,145,42,1150,510,occupied),null);
 const selected=placeLabel(546,328,145,42,1150,510,occupied,true);
 assert.ok(selected);
 assert.ok(selected.x>=8&&selected.y>=8&&selected.x+selected.width<=1142&&selected.y+selected.height<=502);
 for(const box of occupied) assert.ok(
  selected.x>=box.x+box.width+8||selected.x+selected.width+8<=box.x||
  selected.y>=box.y+box.height+5||selected.y+selected.height+5<=box.y,
 );
});
test('perspective camera clips terrain before any outer board edge, across allowed views',()=>{
 for(const aspect of [1,1.5,2.5])for(const elevation of [.44,1.38])for(const span of [160,1350]){
   const rig=clampCamera({...cameraPreset('tactical'),elevation,span});
   const eye=cameraEye(rig,aspect);
   assert.ok(Math.abs(eye.x)+4500<7000&&Math.abs(eye.z)+4500<7000);
   const camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
   camera.position.copy(eye);camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
   const center=new THREE.Vector3(rig.target.x,rig.target.y,rig.target.z).project(camera);
   assert.ok(Math.abs(center.x)<1e-8&&Math.abs(center.y)<1e-8);
 }
});
test('overview and network orbital framing include patrol volume, GCS and compressed satellite',()=>{
 const patrol=[];
 for(const x of [-360,360])for(const z of [-240,240])for(const y of [35,130])patrol.push({x,y,z});
 const satellite=[{x:-360,y:350,z:-400},{x:360,y:350,z:-400},{x:0,y:350,z:-450},{x:0,y:350,z:-350}];
 for(const aspect of [1150/510,990/510,976/480,720/450])
 for(const mode of ['overview','network'] as const){
  const rig=cameraPreset(mode), camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
  camera.position.copy(cameraEye(rig,aspect));camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
  for(const point of [...patrol,GROUND_STATION_VISUAL_ENDPOINT,...satellite]){
   const p=new THREE.Vector3(point.x,point.y,point.z).project(camera);
   assert.ok(Math.abs(p.x)<.8&&Math.abs(p.y)<.8&&p.z<1,`${mode}: ${JSON.stringify(point)}`);
  }
 }
});
test('tactical and ground-network cameras hold selected craft and mast tip together',()=>{
 for(const aspect of [1150/510,990/510,976/480,720/450])
 for(const x of [-360,0,360])for(const z of [-240,0,240])for(const y of [35,100,130]){
  const selected={position:{x,y,z},heading:0};
  for(const mode of ['tactical','network'] as const){
   const rig=cameraPreset(mode,selected,'direct');
   const camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
   camera.position.copy(cameraEye(rig,aspect));camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
   for(const point of [selected.position,GROUND_STATION_VISUAL_ENDPOINT]){
    const p=new THREE.Vector3(point.x,point.y,point.z).project(camera);
    assert.ok(Math.abs(p.x)<.82&&Math.abs(p.y)<.82,`${mode}: ${JSON.stringify(point)}`);
   }
  }
 }
});
test('mesh network camera frames all possible peers without claiming a relay',()=>{
 for(const aspect of [1150/510,990/510,976/480,720/450]){
  const rig=cameraPreset('network',undefined,'mesh');
  const camera=new THREE.PerspectiveCamera(CAMERA_FOV,aspect,2,4500);
  camera.position.copy(cameraEye(rig,aspect));camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
  for(const x of [-360,360])for(const z of [-240,240])for(const y of [35,130]){
   const p=new THREE.Vector3(x,y,z).project(camera);
   assert.ok(Math.abs(p.x)<.82&&Math.abs(p.y)<.82);
  }
 }
});
