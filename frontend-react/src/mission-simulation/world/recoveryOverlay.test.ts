import {test} from 'node:test';
import assert from 'node:assert/strict';
import {recoveryOverlay} from './recoveryOverlay.ts';
import type {SwarmSnapshot} from '../swarm.ts';
test('both renderers receive only current authoritative recovery edges, separate from topology',()=>{
 const snapshot={simulation_time:10,nodes:[{id:'a'},{id:'b'}],links:[],autonomy:{overlay_edges:[
 {a:'a',b:'b',kind:'recovery_control',expires_at:11},{a:'b',b:'a',kind:'verification',expires_at:11},
 {a:'a',b:'base',kind:'recovery_control',expires_at:10},{a:'unknown',b:'a',kind:'verification',expires_at:11},
 ]}} as unknown as SwarmSnapshot;
 assert.deepEqual(recoveryOverlay(snapshot,'a'),[{a:'b',b:'a',kind:'verification'}]);
 assert.equal(snapshot.links.length,0);assert.deepEqual(recoveryOverlay(snapshot,'other'),[]);
});
test('recovery overlay is bounded and absent data does not invent connectivity',()=>{
 assert.deepEqual(recoveryOverlay({nodes:[],simulation_time:0} as unknown as SwarmSnapshot,''),[]);
});
test('assisted verification remains visible when flight autonomy is off',()=>{
 const snapshot={simulation_time:2,nodes:[{id:'a'}],links:[],autonomy:{mode:'off',overlay_edges:[{a:'a',b:'base',kind:'verification',expires_at:3}]}} as unknown as SwarmSnapshot;
 assert.deepEqual(recoveryOverlay(snapshot,'a'),[{a:'a',b:'base',kind:'verification'}]);
 assert.equal(snapshot.links.length,0);
});
