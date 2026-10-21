import {test} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';

/** Optional execution evidence, distinct from assertion coverage. No form values. */
export function enableAuditEvidence(){
 const directory=process.env.FLARE_ARTIFACT_DIR;if(!directory)return;
 test.beforeEach(async({page})=>{await page.coverage.startJSCoverage({resetOnNavigation:false});});
 test.afterEach(async({page},info)=>{
  const name=createHash('sha256').update(info.titlePath.join('/')).digest('hex').slice(0,16),output=path.join(directory,'browser-coverage');fs.mkdirSync(output,{recursive:true});
  try{
   const controls=await page.locator('button,input,select,textarea,summary,a[href],[role="tab"],[role="separator"]').evaluateAll(elements=>elements.map(e=>({tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),label:e.getAttribute('aria-label')||e.getAttribute('placeholder')||(e instanceof HTMLInputElement?'':e.textContent?.trim().slice(0,160)),visible:!!e.getClientRects().length,disabled:e.hasAttribute('disabled')})));
   const scripts=(await page.coverage.stopJSCoverage()).map(e=>({url:e.url,source_sha256:createHash('sha256').update(e.text||'').digest('hex'),source_bytes:e.text?.length,ranges:e.ranges}));
   fs.writeFileSync(path.join(output,name+'.json'),JSON.stringify({test:info.titlePath,status:info.status,controls,scripts},null,2));
  }catch(error){fs.writeFileSync(path.join(output,name+'.json'),JSON.stringify({test:info.titlePath,status:info.status,evidence_error:String(error)}));}
 });
}
