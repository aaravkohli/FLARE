import type React from 'react';
import { ChevronDown } from 'lucide-react';
import type { CommunicationRoute, MissionRoute, MissionSimulationState } from './types';
import type { MissionEvidenceStatus } from './world/evidenceFreshness';
import { routeHealth } from './world/routeHealth';
const ROUTE_LABELS: Record<MissionRoute,string> = { direct:'Direct', satellite:'Satellite', mesh:'Mesh', hold:'Network hold' };
const TELEMETRY_LABELS: Record<string,string> = {rssi:'RSSI',pdr:'PDR',sinr:'SINR',latency:'Latency',packet_loss:'Packet loss'};
const formatPercent = (value?:number,digits=1) => value == null || !Number.isFinite(value) ? '—' : `${(value*100).toFixed(digits)}%`;
const formatMetric = (value:number|undefined,unit:string) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(0)} ${unit}`;
const titleCase = (value:string) => value.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
export function RouteSelector({ state, inspectedRoute, evidence, onInspect, explainHealth=false }: {
  state: MissionSimulationState;
  inspectedRoute: CommunicationRoute;
  evidence: MissionEvidenceStatus;
  onInspect: (route: CommunicationRoute) => void;
  explainHealth?: boolean;
}) {
  return (
    <div className="mission-route-strip" aria-label="Communication routes">
      {state.routes.map(route => {
        const requested = state.requestedRoute === route.id && !route.active;
        const status = routeHealth(route, state.noSafeRoute), health = status.health;
        const label = !evidence.evidenceCurrent
          ? !evidence.lastSnapshot ? 'No snapshot'
            : route.active ? 'Last installed'
              : requested ? 'Last requested'
                : route.metric ? 'Last metric' : 'Unknown'
          : explainHealth&&!route.available ? status.label : requested ? state.sdnApplied === false || state.sdnError ? 'Request not confirmed' : 'Requested · not installed' : state.sdnApplied === false && route.active ? 'Last confirmed' : explainHealth ? status.label : health;
        return (
        <button
          type="button"
          key={route.id}
          className={`mission-route-option${route.active ? ' mission-route-option--active' : ''}${requested ? ' mission-route-option--requested' : ''}${inspectedRoute === route.id ? ' mission-route-option--inspected' : ''}${evidence.evidenceCurrent ? '' : ' mission-route-option--retained'}`}
          onClick={() => onInspect(route.id)}
          aria-pressed={inspectedRoute === route.id}
          data-interference={explainHealth ? route.jammed?'active':'clear' : undefined}
          disabled={!explainHealth && evidence.evidenceCurrent && !route.available}
          title={explainHealth ? `${evidence.evidenceCurrent?'':'Retained observation. '}${status.reason}` : undefined}
        >
          <span className="mission-route-option__name">{ROUTE_LABELS[route.id]}</span>
          <span className="mission-route-option__latency">{formatMetric(explainHealth&&route.measurementUnavailable?undefined:route.metric?.latency, 'ms')}</span>
          <span className="mission-route-option__threat">{explainHealth?route.recoveryPending||!evidence.evidenceCurrent?'Last detector':'Detector':'Threat'} {formatPercent(route.threatScore, 0)}</span>
          <span className={`mission-route-option__state mission-route-option__state--${requested ? 'requested' : health.toLowerCase()}`}>
            {route.active && evidence.evidenceCurrent && (!explainHealth||route.available) ? '● ' : ''}{label}
          </span>
        </button>
      )})}
    </div>
  );
}

export function DecisionEvidence({ state, evidenceCurrent }: { state: MissionSimulationState; evidenceCurrent: boolean }) {
  if (!state.explanation) return null;
  return (
    <details className="mission-evidence">
      <summary>
        <ChevronDown aria-hidden="true" />
        <span>{evidenceCurrent ? 'Decision evidence' : 'Last snapshot · decision evidence'} <small>Integrated Gradients · model attribution</small></span>
        <b>{formatPercent(state.explanation.normalized_completeness_error, 2)} error</b>
      </summary>
      <div className="mission-evidence__grid">
        {state.explanation.feature_attributions.map(item => (
          <div key={item.feature}>
            <span>{TELEMETRY_LABELS[item.feature.toLowerCase()] || titleCase(item.feature)}</span>
            <strong>{formatPercent(item.importance)}</strong>
            <i
              className={item.signed_attribution >= 0 ? 'is-positive' : 'is-negative'}
              style={{ '--evidence-width': `${Math.min(100, item.importance * 100)}%` } as React.CSSProperties}
            />
          </div>
        ))}
      </div>
      <p>Attribution indicates model sensitivity, not causal proof. {state.explanation.method} · target {state.explanation.target.head}[{state.explanation.target.index}]</p>
    </details>
  );
}

