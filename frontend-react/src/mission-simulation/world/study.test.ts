import {test} from 'node:test';
import assert from 'node:assert/strict';
import {studyResultView,pairedValue} from '../study.ts';
import type {StudyState} from '../study.ts';
test('study views keep policies distinct and do not mutate or filter exports',()=>{
 const job={id:'study',mode:'adaptive_study',status:'cancelled',progress:.1,protocol:{id:'adaptive_resilience_v1',preset:'pilot',total_runs:165,seeds:[7,42,99],policies:['auto','ring'],scenarios:['normal','mobility']},results:[{scenario:'normal',policy_id:'auto',topology:'grid',seed:7,pdr:0},{scenario:'mobility',policy_id:'ring',topology:'ring',seed:7,pdr:null}]} as StudyState;
 const original=JSON.stringify(job);const view=studyResultView(job,'normal')!;
 assert.equal(view.results.length,1);assert.equal(view.results[0].topology,'auto');assert.equal(view.results[0].pdr,0);assert.equal(view.status,'cancelled');
 assert.equal(view.configuration.duration_s-view.configuration.warmup_s,60);
 assert.equal(JSON.stringify(job),original);assert.equal(studyResultView({...job,protocol:undefined},'normal'),null);
});
test('paired display preserves zero and missing intervals',()=>{
 assert.equal(pairedValue(0),'0.000');assert.equal(pairedValue(null),'—');assert.equal(pairedValue(undefined),'—');assert.equal(pairedValue(-2),'-2.000');
});
