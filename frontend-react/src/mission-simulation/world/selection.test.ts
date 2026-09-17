/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { selectWorldObject } from './selection.ts';
import { curvePoint, routeCurve } from './networkGeometry.ts';

test('scene picks keep threat and infrastructure out of fleet selection',()=>{
 const drones:string[]=[], assets:string[]=[];
 for(const id of ['jammer','satellite','base','drone_12','removed_drone',undefined])
   selectWorldObject(id,['drone_12'],id=>drones.push(id),id=>assets.push(id));
 assert.deepEqual(drones,['drone_12']);
 assert.deepEqual(assets,['jammer','satellite','base']);
});
test('transient route sampling reuses storage without changing geometry or endpoints',()=>{
 const curve=routeCurve({x:10,y:80,z:20},{x:-280,y:5,z:170},{x:200,y:280,z:-200});
 const snapshot=structuredClone(curve), target={x:0,y:0,z:0};
 for(let i=0;i<=128;i++){
   const expected=curvePoint(curve,i/128);
   assert.strictEqual(curvePoint(curve,i/128,target),target);
   assert.deepEqual(target,expected);
 }
 assert.deepEqual(curve,snapshot);
});
