import { publishPacketEvents, publishMissionSnapshot, clearMissionSnapshot, getMissionSnapshot, inspectMissionTarget, subscribeMission } from './mission-simulation/swarm';
import SwarmCommandCenter from './mission-simulation/SwarmCommandCenter';
import type { PacketBatch, SwarmSnapshot } from './mission-simulation/swarm';
import React, { useState, useEffect, useRef } from 'react';
import { WifiOff, ShieldCheck, RefreshCw, UserPlus } from 'lucide-react';
import './App.css';
import SignIn from './sign-in/SignIn';
import { ApplicationHeader, SectionTabs } from './application/ApplicationShell';
import { useApplicationNavigation } from './application/useApplicationNavigation';
import Overview, { ContributionChart } from './application/Overview';
import Fleet from './application/Fleet';
import SecurityMonitoring from './application/SecurityMonitoring';
import Validation from './application/Validation';
import ActivityDrawer from './application/Activity';
import { useTrafficValidation } from './application/useTrafficValidation';
import type { ActivityEntry, ChartDataPoint } from './application/presentation';
import { federationForSession } from './application/presentation';
import './application/application.css';
import LiveMissionSimulation from './mission-simulation/LiveMissionSimulation';
import SecureFlControlCenter from './fl-control-center/SecureFlControlCenter';
import type {
  AttackProfile,
  CommunicationRoute,
  MissionTelemetryPayload,
} from './mission-simulation/types';
import {
  initialMissionRouteTransitionState,
  missionPayloadFromCompletion,
  parseMissionRouteTransition,
  reduceMissionRouteTransition,
} from './mission-simulation/routeTransition';
import type { MissionRouteTransitionState } from './mission-simulation/routeTransition';

function resolveApiBaseUrl(): string {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl && typeof envUrl === 'string' && envUrl.trim() !== '') {
    return envUrl.trim().replace(/\/$/, '');
  }
  if (typeof window !== 'undefined' && window.location) {
    const { hostname, protocol, port } = window.location;
    if (port === '80' || port === '443' || port === '') {
      return '';
    }
    return `${protocol}//${hostname}:8000`;
  }
  return 'http://127.0.0.1:8000';
}

function resolveWsBaseUrl(apiBase: string): string {
  if (typeof window !== 'undefined' && window.location) {
    const isHttps = window.location.protocol === 'https:';
    const wsScheme = isHttps ? 'wss:' : 'ws:';
    if (!apiBase || apiBase.startsWith('/')) {
      return `${wsScheme}//${window.location.host}${apiBase}`;
    }
    return apiBase.replace(/^https?:/, wsScheme);
  }
  return apiBase.replace(/^http/, 'ws');
}

const API_BASE_URL = resolveApiBaseUrl();
const WS_BASE_URL = resolveWsBaseUrl(API_BASE_URL);
const TelemetryCharts = React.lazy(() => import('./TelemetryCharts'));

type DroneId = string;
export type InsiderProfile =
  | 'normal'
  | 'selective_forwarding'
  | 'telemetry_falsification'
  | 'control_flood'
  | 'replay';

// Type definitions
interface MetricDetail {
  path_id: string;
  rssi: number;
  pdr: number;
  sinr: number;
  latency: number;
  packet_loss: number;
}

interface ReadinessTransition {
  timestamp: number;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  ready: boolean;
  status: string;
  connected_switches: number;
  expected_switches: number;
  unavailable_paths: Record<string, string[]>;
}

export interface SdnReadinessState {
  ready: boolean;
  status: string;
  connected_switches: number;
  expected_switches: number;
  available_paths: Record<string, string[]>;
  error?: string | null;
  alert?: ReadinessTransition;
}

export interface ClientSecurityMetric {
  client_id: string;
  status: 'NORMAL' | 'SUSPICIOUS' | 'MALICIOUS' | 'ATTACK_ARMED' | 'NO_UPDATE';
  action: 'ACCEPTED' | 'DOWN_WEIGHTED' | 'REJECTED' | 'PENDING';
  trust_score?: number;
  deviation_score?: number;
  update_norm?: number;
  cosine_similarity?: number;
  aggregation_weight?: number;
  reported_num_examples?: number;
  effective_num_examples?: number;
  simulation_mode?: 'normal' | 'noisy' | 'poisoned';
}

export interface FleetDrone {
  drone_id: DroneId;
  display_name: string;
  index: number;
  mac: string;
  access_port: number;
  enabled: boolean;
  rf_profile: {
    rssi_offset: number;
    pdr_offset: number;
    latency_factor: number;
  };
  fl_client?: { status: string; pid?: number | null; dataset?: string | null };
}

interface FlClientManagerStatus {
  status: string;
  mode?: string;
  age_s?: number;
  clients?: Record<string, { status: string; pid?: number | null; dataset?: string | null }>;
}

export interface FlProtectionSelfTest {
  passed: boolean;
  mode: 'isolated_shadow_test';
  protection_active: boolean;
  global_model_modified: boolean;
  trust_history_modified: boolean;
  client_modes_modified: boolean;
  aggregation_method: string;
  malicious_candidate: string;
  malicious_candidate_action: string;
  attacked_fedavg_distance: number;
  secure_aggregate_distance: number;
  damage_reduction_percent: number;
  rejected_updates: number;
  tested_updates: number;
  clients: ClientSecurityMetric[];
}

export type FlProtectionPhase = 'idle' | 'preparing' | 'analyzing' | 'verifying' | 'complete' | 'failed';

interface ModelExplanation {
  method: string;
  target: { head: string; index: number };
  prediction: number;
  normalized_completeness_error: number;
  faithfulness_passed: boolean;
  feature_attributions: Array<{
    feature: string;
    importance: number;
    signed_attribution: number;
  }>;
}

interface InsiderAnalysis {
  status: 'NORMAL' | 'SUSPICIOUS' | 'MALICIOUS';
  predicted_class: string;
  risk_score: number | null;
  instantaneous_risk: number;
  forwarding_ratio: number;
  telemetry_claim_gap: number;
  control_rate_score: number;
  replay_score: number;
  evidence_freshness: number;
}

interface ContainmentAnalysis {
  mode: 'normal' | 'restricted' | 'control_only' | 'quarantined';
  reason: string;
  trust_score: number;
  insider_risk: number;
  network_risk?: number | null;
  drivers?: string[];
  decided_at: number;
}

interface NetworkSecurityAnalysis {
  status: 'NORMAL' | 'SUSPICIOUS' | 'MALICIOUS' | 'UNAVAILABLE';
  detected_classes: string[];
  risk_score: number;
  dos_score: number;
  spoofing_score: number;
  evidence_freshness: number;
  evidence_source: string;
  evidence_independent: boolean;
  reason: string;
  available_signals?: string[];
}

export interface RoutingExplanation {
  available: boolean;
  reason?: string;
  diagnostic_only?: boolean;
  policy_path?: string;
  installed_path?: string | null;
  safety_override?: boolean;
  model_id?: string | null;
  model_generation?: number | null;
  explanation?: {
    action: string;
    routing_contract: string;
    prediction: number;
    baseline_prediction: number;
    normalized_completeness_error: number;
    feature_attributions: Array<{
      feature: string;
      signed_attribution: number;
    }>;
  };
}

