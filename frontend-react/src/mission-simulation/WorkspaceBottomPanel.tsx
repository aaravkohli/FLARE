import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { WorkspaceTabs } from './WorkspaceControls';
export type BottomTab='telemetry'|'decisions'|'activity';
export default function WorkspaceBottomPanel({tab,onTab,children}:{tab:BottomTab;onTab:(tab:BottomTab)=>void;children:ReactNode}) {
 const [height,setHeight]=useState(220),[collapsed,setCollapsed]=useState(false),drag=useRef<{y:number;height:number}|null>(null),panel=useRef<HTMLElement>(null);
 const [maxHeight,setMaxHeight]=useState(440);
 useEffect(()=>{
  const node=panel.current,root=node?.closest('.swarm-center'),page=node?.parentElement;if(!root||!page)return;
  const observer=new ResizeObserver(()=>{
   const chrome=Array.from(page.children).filter(child=>child!==node&&!child.classList.contains('swarm-layout')).reduce((sum,child)=>sum+(child as HTMLElement).offsetHeight,0);
   const limit=Math.max(160,Math.min(root.clientHeight*.45,page.clientHeight-chrome-240));setMaxHeight(limit);setHeight(current=>Math.min(current,limit));
  });observer.observe(root);observer.observe(page);return ()=>observer.disconnect();
 },[]);
 const maximum=()=>maxHeight;
 const resize=(value:number)=>setHeight(Math.max(160,Math.min(maximum(),value)));
 return <section ref={panel} className={`workspace-bottom-panel${collapsed?' is-collapsed':''}`} style={{height:collapsed?44:height,maxHeight:collapsed?44:maxHeight}} aria-label="Research details">
 <div className="bottom-resize" role="separator" tabIndex={0} aria-label="Bottom panel height" aria-orientation="horizontal" aria-valuemin={160} aria-valuemax={Math.round(maximum())} aria-valuenow={height} onKeyDown={e=>{if(['ArrowUp','ArrowDown','Home','End'].includes(e.key)){e.preventDefault();resize(e.key==='Home'?160:e.key==='End'?maximum():height+(e.key==='ArrowUp'?20:-20));}}} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);drag.current={y:e.clientY,height};}} onPointerMove={e=>{if(drag.current)resize(drag.current.height+drag.current.y-e.clientY);}} onPointerUp={()=>{drag.current=null;}} onLostPointerCapture={()=>{drag.current=null;}}/>
 <div className="bottom-panel-toolbar"><WorkspaceTabs label="Research details" value={tab} onChange={next=>{onTab(next);setCollapsed(false);}} items={[{id:'telemetry',label:'Telemetry'},{id:'decisions',label:'Decisions'},{id:'activity',label:'Activity'}]}/><button aria-label={collapsed?'Expand bottom panel':'Collapse bottom panel'} aria-expanded={!collapsed} onClick={()=>setCollapsed(!collapsed)}>{collapsed?<ChevronUp size={16}/>:<ChevronDown size={16}/>}</button></div>
 <div className="bottom-panel-content" hidden={collapsed}>{children}</div>
 </section>;
}
