import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Activity,
  BrainCircuit,
  ChevronDown,
  Cpu,
  Database,
  Layers,
  RefreshCw,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react';
import './secure-fl-control-center.css';

type FlConfig = Record<string, any>;
type FlMetrics = { latest?: Record<string, unknown> } | null;
type ConfigSection = 'safeguards' | 'federation';

interface SecureFlControlCenterProps {
  config: FlConfig;
  metrics: FlMetrics;
  evidenceWarning?: string;
  isSaving: boolean;
  onConfigChange: (config: FlConfig) => void;
  onSave: (config: FlConfig) => Promise<boolean>;
}

const number = (value: unknown, digits = 2) => typeof value === 'number' ? value.toFixed(digits) : '—';
const percent = (value: unknown, digits = 1) => typeof value === 'number' ? `${(value * 100).toFixed(digits)}%` : '—';

function deepCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (next: boolean) => void }) {
  return (
    <label className="flc-toggle">
      <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} />
      <span className="flc-toggle__track" aria-hidden="true"><span /></span>
      <span className="sr-only">{label}</span>
    </label>
  );
}

function RangeField({ id, label, value, min, max, step, display, onChange }: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flc-range-field">
      <label htmlFor={id}>{label}<output htmlFor={id}>{display}</output></label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={event => onChange(Number(event.target.value))} />
    </div>
  );
}

function ControlModule({ icon: Icon, title, description, enabled, onToggle, children }: {
  icon: typeof Shield;
  title: string;
  description: string;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  children: ReactNode;
}) {
  return (
    <article className={`flc-module${enabled ? ' is-enabled' : ''}`}>
      <div className="flc-module__header">
        <span className="flc-module__icon"><Icon aria-hidden="true" /></span>
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <span className={`flc-module__state${enabled ? ' is-on' : ''}`}>{enabled ? 'On' : 'Off'}</span>
        <Toggle checked={enabled} label={`${title} enabled`} onChange={onToggle} />
      </div>
      {enabled && <div className="flc-module__controls">{children}</div>}
    </article>
  );
}

