import type { MissionSimulationState } from '../types.ts';
export type DecisionPhase = 'awaiting-evidence' | 'nominal' | 'scenario-active' | 'threat-reported' | 'rerouting' | 'apply-failed' | 'hold' | 'containment' | 'recovery' | 'degraded';
export const PHASE_LABELS: Record<DecisionPhase,string> = {
  'awaiting-evidence':'Awaiting evidence', degraded:'Link degraded', nominal:'Nominal', 'scenario-active':'Scenario active',
  'threat-reported':'Threat reported', rerouting:'Route change requested', 'apply-failed':'SDN update failed',
  hold:'Forwarding suspended', containment:'Containment active', recovery:'Forwarding recovered',
};
export interface DecisionEventItem { id:string;at:number;label:string;tone:'quiet'|'info'|'warning'|'success' }
export interface DecisionHistory { droneId:string; last:MissionSimulationState|null; seen:string[]; events:DecisionEventItem[]; phase:DecisionPhase }
export function emptyDecisionHistory(droneId=''): DecisionHistory { return {droneId,last:null,seen:[],events:[],phase:'awaiting-evidence'}; }
export function decisionEventKey(state:MissionSimulationState) { return state.eventId ?? (state.timestamp ? `${state.droneId}:${state.timestamp}:${state.rlStep}` : null); }
export function hasReportedThreat(s:MissionSimulationState) {
  return s.reportedThreatLevel==='HIGH'||s.reportedThreatLevel==='MEDIUM'||s.networkDetection?.status==='MALICIOUS'||s.networkDetection?.status==='SUSPICIOUS';
}
export function hasHealthyForwarding(s:MissionSimulationState) {
  const route=s.routes.find(r=>r.id===s.installedRoute);
  return !!route && !s.noSafeRoute && s.reportedThreatLevel==='LOW' && route.safe===true && !route.jammed
    && Number.isFinite(route.metric?.packet_loss) && route.metric!.packet_loss<=.2
    && s.activeAttack==='none' && (!s.containmentMode||s.containmentMode==='normal')
    && s.networkAction!=='hold' && !s.sdnError && !hasReportedThreat(s)
    && (!s.requestedRoute||s.requestedRoute===s.installedRoute);
}
export function decisionPhase(s:MissionSimulationState, prior:MissionSimulationState|null, selectedId:string):DecisionPhase {
  if(s.droneId!==selectedId||!decisionEventKey(s))return 'awaiting-evidence';
  if(s.installedRoute==='hold')return 'hold';
  if(s.sdnError)return 'apply-failed';
  if(s.requestedRoute && s.requestedRoute!==s.installedRoute)return 'rerouting';
  if(s.networkAction==='hold')return 'rerouting';
  if(s.containmentMode&&s.containmentMode!=='normal')return 'containment';
  if(hasReportedThreat(s))return 'threat-reported';
  if(s.activeAttack!=='none')return 'scenario-active';
  if(hasHealthyForwarding(s)) {
    const recovering=prior && prior.droneId===s.droneId && (prior.installedRoute==='hold'||hasReportedThreat(prior)||prior.activeAttack!=='none'||prior.routes.some(r=>r.id===prior.installedRoute&&(r.jammed||r.safe===false||(r.metric?.packet_loss??0)>.2)));
    return recovering?'recovery':'nominal';
  }
  if(s.routes.some(r=>r.id===s.installedRoute&&(r.jammed||r.safe===false||(r.metric?.packet_loss??0)>.2)))return 'degraded';
  return 'awaiting-evidence';
}
export function reduceDecisionHistory(history:DecisionHistory, input:{state:MissionSimulationState;selectedId:string}):DecisionHistory {
  const {state:s,selectedId}=input;
  const h=history.droneId===selectedId?history:emptyDecisionHistory(selectedId);
  if(s.droneId!==selectedId)return h;
  const key=decisionEventKey(s);
  if(!key)return h.last ? {...emptyDecisionHistory(selectedId),seen:h.seen} : h;
  if(h.seen.includes(key)|| (h.last?.timestamp!=null && s.timestamp!=null && s.timestamp<h.last.timestamp))return h;
  const prior=h.last, at=s.timestamp;
  const events:DecisionEventItem[]=[];
  const add=(kind:string,label:string,tone:DecisionEventItem['tone']='info')=>{
    if(at!=null&&Number.isFinite(at))events.push({id:`${s.droneId}:${key}:${kind}`,at,label,tone});
  };
  if(!prior)add('evidence','Runtime evidence received');
  if(prior?.activeAttack!==s.activeAttack && s.activeAttack!=='none')add('scenario',`Scenario active: ${s.activeAttack.replaceAll('_',' ')}`,'warning');
  if(prior && prior.activeAttack!=='none'&&s.activeAttack==='none')add('scenario-clear','Scenario cleared; network health evaluated separately');
  if(hasReportedThreat(s)&&(!prior||!hasReportedThreat(prior)))add('threat',s.networkDetection?.detected_classes.length?`Network detector reported ${s.networkDetection.detected_classes.join(', ')}`:'Communication threat reported','warning');
  if(s.policyRoute&&s.policyRoute!==prior?.policyRoute)add('policy',`${s.decisionSource==='rl_model'?'DQN':s.decisionSource==='greedy_fallback'?'Fallback policy':'Policy'} selected ${s.policyRoute}`);
  if(s.safetyOverride&&(!prior?.safetyOverride||s.requestedRoute!==prior.requestedRoute))add('override',`Safety override requested ${s.requestedRoute??'unavailable'}${s.constraintReason?` · ${s.constraintReason.replaceAll('_',' ')}`:''}`,'warning');
  if(s.requestedRoute&&s.requestedRoute!==s.installedRoute&&s.requestedRoute!==prior?.requestedRoute)add('request',`Requested ${s.requestedRoute}; installed ${s.installedRoute??'unknown'}`);
  if(s.sdnError&&s.sdnError!==prior?.sdnError)add('failed',`SDN update failed; installed ${s.installedRoute??'unknown'}`,'warning');
  if(s.routeChanged && s.installedRoute!=null && s.sdnApplied===true && prior && prior.installedRoute!==s.installedRoute)
    add('route-changed',`Route changed: ${prior.installedRoute??'unknown'} → ${s.installedRoute}`);
  if(s.installedRoute!=null&&s.sdnApplied===true&&(!prior?.sdnApplied||prior.installedRoute!==s.installedRoute))add('installed',s.installedRoute==='hold'?`SDN applied HOLD · ${s.constraintReason==='containment_required'?'containment':s.noSafeRoute?'no safe route':'runtime hold'}`:`SDN installed ${s.installedRoute}`,'success');
  if(s.containmentMode&&s.containmentMode!=='normal'&&s.containmentMode!==prior?.containmentMode)add('containment',`Containment: ${s.containmentMode.replaceAll('_',' ')}`,'warning');
  const phase=decisionPhase(s,prior,selectedId);
  if(phase==='recovery')add('recovery',`Healthy forwarding restored on ${s.installedRoute}`,'success');
  return {droneId:selectedId,last:s,seen:[...h.seen,key].slice(-128),events:[...h.events,...events].slice(-20),phase};
}
