import test from 'node:test';
import assert from 'node:assert/strict';
import { activityItems, formatMetric, mergeEvents, metricDifference, resultRows } from './workspacePresentation.ts';
import { clearMissionSnapshot, getMissionSelection, inspectMissionTarget, publishMissionSnapshot, toggleMissionTarget } from '../swarm.ts';
import type { ExperimentResult, SwarmEvent, SwarmSnapshot } from '../swarm.ts';
const result=(extra:Partial<ExperimentResult>={}):ExperimentResult=>({topology:'ring',seed:7,generated:10,delivered:8,dropped:2,pdr:.8,average_latency_ms:12,jitter_ms:1,throughput_mbps:.01,average_hops:2,connected_components:1,conservation:true,recovery:[],...extra});
test('inspection cannot silently retarget a command; explicit targets survive inspection and retire correctly',()=>{
 clearMissionSnapshot();const snapshot={session_id:'selection',revision:1,simulation_time:0,nodes:[{id:'a'},{id:'b'}],packet_events:[]} as unknown as SwarmSnapshot;publishMissionSnapshot(snapshot);
 toggleMissionTarget('a');inspectMissionTarget('b');assert.deepEqual(getMissionSelection(),{targets:['a'],inspected:'b'});
 toggleMissionTarget('b');inspectMissionTarget('missing');assert.deepEqual(getMissionSelection().targets,['a','b']);
 publishMissionSnapshot({...snapshot,revision:2,nodes:[snapshot.nodes[1]]});assert.deepEqual(getMissionSelection(),{targets:['b'],inspected:'b'});
 publishMissionSnapshot({...snapshot,session_id:'reset'});assert.deepEqual(getMissionSelection(),{targets:[],inspected:'a'});clearMissionSnapshot();
});
test('metric formatting keeps nulls distinct from zero; delivery deltas use percentage points and matched seeds',()=>{
 assert.equal(formatMetric(result({pdr:null}),'pdr'),'—');assert.equal(formatMetric(result({average_latency_ms:0}),'average_latency_ms'),'0.00 ms');
 assert.equal(metricDifference(result(),result({pdr:.9}),'pdr'),'+10.0 pp');assert.equal(metricDifference(result(),result({seed:42}),'pdr'),'—');
 assert.equal(metricDifference(result({average_latency_ms:null}),result(),'average_latency_ms'),'—');
});
test('seed filtering and metric sorting retain every matching row, stable topology order and nulls last in either direction',()=>{
 const rows=[result({topology:'tree',average_latency_ms:null}),result({seed:42}),result({average_latency_ms:3}),result({topology:'full_mesh',average_latency_ms:0})],original=structuredClone(rows),order=['full_mesh','ring','tree'];
 assert.deepEqual(resultRows(rows,[7],'average_latency_ms','desc',order).map(r=>r.average_latency_ms),[3,0,null]);
 assert.deepEqual(resultRows(rows,[7],'topology','asc',order).map(r=>r.topology),order);assert.deepEqual(resultRows(rows,[],'seed','asc',order),[]);assert.deepEqual(rows,original);
});
test('activity merges cursor duplicates and groups command stages without fabricating timestamps or dropping outcomes',()=>{
 const event=(cursor:number,kind:string,data:Record<string,unknown>):SwarmEvent=>({cursor,kind,data,category:'COMMAND',simulation_time:cursor});
 const submitted=event(1,'command_submitted',{command_id:'cmd',action:'hold',targets:['a']}),ack=event(2,'command_ack',{command_id:'cmd',node:'a'});
 assert.equal(mergeEvents([submitted],[submitted,ack]).length,2);
 const items=activityItems('session',[submitted,submitted,ack],[{command_id:'cmd',action:'hold',outcomes:{a:{status:'acknowledged'}}},{command_id:'retained',action:'resume',outcomes:{a:{status:'expired'}}}],id=>id);
 assert.equal(items.length,2);assert.equal(items[0].time,2);assert.equal(items[0].events.length,2);assert.match(items[0].summary,/acknowledged/);assert.equal(items[1].time,null);
 assert.notEqual(items[0].id,activityItems('other',[submitted,ack],[],id=>id)[0].id);
});
