import type {TopologySelection} from '../swarm.ts';
export function assistanceStatus(selection:TopologySelection|undefined,inspected:string,current:boolean):string {
 const a=selection?.assistance;
 if(!a)return 'Assistance evidence unavailable';
 if(!a.enabled)return 'Disabled';
 if(!current)return 'Connection stale · retained Mesh evidence';
 if(selection.mode==='manual')return 'Inactive—manual topology pinned';
 return a.nodes.find(n=>n.node===inspected)?.status??'Awaiting legitimate Mesh evidence';
}
export function assistanceMeasurement(value:number|null|undefined,kind:'delivery'|'latency'):string {
 return value==null||!Number.isFinite(value)?'—':kind==='delivery'?`${(value*100).toFixed(1)}%`:`${value.toFixed(1)} ms`;
}
