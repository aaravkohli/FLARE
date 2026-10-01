/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cameraPreset, clampCamera, dampCamera, cameraEye } from './cameraRig.ts';
import { terrainHeight } from './terrain.ts';
import { satelliteWorldPosition } from './satelliteWorldPosition.ts';
import { angleDifference } from './dynamics.ts';
import * as THREE from 'three';

test('camera presets are deterministic, distinct and leave selected motion unchanged', () => {
  const position = Object.freeze({x:200,y:100,z:50});
  const selected = Object.freeze({position,heading:Math.PI/2});
  assert.deepEqual(cameraPreset('overview'),cameraPreset('overview'));
  assert.equal(cameraPreset('overview').elevation,35*Math.PI/180);
  assert.equal(cameraPreset('tactical',selected).elevation,38*Math.PI/180);
  assert.notDeepEqual(cameraPreset('network',selected,'direct'),cameraPreset('network',selected,'satellite'));
  const follow=cameraPreset('follow',selected); follow.target.x=0;
  assert.equal(position.x,200);
  assert.ok(Math.abs(angleDifference(cameraPreset('follow',selected).yaw,Math.PI))<1e-12);
});
test('manual camera clamps keep eye above terrain and allow full trailing yaw',()=>{
  for(const elevation of [-100,0,Math.PI,100]) for(const span of [-100,0,100000]) {
    const rig=clampCamera({yaw:100,elevation,span,target:{x:9000,y:-500,z:-9000}});
    assert.ok(rig.span>=160&&rig.span<=1350); assert.ok(Math.abs(rig.yaw)<=Math.PI);
    const eye=cameraEye(rig);assert.ok(eye.y>17);assert.ok(rig.target.x<=420&&rig.target.z>=-300);
  }
});
test('camera damping uses the short yaw arc, is frame partition independent and respects reduced motion',()=>{
  const start=cameraPreset('overview'),goal=cameraPreset('follow',{position:{x:200,y:100,z:-100},heading:2});
  let a=start,b=start;
  for(let i=0;i<60;i++) a=dampCamera(a,goal,1/60);
  for(let i=0;i<30;i++) b=dampCamera(b,goal,1/30);
  assert.ok(Math.abs(a.target.x-b.target.x)<1e-9);assert.ok(a.span>goal.span&&a.span<start.span);
  assert.deepEqual(dampCamera(start,goal,0,true),clampCamera(goal));
  const nearWrap={...start,yaw:179*Math.PI/180};
  const acrossWrap={...goal,yaw:-179*Math.PI/180};
  const first=dampCamera(nearWrap,acrossWrap,1/60);
  assert.ok(angleDifference(first.yaw,nearWrap.yaw)>0);
  assert.ok(angleDifference(first.yaw,nearWrap.yaw)<2*Math.PI/180);
  let fine=nearWrap, coarse=nearWrap;
  for(let i=0;i<60;i++) fine=dampCamera(fine,acrossWrap,1/60);
  for(let i=0;i<30;i++) coarse=dampCamera(coarse,acrossWrap,1/30);
  assert.ok(Math.abs(angleDifference(fine.yaw,coarse.yaw))<1e-9);
});
test('following from heading keeps selected drone and visual ground reference in view',()=>{
  const aspect=990/510;
  for(const x of [-360,-180,0,180,360])for(const z of [-240,-120,0,120,240])
  for(const y of [35,70,100,130])for(const heading of [-Math.PI,-Math.PI/2,0,Math.PI/2]){
    const rig=clampCamera(cameraPreset('follow',{position:{x,y,z},heading}));
    const eye=cameraEye(rig,aspect);
    assert.ok(eye.y>terrainHeight(eye.x,eye.z)+30);
    const camera=new THREE.PerspectiveCamera(44,aspect,2,4500);
    camera.position.copy(eye);camera.lookAt(rig.target.x,rig.target.y,rig.target.z);camera.updateMatrixWorld();
    const drone=new THREE.Vector3(x,y,z).project(camera);
    const ground=new THREE.Vector3(x,terrainHeight(x,z),z).project(camera);
    assert.ok(Math.abs(drone.x)<.1&&Math.abs(drone.y)<.2);
    assert.ok(Math.abs(ground.x)<.1&&ground.y>-.95&&ground.y<drone.y);
  }
});
test('visual DEM relief remains below the patrol floor without changing flight altitude',()=>{
  for(let x=-460;x<=460;x+=10)for(let z=-340;z<=340;z+=10){
    const h=terrainHeight(x,z);
    assert.ok(Number.isFinite(h)&&h<=17&&h<35-10);
    assert.equal(h,terrainHeight(x,z));
  }
});
test('compressed satellite world stays above UAV volume and cannot reach literal orbital scale',()=>{
  for(let i=0;i<1000;i++){const p=satelliteWorldPosition({x:Math.cos(i),y:Math.sin(i),z:Math.cos(i*.3)});assert.ok(p.y>=330&&p.y<=370);assert.ok(Math.abs(p.x)<=360&&p.z>=-450&&p.z<=-350);}
  const a=satelliteWorldPosition({x:1,y:2,z:3}), b=satelliteWorldPosition({x:100,y:200,z:300});
  assert.ok(Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z)<1e-10);
  assert.throws(()=>satelliteWorldPosition({x:0,y:0,z:0}),RangeError);
});
