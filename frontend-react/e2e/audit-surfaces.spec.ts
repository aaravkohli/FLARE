import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {enableAuditEvidence} from './audit-evidence';
enableAuditEvidence();
test('runtime controls, all navigation destinations and intermediate responsive layouts',async({page})=>{
 test.setTimeout(90000);const directory=process.env.FLARE_ARTIFACT_DIR||'/private/tmp/flare-audit-surfaces';fs.mkdirSync(directory,{recursive:true});const controls:unknown[]=[];
 async function capture(surface:string){controls.push({surface,accessibility:await page.locator('body').ariaSnapshot(),controls:await page.locator('button,input,select,textarea,summary,a[href],[role="tab"]').evaluateAll(es=>es.filter(e=>!!e.getClientRects().length).map(e=>({tag:e.tagName,role:e.getAttribute('role'),name:e.getAttribute('aria-label')||e.getAttribute('placeholder')||(e instanceof HTMLInputElement?'':e.textContent?.trim()),disabled:e.hasAttribute('disabled')})))});}
 await page.goto('/#mission');await capture('sign-in');await page.getByPlaceholder('Operator').fill('admin');await page.getByPlaceholder('Password').fill('antijam2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();const center=page.getByRole('region',{name:'Swarm command center'});await expect(center).toBeVisible();
 const nav=page.getByRole('navigation',{name:'Application',exact:true});
 for(const name of ['Overview','Fleet','Security','Validation','Mission']){await nav.getByRole('link',{name,exact:true}).click();await expect(page.getByRole('region',{name,exact:true})).toBeVisible();await capture(name);}
 for(const tab of ['Events','Inspect','Commands']){await center.getByRole('tab',{name:tab,exact:true}).click();await capture('mission/'+tab);}
 for(const tool of ['Mission setup','Camera','Layers','Route probes','View settings']){await center.getByRole('button',{name:tool,exact:true}).click();await expect(center.getByRole('dialog',{name:tool,exact:true})).toBeVisible();await capture('mission/'+tool);await page.keyboard.press('Escape');}
 for(const view of ['A/B comparison','Topology benchmark','Live mission']){await center.getByRole('button',{name:view,exact:true}).click();await capture(view);}
 for(const width of [687,915]){await page.setViewportSize({width,height:765});await page.screenshot({path:`${directory}/mission-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);}
 fs.writeFileSync(`${directory}/runtime-controls.json`,JSON.stringify(controls,null,2));
});
