import { expect } from '@playwright/test';
import type { Locator } from '@playwright/test';
export async function missionSetting(center:Locator,label:string,value:string){
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();
 await center.getByRole('dialog',{name:'Mission setup',exact:true}).getByLabel(label,{exact:true}).selectOption(value);
 await center.getByRole('button',{name:'Close Mission setup',exact:true}).click();
}
export async function resetMission(center:Locator){
 await center.getByRole('button',{name:'Mission setup',exact:true}).click();await center.getByRole('button',{name:'Reset',exact:true}).click();await center.getByRole('button',{name:'Close Mission setup',exact:true}).click();
}
export async function commandOutcomes(center:Locator){
 await center.getByRole('tab',{name:'Activity',exact:true}).click();
 const commands=center.locator('.activity-list>li>details:has(.swarm-command-outcome)');
 await expect(commands.first()).toBeVisible();
 for(const item of await commands.all()){if(!await item.evaluate(e=>(e as HTMLDetailsElement).open))await item.locator(':scope>summary').click();}
}
