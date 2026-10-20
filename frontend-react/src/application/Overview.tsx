import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { getMissionSnapshot, subscribeMission } from '../mission-simulation/swarm';
import type { TelemetryPayload, SdnReadinessState, RoutingExplanation } from '../App';
import { missionEvidenceStatus } from '../mission-simulation/world/evidenceFreshness';
import { fleetMeasurement, metric, signed, title } from './presentation';

export function ContributionChart({ explanation }: { explanation?: RoutingExplanation | null }) {
  const data = explanation?.explanation;
  if (!explanation?.available || !data) return <p className="empty-state">Unavailable: {explanation?.reason?.replaceAll('_', ' ') ?? 'awaiting a persisted DQN event'}</p>;
  const features = [...data.feature_attributions].sort((a, b) => Math.abs(b.signed_attribution) - Math.abs(a.signed_attribution));
  const extent = Math.max(...features.map(f => Math.abs(f.signed_attribution)), 0.000001);
  return <section className="contribution-chart" aria-label="Contributions to selected action Q-value"><h3>Contributions to selected action Q-value</h3><p>Policy {explanation.policy_path ?? data.action} · Installed {explanation.installed_path ?? 'unconfirmed'}{explanation.safety_override ? ' · Safety override' : ''}</p>
    <div className="contribution-values"><span>Selected action Q <strong>{data.prediction}</strong></span><span>Baseline Q <strong>{data.baseline_prediction}</strong></span></div>
    {features.map(item => <div className="contribution-row" key={item.feature}><span>{item.feature.replaceAll('_', ' ')}</span><span className="contribution-track" aria-hidden="true"><i style={{ left: item.signed_attribution < 0 ? `${50 - Math.abs(item.signed_attribution) / extent * 50}%` : '50%', width: `${Math.abs(item.signed_attribution) / extent * 50}%` }} className={item.signed_attribution < 0 ? 'negative' : 'positive'} /></span><output>{signed(item.signed_attribution)}</output></div>)}
    {!features.length && <p className="empty-state">Feature contributions unavailable.</p>}<p className="subtle">Local diagnostic; attribution does not establish causality. Completeness error {metric(data.normalized_completeness_error, 6)}.</p>
  </section>;
}
export default function Overview({ mode, connection, controller, readiness, controllerEvent, activeDrone, activeName, payload, receivedAtMs, unavailable, charts, children }: { mode: string; connection: 'connecting'|'connected'|'disconnected'; controller: string; readiness: SdnReadinessState | null; controllerEvent?: {message:string;timestamp:number}; activeDrone: string; activeName: string; payload: TelemetryPayload | null; receivedAtMs: number|null; unavailable: string | null; charts: ReactNode; children: ReactNode }) {
  const mission = useSyncExternalStore(subscribeMission, getMissionSnapshot);
  const measurement = fleetMeasurement(activeDrone, mission, payload ?? undefined);
  const [now,setNow] = useState(() => performance.now());
  useEffect(() => {const timer=setInterval(() => setNow(performance.now()),1000);return () => clearInterval(timer);},[]);
  const currentPayload = payload?.metrics.drone_id === activeDrone && (!mission || payload.event?.mission_context?.session_id === mission.session_id) ? payload : null;
  const freshness = missionEvidenceStatus({payload:currentPayload,selectedDroneId:activeDrone,transportStatus:connection,receivedAtMs,nowMs:now,telemetryUnavailable:unavailable});
  const event = currentPayload?.event;
  const detector = event?.network_security_analysis;
  const age = freshness.ageSeconds;
  const available = readiness?.available_paths?.[activeDrone];
  const unavailableFamilies = available ? ['direct','satellite','mesh'].filter(family => !available.includes(family)) : [];
  return <div className="application-section overview-page"><div className="page-heading"><div><h1>Overview</h1><p>{title(mode)} · {activeName}</p></div><a className="button-link" href="#mission">Open Mission</a></div>
    <div className="overview-status" aria-label="System status"><div><span>Telemetry link</span><strong className={connection === 'connected' ? 'tone-success' : 'tone-warning'}>{title(connection)}</strong></div><div><span>Controller</span><strong>{controller}</strong></div><div><span>Readiness</span><strong>{readiness ? readiness.ready ? 'Ready' : title(readiness.status) : 'Checking'}</strong></div><div><span>Inspected UAV</span><strong>{activeName}</strong></div></div>
    {controllerEvent && <details className="controller-event"><summary><time>{new Date(controllerEvent.timestamp * 1000).toLocaleTimeString()}</time> Controller update</summary><p>{controllerEvent.message}</p></details>}
    {readiness && !readiness.ready && <p role="alert" className="application-notice">{readiness.alert?.message || readiness.error || `Controller ${title(readiness.status)}. Routing changes may be unavailable.`}</p>}
    {readiness?.ready && unavailableFamilies.length > 0 && <p className="application-notice" role="status">{unavailableFamilies.map(title).join(', ')} unavailable for {activeName}. Available: {available?.map(title).join(', ') || 'none'}.</p>}
    {unavailable && <p className="application-notice" role="status">{unavailable}</p>}
    <div className="overview-decision" aria-label="Decision summary"><div><span>Installed family</span><strong>{measurement.route ? title(measurement.route) : 'Unconfirmed'}</strong></div><div><span>Detector observation</span><strong>{detector ? `${freshness.lastSnapshot ? 'Retained · ' : ''}${title(detector.status)}` : 'Unavailable'}</strong></div><div><span>Evidence freshness</span><strong>{age == null ? freshness.label : `${freshness.evidenceCurrent ? "Fresh" : "Retained"} · ${metric(age,1," s")}`}</strong></div><div><span>Policy request</span><strong>{event?.decision?.requested_path ? title(event.decision.requested_path) : '—'}</strong></div><div><span>Controller acknowledgment</span><strong>{event?.sdn?.applied === true ? 'Applied' : event?.sdn?.applied === false ? 'Not applied' : 'Unavailable'}</strong></div></div>
    <section className="network-health" aria-labelledby="network-health-title"><div className="section-heading"><h2 id="network-health-title">Network Health</h2><span>{activeName} · Direct family · latest 20 observations</span></div>{charts}</section>
    <details className="decision-disclosure"><summary>Decision Evidence <span>Provenance, models, and routing attribution</span></summary>{children}</details>
    <details className="research-resource"><summary>Research resources</summary><a href={`${import.meta.env.BASE_URL}mission-demo.html`}>Recorded single-UAV simulation · Direct → Satellite · mock SDN</a></details>
  </div>;
}
