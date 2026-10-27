import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {SwarmSnapshot} from '../swarm.ts';
import {receiptEvidence} from './receiptEvidence.ts';
import {topologyControlValue,topologyStatus} from './topologySelection.ts';
const snapshot=()=>({simulation_time:12,playing:true,observability:{epoch:3,freshness_s:3,nodes:[{node:'a',eligible:true}]}} as SwarmSnapshot);
const report={epoch:3,clock:'simulation',captured_at:10};
test('simulation report freshness freezes on pause and rejects epochs and missing evidence',()=>{
 const s=snapshot();assert.equal(receiptEvidence(s,'a',report,true)?.ageSeconds,2);
 s.playing=false;const frozen=receiptEvidence(s,'a',report,true);assert.match(frozen!.label,/Paused/);assert.equal(frozen!.evidenceCurrent,true);
 s.simulation_time=13;assert.equal(receiptEvidence(s,'a',report,true)?.evidenceCurrent,false);
 assert.equal(receiptEvidence(s,'a',{...report,epoch:2},true)?.kind,'awaiting');
 assert.equal(receiptEvidence(s,'b',report,true)?.evidenceCurrent,false);
 assert.equal(receiptEvidence(s,'a',report,false)?.kind,'disconnected');
 assert.equal(receiptEvidence(s,'a',null,true)?.kind,'awaiting');
 assert.equal(receiptEvidence({} as SwarmSnapshot,'a',report,true),null);
});
test('safeguarded and balanced selections remain distinct from manual topology',()=>{
 const selection={mode:'auto',policy_id:'evidence_resilience_v1',reason:'evidence budget',requested_target:'ring'} as const;
 assert.equal(topologyControlValue('tree',selection as never),'safeguarded');
 assert.match(topologyStatus(selection as never,false),/Auto safeguarded/);
 assert.equal(topologyControlValue('tree',{...selection,mode:'manual'} as never),'ring');
 assert.match(topologyStatus({...selection,mode:'manual'} as never,false),/Manual override · delivered evidence/);
});
