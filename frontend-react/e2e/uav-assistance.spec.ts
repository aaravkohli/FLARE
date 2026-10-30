import {resetMission,selectMissionValue,missionControl,signInToMission} from './workspace-helpers';
import { testCredentials } from './support/credentials.mjs';
const { username: testOperator, password: testPassword } = testCredentials();
import {test,expect} from '@playwright/test';
import fs from 'node:fs';

test('assistance is opt-in, independent of safety, retained across reset, and truthful in manual mode',async({page,request})=>{
 test.setTimeout(150000);
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const center=await signInToMission(page);
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username: testOperator,password: testPassword}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const state=async()=>{const r=await request.get('http://127.0.0.1:18000/mission/simulation',{headers});expect(r.ok()).toBeTruthy();return r.json();};
 const setup=async()=>{await center.getByRole('button',{name:'Mission setup',exact:true}).click();await expect(center.getByRole('dialog',{name:'Mission setup'})).toBeVisible();};
 const initial=await state();expect(initial.topology_selection.assistance.enabled).toBe(false);expect(initial.autonomy.mode).toBe('off');
 await setup();await center.getByLabel('Prioritize affected UAVs',{exact:true}).check();await expect.poll(async()=>(await state()).topology_selection.assistance.enabled).toBe(true);
 await expect(center.getByRole('dialog',{name:'Mission setup'})).toContainText('Independent of flight safety');
 await page.locator('body').evaluate(e=>{e.tabIndex=-1;e.focus();});await page.keyboard.press('Escape');await expect(center.getByRole('button',{name:'Mission setup',exact:true})).toBeFocused();
 const active=await state();expect(active.autonomy.mode).toBe('off');
 await selectMissionValue(center,'Topology','ring');await center.getByRole('tab',{name:'Inspect',exact:true}).click();
 await expect(center.getByRole('region',{name:'Affected UAV assistance'})).toContainText('Inactive—manual topology pinned');
 await resetMission(center);
 await expect.poll(async()=>(await state()).session_id).not.toBe(active.session_id);
 let s=await state();expect(s.topology_selection.assistance.enabled).toBe(true);expect(s.topology_selection.assistance.history).toEqual([]);expect(s.topology_selection.mode).toBe('manual');
 for(const policy of ['auto','safeguarded']){
  await selectMissionValue(center,'Topology',policy);
  await expect.poll(async()=>(await state()).topology_selection.policy_id).toBe(policy==='auto'?'balanced_v1':'evidence_resilience_v1');
  expect((await state()).autonomy.mode).toBe('off');expect((await state()).topology_selection.assistance.enabled).toBe(true);
 }
 await selectMissionValue(center,'Speed','4');await missionControl(center,'play');
 await expect.poll(async()=>(await state()).simulation_time,{timeout:45000}).toBeGreaterThan(3);
 await missionControl(center,'pause');await expect.poll(async()=>(await state()).playing).toBe(false);
 s=await state();await page.waitForTimeout(600);expect((await state()).simulation_time).toBe(s.simulation_time);
 await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('.mission-world-canvas')).toBeVisible();await center.getByRole('button',{name:'2D network',exact:true}).click();
 const dir=process.env.FLARE_ARTIFACT_DIR;if(dir){fs.mkdirSync(dir,{recursive:true});for(const width of [390,687,768,915,1024,1440]){await page.setViewportSize({width,height:850});await setup();await page.screenshot({path:`${dir}/assistance-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();await page.keyboard.press('Escape');}}
 await setup();await center.getByLabel('Prioritize affected UAVs',{exact:true}).uncheck();await expect.poll(async()=>(await state()).topology_selection.assistance.enabled).toBe(false);
 expect(errors).toEqual([]);
});
