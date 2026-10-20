import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FileText, List, LockKeyhole, Menu, X } from 'lucide-react';
import { destinations, title, type Destination } from './presentation';

export function ApplicationHeader({ page, navigate, online, reporting, onReport, onActivity, onLock }: { page: Destination; navigate: (page: Destination) => void; online: string; reporting: boolean; onReport: () => void; onActivity: () => void; onLock: () => void }) {
  const [menu, setMenu] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const header = useRef<HTMLElement>(null);
  useEffect(() => {if(!menu)return;const dismiss=(event:PointerEvent)=>{if(!header.current?.contains(event.target as Node))setMenu(false);};document.addEventListener('pointerdown',dismiss);return () => document.removeEventListener('pointerdown',dismiss);},[menu]);
  useEffect(() => { const close = () => setMenu(false); window.addEventListener('hashchange', close); return () => window.removeEventListener('hashchange', close); }, []);
  return <header ref={header} className="application-header" onKeyDown={e => {if(e.key === "Escape" && menu){e.preventDefault();setMenu(false);trigger.current?.focus();}}}>
    <a className="skip-link" href="#application-content" onClick={e => { e.preventDefault(); document.querySelector<HTMLElement>('.application-page:not([hidden])')?.focus(); }}>Skip to content</a>
    <a href="#overview" className="application-wordmark" aria-label="FLARE overview">FLARE</a>
    <button ref={trigger} className="application-menu-button" aria-label="Navigation" aria-expanded={menu} aria-controls="application-navigation" onClick={() => setMenu(!menu)}><Menu size={18} /></button>
    <nav id="application-navigation" aria-label="Application" className={menu ? 'is-open' : ''}>
      {destinations.map(item => <a key={item} href={`#${item}`} aria-current={page === item ? 'page' : undefined} onClick={e => { e.preventDefault(); navigate(item); setMenu(false); }}>{title(item)}</a>)}
    </nav>
    <span className={`application-connection ${online === 'connected' ? 'is-connected' : ''}`}>{title(online)}</span>
    <div className="application-header-actions"><button onClick={onReport} disabled={reporting} title="Export report" aria-label="Export report"><FileText size={16} /><span>{reporting ? 'Exporting…' : 'Export report'}</span></button><button onClick={onActivity} title="Activity" aria-label="Activity"><List size={17} /><span>Activity</span></button><button onClick={onLock} title="Lock" aria-label="Lock"><LockKeyhole size={16} /><span>Lock</span></button></div>
  </header>;
}
export function Drawer({ open, active = true, title, onClose, children }: { open: boolean; active?: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    if (open && active && !element.open) element.showModal();
    else if ((!open || !active) && element.open) element.close();
    return () => { if (element.open) element.close(); };
  }, [open, active]);
  return <dialog ref={dialog} className="application-drawer" aria-label={title} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === dialog.current) onClose(); }}>
    <div className="application-drawer-body"><header><h2>{title}</h2><button aria-label={`Close ${title}`} onClick={onClose}><X size={18} /></button></header><div className="application-drawer-content">{children}</div></div>
  </dialog>;
}
export function SectionTabs<T extends string>({ label, value, items, onChange }: { label: string; value: T; items: { id: T; label: string }[]; onChange: (value: T) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return <div role="tablist" aria-label={label} className="application-tabs" onKeyDown={e => {
    const index = items.findIndex(item => item.id === value);
    const next = e.key === 'ArrowRight' ? (index + 1) % items.length : e.key === 'ArrowLeft' ? (index - 1 + items.length) % items.length : e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : -1;
    if (next >= 0) { e.preventDefault(); onChange(items[next].id); refs.current[next]?.focus(); }
  }}>{items.map((item, index) => <button key={item.id} ref={element => { refs.current[index] = element; }} role="tab" id={`${label}-${item.id}-tab`} aria-controls={`${label}-${item.id}-panel`} tabIndex={value === item.id ? 0 : -1} aria-selected={value === item.id} onClick={() => onChange(item.id)}>{item.label}</button>)}</div>;
}
