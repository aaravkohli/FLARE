import { missionSetting, resetMission } from './workspace-helpers';
import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const artifacts=process.env.FLARE_ARTIFACT_DIR||'/private/tmp/flare-mesh-browser';fs.mkdirSync(artifacts,{recursive:true});
test('network routes, real packet replay, 3D parity and responsive workspace',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 await resetMission(center);await missionSetting(center,'Fleet size','30');
 await center.getByLabel('Topology',{exact:true}).selectOption('self_healing_mesh');await center.getByLabel('Speed',{exact:true}).selectOption('0.1');
 await expect(center.getByRole('img',{name:'Authoritative 2D mission network'})).toBeVisible();await expect(center.getByLabel('Installed FLARE route')).toBeVisible();
 await expect(center.getByLabel('Topology',{exact:true}).locator('option')).toHaveCount(12);
 await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await center.getByRole('button',{name:'Layers',exact:true}).click();await center.getByLabel('Idle links',{exact:true}).uncheck();await center.getByLabel('Selected range',{exact:true}).check();await center.getByRole('button',{name:'Close Layers',exact:true}).click();
 await center.getByRole('button',{name:'Route probes',exact:true}).click();const sent=page.waitForResponse(r=>r.url().endsWith('/probes')&&r.request().method()==='POST');await center.getByRole('button',{name:'Send probe',exact:true}).click();const probe=await (await sent).json();expect(probe.packet.route.length).toBeGreaterThan(1);
 await center.getByRole('button',{name:'View settings',exact:true}).click();await center.getByRole('dialog',{name:'View settings',exact:true}).locator('summary').filter({hasText:'Packet replay'}).click();

 await expect(center.getByLabel('Recorded packet').locator(`option[value="${probe.packet.id}"]`)).toHaveCount(1,{timeout:15000});
 await center.getByLabel('Recorded packet').selectOption(probe.packet.id);await center.getByRole('button',{name:'Replay 0.1×'}).click();
 await expect(center.locator('canvas')).toHaveAttribute('data-packet-visual','authoritative-packet-events');
 await center.getByLabel('Recorded packet').selectOption('');await center.getByRole('button',{name:'Close View settings',exact:true}).click();
 await center.screenshot({path:`${artifacts}/mesh-desktop.png`});
 await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toHaveAttribute('data-packet-visual','authoritative-packet-events');
 await center.screenshot({path:`${artifacts}/mesh-3d.png`});
 await center.getByRole('button',{name:'2D network',exact:true}).click();await page.setViewportSize({width:768,height:1024});await center.screenshot({path:`${artifacts}/mesh-tablet.png`});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.setViewportSize({width:390,height:844});await center.screenshot({path:`${artifacts}/mesh-mobile.png`});
 expect(await center.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBeTruthy();
 await page.setViewportSize({width:1440,height:1000});await missionSetting(center,'Scenario','regional_jamming');await center.getByLabel('Speed',{exact:true}).selectOption('4');await center.getByRole('button',{name:'Play mission',exact:true}).click();
 await expect(center.locator('canvas')).toHaveAttribute('data-interference-region-count','1',{timeout:10000});await center.screenshot({path:`${artifacts}/mesh-jamming-region.png`});await center.getByRole('button',{name:'3D world',exact:true}).click();await expect(center.locator('canvas')).toHaveAttribute('data-interference-region-count','1');expect(errors).toEqual([]);
});
test('isolated synchronized comparison and exportable all-ten benchmark',async({page})=>{
 test.setTimeout(120000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/#mission');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 // Start from a bounded, paused live fleet; this workflow measures the isolated
 // experiment rather than inheriting the preceding regional-jamming workload.
 await resetMission(center);await missionSetting(center,'Fleet size','10');await center.getByLabel('Speed',{exact:true}).selectOption('1');
 await center.getByRole('button',{name:'A/B comparison',exact:true}).click();await center.getByRole('button',{name:'Shared settings',exact:true}).click();
 await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment fleet size').selectOption('10');await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment duration').fill('10');
 await center.getByRole('button',{name:'Close Shared settings',exact:true}).click();await center.getByRole('button',{name:'Create comparison',exact:true}).click();await expect(center.locator('.comparison-pane')).toHaveCount(2,{timeout:15000});
 await center.getByRole('button',{name:'Play comparison',exact:true}).click();await page.waitForTimeout(1200);await center.getByRole('button',{name:'Pause comparison',exact:true}).click();
 await expect(center.getByRole('button',{name:'Play comparison',exact:true})).toBeVisible();
 const times=await center.locator('.comparison-pane time').allTextContents();expect(times[0]).toBe(times[1]);
 await center.screenshot({path:`${artifacts}/mesh-comparison.png`});
 await center.getByRole('button',{name:'Cancel experiment',exact:true}).click();await expect(center.getByRole('button',{name:'Create comparison',exact:true})).toBeEnabled({timeout:15000});
 await center.getByRole('button',{name:'Topology benchmark',exact:true}).click();
 await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment fleet size').selectOption('10');await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment duration').fill('10');await center.locator('.experiment-workspace-page:not([hidden])').getByLabel('Experiment seeds').fill('7');
 await center.getByRole('button',{name:'Run all ten',exact:true}).click();await expect(center.locator('.experiment-results tbody tr')).toHaveCount(10,{timeout:90000});
 const download=page.waitForEvent('download');await center.getByRole('button',{name:'Export JSON',exact:true}).click();const file=await download;await file.saveAs(`${artifacts}/benchmark.json`);
 const report=JSON.parse(fs.readFileSync(`${artifacts}/benchmark.json`,'utf8'));expect(report.evidence).toBe('packet_simulation');expect(report.results.every((r:{conservation:boolean})=>r.conservation)).toBeTruthy();expect(report.trace_files).toHaveLength(10);expect(report.source_sha256).toMatch(/^[a-f0-9]{64}$/);expect(report.simulation_config.radio.bandwidth_mbps).toBeGreaterThan(0);expect(report.terrain_provenance.source).toBeTruthy();
 await center.screenshot({path:`${artifacts}/mesh-benchmark.png`});expect(errors).toEqual([]);
});
