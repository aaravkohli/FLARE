import test from 'node:test';
import assert from 'node:assert/strict';
import { probeRouteHops, showGroundLink } from './networkLayers.ts';
import type { NetworkDisplay, RouteInspection } from '../swarm.ts';

const display:NetworkDisplay={packets:true,inactive:true,ranges:false,hops:[],groundLinks:false};
const route:RouteInspection={session_id:'s',network_revision:1,source:'a',destination:'c',candidate_hops:['a','b','c'],installed_hops:[],reachable:true,routing_source:'mesh'};
test('probe data never highlights a route without explicit intent',()=>{
 assert.deepEqual(probeRouteHops(route,false),[]);
 assert.deepEqual(probeRouteHops(route,true),['a','b','c']);
 assert.deepEqual(probeRouteHops({...route,installed_hops:['a','c']},true),['a','c']);
 assert.deepEqual(probeRouteHops({...route,reachable:false},true),[]);
 assert.deepEqual(probeRouteHops(null,true),[]);
});
test('ground-control layer preserves topology and explicitly inspected endpoint routes',()=>{
 assert.equal(showGroundLink('a','b',display),true);
 assert.equal(showGroundLink('a','base',display),false);
 assert.equal(showGroundLink('base','a',display),false);
 assert.equal(showGroundLink('a','base',display,true),true);
 assert.equal(showGroundLink('a','base',{...display,groundLinks:true}),true);
 // Non-workspace callers retain their embedded-control defaults.
 assert.equal(showGroundLink('a','base',{...display,groundLinks:undefined}),true);
});
