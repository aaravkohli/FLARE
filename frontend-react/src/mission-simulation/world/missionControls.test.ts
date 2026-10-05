import test from 'node:test';
import assert from 'node:assert/strict';
import {publishMissionSnapshot,clearMissionSnapshot,getMissionSelection,selectMissionTarget,selectAllMissionTargets,clearMissionTargets} from '../swarm.ts';
import type {SwarmSnapshot} from '../swarm.ts';
import {commandParameters} from '../commandParameters.ts';
import {MissionClient} from '../missionClient.ts';
const state=(session='selection',revision=1)=>({schema_version:'mission_snapshot_v1',session_id:session,revision,simulation_time:0,nodes:[{id:'one'},{id:'two'}]} as SwarmSnapshot);

test('shared targets are additive, preserved while inspecting, pruned on retirement and cleared on reset',()=>{
 clearMissionSnapshot();publishMissionSnapshot(state());selectMissionTarget('one');selectMissionTarget('two',true);
 assert.deepEqual(getMissionSelection(),{targets:['one','two'],inspected:'two'});
 selectMissionTarget('one',true);assert.deepEqual(getMissionSelection().targets,['two']);
 publishMissionSnapshot({...state(),nodes:[{id:'one'}]} as SwarmSnapshot);assert.deepEqual(getMissionSelection(),{targets:[],inspected:'one'});
 selectAllMissionTargets();clearMissionTargets();assert.equal(getMissionSelection().inspected,'one');
 selectAllMissionTargets();publishMissionSnapshot(state('reset'));assert.deepEqual(getMissionSelection().targets,[]);
});

test('flight coordinates and waypoints reject empty, nonfinite, out of bounds and malformed values',()=>{
 for(const altitude of ['','NaN','34','301'])assert.throws(()=>commandParameters('altitude',{altitude}));
 assert.deepEqual(commandParameters('move',{x:'-10000',y:'35',z:'10000'}),{x:-10000,y:35,z:10000});
 assert.throws(()=>commandParameters('move',{x:'10001',y:'100',z:'0'}));
 for(const points of ['[]','[[0,34,0]]','[["0",100,0]]','invalid'])assert.throws(()=>commandParameters('waypoints',{points}));
 assert.deepEqual(commandParameters('waypoints',{points:'[[0,100,0]]',loop:true}),{points:[[0,100,0]],loop:true});
});

test('revision conflicts refresh current state without replaying commands or dropping user parameters',async()=>{
 clearMissionSnapshot();publishMissionSnapshot(state('conflict'));const original=globalThis.fetch;const calls:string[]=[];
 globalThis.fetch=async(url,options)=>{calls.push(`${options?.method??'GET'} ${url}`);return new Response(JSON.stringify(options?.method?{detail:'stale revision'}:state('conflict',2)),{status:options?.method?409:200});};
 try{const client=new MissionClient('http://test','token',publishMissionSnapshot);await assert.rejects(client.mutate('/commands',{session_id:'conflict',command_id:'fixed',action:'altitude',parameters:{altitude:120}}),/revision changed/);assert.equal(calls.filter(c=>c.startsWith('POST')).length,1);}finally{globalThis.fetch=original;}
});

test('uncertain command submission recovers by command ID and never repeats POST',async()=>{
 clearMissionSnapshot();publishMissionSnapshot(state('uncertain'));const original=globalThis.fetch;const calls:string[]=[];
 globalThis.fetch=async(url,options)=>{calls.push(`${options?.method??'GET'} ${url}`);if(options?.method)throw new TypeError('Connection interrupted');return new Response(JSON.stringify(String(url).endsWith('/commands/stable-id')?{command_id:'stable-id',outcomes:{one:{status:'queued'}}}:state('uncertain')));};
 try{const client=new MissionClient('http://test','token',publishMissionSnapshot);const result=await client.mutate('/commands',{session_id:'uncertain',command_id:'stable-id'});assert.equal(result.command_id,'stable-id');assert.equal(calls.filter(c=>c.startsWith('POST')).length,1);}finally{globalThis.fetch=original;}
});
