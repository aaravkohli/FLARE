import type {SwarmSnapshot} from '../swarm.ts';
import type {MissionEvidenceStatus} from './evidenceFreshness.ts';
export function receiptEvidence(snapshot:SwarmSnapshot,inspected:string,observation:{epoch:number;clock:string;captured_at:number}|null|undefined,current:boolean):MissionEvidenceStatus|null {
 const reports=snapshot.observability;if(!reports)return null;
 const row=reports.nodes.find(n=>n.node===inspected);
 if(!current)return {kind:'disconnected',label:'Connection stale · retained evidence',ageSeconds:null,evidenceCurrent:false,lastSnapshot:true};
 if(!observation||observation.epoch!==reports.epoch||observation.clock!=='simulation')return {kind:'awaiting',label:'Awaiting report-backed decision',ageSeconds:null,evidenceCurrent:false,lastSnapshot:true};
 const age=snapshot.simulation_time-observation.captured_at;
 const fresh=Number.isFinite(age)&&age>=0&&age<reports.freshness_s&&row?.eligible===true;
 return {kind:fresh?'fresh':'stale',label:`${fresh?'Fresh':'Retained'} ${Number.isFinite(age)?age.toFixed(1)+' s':'—'}${!snapshot.playing?' · Paused':''}`,ageSeconds:Number.isFinite(age)?age:null,evidenceCurrent:fresh,lastSnapshot:!fresh};
}
