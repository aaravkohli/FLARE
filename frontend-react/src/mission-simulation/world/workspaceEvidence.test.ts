import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceEvidence } from './workspaceEvidence.ts';
import { attackTargets } from '../attackProfiles.ts';
import type { MissionTelemetryPayload } from '../types.ts';
import type { MissionRouteRequest } from '../routeTransition.ts';
import type { WorkspaceEvidenceInput } from './workspaceEvidence.ts';
const payload = ():MissionTelemetryPayload => ({type:'telemetry',timestamp:100,telemetry_age_s:0,
  decision:{path_name:'direct',threat_level:'LOW',step:2,reward:0,sdn_applied:true},
  metrics:{drone_id:'one',timestamp:100,source:'synthetic',paths:[{path_id:'direct',rssi:-50,pdr:.99,sinr:20,latency:20,packet_loss:.01}]},
  event:{event_id:'event-2',step:2,mission_context:{session_id:'current'},inference:{source:'fl_model',path_scores:[.1,.2,.3]},
    decision:{source:'rl_model',policy_path:'direct',requested_path:'direct',installed_path:'direct',threat_level:'LOW',safe_action_mask:[true,true,true]},sdn:{applied:true}}});
const input = ():WorkspaceEvidenceInput => ({payload:payload(),receivedAtMs:1000,transportStatus:'connected',controller:'Mock SDN'});
test('workspace evidence rejects other UAVs, previous sessions and missing session provenance',()=>{
  for(const [session,id] of [['other','one'],['current','two']]){
    const result=workspaceEvidence(input(),session,id,1100);
    assert.equal(result.state.installedRoute,null);assert.equal(result.state.reportedThreatLevel,undefined);assert.equal(result.evidence.evidenceCurrent,false);
  }
  const i=input();delete i.payload!.event!.mission_context;
  assert.equal(workspaceEvidence(i,'current','one',1100).state.installedRoute,null);
});
test('stale and disconnected observations are retained without fresh confirmation',()=>{
  const i=input();assert.equal(workspaceEvidence(i,'current','one',1100).evidence.evidenceCurrent,true);
  const stale=workspaceEvidence(i,'current','one',7000);assert.equal(stale.evidence.kind,'stale');assert.equal(stale.state.installedRoute,'direct');
  i.transportStatus='disconnected';assert.equal(workspaceEvidence(i,'current','one',1100).evidence.kind,'disconnected');
  i.transportStatus='connected';i.unavailable='model offline';assert.equal(workspaceEvidence(i,'current','one',1100).evidence.kind,'unavailable');
});
test('pending route keeps the prior installation and stale request outcome remains unknown',()=>{
  const i=input();i.requestReceivedAtMs=1100;i.request={type:'route_transition',phase:'requested',transition_id:'pending',drone_id:'one',step:3,occurred_at:101,
    telemetry:{...payload().metrics,timestamp:101},last_confirmed_path:'direct',inference:{source:'rl_model',path_scores:[.9,.1,.2],confidence:.9,attack_type:'spot'},
    decision:{source:'rl_model',policy_path:'satellite',requested_path:'satellite',threat_level:'HIGH',safety_override:false,safe_action_mask:[false,true,true],no_safe_route:false,network_action:'forward'},
  } as MissionRouteRequest;
  const result=workspaceEvidence(i,'current','one',1200);assert.equal(result.state.requestedRoute,'satellite');assert.equal(result.state.installedRoute,'direct');assert.equal(result.state.sdnApplied,undefined);assert.equal(result.controllerProgress,'awaiting');
  assert.equal(workspaceEvidence(i,'current','one',8000).controllerProgress,'outcome-unknown');
  i.request.step=1;assert.equal(workspaceEvidence(i,'current','one',1200).controllerProgress,null);
});
test('failed SDN installation remains failed and missing measurements never become zero',()=>{
  const i=input();i.payload!.event!.sdn={applied:false,error:'controller timeout'};i.payload!.event!.decision!.installed_path=null;i.payload!.event!.decision!.requested_path='satellite';i.payload!.metrics.paths=[];
  const result=workspaceEvidence(i,'current','one',1100);assert.equal(result.phase,'apply-failed');assert.equal(result.state.installedRoute,null);assert.equal(result.state.routes[0].metric,undefined);
});
test('attack scope is explicit and filters retired UAV identities',()=>{
  assert.deepEqual(attackTargets('inspected','two',['one'],['one','two']),['two']);
  assert.deepEqual(attackTargets('selected','two',['one','retired','one'],['one','two']),['one']);
  assert.deepEqual(attackTargets('selected','one',[],['one']),[]);
  assert.deepEqual(attackTargets('fleet','one',[],['one','two']),['one','two']);
});
