import type { TopologySelection } from '../swarm.ts';

export function topologyControlValue(active:string, selection?:TopologySelection){
 return selection?.mode==='auto'?(selection.policy_id==='evidence_resilience_v1'?'safeguarded':'auto'):selection?.requested_target??active;
}
export function topologyStatus(selection:TopologySelection|undefined, stale:boolean){
 if(stale)return 'Connection stale · last confirmed topology';
 if(!selection)return 'Selection evidence unavailable';
 if(selection.fallback)return `Fallback · ${selection.reason}`;
 const label=selection.mode==='manual'?`Manual override${selection.policy_id==='evidence_resilience_v1'?' · delivered evidence':''}`:selection.policy_id==='evidence_resilience_v1'?'Auto safeguarded':'Auto balanced';
 return selection.reason==='Manual override'?label:`${label} · ${selection.reason}`;
}
export function simulationTimestamp(value:number|null|undefined){
 return value==null?'—':`T+${value.toFixed(1)}s`;
}
