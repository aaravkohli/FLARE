/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cameraPreset, clampCamera, dampCamera, cameraEye } from './cameraRig.ts';
import { terrainHeight } from './terrain.ts';
import { satelliteWorldPosition } from './satelliteWorldPosition.ts';

test('camera presets are deterministic and follow never mutates selected motion', () => {
  const position = Object.freeze({x:200,y:100,z:50});
  assert.deepEqual(cameraPreset('tactical'),cameraPreset('tactical'));
  const follow=cameraPreset('follow',position); follow.target.x=0;
  assert.equal(position.x,200);
  assert.equal(cameraPreset('tactical').elevation,38*Math.PI/180);
});
test('manual camera clamps keep eye above terrain and zoom in useful bounds',()=>{
  for(const elevation of [-100,0,Math.PI,100]) for(const span of [-100,0,100000]) {
    const rig=clampCamera({yaw:100,elevation,span,target:{x:9000,y:-500,z:-9000}});
    assert.ok(rig.span>=560&&rig.span<=1350); assert.ok(rig.yaw<=.8);
    const eye=cameraEye(rig);assert.ok(eye.y>200);assert.ok(rig.target.x<=360&&rig.target.z>=-240);
  }
});
test('camera damping is frame partition independent and reduced motion reaches target immediately',()=>{
  const start=cameraPreset('tactical'),goal=cameraPreset('follow',{x:200,y:100,z:-100});
  let a=start,b=start;
  for(let i=0;i<60;i++) a=dampCamera(a,goal,1/60);
  for(let i=0;i<30;i++) b=dampCamera(b,goal,1/30);
  assert.ok(Math.abs(a.target.x-b.target.x)<1e-9);assert.ok(a.span>goal.span&&a.span<start.span);
  assert.deepEqual(dampCamera(start,goal,0,true),clampCamera(goal));
});
test('static terrain remains below every patrol altitude across the scene',()=>{
  for(let x=-460;x<=460;x+=10)for(let z=-340;z<=340;z+=10){const h=terrainHeight(x,z);assert.ok(Number.isFinite(h)&&h<35&&h>=-3);assert.equal(h,terrainHeight(x,z));}
});
test('compressed satellite world stays above UAV volume and cannot reach literal orbital scale',()=>{
  for(let i=0;i<1000;i++){const p=satelliteWorldPosition({x:Math.cos(i),y:Math.sin(i),z:Math.cos(i*.3)});assert.ok(p.y>=330&&p.y<=370);assert.ok(Math.abs(p.x)<=360&&p.z>=-450&&p.z<=-350);}
  const a=satelliteWorldPosition({x:1,y:2,z:3}), b=satelliteWorldPosition({x:100,y:200,z:300});
  assert.ok(Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z)<1e-10);
  assert.throws(()=>satelliteWorldPosition({x:0,y:0,z:0}),RangeError);
});
