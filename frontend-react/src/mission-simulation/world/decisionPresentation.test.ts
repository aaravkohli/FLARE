/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import type { MissionSimulationState } from '../types.ts';
import { decisionPhase, emptyDecisionHistory, reduceDecisionHistory } from './decisionPresentation.ts';
const fallback={droneId:'drone_1',route:'direct',threatLevel:'LOW',reward:0,step:0,noSafeRoute:false,availablePaths:['direct','satellite','mesh']};
function state(overrides:Partial<MissionSimulationState>={}):MissionSimulationState {
 const s=adaptMissionSimulationState(null,fallback);
 return {...s,eventId:'event-1',timestamp:1,reportedThreatLevel:'LOW',installedRoute:'direct',requestedRoute:'direct',networkAction:'forward',sdnApplied:true,
 routes:s.routes.map(r=>({...r,safe:true,jammed:false,metric:{path_id:r.id,rssi:-60,sinr:20,pdr:.99,latency:30,packet_loss:.01}})),...overrides};
}
const step=(h:ReturnType<typeof emptyDecisionHistory>,s:MissionSimulationState,id='drone_1')=>reduceDecisionHistory(h,{state:s,selectedId:id});
test('missing evidence cannot become nominal or imply model provenance',()=>{
 const s=adaptMissionSimulationState(null,fallback);
 assert.equal(decisionPhase(s,null,'drone_1'),'awaiting-evidence');
 assert.equal(s.inferenceSource,undefined);assert.equal(s.decisionSource,undefined);
 assert.equal(s.reportedSafetyOverride,undefined);
});
test('injected scenario and detector findings are distinct',()=>{
 assert.equal(decisionPhase(state({activeAttack:'spot'}),null,'drone_1'),'scenario-active');
 assert.equal(decisionPhase(state({reportedThreatLevel:'HIGH'}),null,'drone_1'),'threat-reported');
 assert.equal(decisionPhase(state({networkDetection:{status:'MALICIOUS',detected_classes:['dos'],evidence_source:'synthetic'}}),null,'drone_1'),'threat-reported');
});
test('request, failed apply and installation follow evidence without timers',()=>{
 const a=state(), pending=state({eventId:'2',timestamp:2,requestedRoute:'satellite',sdnApplied:false});
 assert.equal(decisionPhase(pending,a,'drone_1'),'rerouting');
 assert.equal(decisionPhase({...pending,sdnError:'unavailable'},a,'drone_1'),'apply-failed');
 const h=step(step(emptyDecisionHistory(),a),pending);
 assert.ok(!h.events.some(e=>e.label==='SDN installed satellite'));
 const installed=step(h,{...pending,eventId:'3',timestamp:3,installedRoute:'satellite',sdnApplied:true,routeChanged:true});
 assert.ok(installed.events.some(e=>e.label==='SDN installed satellite'));
 assert.ok(installed.events.some(e=>e.label==='Route changed: direct → satellite'));
});
test('rapid route changes deduplicate and reject older snapshots',()=>{
 let h=emptyDecisionHistory();
 for(const [i,route] of ['direct','satellite','mesh'].entries())h=step(h,state({eventId:String(i),timestamp:i+1,installedRoute:route as 'direct',requestedRoute:route as 'direct'}));
 const same=step(h,h.last!);assert.strictEqual(same,h);
 assert.strictEqual(step(h,state({eventId:'older',timestamp:.5})),h);
 assert.equal(h.events.filter(e=>e.label.startsWith('SDN installed')).length,3);
});
test('clearing attack does not imply recovery while route is degraded',()=>{
 const attack=state({activeAttack:'barrage',reportedThreatLevel:'HIGH'});
 const degraded=state({routes:attack.routes.map(r=>({...r,safe:false}))});
 assert.equal(decisionPhase(degraded,attack,'drone_1'),'degraded');
 assert.equal(decisionPhase(state(),degraded,'drone_1'),'recovery');
 assert.equal(decisionPhase(state(),state(),'drone_1'),'nominal');
 const unknown=state({reportedThreatLevel:undefined});
 assert.equal(decisionPhase(unknown,attack,'drone_1'),'awaiting-evidence');
});
test('selected drone changes discard prior history and reject stale payload',()=>{
 const h=step(emptyDecisionHistory(),state({activeAttack:'spot'}));
 const switched=step(h,state(),'drone_2');
 assert.equal(switched.events.length,0);assert.equal(switched.phase,'awaiting-evidence');
 const next=step(switched,state({droneId:'drone_2'}),'drone_2');
 assert.ok(!next.events.some(e=>e.label.includes('cleared')));
});
test('HOLD request differs from applied HOLD and containment keeps its reason',()=>{
 assert.equal(decisionPhase(state({requestedRoute:'hold',networkAction:'hold'}),null,'drone_1'),'rerouting');
 const held=state({requestedRoute:'hold',installedRoute:'hold',networkAction:'hold',constraintReason:'containment_required',containmentMode:'quarantined'});
 assert.equal(decisionPhase(held,null,'drone_1'),'hold');
 assert.ok(step(emptyDecisionHistory(),held).events.some(e=>e.label==='SDN applied HOLD · containment'));
 assert.equal(decisionPhase(state({containmentMode:'restricted'}),null,'drone_1'),'containment');
 assert.equal(decisionPhase(state({noSafeRoute:true}),null,'drone_1'),'awaiting-evidence');
});
test('safety override retains policy, request and installation; fallback is never labelled DQN',()=>{
 const s=state({policyRoute:'direct',requestedRoute:'satellite',installedRoute:'mesh',safetyOverride:true,reportedSafetyOverride:true,decisionSource:'greedy_fallback'});
 const h=step(emptyDecisionHistory(),s);
 assert.ok(h.events.some(e=>e.label==='Fallback policy selected direct'));
 assert.ok(h.events.some(e=>e.label.startsWith('Safety override requested satellite')));
 assert.ok(h.events.some(e=>e.label==='SDN installed mesh'));
 assert.ok(!h.events.some(e=>e.label.includes('DQN')));
});
