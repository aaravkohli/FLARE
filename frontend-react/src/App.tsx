import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  AlertTriangle,
  Database,
  Radio,
  Server,
  Shield,
  ShieldAlert,
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
  Layers
} from 'lucide-react';
import './App.css';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws');
const TelemetryCharts = React.lazy(() => import('./TelemetryCharts'));

// Type definitions
interface MetricDetail {
  path_id: string;
  rssi: number;
  pdr: number;
  sinr: number;
  latency: number;
  packet_loss: number;
}

interface GpsInfo {
  latitude: number;
  longitude: number;
  drift_m: number;
}

interface EwStatus {
  active_attack: string | null;
  jammed_paths: string[];
}

// Matches the new generator.py output: paths is an array, not a keyed dict
interface TelemetryPayload {
  type: string;
  timestamp: number;
  decision: {
    path_name: string;
    threat_level: string;
    reward: number;
    step: number;
  };
  metrics: {
    drone_id: string;
    timestamp: number;
    paths: MetricDetail[];
    gps?: GpsInfo;
    ew_status?: EwStatus;
  };
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
  // Auth state
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);

  // Connection & Active states
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const [activeDrone, setActiveDrone] = useState<'drone_1' | 'drone_2' | 'drone_3'>('drone_1');
  const [activePath, setActivePath] = useState<string>('direct');
  const [threatLevel, setThreatLevel] = useState<string>('LOW');
  const [reward, setReward] = useState<number>(0.0);
  const [step, setStep] = useState<number>(0);
  const [systemMode, setSystemMode] = useState<string>('UNKNOWN');
  const [sdnController, setSdnController] = useState<string>('UNKNOWN');

  // Telemetry details
  const [telemetry, setTelemetry] = useState<Record<string, MetricDetail> | null>(null);
  const [history, setHistory] = useState<ChartDataPoint[]>([]);

  // Control action states
  const [jamDuration, setJamDuration] = useState<number>(15);
  const [isJamming, setIsJamming] = useState<{ [key: string]: boolean }>({
    direct: false,
    satellite: false,
    mesh: false
  });
  const [statusLog, setStatusLog] = useState<string[]>([]);
  
  // Capstone Phase 4/5 States
  const [xai, setXai] = useState<{ rssi: number, pdr: number, sinr: number, latency: number, packet_loss: number } | null>({
    rssi: 15,
    pdr: 30,
    sinr: 40,
    latency: 10,
    packet_loss: 5
  });
  const [byzantineStatus, setByzantineStatus] = useState<any[]>([
    { drone_id: 'drone_1', status: 'NORMAL', anomaly_score: 0.35, z_score: 0.28, action: 'ACCEPTED' },
    { drone_id: 'drone_2', status: 'NORMAL', anomaly_score: 0.42, z_score: 0.34, action: 'ACCEPTED' },
    { drone_id: 'drone_3', status: 'NORMAL', anomaly_score: 0.39, z_score: 0.31, action: 'ACCEPTED' }
  ]);
  const [jamProfile, setJamProfile] = useState<string>('spot');

  // FLARE v2 Federated Learning states
  const [flConfig, setFlConfig] = useState<any>(null);
  const [flMetrics, setFlMetrics] = useState<any>(null);
  const [isSavingConfig, setIsSavingConfig] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const shouldReconnectRef = useRef(false);

  // Set up WebSocket connection when authenticated
  useEffect(() => {
    if (!token) return;

    shouldReconnectRef.current = true;
    connectWebSocket();
    fetchSystemHealth();
    fetchFlConfig();
    fetchFlMetrics();

    return () => {
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

  // Log helper
  const addLog = (msg: string) => {
    const timeStr = new Date().toLocaleTimeString();
    setStatusLog(prev => [`[${timeStr}] ${msg}`, ...prev.slice(0, 49)]);
  };

  const fetchSystemHealth = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (!res.ok) return;
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
        
        // If it's a telemetry broadcast
        if (payload.type === 'telemetry') {
          const dec = payload.decision;
          const met = payload.metrics;

          // Check if payload matches active drone
          if (met.drone_id === activeDrone) {
            setActivePath(dec.path_name);
            setThreatLevel(dec.threat_level);
            setReward(dec.reward);
            setStep(dec.step);

            // Safely parse paths list from backend into direct/satellite/mesh key structure
            const pathsList = (met as any).paths || [];
            const metricsDict: Record<string, MetricDetail> = {};
            pathsList.forEach((p: any) => {
              if (p && p.path_id) {
                metricsDict[p.path_id] = p;
              }
            });

            setTelemetry(metricsDict as any);

            if ((payload as any).xai) {
              setXai((payload as any).xai);
            }
            if ((payload as any).byzantine) {
              setByzantineStatus((payload as any).byzantine);
            }
            if ((payload as any).fl_metrics) {
              setFlMetrics((payload as any).fl_metrics);
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

  // Trigger Jamming API
  const injectJamming = async (path: string) => {
    if (!token) return;
    setIsJamming(prev => ({ ...prev, [path]: true }));
    addLog(`Transmitting tactical jamming carrier (${jamProfile.toUpperCase()}) on path [${path.toUpperCase()}]...`);

    try {
      const res = await fetch(`${API_BASE_URL}/jam`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          drone_id: activeDrone,
          paths: [path],
          duration: jamDuration,
          profile: jamProfile
        })
      });

      if (!res.ok) {
        throw new Error('API Jamming request failed');
      }

      const data = await res.json();
      addLog(`Jamming active on: ${JSON.stringify(data.paths)}. Profile: ${jamProfile.toUpperCase()}. Duration: ${data.duration}s.`);
      
      // Auto reset visual jam status after duration
      setTimeout(() => {
        setIsJamming(prev => ({ ...prev, [path]: false }));
        addLog(`Jamming carrier cleared on path [${path.toUpperCase()}].`);
      }, jamDuration * 1000);

    } catch (err: any) {
      addLog(`Error injecting jam: ${err.message}`);
      setIsJamming(prev => ({ ...prev, [path]: false }));
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

  // Save FL Config
  const saveFlConfig = async (updatedConfig: any) => {
    if (!token) return;
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
        addLog('FLARE v2 dynamic parameters updated and saved to config/fl_config.yaml.');
      } else {
        throw new Error('Save configuration failed');
      }
    } catch (err: any) {
      addLog(`Error saving FL config: ${err.message}`);
    } finally {
      setIsSavingConfig(false);
    }
  };

  // Byzantine Swarm Control Trigger API
  const toggleByzantineCompromise = async (drone: string, currentlyCompromised: boolean) => {
    if (!token) return;
    const endpoint = currentlyCompromised 
      ? `${API_BASE_URL}/swarm/restore/${drone}`
      : `${API_BASE_URL}/swarm/compromise/${drone}`;
      
    addLog(`Updating swarm trust profiles... updating trust state for [${drone.toUpperCase()}]`);
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (!res.ok) throw new Error('Swarm security command failed');
      addLog(`Secure aggregation updated: ${drone.toUpperCase()} is now ${currentlyCompromised ? 'HEALTHY' : 'COMPROMISED (Model Poisoning)'}.`);
    } catch (err: any) {
      addLog(`Error updating trust state: ${err.message}`);
    }
  };

  // Switch Active Drone Selector
  const selectDrone = (drone: 'drone_1' | 'drone_2' | 'drone_3') => {
    setActiveDrone(drone);
    setHistory([]);
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

  // Helpers for progress bar coloring
  const getPDRColor = (val: number) => {
    if (val >= 0.8) return 'bg-emerald-500';
    if (val >= 0.4) return 'bg-amber-500';
    return 'bg-rose-500';
  };

  const getLatencyColor = (val: number) => {
    if (val < 100) return 'bg-emerald-500';
    if (val < 300) return 'bg-amber-500';
    return 'bg-rose-500';
  };

  // Unauthenticated view
  if (!token) {
    return (
      <div className="min-h-screen bg-[#08090d] bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-blue-950/20 via-[#08090d] to-[#08090d] flex items-center justify-center p-4">
        <div className="w-full max-w-md glass-panel p-8 rounded-2xl border border-blue-500/10">
          <div className="flex flex-col items-center mb-8">
            <div className="w-16 h-16 rounded-2xl bg-blue-500/10 flex items-center justify-center border border-blue-500/20 mb-4 animate-pulse">
              <Shield className="w-8 h-8 text-blue-400" />
            </div>
            <h1 className="text-2xl font-bold text-gray-100 tracking-tight text-glow-blue">CAPSTONE SDN</h1>
            <p className="text-sm text-gray-400 mt-1">AI-Powered Anti-Jamming Command Center</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-5">
            <div>
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Operator ID</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-[#111218] border border-gray-800 rounded-xl px-4 py-3 text-gray-200 focus:outline-none focus:border-blue-500/50 transition-colors"
                placeholder="Enter Operator ID"
                required
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Access Signature</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-[#111218] border border-gray-800 rounded-xl px-4 py-3 text-gray-200 focus:outline-none focus:border-blue-500/50 transition-colors"
                placeholder="••••••••"
                required
              />
            </div>

            {authError && (
              <div className="bg-red-950/30 border border-red-500/20 text-red-400 text-xs px-4 py-3 rounded-xl flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{authError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={isAuthenticating}
              className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-3 rounded-xl transition-all shadow-lg shadow-blue-500/20 flex items-center justify-center gap-2 cursor-pointer"
            >
              {isAuthenticating ? (
                <span>Unlocking Systems...</span>
              ) : (
                <>
                  <Lock className="w-4 h-4" />
                  <span>Initiate Cryptographic Link</span>
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#08090d] text-gray-200 font-sans p-6 select-none">
      {/* HEADER SECTION */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6 pb-6 border-b border-gray-800/50">
        <div>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
              <Shield className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-gray-100 flex items-center gap-2">
                FLARE COMMAND CENTER
                <span className="text-xs bg-blue-500/10 border border-blue-500/20 text-blue-400 px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold">
                  {systemMode} Mode
                </span>
              </h1>
              <p className="text-xs text-gray-400">Federated Learning & Reinforcement Learning Anti-Jamming Swarm Router</p>
            </div>
          </div>
        </div>

        {/* Connections State */}
        <div className="flex flex-wrap items-center gap-3">
          {/* WS status */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#111218] border border-gray-800 text-xs">
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
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#111218] border border-gray-800 text-xs">
            <Server className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-gray-300 font-medium">SDN: </span>
            <span className="text-blue-400 font-semibold">{sdnController}</span>
          </div>

          <button
            onClick={downloadReport}
            className="px-3 py-1.5 rounded-xl bg-blue-950/20 border border-blue-500/20 hover:bg-blue-950/40 text-blue-400 text-xs font-semibold transition-colors cursor-pointer flex items-center gap-1.5"
          >
            <FileText className="w-3.5 h-3.5" />
            Export Academic Report
          </button>

          <button
            onClick={() => setToken(null)}
            className="px-3 py-1.5 rounded-xl bg-red-950/20 border border-red-500/20 hover:bg-red-950/40 text-red-400 text-xs font-semibold transition-colors cursor-pointer"
          >
            Lock Terminal
          </button>
        </div>
      </header>

      {/* SWARM DRONE SELECTOR CARDS */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        {(['drone_1', 'drone_2', 'drone_3'] as const).map((id) => {
          const isActive = activeDrone === id;
          const isJammed = activeDrone === id && (threatLevel === 'HIGH' || threatLevel === 'MEDIUM');
          
          return (
            <div
              key={id}
              onClick={() => selectDrone(id)}
              className={`glass-panel p-5 rounded-2xl border transition-all cursor-pointer relative overflow-hidden ${
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
                    <h2 className="font-bold text-gray-100 uppercase tracking-wide">{id.replace('_', ' ')}</h2>
                    <p className="text-[10px] text-gray-500">Autonomous UAV node</p>
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
            </div>
          );
        })}
      </section>

      {/* TOPOLOGY MAP & CORE GAUGES */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mb-6">
        {/* SVG Network Topology (Left 8 cols) */}
        <section className="lg:col-span-8 glass-panel p-6 rounded-2xl flex flex-col justify-between min-h-[420px]">
          <div>
            <h2 className="font-bold text-gray-100 flex items-center gap-2 text-sm tracking-wider uppercase mb-1">
              <Activity className="w-4 h-4 text-blue-400" />
              Live Flow Map & SDN Path Topology
            </h2>
            <p className="text-xs text-gray-400">Animated packet flow vectors representing routing rules installed by {sdnController}</p>
          </div>

          {/* SVG Map Container */}
          <div className="flex-1 flex items-center justify-center p-4 min-h-[280px]">
            <svg viewBox="0 0 800 240" className="w-full max-w-3xl overflow-visible">
              <defs>
                {/* Neon Glow Filters */}
                <filter id="glow-blue" x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="6" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
                <filter id="glow-red" x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="6" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* CURVED PATH LINKS */}
              {/* Direct Path Switch Link (s1 -> s2 -> s5) */}
              <path
                d="M 120 120 Q 250 30 380 30 Q 510 30 640 120"
                fill="none"
                stroke={activePath === 'direct' ? '#3b82f6' : isJamming['direct'] ? '#ef4444' : '#1e293b'}
                strokeWidth={activePath === 'direct' ? 4 : 2}
                className={activePath === 'direct' ? 'animate-svg-flow' : isJamming['direct'] ? 'animate-svg-jam-flow' : ''}
                filter={activePath === 'direct' ? 'url(#glow-blue)' : isJamming['direct'] ? 'url(#glow-red)' : ''}
              />
              {isJamming['direct'] && (
                <path
                  d="M 120 120 Q 250 30 380 30 Q 510 30 640 120"
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth={8}
                  className="animate-warning-pulse"
                />
              )}

              {/* Satellite Path Switch Link (s1 -> s3 -> s5) */}
              <path
                d="M 120 120 Q 380 -20 640 120"
                fill="none"
                stroke={activePath === 'satellite' ? '#3b82f6' : isJamming['satellite'] ? '#ef4444' : '#1e293b'}
                strokeWidth={activePath === 'satellite' ? 4 : 2}
                className={activePath === 'satellite' ? 'animate-svg-flow' : isJamming['satellite'] ? 'animate-svg-jam-flow' : ''}
                filter={activePath === 'satellite' ? 'url(#glow-blue)' : isJamming['satellite'] ? 'url(#glow-red)' : ''}
              />
              {isJamming['satellite'] && (
                <path
                  d="M 120 120 Q 380 -20 640 120"
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth={8}
                  className="animate-warning-pulse"
                />
              )}

              {/* Mesh Path Switch Link (s1 -> s4 -> s5) */}
              <path
                d="M 120 120 Q 380 260 640 120"
                fill="none"
                stroke={activePath === 'mesh' ? '#3b82f6' : isJamming['mesh'] ? '#ef4444' : '#1e293b'}
                strokeWidth={activePath === 'mesh' ? 4 : 2}
                className={activePath === 'mesh' ? 'animate-svg-flow' : isJamming['mesh'] ? 'animate-svg-jam-flow' : ''}
                filter={activePath === 'mesh' ? 'url(#glow-blue)' : isJamming['mesh'] ? 'url(#glow-red)' : ''}
              />
              {isJamming['mesh'] && (
                <path
                  d="M 120 120 Q 380 260 640 120"
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth={8}
                  className="animate-warning-pulse"
                />
              )}

              {/* INTERMEDIATE SWITCH NODES */}
              {/* s2 (Direct) */}
              <g transform="translate(380, 30)">
                <circle r="14" fill="#0b0f19" stroke={activePath === 'direct' ? '#60a5fa' : isJamming['direct'] ? '#f87171' : '#334155'} strokeWidth="2" />
                <text textAnchor="middle" y="4" fill="#94a3b8" fontSize="10" fontWeight="bold">s2</text>
                <text textAnchor="middle" y="-20" fill="#cbd5e1" fontSize="10" fontWeight="semibold">Direct Link</text>
              </g>

              {/* s3 (Satellite) */}
              <g transform="translate(380, 50)">
                <circle r="14" fill="#0b0f19" stroke={activePath === 'satellite' ? '#60a5fa' : isJamming['satellite'] ? '#f87171' : '#334155'} strokeWidth="2" />
                <text textAnchor="middle" y="4" fill="#94a3b8" fontSize="10" fontWeight="bold">s3</text>
                <text textAnchor="middle" y="-20" fill="#cbd5e1" fontSize="10" fontWeight="semibold">Satellite Fallback</text>
              </g>

              {/* s4 (Mesh) */}
              <g transform="translate(380, 190)">
                <circle r="14" fill="#0b0f19" stroke={activePath === 'mesh' ? '#60a5fa' : isJamming['mesh'] ? '#f87171' : '#334155'} strokeWidth="2" />
                <text textAnchor="middle" y="4" fill="#94a3b8" fontSize="10" fontWeight="bold">s4</text>
                <text textAnchor="middle" y="24" fill="#cbd5e1" fontSize="10" fontWeight="semibold">Mesh Relay</text>
              </g>

              {/* CORE INGRESS SWITCH (s1) & DRONE (h2) */}
              <g transform="translate(120, 120)">
                {/* s1 */}
                <circle r="22" fill="#0b0f19" stroke="#3b82f6" strokeWidth="2" />
                <text textAnchor="middle" y="4" fill="#60a5fa" fontSize="12" fontWeight="bold">s1</text>
                <text textAnchor="middle" y="-30" fill="#f3f4f6" fontSize="11" fontWeight="bold">Ingress Switch</text>
                {/* Connection line to h2 */}
                <line x1="-22" y1="0" x2="-60" y2="0" stroke="#334155" strokeWidth="2" strokeDasharray="3 3" />
                {/* h2 Node */}
                <g transform="translate(-85, 0)">
                  <rect x="-15" y="-15" width="30" height="30" rx="6" fill="#1e1b4b" stroke="#818cf8" strokeWidth="2" />
                  <text textAnchor="middle" y="4" fill="#c7d2fe" fontSize="10" fontWeight="bold">h2</text>
                  <text textAnchor="middle" y="26" fill="#94a3b8" fontSize="9" fontWeight="semibold">Drone Client</text>
                </g>
              </g>

              {/* CORE EGRESS SWITCH (s5) & BASE STATION (h1) */}
              <g transform="translate(640, 120)">
                {/* s5 */}
                <circle r="22" fill="#0b0f19" stroke="#3b82f6" strokeWidth="2" />
                <text textAnchor="middle" y="4" fill="#60a5fa" fontSize="12" fontWeight="bold">s5</text>
                <text textAnchor="middle" y="-30" fill="#f3f4f6" fontSize="11" fontWeight="bold">Egress Switch</text>
                {/* Connection line to h1 */}
                <line x1="22" y1="0" x2="60" y2="0" stroke="#334155" strokeWidth="2" strokeDasharray="3 3" />
                {/* h1 Node */}
                <g transform="translate(85, 0)">
                  <rect x="-15" y="-15" width="30" height="30" rx="6" fill="#064e3b" stroke="#34d399" strokeWidth="2" />
                  <text textAnchor="middle" y="4" fill="#a7f3d0" fontSize="10" fontWeight="bold">h1</text>
                  <text textAnchor="middle" y="26" fill="#94a3b8" fontSize="9" fontWeight="semibold">Base Station</text>
                </g>
              </g>
            </svg>
          </div>

          {/* Quick Stats Overlay Footer */}
          <div className="flex flex-wrap justify-between items-center gap-4 pt-4 border-t border-white/5 text-xs text-gray-400">
            <div className="flex items-center gap-2">
              <ShieldAlert className={`w-4 h-4 ${threatLevel === 'HIGH' ? 'text-rose-400' : 'text-emerald-400'}`} />
              <span>Current Threat Level: <strong className={threatLevel === 'HIGH' ? 'text-rose-400 text-glow-red' : 'text-emerald-400'}>{threatLevel}</strong></span>
            </div>
            <div>
              <span>RL step: <strong className="text-gray-200">{step}</strong></span>
            </div>
            <div>
              <span>RL reward: <strong className="text-emerald-400 font-mono">{reward.toFixed(4)}</strong></span>
            </div>
          </div>
        </section>

        {/* Real-time RF Gauges (Right 4 cols) */}
        <section className="lg:col-span-4 flex flex-col gap-4">
          <div className="glass-panel p-5 rounded-2xl border border-white/5 flex-1 flex flex-col justify-between">
            <div className="mb-4">
              <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
                <Radio className="w-4 h-4 text-blue-400" />
                Active Path Telemetry
              </h2>
              <p className="text-[10px] text-gray-500 mt-0.5">Physical telemetry metrics queried from link interfaces</p>
            </div>

            {/* Metrics Bars */}
            <div className="space-y-4">
              {/* Latency */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-400 font-medium">Latency</span>
                  <span className="font-mono text-gray-200">{telemetry ? Math.round(telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.latency || 0) : 0} ms</span>
                </div>
                <div className="w-full bg-[#111218] h-2 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 ${getLatencyColor(telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.latency || 0 : 0)}`}
                    style={{ width: `${Math.min(((telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.latency || 0 : 0) / 1000) * 100, 100)}%` }}
                  />
                </div>
              </div>

              {/* PDR */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-400 font-medium">Packet Delivery Ratio (PDR)</span>
                  <span className="font-mono text-gray-200">{telemetry ? Math.round((telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.pdr || 0) * 100) : 0}%</span>
                </div>
                <div className="w-full bg-[#111218] h-2 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-300 ${getPDRColor(telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.pdr || 0 : 0)}`}
                    style={{ width: `${(telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.pdr || 0 : 0) * 100}%` }}
                  />
                </div>
              </div>

              {/* RSSI */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-400 font-medium">Signal Strength (RSSI)</span>
                  <span className="font-mono text-gray-200">{telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.rssi : -120} dBm</span>
                </div>
                <div className="w-full bg-[#111218] h-2 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${Math.max((( (telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.rssi : -120) + 130) / 110) * 100, 0)}%` }}
                  />
                </div>
              </div>

              {/* SINR */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-400 font-medium">SINR</span>
                  <span className="font-mono text-gray-200">{telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.sinr : 0} dB</span>
                </div>
                <div className="w-full bg-[#111218] h-2 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-violet-500 transition-all duration-300"
                    style={{ width: `${Math.max((( (telemetry ? telemetry?.[activePath as 'direct' | 'satellite' | 'mesh']?.sinr : 0) + 10) / 45) * 100, 0)}%` }}
                  />
                </div>
              </div>
            </div>

            <div className="mt-4 pt-3 border-t border-white/5 text-[10px] text-gray-500 italic">
              Active interface: 10.0.0.2 → 10.0.0.1
            </div>
          </div>

          {/* Explainable AI (XAI) Diagnostics Panel */}
          <div className="glass-panel p-5 rounded-2xl border border-white/5">
            <div className="mb-4">
              <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
                <BrainCircuit className="w-4 h-4 text-violet-400" />
                Edge Explainable AI (XAI)
              </h2>
              <p className="text-[10px] text-gray-500 mt-0.5">Local perturbation feature attributions on BiLSTM threat model</p>
            </div>

            <div className="space-y-3">
              {xai ? (
                <>
                  {/* RSSI weight */}
                  <div>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-gray-400 font-medium">Signal Strength (RSSI) influence</span>
                      <span className="font-mono text-violet-400 font-semibold">{xai.rssi}%</span>
                    </div>
                    <div className="w-full bg-[#111218] h-1.5 rounded-full overflow-hidden">
                      <div className="h-full bg-violet-500 transition-all duration-500" style={{ width: `${xai.rssi}%` }} />
                    </div>
                  </div>

                  {/* PDR weight */}
                  <div>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-gray-400 font-medium">PDR influence</span>
                      <span className="font-mono text-violet-400 font-semibold">{xai.pdr}%</span>
                    </div>
                    <div className="w-full bg-[#111218] h-1.5 rounded-full overflow-hidden">
                      <div className="h-full bg-violet-500 transition-all duration-500" style={{ width: `${xai.pdr}%` }} />
                    </div>
                  </div>

                  {/* SINR weight */}
                  <div>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-gray-400 font-medium">Signal Quality (SINR) influence</span>
                      <span className="font-mono text-violet-400 font-semibold">{xai.sinr}%</span>
                    </div>
                    <div className="w-full bg-[#111218] h-1.5 rounded-full overflow-hidden">
                      <div className="h-full bg-violet-500 transition-all duration-500" style={{ width: `${xai.sinr}%` }} />
                    </div>
                  </div>

                  {/* Latency weight */}
                  <div>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-gray-400 font-medium">Latency influence</span>
                      <span className="font-mono text-violet-400 font-semibold">{xai.latency}%</span>
                    </div>
                    <div className="w-full bg-[#111218] h-1.5 rounded-full overflow-hidden">
                      <div className="h-full bg-violet-500 transition-all duration-500" style={{ width: `${xai.latency}%` }} />
                    </div>
                  </div>

                  {/* Packet Loss weight */}
                  <div>
                    <div className="flex justify-between text-[11px] mb-1">
                      <span className="text-gray-400 font-medium">Packet Loss influence</span>
                      <span className="font-mono text-violet-400 font-semibold">{xai.packet_loss}%</span>
                    </div>
                    <div className="w-full bg-[#111218] h-1.5 rounded-full overflow-hidden">
                      <div className="h-full bg-violet-500 transition-all duration-500" style={{ width: `${xai.packet_loss}%` }} />
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-center text-[10px] text-gray-500 py-4 italic">
                  Awaiting FL model inference to calculate attributions...
                </div>
              )}
            </div>
          </div>

          {/* Adversarial Jamming controls */}
          <div className="glass-panel p-5 rounded-2xl border border-white/5">
            <div className="mb-4">
              <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-500" />
                Adversarial Controls
              </h2>
              <p className="text-[10px] text-gray-500 mt-0.5">Inject physical wave interference to evaluate anti-jamming routing</p>
            </div>

            <div className="space-y-3">
              <div>
                <label className="flex justify-between text-[10px] text-gray-400 font-semibold mb-1">
                  <span>DURATION: {jamDuration} SECONDS</span>
                </label>
                <input
                  type="range"
                  min="5"
                  max="60"
                  step="5"
                  value={jamDuration}
                  onChange={(e) => setJamDuration(parseInt(e.target.value))}
                  className="w-full bg-[#111218] rounded-lg appearance-none h-1.5 cursor-pointer accent-blue-500"
                />
              </div>

              <div>
                <label className="text-[10px] text-gray-400 font-semibold mb-1 block">JAMMING PROFILE / EW METHOD</label>
                <select
                  value={jamProfile}
                  onChange={(e) => setJamProfile(e.target.value)}
                  className="w-full bg-[#111218] border border-white/5 text-gray-200 text-xs py-1.5 px-2 rounded-lg focus:outline-none focus:border-blue-500 cursor-pointer"
                >
                  <option value="spot">Spot Jamming (Target Path)</option>
                  <option value="barrage">Barrage Jamming (All Paths)</option>
                  <option value="sweep">Sweep Jamming (Dynamic Sweep)</option>
                  <option value="spoofing">Deceptive Spoofing (Subtle Attack)</option>
                  <option value="reactive">Reactive Jamming (Dynamic Sensing)</option>
                  <option value="adaptive">Adaptive RL Jamming (Cognitive EW)</option>
                  <option value="smart">Smart Jamming (Control Target)</option>
                  <option value="fhss">FHSS Mitigation (Hopping Defense)</option>
                  <option value="gps_spoofing">GPS Spoofing (Position Drift)</option>
                  <option value="replay">Telemetry Replay (State Freeze)</option>
                  <option value="dos">Denial of Service (Link Flood)</option>
                  <option value="sybil">Sybil Swarm Attack (Fake Nodes)</option>
                  <option value="model_poisoning">Model Poisoning (Weight Hijack)</option>
                  <option value="data_poisoning">Data Poisoning (Label Flip)</option>
                  <option value="backdoor">Backdoor Trojan Trigger</option>
                </select>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={() => injectJamming('direct')}
                  disabled={isJamming['direct']}
                  className={`py-2 px-1 text-[10px] font-bold rounded-xl border transition-all cursor-pointer ${
                    isJamming['direct']
                      ? 'bg-red-950/20 border-red-500/20 text-red-500 animate-pulse'
                      : 'bg-white/5 border-white/5 hover:bg-white/10 text-gray-300'
                  }`}
                >
                  JAM DIRECT
                </button>

                <button
                  onClick={() => injectJamming('satellite')}
                  disabled={isJamming['satellite']}
                  className={`py-2 px-1 text-[10px] font-bold rounded-xl border transition-all cursor-pointer ${
                    isJamming['satellite']
                      ? 'bg-red-950/20 border-red-500/20 text-red-500 animate-pulse'
                      : 'bg-white/5 border-white/5 hover:bg-white/10 text-gray-300'
                  }`}
                >
                  JAM SAT
                </button>

                <button
                  onClick={() => injectJamming('mesh')}
                  disabled={isJamming['mesh']}
                  className={`py-2 px-1 text-[10px] font-bold rounded-xl border transition-all cursor-pointer ${
                    isJamming['mesh']
                      ? 'bg-red-950/20 border-red-500/20 text-red-500 animate-pulse'
                      : 'bg-white/5 border-white/5 hover:bg-white/10 text-gray-300'
                  }`}
                >
                  JAM MESH
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <React.Suspense fallback={<div className="glass-panel h-[300px] mb-6 rounded-2xl animate-pulse" />}>
        <TelemetryCharts history={history} />
      </React.Suspense>

      {/* FLARE V2 FEDERATED LEARNING CONFIGURATION & PRIVACY CONTROL CENTER */}
      {flConfig && (
        <section className="glass-panel p-6 rounded-2xl border border-white/5 mb-6">
          <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-6 pb-4 border-b border-white/5">
            <div>
              <h2 className="font-bold text-gray-100 text-sm tracking-wider uppercase flex items-center gap-2">
                <Sliders className="w-4 h-4 text-blue-400" />
                FLARE v2 Swarm Config & Privacy Controls
              </h2>
              <p className="text-xs text-gray-400 mt-0.5">Dynamically adjust parameters, enable Differential Privacy, Compression, and pFedMe Personalization</p>
            </div>
            <button
              onClick={() => saveFlConfig(flConfig)}
              disabled={isSavingConfig}
              className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white text-xs font-semibold shadow-lg shadow-blue-500/20 transition-all cursor-pointer flex items-center gap-1.5"
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
                    <BrainCircuit className="w-3.5 h-3.5 text-violet-400" />
                    pFedMe Personalization
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
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
                    <Layers className="w-3.5 h-3.5 text-amber-400" />
                    Swarm Selection
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
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
                    <Activity className="w-3.5 h-3.5 text-pink-400" />
                    Asynchronous FL
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
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
                    <ShieldCheck className="w-3.5 h-3.5 text-teal-400" />
                    Reputation Engine
                  </span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
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
                        <span>Quarantine Threshold (τ_min)</span>
                        <span className="font-mono text-gray-200">{flConfig.trust?.tau_min}</span>
                      </div>
                      <input
                        type="range"
                        min="0.05"
                        max="0.50"
                        step="0.05"
                        value={flConfig.trust?.tau_min ?? 0.20}
                        onChange={(e) => setFlConfig({
                          ...flConfig,
                          trust: {
                            ...flConfig.trust,
                            tau_min: parseFloat(e.target.value)
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
                <h3 className="text-xs font-bold text-gray-100 uppercase tracking-wider mb-3 pb-1.5 border-b border-white/5 flex items-center gap-1.5">
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
                      <span className="font-mono text-violet-400 font-bold">{(flMetrics.latest.privacy_epsilon ?? 0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Avg Compression Ratio</span>
                      <span className="font-mono text-blue-400 font-bold">{(flMetrics.latest.compression_ratio ?? 1.0).toFixed(1)}×</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Mean Client Trust</span>
                      <span className="font-mono text-teal-400 font-bold">{(flMetrics.latest.mean_trust ?? 1.0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Jain's Selection Fairness</span>
                      <span className="font-mono text-amber-400 font-bold">{(flMetrics.latest.jains_fairness ?? 1.0).toFixed(4)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-400">Client Drift Rate</span>
                      <span className="font-mono text-pink-400 font-bold">{((flMetrics.latest.drift_rate ?? 0) * 100).toFixed(1)}%</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-center text-[11px] text-gray-500 py-8 italic">
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

      {/* BYZANTINE SWARM SECURITY CONSOLE */}
      <section className="glass-panel p-5 rounded-2xl border border-white/5 mb-6">
        <div className="mb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
          <div>
            <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              Byzantine Swarm Trust Console
            </h2>
            <p className="text-[10px] text-gray-500 mt-0.5">Secure Federated Learning weight aggregation & gradient anomalies</p>
          </div>
          <span className="text-[9px] bg-blue-500/10 border border-blue-500/20 text-blue-400 px-2 py-0.5 rounded uppercase font-bold tracking-wider">
            Trimmed-Mean Aggregations Enabled
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {byzantineStatus.map((drone) => {
            const isComp = drone.status === 'COMPROMISED';
            return (
              <div key={drone.drone_id} className={`p-4 rounded-xl border transition-all ${isComp ? 'bg-red-950/15 border-red-500/20' : 'bg-white/5 border-white/5'}`}>
                <div className="flex justify-between items-center mb-2.5">
                  <span className="text-xs font-bold text-gray-200">{drone.drone_id.toUpperCase().replace('_', ' ')}</span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${isComp ? 'bg-red-500/10 border border-red-500/20 text-red-400' : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'}`}>
                    {drone.status}
                  </span>
                </div>
                
                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Anomaly Score</span>
                    <span className="font-mono text-gray-300">{drone.anomaly_score}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Z-Score Deviation</span>
                    <span className="font-mono text-gray-300">{drone.z_score}</span>
                  </div>
                  <div className="flex justify-between items-center pt-1 border-t border-white/5">
                    <span className="text-gray-500">Aggregation Filter</span>
                    <span className={`font-bold uppercase ${isComp ? 'text-red-400 text-glow-red animate-pulse' : 'text-emerald-400'}`}>{drone.action}</span>
                  </div>
                </div>

                <div className="mt-3.5">
                  <button
                    onClick={() => toggleByzantineCompromise(drone.drone_id, isComp)}
                    className={`w-full py-1.5 text-[10px] font-bold rounded-lg border transition-all cursor-pointer ${
                      isComp 
                        ? 'bg-emerald-500/10 border-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400' 
                        : 'bg-red-500/10 border-red-500/20 hover:bg-red-500/30 text-red-400'
                    }`}
                  >
                    {isComp ? 'RESTORE TRUST PROFILE' : 'SIMULATE BYZANTINE ATTACK'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* FOOTER TERMINAL LOG VIEW */}
      <footer className="glass-panel p-5 rounded-2xl border border-white/5">
        <div className="flex justify-between items-center mb-3">
          <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
            <Database className="w-4 h-4 text-emerald-400" />
            Tactical Operation Log
          </h2>
          <span className="text-[9px] bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded uppercase font-bold tracking-wider">
            Live Stream
          </span>
        </div>

        {/* Scrolling Log Output */}
        <div className="bg-[#0b0c10] border border-gray-900 rounded-xl p-4 h-[120px] overflow-y-auto font-mono text-xs text-gray-400 space-y-1.5">
          {statusLog.length === 0 ? (
            <div className="text-gray-600 italic">Awaiting telemetry packets...</div>
          ) : (
            statusLog.map((log, idx) => (
              <div key={idx} className="flex gap-2">
                <span className="text-emerald-500">❯</span>
                <span>{log}</span>
              </div>
            ))
          )}
        </div>
      </footer>
    </div>
  );
}

export default App;
