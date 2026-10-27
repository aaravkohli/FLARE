import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {missionSetting} from './workspace-helpers';

let originalSafetyProfile:unknown;
test.afterEach(async({request})=>{
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const read=async()=>(await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 const before=await read();const restored=await request.patch('http://127.0.0.1:18000/mission/simulation/config',{headers,data:{expected_revision:before.revision,autonomy_mode:'off',...(originalSafetyProfile?{safety_profile:originalSafetyProfile}:{})}});expect(restored.ok()).toBeTruthy();
 const ready=await read();const reset=await request.post('http://127.0.0.1:18000/mission/simulation/scenarios/normal',{headers,data:{expected_revision:ready.revision,action:'reset'}});expect(reset.ok()).toBeTruthy();
});

test('opt-in local recovery, frozen clocks, delivered profile, safety latch and view consistency',async({page,request})=>{
 test.setTimeout(180000);
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const state=async()=>{const r=await request.get('http://127.0.0.1:18000/mission/simulation',{headers});expect(r.ok()).toBeTruthy();return r.json();};
 const initial=await state();originalSafetyProfile=initial.autonomy.profile;expect(initial.autonomy.mode).toBe('off');
 const normal=await request.post('http://127.0.0.1:18000/mission/simulation/scenarios/normal',{headers,data:{expected_revision:initial.revision,action:'reset'}});expect(normal.ok()).toBeTruthy();
 await missionSetting(center,'Fleet size','10');await missionSetting(center,'Autonomous recovery','recovery_v1');
 await center.getByLabel('Speed',{exact:true}).selectOption('4');await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect.poll(async()=>{const s=await state();return s.autonomy.nodes.some((n:{stable:boolean})=>n.stable);},{timeout:45000}).toBeTruthy();
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();await expect.poll(async()=>(await state()).playing).toBe(false);const paused=await state();await page.waitForTimeout(700);expect((await state()).simulation_time).toBe(paused.simulation_time);
 const before=await center.locator('canvas').first().getAttribute('data-recovery-edges');
 await center.getByRole('button',{name:'3D world',exact:true}).click();await page.waitForTimeout(600);
 const after=await center.locator('canvas.mission-world-canvas').getAttribute('data-recovery-edges');expect(JSON.parse(after??'[]')).toEqual(JSON.parse(before??'[]'));
 await center.getByRole('button',{name:'2D network',exact:true}).click();
 await missionSetting(center,'Scenario','recovery_total_loss');await center.getByLabel('Speed',{exact:true}).selectOption('4');await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect.poll(async()=>{const s=await state();return s.autonomy.safety.some((n:{latched:boolean})=>n.latched);},{timeout:45000}).toBeTruthy();
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();await expect.poll(async()=>(await state()).playing).toBe(false);
 await center.getByRole('tab',{name:'Inspect',exact:true}).click();await expect(center.getByRole('region',{name:'Local recovery and safety'})).toContainText('Resume required');
 await missionSetting(center,'Autonomous recovery','off');expect((await state()).autonomy.safety.some((s:{latched:boolean})=>s.latched)).toBeTruthy();
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();await center.getByText('Simulated safety profile',{exact:true}).click();await center.getByRole('button',{name:'Edit safety profile',exact:true}).click();await center.getByLabel('Navigation evidence',{exact:true}).selectOption('unknown');await center.getByRole('button',{name:'Send safety profile',exact:true}).click();
 await expect.poll(async()=>(await state()).autonomy.profile.navigation_confidence).toBeNull();const configured=await state();expect(configured.autonomy.safety[0].navigation_confidence).toBe(1);
 // Disabled request controls may temporarily send focus to the document.
 await page.locator('body').evaluate(e=>{e.tabIndex=-1;e.focus();});await page.keyboard.press('Escape');
 await expect(center.getByRole('dialog',{name:'Mission setup',exact:true})).toBeHidden();
 await expect(center.getByRole('button',{name:'Mission setup',exact:true})).toBeFocused();
 const dir=process.env.FLARE_ARTIFACT_DIR;
 if(dir){fs.mkdirSync(dir,{recursive:true});for(const width of [390,687,768,915,1024,1440]){await page.setViewportSize({width,height:900});await page.screenshot({path:`${dir}/recovery-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();}}
});

test('all ten recovery presets are accessible and resolve to authoritative scenarios',async({page,request})=>{
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 const token=await request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await token.json()).access_token}`};
 const names=['direct_jam','satellite_switch_failure','dual_interference','mesh_transition_loss','total_loss','intermittent','switch_instability','multiple_isolation','low_battery_isolation','failed_recovery_landing'];
 for(const name of names){await missionSetting(center,'Scenario',`recovery_${name}`);await expect.poll(async()=>{const s=await (await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();return s.scenario;}).toBe(`recovery_${name}`);}
});
