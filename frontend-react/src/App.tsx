import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  AlertTriangle,
  Database,
  Radio,
  Server,
  Shield,
  Wifi,
  WifiOff,
  Lock,
  Compass,
  BrainCircuit,
  ShieldCheck,
  FileText,
  Sliders,
  RefreshCw,
  Cpu,
  Layers,
  Play,
  RotateCcw,
  ArrowRight,
  Check,
  ChevronDown,
  UserPlus,
  X
} from 'lucide-react';
import './App.css';
import CommandOverview from './command-overview/CommandOverview';
import LiveMissionSimulation from './mission-simulation/LiveMissionSimulation';
import SecureFlControlCenter from './fl-control-center/SecureFlControlCenter';
import type {
  AttackProfile,
  CommunicationRoute,
  MissionTelemetryPayload,
} from './mission-simulation/types';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws');
const TelemetryCharts = React.lazy(() => import('./TelemetryCharts'));

const formatRiskPercent = (value: number | null | undefined): string =>
  value == null ? 'Unavailable' : `${(value * 100).toFixed(0)}%`;

type DroneId = string;
type InsiderProfile =
  | 'normal'
  | 'selective_forwarding'
  | 'telemetry_falsification'
  | 'control_flood'
  | 'replay';

const INSIDER_SCENARIOS: Array<{
  profile: InsiderProfile;
  label: string;
  summary: string;
  evidence: string;
}> = [
  {
    profile: 'normal',
    label: 'Normal baseline',
    summary: 'Honest forwarding and control traffic.',
    evidence: 'High forwarding ratio, aligned claims, and low replay/control scores.'
  },
  {
    profile: 'selective_forwarding',
    label: 'Selective forwarding',
    summary: 'Silently drops transit packets.',
    evidence: 'Controller-observed forwarding falls below received traffic.'
  },
  {
    profile: 'telemetry_falsification',
    label: 'Telemetry falsification',
    summary: 'Reports forwarding that did not occur.',
    evidence: 'Drone claims diverge from controller-observed packet counts.'
  },
  {
    profile: 'control_flood',
    label: 'Control flood',
    summary: 'Overloads the control plane with messages.',
    evidence: 'Control-rate and duplicate-sequence evidence rise sharply.'
  },
  {
    profile: 'replay',
    label: 'Replay attack',
    summary: 'Reuses stale or duplicate sequences.',
    evidence: 'Duplicate-sequence ratio and inconsistent packet claims increase.'
  }
];

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

interface SdnReadinessState {
  ready: boolean;
  status: string;
  connected_switches: number;
  expected_switches: number;
  available_paths: Record<string, string[]>;
  error?: string | null;
  alert?: ReadinessTransition;
}

interface ClientSecurityMetric {
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

interface FleetDrone {
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
}

interface FlProtectionSelfTest {
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

type FlProtectionPhase = 'idle' | 'preparing' | 'analyzing' | 'verifying' | 'complete' | 'failed';

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
}

interface RoutingExplanation {
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

interface InsiderDecision {
  installedPath: string;
  networkAction: string;
  constraintReason: string | null;
  sdnApplied: boolean;
}

interface TrafficDefenseBaseline {
  droneId: DroneId;
  capturedAt: number;
  analysis: InsiderAnalysis;
  containment: ContainmentAnalysis | null;
  decision: InsiderDecision | null;
}

type InsiderEvidenceState = 'loading' | 'waiting' | 'live' | 'stale' | 'error';
type TrafficValidationPhase = 'idle' | 'capturing_baseline' | 'running_attack' | 'complete' | 'failed';

interface InsiderEvidenceSnapshot {
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
interface TelemetryPayload extends MissionTelemetryPayload {
  event?: CanonicalEventPayload & NonNullable<MissionTelemetryPayload['event']>;
  telemetry_age_s?: number;
  explanation?: ModelExplanation | null;
  routing_explanation?: RoutingExplanation | null;
  byzantine?: ClientSecurityMetric[];
  fl_metrics?: any;
}

export interface ChartDataPoint {
  time: string;
  rssi_direct: number;
  rssi_satellite: number;
  rssi_mesh: number;
  sinr_direct: number;
  sinr_satellite: number;
  sinr_mesh: number;
  latency_direct: number;
  latency_satellite: number;
  latency_mesh: number;
  loss_direct: number;
  loss_satellite: number;
  loss_mesh: number;
  reward: number;
}

function App() {
  const showLegacyFlControls = import.meta.env.VITE_SHOW_LEGACY_FL_CONTROLS === 'true';
  const showLegacyCommandOverview = import.meta.env.VITE_SHOW_LEGACY_COMMAND_OVERVIEW === 'true';
  // Auth state
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);

  // Connection & Active states
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const [activeDrone, setActiveDrone] = useState<DroneId>('drone_1');
  const [activePath, setActivePath] = useState<string>('direct');
  const [threatLevel, setThreatLevel] = useState<string>('LOW');
  const [reward, setReward] = useState<number>(0.0);
  const [step, setStep] = useState<number>(0);
  const [noSafeRoute, setNoSafeRoute] = useState<boolean>(false);
  const [telemetryUnavailable, setTelemetryUnavailable] = useState<string | null>(null);
  const [systemMode, setSystemMode] = useState<string>('UNKNOWN');
  const [sdnController, setSdnController] = useState<string>('UNKNOWN');
  const [sdnReadiness, setSdnReadiness] = useState<SdnReadinessState | null>(null);
  const [readinessHistory, setReadinessHistory] = useState<ReadinessTransition[]>([]);
  const [fleetDrones, setFleetDrones] = useState<FleetDrone[]>([
    { drone_id: 'drone_1', display_name: 'Drone 1', index: 1, mac: '00:00:00:00:00:02', access_port: 4, enabled: true, rf_profile: { rssi_offset: 0, pdr_offset: 0, latency_factor: 1 } },
    { drone_id: 'drone_2', display_name: 'Drone 2', index: 2, mac: '00:00:00:00:00:03', access_port: 5, enabled: true, rf_profile: { rssi_offset: 0, pdr_offset: 0, latency_factor: 1 } },
    { drone_id: 'drone_3', display_name: 'Drone 3', index: 3, mac: '00:00:00:00:00:04', access_port: 6, enabled: true, rf_profile: { rssi_offset: 0, pdr_offset: 0, latency_factor: 1 } }
  ]);
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
  const [telemetry, setTelemetry] = useState<Record<string, MetricDetail> | null>(null);
  const [missionPayload, setMissionPayload] = useState<TelemetryPayload | null>(null);
  const [missionPayloads, setMissionPayloads] = useState<Record<DroneId, TelemetryPayload>>({});
  const [history, setHistory] = useState<ChartDataPoint[]>([]);

  // Control action states
  const [jamDuration, setJamDuration] = useState<number>(15);
  const [statusLog, setStatusLog] = useState<string[]>([]);
  
