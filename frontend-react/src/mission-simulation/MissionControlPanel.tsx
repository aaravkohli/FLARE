import { useState } from 'react';
import { COMMANDS, ROLES, commandParameters } from './commandParameters';
import { FORMATIONS, title, selectAllMissionTargets, clearMissionTargets } from './swarm';
import type { SwarmSnapshot } from './swarm';
type Props = {snapshot:SwarmSnapshot; first:string; targets:string[]; busy:boolean; tab:'inspect'|'commands'; onTab:(tab:'inspect'|'commands')=>void;
 linkId:string; onLink:(id:string)=>void; select:(id:string,additive?:boolean)=>void; mutate:(path:string,body:Record<string,unknown>,method?:string)=>Promise<unknown>};
export default function MissionControlPanel({snapshot,first,targets,busy,tab,onTab,linkId,onLink,select,mutate}:Props) {
 const [query,setQuery]=useState(''),[command,setCommand]=useState('hold'),[error,setError]=useState('');
 const [fields,setFields]=useState<Record<string,string|boolean>>({altitude:'120',x:'0',y:'100',z:'0',points:'[[0,100,0],[100,100,100]]',loop:false,family:'mesh',role:'relay',formation:'v'});
 const drone=snapshot.nodes.find(n=>n.id===first),link=snapshot.links.find(l=>l.id===linkId);
 const linkOptions=snapshot.links.filter(l=>l.a===first||l.b===first);
 function send(){setError('');try{
  const parameters=commandParameters(command,fields);
  if(command==='formation'&&targets.length!==snapshot.nodes.length)throw new Error('Formation commands require the entire fleet. Choose Select all.');
  void mutate('/commands',{session_id:snapshot.session_id,command_id:crypto.randomUUID(),targets,action:command,parameters,ttl_s:10});
 }catch(reason){setError(reason instanceof Error?reason.message:'Invalid parameters');}}
 const field=(name:string,value:string|boolean)=>setFields(old=>({...old,[name]:value}));
 const visible=snapshot.nodes.filter(n=>`${n.alias} ${n.id} ${n.role}`.toLowerCase().includes(query.toLowerCase()));
 return <section className="mission-control-panel" aria-label="Central control">
  <div className="inspector-tabs" role="tablist" aria-label="UAV tools">{(['inspect','commands'] as const).map(id=><button key={id} role="tab" id={`uav-tab-${id}`} aria-selected={tab===id} tabIndex={tab===id?0:-1} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'inspect':e.key==='End'?'commands':id==='inspect'?'commands':'inspect';onTab(next);document.getElementById(`uav-tab-${next}`)?.focus();}}} aria-controls={`uav-panel-${id}`} onClick={()=>onTab(id)}>{id==='inspect'?'Inspect':'Commands'}</button>)}<span>{targets.length} selected</span></div>
  <div role="tabpanel" id="uav-panel-commands" aria-labelledby="uav-tab-commands" hidden={tab!=='commands'}>
   {error&&<p role="alert" className="swarm-error">{error}</p>}
    <input aria-label="Search fleet" placeholder="Search UAV, identity, role" value={query} onChange={e=>setQuery(e.target.value)}/>
    <div className="swarm-selection"><button onClick={selectAllMissionTargets}>Select all</button><button onClick={clearMissionTargets}>Clear</button></div>
    <div className="swarm-fleet" role="group" aria-label="Fleet selection">{visible.map(n=><button key={n.id} aria-pressed={targets.includes(n.id)} onClick={e=>select(n.id,e.shiftKey||e.ctrlKey||e.metaKey)}><span>{n.alias}</span><small>{n.available?n.role:'OFFLINE'} · {Math.round(n.battery*100)}%</small></button>)}</div>
    <label>Command<select aria-label="Command" value={command} onChange={e=>{setCommand(e.target.value);setError('');}}>{COMMANDS.map(n=><option key={n} value={n}>{n==='hold'?'Flight HOLD':title(n)}</option>)}</select></label>
    {command==='altitude'&&<label>Altitude (local m, 35–300)<input aria-label="Command altitude" type="number" min="35" max="300" value={String(fields.altitude)} onChange={e=>field('altitude',e.target.value)}/></label>}
    {command==='move'&&<div className="swarm-coordinate-fields">{['x','y','z'].map(k=><label key={k}>{k.toUpperCase()} (local m)<input aria-label={`Command ${k}`} type="number" min={k==='y'?35:-10000} max={k==='y'?300:10000} value={String(fields[k])} onChange={e=>field(k,e.target.value)}/></label>)}</div>}
    {command==='waypoints'&&<><label>Waypoints ([x,y,z], local m)<textarea aria-label="Command waypoints" value={String(fields.points)} onChange={e=>field('points',e.target.value)}/></label><label className="swarm-check"><input aria-label="Loop waypoints" type="checkbox" checked={fields.loop===true} onChange={e=>field('loop',e.target.checked)}/>Loop waypoints</label></>}
    {command==='route'&&<label>Route preference<select aria-label="Command route" value={String(fields.family)} onChange={e=>field('family',e.target.value)}>{['direct','satellite','mesh','hold'].map(n=><option key={n} value={n}>{n==='hold'?'Network HOLD':title(n)}</option>)}</select></label>}
    {command==='role'&&<label>Role<select aria-label="Command role" value={String(fields.role)} onChange={e=>field('role',e.target.value)}>{ROLES.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label>}
    {command==='formation'&&<><label>Fleet formation<select aria-label="Command formation" value={String(fields.formation)} onChange={e=>field('formation',e.target.value)}>{FORMATIONS.map(n=><option key={n} value={n}>{title(n)}</option>)}</select></label><p className="swarm-note">Select all for formation commands. Only receiving UAVs transition; failed deliveries keep their current formation.</p></>}
    <button className="swarm-primary" disabled={busy||!targets.length} onClick={send}>Send to {targets.length} UAV{targets.length===1?'':'s'}</button>
    {!snapshot.playing&&<p className="swarm-note">Paused flight commands remain queued: Waiting for mission to play. Management actions remain available.</p>}
    <button disabled={busy} onClick={()=>void mutate('/control',{action:snapshot.controller_connected?'disconnect_controller':'reconnect_controller'})}>{snapshot.controller_connected?'Disconnect command link':'Restore command link'}</button>
    <p className="swarm-note">Flight HOLD stops movement. Network HOLD suspends data forwarding. Route preferences remain safety constrained. During command-link loss, UAV networking continues autonomously; undelivered commands expire without replay.</p>

  </div>
  <div role="tabpanel" id="uav-panel-inspect" aria-labelledby="uav-tab-inspect" hidden={tab!=='inspect'}>
   <label>Inspected UAV<select aria-label="Inspected UAV" value={first} onChange={e=>select(e.target.value)}>{snapshot.nodes.map(n=><option key={n.id} value={n.id}>{n.alias}</option>)}</select></label>
   {drone&&<div className="swarm-detail"><h3>{drone.alias}<small>{drone.id} · {drone.available?'Online':'Offline'}</small></h3>
    {Object.entries({
     Network:{Role:snapshot.heads?.includes(drone.id)?snapshot.topology==='star'?'Leader':'Cluster head':title(drone.role),Neighbors:drone.neighbors.length,'Radio range':`${drone.range_m.toFixed(0)} m`,'Installed hops':drone.current_path.map(id=>snapshot.nodes.find(n=>n.id===id)?.alias??id).join(' → ')||'None','Sent / received':`${drone.sent} / ${drone.received}`,'Forwarded / dropped':`${drone.forwarded} / ${drone.dropped}`},
     Security:{Trust:drone.trust?.toFixed(2)??'—',Containment:title(drone.containment),'Model policy':drone.security?.policy_path??'Awaiting inference','Detector source':drone.security?.source??'Unavailable','Reported class':drone.security?.attack_type??'—','Traffic risk':drone.security?.insider_risk?.toFixed(2)??'—',FL:title(drone.fl_status)},
     Flight:{Mode:title(drone.mode),Position:drone.position.map(v=>v.toFixed(1)).join(', '),'Reported position':drone.reported_position?.map(v=>v.toFixed(1)).join(', ')??'—',Battery:`${Math.round(drone.battery*100)}%`,Health:`${Math.round(drone.health*100)}%`,'Current command':drone.current_command||'None'},
    }).map(([group,values])=><details key={group} open={group==='Network'}><summary>{group}</summary><dl>{Object.entries(values).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></details>)}
   </div>}
   <label>Inspect link<select aria-label="Inspect link" value={linkId} onChange={e=>onLink(e.target.value)}><option value="">Choose link</option>{linkOptions.map(l=><option key={l.id} value={l.id}>{l.a} ↔ {l.b}</option>)}</select></label>
   {link&&<dl className="inspected-link">{Object.entries({State:title(link.status),Distance:`${link.distance_m.toFixed(0)} m`,RSSI:`${link.rssi.toFixed(1)} dBm`,SINR:`${link.sinr.toFixed(1)} dB`,Loss:`${(link.loss*100).toFixed(1)}%`,Bandwidth:`${link.bandwidth_mbps.toFixed(2)} Mb/s`,Latency:`${link.latency_ms.toFixed(1)} ms`}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
  </div>
 </section>;
}
