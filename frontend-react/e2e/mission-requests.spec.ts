import {test,expect} from '@playwright/test';
import {testCredentials} from './support/credentials.mjs';
import {missionControl,missionSetting,selectMissionValue,signInToMission} from './workspace-helpers';

test('confirmed revision rejection permits an explicit retry; lost receipts never replay intent',async({page,request})=>{
 const center=await signInToMission(page);
 await missionSetting(center,'Fleet size','10');
 await missionSetting(center,'Scenario','normal');
 await selectMissionValue(center,'Topology','ring');
 await selectMissionValue(center,'Speed','1');
 const {username,password}=testCredentials();
 const auth=await request.post('http://127.0.0.1:18000/auth/token',{form:{username,password}});
 expect(auth.ok()).toBeTruthy();
 const headers={Authorization:`Bearer ${(await auth.json()).access_token}`};
 const state=async()=>(await request.get('http://127.0.0.1:18000/mission/simulation',{headers})).json();
 await missionControl(center,'play');
 let attempts=0;
 await page.route('**/mission/simulation/control',async route=>{
  if(route.request().postDataJSON()?.action!=='pause'){await route.continue();return;}
  attempts++;
  if(attempts===1)await route.fulfill({status:409,json:{detail:'Injected confirmed revision conflict'}});
  else await route.continue();
 });
 await missionControl(center,'pause');
 expect(attempts).toBe(2);
 expect((await state()).playing).toBe(false);
 await page.unroute('**/mission/simulation/control');

 await missionControl(center,'play');
 let uncertainPosts=0;
 await page.route('**/mission/simulation/control',async route=>{
  if(route.request().postDataJSON()?.action!=='pause'){await route.continue();return;}
  uncertainPosts++;
  const accepted=await route.fetch();
  expect(accepted.ok()).toBeTruthy();
  await route.abort('failed');
 });
 // Actual backend execution, with only its response deliberately lost.
 const outcome=missionControl(center,'pause').then(()=>null,error=>error);
 await expect(center.getByRole('alert')).toContainText('Delivery could not be confirmed');
 expect((await state()).playing).toBe(false);
 expect(await outcome).toBeInstanceOf(Error);
 expect(uncertainPosts).toBe(1);
 await page.unroute('**/mission/simulation/control');
});

test('policy selection waits for the real configuration receipt before reading state',async({page})=>{
 const center=await signInToMission(page);
 let release:()=>void=()=>{};
 const delayed=new Promise<void>(resolve=>{release=resolve;});
 let received:()=>void=()=>{};
 const accepted=new Promise<void>(resolve=>{received=resolve;});
 let posts=0,finished=false;
 await page.route('**/mission/simulation/config',async route=>{
  if(route.request().postDataJSON()?.adaptive_policy!=='evidence_resilience_v1'){await route.continue();return;}
  posts++;
  const response=await route.fetch();
  expect(response.ok()).toBeTruthy();
  received();
  await delayed;
  await route.fulfill({response});
 });
 const selection=selectMissionValue(center,'Topology','safeguarded').then(()=>{finished=true;});
 await accepted;
 expect(finished).toBe(false);
 release();
 await selection;
 expect(posts).toBe(1);
 await expect(center.getByLabel('Topology',{exact:true})).toHaveValue('safeguarded');
 await page.unroute('**/mission/simulation/config');
 await selectMissionValue(center,'Topology','auto');
});
