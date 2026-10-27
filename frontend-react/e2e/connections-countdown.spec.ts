import {test,expect} from '@playwright/test';
import type {Page,Locator} from '@playwright/test';
import fs from 'node:fs';
import {resetMission,missionSetting} from './workspace-helpers';

const directory=process.env.FLARE_ARTIFACT_DIR||'/private/tmp/flare-connections-countdown';
fs.mkdirSync(directory,{recursive:true});
async function openMission(page:Page){
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();await resetMission(center);await missionSetting(center,'Fleet size','10');await center.getByLabel('Speed',{exact:true}).selectOption('1');return center;
}
const remaining=async(panel:Locator)=>Number((await panel.getByTestId('attack-remaining').first().innerText()).split('s')[0]);

test('visible countdown follows mission speed, pauses, expires and retains the captured UAV',async({page})=>{
 test.setTimeout(150000);const center=await openMission(page),attack=center.getByRole('region',{name:'Simulation event'});
 await attack.getByLabel('Attack duration',{exact:true}).fill('15');await attack.getByTestId('attack-apply').click();
 await expect(attack.getByTestId('attack-receipt')).toContainText('Active · Spot jam');await expect(attack.getByTestId('attack-remaining')).toHaveText('15.0s remaining');
 await expect(attack.getByText('Paused',{exact:true})).toBeVisible();await page.waitForTimeout(500);expect(await remaining(attack)).toBe(15);
 await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect.poll(()=>remaining(attack)).toBeLessThan(14.5);
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();await expect(attack.getByText('Paused',{exact:true})).toBeVisible();
 const paused=await remaining(attack);await page.waitForTimeout(500);expect(await remaining(attack)).toBe(paused);
 await center.getByRole('tab',{name:'Inspect',exact:true}).click();await center.getByLabel('Inspected UAV',{exact:true}).selectOption('drone_2');await center.getByRole('tab',{name:'Events',exact:true}).click();
 await expect(attack.getByLabel('Attack targets')).toContainText('UAV-002');await expect(attack.getByTestId('attack-receipt')).toContainText('UAV-001');
 await expect(attack.locator('.active-condition')).toContainText('UAV-001');
 await attack.getByRole('button',{name:'Clear spot condition',exact:true}).click();await expect(attack.getByTestId('attack-receipt')).toContainText('Cleared');
 for(const speed of ['0.1','0.25','0.5','1','2','4']){
  await center.getByLabel('Speed',{exact:true}).selectOption(speed);await attack.getByLabel('Attack duration',{exact:true}).fill(speed==='0.1'?'5':Number(speed)>=2?'4':'1');await attack.getByTestId('attack-apply').click();await expect(attack.locator('.active-condition')).toHaveCount(1);
  const initial=await remaining(attack);await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect.poll(()=>remaining(attack)).toBeLessThan(initial);
  if(speed==='0.1'){await center.getByRole('button',{name:'Pause mission',exact:true}).click();await attack.getByRole('button',{name:'Clear spot condition',exact:true}).click();}
  else{await expect(attack.getByTestId('attack-receipt')).toContainText('Expired',{timeout:12000});await expect(attack.getByTestId('attack-receipt').locator('time')).toHaveText(/^T\+\d+\.\ds$/);await expect(attack.locator('.active-condition')).toHaveCount(0);await center.getByRole('button',{name:'Pause mission',exact:true}).click();}
 }
 await expect(attack.getByLabel('Attack duration',{exact:true})).toHaveValue('4');await expect(attack.getByText('No active injections',{exact:true})).toBeVisible();
 await page.screenshot({path:`${directory}/expired-receipt.png`});await resetMission(center);await expect(attack.getByTestId('attack-receipt')).toHaveCount(0);
});

