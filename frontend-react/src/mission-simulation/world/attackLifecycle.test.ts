import test from 'node:test';
import assert from 'node:assert/strict';
import { attackCountdown, attackReceipt, retainAttackReceipt } from './attackLifecycle.ts';
import type { AttackSubmission } from './attackLifecycle.ts';
import type { SwarmAttack, SwarmSnapshot } from '../swarm.ts';

const attack = (changes: Partial<SwarmAttack> = {}): SwarmAttack => ({attack_id:'one',group:'ew',profile:'spot',affected_targets:['uav-1'],effective_paths:['direct'],created_at:10,duration_s:15,expires_at:25,status:'active',outcomes:{},...changes});
const snapshot = (active: SwarmAttack[] = [], history: SwarmAttack[] = [], changes: Partial<SwarmSnapshot> = {}): SwarmSnapshot => ({session_id:'mission',simulation_time:15,playing:true,speed:1,events:[],attacks:{capabilities:{profiles:{},families:[]},active,history},...changes} as SwarmSnapshot);
const submission = (changes: Partial<AttackSubmission> = {}): AttackSubmission => ({session:'mission',id:'one',operation:'inject',profile:'spot',targets:['uav-1'],attackIds:['one'],transport:'accepted',...changes});

test('countdown uses simulation time at every speed and preserves observed zero', () => {
  for (const speed of [.1,.25,.5,1,2,4]) {
    const state = snapshot([attack()], [], {speed,simulation_time:20});
    assert.deepEqual(attackCountdown(attack(), state.simulation_time, state.playing, false), {remaining:5,fraction:1/3,label:'Simulation time'});
  }
  assert.deepEqual(attackCountdown(attack(), 25, true, false), {remaining:0,fraction:0,label:'Awaiting expiry confirmation'});
  assert.equal(attackCountdown(attack(), 40, true, false).remaining,0);
  assert.equal(attackReceipt(submission(), snapshot([attack()],[],{simulation_time:40}))?.status,'active');
});
test('pause and stale evidence label the frozen authoritative value without advancing it', () => {
  assert.deepEqual(attackCountdown(attack(), 20, false, false), {remaining:5,fraction:1/3,label:'Paused'});
  assert.deepEqual(attackCountdown(attack(), 20, true, true), {remaining:5,fraction:1/3,label:'Connection stale'});
  assert.equal(attackCountdown(attack(),20,false,true).label,'Connection stale');
});
test('missing timestamps stay unavailable; missing start suppresses progress; progress is bounded', () => {
  assert.deepEqual(attackCountdown(attack({expires_at:NaN}),20,true,false), {remaining:null,fraction:null,label:'Simulation time'});
  assert.equal(attackCountdown(attack({created_at:undefined}),20,true,false).fraction,null);
  assert.equal(attackCountdown(attack({created_at:25}),20,true,false).fraction,null);
  assert.equal(attackCountdown(attack(),5,true,false).fraction,1);
  assert.equal(attackReceipt(submission(),snapshot([attack({created_at:undefined})]))?.at,null);
  assert.equal(attackReceipt(submission(),snapshot([],[attack({status:'expired'})]))?.at,null);
});
test('submission follows captured attack identity through inspection changes and unrelated conditions', () => {
  const sent = submission();
  assert.deepEqual(attackReceipt(sent,snapshot([attack(),attack({attack_id:'other',affected_targets:['uav-2']})])), {status:'active',at:10});
  assert.deepEqual(sent.targets,['uav-1']);
  assert.equal(attackReceipt(sent,snapshot([attack({attack_id:'other'})]))?.status,'awaiting');
  assert.equal(attackReceipt(sent,snapshot([attack()],[],{session_id:'reset'})),null);
});
test('short injections already expired before acknowledgment show their actual completion time', () => {
  assert.deepEqual(attackReceipt(submission(),snapshot([],[attack({status:'expired',completed_at:25})])), {status:'expired',at:25});
});
test('clear and replacement evidence replaces the old active confirmation and survives retention', () => {
  for (const status of ['expired','cleared','replaced','node_retired']) {
    const sent = submission();
    const receipt = attackReceipt(sent,snapshot([],[attack({status,completed_at:22})]));
    const retained = retainAttackReceipt(sent,receipt);
    assert.deepEqual(attackReceipt(retained,snapshot()),receipt);
    assert.equal(retainAttackReceipt(retained,receipt),retained);
  }
});
test('missing evidence never implies expiry; lifecycle events can confirm terminal status', () => {
  const observed = retainAttackReceipt(submission(),{status:'active',at:10});
  assert.deepEqual(attackReceipt(observed,snapshot()),{status:'unavailable',at:null});
  assert.deepEqual(attackReceipt(observed,snapshot([],[],{events:[{cursor:1,simulation_time:23,category:'SECURITY',kind:'attack_cleared',data:{attack_id:'one',reason:'replaced',targets:['uav-1']}}]})),{status:'replaced',at:23});
});
test('uncertain submissions stay unconfirmed until observed; authoritative evidence supersedes transport errors', () => {
  assert.equal(attackReceipt(submission({transport:'unconfirmed'}),snapshot())?.status,'awaiting');
  assert.equal(attackReceipt(submission({transport:'failed'}),snapshot())?.status,'failed');
  assert.equal(attackReceipt(submission({transport:'unconfirmed'}),snapshot([attack()]))?.status,'active');
});
test('partial clearing confirms only captured targets and acknowledgment does not invent a timestamp', () => {
  const sent = submission({operation:'clear',id:'clear-request',clearAcknowledged:true});
  assert.deepEqual(attackReceipt(sent,snapshot([attack({affected_targets:['uav-2']})])),{status:'cleared',at:null});
  assert.deepEqual(attackReceipt({...sent,acknowledgedAt:21},snapshot()),{status:'cleared',at:21});
  const event = {cursor:2,simulation_time:20,kind:'attack_cleared',category:'SECURITY',data:{attack_id:'one',targets:['uav-1'],reason:'cleared'}};
  assert.equal(attackReceipt({...sent,clearAcknowledged:false},snapshot([attack()],[],{events:[event]}))?.status,'awaiting');
  assert.deepEqual(attackReceipt({...sent,clearAcknowledged:false},snapshot([attack({affected_targets:['uav-2']})],[],{events:[event]})),{status:'cleared',at:20});
});

test('expiry racing an uncertain clear remains expired, never relabeled as a successful clear', () => {
  const sent=submission({operation:'clear',id:'clear-request',transport:'unconfirmed'});
  const event={cursor:3,simulation_time:25,category:'SECURITY',kind:'attack_cleared',data:{attack_id:'one',targets:['uav-1'],reason:'expired'}};
  assert.equal(attackReceipt(sent,snapshot([],[],{events:[event]}))?.status,'awaiting');
  assert.deepEqual(attackReceipt(sent,snapshot([],[attack({status:'expired',completed_at:25})],{events:[event]})),{status:'expired',at:25});
});
