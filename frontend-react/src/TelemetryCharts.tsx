import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, Zap } from 'lucide-react';
import type { ChartDataPoint } from './App';

interface TelemetryChartsProps {
  history: ChartDataPoint[];
}

export default function TelemetryCharts({ history }: TelemetryChartsProps) {
  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
      <div className="glass-panel p-5 rounded-2xl border border-white/5">
        <div className="mb-4">
          <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
            <Zap className="w-4 h-4 text-blue-400" />
            RF Signal Characteristics (RSSI & SINR)
          </h2>
          <p className="text-[10px] text-gray-500">Live scrolling wave analysis representing signal strength and SINR levels</p>
        </div>
        <div className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history}>
              <defs>
                <linearGradient id="colorRssi" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2}/>
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorSinr" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#a78bfa" stopOpacity={0.2}/>
                  <stop offset="95%" stopColor="#a78bfa" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis dataKey="time" stroke="#4b5563" fontSize={9} tickLine={false} />
              <YAxis stroke="#4b5563" fontSize={9} tickLine={false} />
              <Tooltip contentStyle={{ backgroundColor: '#111218', borderColor: '#1f2937' }} />
              <Area type="monotone" name="RSSI Direct" dataKey="rssi_direct" stroke="#3b82f6" strokeWidth={1.5} fillOpacity={1} fill="url(#colorRssi)" />
              <Area type="monotone" name="SINR Direct" dataKey="sinr_direct" stroke="#a78bfa" strokeWidth={1.5} fillOpacity={1} fill="url(#colorSinr)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="glass-panel p-5 rounded-2xl border border-white/5">
        <div className="mb-4">
          <h2 className="font-bold text-gray-100 text-xs tracking-wider uppercase flex items-center gap-2">
            <Activity className="w-4 h-4 text-violet-400" />
            Network Congestion (Latency & Loss)
          </h2>
          <p className="text-[10px] text-gray-500">Continuous telemetry of packet latency in milliseconds and packet drop ratios</p>
        </div>
        <div className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={history}>
              <defs>
                <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ec4899" stopOpacity={0.2}/>
                  <stop offset="95%" stopColor="#ec4899" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorLoss" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.2}/>
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis dataKey="time" stroke="#4b5563" fontSize={9} tickLine={false} />
              <YAxis stroke="#4b5563" fontSize={9} tickLine={false} />
              <Tooltip contentStyle={{ backgroundColor: '#111218', borderColor: '#1f2937' }} />
              <Area type="monotone" name="Latency Direct" dataKey="latency_direct" stroke="#ec4899" strokeWidth={1.5} fillOpacity={1} fill="url(#colorLatency)" />
              <Area type="monotone" name="Packet Loss Direct" dataKey="loss_direct" stroke="#f59e0b" strokeWidth={1.5} fillOpacity={1} fill="url(#colorLoss)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