export interface InsiderEvidenceSnapshot {
  available: boolean;
  reason?: string;
  current_profile: InsiderProfile;
  observed_profile?: InsiderProfile | null;
  profile_synchronized?: boolean;
  event_age_s?: number;
  analysis?: InsiderAnalysis | null;
  containment?: ContainmentAnalysis | null;
  decision?: {
    model_generation?: number | null;
    installed_path?: string | null;
    requested_path?: string;
    network_action?: string;
    constraint_reason?: string | null;
  };
  sdn_applied?: boolean;
}

interface CanonicalEventPayload {
  schema_version?: string;
  timestamp?: number;
  insider_analysis?: InsiderAnalysis | null;
  network_security_analysis?: NetworkSecurityAnalysis | null;
  containment?: ContainmentAnalysis | null;
  inference?: { model_generation?: number | null; model_id?: string | null };
  fallback_reasons?: string[];
  decision?: {
    model_generation?: number | null;
    installed_path?: string | null;
    requested_path?: string;
    network_action?: string;
    constraint_reason?: string | null;
  };
  sdn?: { applied?: boolean };
  telemetry?: {
    source?: string;
    source_collected_at?: number | null;
    source_age_s?: number | null;
    ew_status?: { active_attack?: string | null } | null;
    security_evidence?: {
      simulation_profile?: InsiderProfile | null;
      controller_policy_dropped_packets?: number | null;
      drop_counter_semantics?: string | null;
      provenance?: {
        category: string;
        observer_id: string;
        independent: boolean;
        collected_at: number;
        age_s: number;
      } | null;
    } | null;
  };
}

// Matches the new generator.py output: paths is an array, not a keyed dict
export interface TelemetryPayload extends MissionTelemetryPayload {
  event?: CanonicalEventPayload & NonNullable<MissionTelemetryPayload['event']>;
  telemetry_age_s?: number;
  explanation?: ModelExplanation | null;
  routing_explanation?: RoutingExplanation | null;
  byzantine?: ClientSecurityMetric[];
  fl_metrics?: any;
}

interface TelemetrySafetyState {
  status: 'hold_applied' | 'hold_failed' | null;
  applied: boolean | null;
}

