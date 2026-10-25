import type { TopologySelection } from '../swarm.ts';

export function topologyControlValue(active:string, selection?:TopologySelection){
 return selection?.mode==='auto'?'auto':selection?.requested_target??active;
}
export function topologyStatus(selection:TopologySelection|undefined, stale:boolean){
 if(stale)return 'Connection stale · last confirmed topology';
 if(!selection)return 'Selection evidence unavailable';
 if(selection.fallback)return `Fallback · ${selection.reason}`;
 return `${selection.mode==='manual'?'Manual override':'Auto balanced'} · ${selection.reason}`;
}
export function simulationTimestamp(value:number|null|undefined){
 return value==null?'—':`T+${value.toFixed(1)}s`;
}