test('short and uncertain submissions reconcile from actual simulator evidence without repeating POST',async({page})=>{
 test.setTimeout(90000);const center=await openMission(page),attack=center.getByRole('region',{name:'Simulation event'});let posts=0;
 await center.getByLabel('Speed',{exact:true}).selectOption('4');await center.getByRole('button',{name:'Play mission',exact:true}).click();await attack.getByLabel('Attack duration',{exact:true}).fill('1');
 await page.route('**/mission/simulation/attacks',async route=>{posts++;const response=await route.fetch();await page.waitForTimeout(1400);await route.fulfill({response});},{times:1});
 await attack.getByTestId('attack-apply').click();await expect(attack.getByTestId('attack-receipt')).toContainText('Expired');await expect(attack.getByTestId('attack-apply')).toBeEnabled();expect(posts).toBe(1);
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();await attack.getByLabel('Attack duration',{exact:true}).fill('60');
 await page.route('**/mission/simulation/attacks',async route=>{posts++;await route.fetch();await route.abort('failed');},{times:1});
 await attack.getByTestId('attack-apply').click();await expect(attack.getByTestId('attack-receipt')).toContainText('Active');await expect(attack.getByTestId('attack-remaining')).toHaveText('60.0s remaining');await page.waitForTimeout(500);expect(posts).toBe(2);
 await attack.getByRole('button',{name:'Clear all conditions',exact:true}).click();
 await page.route('**/mission/simulation/attacks',async route=>{posts++;await route.abort('failed');},{times:1});await attack.getByTestId('attack-apply').click();await expect(attack.getByTestId('attack-receipt')).toContainText('Awaiting confirmation');await expect(attack.getByRole('alert')).toContainText('could not be confirmed');await page.waitForTimeout(800);expect(posts).toBe(3);
});

test('a disconnected countdown freezes until fresh authoritative snapshots return',async({page,context})=>{
 test.setTimeout(60000);const center=await openMission(page),attack=center.getByRole('region',{name:'Simulation event'});
 await attack.getByLabel('Attack duration',{exact:true}).fill('60');await attack.getByTestId('attack-apply').click();await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect.poll(()=>remaining(attack)).toBeLessThan(59.5);
 await context.setOffline(true);
 try{
  await expect(attack.locator('.condition-countdown>span')).toHaveText('Connection stale',{timeout:12000});
  const frozen=await remaining(attack);await page.waitForTimeout(700);expect(await remaining(attack)).toBe(frozen);await expect(attack.getByTestId('attack-apply')).toBeDisabled();
 }finally{await context.setOffline(false);}
 await expect(attack.locator('.condition-countdown>span')).toHaveText('Auto ends',{timeout:20000});await center.getByRole('button',{name:'Pause mission',exact:true}).click();await attack.getByRole('button',{name:'Clear all conditions',exact:true}).click();
});

test('interference ends and route indicators clear automatically, without a Clear request',async({page,request})=>{
 test.setTimeout(90000);const center=await openMission(page);await missionSetting(center,'Radio range','1000');
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const snapshot=async()=>await(await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 const id=(await snapshot()).nodes[0].id;
 const metrics=async()=>{const value=await(await request.get('http://127.0.0.1:18000/swarm/metrics',{headers})).json();return (value.metrics??value)[id];};
 let clearPosts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/attacks/clear'))clearPosts++;});
 const attack=center.getByRole('region',{name:'Simulation event'}),direct=center.locator('.mission-route-option').filter({hasText:'Direct'});
 await attack.getByLabel('Attack duration',{exact:true}).fill('5');await attack.getByTestId('attack-apply').click();await expect(direct).toHaveAttribute('data-interference','active');
 const during=await metrics();expect(during.ew_status.active_attack).toBe('spot');const impairedSinr=during.paths.find((p:{path_id:string})=>p.path_id==='direct').sinr;
 await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect.poll(()=>remaining(attack)).toBeLessThan(3.5);await center.getByRole('button',{name:'Pause mission',exact:true}).click();
 for(const width of [915,390]){
  await page.setViewportSize({width,height:765});await attack.locator('.active-condition').scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/compact-timer-${width}.png`});
  expect(await attack.locator('.active-condition').evaluate(e=>e.getBoundingClientRect().height)).toBeLessThan(145);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 }
 await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect(attack.getByTestId('attack-receipt')).toContainText('Expired',{timeout:12000});await expect(attack.getByText('Condition ended automatically.',{exact:true})).toBeVisible();
 await expect(direct).toHaveAttribute('data-interference','clear');await expect(attack.locator('.active-condition')).toHaveCount(0);expect(clearPosts).toBe(0);
 const after=await metrics();expect(after.ew_status).toEqual({active_attack:'none',jammed_paths:[]});expect(after.paths.find((p:{path_id:string})=>p.path_id==='direct').sinr).toBeGreaterThan(impairedSinr+15);
 await page.setViewportSize({width:915,height:765});await direct.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/automatic-recovery.png`});
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();
});