  // Capstone Phase 4/5 States
  const [insiderAnalysis, setInsiderAnalysis] = useState<InsiderAnalysis | null>(null);
  const [containmentAnalysis, setContainmentAnalysis] = useState<ContainmentAnalysis | null>(null);
  const [insiderDecision, setInsiderDecision] = useState<InsiderDecision | null>(null);
  const [insiderProfiles, setInsiderProfiles] = useState<Record<DroneId, InsiderProfile>>({
    drone_1: 'normal',
    drone_2: 'normal',
    drone_3: 'normal'
  });
  const [selectedInsiderProfile, setSelectedInsiderProfile] = useState<InsiderProfile>('control_flood');
  const [scenarioMenuOpen, setScenarioMenuOpen] = useState(false);
  const [insiderDemoTab, setInsiderDemoTab] = useState<'traffic' | 'model'>('traffic');
  const [insiderCommand, setInsiderCommand] = useState<InsiderProfile | null>(null);
  const [insiderFeedback, setInsiderFeedback] = useState<string | null>(null);
  const [insiderEvidenceState, setInsiderEvidenceState] = useState<InsiderEvidenceState>('loading');
  const [insiderEvidenceMessage, setInsiderEvidenceMessage] = useState('Checking the live control loop…');
  const [insiderEventAge, setInsiderEventAge] = useState<number | null>(null);
  const [observedInsiderProfile, setObservedInsiderProfile] = useState<InsiderProfile | null>(null);
  const [trafficDefenseBaseline, setTrafficDefenseBaseline] = useState<TrafficDefenseBaseline | null>(null);
  const [trafficValidationPhase, setTrafficValidationPhase] = useState<TrafficValidationPhase>('idle');
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
  const shouldReconnectRef = useRef(false);
  const activeDroneRef = useRef(activeDrone);
  const scenarioMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!scenarioMenuOpen) return;
    const closeScenarioMenu = (event: PointerEvent) => {
      if (!scenarioMenuRef.current?.contains(event.target as Node)) {
        setScenarioMenuOpen(false);
      }
    };
    document.addEventListener('pointerdown', closeScenarioMenu);
    return () => document.removeEventListener('pointerdown', closeScenarioMenu);
  }, [scenarioMenuOpen]);

  // Set up WebSocket connection when authenticated
  useEffect(() => {
    if (!token) return;

    shouldReconnectRef.current = true;
    connectWebSocket();
    fetchSystemHealth();
    fetchFlConfig();
    fetchFlMetrics();
    fetchFleet();
    fetchInsiderProfiles();
    const healthTimer = window.setInterval(fetchSystemHealth, 5000);

    return () => {
      window.clearInterval(healthTimer);
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
    setInsiderEvidenceState('loading');
    setInsiderEvidenceMessage('Checking the live control loop…');
    fetchInsiderEvidence(activeDrone);
    const evidenceTimer = window.setInterval(
      () => fetchInsiderEvidence(activeDrone),
      2000
    );
    return () => window.clearInterval(evidenceTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeDrone]);

  // Log helper
  const addLog = (msg: string) => {
    const timeStr = new Date().toLocaleTimeString();
    setStatusLog(prev => [`[${timeStr}] ${msg}`, ...prev.slice(0, 49)]);
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
        throw new Error('Invalid credentials');
      }

      const data = await res.json();
      setToken(data.access_token);
      addLog('Authentication successful. Command interface unlocked.');
    } catch (err: any) {
      setAuthError(err.message || 'Connection to authentication server failed.');
    } finally {
      setIsAuthenticating(false);
    }
  };

  // WebSocket Connection
  const connectWebSocket = () => {
    setWsStatus('connecting');
    addLog('Connecting to command loop telemetry stream...');

    const ws = new WebSocket(`${WS_BASE_URL}/ws`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token }));
      setWsStatus('connected');
      addLog('WebSocket link established. Receiving real-time RF payload.');
    };

    ws.onmessage = (event) => {
      try {
        const payload: TelemetryPayload = JSON.parse(event.data);

        if ((payload as any).type === 'telemetry_unavailable') {
          const droneId = String((payload as any).drone_id || 'unknown');
          if (droneId === activeDroneRef.current) {
            setTelemetryUnavailable(String((payload as any).reason || 'Live telemetry unavailable'));
            setActivePath('hold');
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
            setMissionPayload(payload);
            setActivePath(dec.path_name);
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

            setTelemetry(metricsDict as any);

            const canonicalEvent = payload.event;
            if (canonicalEvent?.insider_analysis) {
              setInsiderAnalysis(canonicalEvent.insider_analysis);
              setContainmentAnalysis(canonicalEvent.containment ?? null);
              setObservedInsiderProfile(
                canonicalEvent.telemetry?.security_evidence?.simulation_profile ?? null
              );
              setInsiderEventAge(
                canonicalEvent.timestamp
                  ? Math.max(0, Date.now() / 1000 - canonicalEvent.timestamp)
                  : 0
              );
              setInsiderEvidenceState('live');
              setInsiderEvidenceMessage('Receiving measured evidence from the control loop.');
            }
            if (canonicalEvent?.decision && canonicalEvent.insider_analysis) {
              setInsiderDecision({
                installedPath: canonicalEvent.decision.installed_path
                  || canonicalEvent.decision.requested_path
                  || dec.path_name,
                networkAction: canonicalEvent.decision.network_action || 'forward',
                constraintReason: canonicalEvent.decision.constraint_reason ?? null,
                sdnApplied: Boolean(canonicalEvent.sdn?.applied)
              });
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
                rssi_direct: metricsDict.direct?.rssi ?? -120,
                rssi_satellite: metricsDict.satellite?.rssi ?? -120,
                rssi_mesh: metricsDict.mesh?.rssi ?? -120,
                sinr_direct: metricsDict.direct?.sinr ?? 0,
                sinr_satellite: metricsDict.satellite?.sinr ?? 0,
                sinr_mesh: metricsDict.mesh?.sinr ?? 0,
                latency_direct: metricsDict.direct?.latency ?? 0,
                latency_satellite: metricsDict.satellite?.latency ?? 0,
                latency_mesh: metricsDict.mesh?.latency ?? 0,
                loss_direct: metricsDict.direct?.packet_loss ?? 0,
                loss_satellite: metricsDict.satellite?.packet_loss ?? 0,
                loss_mesh: metricsDict.mesh?.packet_loss ?? 0,
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
      setWsStatus('disconnected');
      if (shouldReconnectRef.current) {
        addLog('WebSocket link closed. Attempting reconnect in 3s...');
        reconnectTimerRef.current = window.setTimeout(connectWebSocket, 3000);
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
      setInsiderProfiles(prev => {
        const next = { ...prev };
        drones.forEach(drone => { next[drone.drone_id] ??= 'normal'; });
        return next;
      });
      setByzantineStatus(prev => drones.map(drone => (
        prev.find(row => row.client_id === drone.drone_id) || {
          client_id: drone.drone_id,
          status: 'NO_UPDATE',
          action: 'PENDING',
          simulation_mode: 'normal'
        }
      )));
      if (!drones.some(drone => drone.drone_id === activeDroneRef.current)) {
        selectDrone(drones[0].drone_id);
      }
    } catch (err: any) {
      addLog(`Failed to discover fleet: ${err.message}`);
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
      selectDrone(enrolledId);
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

  const fetchInsiderProfiles = async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE_URL}/swarm/insider`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!res.ok) throw new Error('Insider simulation state is unavailable');
      const data = await res.json();
      if (data?.profiles) {
        setInsiderProfiles(prev => ({ ...prev, ...data.profiles }));
      }
    } catch (err: any) {
      addLog(`Failed to read insider simulation state: ${err.message}`);
    }
  };

  const fetchInsiderEvidence = async (drone: DroneId) => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE_URL}/swarm/insider/${drone}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!res.ok) throw new Error(`Evidence endpoint returned ${res.status}`);
      const data = await res.json() as InsiderEvidenceSnapshot;
      setInsiderProfiles(prev => ({ ...prev, [drone]: data.current_profile }));
      if (!data.available) {
        setInsiderEvidenceState('waiting');
        setInsiderEvidenceMessage('The orchestrator has not produced a decision yet. Keep the simulation services running.');
        return;
      }

      const age = Number(data.event_age_s ?? 0);
      setInsiderEventAge(age);
      setObservedInsiderProfile(data.observed_profile ?? null);
      if (data.analysis) {
        setInsiderAnalysis(data.analysis);
        setContainmentAnalysis(data.containment ?? null);
        setInsiderDecision({
          installedPath: data.decision?.installed_path
            || data.decision?.requested_path
            || 'unknown',
          networkAction: data.decision?.network_action || 'unknown',
          constraintReason: data.decision?.constraint_reason ?? null,
          sdnApplied: Boolean(data.sdn_applied)
        });
      }

      if (!data.profile_synchronized) {
        setInsiderEvidenceState('waiting');
        setInsiderEvidenceMessage(`Scenario accepted. Waiting for ${data.current_profile.replaceAll('_', ' ')} evidence to reach the control loop.`);
      } else if (age > 5) {
        setInsiderEvidenceState('stale');
        setInsiderEvidenceMessage(`The last decision is ${Math.round(age)} seconds old. Restart or check the orchestrator.`);
      } else if (data.analysis) {
        setInsiderEvidenceState('live');
        setInsiderEvidenceMessage('Receiving measured evidence from the control loop.');
      } else {
        setInsiderEvidenceState('waiting');
        setInsiderEvidenceMessage('Telemetry arrived without insider analysis. Waiting for a complete control cycle.');
      }
    } catch (err: any) {
      setInsiderEvidenceState('error');
      setInsiderEvidenceMessage(`Live evidence is unavailable: ${err.message}. Check that the API and orchestrator are running.`);
    }
  };

  const applyInsiderProfile = async (profile: InsiderProfile) => {
    if (!token || insiderCommand) return;
    if (!attackSimulationEnabled) {
      setInsiderFeedback('Attack controls are locked outside simulation mode.');
      return;
    }
    setInsiderCommand(profile);
    setInsiderFeedback(null);
    addLog(`Applying ${profile.replaceAll('_', ' ')} behavior to ${activeDrone.toUpperCase()}...`);
    try {
      const res = await fetch(`${API_BASE_URL}/swarm/insider/${activeDrone}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ profile })
      });
      if (!res.ok) throw new Error('Insider simulation command failed');
      if (profile !== 'normal' && activeInsiderProfile === 'normal' && insiderAnalysis) {
        setTrafficDefenseBaseline({
          droneId: activeDrone,
          capturedAt: Date.now(),
          analysis: insiderAnalysis,
          containment: containmentAnalysis,
          decision: insiderDecision
        });
      }
      setInsiderProfiles(prev => ({ ...prev, [activeDrone]: profile }));
      setInsiderAnalysis(null);
      setContainmentAnalysis(null);
      setInsiderDecision(null);
      setObservedInsiderProfile(null);
      setInsiderEvidenceState('waiting');
      setInsiderEvidenceMessage(`Scenario accepted. Waiting for ${profile.replaceAll('_', ' ')} evidence to reach the control loop.`);
      const message = profile === 'normal'
        ? `${activeDroneName} returned to normal. Risk history will settle over the next few cycles.`
        : `${activeDroneName} is now running the ${profile.replaceAll('_', ' ')} scenario.`;
      if (profile === 'normal') {
        setTrafficValidationPhase('idle');
        setTrafficDefenseBaseline(null);
      }
      setInsiderFeedback(message);
      addLog(message);
      window.setTimeout(() => fetchInsiderEvidence(activeDrone), 500);
    } catch (err: any) {
      const message = `Unable to update insider scenario: ${err.message}`;
      setInsiderFeedback(message);
      addLog(message);
    } finally {
      setInsiderCommand(null);
    }
  };

  const runTrafficDefenseValidation = async () => {
    if (!token || insiderCommand || !attackSimulationEnabled) return;

    const scenario = selectedInsiderProfile;
    const postProfile = async (profile: InsiderProfile) => {
      const res = await fetch(`${API_BASE_URL}/swarm/insider/${activeDrone}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ profile })
      });
      if (!res.ok) throw new Error(`Unable to apply ${profile.replaceAll('_', ' ')}`);
      setInsiderProfiles(previous => ({ ...previous, [activeDrone]: profile }));
    };
    const waitForEvidence = async (
      profile: InsiderProfile,
      { requireNormalStatus = false, requireEnforcement = false } = {}
    ) => {
      let latest: InsiderEvidenceSnapshot | null = null;
      for (let attempt = 0; attempt < 30; attempt += 1) {
        const res = await fetch(`${API_BASE_URL}/swarm/insider/${activeDrone}`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error(`Evidence endpoint returned ${res.status}`);
        latest = await res.json() as InsiderEvidenceSnapshot;
        const profileReady = latest.available
          && latest.profile_synchronized
          && latest.observed_profile === profile
          && Boolean(latest.analysis);
        const statusReady = !requireNormalStatus || latest.analysis?.status === 'NORMAL';
        const enforcementReady = !requireEnforcement || Boolean(
          latest.analysis
          && latest.analysis.status !== 'NORMAL'
          && latest.containment
          && latest.containment.mode !== 'normal'
          && latest.sdn_applied
        );
        if (profileReady && statusReady && enforcementReady) return latest;
        await new Promise(resolve => window.setTimeout(resolve, 500));
      }
      throw new Error(
        latest?.analysis && requireNormalStatus
          ? 'Normal risk did not settle before the validation timeout'
          : latest?.analysis && requireEnforcement
            ? `${profile.replaceAll('_', ' ')} was observed but no containment rule was enforced`
            : `${profile.replaceAll('_', ' ')} evidence did not reach the control loop`
      );
    };
    const applySnapshot = (snapshot: InsiderEvidenceSnapshot) => {
      setObservedInsiderProfile(snapshot.observed_profile ?? null);
      setInsiderAnalysis(snapshot.analysis ?? null);
      setContainmentAnalysis(snapshot.containment ?? null);
      setInsiderDecision(snapshot.analysis ? {
        installedPath: snapshot.decision?.installed_path || snapshot.decision?.requested_path || 'unknown',
        networkAction: snapshot.decision?.network_action || 'unknown',
        constraintReason: snapshot.decision?.constraint_reason ?? null,
        sdnApplied: Boolean(snapshot.sdn_applied)
      } : null);
      setInsiderEventAge(Number(snapshot.event_age_s ?? 0));
      setInsiderEvidenceState('live');
      setInsiderEvidenceMessage('Receiving measured evidence from the control loop.');
    };

    setInsiderCommand(scenario);
    setTrafficDefenseBaseline(null);
    setTrafficValidationPhase('capturing_baseline');
    setInsiderEvidenceState('waiting');
    setInsiderFeedback(`Establishing a measured normal baseline for ${activeDroneName}…`);
    addLog(`Starting traffic-defense validation for ${activeDrone.toUpperCase()}: normal baseline → ${scenario.replaceAll('_', ' ')}.`);

    try {
      await postProfile('normal');
      const baselineSnapshot = await waitForEvidence('normal', { requireNormalStatus: true });
      if (!baselineSnapshot.analysis) throw new Error('Normal baseline did not include analyzer evidence');
      const baselineDecision: InsiderDecision | null = baselineSnapshot.decision ? {
        installedPath: baselineSnapshot.decision.installed_path || baselineSnapshot.decision.requested_path || 'unknown',
        networkAction: baselineSnapshot.decision.network_action || 'unknown',
        constraintReason: baselineSnapshot.decision.constraint_reason ?? null,
        sdnApplied: Boolean(baselineSnapshot.sdn_applied)
      } : null;
      setTrafficDefenseBaseline({
        droneId: activeDrone,
        capturedAt: Date.now(),
        analysis: baselineSnapshot.analysis,
        containment: baselineSnapshot.containment ?? null,
        decision: baselineDecision
      });
      applySnapshot(baselineSnapshot);

      setTrafficValidationPhase('running_attack');
      setInsiderEvidenceState('waiting');
      setInsiderFeedback(`Baseline captured. Injecting ${scenario.replaceAll('_', ' ')} and waiting for enforcement…`);
      await postProfile(scenario);
      const attackSnapshot = await waitForEvidence(scenario, { requireEnforcement: true });
      applySnapshot(attackSnapshot);
      setTrafficValidationPhase('complete');
      setInsiderFeedback(`Validation complete: normal baseline compared with the enforced ${scenario.replaceAll('_', ' ')} response.`);
      addLog(`Traffic-defense validation completed for ${activeDrone.toUpperCase()}.`);
    } catch (err: any) {
      const message = err.message || 'Traffic-defense validation failed';
      setTrafficValidationPhase('failed');
      setInsiderEvidenceState('error');
      setInsiderEvidenceMessage(message);
      setInsiderFeedback(`${message}. Reset to normal and try again.`);
      addLog(`Traffic-defense validation failed: ${message}`);
    } finally {
      setInsiderCommand(null);
    }
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
        addLog('Secure FL parameters updated and saved to config/fl_config.yaml.');
        return true;
      } else {
        throw new Error('Save configuration failed');
      }
    } catch (err: any) {
      addLog(`Error saving FL config: ${err.message}`);
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
      setFlProtectionPhase('verifying');
      await new Promise(resolve => window.setTimeout(resolve, 400));
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
      const message = err.message || 'Protection self-test failed';
      setFlProtectionTestError(message);
      setFlProtectionRunTimeMs(Math.round(performance.now() - startedAt));
      setFlProtectionPhase('failed');
      addLog(`Unable to verify model protection: ${message}`);
    } finally {
      setIsRunningFlProtectionTest(false);
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
    activeDroneRef.current = drone;
    setActiveDrone(drone);
    setMissionPayload(null);
    setHistory([]);
    setInsiderAnalysis(null);
    setContainmentAnalysis(null);
    setInsiderDecision(null);
    setObservedInsiderProfile(null);
    setInsiderEventAge(null);
    setTrafficDefenseBaseline(null);
    setTrafficValidationPhase('idle');
    setInsiderEvidenceState('loading');
    setInsiderEvidenceMessage('Checking the selected drone…');
    setInsiderFeedback(null);
    addLog(`Swapped active interface to [${drone.toUpperCase()}]. Recalibrating gauges.`);
  };

  const downloadReport = async () => {
    if (!token) return;
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
      addLog('Evaluation report generated securely.');
    } catch (err: any) {
      addLog(`Error generating report: ${err.message}`);
    }
  };

  const routeNames = ['direct', 'satellite', 'mesh'];
  const activeAvailablePaths = sdnReadiness?.available_paths?.[activeDrone] || [];
  const unavailablePaths = sdnReadiness?.ready
    ? routeNames.filter(path => !activeAvailablePaths.includes(path))
    : routeNames;
  const activeInsiderProfile = insiderProfiles[activeDrone] || 'normal';
  const activeDroneRecord = fleetDrones.find(drone => drone.drone_id === activeDrone);
  const activeDroneName = activeDroneRecord?.display_name || activeDrone.replaceAll('_', ' ');
  const canonicalEvent = missionPayload?.event;
  const evidenceProvenance = canonicalEvent?.telemetry?.security_evidence?.provenance;
  const networkSecurity = canonicalEvent?.network_security_analysis;
  const routingExplanation = missionPayload?.routing_explanation;
  const topRoutingAttributions = routingExplanation?.available
    ? [...(routingExplanation.explanation?.feature_attributions || [])]
        .sort((a, b) => Math.abs(b.signed_attribution) - Math.abs(a.signed_attribution))
        .slice(0, 5)
    : [];
  const activeFlSecurity = byzantineStatus.find(client => client.client_id === activeDrone);
  const trafficDefenseActive = activeInsiderProfile !== 'normal';
  const activeTrafficBaseline = trafficDefenseBaseline?.droneId === activeDrone
    ? trafficDefenseBaseline
    : null;
  const trafficDefenseVerified = Boolean(
    trafficDefenseActive
    && insiderEvidenceState === 'live'
    && observedInsiderProfile === activeInsiderProfile
    && insiderAnalysis
    && insiderAnalysis.status !== 'NORMAL'
    && containmentAnalysis
    && containmentAnalysis.mode !== 'normal'
    && insiderDecision?.sdnApplied
  );
  const selectedInsiderScenario = INSIDER_SCENARIOS.find(
    scenario => scenario.profile === selectedInsiderProfile
  ) || INSIDER_SCENARIOS[0];
  const activeInsiderScenario = INSIDER_SCENARIOS.find(
    scenario => scenario.profile === activeInsiderProfile
  ) || INSIDER_SCENARIOS[0];
  const flProtectionCandidate = flProtectionTest?.clients.find(
    client => client.client_id === flProtectionTest.malicious_candidate
  );
  const flProtectionAssertions = flProtectionTest ? [
    {
      label: 'Hostile candidate rejected',
      observed: flProtectionTest.malicious_candidate_action,
      passed: flProtectionTest.malicious_candidate_action === 'REJECTED'
    },
    {
      label: 'Candidate influence removed',
      observed: `weight ${(flProtectionCandidate?.aggregation_weight ?? 0).toFixed(3)}`,
      passed: flProtectionCandidate?.aggregation_weight === 0
    },
    {
      label: 'Protected result closer to control',
      observed: `${flProtectionTest.secure_aggregate_distance.toFixed(6)} < ${flProtectionTest.attacked_fedavg_distance.toFixed(6)}`,
      passed: flProtectionTest.secure_aggregate_distance < flProtectionTest.attacked_fedavg_distance
    },
    {
      label: 'Deployed global model untouched',
      observed: flProtectionTest.global_model_modified ? 'modified' : 'unchanged',
      passed: !flProtectionTest.global_model_modified
    },
    {
      label: 'Trust and client state untouched',
      observed: flProtectionTest.trust_history_modified || flProtectionTest.client_modes_modified ? 'modified' : 'unchanged',
      passed: !flProtectionTest.trust_history_modified && !flProtectionTest.client_modes_modified
    }
  ] : [];
  const flProtectionAssertionsPassed = flProtectionAssertions.filter(assertion => assertion.passed).length;
  const flProtectionAllAssertionsPassed = Boolean(
    flProtectionTest?.passed && flProtectionAssertions.every(assertion => assertion.passed)
  );
  const insiderEvidenceLabel: Record<InsiderEvidenceState, string> = {
    loading: 'Checking',
    waiting: 'Starting',
    live: 'Live',
    stale: 'Stale',
    error: 'Stream unavailable'
  };
  const insiderEvidenceTone = insiderEvidenceState === 'live'
    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
    : insiderEvidenceState === 'loading' || insiderEvidenceState === 'waiting'
      ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
      : 'border-rose-500/30 bg-rose-500/10 text-rose-300';
  // Unauthenticated view
  if (!token) {
    return (
      <main className="app-shell login-shell">
        <section className="login-surface" aria-labelledby="login-title">
          <header className="login-header">
            <p className="login-wordmark">FLARE</p>
            <h1 id="login-title">UAV Network Resilience</h1>
          </header>

          <form onSubmit={handleLogin} className="login-form">
            <div className="login-field">
              <label htmlFor="operator-id">Operator</label>
              <input
                id="operator-id"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-label="Operator ID"
                autoComplete="username"
                placeholder="Operator"
                required
              />
            </div>

            <div className="login-field">
              <label htmlFor="operator-password">Password</label>
              <input
                id="operator-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-label="Access Signature"
                autoComplete="current-password"
                placeholder="Password"
                required
              />
            </div>

            {authError && (
              <div role="alert" aria-live="assertive" className="login-error">
                <AlertTriangle aria-hidden="true" />
                <span>{authError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={isAuthenticating}
              className="login-submit"
            >
              {isAuthenticating ? (
                <span>Signing in...</span>
              ) : (
                <span>Sign in</span>
              )}
            </button>
          </form>
        </section>
      </main>
    );
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
    <div className="app-shell font-sans">
      <main className="command-main">
      <CommandOverview
        systemMode={systemMode}
        wsStatus={wsStatus}
        sdnController={sdnController}
        sdnReadiness={sdnReadiness}
        readinessHistory={readinessHistory}
        fleetDrones={fleetDrones}
        activeDrone={activeDrone}
        activePath={activePath}
        threatLevel={threatLevel}
        noSafeRoute={noSafeRoute}
        swarmPayloads={missionPayloads}
        unavailablePaths={unavailablePaths}
        activeAvailablePaths={activeAvailablePaths}
        showEnrollment={showEnrollment}
        enrollment={enrollmentForm}
        onToggleEnrollment={() => { setShowEnrollment(value => !value); setEnrollmentError(null); }}
        onSelectDrone={selectDrone}
        onExportReport={downloadReport}
        onLock={() => setToken(null)}
      />

      {showLegacyCommandOverview && <>
      {/* HEADER SECTION */}
      <header className="mb-6 flex flex-col items-start justify-between gap-5 border-b border-[#252b36] pb-5 xl:flex-row xl:items-center">
        <div>
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-blue-500/30 bg-blue-500/8">
              <Shield className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h1 className="flex flex-wrap items-center gap-2 text-lg font-bold tracking-tight text-slate-100 sm:text-xl">
                FLARE COMMAND CENTER
                <span className="rounded border border-blue-500/25 bg-blue-500/8 px-2 py-1 text-[10px] font-semibold uppercase text-blue-300">
                  {systemMode} Mode
                </span>
              </h1>
              <p className="mt-1 max-w-2xl text-sm leading-5 text-slate-400">Communication Threat Detection · Secure Federated Learning · Safe DQN Routing</p>
            </div>
          </div>
        </div>

        {/* Connections State */}
        <div className="flex w-full flex-wrap items-center gap-2 xl:w-auto xl:justify-end">
          {/* WS status */}
          <div className="flex min-h-10 items-center gap-2 rounded-lg border border-[#252b36] bg-[#0d0f14] px-3 py-2 text-xs">
            {wsStatus === 'connected' ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-gray-300 font-medium">Link Status: </span>
                <span className="text-emerald-400 font-semibold uppercase">ONLINE</span>
              </>
            ) : wsStatus === 'connecting' ? (
              <>
                <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                <span className="text-gray-300 font-medium">Link Status: </span>
                <span className="text-amber-400 font-semibold uppercase">TUNING...</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-rose-500" />
                <span className="text-gray-300 font-medium">Link Status: </span>
                <span className="text-rose-500 font-semibold uppercase">OFFLINE</span>
              </>
            )}
          </div>

          {/* Controller Mode */}
          <div className={`flex min-h-10 items-center gap-2 rounded-lg bg-[#0d0f14] px-3 py-2 text-xs ${
            sdnReadiness?.ready ? 'border-emerald-500/20' : 'border-amber-500/30'
          }`}>
            <Server className={`w-3.5 h-3.5 ${sdnReadiness?.ready ? 'text-emerald-400' : 'text-amber-400'}`} />
            <span className="text-gray-300 font-medium">SDN: </span>
            <span className={sdnReadiness?.ready ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>
              {sdnController} · {sdnReadiness?.ready ? 'READY' : sdnReadiness ? 'NOT READY' : 'CHECKING'}
            </span>
          </div>

          <button
            onClick={downloadReport}
            className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-blue-500/30 bg-blue-500/8 px-3 py-2 text-xs font-semibold text-blue-300 transition-colors hover:bg-blue-500/15"
          >
            <FileText className="w-3.5 h-3.5" />
            Export Academic Report
          </button>

          <button
            onClick={() => setToken(null)}
            className="cursor-pointer rounded-lg border border-rose-500/30 bg-transparent px-3 py-2 text-xs font-semibold text-rose-300 transition-colors hover:bg-rose-500/10"
          >
            Lock Terminal
          </button>
        </div>
      </header>

      {/* SWARM DRONE SELECTOR CARDS */}
      {sdnReadiness && !sdnReadiness.ready && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-950/20 p-4 text-amber-100"
        >
          <WifiOff className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
          <div>
            <p className="text-sm font-bold uppercase">SDN data plane not ready</p>
            <p className="mt-1 text-xs text-amber-100/80">
              {sdnReadiness.alert?.message || (
                `Routing changes are unavailable while the controller reports ${sdnReadiness.error || sdnReadiness.status.replace('_', ' ')}${
                  sdnReadiness.expected_switches > 0
                    ? ` (${sdnReadiness.connected_switches}/${sdnReadiness.expected_switches} switches connected).`
                    : '.'
                }`
              )}
            </p>
          </div>
        </div>
      )}

      {sdnReadiness?.ready && unavailablePaths.length > 0 && (
        <div
          role="status"
          className="mb-6 flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
          <div>
            <p className="text-sm font-bold uppercase">Reduced path availability</p>
            <p className="mt-1 text-xs text-amber-100/80">
              {unavailablePaths.map(path => path.toUpperCase()).join(', ')} unavailable for {activeDrone.toUpperCase()}.
              Available routes: {activeAvailablePaths.map(path => path.toUpperCase()).join(', ') || 'none'}.
            </p>
          </div>
        </div>
      )}

      {readinessHistory.length > 0 && (
        <section className="content-section mb-6 rounded-xl border border-[#252b36] bg-[#0d0f14] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
              <Activity className="h-4 w-4 text-blue-400" />
              Recent SDN state changes
            </h2>
            <span className="text-[10px] uppercase text-gray-500">
              Process-local history
            </span>
          </div>
          <div className="space-y-2">
            {readinessHistory.map((event, index) => (
              <div
                key={`${event.timestamp}-${index}`}
                className="flex flex-col gap-1 border-t border-[#252b36] px-1 py-2 first:border-t-0 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex items-center gap-2 text-xs text-gray-300">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${
                    event.severity === 'critical'
                      ? 'bg-rose-500'
                      : event.severity === 'warning'
                        ? 'bg-amber-400'
                        : 'bg-emerald-400'
                  }`} />
                  <span>{event.message}</span>
                </div>
                <time className="pl-4 text-[10px] text-gray-500 sm:pl-0">
                  {new Date(event.timestamp * 1000).toLocaleTimeString()}
                </time>
              </div>
            ))}
          </div>
        </section>
      )}
      </>}

      {noSafeRoute && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-xl border border-rose-500/40 bg-rose-950/20 p-4 text-rose-200"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
          <div>
            <p className="text-sm font-bold uppercase">No safe route available</p>
            <p className="mt-1 text-xs text-rose-200/80">
              {activePath === 'hold'
                ? 'Every route exceeds the configured threat threshold. FLARE has installed an explicit fail-closed HOLD rule, so data traffic is blocked until a safe route returns.'
                : `Every route exceeds the configured threat threshold. The legacy v2 policy is preserving connectivity over the least-risk route (${activePath.toUpperCase()}) in degraded mode.`}
            </p>
          </div>
        </div>
      )}

      <section aria-labelledby="provenance-heading" className="glass-panel mb-6 overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-white/5 p-4 sm:flex-row sm:items-end sm:justify-between sm:p-5">
          <div>
            <h2 id="provenance-heading" className="text-sm font-semibold text-gray-100">Decision evidence and model provenance</h2>
            <p className="mt-1 text-[11px] leading-5 text-gray-500">Source labels describe what the runtime actually consumed; scenario injection is shown separately from detector output.</p>
          </div>
          <span className="font-mono text-[10px] text-gray-500">Event schema {canonicalEvent?.schema_version || 'unavailable'}</span>
        </div>

        {telemetryUnavailable ? (
          <div role="status" className="flex items-start gap-3 bg-amber-500/[0.06] p-5 text-amber-200">
            <WifiOff className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="text-xs font-semibold">Live telemetry unavailable — HOLD requested</p>
              <p className="mt-1 text-[11px] text-amber-200/70">{telemetryUnavailable}. Sensor failure is an operational safety condition, not a malicious classification.</p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 divide-y divide-white/5 lg:grid-cols-3 lg:divide-x lg:divide-y-0">
            <div className="p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">Telemetry</p>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Source</dt><dd className="font-mono text-gray-200">{canonicalEvent?.telemetry?.source || 'unavailable'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-gray-500">Snapshot age</dt><dd className="font-mono text-gray-200">{canonicalEvent?.telemetry?.source_age_s != null ? `${canonicalEvent.telemetry.source_age_s.toFixed(2)} s` : 'live event clock'}</dd></div>
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

        <div className="border-t border-white/5 bg-black/10 p-5">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-xs font-semibold text-gray-200">DQN Q-value explanation</h3>
              <p className="mt-1 text-[10px] text-gray-500">Local diagnostic for the policy-selected action, not causal proof. An installed safety override is a separate decision.</p>
            </div>
            {routingExplanation?.available && (
              <span className="mt-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-2.5 py-1 font-mono text-[9px] text-blue-300 sm:mt-0">
                policy {routingExplanation.policy_path} → installed {routingExplanation.installed_path || 'unknown'}
              </span>
            )}
          </div>
          {routingExplanation?.available ? (
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {topRoutingAttributions.map(item => (
                <div key={item.feature} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                  <p className="truncate text-[10px] text-gray-500" title={item.feature}>{item.feature.replaceAll('_', ' ')}</p>
                  <p className={`mt-1 font-mono text-xs ${item.signed_attribution >= 0 ? 'text-amber-300' : 'text-blue-300'}`}>{item.signed_attribution >= 0 ? '+' : ''}{item.signed_attribution.toFixed(4)}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-[11px] text-gray-500">Unavailable: {routingExplanation?.reason?.replaceAll('_', ' ') || 'awaiting a persisted standard-DQN event'}</p>
          )}
        </div>
      </section>

      {showLegacyCommandOverview && <>
      <section aria-labelledby="fleet-heading" className="glass-panel mb-6 overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-white/5 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h2 id="fleet-heading" className="text-sm font-semibold text-gray-100">Enrolled UAV fleet</h2>
              <span className="rounded-full border border-blue-500/20 bg-blue-500/10 px-2 py-0.5 font-mono text-[9px] text-blue-300">{fleetDrones.length} active</span>
            </div>
            <p className="mt-1 text-[10px] text-gray-500">One registry drives telemetry, anomaly detection, routing, SDN identity checks, and FL authorization.</p>
          </div>
          <button
            type="button"
            onClick={() => { setShowEnrollment(value => !value); setEnrollmentError(null); }}
            className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-blue-500/25 bg-blue-500/10 px-3 text-[10px] font-semibold text-blue-200 transition-colors hover:bg-blue-500/15"
          >
            {showEnrollment ? <X className="h-3.5 w-3.5" /> : <UserPlus className="h-3.5 w-3.5" />}
            {showEnrollment ? 'Close enrollment' : 'Enroll a UAV'}
          </button>
        </div>

        {showEnrollment && (
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
        )}

        <div aria-label="Swarm drone selection" className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
        {fleetDrones.map((drone) => {
          const id = drone.drone_id;
          const isActive = activeDrone === id;
          const isJammed = activeDrone === id && (threatLevel === 'HIGH' || threatLevel === 'MEDIUM');
          
          return (
            <button
              type="button"
              key={id}
              onClick={() => selectDrone(id)}
              aria-pressed={isActive}
              className={`glass-panel relative w-full cursor-pointer overflow-hidden p-5 text-left transition-colors ${
                isActive 
                  ? isJammed 
                    ? 'glow-border-red border-red-500/40' 
                    : 'glow-border-blue border-blue-500/40'
                  : 'border-white/5 hover:border-white/10'
              }`}
            >
              <div className="flex justify-between items-start">
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center border ${
                    isActive
                      ? isJammed ? 'bg-red-500/10 border-red-500/20' : 'bg-blue-500/10 border-blue-500/20'
                      : 'bg-white/5 border-white/5'
                  }`}>
                    <Compass className={`w-5 h-5 ${
                      isActive 
                        ? isJammed ? 'text-red-400' : 'text-blue-400'
                        : 'text-gray-400'
                    }`} />
                  </div>
                  <div>
                    <span className="block font-bold text-gray-100">{drone.display_name}</span>
                    <p className="font-mono text-[9px] text-gray-500">{id} · port {drone.access_port}</p>
                  </div>
                </div>

                {/* status breathing light */}
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-bold text-gray-400 tracking-wider">
                    {isActive ? (isJammed ? 'THREAT' : 'ACTIVE') : 'STANDBY'}
                  </span>
                  <div className={`w-2.5 h-2.5 rounded-full ${
                    isActive 
                      ? isJammed ? 'bg-red-500 animate-status-pulse' : 'bg-emerald-400 animate-status-pulse' 
                      : 'bg-blue-500/30'
                  }`} />
                </div>
              </div>

              {/* small summary line */}
              {isActive && (
                <div className="mt-4 pt-3 border-t border-white/5 flex justify-between text-xs text-gray-400">
                  <span>PDR: <strong className="text-gray-200">{Math.round((telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.pdr || 0) * 100)}%</strong></span>
                  <span>Lat: <strong className="text-gray-200">{Math.round(telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.latency || 0)}ms</strong></span>
                  <span>Path: <strong className="text-blue-400 uppercase">{activePath}</strong></span>
                </div>
              )}
            </button>
          );
        })}
        </div>
      </section>
      </>}

      {/* WEBSITE-CONTROLLED INSIDER DEFENSE DEMO */}
      <section data-testid="defense-validation-lab" className="defense-lab content-section mb-6 overflow-hidden">
        <div className="defense-lab__header flex flex-col gap-4 p-5 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-[24px] font-semibold tracking-[-0.025em] text-gray-100">Defense validation</h2>
          </div>
          <div className="defense-lab__context">
            <span>{activeDroneName}</span>
            <span className={`defense-lab__context-state ${
              insiderDemoTab === 'model'
                ? 'is-safe'
                : attackSimulationEnabled
                  ? 'is-live'
                  : 'is-warning'
            }`}>
              {insiderDemoTab === 'model'
                ? 'Isolated test'
                : attackSimulationEnabled
                  ? 'Simulation available'
                  : 'Simulation unavailable'}
            </span>
          </div>
        </div>

        <div className="defense-lab__nav px-5 py-3 sm:px-6">
          <div role="tablist" aria-label="Defense demonstration mode" data-active={insiderDemoTab} className="defense-lab__tabs inline-flex w-full p-1 sm:w-[326px]">
            <button
              type="button"
              role="tab"
              aria-selected={insiderDemoTab === 'traffic'}
              onClick={() => setInsiderDemoTab('traffic')}
              className={`defense-lab__tab flex min-h-10 flex-1 items-center justify-center gap-2 px-4 text-[13px] font-medium ${
                insiderDemoTab === 'traffic' ? 'defense-lab__tab--active' : ''
              }`}
            >
              Traffic evidence
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={insiderDemoTab === 'model'}
              onClick={() => setInsiderDemoTab('model')}
              className={`defense-lab__tab flex min-h-10 flex-1 items-center justify-center gap-2 px-4 text-[13px] font-medium ${
                insiderDemoTab === 'model' ? 'defense-lab__tab--active' : ''
              }`}
            >
              FL update gate
            </button>
          </div>
        </div>

        <div className="defense-lab__body grid grid-cols-1 lg:grid-cols-12">
          <div className="defense-lab__controls p-5 sm:p-6 lg:col-span-5">
            {insiderDemoTab === 'traffic' ? (
              <>
                <div className="mb-6">
                  <div>
                    <h3 className="text-[18px] font-semibold tracking-[-0.015em] text-gray-100">Traffic test</h3>
                    <p className="mt-2 text-[13px] text-gray-500">State <span className={`ml-2 capitalize ${activeInsiderProfile === 'normal' ? 'text-emerald-300' : 'text-rose-300'}`}>{activeInsiderProfile.replaceAll('_', ' ')}</span></p>
                  </div>
                </div>

                <label id="insider-scenario-label" className="mb-2 block text-[13px] font-medium text-gray-300">Scenario</label>
                <div ref={scenarioMenuRef} className="relative">
                  <button
                    type="button"
                    aria-labelledby="insider-scenario-label insider-scenario-value"
                    aria-haspopup="listbox"
                    aria-expanded={scenarioMenuOpen}
                    onClick={() => setScenarioMenuOpen(open => !open)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') setScenarioMenuOpen(false);
                      if (event.key === 'ArrowDown' && !scenarioMenuOpen) {
                        event.preventDefault();
                        setScenarioMenuOpen(true);
                      }
                    }}
                    className={`defense-lab__select flex min-h-12 w-full items-center justify-between px-3.5 text-left text-sm text-gray-100 outline-none ${scenarioMenuOpen ? 'defense-lab__select--open' : ''}`}
                  >
                    <span id="insider-scenario-value">{selectedInsiderScenario.label}</span>
                    <ChevronDown className={`h-4 w-4 text-gray-500 transition-transform ${scenarioMenuOpen ? 'rotate-180' : ''}`} />
                  </button>
                  {scenarioMenuOpen && (
                    <div role="listbox" aria-labelledby="insider-scenario-label" className="defense-lab__select-menu absolute left-0 right-0 top-full z-30 mt-2 overflow-hidden p-1.5">
                      {INSIDER_SCENARIOS.filter(scenario => scenario.profile !== 'normal').map(scenario => {
                        const selected = scenario.profile === selectedInsiderProfile;
                        return (
                          <button
                            key={scenario.profile}
                            type="button"
                            role="option"
                            aria-selected={selected}
                            onClick={() => {
                              setSelectedInsiderProfile(scenario.profile);
                              setScenarioMenuOpen(false);
                            }}
                            className={`defense-lab__select-option flex min-h-11 w-full items-center justify-between gap-3 px-3 text-left text-[13px] ${selected ? 'defense-lab__select-option--selected' : ''}`}
                          >
                            <span>{scenario.label}</span>
                            {selected && <Check className="h-4 w-4" />}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="defense-lab__scenario-note mt-3">
                  <p>{selectedInsiderScenario.summary}</p>
                  <details className="defense-lab__scenario-details">
                    <summary>Expected detector response <ChevronDown aria-hidden="true" /></summary>
                    <p>{selectedInsiderScenario.evidence}</p>
                  </details>
                </div>

                <div className="mt-5 flex flex-col gap-2 sm:flex-row">
                  <button
                    type="button"
                    onClick={runTrafficDefenseValidation}
                    disabled={!attackSimulationEnabled || insiderCommand !== null}
                    className="defense-lab__primary flex min-h-12 flex-1 items-center justify-center gap-2 px-4 text-[13px] font-semibold"
                  >
                    {insiderCommand !== null
                      ? <RefreshCw className="h-4 w-4 animate-spin" />
                      : <Play className="h-4 w-4" />}
                    {trafficValidationPhase === 'capturing_baseline'
                      ? 'Capturing baseline...'
                      : trafficValidationPhase === 'running_attack'
                        ? 'Running test...'
                        : 'Run test'}
                  </button>
                  <button
                    type="button"
                    onClick={() => applyInsiderProfile('normal')}
                    disabled={!attackSimulationEnabled || insiderCommand !== null || activeInsiderProfile === 'normal'}
                    className="defense-lab__secondary flex min-h-12 items-center justify-center gap-2 px-4 text-[13px] font-medium"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Reset
                  </button>
                </div>

                <div className="defense-lab__progress mt-6" aria-label="Traffic test progress">
                  {[
                    ['Baseline', trafficValidationPhase !== 'idle'],
                    ['Attack', trafficValidationPhase === 'running_attack' || trafficValidationPhase === 'complete'],
                    ['Results', trafficValidationPhase === 'complete']
                  ].map(([label, active]) => (
                    <div key={String(label)} className={`defense-lab__progress-step ${active ? 'is-active' : ''}`}>
                      <span aria-hidden="true">{active ? <Check /> : null}</span>
                      <small>{label}</small>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="defense-lab__test-intro">
                  <h3>Federated update test</h3>
                  <p>Test the Byzantine update gate using isolated model copies.</p>
                  <span><Lock aria-hidden="true" /> Isolated test <i aria-hidden="true" /> Live model unchanged</span>
                </div>

                <div className="defense-lab__configuration mt-6">
                  <p className="defense-lab__subheading">Test configuration</p>
                  <dl>
                    <div><dt>Input</dt><dd>4 reference updates + 1 adversarial update</dd></div>
                    <div><dt>Control</dt><dd>FedAvg</dd></div>
                    <div><dt>Defense</dt><dd>Trust-weighted FedAvg</dd></div>
                    <div><dt>Isolation</dt><dd>In-memory copies</dd></div>
                  </dl>
                </div>

                <div className="defense-lab__conditions mt-5">
                  <p className="defense-lab__subheading">Pass conditions</p>
                  <div>
                    {[
                      ['Hostile update rejected', flProtectionAssertions[0]?.passed],
                      ['Protected aggregate remains close to control', flProtectionAssertions[1]?.passed],
                      ['Live model remains unchanged', flProtectionAssertions[2]?.passed]
                    ].map(([condition, passed]) => (
                      <div key={String(condition)} className={flProtectionPhase === 'complete' ? passed ? 'is-passed' : 'is-failed' : ''}>
                        <span>
                          {flProtectionPhase === 'complete' && (passed ? <Check aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />)}
                        </span>
                        {condition}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-5 grid gap-2 sm:grid-cols-[1fr_auto]">
                  <button
                    type="button"
                    onClick={runFlProtectionSelfTest}
                    disabled={isRunningFlProtectionTest}
                    className="defense-lab__primary flex min-h-12 items-center justify-center gap-2 whitespace-nowrap px-4 text-[13px] font-semibold"
                  >
                    {isRunningFlProtectionTest ? <Activity className="h-4 w-4 animate-pulse motion-reduce:animate-none" /> : <Play className="h-4 w-4" />}
                    {isRunningFlProtectionTest ? 'Test in progress' : flProtectionTest ? 'Run test again' : 'Start isolated test'}
                  </button>
                  <button
                    type="button"
                    onClick={resetFlProtectionSelfTest}
                    disabled={isRunningFlProtectionTest || flProtectionPhase === 'idle'}
                    className="defense-lab__secondary flex min-h-12 items-center justify-center gap-2 whitespace-nowrap px-4 text-[13px] font-medium"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Reset
                  </button>
                </div>

                {flProtectionPhase === 'complete' && (
                  <button
                    type="button"
                    onClick={() => setInsiderDemoTab('traffic')}
                    className="defense-lab__link mt-3 flex min-h-10 w-full items-center justify-center gap-2 px-3 text-xs font-medium text-blue-200"
                  >
                    <Radio className="h-3.5 w-3.5" /> Back to traffic evidence
                  </button>
                )}
              </>
            )}

            {insiderDemoTab === 'traffic' && insiderFeedback && (
              <p role="status" aria-live="polite" className="defense-lab__feedback mt-4 px-3 py-2.5 text-xs leading-5 text-gray-400">
                {insiderFeedback}
              </p>
            )}
          </div>

          <div className="defense-lab__evidence p-5 sm:p-6 lg:col-span-7">
            {insiderDemoTab === 'traffic' ? (
              <>
                <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm font-semibold text-gray-100">Results</p>
                  <span className={`defense-lab__live-state w-fit text-[11px] font-medium ${insiderEvidenceTone}`}>
                    {insiderEvidenceLabel[insiderEvidenceState]}
                  </span>
                </div>

                {insiderAnalysis ? (
                  <>
                    {insiderEvidenceState !== 'live' && (
                      <div role={insiderEvidenceState === 'error' || insiderEvidenceState === 'stale' ? 'alert' : 'status'} className={`mb-4 rounded-lg border px-3 py-2 text-[10px] leading-4 ${insiderEvidenceTone}`}>
                        {insiderEvidenceMessage}
                      </div>
                    )}
                    {trafficDefenseActive ? (
                      <>
                        <div role="status" aria-live="polite" className={`defense-lab__verdict p-4 sm:p-5 ${trafficDefenseVerified ? 'defense-lab__verdict--passed' : 'defense-lab__verdict--pending'}`}>
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <div className="flex items-center gap-3">
                              <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${trafficDefenseVerified ? 'bg-emerald-500/10 text-emerald-300' : 'bg-amber-500/10 text-amber-300'}`}>
                                {trafficDefenseVerified ? <ShieldCheck className="h-5 w-5" /> : <Activity className="h-5 w-5 animate-pulse motion-reduce:animate-none" />}
                              </span>
                              <div>
                                <p className="text-base font-semibold tracking-[-0.01em] text-gray-100">
                                  {trafficDefenseVerified ? `${activeInsiderScenario.label} detected and contained` : `Validating ${activeInsiderScenario.label.toLowerCase()}`}
                                </p>
                                <p className="mt-1 text-xs text-gray-500">
                                  {trafficDefenseVerified ? 'The observed attack reached an enforced SDN response.' : insiderEvidenceMessage}
                                </p>
                              </div>
                            </div>
                            <span className={`defense-lab__result-state w-fit text-[11px] font-medium ${trafficDefenseVerified ? 'text-emerald-300' : 'text-amber-300'}`}>
                              {trafficDefenseVerified ? 'Contained' : 'Pending'}
                            </span>
                          </div>
                        </div>

                        <div className="defense-lab__trace mt-4 grid sm:grid-cols-3">
                          {[
                            {
                              key: 'observed',
                              label: 'Attack observed',
                              value: observedInsiderProfile?.replaceAll('_', ' ') || 'synchronizing',
                              tone: 'text-rose-300'
                            },
                            {
                              key: 'changed',
                              label: 'Evidence changed',
                              value: `${(insiderAnalysis.control_rate_score * 100).toFixed(0)}% control, ${formatRiskPercent(insiderAnalysis.risk_score)} risk`,
                              tone: 'text-amber-300'
                            },
                            {
                              key: 'enforced',
                              label: 'Policy enforced',
                              value: `${containmentAnalysis?.mode.replaceAll('_', ' ') || 'pending'}, ${insiderDecision?.networkAction || 'pending'}`,
                              tone: trafficDefenseVerified ? 'text-emerald-300' : 'text-gray-400'
                            }
                          ].map(stage => (
                            <div key={stage.key} className="defense-lab__trace-step p-3.5">
                              <p className="text-[11px] font-medium text-gray-500">{stage.label}</p>
                              <p className={`mt-2 text-[13px] font-medium capitalize ${stage.tone}`}>{stage.value}</p>
                            </div>
                          ))}
                        </div>

                        <div className="defense-lab__comparison mt-4 overflow-hidden">
                          <div className="grid grid-cols-[1.2fr_0.8fr_0.8fr] px-4 py-3 text-[11px] font-medium text-gray-500">
                            <span>Measured signal</span>
                            <span>Normal baseline</span>
                            <span>Under attack</span>
                          </div>
                          {[
                            [
                              'Insider risk',
                              activeTrafficBaseline ? formatRiskPercent(activeTrafficBaseline.analysis.risk_score) : 'Not captured',
                              formatRiskPercent(insiderAnalysis.risk_score)
                            ],
                            [
                              'Control-rate score',
                              activeTrafficBaseline ? `${(activeTrafficBaseline.analysis.control_rate_score * 100).toFixed(0)}%` : 'Not captured',
                              `${(insiderAnalysis.control_rate_score * 100).toFixed(0)}%`
                            ],
                            [
                              'Containment',
                              activeTrafficBaseline?.containment?.mode.replaceAll('_', ' ') || 'normal',
                              containmentAnalysis?.mode.replaceAll('_', ' ') || 'pending'
                            ],
                            [
                              'Network action',
                              activeTrafficBaseline?.decision?.networkAction || 'forward',
                              insiderDecision?.networkAction || 'pending'
                            ]
                          ].map(([label, baseline, attacked]) => (
                            <div key={label} className="defense-lab__comparison-row grid grid-cols-[1.2fr_0.8fr_0.8fr] px-4 py-3 text-xs">
                              <span className="text-gray-400">{label}</span>
                              <span className="font-mono capitalize text-gray-500">{baseline}</span>
                              <span className="font-mono font-semibold capitalize text-rose-200">{attacked}</span>
                            </div>
                          ))}
                        </div>

                        <div className="defense-lab__isolation mt-4 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex items-start gap-2.5">
                            <Lock className="mt-0.5 h-4 w-4 shrink-0 text-blue-300" />
                            <div>
                              <p className="text-xs font-semibold text-gray-200">Model path unaffected</p>
                              <p className="mt-1 text-[11px] leading-4 text-gray-500">This traffic test created no Flower update. Live FL action remains {activeFlSecurity?.action?.replaceAll('_', ' ').toLowerCase() || 'not observed'}.</p>
                            </div>
                          </div>
                          <button type="button" onClick={() => setInsiderDemoTab('model')} className="defense-lab__link min-h-10 shrink-0 px-3 text-xs font-medium text-blue-200">
                            Open model test
                          </button>
                        </div>
                      </>
                    ) : (
                      <div className="defense-lab__baseline p-4 sm:p-5">
                        <div className="flex items-center gap-3">
                          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-300"><Check className="h-5 w-5" /></span>
                          <div>
                            <p className="text-base font-semibold tracking-[-0.01em] text-gray-100">Baseline ready</p>
                          </div>
                        </div>
                        <div className="defense-lab__metric-strip mt-5 grid sm:grid-cols-4">
                          {[
                            ['Risk', formatRiskPercent(insiderAnalysis.risk_score)],
                            ['Control', `${(insiderAnalysis.control_rate_score * 100).toFixed(0)}%`],
                            ['Containment', containmentAnalysis?.mode.replaceAll('_', ' ') || 'normal'],
                            ['Network', insiderDecision?.networkAction || 'forward']
                          ].map(([label, value]) => (
                            <div key={label} className="defense-lab__metric p-3">
                              <p className="text-[11px] text-gray-500">{label}</p>
                              <p className="mt-1.5 font-mono text-sm capitalize text-emerald-200">{value}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="defense-lab__metadata mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span>Observed: {observedInsiderProfile?.replaceAll('_', ' ') || 'unknown'}</span>
                      <span>Event age: {insiderEventAge === null ? 'N/A' : `${insiderEventAge.toFixed(1)}s`}</span>
                      <span>Transport: {wsStatus === 'connected' ? 'WebSocket + snapshot' : 'Snapshot fallback'}</span>
                    </div>

                    <details className="defense-lab__detector mt-4">
                      <summary>Detector signals <ChevronDown aria-hidden="true" /></summary>
                      <div className="defense-lab__detector-grid grid grid-cols-2 sm:grid-cols-4">
                        {[
                          ['Instant risk', `${(insiderAnalysis.instantaneous_risk * 100).toFixed(1)}%`],
                          ['Forwarding ratio', `${(insiderAnalysis.forwarding_ratio * 100).toFixed(1)}%`],
                          ['Claim gap', `${(insiderAnalysis.telemetry_claim_gap * 100).toFixed(1)}%`],
                          ['Control score', `${(insiderAnalysis.control_rate_score * 100).toFixed(1)}%`],
                          ['Replay score', `${(insiderAnalysis.replay_score * 100).toFixed(1)}%`],
                          ['Freshness', `${(insiderAnalysis.evidence_freshness * 100).toFixed(1)}%`],
                          ['Constraint', insiderDecision?.constraintReason?.replaceAll('_', ' ') || 'none'],
                          ['Profile match', observedInsiderProfile === activeInsiderProfile ? 'yes' : 'waiting']
                        ].map(([label, value]) => (
                          <div key={label}>
                            <p>{label}</p>
                            <strong>{value}</strong>
                          </div>
                        ))}
                      </div>
                    </details>
                  </>
                ) : (
                  <div className={`defense-lab__empty flex min-h-64 flex-col items-center justify-center px-6 text-center ${insiderEvidenceTone}`}>
                    {insiderEvidenceState === 'loading' || insiderEvidenceState === 'waiting'
                      ? <Activity className="mb-3 h-6 w-6 animate-pulse" />
                      : <AlertTriangle className="mb-3 h-6 w-6" />}
                    <p className="text-sm font-semibold">
                      {insiderEvidenceState === 'error' ? 'Live services are unreachable' : insiderEvidenceState === 'stale' ? 'Control loop stopped updating' : 'Starting control-loop analysis'}
                    </p>
                    <p className="mt-2 max-w-md text-[11px] leading-5 opacity-80">{insiderEvidenceMessage}</p>
                    <div className="defense-lab__service-state mt-4 flex flex-wrap justify-center gap-x-4 gap-y-1">
                      <span>API {systemMode === 'UNKNOWN' ? 'offline' : 'online'}</span>
                      <span>Stream {wsStatus}</span>
                      <span>SDN {sdnReadiness?.ready ? 'ready' : 'not ready'}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => fetchInsiderEvidence(activeDrone)}
                      className="defense-lab__secondary mt-5 flex min-h-10 items-center gap-2 px-4 text-xs font-medium"
                    >
                      <RefreshCw className="h-3.5 w-3.5" /> Retry
                    </button>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-100">Update gate</h3>
                    <p className="mt-1 text-[11px] text-gray-500">
                      {flProtectionPhase === 'idle'
                        ? 'Waiting for an operator to start the experiment'
                        : flProtectionPhase === 'complete'
                          ? `Completed${flProtectionRunAt ? ` at ${flProtectionRunAt}` : ''}${flProtectionRunTimeMs !== null ? ` in ${flProtectionRunTimeMs} ms` : ''}`
                          : flProtectionPhase === 'failed'
                            ? 'The test stopped before a verdict was produced'
                            : 'Executing the real secure-aggregation self-test'}
                    </p>
                  </div>
                </div>
                {isRunningFlProtectionTest && !flProtectionTest ? (
                  <div role="status" aria-live="polite" className="defense-lab__gate-running min-h-80 overflow-hidden rounded-xl border border-amber-500/20 bg-amber-500/[0.025]">
                    <div className="flex items-center justify-between border-b border-white/5 px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <Activity className="h-4 w-4 animate-pulse text-amber-300 motion-reduce:animate-none" />
                        <div>
                          <p className="text-xs font-semibold text-gray-200">Experiment running</p>
                          <p className="mt-0.5 text-[11px] text-gray-500">Analyzing isolated model copies</p>
                        </div>
                      </div>
                      <span className="font-mono text-[9px] text-amber-300">LIVE</span>
                    </div>
                    <div className="px-5 py-4">
                      {[
                        { phase: 'preparing', icon: Database, label: 'Prepare isolated model copies', detail: 'Constructing four synthetic reference updates and one amplified sign-flip candidate.' },
                        { phase: 'analyzing', icon: Shield, label: 'Run Byzantine update analysis', detail: 'Computing trust, robust deviation, update norm, and cosine direction.' },
                        { phase: 'verifying', icon: Layers, label: 'Verify aggregation invariants', detail: 'Comparing protected output with the honest control and checking live state.' }
                      ].map((stage, index) => {
                        const currentIndex = ['preparing', 'analyzing', 'verifying'].indexOf(flProtectionPhase);
                        const completed = currentIndex > index;
                        const active = currentIndex === index;
                        const StageIcon = stage.icon;
                        return (
                          <div key={stage.phase} className="relative flex gap-3 pb-5 last:pb-0">
                            {index < 2 && <span className={`absolute left-[15px] top-8 h-[calc(100%-1.5rem)] w-px ${completed ? 'bg-emerald-500/35' : 'bg-white/10'}`} aria-hidden="true" />}
                            <span className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md border ${
                              completed
                                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                                : active
                                  ? 'border-amber-500/35 bg-amber-500/10 text-amber-300'
                                  : 'border-white/10 bg-white/[0.025] text-gray-600'
                            }`}>
                              {completed ? <Check className="h-4 w-4" /> : <StageIcon className={`h-4 w-4 ${active ? 'animate-pulse motion-reduce:animate-none' : ''}`} />}
                            </span>
                            <div className="pt-0.5">
                              <p className={`text-xs font-semibold ${active ? 'text-amber-200' : completed ? 'text-emerald-200' : 'text-gray-500'}`}>{stage.label}</p>
                              <p className="mt-1 text-[9px] leading-4 text-gray-600">{stage.detail}</p>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : flProtectionTest ? (
                  <>
                    <div role="status" className={`defense-lab__gate-verdict flex flex-col gap-4 rounded-xl border-l-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between ${
                      flProtectionAllAssertionsPassed
                        ? 'border-emerald-400 bg-emerald-500/[0.065]'
                        : 'border-rose-400 bg-rose-500/[0.065]'
                    }`}>
                      <div className="flex items-center gap-4">
                        <div className={`defense-lab__gate-mark flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border-2 ${
                          flProtectionAllAssertionsPassed
                            ? 'border-emerald-400/60 text-emerald-300'
                            : 'border-rose-400/60 text-rose-300'
                        }`}>
                          {flProtectionAllAssertionsPassed ? <ShieldCheck aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
                        </div>
                        <div>
                          <p className="text-base font-semibold text-gray-100">
                            {flProtectionAllAssertionsPassed ? 'Update gate passed' : 'Update gate failed'}
                          </p>
                        </div>
                      </div>
                      <div className="shrink-0 text-left sm:text-right">
                        <p className={`font-mono text-2xl font-semibold ${flProtectionAllAssertionsPassed ? 'text-emerald-300' : 'text-rose-300'}`}>{flProtectionAssertionsPassed}/{flProtectionAssertions.length}</p>
                        <p className="text-[9px] text-gray-600">assertions passed</p>
                      </div>
                    </div>

                    <div className="defense-lab__assertions mt-3 overflow-hidden rounded-xl border border-white/10 bg-black/15">
                      <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
                        <p className="text-xs font-semibold text-gray-200">Assertion report</p>
                        <span className="font-mono text-[9px] text-gray-600">actual vs expected</span>
                      </div>
                      <div className="grid sm:grid-cols-2">
                        {flProtectionAssertions.map((assertion, index) => (
                          <div key={assertion.label} className={`flex items-start gap-2.5 px-4 py-3 ${index >= 2 ? 'border-t border-white/5' : ''} ${index % 2 === 1 ? 'sm:border-l sm:border-white/5' : ''} ${index === 1 ? 'border-t border-white/5 sm:border-t-0' : ''}`}>
                            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${assertion.passed ? 'border-emerald-500/30 text-emerald-300' : 'border-rose-500/30 text-rose-300'}`}>
                              {assertion.passed ? <Check className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
                            </span>
                            <div className="min-w-0">
                              <p className="text-[10px] font-semibold text-gray-300">{assertion.label}</p>
                              <p className={`mt-0.5 break-words font-mono text-[9px] ${assertion.passed ? 'text-emerald-300/75' : 'text-rose-300/75'}`}>Observed: {assertion.observed}</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <p className="mb-2 mt-4 text-[10px] font-semibold text-gray-300">Evidence trace</p>
                    <div className="grid items-stretch gap-3 md:grid-cols-[minmax(0,1fr)_36px_minmax(170px,0.72fr)]">
                      <div className="defense-lab__updates overflow-hidden rounded-xl border border-white/10 bg-black/15">
                        <div className="flex items-center justify-between border-b border-white/5 px-3.5 py-2.5">
                          <div>
                            <p className="text-xs font-semibold text-gray-200">Test updates</p>
                            <p className="mt-0.5 text-[11px] text-gray-500">Reference A to D are isolated test vectors</p>
                          </div>
                          <span className="font-mono text-[10px] text-gray-500">{flProtectionTest.tested_updates} total</span>
                        </div>
                        <div className="divide-y divide-white/5">
                          {flProtectionTest.clients.map((client, index) => {
                            const rejected = client.action === 'REJECTED';
                            const isCandidate = client.client_id === flProtectionTest.malicious_candidate;
                            const referenceSuffix = client.client_id.match(/^reference_update_([a-d])$/)?.[1];
                            const clientLabel = isCandidate
                              ? 'Hostile test update'
                              : referenceSuffix
                                ? `Reference update ${referenceSuffix.toUpperCase()}`
                                : 'Reference update';
                            return (
                              <div key={client.client_id} className={`defense-lab__candidate flex min-h-10 items-center gap-3 px-3.5 py-2 ${isCandidate ? 'is-candidate' : ''} ${rejected ? 'is-rejected bg-rose-500/[0.05]' : ''}`}>
                                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[9px] ${rejected ? 'border-rose-500/30 bg-rose-500/10 text-rose-300' : 'border-white/10 bg-white/[0.035] text-gray-400'}`}>
                                  {isCandidate ? '!' : index + 1}
                                </span>
                                <div className="min-w-0 flex-1">
                                  <p className={`truncate text-[10px] font-semibold ${rejected ? 'text-rose-200' : 'text-gray-300'}`}>{clientLabel}</p>
                                  <p className="mt-0.5 font-mono text-[8px] text-gray-600">weight {(client.aggregation_weight ?? 0).toFixed(3)}</p>
                                </div>
                                <span className={`text-[9px] font-semibold ${rejected ? 'text-rose-300' : client.action === 'DOWN_WEIGHTED' ? 'text-amber-300' : 'text-emerald-300'}`}>
                                  {client.action.replaceAll('_', ' ').toLowerCase()}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      <div className="hidden items-center justify-center md:flex" aria-hidden="true">
                        <ArrowRight className="h-5 w-5 text-gray-600" />
                      </div>
                      <div className={`defense-lab__gate-outcome flex min-h-48 flex-col justify-between rounded-xl border p-4 ${flProtectionAllAssertionsPassed ? 'border-emerald-500/25 bg-emerald-500/[0.055]' : 'border-rose-500/25 bg-rose-500/[0.055]'}`}>
                        <div>
                          <div className={`flex h-10 w-10 items-center justify-center rounded-lg border ${flProtectionAllAssertionsPassed ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : 'border-rose-500/30 bg-rose-500/10 text-rose-300'}`}>
                            {flProtectionAllAssertionsPassed ? <ShieldCheck className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
                          </div>
                          <p className="mt-4 text-base font-semibold text-gray-100">
                            {flProtectionAllAssertionsPassed ? 'Hostile update rejected' : 'Update requires review'}
                          </p>
                          <p className="mt-1.5 text-[10px] leading-4 text-gray-500">
                            {flProtectionTest.rejected_updates} of {flProtectionTest.tested_updates} copied updates rejected before aggregation.
                          </p>
                        </div>
                        <div className="mt-4 border-t border-white/10 pt-3">
                          <p className="text-[9px] text-gray-600">Aggregation method</p>
                          <p className="mt-1 font-mono text-[10px] text-emerald-200">{flProtectionTest.aggregation_method.replaceAll('_', ' ')}</p>
                        </div>
                      </div>
                    </div>

                    <div className="defense-lab__influence mt-3 overflow-hidden rounded-xl border border-white/10 bg-black/15">
                      <div className="flex flex-col gap-1 border-b border-white/5 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <p className="text-xs font-semibold text-gray-200">Measured model influence</p>
                        <p className="text-[9px] text-gray-500">Distance from the honest aggregate. Lower is safer.</p>
                      </div>
                      <div className="grid items-stretch sm:grid-cols-[1fr_auto_1fr]">
                        <div className="px-4 py-3.5">
                          <p className="text-[10px] text-gray-500">Without the guard</p>
                          <p className="mt-1 font-mono text-xl font-semibold text-rose-300">{flProtectionTest.attacked_fedavg_distance.toFixed(6)}</p>
                          <p className="mt-1 text-[9px] text-rose-300/70">Poisoned update included by FedAvg</p>
                        </div>
                        <div className="flex items-center justify-center border-y border-white/5 px-4 py-3 sm:border-x sm:border-y-0">
                          <div className="text-center">
                            <ArrowRight className="mx-auto h-4 w-4 text-gray-600" />
                            <p className="mt-1 font-mono text-[10px] font-semibold text-emerald-300">{flProtectionTest.damage_reduction_percent.toFixed(3)}%</p>
                            <p className="text-[8px] text-gray-600">influence removed</p>
                          </div>
                        </div>
                        <div className="px-4 py-3.5">
                          <p className="text-[10px] text-gray-500">With FLARE protection</p>
                          <p className="mt-1 font-mono text-xl font-semibold text-emerald-300">{flProtectionTest.secure_aggregate_distance.toFixed(6)}</p>
                          <p className="mt-1 text-[9px] text-emerald-300/70">Hostile candidate receives zero weight</p>
                        </div>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="defense-lab__gate-ready min-h-80 p-5">
                    <div className="grid items-center gap-4 md:grid-cols-[1fr_auto_0.8fr]">
                      <div>
                        <p className="text-[10px] font-semibold text-gray-400">Copied updates</p>
                        <div className="mt-3 flex gap-2" aria-hidden="true">
                          {[0, 1, 2, 3].map(index => <span key={index} className="flex h-8 w-8 items-center justify-center rounded-md border border-white/10 bg-white/[0.03] font-mono text-[9px] text-gray-500">D{index + 1}</span>)}
                          <span className="flex h-8 w-8 items-center justify-center rounded-md border border-rose-500/25 bg-rose-500/[0.07] font-mono text-xs text-rose-300">!</span>
                        </div>
                      </div>
                      <ArrowRight className="hidden h-5 w-5 text-gray-600 md:block" aria-hidden="true" />
                      <div className="defense-lab__gate-target flex items-center gap-3 p-3.5">
                        <ShieldCheck className="h-6 w-6 shrink-0 text-emerald-300" />
                        <div>
                          <p className="text-xs font-semibold text-gray-200">Update gate</p>
                          <p className="mt-1 text-[11px] leading-4 text-gray-500">Ready to test</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
                {flProtectionTestError && (
                  <div role="alert" className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2 text-[10px] text-rose-300">
                    Verification failed to run: {flProtectionTestError}. Check that the API service is online, then try again.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </section>

      {/* LIVE MISSION NETWORK — reuses the parent WebSocket and selected-drone state */}
      <LiveMissionSimulation
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
      />

      <React.Suspense fallback={<div className="glass-panel mb-6 h-[300px] animate-pulse" aria-label="Loading telemetry charts" />}>
        <TelemetryCharts history={history} />
      </React.Suspense>

      {/* SECURE FEDERATED LEARNING CONFIGURATION & PRIVACY CONTROL CENTER */}
      {flConfig && <SecureFlControlCenter
        config={flConfig}
        metrics={flMetrics}
        isSaving={isSavingConfig}
        onConfigChange={setFlConfig}
        onSave={saveFlConfig}
      />}

      {showLegacyFlControls && flConfig && (
        <section className="content-section glass-panel mb-6 p-4 sm:p-6">
          <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 pb-4 border-b border-white/5">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-100">
                <Sliders className="w-4 h-4 text-blue-400" />
                Secure FL Swarm Config & Privacy Controls
              </h2>
              <p className="text-xs text-gray-400 mt-0.5">Dynamically adjust parameters, enable Differential Privacy, Compression, and pFedMe Personalization</p>
            </div>
            <button
              onClick={() => saveFlConfig(flConfig)}
              disabled={isSavingConfig}
              className="flex cursor-pointer items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-blue-500 disabled:bg-blue-800"
            >
              {isSavingConfig ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Saving Parameters...
                </>
              ) : (
                <>
                  <Database className="w-3.5 h-3.5" />
                  Save config/fl_config.yaml
                </>
              )}
            </button>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* CONFIG SLIDERS / CONTROLS (Left 7 cols) */}
            <div className="lg:col-span-8 grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* DP section */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <Shield className="w-3.5 h-3.5 text-emerald-400" />
                    Differential Privacy
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="Differential Privacy enabled"
                      checked={flConfig.differential_privacy?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        differential_privacy: {
                          ...flConfig.differential_privacy,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.differential_privacy?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <div className="flex justify-between text-gray-400 mb-1">
                        <span>Client Epsilon (ε Budget)</span>
                        <span className="font-mono text-gray-200">{flConfig.differential_privacy?.client_side?.epsilon}</span>
                      </div>
                      <input
                        type="range"
                        aria-label="Client Epsilon budget"
                        min="1"
                        max="10"
                        step="0.5"
                        value={flConfig.differential_privacy?.client_side?.epsilon ?? 5.0}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          differential_privacy: {
                            ...flConfig.differential_privacy,
                            client_side: {
                              ...flConfig.differential_privacy.client_side,
                              epsilon: parseFloat(e.target.value)
                            }
                          }
                        })}
                        className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                      />
                    </div>
                    <div>
                      <div className="flex justify-between text-gray-400 mb-1">
                        <span>Server DP Noise Scale (σ)</span>
                        <span className="font-mono text-gray-200">{flConfig.differential_privacy?.server_side?.noise_scale}</span>
                      </div>
                      <input
                        type="range"
                        aria-label="Server differential privacy noise scale"
                        min="0"
                        max="0.10"
                        step="0.01"
                        value={flConfig.differential_privacy?.server_side?.noise_scale ?? 0.01}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          differential_privacy: {
                            ...flConfig.differential_privacy,
                            server_side: {
                              ...flConfig.differential_privacy.server_side,
                              noise_scale: parseFloat(e.target.value)
                            }
                          }
                        })}
                        className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Compression section */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <Cpu className="w-3.5 h-3.5 text-blue-400" />
                    Gradient Compression
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="Gradient Compression enabled"
                      checked={flConfig.compression?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        compression: {
                          ...flConfig.compression,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.compression?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <label className="text-gray-400 mb-1 block">Compression Strategy</label>
                      <select
                        aria-label="Compression Strategy"
                        value={flConfig.compression?.strategy ?? 'topk'}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          compression: {
                            ...flConfig.compression,
                            strategy: e.target.value
                          }
                        })}
                        className="w-full bg-[#111218] border border-white/5 text-gray-200 py-1 px-1.5 rounded focus:outline-none text-[10px] cursor-pointer"
                      >
                        <option value="topk">Top-K Sparsification</option>
                        <option value="quantize">INT8 Quantization</option>
                        <option value="both">Sparsify & Quantize</option>
                        <option value="none">None (Float32)</option>
                      </select>
                    </div>
                    {flConfig.compression?.strategy !== 'quantize' && (
                      <div>
                        <div className="flex justify-between text-gray-400 mb-1">
                          <span>Top-K Sparsity Ratio</span>
                          <span className="font-mono text-gray-200">{Math.round((flConfig.compression?.topk?.ratio ?? 0.10) * 100)}%</span>
                        </div>
                        <input
                          type="range"
                          aria-label="Top-K Sparsity Ratio"
                          min="0.01"
                          max="0.50"
                          step="0.01"
                          value={flConfig.compression?.topk?.ratio ?? 0.10}
                          onChange={(e) => setFlConfig({
                            ...flConfig,
                            compression: {
                              ...flConfig.compression,
                              topk: {
                                ...flConfig.compression.topk,
                                ratio: parseFloat(e.target.value)
                              }
                            }
                          })}
                          className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Personalization (pFedMe) */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <BrainCircuit className="w-3.5 h-3.5 text-blue-400" />
                    pFedMe Personalization
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="pFedMe Personalization enabled"
                      checked={flConfig.personalization?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        personalization: {
                          ...flConfig.personalization,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.personalization?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <div className="flex justify-between text-gray-400 mb-1">
                        <span>Proximal Regularization (λ)</span>
                        <span className="font-mono text-gray-200">{flConfig.personalization?.lambda_prox}</span>
                      </div>
                      <input
                        type="range"
                        aria-label="Proximal Regularization lambda"
                        min="0.01"
                        max="1.0"
                        step="0.05"
                        value={flConfig.personalization?.lambda_prox ?? 0.1}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          personalization: {
                            ...flConfig.personalization,
                            lambda_prox: parseFloat(e.target.value)
                          }
                        })}
                        className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Client Selection */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <Layers className="w-3.5 h-3.5 text-blue-400" />
                    Swarm Selection
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="Swarm Selection enabled"
                      checked={flConfig.client_selection?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        client_selection: {
                          ...flConfig.client_selection,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.client_selection?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <label className="text-gray-400 mb-1 block">Selection Strategy</label>
                      <select
                        aria-label="Selection Strategy"
                        value={flConfig.client_selection?.strategy ?? 'combined'}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          client_selection: {
                            ...flConfig.client_selection,
                            strategy: e.target.value
                          }
                        })}
                        className="w-full bg-[#111218] border border-white/5 text-gray-200 py-1 px-1.5 rounded focus:outline-none text-[10px] cursor-pointer"
                      >
                        <option value="poco">Power-of-Choice (Oort)</option>
                        <option value="ucb">UCB (Exploration/Exploit)</option>
                        <option value="combined">Combined Oort + UCB</option>
                        <option value="random">Random Sampling</option>
                      </select>
                    </div>
                  </div>
                )}
              </div>

              {/* Asynchronous FL */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <Activity className="w-3.5 h-3.5 text-blue-400" />
                    Asynchronous FL
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="Asynchronous FL enabled"
                      checked={flConfig.async_fl?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        async_fl: {
                          ...flConfig.async_fl,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.async_fl?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <div className="flex justify-between text-gray-400 mb-1">
                        <span>FedBuff Buffer Size (K)</span>
                        <span className="font-mono text-gray-200">{flConfig.async_fl?.buffer_size}</span>
                      </div>
                      <input
                        type="range"
                        aria-label="FedBuff Buffer Size"
                        min="2"
                        max="10"
                        step="1"
                        value={flConfig.async_fl?.buffer_size ?? 4}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          async_fl: {
                            ...flConfig.async_fl,
                            buffer_size: parseInt(e.target.value)
                          }
                        })}
                        className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Trust Decay / Reputation */}
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5 uppercase">
                    <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
                    Reputation Engine
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      aria-label="Reputation Engine enabled"
                      checked={flConfig.trust?.enabled ?? false}
                      onChange={(e) => setFlConfig({
                        ...flConfig,
                        trust: {
                          ...flConfig.trust,
                          enabled: e.target.checked
                        }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-8 h-4 bg-[#111218] border border-gray-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600 peer-checked:after:bg-white"></div>
                  </label>
                </div>
                {flConfig.trust?.enabled && (
                  <div className="space-y-3 pt-2 border-t border-white/5 text-[11px]">
                    <div>
                      <div className="flex justify-between text-gray-400 mb-1">
                        <span>Historical Rejection Threshold</span>
                        <span className="font-mono text-gray-200">{flConfig.trust?.reject_trust_threshold}</span>
                      </div>
                      <input
                        type="range"
                        aria-label="Quarantine Threshold"
                        min="0.05"
                        max="0.50"
                        step="0.05"
                        value={flConfig.trust?.reject_trust_threshold ?? 0.15}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          trust: {
                            ...flConfig.trust,
                            reject_trust_threshold: parseFloat(e.target.value)
                          }
                        })}
                        className="w-full bg-[#111218] rounded-lg appearance-none h-1 cursor-pointer accent-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* REAL-TIME FL MONITORING METRICS (Right 4 cols) */}
            <div className="lg:col-span-4 bg-white/[0.01] border border-white/5 p-4 rounded-xl flex flex-col justify-between">
              <div>
                <h3 className="mb-3 flex items-center gap-1.5 border-b border-white/5 pb-1.5 text-sm font-semibold text-gray-100">
                  <Activity className="w-3.5 h-3.5 text-blue-400" />
                  FL Live Metrics Console
                </h3>
                {flMetrics && flMetrics.latest ? (
                  <div className="space-y-3.5 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="text-gray-400">FL Round</span>
                      <span className="font-mono font-bold text-gray-100 bg-[#111218] border border-white/5 px-2 py-0.5 rounded">
                        {flMetrics.latest.round}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Threat Classifier F1</span>
                      <span className="font-mono text-emerald-400 font-bold">{(flMetrics.latest.threat_f1 ?? 0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Cumulative Privacy (ε)</span>
                      <span className="metric-number font-mono font-bold text-blue-300">{(flMetrics.latest.privacy_epsilon ?? 0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Avg Compression Ratio</span>
                      <span className="metric-number font-mono font-bold text-slate-200">{(flMetrics.latest.compression_ratio ?? 1.0).toFixed(1)}×</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Mean Client Trust</span>
                      <span className="metric-number font-mono font-bold text-emerald-400">{(flMetrics.latest.mean_trust ?? 1.0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Jain's Selection Fairness</span>
                      <span className="font-mono text-amber-400 font-bold">{(flMetrics.latest.jains_fairness ?? 1.0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Client Drift Rate</span>
                      <span className="metric-number font-mono font-bold text-slate-200">{((flMetrics.latest.drift_rate ?? 0) * 100).toFixed(1)}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Aggregation Method</span>
                      <span className="max-w-[55%] text-right font-mono text-blue-300">{flMetrics.latest.aggregation_method ?? 'N/A'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Suspected This Round</span>
                      <span className="font-mono font-bold text-amber-300">{flMetrics.latest.suspected_malicious_clients ?? 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Rejected This Round</span>
                      <span className="font-mono font-bold text-rose-400">{flMetrics.latest.rejected_updates ?? 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Detected Attempts</span>
                      <span className="font-mono font-bold text-slate-200">{flMetrics.latest.poisoning_attempts_detected ?? 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Adaptive Clip Bound</span>
                      <span className="font-mono font-bold text-slate-200">{typeof flMetrics.latest.effective_clip_norm === 'number' ? flMetrics.latest.effective_clip_norm.toFixed(3) : 'N/A'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Sample Claims Capped</span>
                      <span className="font-mono font-bold text-amber-300">{flMetrics.latest.sample_count_capped_clients ?? 0}</span>
                    </div>
                  </div>
                ) : (
                  <div className="py-8 text-center text-[11px] text-gray-500">
                    Awaiting server orchestration to push FL aggregation metrics...
                  </div>
                )}
              </div>

              <div className="mt-4 pt-3 border-t border-white/5 text-[9px] text-gray-500 text-center">
                SecureFedAvgV2 Pipeline Core Enabled
              </div>
            </div>
          </div>
        </section>
      )}

      {/* FEDERATED UPDATE SECURITY CONSOLE */}
      <section className="content-section glass-panel mb-6 p-5">
        <div className="mb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-100">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              Federated Update Security Console
            </h2>
            <p className="text-[10px] text-gray-500 mt-0.5">Model-delta evidence, historical trust, and Byzantine aggregation actions</p>
          </div>
          <span className="rounded border border-blue-500/20 bg-blue-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-blue-400">
            {flMetrics?.latest?.aggregation_method || 'Awaiting aggregation'}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {byzantineStatus.map((drone) => {
            const isMalicious = drone.status === 'MALICIOUS' || drone.action === 'REJECTED';
            const isSuspicious = drone.status === 'SUSPICIOUS' || drone.action === 'DOWN_WEIGHTED';
            return (
              <div key={drone.client_id} className={`p-4 rounded-xl border transition-all ${isMalicious ? 'bg-red-950/15 border-red-500/30' : isSuspicious ? 'bg-amber-950/10 border-amber-500/25' : 'bg-white/5 border-white/5'}`}>
                <div className="flex justify-between items-center mb-2.5">
                  <span className="text-xs font-bold text-gray-200">{drone.client_id.toUpperCase().replace('_', ' ')}</span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${isMalicious ? 'bg-red-500/10 border border-red-500/20 text-red-400' : isSuspicious ? 'bg-amber-500/10 border border-amber-500/20 text-amber-300' : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'}`}>
                    {drone.status}
                  </span>
                </div>
                
                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Historical Trust</span>
                    <span className="font-mono text-gray-300">{typeof drone.trust_score === 'number' ? drone.trust_score.toFixed(3) : 'N/A'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Update Deviation</span>
                    <span className="font-mono text-gray-300">{typeof drone.deviation_score === 'number' ? drone.deviation_score.toFixed(3) : 'N/A'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Update Norm</span>
                    <span className="font-mono text-gray-300">{typeof drone.update_norm === 'number' ? drone.update_norm.toFixed(3) : 'N/A'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Aggregate Weight</span>
                    <span className="font-mono text-gray-300">{typeof drone.aggregation_weight === 'number' ? drone.aggregation_weight.toFixed(3) : 'N/A'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Effective Samples</span>
                    <span className="font-mono text-gray-300">
                      {typeof drone.effective_num_examples === 'number'
                        ? `${Math.round(drone.effective_num_examples)} / ${drone.reported_num_examples ?? 'N/A'}`
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="flex justify-between items-center pt-1 border-t border-white/5">
                    <span className="text-gray-500">Aggregation Filter</span>
                    <span className={`font-bold uppercase ${isMalicious ? 'text-red-400 text-glow-red' : isSuspicious ? 'text-amber-300' : 'text-emerald-400'}`}>{drone.action}</span>
                  </div>
                </div>

                <div className="mt-3.5 flex min-h-9 items-center justify-center gap-2 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] px-3 text-[9px] font-bold uppercase text-emerald-300">
                  <Lock className="h-3 w-3" />
                  Protected · read-only monitoring
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* FOOTER TERMINAL LOG VIEW */}
      <footer className="content-section glass-panel p-5">
        <div className="flex justify-between items-center mb-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-100">
            <Database className="w-4 h-4 text-emerald-400" />
            Tactical Operation Log
          </h2>
          <span className="rounded border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-emerald-400">
            Live Stream
          </span>
        </div>

        {/* Scrolling Log Output */}
        <div
          className="operation-log h-[160px] space-y-1.5 overflow-y-auto rounded-lg border border-[#252b36] bg-[#090b0f] p-4 font-mono text-xs text-gray-400"
          role="log"
          aria-live="polite"
        >
          {statusLog.length === 0 ? (
            <div className="text-gray-600">Awaiting telemetry packets...</div>
          ) : (
            statusLog.map((log, idx) => (
              <div key={idx} className="flex gap-2">
                <span className="text-blue-400" aria-hidden="true">›</span>
                <span>{log}</span>
              </div>
            ))
          )}
        </div>
      </footer>
      </main>
    </div>
  );
}

export default App;
