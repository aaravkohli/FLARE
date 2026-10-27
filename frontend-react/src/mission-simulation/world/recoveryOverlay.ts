import type {SwarmSnapshot} from '../swarm.ts';
/** Same authoritative edges in both renderers; no inferred recovery geometry. */
export function recoveryOverlay(snapshot:SwarmSnapshot,selected:string){
 const nodes=new Set([...snapshot.nodes.map(n=>n.id),'base']);
 const edges=new Map<string,{a:string;b:string;kind:'verification'|'recovery_control'}>();
 for(const e of snapshot.autonomy?.overlay_edges??[]){
  if(e.expires_at<=snapshot.simulation_time||!nodes.has(e.a)||!nodes.has(e.b)||e.a===e.b)continue;
  if(selected&&e.a!==selected&&e.b!==selected)continue;
  const id=[e.a,e.b].sort().join(':');const previous=edges.get(id);
  if(!previous||e.kind==='verification')edges.set(id,{a:e.a,b:e.b,kind:e.kind});
  if(edges.size>=256)break;
 }
 return [...edges.values()];
}