test('fleet-wide conditions keep the countdown visible and disclose every captured UAV accessibly',async({page})=>{
 test.setTimeout(60000);const center=await openMission(page);await missionSetting(center,'Fleet size','100');
 const attack=center.getByRole('region',{name:'Simulation event'});await attack.getByLabel('Attack UAV scope').selectOption('fleet');await expect(attack.getByLabel('Attack targets')).toContainText('100 UAVs');await attack.getByTestId('attack-apply').click();
 const conditions=attack.getByRole('region',{name:'Active conditions',exact:true});await expect(conditions.getByTestId('attack-remaining')).toHaveText('15.0s remaining');
 const names=conditions.locator('.condition-targets');await expect(names).not.toHaveAttribute('open','');await expect(names.locator('.condition-target-list')).not.toBeVisible();await names.locator('summary').focus();await page.keyboard.press('Enter');await expect(names.locator('.condition-target-list')).toContainText('UAV-100');
 await page.keyboard.press('Enter');await expect(names.locator('.condition-target-list')).not.toBeVisible();await attack.getByTestId('attack-remaining').scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/countdown-fleet-100.png`});
 await attack.getByRole('button',{name:'Clear spot condition',exact:true}).click();await expect(attack.getByTestId('attack-receipt')).toContainText('Cleared');await expect(attack.locator('.active-condition')).toHaveCount(0);await expect(attack.getByTestId('attack-receipt')).toContainText('100 UAVs');
});

test('3D wide connections and active timers remain readable across layouts and DPR',async({page})=>{
 test.setTimeout(120000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const center=await openMission(page),attack=center.getByRole('region',{name:'Simulation event'});await missionSetting(center,'Radio range','1000');
 await attack.getByTestId('attack-apply').click();await center.getByRole('button',{name:'3D world',exact:true}).click();const canvas=center.locator('canvas.mission-world-canvas');
 await expect(canvas).toHaveAttribute('data-link-widths',JSON.stringify({background:1.5,neighbor:2.2,route:3.2,candidate:3.2,recovery:2.2}));
 await expect.poll(async()=>JSON.parse((await canvas.getAttribute('data-link-counts'))??'{}').neighbor).toBeGreaterThan(0);
 const before=await canvas.getAttribute('data-geometries');
 for(const width of [1440,915,687,390]){
  await page.setViewportSize({width,height:width===915?765:900});await canvas.scrollIntoViewIfNeeded();await page.waitForTimeout(500);await page.screenshot({path:`${directory}/connections-${width}.png`});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await attack.getByTestId('attack-remaining').scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/countdown-${width}.png`});
 }
 await page.setViewportSize({width:1440,height:1000});const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:2,mobile:false});
 await page.waitForTimeout(500);await canvas.scrollIntoViewIfNeeded();await expect(canvas).toHaveAttribute('data-render-dpr','1');await page.screenshot({path:`${directory}/connections-dpr2.png`});
 // Three uploads each fixed batch only when it first becomes visible.
 const uploaded=Number(await canvas.getAttribute('data-geometries'));expect(uploaded).toBeGreaterThanOrEqual(Number(before));expect(uploaded).toBeLessThanOrEqual(Number(before)+4);await page.emulateMedia({reducedMotion:'reduce'});
 await center.getByRole('button',{name:'Layers',exact:true}).click();await center.getByRole('checkbox',{name:'Idle links',exact:true}).uncheck();await center.getByRole('button',{name:'Close Layers',exact:true}).click();
 await expect.poll(async()=>JSON.parse((await canvas.getAttribute('data-link-counts'))??'{}').background).toBe(0);
 expect(errors).toEqual([]);
});

