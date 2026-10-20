import { chromium } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
const duration=Number(process.env.FLARE_PROFILE_SECONDS||600);
const warmup=Number(process.env.FLARE_PROFILE_WARMUP_SECONDS||5);
const artifacts=process.env.FLARE_ARTIFACT_DIR||`artifacts/repair-performance-${new Date().toISOString().replaceAll(':','-')}`;
fs.mkdirSync(artifacts,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.FLARE_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=metal']});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:15173/?profile=1#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await center.waitFor();
 const auth=await context.request.post('http://127.0.0.1:18000/auth/token',{form:{username:'admin',password:'antijam2026'}});const token=(await auth.json()).access_token;const headers={Authorization:`Bearer ${token}`};
 let mission=await (await context.request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 const configured=await context.request.patch('http://127.0.0.1:18000/mission/simulation/config',{headers,data:{expected_revision:mission.revision,count:100,topology:'manet',formation:'distributed',speed:1,seed:42}});
 if(!configured.ok())throw new Error('Profile configuration was rejected');mission=await configured.json();
 const reset=await context.request.post('http://127.0.0.1:18000/mission/simulation/control',{headers,data:{action:'reset',expected_revision:mission.revision}});
 if(!reset.ok())throw new Error('Profile reset was rejected');await page.waitForTimeout(500);
 await center.getByRole('button',{name:'Presentation',exact:true}).click();await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await page.waitForTimeout(warmup*1000);
 const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');
 const retained=[];
 async function retainedHeap(elapsed){
  await cdp.send('HeapProfiler.collectGarbage');
  const metric=await cdp.send('Performance.getMetrics');
  retained.push({elapsed_s:elapsed,heap_bytes:metric.metrics.find(m=>m.name==='JSHeapUsedSize')?.value});
 }
 await retainedHeap(0);
 const cpuProfiling=process.env.FLARE_PROFILE_CPU==='1';
 if(cpuProfiling){await cdp.send('Profiler.enable');await cdp.send('Profiler.start');}
 await page.evaluate(()=>{const c=document.querySelector('.swarm-center canvas');if(c?.__flareFrameTimes)c.__flareFrameTimes.count=0;});
 const started=Date.now(),samples=[];
 while(Date.now()-started<duration*1000){await page.waitForTimeout(Math.min(10000,Math.max(1,duration*1000-(Date.now()-started))));
  const metric=await cdp.send('Performance.getMetrics');const memory=metric.metrics.find(m=>m.name==='JSHeapUsedSize')?.value;
  const canvas=await center.locator('canvas').evaluate(c=>({average:Number(c.dataset.fpsAverage),low:Number(c.dataset.fpsLow)}));
  samples.push({elapsed_s:(Date.now()-started)/1000,heap_bytes:memory,...canvas});
  if(samples.length%6===0){await retainedHeap((Date.now()-started)/1000);console.log(JSON.stringify({...samples.at(-1),retained_heap_bytes:retained.at(-1).heap_bytes}));}
 }
 const frames=await page.evaluate(()=>{const p=document.querySelector('.swarm-center canvas')?.__flareFrameTimes;return p?Array.from(p.values.slice(0,Math.min(p.count,p.values.length))):[];});
 if(cpuProfiling){const cpu=await cdp.send('Profiler.stop');fs.writeFileSync(`${artifacts}/renderer.cpuprofile`,JSON.stringify(cpu.profile));}
 fs.writeFileSync(`${artifacts}/frame-times.json`,JSON.stringify({unit:'milliseconds',frames}));
 const fps=frames.filter(t=>t>0).map(t=>1000/t).sort((a,b)=>a-b);
 const average=frames.length?frames.length*1000/frames.reduce((a,b)=>a+b,0):0,low=fps[Math.floor(fps.length*.05)]||0;
 const elapsed=(Date.now()-started)/1000;
 await retainedHeap(elapsed);
 const finalState=await context.request.get('http://127.0.0.1:18000/mission/simulation',{headers});
 if(!finalState.ok())throw new Error(`Final mission snapshot failed: ${finalState.status()} ${await finalState.text()}`);
 mission=await finalState.json();
 const configuration={seed:mission.seed,count:mission.nodes.length,topology:mission.topology,formation:mission.formation,speed:mission.speed,session_id:mission.session_id,simulation_time:mission.simulation_time};
 if(configuration.count!==100||configuration.speed!==1||configuration.topology!=='manet'||configuration.formation!=='distributed'||configuration.seed!==42)throw new Error('Profile mission configuration changed');
 const gpu=await center.locator('canvas').evaluate(c=>{const gl=c.getContext('webgl2');const debug=gl?.getExtension('WEBGL_debug_renderer_info');return debug?{vendor:gl.getParameter(debug.UNMASKED_VENDOR_WEBGL),renderer:gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)}:null;});
 const commandStart=Date.now();await center.getByRole('button',{name:'Pause mission',exact:true}).click();await center.getByRole('button',{name:'Play mission',exact:true}).waitFor();
 const commandResponse=Date.now()-commandStart;
 await center.screenshot({path:`${artifacts}/swarm-100-performance.png`});
 const report={evidence:'controlled_browser_simulation',frontend_mode:process.env.FLARE_TEST_PRODUCTION==='1'?'production':'development',duration_s:elapsed,warmup_s:warmup,viewport:{width:1440,height:1000},browser:browser.version(),headless:true,quality:'balanced',configuration,gpu,hardware:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpus:os.cpus().length,total_memory:os.totalmem()},average_fps:average,fifth_percentile_fps:low,frame_samples:frames.length,command_response_ms:commandResponse,errors,samples,retained_heap_samples:retained,memory_measurement:'CDP garbage collection before timing, every minute and after timing; timed pauses remain in FPS samples',acceptance:{fps:average>=30&&low>=20,memory_growth_bytes:samples.at(-1)?.heap_bytes-samples[0]?.heap_bytes}};
 fs.writeFileSync(`${artifacts}/swarm-performance.json`,JSON.stringify({...report,timestamp:new Date().toISOString(),command:['npm','run','profile:swarm']},null,2));console.log(JSON.stringify({...report,samples:undefined}));
}finally{await context.close();await browser.close();}
