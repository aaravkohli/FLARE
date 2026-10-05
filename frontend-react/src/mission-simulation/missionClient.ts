import { getMissionSnapshot } from './swarm.ts';
import type { SwarmSnapshot } from './swarm.ts';

export function requestError(detail:unknown):string {
 if(typeof detail==='string')return detail;
 if(Array.isArray(detail))return detail.map(e=>`${e.loc?.slice(1).join('.')||'Request'}: ${e.msg||'invalid value'}`).join('; ');
 return 'Request failed. Check the connection and try again.';
}

/** One ordered mutation stream. Conflict/uncertainty refreshes never replay intent. */
export class MissionClient {
 private tail:Promise<unknown>=Promise.resolve();
 private api:string;private token:string;private publish:(s:SwarmSnapshot)=>void;
 constructor(api:string,token:string,publish:(s:SwarmSnapshot)=>void){this.api=api;this.token=token;this.publish=publish;}
 async read(path=''){
  const response=await fetch(`${this.api}/mission/simulation${path}`,{headers:{Authorization:`Bearer ${this.token}`},signal:AbortSignal.timeout(7000)});
  const data=await response.json();if(!response.ok)throw new Error(requestError(data.detail));return data;
 }
 mutate(path:string,body:Record<string,unknown>,method='POST'){
  const requestedSession=getMissionSnapshot()?.session_id;
  const operation=this.tail.then(async()=>{
   const current=getMissionSnapshot();
   if(!current||current.session_id!==requestedSession)throw new Error('Mission reset. Review targets and send again.');
   const request={...body,expected_revision:current.revision};
   if('session_id' in request)request.session_id=current.session_id;
   let response:Response;
   try{response=await fetch(`${this.api}/mission/simulation${path}`,{method,headers:{Authorization:`Bearer ${this.token}`,'Content-Type':'application/json'},body:JSON.stringify(request),signal:AbortSignal.timeout(7000)});}
   catch{
    if(path==='/commands'&&typeof body.command_id==='string'){
     try{const recovered=await this.read(`/commands/${body.command_id}`);this.publish(await this.read());return recovered;}catch{/* Preserve uncertainty; never issue a duplicate command. */}
    }
    throw new Error('Delivery could not be confirmed. Refresh status before sending another command.');
   }
   const data=await response.json();
   if(response.status===409){this.publish(await this.read());throw new Error('Mission revision changed. Review current targets and retry.');}
   if(response.status===401)throw new Error('Sign-in expired. Sign in again to continue.');
   if(!response.ok)throw new Error(requestError(data.detail));
   if(data.schema_version==='mission_snapshot_v1')this.publish(data);
   else this.publish(await this.read());
   return data;
  });
  this.tail=operation.catch(()=>{});return operation;
 }
}
