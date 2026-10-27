import type { SwarmSnapshot } from './swarm';
import { title } from './swarm';
import { simulationTimestamp, topologyStatus } from './world/topologySelection';

export function ActiveTopology({snapshot,stale}:{snapshot:SwarmSnapshot;stale:boolean}){
 const selection=snapshot.topology_selection;
 return <div className="workspace-topology-status" aria-label="Topology selection" role="status">
  <strong>Active: {title(snapshot.topology)}</strong>
  <span>{topologyStatus(selection,stale)}</span>
  {selection?.requested_target&&<span className="topology-transition">Requested: {title(selection.requested_target)} · {selection.transition_state==='preparing'?'Preparing protected transition':selection.transition_state==='overlap'?'Finishing captured packets':selection.transition_state==='draining'?'Draining mesh traffic':'Validating forwarding'}</span>}
  {selection?.transition_state==='overlap'&&<span>Temporary connections · {selection.resilience?.active_bindings??0} captured packets</span>}
 </div>;
}

export function TopologyDiagnostics({snapshot}:{snapshot:SwarmSnapshot}){
 const selection=snapshot.topology_selection;
 return <details className="topology-diagnostics"><summary>Topology selection details</summary>
  {!selection?<p>Selection evidence unavailable.</p>:<>
   <dl><div><dt>Mode</dt><dd>{selection.mode==='auto'?selection.policy_id==='evidence_resilience_v1'?'Auto (safeguarded)':'Auto (balanced)':'Manual override'}</dd></div><div><dt>Evaluated</dt><dd>{simulationTimestamp(selection.evaluated_at)}</dd></div><div><dt>Last switch</dt><dd>{simulationTimestamp(selection.switched_at)}</dd></div><div><dt>Cooldown</dt><dd>{selection.cooldown_remaining_s.toFixed(1)} simulation seconds{!snapshot.playing?' · Paused':''}</dd></div></dl>
   <p>{selection.reason}</p>
   <p className="swarm-note">{selection.evidence==='nominal_schedule_estimate'?'Nominal traffic estimates':'Observed offered mesh traffic over the last 10 seconds'}. Hop estimates use measured mesh samples only after eight observations; unobserved hops use the radio model. Resource efficiency is a link and relay-load proxy.</p>
   {snapshot.observability&&<section aria-label="Security report delivery"><h4>Security report delivery</h4><p>{snapshot.observability.fresh} fresh / {snapshot.observability.eligible} eligible · {snapshot.observability.total} total{!snapshot.playing?' · Paused':''}</p><p className="swarm-note">Reports must arrive within {snapshot.observability.freshness_s} simulation seconds. Delivery is separate from detection. Protected airtime is work-conserving; unmet budgets remain unavailable.</p><dl>{Object.entries(snapshot.observability.statuses).map(([status,count])=><div key={status}><dt>{status}</dt><dd>{count}</dd></div>)}</dl></section>}
   {selection.resilience?.last_plan&&<section aria-label="Protected transition"><h4>Protected transition · {selection.resilience.last_plan.status}</h4><dl><div><dt>Captured packets</dt><dd>{selection.resilience.last_plan.captured_packets}</dd></div><div><dt>Protected packets</dt><dd>{selection.resilience.last_plan.protected_packets}</dd></div><div><dt>Overlap deadline</dt><dd>{simulationTimestamp(selection.resilience.last_plan.overlap_deadline)}</dd></div><div><dt>Predicted deadline misses</dt><dd>{selection.resilience.last_plan.predicted.missed_packet_count} · estimate</dd></div><div><dt>Pending packet outcomes</dt><dd>{Math.max(0,selection.resilience.last_plan.captured_packets-selection.resilience.last_plan.realized.delivered-selection.resilience.last_plan.realized.dropped)}</dd></div><div><dt>Actual delivered / dropped</dt><dd>{selection.resilience.last_plan.realized.delivered} / {selection.resilience.last_plan.realized.dropped}</dd></div></dl></section>}
   {selection.fallback&&<p role="alert" className="swarm-error">{selection.fallback}</p>}
   <div className="topology-score-scroll"><table><caption>Candidate estimates · higher scores are better</caption><thead><tr><th>Topology</th><th>Net score</th><th>Reliability</th><th>Latency</th><th>Capacity</th><th>Efficiency</th><th>Hops measured / estimated</th><th>Connectivity</th></tr></thead><tbody>{selection.candidates.map(c=><tr key={c.topology} aria-current={c.topology===snapshot.topology?'true':undefined}><th scope="row">{title(c.topology)}</th><td>{c.net_score.toFixed(3)}<small> −{c.transition_penalty.toFixed(3)} transition</small></td>{(['reliability','latency','capacity','efficiency'] as const).map(key=><td key={key}>{c.factors[key].toFixed(3)}</td>)}<td>{c.observed_edges} / {c.estimated_edges}</td><td>{c.control_routes} control · {c.data_pairs} pairs{!c.admissible?' · would reduce connectivity':''}{c.service_admissible===false?' · service budget rejected':''}{c.resilience&&<small>{c.resilience.feasible_reports.length} predicted fresh reports · {c.resilience.missed_packet_count} predicted deadline misses</small>}</td></tr>)}</tbody></table></div>
   {selection.blacklisted.map(item=><p key={item.topology}>{title(item.topology)} backed off until {simulationTimestamp(item.until)}</p>)}
  </>}
 </details>;
}
