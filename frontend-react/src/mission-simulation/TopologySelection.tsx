import type { SwarmSnapshot } from './swarm';
import { title } from './swarm';
import { simulationTimestamp, topologyStatus } from './world/topologySelection';

export function ActiveTopology({snapshot,stale}:{snapshot:SwarmSnapshot;stale:boolean}){
 const selection=snapshot.topology_selection;
 return <div className="workspace-topology-status" aria-label="Topology selection" role="status">
  <strong>Active: {title(snapshot.topology)}</strong>
  <span>{topologyStatus(selection,stale)}</span>
  {selection?.requested_target&&<span className="topology-transition">Requested: {title(selection.requested_target)} · {selection.transition_state==='draining'?'Draining mesh traffic':'Validating forwarding'}</span>}
 </div>;
}

export function TopologyDiagnostics({snapshot}:{snapshot:SwarmSnapshot}){
 const selection=snapshot.topology_selection;
 return <details className="topology-diagnostics"><summary>Topology selection details</summary>
  {!selection?<p>Selection evidence unavailable.</p>:<>
   <dl><div><dt>Mode</dt><dd>{selection.mode==='auto'?'Auto (balanced)':'Manual override'}</dd></div><div><dt>Evaluated</dt><dd>{simulationTimestamp(selection.evaluated_at)}</dd></div><div><dt>Last switch</dt><dd>{simulationTimestamp(selection.switched_at)}</dd></div><div><dt>Cooldown</dt><dd>{selection.cooldown_remaining_s.toFixed(1)} simulation seconds{!snapshot.playing?' · Paused':''}</dd></div></dl>
   <p>{selection.reason}</p>
   <p className="swarm-note">{selection.evidence==='nominal_schedule_estimate'?'Nominal traffic estimates':'Observed offered mesh traffic over the last 10 seconds'}. Hop estimates use measured mesh samples only after eight observations; unobserved hops use the radio model. Resource efficiency is a link and relay-load proxy.</p>
   {selection.fallback&&<p role="alert" className="swarm-error">{selection.fallback}</p>}
   <div className="topology-score-scroll"><table><caption>Candidate estimates · higher scores are better</caption><thead><tr><th>Topology</th><th>Net score</th><th>Reliability</th><th>Latency</th><th>Capacity</th><th>Efficiency</th><th>Hops measured / estimated</th><th>Connectivity</th></tr></thead><tbody>{selection.candidates.map(c=><tr key={c.topology} aria-current={c.topology===snapshot.topology?'true':undefined}><th scope="row">{title(c.topology)}</th><td>{c.net_score.toFixed(3)}<small> −{c.transition_penalty.toFixed(3)} transition</small></td>{(['reliability','latency','capacity','efficiency'] as const).map(key=><td key={key}>{c.factors[key].toFixed(3)}</td>)}<td>{c.observed_edges} / {c.estimated_edges}</td><td>{c.control_routes} control · {c.data_pairs} pairs{!c.admissible?' · would reduce connectivity':''}</td></tr>)}</tbody></table></div>
   {selection.blacklisted.map(item=><p key={item.topology}>{title(item.topology)} backed off until {simulationTimestamp(item.until)}</p>)}
  </>}
 </details>;
}
