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