function Metric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'neutral' | 'nominal' | 'warning' | 'danger' }) {
  return (
    <div className={`flc-metric flc-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default function SecureFlControlCenter({ config, metrics, evidenceWarning, isSaving, onConfigChange, onSave }: SecureFlControlCenterProps) {
  const [section, setSection] = useState<ConfigSection>('safeguards');
  const [saveState, setSaveState] = useState<'idle' | 'saved' | 'error'>('idle');
  const latest = metrics?.latest;
  const serialized = useMemo(() => JSON.stringify(config), [config]);
  const [baseline, setBaseline] = useState<string | null>(null);

  useEffect(() => {
    setBaseline(prev => (prev === null ? serialized : prev));
  }, [serialized]);

  const dirty = baseline !== null && baseline !== serialized;

  const change = (next: FlConfig) => {
    setSaveState('idle');
    onConfigChange(next);
  };

  const update = (sectionName: string, value: Record<string, unknown>) => change({
    ...config,
    [sectionName]: { ...config[sectionName], ...value },
  });

  const save = async () => {
    const saved = await onSave(config);
    if (saved) {
      setBaseline(JSON.stringify(config));
      setSaveState('saved');
    } else {
      setSaveState('error');
    }
  };

  const revert = () => {
    if (!baseline) return;
    change(deepCopy(JSON.parse(baseline)));
  };

  const protectionEnabled = [
    config.differential_privacy?.enabled,
    config.trust?.enabled,
    config.byzantine_aggregation?.enabled,
  ].filter(Boolean).length;
  const activeClients = typeof latest?.participating_clients === 'number' ? latest.participating_clients : undefined;
  const rejected = typeof latest?.rejected_updates === 'number' ? latest.rejected_updates : undefined;

  return (
    <section className="content-section flc" aria-labelledby="flc-title">
      <header className="flc__header">
        <div>
          <p className="flc__eyebrow">FL / SWARM ASSURANCE</p>
          <h2 id="flc-title"><ShieldCheck aria-hidden="true" /> Federated learning safeguards</h2>
          <p className="flc__summary">Privacy, update integrity, and participation policy for the enrolled swarm.</p>
        </div>
        <div className="flc__actions">
          {dirty && <button type="button" className="flc__revert" onClick={revert}>Revert edits</button>}
          <button type="button" className="flc__save" onClick={save} disabled={isSaving || !dirty}>
            {isSaving ? <RefreshCw aria-hidden="true" /> : <Database aria-hidden="true" />}
            {isSaving ? 'Saving safeguards' : dirty ? 'Save safeguards' : 'Safeguards saved'}
          </button>
        </div>
      </header>

      {evidenceWarning && <p className="mx-6 mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200" role="status">{evidenceWarning}</p>}

      <div className="flc__posture" aria-label="Federated learning posture">
        <Metric label="Protection layers" value={`${protectionEnabled} active`} tone={protectionEnabled >= 2 ? 'nominal' : 'warning'} />
        <Metric label="FL round" value={number(latest?.round, 0)} />
        <Metric label="Cumulative privacy ε" value={number(latest?.privacy_epsilon, 3)} tone={config.differential_privacy?.enabled ? 'nominal' : 'neutral'} />
        <Metric label="Mean swarm trust" value={number(latest?.mean_trust, 3)} tone={typeof latest?.mean_trust === 'number' && latest.mean_trust < 0.5 ? 'danger' : 'nominal'} />
        <Metric label="Rejected this round" value={rejected === undefined ? '—' : String(rejected)} tone={rejected ? 'warning' : 'nominal'} />
        <Metric label="Participating clients" value={activeClients === undefined ? '—' : String(activeClients)} />
      </div>

      <div className="flc__workspace">
        <div className="flc__policy">
          <div className="flc__policy-nav" role="tablist" aria-label="Federated learning policy areas">
            <button type="button" role="tab" aria-selected={section === 'safeguards'} className={section === 'safeguards' ? 'is-active' : ''} onClick={() => setSection('safeguards')}>
              <Shield aria-hidden="true" /> Safeguards
            </button>
            <button type="button" role="tab" aria-selected={section === 'federation'} className={section === 'federation' ? 'is-active' : ''} onClick={() => setSection('federation')}>
              <Layers aria-hidden="true" /> Federation policy
            </button>
            <span className="flc__save-state" role="status">
              {saveState === 'saved' ? 'Applied' : saveState === 'error' ? 'Save failed' : dirty ? 'Unsaved edits' : 'Live policy'}
            </span>
          </div>

          {section === 'safeguards' ? (
            <div className="flc-modules" role="tabpanel">
              <ControlModule
                icon={Shield}
                title="Differential privacy"
                description="Controls the privacy budget and server-side perturbation."
                enabled={Boolean(config.differential_privacy?.enabled)}
                onToggle={enabled => update('differential_privacy', { enabled })}
              >
                <RangeField id="flc-epsilon" label="Client ε budget" min={1} max={10} step={0.5} value={config.differential_privacy?.client_side?.epsilon ?? 5} display={number(config.differential_privacy?.client_side?.epsilon ?? 5, 1)} onChange={epsilon => update('differential_privacy', { client_side: { ...config.differential_privacy?.client_side, epsilon } })} />
                <RangeField id="flc-noise" label="Server noise scale" min={0} max={0.1} step={0.01} value={config.differential_privacy?.server_side?.noise_scale ?? 0.01} display={number(config.differential_privacy?.server_side?.noise_scale ?? 0.01, 2)} onChange={noise_scale => update('differential_privacy', { server_side: { ...config.differential_privacy?.server_side, noise_scale } })} />
              </ControlModule>

              <ControlModule
                icon={ShieldCheck}
                title="Reputation engine"
                description="Weights aggregation with historical client behavior."
                enabled={Boolean(config.trust?.enabled)}
                onToggle={enabled => update('trust', { enabled })}
              >
                <RangeField id="flc-trust-threshold" label="Historical rejection threshold" min={0.05} max={0.5} step={0.05} value={config.trust?.reject_trust_threshold ?? 0.15} display={number(config.trust?.reject_trust_threshold ?? 0.15, 2)} onChange={reject_trust_threshold => update('trust', { reject_trust_threshold })} />
              </ControlModule>

              <ControlModule
                icon={Cpu}
                title="Gradient compression"
                description="Reduces update payloads before aggregation."
                enabled={Boolean(config.compression?.enabled)}
                onToggle={enabled => update('compression', { enabled })}
              >
                <label className="flc-select-field">Compression strategy
                  <select value={config.compression?.strategy ?? 'topk'} onChange={event => update('compression', { strategy: event.target.value })}>
                    <option value="topk">Top-K sparsification</option>
                    <option value="quantize">INT8 quantization</option>
                    <option value="both">Sparsify &amp; quantize</option>
                    <option value="none">None (Float32)</option>
                  </select>
                  <ChevronDown aria-hidden="true" />
                </label>
                {config.compression?.strategy !== 'quantize' && <RangeField id="flc-sparsity" label="Top-K sparsity ratio" min={0.01} max={0.5} step={0.01} value={config.compression?.topk?.ratio ?? 0.1} display={percent(config.compression?.topk?.ratio ?? 0.1, 0)} onChange={ratio => update('compression', { topk: { ...config.compression?.topk, ratio } })} />}
              </ControlModule>
            </div>
          ) : (
            <div className="flc-modules" role="tabpanel">
              <ControlModule
                icon={BrainCircuit}
                title="pFedMe personalization"
                description="Keeps a controlled local adaptation path for each client."
                enabled={Boolean(config.personalization?.enabled)}
                onToggle={enabled => update('personalization', { enabled })}
              >
                <RangeField id="flc-lambda" label="Proximal regularization λ" min={0.01} max={1} step={0.05} value={config.personalization?.lambda_prox ?? 0.1} display={number(config.personalization?.lambda_prox ?? 0.1, 2)} onChange={lambda_prox => update('personalization', { lambda_prox })} />
              </ControlModule>

              <ControlModule
                icon={Layers}
                title="Swarm selection"
                description="Chooses which eligible clients participate in each round."
                enabled={Boolean(config.client_selection?.enabled)}
                onToggle={enabled => update('client_selection', { enabled })}
              >
                <label className="flc-select-field">Selection strategy
                  <select value={config.client_selection?.strategy ?? 'combined'} onChange={event => update('client_selection', { strategy: event.target.value })}>
                    <option value="poco">Power-of-Choice (Oort)</option>
                    <option value="ucb">UCB exploration / exploitation</option>
                    <option value="combined">Combined Oort + UCB</option>
                    <option value="random">Random sampling</option>
                  </select>
                  <ChevronDown aria-hidden="true" />
                </label>
              </ControlModule>

              <ControlModule
                icon={Activity}
                title="Asynchronous FL"
                description="Buffers updates when client completion times diverge."
                enabled={Boolean(config.async_fl?.enabled)}
                onToggle={enabled => update('async_fl', { enabled })}
              >
                <RangeField id="flc-buffer" label="FedBuff buffer size" min={2} max={10} step={1} value={config.async_fl?.buffer_size ?? 4} display={number(config.async_fl?.buffer_size ?? 4, 0)} onChange={buffer_size => update('async_fl', { buffer_size })} />
              </ControlModule>
            </div>
          )}
        </div>

        <aside className="flc__live" aria-label="Live federated learning evidence">
          <div className="flc__live-heading">
            <span><Activity aria-hidden="true" /> Federation evidence</span>
            <span className="flc__live-dot">Telemetry</span>
          </div>
          {latest ? (
            <>
              <div className="flc__live-grid">
                <Metric label="Threat classifier F1" value={number(latest.threat_f1, 4)} tone="nominal" />
                <Metric label="Compression ratio" value={typeof latest.compression_ratio === 'number' ? `${number(latest.compression_ratio, 1)}×` : '—'} />
                <Metric label="Selection fairness" value={number(latest.jains_fairness, 4)} />
                <Metric label="Client drift" value={percent(latest.drift_rate, 1)} tone={typeof latest.drift_rate === 'number' && latest.drift_rate > 0.25 ? 'warning' : 'neutral'} />
                <Metric label="Adaptive clip bound" value={number(latest.effective_clip_norm, 3)} />
                <Metric label="Sample claims capped" value={number(latest.sample_count_capped_clients, 0)} tone={typeof latest.sample_count_capped_clients === 'number' && latest.sample_count_capped_clients > 0 ? 'warning' : 'neutral'} />
              </div>
              <div className="flc__aggregation">
                <span>Aggregation method</span>
                <strong>{typeof latest.aggregation_method === 'string' ? latest.aggregation_method : '—'}</strong>
              </div>
            </>
          ) : (
            <div className="flc__empty"><SlidersHorizontal aria-hidden="true" /> {evidenceWarning || 'Awaiting federated metrics'}</div>
          )}
        </aside>
      </div>
    </section>
  );
}
