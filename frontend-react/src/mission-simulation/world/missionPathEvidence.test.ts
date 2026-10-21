import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import { isNoRouteSample, withMissionPathEvidence } from './missionPathEvidence.ts';
import { routeHealth } from './routeHealth.ts';
import type { MissionPathMetric, MissionSimulationState } from '../types.ts';
import type { SwarmSnapshot } from '../swarm.ts';

const missing:MissionPathMetric={path_id:'direct',rssi:-125,pdr:0,sinr:-10,latency:1000,packet_loss:1};
function state():MissionSimulationState{const initial=adaptMissionSimulationState(null,{droneId:'one',route:'direct',threatLevel:'UNKNOWN',reward:0,step:0,noSafeRoute:false,availablePaths:['direct','satellite','mesh']});return {...initial,source:'synthetic',routes:initial.routes.map(r=>({...r,metric:{...missing,path_id:r.id},threatScore:1,safe:false}))};}
function snapshot(distance=351,link=false){return {base_position:[0,0,0],nodes:[{id:'one',position:[distance,0,0],range_m:350}],links:link?[{a:'one',b:'base'}]:[]} as SwarmSnapshot;}
test('no-route sentinels are recognized as a complete synthetic tuple, never from latency or loss alone',()=>{
 assert.equal(isNoRouteSample(missing,'synthetic'),true);
 assert.equal(isNoRouteSample(missing,undefined),false);
 for(const metric of [{...missing,rssi:-80},{...missing,sinr:15},{...missing,pdr:.1},{...missing,packet_loss:.9},{...missing,latency:8}])assert.equal(isNoRouteSample(metric,'synthetic'),false);
});
test('a current out-of-range path hides placeholder measurements without changing model inputs or exclusions',()=>{
 const original=state(),after=withMissionPathEvidence(original,snapshot(),'one',true),direct=after.routes[0];
 assert.equal(routeHealth(direct,false).label,'Out of range');assert.match(direct.availabilityReason!,/351 m.*350 m/);
 assert.equal(direct.measurementUnavailable,true);assert.equal(direct.metric,original.routes[0].metric);assert.equal(direct.metric!.latency,1000);
 assert.equal(direct.threatScore,1);assert.equal(direct.safe,false);
});
test('terrain or other radio ineligibility is not falsely described as out of range',()=>{
 const after=withMissionPathEvidence(state(),snapshot(150),'one',true);
 assert.equal(routeHealth(after.routes[0],false).label,'No radio link');
});
test('a reconnected radio waits for a real measurement instead of showing the previous 1000 ms placeholder',()=>{
 const original=state(),waiting=withMissionPathEvidence(original,snapshot(150,true),'one',true);
 assert.equal(routeHealth(waiting.routes[0],false).label,'Awaiting sample');
 original.routes[0].metric={...missing,rssi:-60,sinr:25,latency:8,pdr:.99,packet_loss:.01};
 const fresh=withMissionPathEvidence(original,snapshot(150,true),'one',true);
 assert.equal(fresh.routes[0].measurementUnavailable,false);assert.equal(fresh.routes[0].available,true);
 assert.equal(routeHealth(fresh.routes[0],false).health,'Unsafe');
});
test('real path loss, even with low detector risk and no jam, remains visible',()=>{
 const original=state();original.routes[2]={...original.routes[2],safe:true,threatScore:.01,metric:{...missing,path_id:'mesh',rssi:-88,sinr:12,latency:26,pdr:.78,packet_loss:.22}};
 const mesh=withMissionPathEvidence(original,snapshot(),'one',true).routes[2];
 assert.equal(routeHealth(mesh,false).label,'Packet loss 22%');assert.equal(mesh.metric!.packet_loss,.22);assert.equal(mesh.threatScore,.01);
});
test('stale snapshots cannot establish current radio availability, and placeholders remain non-measurements',()=>{
 const original=state(),stale=withMissionPathEvidence(original,snapshot(),'one',false);
 assert.equal(stale.routes[0].available,true);assert.equal(stale.routes[0].unavailableLabel,undefined);assert.equal(stale.routes[0].measurementUnavailable,true);assert.equal(stale.routes[0].metric,original.routes[0].metric);
 assert.equal(withMissionPathEvidence(original,snapshot(),'other',true),original);
});
