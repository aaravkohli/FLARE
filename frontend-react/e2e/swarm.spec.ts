import { missionSetting, resetMission, commandOutcomes } from './workspace-helpers';
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
const artifacts=process.env.FLARE_ARTIFACT_DIR||`artifacts/swarm-repair-${new Date().toISOString().replaceAll(':','-')}`;
fs.mkdirSync(artifacts,{recursive:true});
test('authoritative controls, multi-selection, commands, scenarios and fallback',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();await center.getByRole('tab',{name:'Activity',exact:true}).click();await center.getByRole('tab',{name:'Commands',exact:true}).click();
 await resetMission(center);await expect(center.getByRole('button',{name:'Play mission'})).toBeVisible();
 await center.getByRole('button',{name:'Play mission'}).click();await expect(center.getByRole('button',{name:'Pause mission'})).toBeVisible();
 await missionSetting(center,'Fleet size','50');await expect(center.getByLabel('Fleet size',{exact:true})).toHaveValue('50');
 await center.getByLabel('Topology',{exact:true}).selectOption('ring');await expect(center.getByLabel('Topology',{exact:true})).toHaveValue('ring');
 await missionSetting(center,'Formation','v');await expect(center.getByLabel('Formation',{exact:true})).toHaveValue('v');
 await center.getByLabel('Speed',{exact:true}).selectOption('2');await expect(center.getByLabel('Speed',{exact:true})).toHaveValue('2');
 await expect(center.getByRole('img',{name:'Authoritative 2D mission network'})).toBeVisible();
 await center.getByLabel('Search fleet').fill('UAV-001');const first=center.getByRole('checkbox',{name:'Target UAV-001',exact:true});await first.click();await expect(first).toBeChecked();
 await center.getByLabel('Search fleet').fill('');await center.getByRole('checkbox',{name:'Target UAV-002',exact:true}).click({modifiers:['Shift']});await expect(center.getByRole('button',{name:'Send to 2 UAVs'})).toBeEnabled();
 await center.getByLabel('Command',{exact:true}).selectOption('isolate');await center.getByRole('button',{name:'Send to 2 UAVs'}).click();await commandOutcomes(center);await expect(center.locator('.swarm-command-outcome').first()).toContainText('Management Applied');
 await missionSetting(center,'Scenario','jamming');await expect(center.getByLabel('Fleet size',{exact:true})).toHaveValue('50');
 await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toBeVisible();
 await center.screenshot({path:`${artifacts}/swarm-browser.png`});
 expect(errors).toEqual([]);
 const unauthorized=await request.get('http://127.0.0.1:18000/mission/simulation');expect(unauthorized.status()).toBe(401);
});

test('delivered flight commands, lost command link, retirement and canvas cleanup',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();await center.getByRole('tab',{name:'Activity',exact:true}).click();await center.getByRole('tab',{name:'Commands',exact:true}).click();
 await resetMission(center);await missionSetting(center,'Fleet size','10');await center.getByLabel('Speed',{exact:true}).selectOption('4');
 await center.getByRole('button',{name:'Play mission'}).click();await center.getByRole('checkbox',{name:'Target UAV-001',exact:true}).click();
 await center.getByLabel('Command',{exact:true}).selectOption('hold');await center.getByRole('button',{name:'Send to 1 UAV'}).click();
 await commandOutcomes(center);await expect(center.locator('.swarm-command-outcome').first()).toContainText('UAV-001 · Acknowledged',{timeout:15000});
 await center.getByRole('button',{name:'Disconnect command link'}).click();await expect(center.getByText(/Command link lost/, {exact:true}).first()).toBeVisible();
 await center.getByLabel('Command',{exact:true}).selectOption('resume');await center.getByRole('button',{name:'Send to 1 UAV'}).click();
 await commandOutcomes(center);await expect(center.locator('.swarm-command-outcome').first()).toContainText('UAV-001 · Expired',{timeout:15000});
 await center.getByRole('button',{name:'Restore command link'}).click();await expect(center.getByLabel('Command',{exact:true})).toHaveValue('resume');
 await missionSetting(center,'Fleet size','20');await center.getByRole('button',{name:'Clear',exact:true}).click();await center.getByRole('checkbox',{name:'Target UAV-020',exact:true}).click();
 await missionSetting(center,'Fleet size','10');await expect(center.getByText('0 command targets',{exact:true})).toBeVisible();
 await missionSetting(center,'Fleet size','20');await expect(center.getByRole('checkbox',{name:'Target UAV-020',exact:true})).not.toBeChecked();
 for(let i=0;i<3;i++){
  await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toHaveCount(1);
  await center.getByRole('button',{name:'2D network',exact:true}).click();await expect(center.getByRole('img',{name:'Authoritative 2D mission network'})).toBeVisible();
 }
 await page.reload();await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(center).toBeVisible({timeout:30000});await expect(center.locator('canvas')).toHaveCount(1,{timeout:20000});
 expect(errors).toEqual([]);
});
