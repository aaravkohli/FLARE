import { useMemo, useState } from 'react';
import type { SwarmEvent, SwarmSnapshot } from './swarm';
import { title } from './swarm';
import { activityItems } from './world/workspacePresentation';
export default function ActivityPanel({snapshot,events,inspected}:{snapshot:SwarmSnapshot;events:SwarmEvent[];inspected:string}) {
 const [category,setCategory]=useState('ALL'),[selectedOnly,setSelectedOnly]=useState(false);
 const name=(id:string)=>snapshot.nodes.find(n=>n.id===id)?.alias??id;
 const items=useMemo(()=>activityItems(snapshot.session_id,events,snapshot.commands,id=>snapshot.nodes.find(n=>n.id===id)?.alias??id),[snapshot.session_id,events,snapshot.commands,snapshot.nodes]);
 const visible=items.filter(item=>(category==='ALL'||item.category===category)&&(!selectedOnly||item.targets.includes(inspected)));
 return <section className="workspace-activity" aria-label="Mission activity"><div className="activity-filters"><label>Category<select aria-label="Event category" value={category} onChange={e=>setCategory(e.target.value)}>{['ALL','NETWORK','SECURITY','SDN','COMMAND','FL'].map(id=><option key={id}>{id}</option>)}</select></label><label className="swarm-check"><input type="checkbox" checked={selectedOnly} onChange={e=>setSelectedOnly(e.target.checked)}/>Inspected UAV only</label><span>{visible.length} events</span></div>
 {snapshot.event_gap&&<p role="status">Earlier mission events are no longer retained.</p>}
 <ol className="activity-list">{visible.map(item=><li key={item.id}><details><summary><time>{item.time==null?'—':`T+${item.time.toFixed(3)}s`}</time><span className="activity-category">{item.category}</span><strong>{item.summary}</strong></summary>
 {item.command&&<div className="swarm-command-outcome">{Object.entries(item.command.outcomes).map(([id,outcome])=><p key={id}><strong>{name(id)} · {title(outcome.status)}</strong><span>{outcome.reason}{outcome.result?.reason?` · ${outcome.result.reason}`:''}</span></p>)}</div>}
 {item.command&&item.events.map(event=><p className="command-stage" key={event.cursor}><time>T+{event.simulation_time.toFixed(3)}s</time> {title(event.kind)}{event.data.node?` · ${name(String(event.data.node))}`:''}</p>)}
 <details className="activity-technical"><summary>Technical details</summary><pre>{JSON.stringify(item.command?{command:item.command,events:item.events}:item.events[0],null,2)}</pre></details>
 </details></li>)}</ol>{!visible.length&&<p className="swarm-note">No matching events in this session.</p>}</section>;
}