test('confirmed Direct, Mesh and Satellite policies retain their distinct 3D transport geometry',async({page,request})=>{
 test.setTimeout(60000);const center=await openMission(page);await missionSetting(center,'Radio range','1000');await center.getByLabel('Topology',{exact:true}).selectOption('full_mesh');
 await center.getByRole('button',{name:'3D world',exact:true}).click();const canvas=center.locator('canvas.mission-world-canvas');
 // Install real policies in the isolated controller; no model verdict or snapshot is fabricated.
 const headers={Authorization:'Bearer antijam-development-sdn-token'};
 const before=await(await request.get('http://127.0.0.1:18080/simulation/state',{headers})).json();
 for(const family of ['direct','mesh','satellite']){
  const state=await(await request.get('http://127.0.0.1:18080/simulation/state',{headers})).json();
  const response=await request.post('http://127.0.0.1:18080/simulation/policies',{headers,data:{policy_id:`visual-${family}`,session_id:state.session_id,revision:state.revision,source:'drone_1',destination:'base',traffic_class:'telemetry',family,hops:['drone_1','base']}});
  expect(response.ok()).toBeTruthy();expect((await response.json()).applied).toBe(true);
  await expect(center.locator('.mission-world-labels')).toContainText(`UAV-001 · INST ${family.toUpperCase()}`);
  await expect.poll(async()=>JSON.parse((await canvas.getAttribute('data-link-counts'))??'{}').route).toBe(family==='satellite'?32:1);
  await page.screenshot({path:`${directory}/connection-${family}.png`});
 }
 await center.getByRole('button',{name:'Camera',exact:true}).click();await center.getByRole('button',{name:'Overview',exact:true}).click();await center.getByRole('button',{name:'Close Camera',exact:true}).click();await page.waitForTimeout(600);await page.screenshot({path:`${directory}/connection-satellite-overview.png`});
 const after=await(await request.get('http://127.0.0.1:18080/simulation/state',{headers})).json();
 expect(after.session_id).toBe(before.session_id);expect(after.simulation_time).toBe(before.simulation_time);expect(after.nodes.map((n:{position:number[]})=>n.position)).toEqual(before.nodes.map((n:{position:number[]})=>n.position));expect(after.statistics).toEqual(before.statistics);
});

