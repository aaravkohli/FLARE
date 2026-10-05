import { test, expect } from '@playwright/test';
import fs from 'node:fs';
const artifacts=process.env.FLARE_ARTIFACT_DIR||`artifacts/swarm-repair-${new Date().toISOString().replaceAll(':','-')}`;
fs.mkdirSync(artifacts,{recursive:true});
test('authoritative controls, multi-selection, commands, scenarios and fallback',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 await center.getByRole('button',{name:'Reset',exact:true}).click();await expect(center.getByRole('button',{name:'Play mission'})).toBeVisible();
 await center.getByRole('button',{name:'Play mission'}).click();await expect(center.getByRole('button',{name:'Pause mission'})).toBeVisible();
 await center.getByLabel('Fleet size').selectOption('50');await expect(center.getByLabel('Fleet size')).toHaveValue('50');
 await center.getByLabel('Topology',{exact:true}).selectOption('ring');await expect(center.getByLabel('Topology',{exact:true})).toHaveValue('ring');
 await center.getByLabel('Formation',{exact:true}).selectOption('v');await expect(center.getByLabel('Formation',{exact:true})).toHaveValue('v');
 await center.getByLabel('Speed',{exact:true}).selectOption('2');await expect(center.getByLabel('Speed',{exact:true})).toHaveValue('2');
 await center.getByRole('button',{name:'2D fallback',exact:true}).click();await expect(center.getByRole('img',{name:'Authoritative 2D mission network'})).toBeVisible();
 await center.getByLabel('Search fleet').fill('UAV-001');const first=center.getByRole('button',{name:/^UAV-001/});await first.click();await expect(first).toHaveAttribute('aria-pressed','true');
 await center.getByLabel('Search fleet').fill('');await center.getByRole('button',{name:/^UAV-002/}).click({modifiers:['Shift']});await expect(center.getByRole('button',{name:'Send to 2 UAVs'})).toBeEnabled();
 await center.getByLabel('Command',{exact:true}).selectOption('isolate');await center.getByRole('button',{name:'Send to 2 UAVs'}).click();await expect(center.getByText(/management_applied/).first()).toBeVisible();
 await center.getByLabel('Scenario',{exact:true}).selectOption('jamming');await expect(center.getByLabel('Fleet size')).toHaveValue('50');
 await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toBeVisible();
 await center.screenshot({path:`${artifacts}/swarm-browser.png`});
 expect(errors).toEqual([]);
 const unauthorized=await request.get('http://127.0.0.1:18000/mission/simulation');expect(unauthorized.status()).toBe(401);
});

test('delivered flight commands, lost command link, retirement and canvas cleanup',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 await center.getByRole('button',{name:'Reset',exact:true}).click();await center.getByLabel('Fleet size').selectOption('10');await center.getByLabel('Speed',{exact:true}).selectOption('4');
 await center.getByRole('button',{name:'Play mission'}).click();await center.getByRole('button',{name:/^UAV-001/}).click();
 await center.getByLabel('Command',{exact:true}).selectOption('hold');await center.getByRole('button',{name:'Send to 1 UAV'}).click();
 await expect(center.getByText(/UAV-001: acknowledged/).first()).toBeVisible({timeout:15000});
 await center.getByRole('button',{name:'Disconnect command link'}).click();await expect(center.getByText(/autonomous flight/).first()).toBeVisible();
 await center.getByLabel('Command',{exact:true}).selectOption('resume');await center.getByRole('button',{name:'Send to 1 UAV'}).click();
 await expect(center.getByText(/UAV-001: expired/).first()).toBeVisible({timeout:15000});
 await center.getByRole('button',{name:'Restore command link'}).click();await expect(center.getByText('hold',{exact:true}).first()).toBeVisible();
 await center.getByLabel('Fleet size').selectOption('20');await center.getByRole('button',{name:/^UAV-020/}).click();
 await center.getByLabel('Fleet size').selectOption('10');await expect(center.getByText('0 selected',{exact:true})).toBeVisible();
 await center.getByLabel('Fleet size').selectOption('20');await expect(center.getByRole('button',{name:/^UAV-020/})).toHaveAttribute('aria-pressed','false');
 for(let i=0;i<3;i++){
  await center.getByRole('button',{name:'2D fallback',exact:true}).click();await expect(center.locator('canvas')).toHaveCount(0);
  await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toHaveCount(1);
 }
 await page.reload();await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(center).toBeVisible({timeout:30000});await expect(center.locator('canvas')).toHaveCount(1,{timeout:20000});
 expect(errors).toEqual([]);
});
