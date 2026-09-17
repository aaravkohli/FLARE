/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import type { MissionTelemetryPayload } from '../types.ts';
import { routeCurve, curvePoint } from './networkGeometry.ts';
import { networkRouteState } from './networkState.ts';
import { advancePacketPhase, PACKET_CAPACITY, packetSample } from './PacketStream.ts';
const fallback = {droneId:'drone_1',route:'direct',threatLevel:'LOW',reward:0,step:1,noSafeRoute:false,availablePaths:['direct','satellite','mesh']};
function payload(installed: string | null, requested='direct', applied=true): MissionTelemetryPayload {
  return {type:'telemetry',timestamp:1,decision:{path_name:installed||requested,requested_path:requested,threat_level:'LOW',reward:0,step:1,sdn_applied:applied},
    metrics:{drone_id:'drone_1',timestamp:1,source:'synthetic',paths:[{path_id:'direct',rssi:-60,sinr:20,pdr:.9,latency:30,packet_loss:.1}]},
    event:{decision:{installed_path:installed,requested_path:requested},sdn:{applied}}};
}
test('explicit null installation is never replaced by legacy/requested route',()=>{
  const state=adaptMissionSimulationState(payload(null),fallback);
  assert.equal(state.installedRoute,null);
  assert.equal(networkRouteState(state,'direct','drone_1').forwarding,false);
  assert.equal(networkRouteState(state,'direct','drone_1').candidate,true);
  assert.equal(adaptMissionSimulationState(null,fallback).installedRoute,null);
});
test('pending, installed, failed update, HOLD and recovery map without a visual enforcement delay',()=>{
  const pending=adaptMissionSimulationState(payload('direct','satellite',false),fallback);
  assert.equal(networkRouteState(pending,'direct','drone_1').forwarding,true);
  assert.equal(networkRouteState(pending,'satellite','drone_1').candidate,true);
  const installed=adaptMissionSimulationState(payload('satellite','satellite'),fallback);
  assert.equal(networkRouteState(installed,'direct','drone_1').forwarding,false);
  assert.equal(networkRouteState(installed,'satellite','drone_1').forwarding,true);
  const hold={...installed,installedRoute:'hold' as const};
  assert.equal(networkRouteState(hold,'satellite','drone_1').forwarding,false);
  assert.equal(networkRouteState({...installed,networkAction:'hold'},'satellite','drone_1').forwarding,false);
  assert.equal(networkRouteState(installed,'satellite','drone_2').forwarding,false);
  assert.equal(networkRouteState(installed,'satellite','drone_1').forwarding,true);
});
test('mesh never invents an animated forwarding hop and warnings use runtime flags',()=>{
  const state=adaptMissionSimulationState(payload('mesh','mesh'),fallback);
  state.routes[2].safe=false; state.routes[2].jammed=true;
  const view=networkRouteState(state,'mesh','drone_1');
  assert.equal(view.installed,true);assert.equal(view.forwarding,false);assert.equal(view.unsafe,true);assert.equal(view.degraded,true);
});
test('dynamic Direct and Satellite curves meet current endpoints exactly',()=>{
  const ground={x:-280,y:5,z:170}, a={x:10,y:70,z:20}, satellite={x:200,y:280,z:-200};
  const direct=routeCurve(a,ground), via=routeCurve(a,ground,satellite);
  assert.deepEqual(curvePoint(direct,0),a);assert.deepEqual(curvePoint(direct,1),ground);
  assert.deepEqual(curvePoint(via,.5),satellite);assert.deepEqual(curvePoint(via,1),ground);
  const moved={...a,x:80}, movedSat={...satellite,x:201};
  assert.deepEqual(curvePoint(routeCurve(moved,ground),0),moved);
  assert.deepEqual(curvePoint(routeCurve(moved,ground,movedSat),.5),movedSat);
  for(let i=0;i<=100;i++) assert.ok(Object.values(curvePoint(via,i/100)).every(Number.isFinite));
});
test('packet timing uses valid latency, changes speed without phase jumps, and pause freezes phase',()=>{
  const fast=advancePacketPhase(0,.1,20), slow=advancePacketPhase(0,.1,200);
  assert.ok(fast>slow);
  assert.equal(advancePacketPhase(fast,0,500),fast);
  assert.equal(advancePacketPhase(fast,.1,undefined),fast);
  assert.equal(packetSample(1,0,undefined,.1),null);
  assert.equal(packetSample(1,0,NaN,.1),null);
  assert.ok(advancePacketPhase(fast,.1,200)>fast);
  let a=0,b=0;for(let i=0;i<60;i++)a=advancePacketPhase(a,1/60,50);for(let i=0;i<30;i++)b=advancePacketPhase(b,1/30,50);
  assert.ok(Math.abs(a-b)<1e-12);
});
test('bounded packet slots have deterministic metric-driven loss, with no invented loss on missing data',()=>{
  assert.equal(PACKET_CAPACITY,8);
  assert.deepEqual(packetSample(.7,0,30,.5),packetSample(.7,0,30,.5));
  assert.equal(packetSample(.7,0,30,1)?.visible,false);
  assert.equal(packetSample(.7,0,30,0)?.visible,true);
  assert.equal(packetSample(.7,0,30,undefined)?.failed,false);
  assert.equal(packetSample(.7,0,30,NaN)?.failed,false);
});