test('topology links remain exact, probe highlighting is explicit and connection emphasis is adjustable',async({page,request})=>{
 test.setTimeout(120000);const center=await openMission(page);await missionSetting(center,'Fleet size','30');await missionSetting(center,'Radio range','1000');
 await center.getByRole('button',{name:'3D world',exact:true}).click();const canvas=center.locator('canvas.mission-world-canvas');
 const headers={Authorization:'Bearer antijam-development-sdn-token'};
 const snapshot=async()=>await(await request.get('http://127.0.0.1:18080/simulation/state',{headers})).json();
 const before=await snapshot();
 await expect(canvas).toHaveAttribute('data-probe-hops','[]');
 await center.getByRole('button',{name:'Layers',exact:true}).click();
 await expect(center.getByLabel('Ground-control links',{exact:true})).not.toBeChecked();
 await center.getByLabel('Selected installed route',{exact:true}).uncheck();
 await center.getByRole('button',{name:'Close Layers',exact:true}).click();
 for(const topology of ['full_mesh','partial_mesh','star','ring','tree','cluster_mesh','grid','random_geometric','hybrid_mesh','self_healing_mesh']){
  await center.getByLabel('Topology',{exact:true}).selectOption(topology);await expect(canvas).toHaveAttribute('data-topology',topology);
  const current=await snapshot(),expected=current.links.filter((l:{a:string;b:string})=>l.a!=='base'&&l.b!=='base').length;
  await expect.poll(async()=>{const counts=JSON.parse((await canvas.getAttribute('data-link-counts'))??'{}');return counts.background+counts.neighbor;}).toBe(expected);
  await expect(canvas).toHaveAttribute('data-probe-hops','[]');
  expect(current.nodes.map((n:{position:number[]})=>n.position)).toEqual(before.nodes.map((n:{position:number[]})=>n.position));
 }
 await center.getByLabel('Topology',{exact:true}).selectOption('ring');
 await center.getByRole('button',{name:'Layers',exact:true}).click();
 await center.getByLabel('Connection emphasis',{exact:true}).selectOption('strong');await center.getByRole('button',{name:'Close Layers',exact:true}).click();await expect(canvas).toHaveAttribute('data-link-emphasis','strong');
 for(const width of [1440,915,390]){await page.setViewportSize({width,height:width===1440?1000:765});await canvas.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/topology-ring-strong-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();}
 await center.getByRole('button',{name:'Route probes',exact:true}).click();await expect(center.getByLabel('Highlight probe route',{exact:true})).not.toBeChecked();
 await center.getByLabel('Highlight probe route',{exact:true}).check();await expect.poll(async()=>JSON.parse((await canvas.getAttribute('data-probe-hops'))??'[]').length).toBeGreaterThan(1);
 await center.getByRole('button',{name:'Close Route probes',exact:true}).click();await expect(center.getByLabel('Connection legend')).toContainText('Probe');
 const hops=await canvas.getAttribute('data-probe-hops');await center.getByRole('button',{name:'2D network',exact:true}).click();await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(canvas).toHaveAttribute('data-probe-hops',hops!);
 await center.getByRole('button',{name:'Route probes',exact:true}).click();await center.getByLabel('Highlight probe route',{exact:true}).uncheck();await center.getByRole('button',{name:'Close Route probes',exact:true}).click();await expect(canvas).toHaveAttribute('data-probe-hops','[]');
 await center.getByRole('button',{name:'Layers',exact:true}).click();await center.getByLabel('Connection emphasis',{exact:true}).selectOption('subtle');await center.getByLabel('Ground-control links',{exact:true}).check();await center.getByRole('button',{name:'Close Layers',exact:true}).click();await expect(canvas).toHaveAttribute('data-link-emphasis','subtle');await expect(canvas).toHaveAttribute('data-ground-links','true');
 await page.setViewportSize({width:915,height:765});await canvas.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/topology-ground-links-subtle.png`});
 const after=await snapshot();expect(after.session_id).toBe(before.session_id);expect(after.simulation_time).toBe(before.simulation_time);expect(after.statistics.generated).toBe(before.statistics.generated);
});

test('ordinary RF degradation is optional coloring with inspectable real link quality',async({page,request})=>{
 test.setTimeout(90000);const center=await openMission(page);await missionSetting(center,'Fleet size','30');await missionSetting(center,'Formation','grid');await missionSetting(center,'Radio range','1000');await center.getByLabel('Topology',{exact:true}).selectOption('tree');await resetMission(center);
 const headers={Authorization:'Bearer antijam-development-sdn-token'};const snapshot=async()=>await(await request.get('http://127.0.0.1:18080/simulation/state',{headers})).json();const before=await snapshot();
 const degraded=before.links.filter((l:{a:string;b:string;status:string})=>l.a!=='base'&&l.b!=='base'&&l.status==='degraded');expect(degraded.length).toBeGreaterThan(0);
 await center.getByRole('button',{name:'3D world',exact:true}).click();const canvas=center.locator('canvas.mission-world-canvas');await expect(canvas).toHaveAttribute('data-quality-colors','false');
 await center.getByRole('tab',{name:'Commands',exact:true}).click();await center.getByLabel('Target UAV-001',{exact:true}).check();
 for(const width of [915,390]){
  await page.setViewportSize({width,height:765});await canvas.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/rf-topology-color-${width}.png`});
  await center.getByRole('button',{name:'UAV link quality',exact:true}).click();const panel=center.getByRole('dialog',{name:'UAV link quality',exact:true});await expect(panel).toContainText('Low RF quality alone is not an attack detection.');await expect(panel.locator('tbody tr')).toHaveCount(degraded.length);
  const box=await panel.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(width);expect(box!.y).toBeGreaterThanOrEqual(0);expect(box!.y+box!.height).toBeLessThanOrEqual(765);
  await page.screenshot({path:`${directory}/rf-quality-details-${width}.png`});await center.getByRole('button',{name:'Close UAV link quality',exact:true}).click();
 }
 await page.setViewportSize({width:915,height:765});await center.getByRole('button',{name:'Layers',exact:true}).click();await center.getByLabel('Color links by RF quality',{exact:true}).check();await center.getByRole('button',{name:'Close Layers',exact:true}).click();await expect(canvas).toHaveAttribute('data-quality-colors','true');await canvas.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/rf-quality-color-enabled.png`});
 await center.getByRole('button',{name:'UAV link quality',exact:true}).click();await center.getByRole('dialog',{name:'UAV link quality',exact:true}).getByRole('button').filter({hasText:'↔'}).first().click();await center.getByRole('button',{name:'Close UAV link quality',exact:true}).click();await expect(center.getByRole('tab',{name:'Inspect',exact:true})).toHaveAttribute('aria-selected','true');await expect(center.getByLabel('Inspect link',{exact:true})).toHaveValue(degraded[0].id);await expect(center.locator('.inspected-link')).toContainText('Degraded');
 await center.getByRole('tab',{name:'Commands',exact:true}).click();await expect(center.getByLabel('Target UAV-001',{exact:true})).toBeChecked();await expect(center.locator('.selection-count')).toHaveText('1 command targets');
 const after=await snapshot();expect(after.simulation_time).toBe(before.simulation_time);expect(after.nodes.map((n:{position:number[]})=>n.position)).toEqual(before.nodes.map((n:{position:number[]})=>n.position));expect(after.links).toEqual(before.links);
});

test('unreachable Direct shows range loss instead of a fake 1000 ms measurement and recovers on fresh evidence',async({page,context})=>{
 test.setTimeout(90000);const center=await openMission(page);await missionSetting(center,'Radio range','100');
 const direct=center.locator('.mission-route-option').filter({hasText:'Direct'});await direct.click();await expect(direct.locator('.mission-route-option__latency')).toHaveText('—');await expect(center.locator('.workspace-path-cause')).toContainText('exceeds the 100 m range');
 await center.getByRole('button',{name:'Play mission',exact:true}).click();await expect(direct.locator('.mission-route-option__state')).toHaveText('Out of range',{timeout:20000});await expect(direct.locator('.mission-route-option__threat')).toContainText('Detector');await expect(center.locator('.workspace-radio-context')).toContainText('No injected RF interference');
 await context.setOffline(true);
 try{await expect(center.locator('.workspace-radio-context')).toHaveAttribute('data-interference-context','unknown',{timeout:12000});await expect(direct.locator('.mission-route-option__latency')).toHaveText('—');}finally{await context.setOffline(false);}
 const attack=center.getByRole('region',{name:'Simulation event'});await expect(attack.getByTestId('attack-apply')).toBeEnabled();await attack.getByLabel('Attack duration',{exact:true}).fill('2');await attack.getByTestId('attack-apply').click();await expect(center.locator('.workspace-radio-context')).toHaveAttribute('data-interference-context','injected');await expect(attack.getByTestId('attack-receipt')).toContainText('Expired',{timeout:12000});await expect(center.locator('.workspace-radio-context')).toHaveAttribute('data-interference-context','none');await expect(direct.locator('.mission-route-option__latency')).toHaveText('—');
 await page.setViewportSize({width:915,height:765});await direct.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/no-direct-radio-path.png`});
 await missionSetting(center,'Radio range','1000');await expect(direct.locator('.mission-route-option__latency')).not.toHaveText('—',{timeout:20000});await expect(direct.locator('.mission-route-option__latency')).not.toHaveText('1000 ms');await expect(center.locator('.workspace-path-cause')).not.toContainText('exceeds');await center.getByRole('button',{name:'Pause mission',exact:true}).click();
 for(const width of [390,687,915]){
  await page.setViewportSize({width,height:765});await direct.scrollIntoViewIfNeeded();await center.getByRole('button',{name:'Mission setup',exact:true}).click();
  const close=center.getByRole('button',{name:'Close Mission setup',exact:true}),bounds=await close.boundingBox();expect(bounds!.y).toBeGreaterThanOrEqual(64);
  await page.screenshot({path:`${directory}/mission-drawer-below-header-${width}.png`});await close.click();await expect(center.getByRole('button',{name:'Mission setup',exact:true})).toBeFocused();
 }
 await direct.scrollIntoViewIfNeeded();await page.screenshot({path:`${directory}/direct-reconnected.png`});
});
