import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import { withMissionConditions, missionCommunicationConditions } from './missionConditions.ts';
import { routeHealth } from './routeHealth.ts';
import type { MissionSimulationState } from '../types.ts';
import type { SwarmAttack, SwarmSnapshot } from '../swarm.ts';

const state=():MissionSimulationState=>{
 const initial=adaptMissionSimulationState(null,{droneId:'one',route:'direct',threatLevel:'LOW',reward:0,step:0,noSafeRoute:false,availablePaths:['direct','satellite','mesh']});
 return {...initial,activeAttack:'spot',jammedPaths:['direct'],installedRoute:'satellite',sdnApplied:true,
  routes:initial.routes.map(r=>({...r,jammed:r.id==='direct',active:r.id==='satellite',safe:true,threatScore:.01,metric:{path_id:r.id,rssi:-60,pdr:.99,sinr:25,latency:8,packet_loss:.01}}))};
};
const condition=(profile='spot',targets=['one'],paths=['direct']):SwarmAttack=>({attack_id:profile,profile,group:'ew',affected_targets:targets,effective_paths:paths,status:'active',expires_at:15,outcomes:{}});
const snapshot=(active:SwarmAttack[]=[])=>({session_id:'current',attacks:{active,history:[],capabilities:{profiles:{},families:[]}}} as unknown as SwarmSnapshot);

test('expiry clears only injected interference flags, without waiting for another detector observation',()=>{
 const before=state(), after=withMissionConditions(before,snapshot(),'one',true);
 assert.equal(after.activeAttack,'none');assert.deepEqual(after.jammedPaths,[]);assert.equal(after.routes[0].jammed,false);
 assert.equal(routeHealth(before.routes[0],false).label,'Interference');assert.equal(routeHealth(after.routes[0],false).label,'Available');
 assert.equal(after.routes[0].metric,before.routes[0].metric);assert.equal(after.routes[0].threatScore,.01);
 assert.equal(after.installedRoute,'satellite');assert.equal(after.sdnApplied,true);assert.equal(before.routes[0].jammed,true);
});
test('RF loss and safety exclusions remain explicit after an injection ends',()=>{
 const original=state();original.routes[0].metric!.packet_loss=.28;
 const after=withMissionConditions(original,snapshot(),'one',true);
 assert.equal(routeHealth(after.routes[0],false).label,'Packet loss 28%');assert.equal(after.routes[0].metric!.packet_loss,.28);
 after.routes[0].safe=false;assert.equal(routeHealth(after.routes[0],false).health,'Unsafe');
 after.routes[0].available=false;assert.equal(routeHealth(after.routes[0],false).health,'Unavailable');
});
test('mitigation is not interference; simultaneous conditions are scoped to their actual targets',()=>{
 const s=snapshot([condition('fhss'),condition('barrage',['one'],['satellite','mesh']),condition('spot',['two'],['direct'])]);
 assert.deepEqual(missionCommunicationConditions(s,'one').jammedPaths,['satellite','mesh']);
 assert.equal(withMissionConditions(state(),snapshot([condition('fhss')]),'one',true).routes[0].jammed,false);
 assert.equal(withMissionConditions(state(),snapshot([condition('gps_spoofing')]),'one',true).routes[0].jammed,false);
});
test('stale state, missing condition evidence and another inspected UAV never manufacture recovery',()=>{
 const original=state();assert.equal(withMissionConditions(original,snapshot(),'one',false),original);
 assert.equal(withMissionConditions(original,{} as SwarmSnapshot,'one',true),original);
 assert.equal(withMissionConditions(original,snapshot(),'two',true),original);
});
test('a pre-expiry sample is marked pending until new telemetry arrives, without clearing measured risk',()=>{
 const s=snapshot();s.attacks!.history=[{...condition(),affected_targets:[],targets:['one'],status:'expired',completed_at:15}];
 const original=state();original.routes[0].safe=false;original.routes[0].threatScore=1;
 const waiting=withMissionConditions(original,s,'one',true);
 assert.equal(waiting.routes[0].jammed,false);assert.equal(waiting.routes[0].recoveryPending,true);
 assert.equal(routeHealth(waiting.routes[0],false).label,'Updating');assert.equal(waiting.routes[0].threatScore,1);
 assert.equal(waiting.routes[0].metric,original.routes[0].metric);assert.equal(waiting.routes[0].safe,false);
 const observed={...original,routes:original.routes.map(r=>({...r,jammed:false}))};
 const fresh=withMissionConditions(observed,s,'one',true);
 assert.equal(fresh.routes[0].recoveryPending,false);assert.equal(routeHealth(fresh.routes[0],false).label,'Safety blocked');
});
