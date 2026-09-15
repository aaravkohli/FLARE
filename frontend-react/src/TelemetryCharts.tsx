import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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

const tooltipStyle = {
  backgroundColor: '#11141a',
  border: '1px solid #343d4c',
  borderRadius: '8px',
  color: '#f1f5f9',
  fontSize: '12px',
};

function EmptyChartState() {
  return (
    <div className="flex h-[220px] items-center justify-center rounded-lg border border-dashed border-[#343d4c] bg-[#0b0d12] px-6 text-center text-sm text-slate-500" role="status">
      Awaiting telemetry samples...
    </div>
  );
}

export default function TelemetryCharts({ history }: TelemetryChartsProps) {
  const latest = history.at(-1);

  return (
    <section className="content-section mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-6" aria-label="Live telemetry charts">
      <div className="glass-panel p-4 sm:p-5">
        <div className="mb-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Zap className="h-4 w-4 text-blue-400" />
            RF Signal Characteristics (RSSI & SINR)
          </h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">Live scrolling wave analysis representing signal strength and SINR levels</p>
        </div>

        {history.length === 0 ? (
          <EmptyChartState />
        ) : (
          <div
            className="h-[240px] w-full"
            role="img"
            aria-label={`RF signal trend. Latest direct RSSI ${latest?.rssi_direct ?? 0} dBm and SINR ${latest?.sinr_direct ?? 0} dB.`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={history} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid stroke="#252b36" vertical={false} />
                <XAxis dataKey="time" stroke="#687385" fontSize={10} tickLine={false} axisLine={{ stroke: '#343d4c' }} minTickGap={24} />
                <YAxis stroke="#687385" fontSize={10} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#9aa4b2' }} />
                <Legend iconType="line" wrapperStyle={{ fontSize: '11px', color: '#9aa4b2', paddingTop: '8px' }} />
                <Line type="monotone" name="RSSI Direct" dataKey="rssi_direct" unit=" dBm" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
                <Line type="monotone" name="SINR Direct" dataKey="sinr_direct" unit=" dB" stroke="#9aa4b2" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="glass-panel p-4 sm:p-5">
        <div className="mb-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Activity className="h-4 w-4 text-blue-400" />
            Network Congestion (Latency & Loss)
          </h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">Continuous telemetry of packet latency in milliseconds and packet drop ratios</p>
        </div>

        {history.length === 0 ? (
          <EmptyChartState />
        ) : (
          <div
            className="h-[240px] w-full"
            role="img"
            aria-label={`Network congestion trend. Latest direct latency ${latest?.latency_direct ?? 0} milliseconds and packet loss ${latest?.loss_direct ?? 0}.`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={history} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid stroke="#252b36" vertical={false} />
                <XAxis dataKey="time" stroke="#687385" fontSize={10} tickLine={false} axisLine={{ stroke: '#343d4c' }} minTickGap={24} />
                <YAxis stroke="#687385" fontSize={10} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#9aa4b2' }} />
                <Legend iconType="line" wrapperStyle={{ fontSize: '11px', color: '#9aa4b2', paddingTop: '8px' }} />
                <Line type="monotone" name="Latency Direct" dataKey="latency_direct" unit=" ms" stroke="#fbbf24" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
                <Line type="monotone" name="Packet Loss Direct" dataKey="loss_direct" stroke="#fb7185" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </section>
  );
}
