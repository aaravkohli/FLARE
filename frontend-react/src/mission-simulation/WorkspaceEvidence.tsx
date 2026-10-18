import { useState } from 'react';
import { RouteSelector, DecisionEvidence } from './MissionEvidencePanels';
import DecisionPanel from './DecisionPanel';
import { workspaceEvidence } from './world/workspaceEvidence';
import type { WorkspaceEvidenceInput } from './world/workspaceEvidence';
import type { CommunicationRoute } from './types';
import type { SwarmSnapshot } from './swarm';
export default function WorkspaceEvidence({input,snapshot,inspected,nowMs,current}:{input:WorkspaceEvidenceInput;snapshot:SwarmSnapshot;inspected:string;nowMs:number;current:boolean}) {
  const [route,setRoute] = useState<CommunicationRoute>('direct');
  const {state,evidence,controllerProgress,phase} = workspaceEvidence(input,snapshot.session_id,inspected,nowMs);
  const node=snapshot.nodes.find(n=>n.id===inspected);
  const installed=snapshot.flows.find(f=>f.source===inspected&&f.destination==='base'&&f.traffic_class==='telemetry');
  const metric=state.routes.find(r=>r.id===route)?.metric;
  return <section className="workspace-evidence" aria-label="Selected UAV evidence" data-evidence-current={evidence.evidenceCurrent}>
    <div className="workspace-evidence-heading"><div><span className="swarm-eyebrow">COMMUNICATION & RESPONSE</span><h3>{node?.alias??'Select a UAV'} <span>→ Ground control</span></h3></div><span className={evidence.evidenceCurrent?'evidence-fresh':'evidence-retained'}>{evidence.label}</span></div>
    <RouteSelector state={state} inspectedRoute={route} evidence={evidence} onInspect={setRoute}/>
    <div className="workspace-path-detail"><span>{route.charAt(0).toUpperCase()+route.slice(1)} measurements</span><dl>{[
      ['RSSI',metric?`${metric.rssi.toFixed(1)} dBm`:'—'],['SINR',metric?`${metric.sinr.toFixed(1)} dB`:'—'],
      ['Delivery',metric?`${(metric.pdr*100).toFixed(1)}%`:'—'],['Loss',metric?`${(metric.packet_loss*100).toFixed(1)}%`:'—'],
    ].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>
    <DecisionPanel state={state} phase={phase} controller={input.controller} evidenceCurrent={evidence.evidenceCurrent} lastSnapshot={evidence.lastSnapshot} controllerProgress={controllerProgress}/>
    <div className="workspace-installed-hops"><span>{current?'Current simulated forwarding':'Last reported forwarding'}</span><strong>{installed ? installed.family === 'hold' ? 'Network HOLD installed' : `${installed.family.toUpperCase()} · ${installed.hops.map(id=>snapshot.nodes.find(n=>n.id===id)?.alias??(id==='base'?'Ground control':id)).join(' → ')}` : 'No installed telemetry route'}</strong></div>
    {state.explanation ? <DecisionEvidence state={state} evidenceCurrent={evidence.evidenceCurrent}/> : <details className="workspace-attribution-empty"><summary>Decision evidence</summary><p>Model attribution is unavailable for this observation.</p></details>}
  </section>;
}
