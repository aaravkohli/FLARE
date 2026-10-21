import { useMemo, useState, type ReactNode } from 'react';
import {
  Activity,
  BrainCircuit,
  ChevronDown,
  Cpu,
  Layers,
  Shield,
  ShieldCheck,
} from 'lucide-react';
import './secure-fl-control-center.css';

type FlConfig = Record<string, any>;

interface SecureFlControlCenterProps {
  config: FlConfig;
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

export default function SecureFlControlCenter({ config, isSaving, onConfigChange, onSave }: SecureFlControlCenterProps) {
  const [saveState, setSaveState] = useState<'idle' | 'saved' | 'error'>('idle');
  const serialized = useMemo(() => JSON.stringify(config), [config]);
  const [baseline, setBaseline] = useState(() => JSON.stringify(config));

  const dirty = baseline !== serialized;

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

  return <section className="flc application-policy" aria-label="Federated learning safeguards">
    <div className="policy-actions"><span role="status">{saveState === 'saved' ? 'Saved · restart FL services' : saveState === 'error' ? 'Save failed. Edits retained.' : dirty ? 'Unsaved edits' : 'Saved configuration'}</span><div>{dirty && <button type="button" onClick={revert} disabled={isSaving}>Revert edits</button>}<button className="primary" type="button" onClick={save} disabled={isSaving || !dirty}>{isSaving ? 'Saving…' : 'Save safeguards'}</button></div></div>
    <p className="swarm-note">Saved settings apply after restarting the FL server and clients. Status switches show configuration, not running-service confirmation.</p>
    <details className="policy-group" open><summary>Safeguards</summary><div className="flc-modules">
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
                description="Simulates compression error. Flower updates still use dense transport."
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
</div></details>
    <details className="policy-group" open><summary>Federation configuration</summary><div className="flc-modules">
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
                description="Research setting. Live missions rotate through eligible clients."
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
                description="Research setting. Live federation currently runs synchronous rounds."
                enabled={Boolean(config.async_fl?.enabled)}
                onToggle={enabled => update('async_fl', { enabled })}
              >
                <RangeField id="flc-buffer" label="FedBuff buffer size" min={2} max={10} step={1} value={config.async_fl?.buffer_size ?? 4} display={number(config.async_fl?.buffer_size ?? 4, 0)} onChange={buffer_size => update('async_fl', { buffer_size })} />
              </ControlModule>
</div></details>
  </section>;
}
