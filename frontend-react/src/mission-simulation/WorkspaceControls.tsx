import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import { ChevronDown, X } from 'lucide-react';

export type CameraAction = 'fleet'|'full'|'selected'|'in'|'out'|'range'|'overview'|'tactical'|'follow'|'network'|'left'|'right';
export interface CanvasControls { camera:(action:CameraAction)=>void; quality?:(value:'high'|'balanced')=>void }
export function WorkspaceTabs<T extends string>({items,value,onChange,label}:{items:{id:T;label:string}[];value:T;onChange:(value:T)=>void;label:string}) {
 const prefix=useId(),group=label.toLowerCase().replaceAll(' ','-');
 return <div className="workspace-tabs" role="tablist" aria-label={label}>{items.map((item,index)=><button key={item.id} id={`${prefix}-${item.id}`} role="tab" aria-controls={`${group}-panel-${item.id}`} aria-selected={value===item.id} tabIndex={value===item.id?0:-1} onClick={()=>onChange(item.id)} onKeyDown={event=>{
  const key=event.key;if(!['ArrowLeft','ArrowRight','Home','End'].includes(key))return;event.preventDefault();
  const next=items[key==='Home'?0:key==='End'?items.length-1:(index+(key==='ArrowRight'?1:items.length-1))%items.length];onChange(next.id);document.getElementById(`${prefix}-${next.id}`)?.focus();
 }}>{item.label}</button>)}</div>;
}
export function ToolPopover({label,children,drawer=false,compactLabel}:{label:string;children:ReactNode;drawer?:boolean;compactLabel?:string}) {
 const [open,setOpen]=useState(false),host=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null),content=useRef<HTMLDivElement>(null),id=useId();
 useLayoutEffect(()=>{
  if(!open||drawer)return;
  const layout=()=>{
   const node=content.current,button=trigger.current;if(!node||!button)return;
   const bounds=button.getBoundingClientRect(),below=innerHeight-bounds.bottom-16,above=bounds.top-16,flip=below<180&&above>below;
   node.style.top=flip?'auto':'';node.style.bottom=flip?'calc(100% + 8px)':'';
   node.style.maxHeight=`${Math.max(80,Math.min(innerHeight*.65,flip?above:below))}px`;
  };
  layout();window.addEventListener('resize',layout);document.addEventListener('scroll',layout,true);
  return ()=>{window.removeEventListener('resize',layout);document.removeEventListener('scroll',layout,true);};
 },[open,drawer]);
 useEffect(()=>{
  if(!open)return;
  const previous=document.activeElement as HTMLElement|null;
  if(drawer)content.current?.querySelector<HTMLElement>('button,input,select')?.focus();
  const close=(event:PointerEvent)=>{if(!host.current?.contains(event.target as Node))setOpen(false);};
  const keys=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){event.stopPropagation();event.preventDefault();setOpen(false);trigger.current?.focus();}
   if(drawer&&event.key==='Tab'){
    const list=Array.from(content.current?.querySelectorAll<HTMLElement>('button,input,select,summary,textarea,[tabindex="0"]')??[]).filter(e=>!e.hasAttribute('disabled')&&e.getClientRects().length);
    const index=list.indexOf(document.activeElement as HTMLElement);
    if(event.shiftKey&&index<=0){event.preventDefault();list.at(-1)?.focus();}else if(!event.shiftKey&&(index<0||index===list.length-1)){event.preventDefault();list[0]?.focus();}
   }
  };
  document.addEventListener('pointerdown',close);document.addEventListener('keydown',keys,true);
  return ()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',keys,true);if(drawer&&previous?.isConnected)previous.focus();};
 },[open,drawer]);
 return <div ref={host} className={`workspace-popover${drawer?' workspace-drawer':''}`}>
  <button ref={trigger} aria-label={label} aria-expanded={open} aria-controls={id} onClick={()=>setOpen(!open)}>{compactLabel?<><span className="tool-full-label">{label}</span><span className="tool-compact-label">{compactLabel}</span></>:label}<ChevronDown size={13}/></button>
  {drawer&&open&&<div className="workspace-scrim" onClick={()=>setOpen(false)}/>}
  <div ref={content} id={id} role="dialog" aria-label={label} aria-modal={drawer?true:undefined} className="workspace-popover-content" hidden={!open}>
   <div className="popover-heading"><strong>{label}</strong><button aria-label={`Close ${label}`} onClick={()=>{setOpen(false);trigger.current?.focus();}}><X size={15}/></button></div>{children}
  </div>
 </div>;
}
export function CameraMenu({controls,world=false}:{controls:RefObject<CanvasControls|null>;world?:boolean}) {
 const items:{action:CameraAction;label:string}[]=[{action:'fleet',label:'Fit fleet'},{action:'full',label:'Fit full network'},{action:'selected',label:'Focus selected UAV'},...(world?[{action:'range',label:'Mission range'},{action:'overview',label:'Overview'},{action:'tactical',label:'Tactical'},{action:'follow',label:'Follow selected UAV'},{action:'network',label:'Network camera'}] as {action:CameraAction;label:string}[]:[]),{action:'in',label:'Zoom in'},{action:'out',label:'Zoom out'},...(world?[{action:'left',label:'Rotate camera left'},{action:'right',label:'Rotate camera right'}] as {action:CameraAction;label:string}[]:[])];
 return <ToolPopover label="Camera"><div className="workspace-menu">{items.map(item=><button key={item.action} onClick={()=>controls.current?.camera(item.action)}>{item.label}</button>)}</div></ToolPopover>;
}
