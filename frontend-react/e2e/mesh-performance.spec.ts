import { missionSetting, resetMission } from './workspace-helpers';
import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
const enabled=process.env.FLARE_MESH_PROFILE==='1';
const duration=Number(process.env.FLARE_PROFILE_SECONDS||60);
// Run each view in a fresh service process when measuring renderer performance
// independently of accumulated live-model/packet backlog: 2d, 3d or compare.
const selectedView=process.env.FLARE_PROFILE_VIEW||'all';
const mainTopology=process.env.FLARE_PROFILE_TOPOLOGY||'full_mesh';
const directory=process.env.FLARE_ARTIFACT_DIR||'/private/tmp/flare-mesh-profile';
test('100-UAV 2D, 3D and synchronized A/B rendering profile',async({page})=>{
 test.skip(!enabled,'Set FLARE_MESH_PROFILE=1 for the sustained rendering profile.');test.setTimeout(duration*3000+90000);fs.mkdirSync(directory,{recursive:true});
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/?profile=1#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 if(!['all','2d','3d','compare'].includes(selectedView))throw new Error('Unknown FLARE_PROFILE_VIEW');
 await resetMission(center);await missionSetting(center,'Fleet size',selectedView==='compare'?'10':'100');await center.getByLabel('Topology',{exact:true}).selectOption(mainTopology);await center.getByLabel('Speed',{exact:true}).selectOption('1');await center.getByRole('button',{name:'Presentation',exact:true}).click();if(selectedView!=='compare')await center.getByRole('button',{name:'Play mission',exact:true}).click();
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');const reports:unknown[]=[];
 async function measure(view:string){
  await center.locator('canvas').first().scrollIntoViewIfNeeded();await page.waitForTimeout(3000);
  await cdp.send('HeapProfiler.collectGarbage');const before=await cdp.send('Performance.getMetrics');
  await center.locator('canvas').evaluateAll(elements=>elements.forEach(e=>{const profile=(e as HTMLCanvasElement & {__flareFrameTimes?:{count:number}}).__flareFrameTimes;if(profile)profile.count=0;}));
  const clockSelector=view==='compare'?'.comparison-pane time':'.swarm-clock';const clockBefore=await center.locator(clockSelector).allTextContents();
  await page.waitForTimeout(duration*1000);
  const clockAfter=await center.locator(clockSelector).allTextContents();
  const canvases=await center.locator('canvas').evaluateAll(elements=>elements.map(e=>{const c=e as HTMLCanvasElement & {__flareFrameTimes?:{values:Float64Array;count:number}};const p=c.__flareFrameTimes;const gl=c.classList.contains('mission-world-canvas')?c.getContext('webgl2'):null;const debug=gl?.getExtension('WEBGL_debug_renderer_info');return {buffer_capacity:p?.values.length,frame_count:p?.count,dpr:devicePixelRatio,canvas_pixels:[c.width,c.height],canvas_css:[c.clientWidth,c.clientHeight],gpu:debug&&gl?{vendor:gl.getParameter(debug.UNMASKED_VENDOR_WEBGL),renderer:gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)}:null,frames:p?Array.from(p.values.slice(0,p.count)):[],packet_visual:c.dataset.packetVisual,site_details:c.dataset.siteDetails,quality:c.dataset.quality,draw_calls:c.dataset.drawCalls,geometries:c.dataset.geometries,textures:c.dataset.textures};}));
  await cdp.send('HeapProfiler.collectGarbage');const after=await cdp.send('Performance.getMetrics');
  const measured=canvases.map(c=>{const frames=c.frames.filter(t=>t>0),rates=frames.map(t=>1000/t).sort((a,b)=>a-b);return {...c,average_fps:frames.length*1000/frames.reduce((a,b)=>a+b,0),fifth_percentile_fps:rates[Math.floor(rates.length*.05)]};});
  reports.push({view,duration_s:duration,clock_before:clockBefore,clock_after:clockAfter,canvases:measured,render_buffers:await center.locator('.network-inspection').evaluateAll(es=>es.map(e=>({packet_event_count:(e as HTMLElement).dataset.eventCount,packet_result_count:(e as HTMLElement).dataset.resultCount}))),retained_heap_before:before.metrics.find(m=>m.name==='JSHeapUsedSize')?.value,retained_heap_after:after.metrics.find(m=>m.name==='JSHeapUsedSize')?.value});
  await center.screenshot({path:`${directory}/${view}-100.png`});
  fs.writeFileSync(`${directory}/performance.json`,JSON.stringify({count:100,selected_view:selectedView,browser:page.context().browser()?.version(),frontend_mode:process.env.FLARE_TEST_PRODUCTION==='1'?'production':'development',main_topology:mainTopology,comparison_topologies:['partial_mesh','self_healing_mesh'],comparison_background:'Live mission reset to 10 UAVs and paused before the isolated 100-UAV A/B run',viewport:page.viewportSize(),headless:true,hardware:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model},reports,errors},null,2));
  expect(clockAfter.some((value,index)=>value!==clockBefore[index]),`${view} authoritative clock advances`).toBeTruthy();
  if(view==='compare')expect(clockAfter[0]).toBe(clockAfter[1]);
  for(const c of measured){expect(c.average_fps,`${view} average FPS`).toBeGreaterThanOrEqual(30);expect(c.fifth_percentile_fps,`${view} fifth percentile FPS`).toBeGreaterThanOrEqual(20);}
 }
 if(selectedView==='all'||selectedView==='2d')await measure('2d');
 if(selectedView==='all'||selectedView==='3d'){await center.getByRole('button',{name:'3D world',exact:true}).click();await measure('3d');}
 if(selectedView==='2d'||selectedView==='3d'){expect(errors).toEqual([]);return;}
 if(selectedView==='all'){
 await center.getByRole('button',{name:'Pause mission',exact:true}).click();
 await expect(center.getByRole('button',{name:'Play mission',exact:true})).toBeVisible({timeout:15000});
 // Complete the live measurement before releasing its packet backlog. A/B
 // remains an independent 100-UAV workload, without a second full-mesh fleet
 // competing for the same isolated simulator service and model orchestrator.
 await resetMission(center);await missionSetting(center,'Fleet size','10');
 }
 await center.getByRole('button',{name:'A/B comparison',exact:true}).click();await center.getByRole('button',{name:'Shared settings',exact:true}).click();await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment fleet size').selectOption('100');await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment duration').fill(String(Math.min(300,duration+20)));await center.getByRole('button',{name:'Close Shared settings',exact:true}).click();await center.getByRole('button',{name:'Create comparison',exact:true}).click();await expect(center.locator('.comparison-pane')).toHaveCount(2,{timeout:15000});await center.getByRole('button',{name:'Play comparison',exact:true}).click();await measure('compare');await center.getByRole('button',{name:'Cancel experiment',exact:true}).click();expect(errors).toEqual([]);
});
