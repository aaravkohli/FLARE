import {test} from 'node:test';
import assert from 'node:assert/strict';
import {simulationTimestamp,topologyControlValue,topologyStatus} from './topologySelection.ts';
import type {TopologySelection} from '../swarm.ts';
const selection:TopologySelection={mode:'auto',reason:'Emergency recovery of achievable connectivity',evaluated_at:12,switched_at:null,transition_state:'draining',requested_target:'full_mesh',cooldown_remaining_s:8,evidence:'offered_mesh_traffic',fallback:null,candidates:[],blacklisted:[]};
test('Auto displays actual topology separately from requested target',()=>{
 assert.equal(topologyControlValue('ring',selection),'auto');
 assert.equal(topologyControlValue('ring',{...selection,mode:'manual'}),'full_mesh');
 assert.equal(topologyControlValue('chain'),'chain');
});
test('stale and unavailable evidence cannot claim a current automatic choice',()=>{
 assert.equal(topologyStatus(selection,true),'Connection stale · last confirmed topology');
 assert.equal(topologyStatus(undefined,false),'Selection evidence unavailable');
 assert.match(topologyStatus({...selection,fallback:'installation failed'},false),/^Fallback/);
 assert.match(topologyStatus({...selection,mode:'manual'},false),/^Manual override/);
});
test('missing timestamps are unavailable and observed zero remains zero',()=>{
 assert.equal(simulationTimestamp(null),'—');assert.equal(simulationTimestamp(undefined),'—');
 assert.equal(simulationTimestamp(0),'T+0.0s');
});
