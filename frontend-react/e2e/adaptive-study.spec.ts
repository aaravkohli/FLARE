import {signInToMission} from './workspace-helpers';
import { testCredentials } from './support/credentials.mjs';
const { username: testOperator, password: testPassword } = testCredentials();
import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
test('isolated production study submits once, retains partial results and exports without resetting mission',async({page,request})=>{
 test.setTimeout(150000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const center=await signInToMission(page);
 const login=await request.post('http://127.0.0.1:18000/auth/token',{form:{username: testOperator,password: testPassword}});
 const headers={Authorization:`Bearer ${(await login.json()).access_token}`};
 const live=async()=>(await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 const before=await live();
 if(process.env.FLARE_TEST_MISSING_MODELS==='1'){const health=await (await request.get('http://127.0.0.1:18000/health')).json();expect(health.fl_loaded).toBe(false);expect(health.rl_loaded).toBe(false);}
 await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await center.getByRole('tab',{name:'Adaptive Study',exact:true}).click();
 const study=center.getByRole('region',{name:'Adaptive resilience study'});await expect(study.getByText(/165 runs/)).toBeVisible();await center.getByRole('tab',{name:'Adaptive Study',exact:true}).press('ArrowLeft');await expect(center.getByRole('tab',{name:'Fixed topologies',exact:true})).toHaveAttribute('aria-selected','true');await center.getByRole('tab',{name:'Fixed topologies',exact:true}).press('ArrowRight');await expect(study).toBeVisible();
 await study.getByLabel('Study preset').selectOption('evidence');await expect(study.locator('.configuration-summary')).toContainText('550 runs');await study.getByLabel('Study preset').selectOption('pilot');
 let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/experiments'))posts++;});
 await study.getByRole('button',{name:'Run adaptive study',exact:true}).click();await expect(study.getByRole('button',{name:'Cancel study',exact:true})).toBeVisible();
 await center.getByRole('button',{name:'Live mission',exact:true}).click();await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await expect(study).toBeVisible();
 await expect(study.locator('.experiment-results tbody tr').first()).toBeVisible({timeout:100000});
 await study.getByRole('button',{name:'Cancel study',exact:true}).click();await expect(study.getByText(/Cancelled · Partial retained/)).toBeVisible({timeout:15000});expect(posts).toBe(1);
 const download=page.waitForEvent('download');await study.getByRole('button',{name:'Export JSON',exact:true}).click();const file=await download;const path=await file.path();const result=JSON.parse(fs.readFileSync(path!,'utf8'));
 expect(result.mode).toBe('adaptive_study');expect(result.results.length).toBeGreaterThan(0);expect(result.results[0].conservation).toBe(true);expect(result.protocol.total_runs).toBe(165);expect(result.model_inference).toBe('not_run_reference_policy');
 const after=await live();expect(after.session_id).toBe(before.session_id);expect(after.simulation_time).toBe(before.simulation_time);expect(after.nodes.map((n:{id:string;position:number[]})=>[n.id,n.position])).toEqual(before.nodes.map((n:{id:string;position:number[]})=>[n.id,n.position]));expect(after.statistics.generated).toBe(before.statistics.generated);
 for(const format of ['csv','md','manifest']){
  const response=await request.get(`http://127.0.0.1:18000/mission/simulation/experiments/${result.id}/export?format=${format}`,{headers});expect(response.ok()).toBe(true);const text=await response.text();
  if(format==='csv')expect(text.trim().split('\n')).toHaveLength(result.results.length+1);
  if(format==='md'){expect(text).toContain('Partial retained results');expect(text).toContain('Auto − Self-healing Mesh');}
  if(format==='manifest'){const manifest=JSON.parse(text);expect(manifest.source_sha256).toBe(result.source_sha256);expect(manifest.results).toBeUndefined();}
 }
 const artifact=result.artifact_manifest[0];const trace=await request.get(`http://127.0.0.1:18000/mission/simulation/experiments/${result.id}/traces/${artifact.file}`,{headers});expect(trace.ok()).toBe(true);expect(createHash('sha256').update(await trace.body()).digest('hex')).toBe(artifact.sha256);
 await study.getByLabel('Comparison metric').selectOption('average_latency_ms');await study.getByLabel('Study result scenario').selectOption('congestion');await study.getByLabel('Study result scenario').selectOption('normal');await expect(study.getByLabel('Comparison metric')).toHaveValue('average_latency_ms');
 await study.getByText('Statistical summary · Auto − fixed policy',{exact:true}).click();await expect(study.getByLabel('Study statistical baseline')).toBeVisible();
 const artifacts=process.env.FLARE_ARTIFACT_DIR;if(artifacts){fs.mkdirSync(artifacts,{recursive:true});fs.writeFileSync(`${artifacts}/pilot-state.json`,JSON.stringify(result,null,2));for(const width of [390,687,768,915,1024,1440]){await page.setViewportSize({width,height:900});await study.evaluate(e=>{e.scrollTop=0;});await page.screenshot({path:`${artifacts}/adaptive-study-${width}.png`,fullPage:true});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}}
 expect(errors).toEqual([]);
});

test('lost study receipt is never automatically resubmitted',async({page,request})=>{
 const center=await signInToMission(page);
 await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();await center.getByRole('tab',{name:'Adaptive Study',exact:true}).click();
 let submissions=0,identity='';
 await page.route('**/mission/simulation/experiments',async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  submissions++;const accepted=await route.fetch();expect(accepted.ok()).toBe(true);identity=(await accepted.json()).id;
  // Fault injection: backend accepted a real isolated job; only its receipt is lost.
  await route.abort('failed');
 });
 const study=center.getByRole('region',{name:'Adaptive resilience study'});
 await study.getByRole('button',{name:'Run adaptive study',exact:true}).click();
 await expect(study.getByRole('alert')).toContainText('not automatically repeated');await page.waitForTimeout(2000);expect(submissions).toBe(1);
 const token=await request.post('http://127.0.0.1:18000/auth/token',{form:{username: testOperator,password: testPassword}});
 const headers={Authorization:`Bearer ${(await token.json()).access_token}`};
 expect((await request.post(`http://127.0.0.1:18000/mission/simulation/experiments/${identity}/control`,{headers,data:{action:'cancel'}})).ok()).toBe(true);
});
