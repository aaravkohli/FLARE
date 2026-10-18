import test from 'node:test';
import assert from 'node:assert/strict';
import { publishMissionSnapshot, getMissionSnapshot, subscribeMission } from '../swarm.ts';
import type { SwarmSnapshot } from '../swarm.ts';

test('authoritative mission store rejects stale revision/time and accepts reset sessions',()=>{
 const state={session_id:'a',revision:3,simulation_time:20} as SwarmSnapshot;
 publishMissionSnapshot(state);publishMissionSnapshot({...state,revision:2,simulation_time:30});
 assert.equal(getMissionSnapshot(),state);
 publishMissionSnapshot({...state,simulation_time:19});assert.equal(getMissionSnapshot(),state);
 const reset={...state,session_id:'b',simulation_time:0};publishMissionSnapshot(reset);assert.equal(getMissionSnapshot(),reset);
 publishMissionSnapshot({...state,simulation_time:50});assert.equal(getMissionSnapshot(),reset);
});
test('mission subscribers stop receiving updates when unmounted',()=>{
 let calls=0;const unsubscribe=subscribeMission(()=>{calls++;});
 const state=getMissionSnapshot()!;publishMissionSnapshot({...state,simulation_time:1});assert.equal(calls,1);
 unsubscribe();publishMissionSnapshot({...state,simulation_time:2});assert.equal(calls,1);
});

test('packet batches deduplicate cursors, retain short hops and reject another session',async()=>{
 const {publishPacketEvents}=await import('../swarm.ts');
 const current=getMissionSnapshot()!;
 const event={schema_version:'mission_packet_event_v1' as const,session_id:current.session_id,cursor:1,simulation_time:current.simulation_time,kind:'packet_result',data:{id:'p1',route:['a','b'],hop:1,status:'delivered',category:'sensor',start_at:0,end_at:.01,family:'mesh'}};
 publishPacketEvents({session_id:current.session_id,events:[event,event],cursor:1});
 assert.equal(getMissionSnapshot()?.packet_events?.length,1);
 publishMissionSnapshot({...getMissionSnapshot()!,simulation_time:current.simulation_time+.1,packet_events:[]});
 assert.equal(getMissionSnapshot()?.packet_events?.length,1);
 publishPacketEvents({session_id:'retired',events:[{...event,cursor:2}],cursor:2});
 assert.equal(getMissionSnapshot()?.packet_events?.length,1);
});

test('received packet cursor and retention gap survive newer snapshots for reconnect',async()=>{
 const {publishPacketEvents}=await import('../swarm.ts');const s=getMissionSnapshot()!;
 publishPacketEvents({session_id:s.session_id,events:[],cursor:2,event_gap:true});
 publishMissionSnapshot({...getMissionSnapshot()!,simulation_time:s.simulation_time+.1,packet_cursor:50,packet_event_gap:undefined});
 assert.equal(getMissionSnapshot()?.packet_received_cursor,2);assert.equal(getMissionSnapshot()?.packet_event_gap,true);
});
