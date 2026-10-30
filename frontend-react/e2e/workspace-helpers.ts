import { expect } from '@playwright/test';
import type { Locator } from '@playwright/test';
export async function missionSetting(center:Locator,label:string,value:string){
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();
 const select=center.getByRole('dialog',{name:'Mission setup',exact:true}).getByLabel(label,{exact:true});
 const path=label==='Scenario'?`/scenarios/${value}`:'/config';
 const receipt=center.page().waitForResponse(r=>r.url().endsWith('/mission/simulation'+path)&&r.request().method()!=='GET');
 await select.selectOption(value);
 expect((await receipt).ok()).toBeTruthy();
 await expect(select).toBeEnabled();
 await center.getByRole('button',{name:'Close Mission setup',exact:true}).click();
}
export async function resetMission(center:Locator){
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();
 const receipt=center.page().waitForResponse(r=>r.url().endsWith('/mission/simulation/control')&&r.request().postDataJSON()?.action==='reset');
 await center.getByRole('button',{name:'Reset',exact:true}).click();
 expect((await receipt).ok()).toBeTruthy();
 await expect(center.getByRole('button',{name:'Reset',exact:true})).toBeEnabled();
 await center.getByRole('button',{name:'Close Mission setup',exact:true}).click();
}

/** Wait for the confirmed mutation, not merely the DOM change event. */
export async function selectMissionValue(center:Locator,label:string,value:string){
 const select=center.getByLabel(label,{exact:true});
 const receipt=center.page().waitForResponse(r=>r.url().endsWith('/mission/simulation/config')&&r.request().method()==='PATCH');
 await select.selectOption(value);
 expect((await receipt).ok()).toBeTruthy();
 await expect(select).toBeEnabled();
}

/** Explicit operator retry after a confirmed rejection; never repeat uncertainty. */
export async function missionControl(center:Locator,action:'play'|'pause'){
 const name=action==='pause'?'Pause mission':'Play mission';
 for(let attempt=0;attempt<3;attempt++){
  const button=center.getByRole('button',{name,exact:true});
  const receipt=center.page().waitForResponse(r=>r.url().endsWith('/mission/simulation/control')&&r.request().postDataJSON()?.action===action,{timeout:15000});
  await button.click();
  const response=await receipt;
  if(response.status()===409){
   await expect(center.getByRole('alert')).toContainText('Mission revision changed');
   await expect(button).toBeEnabled();
   continue;
  }
  expect(response.ok()).toBeTruthy();
  await expect(center.getByRole('button',{name:action==='pause'?'Play mission':'Pause mission',exact:true})).toBeEnabled();
  return;
 }
 throw new Error('Three confirmed revision conflicts prevented '+action);
}

export async function signInToMission(page:import('@playwright/test').Page){
 const {testCredentials}=await import('./support/credentials.mjs');
 const credentials=testCredentials();
 await page.goto('/#mission');
 await page.getByPlaceholder('Operator').fill(credentials.username);
 await page.getByPlaceholder('Password').fill(credentials.password);
 const receipt=page.waitForResponse(r=>r.url().endsWith('/auth/token')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 expect((await receipt).ok()).toBeTruthy();
 const center=page.getByRole('region',{name:'Swarm command center'});
 await expect(center).toBeVisible({timeout:20000});
 return center;
}
export async function commandOutcomes(center:Locator){
 await center.getByRole('tab',{name:'Activity',exact:true}).click();
 const commands=center.locator('.activity-list>li>details:has(.swarm-command-outcome)');
 await expect(commands.first()).toBeVisible();
 for(const item of await commands.all()){if(!await item.evaluate(e=>(e as HTMLDetailsElement).open))await item.locator(':scope>summary').click();}
}
