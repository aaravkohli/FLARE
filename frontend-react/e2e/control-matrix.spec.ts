import { missionSetting, resetMission, commandOutcomes } from './workspace-helpers';
import {test,expect} from '@playwright/test';
import type {Page,APIRequestContext,Locator} from '@playwright/test';
import fs from 'node:fs';
const api='http://127.0.0.1:18000',sim=api+'/mission/simulation';
const dir=process.env.FLARE_ARTIFACT_DIR||`artifacts/repair-matrix-${new Date().toISOString().replaceAll(':','-')}`;
fs.mkdirSync(dir,{recursive:true});
function record(control:string,path:string,effect:unknown,evidence='authenticated_frontend_packet_simulation'){const output=`${dir}/control-matrix.json`;const matrix:unknown[]=fs.existsSync(output)?JSON.parse(fs.readFileSync(output,'utf8')):[];matrix.push({control,backend_path:path,effect,evidence,timestamp:new Date().toISOString()});fs.writeFileSync(output,JSON.stringify(matrix,null,2));}
async function setup(page:Page,request:APIRequestContext){
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible({timeout:30000});await center.getByRole('tab',{name:'Activity',exact:true}).click();await center.getByRole('tab',{name:'Commands',exact:true}).click();
 const auth=await request.post(api+'/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const state=async()=>{const r=await request.get(sim,{headers});expect(r.ok()).toBeTruthy();return r.json();};
 const metrics=async()=>{const r=await request.get(api+'/swarm/metrics',{headers});expect(r.ok()).toBeTruthy();return r.json();};
 const reset=async()=>{
  // Reset retains configuration by design. Establish a reachable command test
  // fleet before resetting positions; do not inherit a previous large mission.
  await missionSetting(center,'Fleet size','10');
  await missionSetting(center,'Formation','circle');
  await missionSetting(center,'Radio range','1000');
  await center.getByLabel('Topology',{exact:true}).selectOption('full_mesh');
  await expect(center.getByLabel('Topology',{exact:true})).toBeEnabled();
  await resetMission(center);await expect(center.getByRole('button',{name:'Play mission',exact:true})).toBeEnabled();
  await expect.poll(async()=>(await state()).nodes.length).toBe(10);
 };
 return {center,state,metrics,headers,reset,errors};
}
const fleetButton=(center:Locator,n='001')=>center.getByRole('group',{name:'Fleet selection'}).getByRole('checkbox',{name:`Target UAV-${n}`,exact:true});

// Keep every finite case, with bounded budgets per fleet. The previous single
// 110-configuration test exhausted its aggregate deadline despite valid effects.
for(const count of [10,20,30,40,50,60,70,75,80,90,100]){
 test(`frontend topology matrix / ${count} UAVs`,async({page,request})=>{
  test.setTimeout(120000);const {center,state,reset,errors}=await setup(page,request);await reset();
  await missionSetting(center,'Fleet size',String(count));await expect.poll(async()=>(await state()).nodes.length).toBe(count);
  for(const topology of ['full_mesh','partial_mesh','star','ring','tree','cluster_mesh','grid','random_geometric','hybrid_mesh','self_healing_mesh']){
   const applied=page.waitForResponse(r=>r.url()===sim+'/config'&&r.request().method()==='PATCH');await center.getByLabel('Topology',{exact:true}).selectOption(topology);expect((await applied).ok()).toBeTruthy();await expect.poll(async()=>(await state()).topology,{timeout:15000}).toBe(topology);
   const s=await state();expect(s.nodes.length).toBe(count);expect(s.links.every((l:any)=>[l.a,l.b].every(n=>n==='base'||s.nodes.some((d:any)=>d.id===n)))).toBeTruthy();record(`${count} UAV / ${topology}`,'PATCH /config',{count,topology,links:s.links.length,revision:s.revision});
  }
  expect(errors).toEqual([]);
 });
}
test('frontend formation / speed / preset matrix',async({page,request})=>{
 test.setTimeout(180000);const {center,state,reset,errors}=await setup(page,request);await reset();
 for(const formation of ['grid','v','line','circle','diamond','wedge','column','distributed','cluster']){
  const before=await state();await missionSetting(center,'Formation',formation);await expect.poll(async()=>(await state()).formation).toBe(formation);const after=await state();expect(after.nodes.map((n:any)=>n.position)).toEqual(before.nodes.map((n:any)=>n.position));record(formation,'PATCH /config',{formation,positions_preserved:true});
 }
 for(const speed of [.1,.25,.5,1,2,4]){await center.getByLabel('Speed',{exact:true}).selectOption(String(speed));await expect.poll(async()=>(await state()).speed).toBe(speed);record(`${speed}×`,'PATCH /config',{speed});}
 for(const scenario of ['normal','large_manet','jamming','poisoning','failures','controller_loss','rerouting']){await missionSetting(center,'Scenario',scenario);await expect.poll(async()=>(await state()).scenario).toBe(scenario);record(scenario,'POST /scenarios/'+scenario,{scenario,playing:(await state()).playing});}
 expect(errors).toEqual([]);
});

test('all 15 commands have visible outcomes and authoritative effects',async({page,request})=>{
 test.setTimeout(180000);const {center,state,reset,errors}=await setup(page,request);
 const actions=['hold','resume','return','altitude','move','waypoints','formation','route','reroute','role','disconnect','reconnect','isolate','restore','emergency'];
 for(const action of actions){
  await reset();await center.getByRole('tab',{name:'Commands',exact:true}).click();await center.getByLabel('Speed',{exact:true}).selectOption('4');await fleetButton(center).click();
  if(action==='formation')await center.getByRole('button',{name:'Select all',exact:true}).click();
  await center.getByLabel('Command',{exact:true}).selectOption(action);
  if(action==='altitude')await center.getByLabel('Command altitude').fill('120');
  if(action==='move'){await center.getByLabel('Command x',{exact:true}).fill('30');await center.getByLabel('Command y',{exact:true}).fill('100');await center.getByLabel('Command z',{exact:true}).fill('50');}
  if(action==='waypoints')await center.getByLabel('Command waypoints').fill('[[0,100,0],[100,100,100]]');
  if(action==='route')await center.getByLabel('Command route').selectOption('hold');
  if(action==='role')await center.getByLabel('Command role').selectOption('gateway');
  if(action==='formation')await center.getByLabel('Command formation').selectOption('v');
  const pending=page.waitForResponse(r=>r.url()===sim+'/commands'&&r.request().method()==='POST');await center.getByRole('button',{name:/^Send to \d+ UAV/}).click();const submitted=await (await pending).json();expect(submitted.command_id).toBeTruthy();
  const management=['disconnect','reconnect','isolate','restore'].includes(action);
  const id=Object.keys(submitted.outcomes)[0];
  if(!management){expect(submitted.outcomes[id].status).toBe('queued');await commandOutcomes(center);await expect(center.getByText('Waiting for mission to play',{exact:true}).first()).toBeVisible();await center.getByRole('button',{name:'Play mission',exact:true}).click();}
  await expect.poll(async()=>(await state()).commands.find((c:any)=>c.command_id===submitted.command_id)?.outcomes[id].status,{timeout:20000}).toBe(management?'management_applied':'acknowledged');
  const s=await state(),n=s.nodes.find((n:any)=>n.id===id),c=s.commands.find((c:any)=>c.command_id===submitted.command_id);
  if(['hold','resume','return','emergency','move','altitude','waypoints'].includes(action))expect(n.mode).toBe({hold:'hold',resume:'patrol',return:'return',emergency:'return',move:'move',altitude:'move',waypoints:'waypoints'}[action]);
  if(action==='altitude')expect(n.target[1]).toBe(120);
  if(action==='move')expect(n.target).toEqual([30,100,50]);
  if(action==='waypoints')expect(n.waypoints.length).toBe(2);
  if(action==='formation')expect(n.formation_slot).toBeTruthy();
  if(action==='route'){expect(n.selected_route).toBe('hold');expect(n.mode).toBe('patrol');}
  if(action==='role')expect(n.role).toBe('gateway');
  if(action==='reroute')expect(typeof c.outcomes[id].result.changed).toBe('boolean');
  if(['disconnect','reconnect'].includes(action))expect(n.available).toBe(action==='reconnect');
  if(['isolate','restore'].includes(action))expect(n.containment).toBe(action==='isolate'?'quarantined':'normal');
  await commandOutcomes(center);await expect(center.locator('.swarm-command-outcome').first()).toContainText(management?'Management Applied':'Acknowledged');
  record(action,'POST /commands',{node:n,outcomes:c.outcomes,session_id:s.session_id});
 }
 expect(errors).toEqual([]);
});

test('every restored attack is injectable, observed and clearable from presentation',async({page,request})=>{
 test.setTimeout(180000);const {center,state,metrics,headers,reset,errors}=await setup(page,request);await center.getByRole('button',{name:'Presentation',exact:true}).click();
 const profiles=[['ew','none'],...['spot','sweep','barrage','smart','reactive','adaptive','fhss','spoofing','gps_spoofing','replay','dos'].map(p=>['ew',p]),...['normal','selective_forwarding','telemetry_falsification','control_flood','replay'].map(p=>['traffic',p]),['fl','poisoned'],['fl','normal']];
 for(const [group,profile] of profiles){
  let relayFlow:{source:string;destination:string}|undefined;
  await reset();await center.getByRole('tab',{name:'Commands',exact:true}).click();await center.getByLabel('Speed',{exact:true}).selectOption('4');await fleetButton(center).click();
  if(profile==='selective_forwarding'){
   await missionSetting(center,'Radio range','350');await missionSetting(center,'Fleet size','50');await missionSetting(center,'Formation','distributed');await center.getByLabel('Topology',{exact:true}).selectOption('partial_mesh');await expect(center.getByLabel('Topology',{exact:true})).toBeEnabled();await resetMission(center);await expect(center.getByRole('button',{name:'Play mission',exact:true})).toBeEnabled();
   const s=await state(),relays=s.flows.flatMap((f:any)=>f.hops.slice(1,-1));expect(relays.length).toBeGreaterThan(0);
   const id=relays.sort((a:string,b:string)=>relays.filter((n:string)=>n===b).length-relays.filter((n:string)=>n===a).length)[0],node=s.nodes.find((n:any)=>n.id===id);
   relayFlow=s.flows.find((f:any)=>f.source!=='base'&&f.hops.slice(1,-1).includes(id));expect(relayFlow).toBeTruthy();
   await fleetButton(center,node.alias.slice(-3)).click();await center.getByRole('button',{name:`Inspect ${node.alias}`,exact:true}).click();
  }
  await center.getByRole('tab',{name:'Events',exact:true}).click();await center.getByLabel('Condition group').selectOption(group);await center.getByLabel('Attack profile').selectOption(profile);if(!['none','normal'].includes(profile))await center.getByLabel('Attack duration',{exact:true}).fill('30');
  if(group==='ew'&&['spot','smart','reactive','adaptive','fhss','spoofing','replay','dos'].includes(profile)){await center.getByLabel('Communication target',{exact:true}).selectOption('satellite');}
  const before=await state();const response=page.waitForResponse(r=>r.url()===sim+'/attacks'&&r.request().method()==='POST');await center.getByTestId('attack-apply').click();const injected=await (await response).json();expect(injected.attack_id).toBeTruthy();
  if(['none','normal'].includes(profile)){expect((await state()).attacks.active.length).toBe(0);record(`${group}/${profile}`,'POST /attacks',{cleared:true});continue;}
  const id=injected.affected_targets[0];
  await expect.poll(async()=>(await state()).attacks.active.map((a:any)=>a.profile)).toContain(profile);
  if(profile==='gps_spoofing'){const n=(await state()).nodes[0];expect(n.position).toEqual(before.nodes[0].position);expect(n.reported_position).not.toEqual(n.position);}
  let relayCursor=(await state()).packet_cursor;
  if(relayFlow){
   // Offer explicit mesh probes while paused, before live DQN telemetry policies
   // can bypass the relay. Observe their real drops in the cursor-based trace;
   // the snapshot's last 40 results can omit them in a busy 50-UAV mission.
   await center.getByRole('button',{name:'Route probes',exact:true}).click();await center.getByLabel('Route source').selectOption(relayFlow.source);await center.getByLabel('Route destination').selectOption(relayFlow.destination);
   for(let i=0;i<12;i++){
    await expect(center.getByRole('button',{name:'Send probe',exact:true})).toBeEnabled({timeout:10000});const pending=page.waitForResponse(r=>r.url()===sim+'/probes'&&r.request().method()==='POST',{timeout:15000});await center.getByRole('button',{name:'Send probe',exact:true}).click();const response=await pending;
    expect(response.ok(),await response.text()).toBeTruthy();expect((await response.json()).packet.route).toContain(id);
    await expect(center.getByRole('button',{name:'Send probe',exact:true})).toBeEnabled();
   }
   await center.getByRole('button',{name:'Close Route probes',exact:true}).click();
  }
  await center.getByRole('button',{name:'Play mission',exact:true}).click();
  let relayDrop=null;
  const relayResults:any[]=[];
  if(profile==='selective_forwarding')await expect.poll(async()=>{
   const response=await request.get(sim+`/packet-events?after=${relayCursor}&limit=5000`,{headers});expect(response.ok()).toBeTruthy();const trace=await response.json();expect(trace.event_gap).toBeFalsy();relayCursor=trace.cursor;
   relayResults.push(...trace.events.filter((e:any)=>e.kind==='packet_result').map((e:any)=>e.data));fs.writeFileSync(`${dir}/relay-probe-results.json`,JSON.stringify({target:id,flow:relayFlow,results:relayResults.filter(p=>p.correlation_id==='probe'||p.reason==='relay_discard')},null,2));
   relayDrop=trace.events.find((e:any)=>e.kind==='packet_result'&&e.data.reason==='relay_discard'&&e.data.route[e.data.hop]===id)?.data??relayDrop;return Boolean(relayDrop);
  },{timeout:15000}).toBeTruthy();
  await expect.poll(async()=>(await state()).simulation_time,{timeout:10000}).toBeGreaterThan(profile==='sweep'?5:2.5);
  const s=await state(),observed=await metrics();const rows=observed.metrics??observed;const row=rows[id];expect(row).toBeTruthy();
  const a=s.attacks.active.find((a:any)=>a.profile===profile);
  if(['dos','control_flood','replay'].includes(profile))expect(a.outcomes[id].generated_packets).toBeGreaterThan(0);
  if(profile==='dos'){expect(s.statistics.generated).toBeGreaterThan(1000);expect(s.statistics.dropped).toBeGreaterThan(0);}
  if(profile==='control_flood')expect(row.security_evidence.control_messages_per_s).toBeGreaterThan(20);
  if(profile==='replay')expect(row.security_evidence.duplicate_sequence_ratio).toBeGreaterThan(.2);
  let spoofedClaim=null;
  if(profile==='spoofing')await expect.poll(async()=>{
   const current=await state();spoofedClaim=current.packet_results.find((p:any)=>p.source===id&&p.claimed_source_mac==='02:ff:00:00:00:01')??spoofedClaim;
   return Boolean(spoofedClaim);
  },{timeout:10000}).toBeTruthy();
  if(['spot','smart'].includes(profile))expect(row.paths.find((p:any)=>p.path_id==='satellite').sinr).toBeLessThan(24);
  if(['reactive','adaptive'].includes(profile))expect(a.outcomes[id].jammed_family).toBeTruthy();
  if(['spoofing','telemetry_falsification'].includes(profile))expect(row.security_evidence.reported_forwarded_packets).toBeGreaterThan(row.security_evidence.controller_rx_packets);
  if(group==='fl')expect(a.status).toBe('awaiting_participating_round');
  record(`${group}/${profile}`,'POST /attacks',{condition:a,statistics:s.statistics,observations:row,relay_drop:relayDrop,spoofed_packet_claim:spoofedClaim,detector_findings:s.nodes.find((n:any)=>n.id===id)?.security??null});
  const cleared=page.waitForResponse(r=>r.url()===sim+'/attacks/clear'&&r.request().method()==='POST');await center.getByRole('button',{name:'Clear all conditions',exact:true}).click();expect((await cleared).ok()).toBeTruthy();await expect.poll(async()=>(await state()).attacks.active.length,{timeout:15000}).toBe(0);
 }
 if((await state()).playing)await center.getByRole('button',{name:'Pause mission',exact:true}).click();
 await center.getByRole('tab',{name:'Events',exact:true}).click();await center.getByLabel('Condition group').selectOption('ew');await center.getByLabel('Attack profile').selectOption('spot');
 await center.getByTestId('attack-apply').click();await expect(center.getByRole('button',{name:'Clear spot condition',exact:true})).toBeVisible();
 await center.screenshot({path:`${dir}/attack-lab-presentation.png`});expect(errors).toEqual([]);
});

test('selection, validation, paused queue, partial failure and conflict retain intent',async({page,request})=>{
 const {center,state,headers,reset,errors}=await setup(page,request);await reset();
 const application=page.getByRole('navigation',{name:'Application',exact:true});await fleetButton(center,'001').check();await fleetButton(center,'002').check();
 await expect(center.getByText('2 command targets',{exact:true})).toBeVisible();await application.getByRole('link',{name:'Fleet',exact:true}).click();const installed=(await state()).flows.find((flow:any)=>flow.source==='drone_1'&&flow.destination==='base'&&flow.traffic_class==='telemetry')?.family;await expect(page.getByRole('table',{name:'Fleet UAVs'}).locator('tbody tr').filter({hasText:'drone_1'}).locator('td').nth(2)).toHaveText(installed?installed.replace(/\b\w/g,(letter:string)=>letter.toUpperCase()):'—');await application.getByRole('link',{name:'Mission',exact:true}).click();await expect(center.getByText('2 command targets',{exact:true})).toBeVisible();
 await center.getByRole('button',{name:'Camera',exact:true}).click();await center.getByRole('button',{name:'Fit full network',exact:true}).click();await center.getByRole('button',{name:'Close Camera',exact:true}).click();await expect(center.getByText('2 command targets',{exact:true})).toBeVisible();
 await expect(center.getByRole('img',{name:'Authoritative 2D mission network'})).toBeVisible();
 await center.getByRole('button',{name:'Clear',exact:true}).click();
 await fleetButton(center,'001').focus();await page.keyboard.press('Space');await fleetButton(center,'002').focus();await page.keyboard.press('Space');
 await expect(center.getByRole('button',{name:'Send to 2 UAVs'})).toBeEnabled();await center.getByRole('button',{name:'Camera',exact:true}).click();await center.getByRole('button',{name:'Fit full network',exact:true}).click();await center.getByRole('button',{name:'Close Camera',exact:true}).click();await expect(center.getByText('2 command targets',{exact:true})).toBeVisible();
 await center.getByLabel('Command',{exact:true}).selectOption('altitude');await center.getByLabel('Command altitude').fill('301');await center.getByRole('button',{name:'Send to 2 UAVs'}).click();await expect(center.getByRole('alert')).toContainText('35–300');expect((await state()).commands.length).toBe(0);
 await center.getByLabel('Command altitude').fill('120');
 await page.route('**/mission/simulation/commands',async route=>{
  const current=await state();await request.patch(sim+'/config',{headers,data:{expected_revision:current.revision,speed:2}});await route.continue();
 },{times:1});
 await center.getByRole('button',{name:'Send to 2 UAVs'}).click();await expect(center.getByRole('alert')).toContainText('revision changed');await expect(center.getByLabel('Command altitude')).toHaveValue('120');expect((await state()).commands.length).toBe(0);
 await center.getByRole('button',{name:'Send to 2 UAVs'}).click();await expect.poll(async()=>(await state()).commands.length).toBe(1);expect(Object.values((await state()).commands[0].outcomes).every((o:any)=>o.status==='queued')).toBeTruthy();
 await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect.poll(async()=>Object.values((await state()).commands[0].outcomes).every((o:any)=>o.status==='acknowledged')).toBeTruthy();
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();await center.getByRole('button',{name:'Clear',exact:true}).click();await fleetButton(center,'002').click();await center.getByLabel('Command',{exact:true}).selectOption('disconnect');await center.getByRole('button',{name:'Send to 1 UAV'}).click();
 await center.getByRole('button',{name:'Select all',exact:true}).click();await center.getByLabel('Command',{exact:true}).selectOption('formation');await center.getByRole('button',{name:'Send to 10 UAVs'}).click();await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect.poll(async()=>{const s=await state(),c=s.commands.at(-1);return Object.values(c.outcomes).some((o:any)=>o.status==='delivery_failed')&&Object.values(c.outcomes).some((o:any)=>o.status==='acknowledged');}).toBeTruthy();
 const s=await state();record('partial formation','POST /commands',{outcomes:s.commands.at(-1).outcomes});
 await resetMission(center);await expect(center.getByText('0 command targets',{exact:true})).toBeVisible();await expect.poll(async()=>(await state()).commands.length).toBe(0);
 expect(errors).toEqual([]);
});

test('browser outage leaves autonomous mission running and reconnect resynchronizes',async({page,request})=>{
 const {center,state,reset,errors}=await setup(page,request);await reset();await center.getByRole('button',{name:'Play mission',exact:true}).click();
 const before=await state();await page.context().setOffline(true);
 await expect(center.getByText(/Snapshot stale/)).toBeVisible({timeout:12000});
 const during=await state();expect(during.simulation_time).toBeGreaterThan(before.simulation_time);expect(during.controller_connected).toBe(true);
 await page.context().setOffline(false);await expect(center.getByText(/Snapshot stale/)).toHaveCount(0,{timeout:20000});
 await fleetButton(center).click();await center.getByRole('button',{name:'Send to 1 UAV'}).click();await expect.poll(async()=>(await state()).commands.at(-1)?.outcomes.drone_1?.status).toBe('acknowledged');
 record('browser reconnect','GET /state + authenticated /ws',{before:before.simulation_time,during:during.simulation_time,after:(await state()).simulation_time,command_link_preserved:true});
 expect(errors).toEqual([]);
});

test('absent FL measurements stay unavailable and measured zero stays zero (contract fixture)',async({page,request})=>{
 const {state,reset,errors}=await setup(page,request);await reset();const session=(await state()).session_id;
 let measured=false;
 await page.route('**/fl/metrics',route=>route.fulfill({json:{session_id:session,last_round:1,latest:{round:1,...(measured?{threat_f1:0}:{})},history:[]}}));
 const reload=async()=>{await page.reload();await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByRole('navigation',{name:'Application',exact:true}).getByRole('link',{name:'Security',exact:true}).click();await page.getByRole('tab',{name:'Monitoring',exact:true}).click();};
 await reload();const metrics=page.getByRole('region',{name:'Security',exact:true}).locator('.federation-evidence');
 await expect(metrics.getByText('Threat classifier F1', {exact:true}).locator('..')).toContainText('—');
 await expect(metrics.getByText('Compression ratio',{exact:true}).locator('..')).toContainText('—');
 measured=true;await reload();await expect(metrics.getByText('Threat classifier F1',{exact:true}).locator('..')).toContainText('0.0000');
 await expect(metrics.getByText('Adaptive clip bound',{exact:true}).locator('..')).toContainText('—');
 record('unavailable FL metrics','GET /fl/metrics',{missing:'Unavailable',measured_zero:'0.0000',fixture:true},'mock_frontend_contract');
 expect(errors).toEqual([]);
});
