// A real round, armed through the authenticated Attack Lab before dispatch.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs';
const api=process.env.FLARE_VALIDATION_API||'http://127.0.0.1:18200',front=process.env.FLARE_VALIDATION_FRONTEND||'http://127.0.0.1:15373';
const dir=process.env.FLARE_ARTIFACT_DIR||`artifacts/repair-flower-ui-${Date.now()}`;fs.mkdirSync(dir,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.FLARE_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=metal']});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(front+'#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 await center.getByRole('button',{name:'Select all',exact:true}).click();await center.getByLabel('Command',{exact:true}).selectOption('route');await center.getByLabel('Command route').selectOption('satellite');await center.getByRole('button',{name:'Send to 30 UAVs'}).click();
 await center.getByRole('group',{name:'Fleet selection'}).getByRole('button',{name:/^UAV-001/}).click();await center.getByLabel('Condition group').selectOption('fl');await center.getByLabel('Attack profile').selectOption('poisoned');await center.getByLabel('Attack duration').fill('300');await center.getByRole('button',{name:'Apply condition to 1 UAV',exact:true}).click();
 await expect(center.getByLabel('Active conditions')).toContainText('Awaiting Participating Round');
 await center.getByRole('button',{name:'Play mission',exact:true}).click();
 const auth=await context.request.post(api+'/auth/token',{form:{username:'admin',password:'antijam2026'}}),headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const events=[];let cursor=0,result;
 const started=Date.now();while(Date.now()-started<180000){
  const r=await context.request.get(api+`/mission/simulation/events?after=${cursor}`,{headers});expect(r.ok()).toBeTruthy();const data=await r.json();events.push(...data.events);cursor=data.cursor;
  result=events.find(e=>e.kind==='aggregation_result'&&e.data.security_audits?.length);if(result)break;await page.waitForTimeout(200);
 }
 expect(result).toBeTruthy();const armed=events.find(e=>e.kind==='poisoning_armed'&&e.data.attack_id);expect(armed).toBeTruthy();
 const audit=result.data.security_audits.find(a=>a.client_id===armed.data.client_id);expect(audit).toBeTruthy();expect(['REJECTED','DOWN_WEIGHTED']).toContain(audit.action);
 await center.screenshot({path:`${dir}/flower-ui.png`});expect(errors).toEqual([]);
 const report={actual_flower:true,evidence:'controlled_simulation',frontend_condition:true,armed:armed.data,aggregation:result.data,errors,command:['node','e2e/flower-ui.mjs'],timestamp:new Date().toISOString()};
 fs.writeFileSync(`${dir}/flower-ui.json`,JSON.stringify(report,null,2));fs.writeFileSync(`${dir}/events.jsonl`,events.map(e=>JSON.stringify(e)).join('\n'));console.log(JSON.stringify(report));
}finally{await context.close();await browser.close();}
