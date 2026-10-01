import type { MissionSimulationState } from './types';
import { PHASE_LABELS } from './world/decisionPresentation';
import type { DecisionPhase } from './world/decisionPresentation';
const number=(n:number|null|undefined,unit='',scale=1)=>n!=null&&Number.isFinite(n)?`${(n*scale).toFixed(unit==='%'?1:2)}${unit}`:'—';
const route=(s:string|null|undefined)=>s?s.replaceAll('_',' '):'—';
type ControllerProgress = 'awaiting' | 'outcome-unknown' | 'acknowledged' | 'not-confirmed' | null;
export default function DecisionPanel({state:s,phase,controller,evidenceCurrent=true,lastSnapshot=false,controllerProgress=null,recordedReplay=false}:{state:MissionSimulationState;phase:DecisionPhase;controller:string;evidenceCurrent?:boolean;lastSnapshot?:boolean;controllerProgress?:ControllerProgress;recordedReplay?:boolean}) {
  const safetyKnown=s.reportedSafetyOverride!==undefined||s.safeActionMask!==undefined;
  const inference=s.inferenceSource==='fl_model'?'BiLSTM':s.inferenceSource==='heuristic_fallback'?'Heuristic fallback':'Threat assessment';
  const policy=s.decisionSource==='rl_model'?'DQN':s.decisionSource==='greedy_fallback'?'Greedy fallback':'Policy';
  const holdReason=s.constraintReason==='containment_required'?'Containment required':s.noSafeRoute?'No safe route reported':'Runtime HOLD';
  const installed=s.installedRoute==='hold'?(evidenceCurrent&&s.sdnApplied===true?'Forwarding suspended':'HOLD last confirmed'):s.installedRoute?`${s.sdnApplied===false?'Last confirmed':evidenceCurrent&&s.sdnApplied===true?'Installed':'Last installed'}: ${s.installedRoute}`:'Installation unknown';
  const controllerDetail=controllerProgress==='awaiting'?'Awaiting controller response'
    :controllerProgress==='outcome-unknown'?'Request outcome unknown · evidence stale'
    :controllerProgress==='acknowledged'?'Controller acknowledged · awaiting canonical state'
    :controllerProgress==='not-confirmed'?'Controller did not confirm apply'
    :s.sdnError?'Last update failed':s.sdnApplied===true?s.installedRoute==='hold'?holdReason:recordedReplay?'Apply confirmed in recorded event':evidenceCurrent?'Apply confirmed':'Apply was confirmed in snapshot':s.sdnApplied===false?'Apply not confirmed':'Apply status unavailable';
  return <section className="mission-decision" aria-label="FLARE decision pipeline">
    <div className="mission-decision__heading"><strong>{recordedReplay?'Recorded frame · ':lastSnapshot?'Last snapshot · ':''}Detection → Policy → Safety → Controller</strong><span role="status">{evidenceCurrent?PHASE_LABELS[phase]:lastSnapshot?'Retained decision':'Awaiting evidence'}</span></div>
    <div className="mission-decision__stages">
      <div><span>{inference}</span><strong>{s.reportedThreatLevel??'Unavailable'}</strong><small>{s.networkDetection?.detected_classes.length?`Network detector: ${s.networkDetection.detected_classes.join(', ')}`:s.inferenceSource??'Source unavailable'}</small></div>
      <div><span>{policy}</span><strong>{route(s.policyRoute)}</strong><small>Requested: {route(s.requestedRoute)}</small></div>
      <div><span>Safety gate</span><strong>{!safetyKnown?'Unavailable':s.safetyOverride?'Override reported':s.noSafeRoute?'No safe route':'No override reported'}</strong><small>{s.constraintReason?route(s.constraintReason):s.containmentMode&&s.containmentMode!=='normal'?`Containment: ${route(s.containmentMode)}`:'Reason: —'}</small></div>
      <div><span>SDN · {controller}</span><strong>{installed}</strong><small>{controllerDetail}</small></div>
    </div>
    {s.timestamp!=null && <details className="mission-decision__explain">
      <summary>Explain decision</summary>
      <div className="mission-decision__table"><table><caption>{recordedReplay?'Recorded frame · ':lastSnapshot?'Last snapshot · ':''}Reported route evidence · {s.source??'source unavailable'}</caption><thead><tr><th>Route</th><th>Threat</th><th>Latency</th><th>Loss</th><th>Safety</th><th>Installed</th></tr></thead><tbody>
        {s.routes.map(r=><tr key={r.id}><th scope="row">{r.id}</th><td>{number(r.threatScore)}</td><td>{number(r.metric?.latency,' ms')}</td><td>{number(r.metric?.packet_loss,'%',100)}</td><td>{r.safe===true?'Safe':r.safe===false?'Unsafe':'—'}</td><td>{s.installedRoute==null?'—':s.installedRoute===r.id?'Yes':'No'}</td></tr>)}
      </tbody></table></div>
      <dl><div><dt>Policy route</dt><dd>{route(s.policyRoute)}</dd></div><div><dt>Requested route</dt><dd>{route(s.requestedRoute)}</dd></div><div><dt>Installed route</dt><dd>{route(s.installedRoute)}</dd></div><div><dt>Safety override</dt><dd>{s.reportedSafetyOverride===undefined?'—':s.reportedSafetyOverride?'Yes':'No'}</dd></div><div><dt>SDN applied</dt><dd>{s.sdnApplied===undefined?'—':s.sdnApplied?'Yes':'No'}</dd></div><div><dt>Outcome</dt><dd>{s.outcomeCurrent?'Reported in completed snapshot':'Pending or unreported'}</dd></div><div><dt>Control cycle</dt><dd>{number(s.tickElapsedMs,' ms')}</dd></div></dl>
      {s.sdnError&&<p role="status">SDN error: {s.sdnError}</p>}
      <p>{controllerProgress==='awaiting'?'Pre-controller request snapshot; installation and reward await a response.':controllerProgress==='outcome-unknown'?'Request evidence is stale; controller installation and reward are unknown.':s.outcomeCurrent?'Completed runtime snapshot. Individual inference-stage durations are not supplied.':'Controller outcome is pending or unreported.'}</p>
    </details>}
  </section>;
}