function App() {
  const {page,navigate} = useApplicationNavigation();
  const [activityOpen,setActivityOpen] = useState(false);
  const [securityTab,setSecurityTab] = useState<'policy'|'monitoring'>('policy');
  const [fleetDetailId,setFleetDetailId] = useState('');
  const [reporting,setReporting] = useState(false);
  const [reportError,setReportError] = useState<string|null>(null);
  const [flRunSession,setFlRunSession] = useState<string|undefined>();
  // Auth state
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [swarmSnapshot,setSwarmSnapshot]=useState<SwarmSnapshot|null>(null);
  useEffect(()=>{
    if(!token){clearMissionSnapshot();setSwarmSnapshot(null);return;}
    const controller=new AbortController();
    const poll=()=>{
      if((getMissionSnapshot()?.timestamp??0)*1000>Date.now()-1500)return;
      void fetch(`${API_BASE_URL}/mission/simulation`,{headers:{Authorization:`Bearer ${token}`},signal:controller.signal}).then(r=>r.ok?r.json():null).then(data=>{if(data?.schema_version==='mission_snapshot_v1'){publishMissionSnapshot(data);setSwarmSnapshot(previous=>previous??data);}}).catch(()=>{});
    };
    poll();const timer=setInterval(poll,1000);
    return ()=>{controller.abort();clearInterval(timer);};
  },[token]);
  useEffect(() => {
    let session = getMissionSnapshot()?.session_id;
    return subscribeMission(() => {
      const next = getMissionSnapshot()?.session_id;
      if(!next || next === session)return;
      session = next;
      setMissionPayloads({});setMissionPayload(null);setHistory([]);setFlMetrics((previous:any)=>federationForSession(previous,next));setByzantineStatus([]);setInsiderAnalysis(null);setContainmentAnalysis(null);
      setActivePath('unknown');setThreatLevel('UNKNOWN');setNoSafeRoute(false);
      setTelemetrySafetyState(null);setTelemetryUnavailable('Awaiting model inference for this mission session');
    });
  },[]);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);

  // Connection & Active states
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const [activeDrone, setActiveDrone] = useState<DroneId>('drone_1');
  const [activePath, setActivePath] = useState<string>('unknown');
  const [threatLevel, setThreatLevel] = useState<string>('LOW');
  const [reward, setReward] = useState<number>(0.0);
  const [step, setStep] = useState<number>(0);
  const [noSafeRoute, setNoSafeRoute] = useState<boolean>(false);
  const [telemetryUnavailable, setTelemetryUnavailable] = useState<string | null>(null);
  const [telemetrySafetyState, setTelemetrySafetyState] = useState<TelemetrySafetyState | null>(null);
  const [systemMode, setSystemMode] = useState<string>('UNKNOWN');
  const [sdnController, setSdnController] = useState<string>('UNKNOWN');
  const [sdnReadiness, setSdnReadiness] = useState<SdnReadinessState | null>(null);
  const [readinessHistory, setReadinessHistory] = useState<ReadinessTransition[]>([]);
  const [fleetDrones, setFleetDrones] = useState<FleetDrone[]>([]);
  const [managedFleetDrones, setManagedFleetDrones] = useState<FleetDrone[]>([]);
  const [canManageFleet, setCanManageFleet] = useState(false);
  const [flClientManager, setFlClientManager] = useState<FlClientManagerStatus | null>(null);
  const [editingDrone, setEditingDrone] = useState<FleetDrone | null>(null);
  const [editDraft, setEditDraft] = useState({ display_name: '', rssi_offset: 0, pdr_offset: 0, latency_factor: 1 });
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [showEnrollment, setShowEnrollment] = useState(false);
  const [isEnrolling, setIsEnrolling] = useState(false);
  const [enrollmentError, setEnrollmentError] = useState<string | null>(null);
  const [newDrone, setNewDrone] = useState({
    drone_id: '',
    display_name: '',
    mac: '02:00:00:00:00:04',
    access_port: 7
  });
  const attackSimulationEnabled = systemMode.toLowerCase() === 'simulation';

  // Telemetry details
  const [missionPayload, setMissionPayload] = useState<TelemetryPayload | null>(null);
  const [missionReceiptAtMs, setMissionReceiptAtMs] = useState<number | null>(null);
  const [missionPayloads, setMissionPayloads] = useState<Record<DroneId, TelemetryPayload>>({});
  const [routeTransitions, setRouteTransitions] = useState<MissionRouteTransitionState>(initialMissionRouteTransitionState);
  const [routeRequestReceiptByDrone, setRouteRequestReceiptByDrone] = useState<Record<DroneId, number>>({});
  const [history, setHistory] = useState<ChartDataPoint[]>([]);

  // Control action states
  const [jamDuration, setJamDuration] = useState<number>(15);
  const [statusLog, setStatusLog] = useState<ActivityEntry[]>([]);

  // Capstone Phase 4/5 States
  const [insiderAnalysis, setInsiderAnalysis] = useState<InsiderAnalysis | null>(null);
  const [containmentAnalysis, setContainmentAnalysis] = useState<ContainmentAnalysis | null>(null);
  const [byzantineStatus, setByzantineStatus] = useState<ClientSecurityMetric[]>(
    (['drone_1', 'drone_2', 'drone_3'] as const).map((drone_id) => ({
      client_id: drone_id,
      status: 'NO_UPDATE',
      action: 'PENDING',
      simulation_mode: 'normal'
    }))
  );
  const [jamProfile, setJamProfile] = useState<string>('spot');

  // Secure federated-learning states
  const [flConfig, setFlConfig] = useState<any>(null);
  const [flMetrics, setFlMetrics] = useState<any>(null);
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [flProtectionTest, setFlProtectionTest] = useState<FlProtectionSelfTest | null>(null);
  const [isRunningFlProtectionTest, setIsRunningFlProtectionTest] = useState(false);
  const [flProtectionTestError, setFlProtectionTestError] = useState<string | null>(null);
  const [flProtectionPhase, setFlProtectionPhase] = useState<FlProtectionPhase>('idle');
  const [flProtectionRunTimeMs, setFlProtectionRunTimeMs] = useState<number | null>(null);
  const [flProtectionRunAt, setFlProtectionRunAt] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const shouldReconnectRef = useRef(false);
  const activeDroneRef = useRef(activeDrone);
  const authTokenRef = useRef(token);
  useEffect(() => {authTokenRef.current=token;},[token]);
  const routeTransitionsRef = useRef<MissionRouteTransitionState>(initialMissionRouteTransitionState);

  useEffect(() => {
    if(token)return;
    setMissionPayloads({});setMissionPayload(null);setHistory([]);setFlMetrics(null);setFlConfig(null);
    setInsiderAnalysis(null);setContainmentAnalysis(null);setRouteTransitions(initialMissionRouteTransitionState);
    routeTransitionsRef.current=initialMissionRouteTransitionState;setRouteRequestReceiptByDrone({});
    setEditingDrone(null);setFleetDetailId('');setShowEnrollment(false);setActivityOpen(false);
    setFlProtectionTest(null);setFlProtectionPhase('idle');setIsRunningFlProtectionTest(false);setReportError(null);
    setStatusLog([]);setWsStatus('disconnected');setFleetDrones([]);setManagedFleetDrones([]);setCanManageFleet(false);setFlClientManager(null);setByzantineStatus([]);
  },[token]);

  // Set up WebSocket connection when authenticated
  useEffect(() => {
    if (!token) return;

    shouldReconnectRef.current = true;
    connectWebSocket();
    fetchSystemHealth();
    fetchFlConfig();
    fetchFlMetrics();
    fetchFleet();
    const healthTimer = window.setInterval(fetchSystemHealth, 5000);
    const fleetTimer = window.setInterval(fetchFleet, 5000);

    return () => {
      window.clearInterval(healthTimer);
      window.clearInterval(fleetTimer);
      shouldReconnectRef.current = false;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    if (!token) return;

    fetchInsiderEvidence(activeDrone);
    const evidenceTimer = window.setInterval(
      () => fetchInsiderEvidence(activeDrone),
      2000
    );
    return () => window.clearInterval(evidenceTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeDrone]);

  // Keep original messages with explicit metadata only when known at the call site.
  const addLog = (message: string, meta: Partial<ActivityEntry> = {}) => {
    const entry: ActivityEntry = {...meta,id:crypto.randomUUID(),timestamp:Date.now(),message};
    setStatusLog(previous => [entry,...previous.slice(0,49)]);
  };
  const trafficValidation = useTrafficValidation(API_BASE_URL,token,activeDrone,attackSimulationEnabled,addLog);
  const inspectInMission = (id:string) => {
    inspectMissionTarget(id);
    selectDrone(id);
    navigate('mission');
  };

  const fetchSystemHealth = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (!res.ok) throw new Error('API health check failed');
      const data = await res.json();
      setSystemMode(String(data.mode || 'unknown').toUpperCase());
      setSdnController(
        data.sdn_controller === 'mock' ? 'Mock SDN' :
        data.sdn_controller === 'ryu' ? 'Ryu OpenFlow v1.3' :
        String(data.sdn_controller || 'unknown')
      );
    } catch {
      setSystemMode('UNKNOWN');
      setSdnController('UNAVAILABLE');
    }

    try {
      const res = await fetch(`${API_BASE_URL}/ready`);
      const data = await res.json();
      if (!data?.sdn) throw new Error('Invalid readiness response');
      setSdnReadiness({
        ready: Boolean(data.sdn.ready && res.ok),
        status: String(data.sdn.status || 'not_ready'),
        connected_switches: Number(data.sdn.connected_switches || 0),
        expected_switches: Number(data.sdn.expected_switches || 0),
        available_paths: data.sdn.available_paths || {},
        error: data.sdn.error || null,
        alert: data.alert
      });
    } catch {
      setSdnReadiness({
        ready: false,
        status: 'unreachable',
        connected_switches: 0,
        expected_switches: 0,
        available_paths: {},
        error: 'Readiness check unavailable'
      });
    }

    if (token) {
      try {
        const historyResponse = await fetch(`${API_BASE_URL}/ready/history?limit=5`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (historyResponse.ok) {
          const historyData = await historyResponse.json();
          setReadinessHistory(Array.isArray(historyData.events) ? historyData.events : []);
        }
      } catch {
        // Preserve the last known transition history during transient API failures.
      }
    }
  };

  // JWT Login
  const handleLogin = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isAuthenticating) return;
    setIsAuthenticating(true);
    setAuthError(null);
    try {
      const params = new URLSearchParams();
      params.append('username', username);
      params.append('password', password);

      const res = await fetch(`${API_BASE_URL}/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params
      });

      if (!res.ok) {
        throw new Error(res.status === 401 || res.status === 403
          ? 'Operator name or password is incorrect. Please try again.'
          : res.status === 429 ? 'Too many sign-in attempts. Please try again shortly.'
          : `Sign-in service is unavailable (${res.status}). Please try again.`);
      }

      const data = await res.json();
      setToken(data.access_token);
      addLog('Authentication successful. Command interface unlocked.',{type:'authentication',source:'api',severity:'info'});
    } catch (err: any) {
      setAuthError(err instanceof TypeError ? 'Unable to reach FLARE. Check your connection and try again.' : err.message || 'Unable to sign in. Please try again.');
    } finally {
      setIsAuthenticating(false);
    }
  };

  // WebSocket Connection
  const connectWebSocket = () => {
    setWsStatus('connecting');
    addLog('Connecting to command loop telemetry stream...',{type:'connection',source:'websocket',severity:'info'});

    const ws = new WebSocket(`${WS_BASE_URL}/ws`);
    wsRef.current = ws;

    ws.onopen = () => {
      if(wsRef.current !== ws || authTokenRef.current !== token){ws.close();return;}
      reconnectAttemptsRef.current = 0;
      const lastMissionCursor = routeTransitionsRef.current.cursor;
      const packetMission=getMissionSnapshot();
      ws.send(JSON.stringify({ type: 'auth', token,
        ...(packetMission?{packet_session_id:packetMission.session_id,packet_cursor:packetMission.packet_received_cursor??packetMission.packet_cursor??0}:{}),
        ...(lastMissionCursor > 0 ? { cursor: lastMissionCursor } : {}),
      }));
      setWsStatus('connected');
      addLog('WebSocket link established. Receiving real-time RF payload.',{type:'connection',source:'websocket',severity:'info'});
    };

    ws.onmessage = (event) => {
      if(wsRef.current !== ws || authTokenRef.current !== token)return;
      try {
        const received: unknown = JSON.parse(event.data);
        if(received && typeof received==='object' && 'type' in received && received.type==='mission_snapshot' && 'data' in received){
          publishMissionSnapshot(received.data as SwarmSnapshot);setSwarmSnapshot(previous=>previous??received.data as SwarmSnapshot); return;
        }
        if(received && typeof received==='object' && 'type' in received && received.type==='mission_packet_events' && 'data' in received){publishPacketEvents(received.data as PacketBatch);return;}
        const transition = parseMissionRouteTransition(received);
        if (transition) {
          const prior = routeTransitionsRef.current;
          // Swarm snapshots own the whole fleet. Advance the legacy journal
          // cursor without rendering the dashboard for every uninspected UAV.
          if(getMissionSnapshot() && transition.drone_id!==activeDroneRef.current){
            routeTransitionsRef.current={...prior,cursor:Math.max(prior.cursor,transition.cursor)};
            return;
          }
          const next = reduceMissionRouteTransition(prior, transition);
          if (next === prior) return;
          routeTransitionsRef.current = next;
          setRouteTransitions(next);
          if (transition.phase === 'requested') {
            setRouteRequestReceiptByDrone(previous => ({
              ...previous,
              [transition.drone_id]: performance.now(),
            }));
          } else {
            const completed = missionPayloadFromCompletion(transition) as TelemetryPayload;
            setMissionPayloads(previous => ({ ...previous, [transition.drone_id]: completed }));
            if (transition.drone_id === activeDroneRef.current) {
              setMissionPayload(completed);
              setMissionReceiptAtMs(performance.now());
              setTelemetryUnavailable(null);
              setTelemetrySafetyState(null);
              const installed = transition.event.sdn.applied
                ? transition.event.decision.installed_path : null;
              setActivePath(installed ?? 'unknown');
              setThreatLevel(completed.decision.threat_level);
              setReward(completed.decision.reward);
              setStep(completed.decision.step);
              setNoSafeRoute(Boolean(completed.decision.no_safe_route));

            }
          }
          return;
        }
        const payload = received as TelemetryPayload;

        if ((payload as any).type === 'telemetry_unavailable') {
          const droneId = String((payload as any).drone_id || 'unknown');
          if (droneId === activeDroneRef.current) {
            setTelemetryUnavailable(String((payload as any).reason || 'Live telemetry unavailable'));
            const control = (payload as any).control_state;
            const status = control?.status;
            setTelemetrySafetyState({
              status: status === 'hold_applied' || status === 'hold_failed' ? status : null,
              applied: typeof control?.sdn?.applied === 'boolean' ? control.sdn.applied : null,
            });
          }
          return;
        }

        // If it's a telemetry broadcast
        if (payload.type === 'telemetry') {
          const dec = payload.decision;
          const met = payload.metrics;

          setMissionPayloads(previous => (
            previous[met.drone_id]?.timestamp === payload.timestamp
              ? previous
              : { ...previous, [met.drone_id]: payload }
          ));

          // Check if payload matches active drone
          if (met.drone_id === activeDroneRef.current) {
            setTelemetryUnavailable(null);
            setTelemetrySafetyState(null);
            setMissionReceiptAtMs(performance.now());
            setMissionPayload(payload);
            const installedPath = payload.event?.decision && 'installed_path' in payload.event.decision
              ? payload.event.decision.installed_path
              : dec.sdn_applied === true ? dec.path_name : null;
            setActivePath(installedPath && ['direct', 'satellite', 'mesh', 'hold'].includes(installedPath)
              ? installedPath : 'unknown');
            setThreatLevel(dec.threat_level);
            setReward(dec.reward);
            setStep(dec.step);
            setNoSafeRoute(Boolean(dec.no_safe_route));

            // Safely parse paths list from backend into direct/satellite/mesh key structure
            const pathsList = (met as any).paths || [];
            const metricsDict: Record<string, MetricDetail> = {};
            pathsList.forEach((p: any) => {
              if (p && p.path_id) {
                metricsDict[p.path_id] = p;
              }
            });

            const canonicalEvent = payload.event;
            if (canonicalEvent?.insider_analysis) {
              setInsiderAnalysis(canonicalEvent.insider_analysis);
              setContainmentAnalysis(canonicalEvent.containment ?? null);

            }
            if (payload.byzantine) {
              setByzantineStatus(payload.byzantine);
            }
            if (payload.fl_metrics) {
              setFlMetrics(payload.fl_metrics);
            }

            // Append to chart history
            const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            setHistory(prev => {
              const newPoint: ChartDataPoint = {
                time: timeStr,
                rssi_direct: metricsDict.direct?.rssi ?? null,
                rssi_satellite: metricsDict.satellite?.rssi ?? null,
                rssi_mesh: metricsDict.mesh?.rssi ?? null,
                sinr_direct: metricsDict.direct?.sinr ?? null,
                sinr_satellite: metricsDict.satellite?.sinr ?? null,
                sinr_mesh: metricsDict.mesh?.sinr ?? null,
                latency_direct: metricsDict.direct?.latency ?? null,
                latency_satellite: metricsDict.satellite?.latency ?? null,
                latency_mesh: metricsDict.mesh?.latency ?? null,
                loss_direct: metricsDict.direct?.packet_loss ?? null,
                loss_satellite: metricsDict.satellite?.packet_loss ?? null,
                loss_mesh: metricsDict.mesh?.packet_loss ?? null,
                reward: dec.reward
              };
              // Limit history to 20 ticks
              const sliced = prev.length >= 20 ? prev.slice(1) : prev;
              return [...sliced, newPoint];
            });
          }
        }
      } catch {
        // Handle message errors silently or as logs
      }
    };

    ws.onclose = () => {
      if(wsRef.current !== ws || authTokenRef.current !== token)return;
      setWsStatus('disconnected');
      if (shouldReconnectRef.current) {
        const attempt = reconnectAttemptsRef.current;
        const delay = Math.min(1000 * Math.pow(1.8, attempt) + Math.random() * 400, 15000);
        reconnectAttemptsRef.current = attempt + 1;
        const delaySec = (delay / 1000).toFixed(1);
        addLog(`WebSocket link closed. Reconnecting in ${delaySec}s (retry #${attempt + 1})...`,{type:'connection',source:'websocket',severity:'warning'});
        reconnectTimerRef.current = window.setTimeout(connectWebSocket, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  };

  const fetchFleet = async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE_URL}/fleet/drones`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!res.ok) throw new Error(`Fleet endpoint returned ${res.status}`);
      const data = await res.json();
      const drones: FleetDrone[] = Array.isArray(data.drones) ? data.drones : [];
      if (!drones.length) throw new Error('Fleet registry returned no active drones');
      setFleetDrones(drones);
      setFlClientManager(data.fl_client_manager || null);
      const manageRes = await fetch(`${API_BASE_URL}/fleet/drones/manage`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (manageRes.ok) {
        const manageData = await manageRes.json();
        setManagedFleetDrones(Array.isArray(manageData.drones) ? manageData.drones : []);
        setCanManageFleet(true);
      } else if (manageRes.status === 403) {
        setManagedFleetDrones([]);
        setCanManageFleet(false);
      }

      setByzantineStatus(prev => drones.map(drone => (
        prev.find(row => row.client_id === drone.drone_id) || {
          client_id: drone.drone_id,
          status: 'NO_UPDATE',
          action: 'PENDING',
          simulation_mode: 'normal'
        }
      )));
      if (drones.length && !drones.some(drone => drone.drone_id === activeDroneRef.current)) {
        selectDrone(drones[0].drone_id);
      }
    } catch (err: any) {
      addLog(`Failed to discover fleet: ${err.message}`);
    }
  };

  const openFleetEditor = (droneId: string) => {
    const drone = managedFleetDrones.find(row => row.drone_id === droneId);
    if (!drone) return;
    setEditingDrone(drone);
    setEditDraft({
      display_name: drone.display_name,
      rssi_offset: drone.rf_profile.rssi_offset,
      pdr_offset: drone.rf_profile.pdr_offset,
      latency_factor: drone.rf_profile.latency_factor
    });
    setEditError(null);
    setConfirmDisable(false);
  };

  const patchFleetDrone = async (droneId: string, changes: Record<string, unknown>) => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE_URL}/fleet/drones/${encodeURIComponent(droneId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify(changes)
      });
    } catch {
      throw new Error(`Cannot reach the API at ${API_BASE_URL}. Check the backend and allowed frontend origin, then retry.`);
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fleet edit failed (${res.status})`);
    await fetchFleet();
    return data;
  };

  const saveFleetEdit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editingDrone || editBusy || !token) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const simulationEditable = systemMode.toLowerCase() === 'simulation' && sdnController.toLowerCase().startsWith('mock');
      await patchFleetDrone(
        editingDrone.drone_id,
        simulationEditable ? editDraft : { display_name: editDraft.display_name }
      );
      addLog(`${editingDrone.drone_id.toUpperCase()} details updated.`,{type:'fleet',source:'api',severity:'info'});
      setEditingDrone(null);
    } catch (err: any) {
      setEditError(err.message || 'Could not save UAV details');
    } finally {
      setEditBusy(false);
    }
  };

  const toggleFleetDrone = async () => {
    if (!editingDrone || editBusy || !token) return;
    if (editingDrone.enabled && !confirmDisable) {
      setConfirmDisable(true);
      return;
    }
    setEditBusy(true);
    setEditError(null);
    try {
      await patchFleetDrone(editingDrone.drone_id, { enabled: !editingDrone.enabled });
      addLog(`${editingDrone.drone_id.toUpperCase()} ${editingDrone.enabled ? 'disabled' : 're-enabled'} in the simulation fleet.`);
      setEditingDrone(null);
      setConfirmDisable(false);
    } catch (err: any) {
      setEditError(err.message || 'Could not update UAV availability');
    } finally {
      setEditBusy(false);
    }
  };

  const enrollDrone = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || isEnrolling) return;
    setIsEnrolling(true);
    setEnrollmentError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/fleet/drones`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(newDrone)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Enrollment failed (${res.status})`);
      await fetchFleet();
      const enrolledId = String(data.drone?.drone_id || newDrone.drone_id);
      setShowEnrollment(false);
      setNewDrone(prev => ({
        drone_id: '',
        display_name: '',
        mac: prev.mac.replace(/([0-9a-fA-F]{2})$/, (_, octet) => (parseInt(octet, 16) + 1).toString(16).padStart(2, '0')),
        access_port: prev.access_port + 1
      }));
      addLog(`${enrolledId.toUpperCase()} enrolled. Telemetry detection and routing discovery are active.`);
      setFleetDetailId(enrolledId);
    } catch (err: any) {
      setEnrollmentError(err.message || 'Enrollment failed');
    } finally {
      setIsEnrolling(false);
    }
  };

  // Trigger Jamming API
  const injectJamming = async (path: CommunicationRoute, profile: AttackProfile = jamProfile as AttackProfile) => {
    if (!token) return;
    const pathless = ['none', 'barrage', 'sweep', 'gps_spoofing'].includes(profile);
    const requestedPaths = pathless ? [] : [path];
    addLog(profile === 'none'
      ? `Clearing communication impairment for ${activeDrone.toUpperCase()}...`
      : `Applying ${profile.replaceAll('_', ' ')} simulation event to ${pathless ? 'the mission network' : path.toUpperCase()}...`);

    try {
      const res = await fetch(`${API_BASE_URL}/jam`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          drone_id: activeDrone,
          paths: requestedPaths,
          duration: jamDuration,
          profile
        })
      });

      if (!res.ok) {
        throw new Error('API Jamming request failed');
      }

      const data = await res.json();
      addLog(profile === 'none'
        ? `Communication impairment cleared for ${activeDrone.toUpperCase()}.`
        : `Simulation event active on: ${JSON.stringify(data.paths)}. Profile: ${profile.toUpperCase()}. Duration: ${data.duration}s.`);

    } catch (err: any) {
      addLog(`Error injecting jam: ${err.message}`);
    }
  };

  // Fetch FL Config
  const fetchFlConfig = async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE_URL}/api/fl/config`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setFlConfig(data);
      }
    } catch (err: any) {
      addLog(`Failed to fetch FL config: ${err.message}`);
    }
  };

  // Fetch FL Metrics
  const fetchFlMetrics = async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE_URL}/api/fl/metrics`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setFlMetrics(data);
      }
    } catch (err: any) {
      addLog(`Failed to fetch FL metrics: ${err.message}`);
    }
  };

  const fetchInsiderEvidence = async (drone: DroneId) => {
    if (!token) return;
    const session = getMissionSnapshot()?.session_id;
    try {
      const response = await fetch(`${API_BASE_URL}/swarm/insider/${encodeURIComponent(drone)}`,{headers:{Authorization:`Bearer ${token}`}});
      if(!response.ok)return;
      const data = await response.json() as InsiderEvidenceSnapshot;
      if(activeDroneRef.current !== drone || !data.available || session !== getMissionSnapshot()?.session_id || authTokenRef.current !== token)return;
      if(data.analysis){setInsiderAnalysis(data.analysis);setContainmentAnalysis(data.containment ?? null);}
    } catch { /* Connection state is reported by the shared transport. */ }
  };

  // Save FL Config
  const saveFlConfig = async (updatedConfig: any) => {
    if (!token) return false;
    setIsSavingConfig(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/fl/config`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(updatedConfig)
      });
      if (res.ok) {
        const data = await res.json();
        setFlConfig(data.config);
        addLog('Secure FL parameters updated and saved to config/fl_config.yaml.',{type:'policy',source:'api',severity:'info'});
        return true;
      } else {
        throw new Error('Save configuration failed');
      }
    } catch (err: any) {
      addLog(`Error saving FL config: ${err.message}`,{type:'policy',source:'api',severity:'warning'});
      return false;
    } finally {
      setIsSavingConfig(false);
    }
  };

  // Non-mutating secure-aggregation verification. This never changes a live
  // client mode, model checkpoint, or persisted trust record.
  const runFlProtectionSelfTest = async () => {
    if (!token) return;
    if (isRunningFlProtectionTest) return;
    const startedAt = performance.now();
    setIsRunningFlProtectionTest(true);
    setFlRunSession(getMissionSnapshot()?.session_id);
    setFlProtectionTest(null);
    setFlProtectionTestError(null);
    setFlProtectionRunTimeMs(null);
    setFlProtectionRunAt(null);
    setFlProtectionPhase('preparing');
    addLog('Running isolated secure-aggregation self-test on copied parameters.');
    try {
      const responsePromise = fetch(`${API_BASE_URL}/api/fl/security/self-test`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      await new Promise(resolve => window.setTimeout(resolve, 400));
      setFlProtectionPhase('analyzing');
      const [res] = await Promise.all([
        responsePromise,
        new Promise(resolve => window.setTimeout(resolve, 650))
      ]);
      if (!res.ok) throw new Error(`Protection self-test returned ${res.status}`);
      const result = await res.json() as FlProtectionSelfTest;
      if(authTokenRef.current !== token)return;
      setFlProtectionPhase('verifying');
      await new Promise(resolve => window.setTimeout(resolve, 400));
      if(authTokenRef.current !== token)return;
      setFlProtectionTest(result);
      setFlProtectionRunTimeMs(Math.round(performance.now() - startedAt));
      setFlProtectionRunAt(new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      }));
      setFlProtectionPhase('complete');
      addLog(
        result.passed
          ? `Model protection verified: synthetic malicious update rejected; deployed model unchanged.`
          : 'Model protection self-test did not meet all safety checks.'
      );
    } catch (err: any) {
      if(authTokenRef.current !== token)return;
      const message = err.message || 'Protection self-test failed';
      setFlProtectionTestError(message);
      setFlProtectionRunTimeMs(Math.round(performance.now() - startedAt));
      setFlProtectionPhase('failed');
      addLog(`Unable to verify model protection: ${message}`);
    } finally {
      if(authTokenRef.current === token)setIsRunningFlProtectionTest(false);
    }
  };

  const resetFlProtectionSelfTest = () => {
    if (isRunningFlProtectionTest) return;
    setFlProtectionTest(null);
    setFlProtectionTestError(null);
    setFlProtectionRunTimeMs(null);
    setFlProtectionRunAt(null);
    setFlProtectionPhase('idle');
  };

  // Switch Active Drone Selector
  const selectDrone = (drone: DroneId) => {
    if(activeDroneRef.current === drone)return;
    activeDroneRef.current = drone;
    setActiveDrone(drone);
    setMissionPayload(null);
    setMissionReceiptAtMs(null);
    setTelemetrySafetyState(null);
    setHistory([]);
    setInsiderAnalysis(null);
    setContainmentAnalysis(null);

    addLog(`Swapped active interface to [${drone.toUpperCase()}]. Recalibrating gauges.`,{type:'inspection',source:'interface',severity:'info'});
  };

  const downloadReport = async () => {
    if (!token || reporting) return;
    setReporting(true); setReportError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/report/generate`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) throw new Error('Report generation failed');

      const reportBlob = await response.blob();
      const reportUrl = URL.createObjectURL(reportBlob);
      const link = document.createElement('a');
      link.href = reportUrl;
      link.download = 'flare-evaluation-report.html';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(reportUrl);
      addLog('Evaluation report generated securely.',{type:'report',source:'api',severity:'info'});
    } catch (err: any) {
      addLog(`Error generating report: ${err.message}`,{type:"report",severity:"warning",source:"api"});
      setReportError(err.message || "Report export failed");
    } finally { setReporting(false); }
  };

  const activeAvailablePaths = sdnReadiness?.available_paths?.[activeDrone] || [];
  const activeDroneRecord = fleetDrones.find(drone => drone.drone_id === activeDrone);
  const activeDroneName = activeDroneRecord?.display_name || activeDrone.replaceAll('_', ' ');
  const canonicalEvent = missionPayload?.event;
  const evidenceProvenance = canonicalEvent?.telemetry?.security_evidence?.provenance;
  const networkSecurity = canonicalEvent?.network_security_analysis;
  const lossEvidenceLabel = networkSecurity?.available_signals?.includes('port_receive_drop')
    ? evidenceProvenance?.category === 'ryu_openflow' ? 'ingress port window' : 'simulated port window'
    : networkSecurity?.available_signals?.includes('drop') ? 'simulated counters' : 'not observed';
  const routingExplanation = missionPayload?.routing_explanation;
  const latestFlRows: ClientSecurityMetric[] = Array.isArray(flMetrics?.latest?.client_security)
    ? flMetrics.latest.client_security : [];
  const flSnapshotMismatch = latestFlRows.some(row => !fleetDrones.some(drone => drone.drone_id === row.client_id)) || Boolean(getMissionSnapshot() && flMetrics?.latest?.mission_session && flMetrics.latest.mission_session !== getMissionSnapshot()?.session_id) || Boolean(getMissionSnapshot() && flMetrics?.session_id && flMetrics.session_id !== getMissionSnapshot()?.session_id);
  const flSnapshotTimestamp = typeof flMetrics?.latest?.timestamp === 'string' ? flMetrics.latest.timestamp : null;
  const flSnapshotHasZone = Boolean(flSnapshotTimestamp && /(?:Z|[+-]\d\d:\d\d)$/.test(flSnapshotTimestamp));
  const flSnapshotAge = flSnapshotHasZone && flSnapshotTimestamp
    ? Math.max(0, Math.round((Date.now() - Date.parse(flSnapshotTimestamp)) / 1000)) : null;
  const participatingClientIds = new Set([...Object.keys(flClientManager?.clients ?? {}), ...latestFlRows.map(row => row.client_id)]);
  const displayedByzantineStatus: ClientSecurityMetric[] = (flSnapshotMismatch
    ? fleetDrones.map(drone => ({ client_id: drone.drone_id, status: 'NO_UPDATE' as const, action: 'PENDING' as const }))
    : latestFlRows.length ? latestFlRows : byzantineStatus).filter(row => !getMissionSnapshot() || participatingClientIds.has(row.client_id));
  const activeFlSecurity = displayedByzantineStatus.find(client => client.client_id === activeDrone);
  // Authentication remains owned here; presentation is isolated from the mounted workspace.
  if (!token) {
    return <SignIn username={username} password={password} error={authError} pending={isAuthenticating} onUsernameChange={setUsername} onPasswordChange={setPassword} onSubmit={handleLogin} />;
  }

  const enrollmentForm = (
    <form onSubmit={enrollDrone} className="border-b border-blue-500/10 bg-blue-500/[0.035] p-4">
      <div className="mb-3 flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-blue-500/20 bg-blue-500/10 text-blue-300"><UserPlus className="h-4 w-4" /></div>
        <div>
          <p className="text-xs font-semibold text-gray-200">Authorize a new drone identity</p>
          <p className="mt-0.5 text-[10px] leading-4 text-gray-500">The shared anomaly model applies immediately because it evaluates telemetry features, not a fixed drone number. A physical Flower client participates after it connects with this ID.</p>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">Drone ID
          <input required pattern="[a-z][a-z0-9_-]{2,63}" placeholder="drone_4" value={newDrone.drone_id} onChange={e => setNewDrone(prev => ({ ...prev, drone_id: e.target.value.toLowerCase() }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-xs normal-case text-gray-200 outline-none focus:border-blue-400/50" />
        </label>
        <label className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">Display name
          <input required placeholder="Survey Drone 4" value={newDrone.display_name} onChange={e => setNewDrone(prev => ({ ...prev, display_name: e.target.value }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-xs normal-case text-gray-200 outline-none focus:border-blue-400/50" />
        </label>
        <label className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">Device MAC
          <input required pattern="(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}" value={newDrone.mac} onChange={e => setNewDrone(prev => ({ ...prev, mac: e.target.value }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 font-mono text-xs normal-case text-gray-200 outline-none focus:border-blue-400/50" />
        </label>
        <label className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">SDN access port
          <input required type="number" min="1" max="65535" value={newDrone.access_port} onChange={e => setNewDrone(prev => ({ ...prev, access_port: Number(e.target.value) }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 font-mono text-xs normal-case text-gray-200 outline-none focus:border-blue-400/50" />
        </label>
      </div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p role="alert" className={`text-[10px] ${enrollmentError ? 'text-rose-300' : 'text-gray-600'}`}>{enrollmentError || 'Duplicate drone IDs, MAC addresses, and switch ports are rejected.'}</p>
        <button type="submit" disabled={isEnrolling} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 text-[10px] font-bold text-slate-950 transition-colors hover:bg-blue-400 disabled:cursor-wait disabled:opacity-60">
          {isEnrolling ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          {isEnrolling ? 'Authorizing…' : 'Authorize and add'}
        </button>
      </div>
    </form>
  );

  return (
    <div className="app-shell flare-application">
      <ApplicationHeader page={page} navigate={navigate} online={wsStatus} reporting={reporting} onReport={downloadReport} onActivity={() => setActivityOpen(true)} onLock={() => setToken(null)} />
      <main className="application-content" id="application-content">
        <section className="application-page" aria-label="Overview" hidden={page !== 'overview'} tabIndex={-1}>
          <Overview mode={systemMode} connection={wsStatus} controller={sdnController} readiness={sdnReadiness} controllerEvent={readinessHistory[0]} activeDrone={activeDrone} activeName={activeDroneName} payload={missionPayload} receivedAtMs={missionReceiptAtMs} unavailable={telemetryUnavailable} charts={<React.Suspense fallback={<p role="status">Loading charts…</p>}><TelemetryCharts history={history} /></React.Suspense>}>
<div className="overview-provenance">        {telemetryUnavailable ? (
          <div role="status" className="flex items-start gap-3 bg-amber-500/[0.06] p-5 text-amber-200">
            <WifiOff className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="text-xs font-semibold">{getMissionSnapshot() ? 'Current mission model evidence unavailable' : <>Live telemetry unavailable — {telemetrySafetyState?.status === 'hold_applied' && telemetrySafetyState.applied === true
                ? 'controller reported HOLD applied'
                : telemetrySafetyState?.status === 'hold_failed' ? 'HOLD update failed' : 'HOLD state unconfirmed'}</>}</p>
              <p className="mt-1 text-[11px] text-amber-200/70">{getMissionSnapshot() ? `${getMissionSnapshot()?.playing ? 'Waiting for current-session inference.' : 'Play the mission to collect packet observations and run inference.'} The swarm panel shows current simulation state and installed rules.` : `${telemetryUnavailable}. Sensor failure is an operational safety condition, not a malicious classification.`}</p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 divide-y divide-white/5 lg:grid-cols-3 lg:divide-x lg:divide-y-0">
            <div className="p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">Telemetry</p>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Source</dt><dd className="font-mono text-gray-200">{canonicalEvent?.telemetry?.source || 'unavailable'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Snapshot age</dt><dd className="font-mono text-gray-200">{canonicalEvent?.telemetry?.source_age_s != null ? `${canonicalEvent.telemetry.source_age_s.toFixed(2)} s` : 'unreported'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Injected scenario</dt><dd className="font-mono text-amber-300">{canonicalEvent?.telemetry?.ew_status?.active_attack || 'none'}</dd></div>
              </dl>
            </div>

            <div className="p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">Network detector</p>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Conclusion</dt><dd className={`font-semibold ${networkSecurity?.status === 'MALICIOUS' ? 'text-rose-300' : networkSecurity?.status === 'SUSPICIOUS' ? 'text-amber-300' : networkSecurity?.status === 'NORMAL' ? 'text-emerald-300' : 'text-gray-400'}`}>{networkSecurity?.status || 'UNAVAILABLE'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Evidence</dt><dd className="font-mono text-gray-200">{evidenceProvenance?.category || networkSecurity?.evidence_source || 'unavailable'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Observer</dt><dd className="max-w-[65%] truncate font-mono text-gray-200" title={evidenceProvenance?.observer_id}>{evidenceProvenance?.observer_id || 'unavailable'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Independent</dt><dd className={evidenceProvenance?.independent ? 'text-emerald-300' : 'text-amber-300'}>{evidenceProvenance?.independent ? 'yes' : 'no'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">DoS / spoof risk</dt><dd className="font-mono text-gray-200">{networkSecurity ? `${networkSecurity.dos_score.toFixed(2)} / ${networkSecurity.spoofing_score.toFixed(2)}` : '—'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Loss evidence</dt><dd className="text-right font-mono text-gray-200">{lossEvidenceLabel}</dd></div>
                {canonicalEvent?.telemetry?.security_evidence?.controller_policy_dropped_packets != null && <div className="flex justify-between gap-3"><dt className="text-gray-500">Policy drops</dt><dd className="text-right font-mono text-gray-200">{canonicalEvent.telemetry.security_evidence.controller_policy_dropped_packets.toLocaleString()} excluded</dd></div>}
              </dl>
            </div>

            <div className="p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">Active models</p>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Generation</dt><dd className="font-mono text-gray-200">{canonicalEvent?.decision?.model_generation ?? canonicalEvent?.inference?.model_generation ?? 'legacy'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Routing contract</dt><dd className="font-mono text-gray-200">{routingExplanation?.explanation?.routing_contract || 'unavailable'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Fallback state</dt><dd className="max-w-[65%] text-right text-gray-200">{canonicalEvent?.fallback_reasons?.length ? canonicalEvent.fallback_reasons.join(', ') : 'none recorded'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Validation tier</dt><dd className="text-gray-200">{systemMode.toLowerCase() === 'simulation' ? 'controlled simulation' : sdnController.includes('Ryu') ? 'real network source; field validation unproven' : 'unverified'}</dd></div>
              </dl>
            </div>
          </div>
        )}

<ContributionChart explanation={routingExplanation} /></div>
          </Overview>
        </section>
        <section className="application-page mission-application-page" aria-label="Mission" hidden={page !== 'mission'} tabIndex={-1}>
      {swarmSnapshot && token ? <SwarmCommandCenter snapshot={swarmSnapshot} api={API_BASE_URL} token={token} evidence={{payload:missionPayload,receivedAtMs:missionReceiptAtMs,transportStatus:wsStatus,unavailable:telemetryUnavailable,
        request:routeTransitions.pendingByDrone[activeDrone]??null,completion:routeTransitions.completedByDrone[activeDrone]??null,
        requestReceivedAtMs:routeRequestReceiptByDrone[activeDrone]??null,controller:sdnController}} onSnapshot={s=>{publishMissionSnapshot(s);setSwarmSnapshot(previous=>previous??s);}} onInspect={id=>{selectDrone(id);if(wsRef.current?.readyState===WebSocket.OPEN)wsRef.current.send(JSON.stringify({type:"inspect",drone_ids:[id]}));}}/> : <LiveMissionSimulation
        payload={missionPayload}
        swarmPayloads={missionPayloads}
        activeDrone={activeDrone}
        fleetDrones={fleetDrones}
        activePath={activePath}
        threatLevel={threatLevel}
        reward={reward}
        step={step}
        noSafeRoute={noSafeRoute}
        availablePaths={activeAvailablePaths}
        sdnReady={sdnReadiness?.ready}
        sdnController={sdnController}
        runtimeMode={systemMode}
        transportStatus={wsStatus}
        telemetryUnavailable={telemetryUnavailable}
        telemetrySafetyState={telemetrySafetyState}
        telemetryReceivedAtMs={missionReceiptAtMs}
        routeRequest={routeTransitions.pendingByDrone[activeDrone] ?? null}
        routeCompletion={routeTransitions.completedByDrone[activeDrone] ?? null}
        routeTransitionHistory={routeTransitions.history}
        routeRequestReceivedAtMs={routeRequestReceiptByDrone[activeDrone] ?? null}
        trust={containmentAnalysis?.trust_score ?? activeFlSecurity?.trust_score}
        insiderRisk={containmentAnalysis?.insider_risk ?? insiderAnalysis?.risk_score ?? undefined}
        containmentMode={containmentAnalysis?.mode}
        attackSimulationEnabled={attackSimulationEnabled}
        jamProfile={jamProfile}
        jamDuration={jamDuration}
        onJamProfileChange={setJamProfile}
        onJamDurationChange={setJamDuration}
        onSelectDrone={selectDrone}
        onApplyAttack={injectJamming}
      />}

        </section>
        <section className="application-page" aria-label="Fleet" hidden={page !== 'fleet'} tabIndex={-1}>
          <Fleet active={page === 'fleet'} drones={fleetDrones} managed={managedFleetDrones} payloads={missionPayloads} canManage={canManageFleet} detailId={fleetDetailId} onDetail={id => { setFleetDetailId(id); if(id)openFleetEditor(id); else { setEditingDrone(null); setConfirmDisable(false); } }} onInspect={inspectInMission} enrolling={showEnrollment} onEnrollment={setShowEnrollment} enrollment={enrollmentForm} onEdit={openFleetEditor} editor={
      editingDrone && canManageFleet && <section className="fleet-editor" aria-labelledby="fleet-edit-heading">
        <h3 id="fleet-edit-heading">Configuration</h3>
        <form onSubmit={saveFleetEdit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-gray-400">Display name<input required maxLength={80} value={editDraft.display_name} onChange={e => setEditDraft(prev => ({ ...prev, display_name: e.target.value }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-gray-200 focus:border-blue-400" /></label>
          {systemMode.toLowerCase() === 'simulation' && sdnController.toLowerCase().startsWith('mock') && <>
            <label className="text-xs text-gray-400">RSSI offset (dB)<input required type="number" min={-60} max={60} step="any" value={editDraft.rssi_offset} onChange={e => setEditDraft(prev => ({ ...prev, rssi_offset: Number(e.target.value) }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-gray-200 focus:border-blue-400" /></label>
            <label className="text-xs text-gray-400">PDR offset<input required type="number" min={-1} max={1} step="any" value={editDraft.pdr_offset} onChange={e => setEditDraft(prev => ({ ...prev, pdr_offset: Number(e.target.value) }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-gray-200 focus:border-blue-400" /></label>
            <label className="text-xs text-gray-400">Latency factor<input required type="number" min={0.1} max={10} step="any" value={editDraft.latency_factor} onChange={e => setEditDraft(prev => ({ ...prev, latency_factor: Number(e.target.value) }))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-gray-200 focus:border-blue-400" /></label>
          </>}
          {editError && <p role="alert" className="sm:col-span-2 lg:col-span-4 text-xs text-rose-300">{editError}</p>}
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2 lg:col-span-4">
            <button type="submit" disabled={editBusy} className="min-h-10 rounded-lg bg-blue-500 px-4 text-xs font-semibold text-slate-950 disabled:opacity-50">{editBusy ? 'Saving…' : 'Save details'}</button>
            {systemMode.toLowerCase() === 'simulation' && sdnController.toLowerCase().startsWith('mock') && <button type="button" disabled={editBusy} onClick={toggleFleetDrone} className={`min-h-10 rounded-lg border px-4 text-xs font-semibold disabled:opacity-50 ${editingDrone.enabled ? 'border-amber-500/35 text-amber-300' : 'border-emerald-500/35 text-emerald-300'}`}>{editingDrone.enabled ? confirmDisable ? 'Confirm disable' : 'Disable UAV' : 'Re-enable UAV'}</button>}
            {confirmDisable && <button type="button" onClick={() => setConfirmDisable(false)} className="min-h-10 px-3 text-xs text-gray-400">Cancel disable</button>}
          </div>
        </form>
        <p className="mt-3 text-xs text-gray-500">Drone ID, MAC, and SDN port are fixed during live operation. RF offsets affect synthetic simulation only.</p>
        {confirmDisable && <p className="mt-3 text-xs text-amber-300">This removes the UAV from active simulation discovery and stops its Flower client after reconciliation. History and identity remain. Mock-SDN state is not real packet enforcement.</p>}
      </section>
          } />
        </section>
        <section className="application-page" aria-label="Security" hidden={page !== 'security'} tabIndex={-1}>
          <div className="application-section security-page"><div className="page-heading"><div><h1>Security</h1><p>Federated learning policy and update evidence</p></div></div>
            <SectionTabs label="security" value={securityTab} onChange={setSecurityTab} items={[{id:'policy',label:'Policy'},{id:'monitoring',label:'Monitoring'}]} />
            <div role="tabpanel" id="security-policy-panel" aria-labelledby="security-policy-tab" hidden={securityTab !== 'policy'}>{flConfig ? <SecureFlControlCenter config={flConfig} isSaving={isSavingConfig} onConfigChange={setFlConfig} onSave={saveFlConfig} /> : <p className="empty-state">Policy unavailable. Check the API connection.</p>}</div>
            <div role="tabpanel" id="security-monitoring-panel" aria-labelledby="security-monitoring-tab" hidden={securityTab !== 'monitoring'}><SecurityMonitoring active={page === 'security' && securityTab === 'monitoring'} rows={displayedByzantineStatus} metrics={flMetrics} warning={flSnapshotMismatch ? 'Snapshot belongs to a different fleet. Its security verdicts are withheld.' : undefined} age={flSnapshotAge} clientManager={flClientManager?.status ?? 'unavailable'} onInspect={inspectInMission} inspectable={fleetDrones.map(d => d.drone_id)} /></div>
          </div>
        </section>
        <section className="application-page" aria-label="Validation" hidden={page !== 'validation'} tabIndex={-1}>
          <Validation traffic={trafficValidation} drones={fleetDrones} simulation={attackSimulationEnabled} fl={{result:flProtectionTest,phase:flProtectionPhase,busy:isRunningFlProtectionTest,error:flProtectionTestError,duration:flProtectionRunTimeMs,at:flProtectionRunAt,retained:Boolean(flProtectionTest && flRunSession !== getMissionSnapshot()?.session_id),run:runFlProtectionSelfTest,reset:resetFlProtectionSelfTest}} />
        </section>
      </main>
      <ActivityDrawer open={activityOpen} onClose={() => setActivityOpen(false)} entries={statusLog} />
      {reportError && <div className="application-toast" role="alert">{reportError}<button aria-label="Dismiss report error" onClick={() => setReportError(null)}>Dismiss</button></div>}
    </div>
  );
}

export default App;
