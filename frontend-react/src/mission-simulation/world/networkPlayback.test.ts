import test from 'node:test';
import assert from 'node:assert/strict';
import { edgePoint, playbackTime, samplePacketEvents } from './networkPlayback.ts';
import type { PacketEvent } from '../swarm.ts';
const packet={id:'p1',source:'a',destination:'c',route:['a','b','c'],hop:0,status:'in_flight',category:'sensor',start_at:1,end_at:1.008,family:'mesh'};
const events=[{schema_version:'mission_packet_event_v1',session_id:'s',cursor:1,simulation_time:1,kind:'packet_hop',data:{...packet,from_node:'a',to_node:'b'}},{schema_version:'mission_packet_event_v1',session_id:'s',cursor:2,simulation_time:1.008,kind:'packet_hop',data:{...packet,hop:1,start_at:1.008,end_at:1.016,from_node:'b',to_node:'c'}},{schema_version:'mission_packet_event_v1',session_id:'s',cursor:3,simulation_time:1.016,kind:'packet_result',data:{...packet,hop:2,status:'delivered',completed_at:1.016}}] as PacketEvent[];
test('short hops remain visible from event history with their exact direction and timing',()=>{
 const first=samplePacketEvents(events,1.004);assert.equal(first[0].from,'a');assert.equal(first[0].to,'b');assert.ok(Math.abs(first[0].progress-.5)<1e-8);
 const second=samplePacketEvents(events,1.012);assert.equal(second[0].from,'b');assert.equal(second[0].to,'c');
 assert.equal(samplePacketEvents(events,1.02)[0].kind,'delivered');assert.equal(samplePacketEvents(events,2).length,0);
});
test('buffer uses simulation speed; pause and stale evidence never extrapolate',()=>{
 assert.equal(playbackTime(10,2,100,true),9.7);assert.equal(playbackTime(10,2,100,false),10);assert.equal(playbackTime(10,2,100,true,false),9.5);
 assert.equal(samplePacketEvents(events,1.004,'',200,'other').length,0);
});
test('curved link and reverse packet direction share identical geometry',()=>{
 const a={x:0,z:0},b={x:100,z:10};assert.deepEqual(edgePoint(a,b,.5,'a','b'),edgePoint(b,a,.5,'b','a'));
});
