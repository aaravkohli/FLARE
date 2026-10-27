import {test,expect} from '@playwright/test';
import fs from 'node:fs';

test('completed resilience pilot renders real per-seed results and intervals (transport fixture)',async({page})=>{
 const file=process.env.FLARE_RESILIENCE_RESULT_FIXTURE;
 test.skip(!file,'Requires a completed real 72-run pilot artifact. This is presentation coverage, not a simulated backend run.');
 const artifact=JSON.parse(fs.readFileSync(file!,'utf8'));
 expect(artifact.status).toBe('completed');expect(artifact.results).toHaveLength(72);
 await page.route('**/mission/simulation/experiments',async route=>{
  if(route.request().method()==='POST')await route.fulfill({status:200,json:artifact});else await route.continue();
 });
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await center.getByRole('tab',{name:'Resilience pilot',exact:true}).click();
 const panel=center.locator('#benchmark-panel-resilience');await panel.getByRole('button',{name:'Run adaptive study',exact:true}).click();
 await expect(panel).toContainText('Completed · Final results');await expect(panel.locator('.experiment-results tbody tr')).toHaveCount(12);
 await panel.getByLabel('Study result scenario').selectOption('congestion');await panel.getByText('Statistical summary · Combined − ablation',{exact:true}).click();
 const statistic=artifact.statistics.find((r:{scenario:string;baseline:string;metric:string})=>r.scenario==='congestion'&&r.baseline==='balanced_control'&&r.metric==='pdr');
 const table=panel.getByRole('table').filter({hasText:'Conditioning'});
 await expect(table.getByRole('row').filter({hasText:'Delivery (pp)'})).toContainText(statistic.mean_difference.toFixed(3));
 await expect(table.getByRole('row').filter({hasText:'Delivery (pp)'})).toContainText(statistic.interval_95.map((v:number)=>v.toFixed(3)).join(' to '));
 await panel.getByLabel('Show seed 42',{exact:true}).uncheck();await panel.getByLabel('Show seed 99',{exact:true}).uncheck();
 await expect(panel.locator('.experiment-results tbody tr')).toHaveCount(4);
 await panel.getByRole('button',{name:'Details Combined safeguards seed 7',exact:true}).click();
 await expect(panel.getByText('Fresh eligible samples',{exact:true})).toBeVisible();
 const directory=process.env.FLARE_ARTIFACT_DIR;
 if(directory){fs.mkdirSync(directory,{recursive:true});await page.screenshot({path:`${directory}/completed-resilience-pilot.png`});}
});
