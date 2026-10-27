import type { NetworkDisplay, SwarmSnapshot } from './swarm';
import { ToolPopover } from './WorkspaceControls';

export default function NetworkLayerKey({snapshot,selected,display,world=false,onLink}:{snapshot:SwarmSnapshot;selected:string;display:NetworkDisplay;world?:boolean;onLink?:(id:string)=>void}){
 const installed=display.installedRoutes!==false&&snapshot.flows.find(f=>f.source===selected&&f.destination==='base'&&f.traffic_class==='telemetry');
 const affected=snapshot.links.filter(l=>l.a!=='base'&&l.b!=='base'&&['degraded','jammed','blocked'].includes(l.status));
 const name=(id:string)=>snapshot.nodes.find(n=>n.id===id)?.alias??id;
 return <div className={`network-layer-key${world?' network-layer-key--world':''}`} aria-label="Connection legend">
  <span className="layer-key-topology" title="Topology changes connections, not drone positions. Mission setup offers a suggested formation.">UAV links</span>
  {installed&&<span className="layer-key-installed" title="Installed forwarding policy for the inspected UAV. This overlay is separate from topology and packet activity.">Installed · {installed.family}</span>}
  {!!display.hops.length&&<span className={display.candidate?'layer-key-candidate':'layer-key-probe'}>{snapshot.routing_mode==='mesh_reference'?'Selected flow':'Probe'} · {display.candidate?'candidate':'installed'}</span>}
  {snapshot.autonomy?.mode==='recovery_v1'&&<span className="layer-key-candidate" title="Dashed control-only discovery or verification edges; separate from the completed topology.">Recovery control · dashed</span>}
  {!!affected.length&&<ToolPopover label="UAV link quality" compactLabel={`RF quality · ${affected.length}`}>
   <p className="swarm-note">{affected.filter(l=>l.status==='degraded').length} degraded · {affected.filter(l=>l.status==='jammed').length} with interference · {affected.filter(l=>l.status==='blocked').length} blocked. Low RF quality alone is not an attack detection.</p>
   <table className="network-link-quality-table"><thead><tr><th>UAV link</th><th>State</th><th>Distance</th><th>Loss</th></tr></thead><tbody>{affected.slice(0,40).map(l=><tr key={l.id}><td>{onLink?<button onClick={()=>onLink(l.id)}>{name(l.a)} ↔ {name(l.b)}</button>:`${name(l.a)} ↔ ${name(l.b)}`}</td><td>{l.status}</td><td>{l.distance_m.toFixed(0)} m</td><td>{(l.loss*100).toFixed(1)}%</td></tr>)}</tbody></table>
   {affected.length>40&&<p className="swarm-note">First 40 of {affected.length} affected links. Inspect a UAV for its complete neighbor list.</p>}
  </ToolPopover>}
 </div>;
}
