import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assistanceStatus,assistanceMeasurement} from './uavAssistance.ts';
import type {TopologySelection,UavAssistance} from '../swarm.ts';
const assistance:UavAssistance={version:'affected_uav_assist_v1',enabled:true,reason:'No feasible alternative',nodes:[{node:'a',status:'Degraded',degraded:true}],attempt:null,history:[],candidates:[],limits:{},counters:{}};
const selection:TopologySelection={mode:'auto',reason:'',evaluated_at:0,switched_at:null,transition_state:'idle',requested_target:null,cooldown_remaining_s:0,evidence:'',fallback:null,candidates:[],blacklisted:[],assistance};
test('assistance uses inspected identity and explicit manual/stale/unavailable states',()=>{
 assert.equal(assistanceStatus(selection,'a',true),'Degraded');
 assert.equal(assistanceStatus(selection,'b',true),'Awaiting legitimate Mesh evidence');
 assert.match(assistanceStatus({...selection,mode:'manual'},'a',true),/manual topology pinned/);
 assert.match(assistanceStatus(selection,'a',false),/Connection stale/);
 assert.match(assistanceStatus(undefined,'a',true),/unavailable/);
 assert.equal(assistanceStatus({...selection,assistance:{...assistance,enabled:false}},'a',true),'Disabled');
});
test('missing quality stays unavailable; observed zero is preserved',()=>{
 assert.equal(assistanceMeasurement(null,'delivery'),'—');assert.equal(assistanceMeasurement(undefined,'latency'),'—');
 assert.equal(assistanceMeasurement(0,'delivery'),'0.0%');assert.equal(assistanceMeasurement(0,'latency'),'0.0 ms');
});
