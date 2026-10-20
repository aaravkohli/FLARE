import { useState, type FormEvent, type KeyboardEvent } from 'react';
import { AlertCircle, ArrowRight, Eye, EyeOff, LoaderCircle } from 'lucide-react';
import './sign-in.css';

type Props = {
  username: string;
  password: string;
  error: string | null;
  pending: boolean;
  onUsernameChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
};

function NetworkMark() {
  return <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M7 23 16 7l9 16H7Zm0 0 9-6 9 6M16 7v10" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    <circle cx="16" cy="7" r="3" fill="#14161a" stroke="currentColor" strokeWidth="1.5" />
    <circle cx="7" cy="23" r="3" fill="#14161a" stroke="currentColor" strokeWidth="1.5" />
    <circle cx="25" cy="23" r="3" fill="#14161a" stroke="currentColor" strokeWidth="1.5" />
  </svg>;
}

function MeshIllustration() {
  const nodes = [[52, 140], [158, 52], [252, 154], [380, 64], [452, 192], [308, 284], [98, 270]];
  return <figure className="login-network">
    <svg viewBox="0 0 520 330" fill="none" aria-hidden="true">
      <g stroke="#363d48" strokeWidth="1.1">
        <path d="m52 140 106-88 94 102L380 64l72 128-144 92-210-14-46-130Z" />
        <path d="m158 52 222 12M98 270l154-116 200 38M52 140l200 14 56 130M380 64l-72 220" />
      </g>
      <path d="m98 270 154-116L380 64" stroke="#89ade1" strokeWidth="1.8" />
      <circle cx="252" cy="154" r="38" stroke="#89ade1" strokeOpacity=".35" strokeDasharray="3 7" />
      {nodes.map(([x, y], index) => <g key={index} transform={`translate(${x} ${y})`}>
        <circle r="15" fill="#14161a" stroke={index === 2 ? '#a8c4ec' : '#525c6b'} />
        <path d="m-5-5 10 10m-10 0L5-5" stroke={index === 2 ? '#c2d5f1' : '#8795a8'} strokeWidth="1.4" />
        {[[-5, -5], [5, -5], [-5, 5], [5, 5]].map(([cx, cy], rotor) => <circle key={rotor} cx={cx} cy={cy} r="2.8" fill="#14161a" stroke={index === 2 ? '#c2d5f1' : '#8795a8'} strokeWidth="1.1" />)}
      </g>)}
    </svg>
    <figcaption>Illustrative mesh network</figcaption>
  </figure>;
}

export default function SignIn({ username, password, error, pending, onUsernameChange, onPasswordChange, onSubmit }: Props) {
  const [visible, setVisible] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const checkCapsLock = (event: KeyboardEvent<HTMLInputElement>) => setCapsLock(event.getModifierState('CapsLock'));
  const passwordDescription = [error ? 'login-error' : '', capsLock ? 'login-caps-lock' : ''].filter(Boolean).join(' ') || undefined;

  return <main className="app-shell login-shell">
    <header className="login-brand" aria-label="FLARE">
      <NetworkMark />
      <div><span className="login-wordmark">FLARE</span><span className="login-brand-description">UAV Network Resilience</span></div>
    </header>
    <div className="login-layout">
      <section className="login-intro" aria-label="About FLARE">
        <p className="login-product-title">UAV network<br />resilience.</p>
        <p className="login-product-description">Mission simulation, fleet operations, and security research in one workspace.</p>
        <MeshIllustration />
      </section>
      <section className="login-surface" aria-labelledby="login-title">
        <header className="login-header"><p className="login-eyebrow">Operator access</p><h1 id="login-title">Sign in to FLARE</h1></header>
        <form className="login-form" onSubmit={onSubmit} aria-busy={pending}>
          <div className="login-field">
            <label htmlFor="operator-id">Operator</label>
            <input id="operator-id" name="username" type="text" value={username} onChange={event => onUsernameChange(event.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Operator" aria-describedby={error ? 'login-error' : undefined} disabled={pending} required />
          </div>
          <div className="login-field">
            <label htmlFor="operator-password">Password</label>
            <div className="login-password-control">
              <input id="operator-password" name="password" type={visible ? 'text' : 'password'} value={password} onChange={event => onPasswordChange(event.target.value)} onKeyDown={checkCapsLock} onKeyUp={checkCapsLock} onBlur={() => setCapsLock(false)} autoComplete="current-password" autoCapitalize="none" spellCheck={false} placeholder="Password" aria-describedby={passwordDescription} disabled={pending} required />
              <button type="button" className="login-password-toggle" aria-label={visible ? 'Hide password' : 'Show password'} title={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} aria-controls="operator-password" onMouseDown={event => event.preventDefault()} onClick={() => setVisible(value => !value)} disabled={pending}>{visible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}</button>
            </div>
            {capsLock && <p className="login-caps-lock" id="login-caps-lock" role="status">Caps Lock is on.</p>}
          </div>
          {error && <div className="login-error" id="login-error" role="alert"><AlertCircle size={17} aria-hidden="true" /><span>{error}</span></div>}
          <button type="submit" className="login-submit" disabled={pending}><span>{pending ? 'Signing in…' : 'Sign in'}</span>{pending ? <LoaderCircle className="login-spinner" size={17} aria-hidden="true" /> : <ArrowRight size={17} aria-hidden="true" />}</button>
          <span className="login-announcement" role="status" aria-live="polite">{pending ? 'Signing in. Please wait.' : ''}</span>
        </form>
      </section>
    </div>
  </main>;
}
