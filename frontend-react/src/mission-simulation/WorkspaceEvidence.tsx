import { AssistanceStatus } from './UavAssistance';
import { useState } from 'react';
import { RouteSelector, DecisionEvidence } from './MissionEvidencePanels';
import DecisionPanel from './DecisionPanel';
import { workspaceEvidence } from './world/workspaceEvidence';
import { withMissionConditions } from './world/missionConditions';
import { withMissionPathEvidence } from './world/missionPathEvidence';
import { routeHealth } from './world/routeHealth';
import type { WorkspaceEvidenceInput } from './world/workspaceEvidence';
import type { CommunicationRoute } from './types';
import type { SwarmSnapshot } from './swarm';
import { receiptEvidence } from './world/receiptEvidence';
import { AutonomyStatus } from './AutonomyPanel';
export default function WorkspaceEvidence({input,snapshot,inspected,nowMs,current,tab}:{tab:'telemetry'|'decisions'|'activity';input:WorkspaceEvidenceInput;snapshot:SwarmSnapshot;inspected:string;nowMs:number;current:boolean}) {
  const [route,setRoute] = useState<CommunicationRoute>('direct');
  const {state:observedState,evidence:legacyEvidence,controllerProgress,phase,observation} = workspaceEvidence(input,snapshot.session_id,inspected,nowMs);
  const evidence=receiptEvidence(snapshot,inspected,observation,current)??legacyEvidence;
  const state=withMissionPathEvidence(withMissionConditions(observedState,snapshot,inspected,current),snapshot,inspected,current);
  const node=snapshot.nodes.find(n=>n.id===inspected);
  const installed=snapshot.flows.find(f=>f.source===inspected&&f.destination==='base'&&f.traffic_class==='telemetry');
  const selected=state.routes.find(r=>r.id===route),metric=selected?.measurementUnavailable?undefined:selected?.metric;
  return <section className="workspace-evidence" aria-label="Selected UAV evidence" data-evidence-current={evidence.evidenceCurrent}>
    <div className="workspace-evidence-heading"><div><h3>{node?.alias??'Select a UAV'} <span>→ Ground control</span></h3></div><span className={evidence.evidenceCurrent?'evidence-fresh':'evidence-retained'}>{evidence.label}</span></div>
    <div role="tabpanel" id="research-details-panel-telemetry" aria-label="Telemetry" hidden={tab!=='telemetry'}><RouteSelector state={state} inspectedRoute={route} evidence={evidence} onInspect={setRoute} explainHealth/>
    <div className="workspace-radio-context" data-interference-context={!current||!snapshot.attacks?'unknown':state.jammedPaths.length?'injected':'none'}>
      <span>{current&&snapshot.attacks?state.jammedPaths.length?`Injected RF interference · ${state.jammedPaths.join(', ')}`:'No injected RF interference':'Injection state unavailable'}</span>
      <span>Detector scores and packet loss describe different evidence.</span>
    </div>
    {selected&&<p className="workspace-path-cause" role="status">{routeHealth(selected,state.noSafeRoute).reason}</p>}
    {selected?.recoveryPending&&<p className="workspace-recovery-note" role="status">Condition ended · waiting for new telemetry. Previous risk and measurements are retained.</p>}
    <div className="workspace-path-detail"><span>{selected?.recoveryPending?'Last ':''}{route.charAt(0).toUpperCase()+route.slice(1)} measurements</span><dl>{[
      ['RSSI',metric?`${metric.rssi.toFixed(1)} dBm`:'—'],['SINR',metric?`${metric.sinr.toFixed(1)} dB`:'—'],
      ['Delivery',metric?`${(metric.pdr*100).toFixed(1)}%`:'—'],['Loss',metric?`${(metric.packet_loss*100).toFixed(1)}%`:'—'],
    ].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>
    <details className="network-stat-details"><summary>Packet statistics</summary><dl>{Object.entries({'Generated':snapshot.statistics.generated,'Delivered':snapshot.statistics.delivered,'Dropped':snapshot.statistics.dropped,'Pending':snapshot.statistics.pending??snapshot.packets.length,'Failed routes':snapshot.statistics.failed_routes,'Reroutes':snapshot.statistics.reroutes,'Jitter':snapshot.statistics.delivered>1?`${snapshot.statistics.jitter_ms?.toFixed(2)??'—'} ms`:'—','Ground mesh reachability':`${(snapshot.statistics.availability*100).toFixed(1)}%`}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value??'—'}</dd></div>)}</dl></details></div>
    <div role="tabpanel" id="research-details-panel-decisions" aria-label="Decisions" hidden={tab!=='decisions'}><DecisionPanel state={state} phase={phase} controller={input.controller} evidenceCurrent={evidence.evidenceCurrent} lastSnapshot={evidence.lastSnapshot} controllerProgress={controllerProgress}/>
    <AutonomyStatus snapshot={snapshot} inspected={inspected} current={current}/>
    <AssistanceStatus snapshot={snapshot} inspected={inspected} current={current} detailed/>
    <div className="workspace-installed-hops"><span>{current?'Current simulated forwarding':'Last reported forwarding'}</span><strong>{installed ? installed.family === 'hold' ? 'Network HOLD installed' : `${installed.family.toUpperCase()} · ${installed.hops.map(id=>snapshot.nodes.find(n=>n.id===id)?.alias??(id==='base'?'Ground control':id)).join(' → ')}` : 'No installed telemetry route'}</strong></div>
    {snapshot.observability&&<p className="swarm-note" aria-label="Inspected report status">Report: {snapshot.observability.nodes.find(n=>n.node===inspected)?.status??'Unavailable'} · {snapshot.observability.fresh} / {snapshot.observability.eligible} eligible UAVs have fresh reports. Packet receipts do not establish a detector verdict.</p>}
    {state.explanation ? <DecisionEvidence state={state} evidenceCurrent={evidence.evidenceCurrent}/> : <details className="workspace-attribution-empty"><summary>Decision evidence</summary><p>Model attribution is unavailable for this observation.</p></details>}
    </div>
  </section>;
}
