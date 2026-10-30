import { testCredentials } from './support/credentials.mjs';
const { username: testOperator, password: testPassword } = testCredentials();
import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const filename=process.env.FLARE_STUDY_RESULT_FIXTURE;
test('completed real study artifact renders final cohorts and paired intervals (transport fixture)',async({page})=>{
 test.skip(!filename,'Run after the full study with FLARE_STUDY_RESULT_FIXTURE pointing to its real completed state.json.');
 const artifact=JSON.parse(fs.readFileSync(filename!,'utf8'));expect(artifact.status).toBe('completed');expect(artifact.results).toHaveLength(550);
 // This verifies presentation of real retained evidence, not another backend run.
 await page.route('**/mission/simulation/experiments',async route=>{if(route.request().method()==='POST')await route.fulfill({status:200,json:artifact});else await route.continue();});
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill(testOperator);await page.getByPlaceholder('Password').fill(testPassword);await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await center.getByRole('tab',{name:'Adaptive Study',exact:true}).click();
 const study=center.getByRole('region',{name:'Adaptive resilience study'});await study.getByLabel('Study preset').selectOption('evidence');await study.getByRole('button',{name:'Run adaptive study',exact:true}).click();
 await expect(study.getByText(/Completed · Final results/)).toBeVisible();await expect(study.locator('.experiment-results tbody tr')).toHaveCount(110);
 await study.getByLabel('Study result scenario').selectOption('congestion');await study.getByText('Statistical summary · Auto − fixed policy',{exact:true}).click();
 const statistic=artifact.statistics.find((r:{metric:string;baseline:string;scenario:string})=>r.metric==='pdr'&&r.baseline==='self_healing_mesh'&&r.scenario==='congestion');
 const table=study.getByRole('table').filter({hasText:'Conditioning'});await expect(table.getByRole('row').filter({hasText:'Delivery (pp)'})).toContainText(statistic.mean_difference.toFixed(3));
 await expect(table.getByRole('row').filter({hasText:'Delivery (pp)'})).toContainText(statistic.interval_95.map((n:number)=>n.toFixed(3)).join(' to '));
 for(const seed of artifact.protocol.seeds.slice(1))await study.getByLabel(`Show seed ${seed}`,{exact:true}).uncheck();await expect(study.locator('.experiment-results tbody tr')).toHaveCount(11);
 await study.getByRole('button',{name:'Details Auto (balanced) seed 1001',exact:true}).click();await expect(study.getByText('Deferred packets',{exact:true})).toBeVisible();await expect(study.getByText('Resource proxy',{exact:true})).toBeVisible();
 const directory=process.env.FLARE_ARTIFACT_DIR;if(directory){fs.mkdirSync(directory,{recursive:true});await study.evaluate(e=>{e.scrollTop=0;});await page.screenshot({path:`${directory}/completed-study.png`});}
});
