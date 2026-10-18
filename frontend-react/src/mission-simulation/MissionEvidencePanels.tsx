import type React from 'react';
import { ChevronDown } from 'lucide-react';
import type { CommunicationRoute, MissionRoute, MissionRouteState, MissionSimulationState } from './types';
import type { MissionEvidenceStatus } from './world/evidenceFreshness';
const ROUTE_LABELS: Record<MissionRoute,string> = { direct:'Direct', satellite:'Satellite', mesh:'Mesh', hold:'Network hold' };
const TELEMETRY_LABELS: Record<string,string> = {rssi:'RSSI',pdr:'PDR',sinr:'SINR',latency:'Latency',packet_loss:'Packet loss'};
const formatPercent = (value?:number,digits=1) => value == null || !Number.isFinite(value) ? '—' : `${(value*100).toFixed(digits)}%`;
const formatMetric = (value:number|undefined,unit:string) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(0)} ${unit}`;
const titleCase = (value:string) => value.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
function routeHealth(route:MissionRouteState,noSafeRoute:boolean) {
 if(!route.available)return 'Unavailable';
 if(route.safe===false || (noSafeRoute && route.safe===undefined && route.threatScore!==undefined))return 'Unsafe';
 if(route.jammed || (route.metric?.packet_loss??0)>=.2)return 'Degraded';
 return route.active?'Active':'Available';
}
export function RouteSelector({ state, inspectedRoute, evidence, onInspect }: {
  state: MissionSimulationState;
  inspectedRoute: CommunicationRoute;
  evidence: MissionEvidenceStatus;
  onInspect: (route: CommunicationRoute) => void;
}) {
  return (
    <div className="mission-route-strip" aria-label="Communication routes">
      {state.routes.map(route => {
        const requested = state.requestedRoute === route.id && !route.active;
        const health = routeHealth(route, state.noSafeRoute);
        const label = !evidence.evidenceCurrent
          ? !evidence.lastSnapshot ? 'No snapshot'
            : route.active ? 'Last installed'
              : requested ? 'Last requested'
                : route.metric ? 'Last metric' : 'Unknown'
          : requested ? state.sdnApplied === false || state.sdnError ? 'Request not confirmed' : 'Requested · not installed' : state.sdnApplied === false && route.active ? 'Last confirmed' : health;
        return (
        <button
          type="button"
          key={route.id}
          className={`mission-route-option${route.active ? ' mission-route-option--active' : ''}${requested ? ' mission-route-option--requested' : ''}${inspectedRoute === route.id ? ' mission-route-option--inspected' : ''}${evidence.evidenceCurrent ? '' : ' mission-route-option--retained'}`}
          onClick={() => onInspect(route.id)}
          aria-pressed={inspectedRoute === route.id}
          disabled={evidence.evidenceCurrent && !route.available}
        >
          <span className="mission-route-option__name">{ROUTE_LABELS[route.id]}</span>
          <span className="mission-route-option__latency">{formatMetric(route.metric?.latency, 'ms')}</span>
          <span className="mission-route-option__threat">Threat {formatPercent(route.threatScore, 0)}</span>
          <span className={`mission-route-option__state mission-route-option__state--${requested ? 'requested' : health.toLowerCase()}`}>
            {route.active && evidence.evidenceCurrent ? '● ' : ''}{label}
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

