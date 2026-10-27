import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {missionSetting,resetMission} from './workspace-helpers';
const artifacts=process.env.FLARE_ARTIFACT_DIR||'/private/tmp/flare-resilience-browser';
test('opt-in delivered evidence, pause, manual pin, views, isolated pilot and exports',async({page,request})=>{
 test.setTimeout(180000);fs.mkdirSync(artifacts,{recursive:true});const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const state=async()=>(await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 async function pauseMission(){
  for(let attempt=0;attempt<3;attempt++){
   const receipt=page.waitForResponse(r=>r.url().endsWith('/mission/simulation/control')&&r.request().postDataJSON()?.action==='pause');
   await center.getByRole('button',{name:'Pause mission',exact:true}).click();const response=await receipt;
   if(response.status()===409){
    // An explicit operator retry is allowed only after confirmed rejection.
    // Unknown delivery never enters this branch and is never resubmitted.
    await expect(center.getByRole('alert')).toContainText('Mission revision changed');continue;
   }
   expect(response.ok()).toBeTruthy();await expect.poll(async()=>(await state()).playing).toBe(false);return;
  }
  throw new Error('Three confirmed revision conflicts prevented pause');
 }

 await resetMission(center);await missionSetting(center,'Fleet size','10');
 await center.getByLabel('Topology',{exact:true}).selectOption('safeguarded');
 await expect(center.getByLabel('Topology',{exact:true})).toHaveValue('safeguarded');
 expect((await state()).topology_selection.policy_id).toBe('evidence_resilience_v1');
 await center.getByLabel('Speed',{exact:true}).selectOption('4');await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect.poll(async()=>(await state()).observability.counters.delivered??0,{timeout:30000}).toBeGreaterThan(10);
 await pauseMission();const paused=await state();
 await page.waitForTimeout(600);const frozen=await state();expect(frozen.simulation_time).toBe(paused.simulation_time);expect(frozen.observability).toEqual(paused.observability);
 if(process.env.FLARE_TEST_MISSING_MODELS==='1'){expect(paused.runtime.orchestrator.fl_model_loaded).toBe(false);await center.getByRole('button',{name:'View settings',exact:true}).click();await center.getByText('Service readiness',{exact:true}).click();await expect(center.getByText('BiLSTM: Unavailable',{exact:true})).toBeVisible();await page.screenshot({path:`${artifacts}/models-unavailable.png`});await center.getByRole('button',{name:'Close View settings',exact:true}).click();}
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();await center.getByText('Topology selection details',{exact:true}).click();
 await expect(center.getByRole('region',{name:'Security report delivery'})).toBeVisible();await page.screenshot({path:`${artifacts}/report-diagnostics.png`});await center.getByRole('button',{name:'Close Mission setup',exact:true}).click();
 await center.getByLabel('Topology',{exact:true}).selectOption('ring');await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect.poll(async()=>(await state()).topology,{timeout:20000}).toBe('ring');await expect(center.getByRole('status',{name:'Topology selection',exact:true})).not.toContainText('Requested:');
 await pauseMission();const manual=await state();expect(manual.topology_selection.mode).toBe('manual');expect(manual.topology_selection.policy_id).toBe('evidence_resilience_v1');
 await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas.mission-world-canvas')).toBeVisible();await page.screenshot({path:`${artifacts}/safeguarded-3d.png`});
 await center.getByRole('button',{name:'2D network',exact:true}).click();
 for(const width of [390,687,768,915,1024,1440]){await page.setViewportSize({width,height:850});await page.screenshot({path:`${artifacts}/safeguarded-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();}
 await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await center.getByRole('tab',{name:'Resilience pilot',exact:true}).click();
 const panel=center.locator('#benchmark-panel-resilience');await expect(panel).toContainText('72 runs');
 await panel.getByRole('button',{name:'Run adaptive study',exact:true}).click();await expect(panel.getByRole('button',{name:'Cancel study',exact:true})).toBeVisible();
 await expect.poll(async()=>await panel.textContent(),{timeout:30000}).toMatch(/[1-9]\d* \/ 72 runs/);
 await panel.getByRole('button',{name:'Cancel study',exact:true}).click();await expect(panel).toContainText('Cancelled',{timeout:30000});await expect(panel).toContainText('Partial retained');
 for(const name of ['Export JSON','Export CSV','Export report','Export MANIFEST']){const download=page.waitForEvent('download');await panel.getByRole('button',{name,exact:true}).click();const file=await download;expect(file.suggestedFilename()).toContain('flare-adaptive-study-');await file.saveAs(`${artifacts}/${file.suggestedFilename()}`);}
 await page.screenshot({path:`${artifacts}/pilot-cancelled.png`});
 await center.getByRole('button',{name:'Live mission',exact:true}).click();expect((await state()).session_id).toBe(manual.session_id);
 await resetMission(center);expect((await state()).topology_selection.policy_id).toBe('evidence_resilience_v1');expect((await state()).topology).toBe('ring');
 await center.getByLabel('Topology',{exact:true}).selectOption('auto');expect((await state()).observability).toBeNull();
 fs.writeFileSync(`${artifacts}/state.json`,JSON.stringify({paused,manual,errors},null,2));expect(errors).toEqual([]);
});
