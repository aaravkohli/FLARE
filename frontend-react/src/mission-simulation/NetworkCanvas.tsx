import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { Ref, RefObject } from 'react';
import type { CanvasControls, CameraAction } from './WorkspaceControls';
import { Crosshair, Minus, Plus } from 'lucide-react';
import type { NetworkCamera, NetworkDisplay, SwarmSnapshot } from './swarm';
import { edgePoint, playbackTime, samplePacketEvents } from './world/networkPlayback';
import { createFrameRateSampler } from './world/framePerformance';

type Props={snapshot:SwarmSnapshot;selected:string;targets?:string[];display:NetworkDisplay;current?:boolean;camera?:RefObject<NetworkCamera>;onSelect:(id:string,additive?:boolean)=>void;onLink?:(id:string)=>void;onGround?:()=>void;externalTools?:boolean;controls?:Ref<CanvasControls>};
const EMPTY_EVENTS:NonNullable<SwarmSnapshot['packet_events']>=[];
export default function NetworkCanvas(props:Props){
 const [groundOutside,setGroundOutside]=useState(false);
 const [hover,setHover]=useState<{id:string;x:number;y:number}|null>(null);
 const canvas=useRef<HTMLCanvasElement>(null),localCamera=useRef<NetworkCamera>({x:0,z:0,span:0});
 const installedPolicy=props.snapshot.flows.find(f=>f.source===props.selected&&f.destination==='base'&&f.traffic_class==='telemetry');
 const camera=props.camera??localCamera,latest=useRef(props),receipt=useRef({snapshot:props.snapshot,previous:props.snapshot,at:performance.now()});
 if(receipt.current.snapshot.session_id!==props.snapshot.session_id||receipt.current.snapshot.simulation_time!==props.snapshot.simulation_time||receipt.current.snapshot.playing!==props.snapshot.playing){receipt.current={previous:receipt.current.snapshot,snapshot:props.snapshot,at:performance.now()};}
 latest.current=props;
 function fit(scope:'fleet'|'full'=props.externalTools?'fleet':'full'){
  const snapshot=latest.current.snapshot,points=[...snapshot.nodes.map(n=>n.position),...(scope==='full'?[snapshot.base_position]:[])];
  if(!points.length)return;
  const xs=points.map(p=>p[0]),zs=points.map(p=>p[2]);
  const width=canvas.current?.clientWidth||600,height=canvas.current?.clientHeight||600,minimum=Math.min(width,height);
  camera.current={x:(Math.min(...xs)+Math.max(...xs))/2,z:(Math.min(...zs)+Math.max(...zs))/2,
   span:Math.max(props.externalTools?160:300,(Math.max(...xs)-Math.min(...xs))*minimum/width,(Math.max(...zs)-Math.min(...zs))*minimum/height)*1.2};
 }
 useImperativeHandle(props.controls,()=>({camera(action:CameraAction){
  if(action==='fleet'||action==='full')fit(action);
  else if(action==='selected'){const node=latest.current.snapshot.nodes.find(n=>n.id===latest.current.selected);if(node)camera.current={x:node.position[0],z:node.position[2],span:Math.min(camera.current.span||500,500)};}
  else if(action==='in')camera.current.span=Math.max(100,camera.current.span*.8);
  else if(action==='out')camera.current.span=Math.min(6000,camera.current.span*1.2);
 }}));
 useEffect(()=>{
  const element=canvas.current!,context=element.getContext('2d');if(!context)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');let frame=0,disposed=false;
  const sampler=createFrameRateSampler();let last=0;
  const profile=new URLSearchParams(location.search).has('profile')?{values:new Float64Array(72000),count:0}:null;
  if(profile)(element as HTMLCanvasElement & {__flareFrameTimes:typeof profile}).__flareFrameTimes=profile;
  let positions=new Map<string,{x:number;z:number}>();let fittedSession=latest.current.snapshot.session_id,fittedCount=latest.current.snapshot.nodes.length,wasOutside=false;
  function screen(x:number,z:number){const c=camera.current,scale=Math.min(element.clientWidth,element.clientHeight)/c.span;return {x:element.clientWidth/2+(x-c.x)*scale,y:element.clientHeight/2+(z-c.z)*scale};}
  function draw(now:number){
   if(disposed)return;
   if(document.hidden){frame=requestAnimationFrame(draw);return;}
   const p=latest.current,s=p.snapshot,dpr=Math.min(devicePixelRatio||1,1.5),w=element.clientWidth,h=element.clientHeight;
   if(!camera.current.span||fittedSession!==s.session_id||fittedCount!==s.nodes.length){fit();fittedSession=s.session_id;fittedCount=s.nodes.length;}
   if(element.width!==Math.round(w*dpr)||element.height!==Math.round(h*dpr)){element.width=Math.round(w*dpr);element.height=Math.round(h*dpr);}
   const ctx=context!;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
   ctx.fillStyle='#0b171e';ctx.fillRect(0,0,w,h);
   const scale=Math.min(w,h)/camera.current.span,rec=receipt.current;
   const mix=s.playing&&p.current!==false&&!reduced.matches?Math.min(1,(now-rec.at)/200):1;
   const past=new Map(rec.previous.session_id===s.session_id?rec.previous.nodes.map(n=>[n.id,n]):[]);
   positions=new Map(s.nodes.map(n=>{const old=past.get(n.id);return [n.id,{x:old?old.position[0]+(n.position[0]-old.position[0])*mix:n.position[0],z:old?old.position[2]+(n.position[2]-old.position[2])*mix:n.position[2]}];}));
   positions.set('base',{x:s.base_position[0],z:s.base_position[2]});
   const ground=screen(s.base_position[0],s.base_position[2]),outside=ground.x<25||ground.x>w-25||ground.y<25||ground.y>h-25;
   if(outside!==wasOutside){wasOutside=outside;setGroundOutside(outside);}
   const seconds=p.display.replay?.time??playbackTime(s.simulation_time,s.speed,now-rec.at,s.playing,p.current!==false);
   const packets=p.display.packets?samplePacketEvents(s.packet_events??EMPTY_EVENTS,seconds,p.selected,200,p.display.replay?.packetId):[];
   const active=new Set(packets.filter(v=>v.kind==='flight').map(v=>[v.from,v.to].sort().join(':')));
   const route=new Set(p.display.hops.slice(1).map((b,i)=>[p.display.hops[i],b].sort().join(':')));
   const selected=s.nodes.find(n=>n.id===p.selected),neighbors=new Set(selected?.neighbors??[]);
   const policy=s.flows.find(f=>f.source===p.selected&&f.destination==='base'&&f.traffic_class==='telemetry');
   const policyEdges=new Set(policy&&policy.family!=='satellite'?policy.hops.slice(1).map((b,i)=>[policy.hops[i],b].sort().join(':')):[]);
   ctx.strokeStyle='#1a2a32';ctx.lineWidth=1;
   const spacing=100*scale;
   if(spacing>12){
    const origin=screen(0,0);
    ctx.beginPath();for(let x=origin.x%spacing;x<w;x+=spacing){ctx.moveTo(x,0);ctx.lineTo(x,h);}for(let y=origin.y%spacing;y<h;y+=spacing){ctx.moveTo(0,y);ctx.lineTo(w,y);}ctx.stroke();
   }
   for(const region of s.interference_regions??[]){
    const center=screen(region.position[0],region.position[2]);ctx.beginPath();ctx.arc(center.x,center.y,region.radius_m*scale,0,Math.PI*2);ctx.fillStyle='#e4b77c0d';ctx.fill();ctx.strokeStyle='#d4a56788';ctx.setLineDash([7,6]);ctx.lineWidth=1.5;ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#c9a77d';ctx.font='9px monospace';ctx.fillText('INJECTED INTERFERENCE',center.x-56,center.y-region.radius_m*scale-8);
   }
   element.dataset.interferenceRegionCount=String(s.interference_regions?.length??0);
   if(p.display.ranges&&selected){const a=positions.get(selected.id)!;const center=screen(a.x,a.z);ctx.beginPath();ctx.arc(center.x,center.y,selected.range_m*scale,0,Math.PI*2);ctx.fillStyle='#91d4c407';ctx.fill();ctx.strokeStyle='#91d4c450';ctx.setLineDash([5,6]);ctx.stroke();ctx.setLineDash([]);}
   function line(aId:string,bId:string,color:string,opacity:number,width:number,dashed=false){
    const a=positions.get(aId),b=positions.get(bId);if(!a||!b)return;
    const from=screen(a.x,a.z),to=screen(b.x,b.z),control=edgePoint(a,b,.5,aId,bId),c=screen(control.cx,control.cz);
    ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dashed?[5,5]:[]);
    ctx.beginPath();ctx.moveTo(from.x,from.y);ctx.quadraticCurveTo(c.x,c.y,to.x,to.y);ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;
   }
   for(const l of s.links){
    const chosen=route.has(l.id),policyLink=policyEdges.has(l.id),busy=active.has(l.id),near=l.a===p.selected||l.b===p.selected;
    if(!p.display.inactive&&!chosen&&!policyLink&&!busy&&!near)continue;
    const blocked=l.status==='blocked',degraded=['jammed','degraded'].includes(l.status);
    line(l.a,l.b,chosen?(p.display.candidate?'#b5a1ea':'#99e1bc'):policyLink?'#88bfec':blocked?'#d4877b':degraded?'#e4b77c':busy?'#8ddce8':'#688895',chosen||policyLink?.95:near?.65:busy?.55:s.nodes.length>50?.12:.22,chosen?2.7:policyLink?1.8:Math.max(.6,1.7*(1-l.loss)),blocked||chosen&&!!p.display.candidate);
   }
   if(p.display.inactive)for(const l of s.disconnected_links??[])line(l.a,l.b,'#d4877b',.25,1,true);
   const satPackets=packets.filter(v=>v.packet.family==='satellite'&&(v.packet.source===p.selected||p.display.replay?.packetId===v.packet.id));
   if(policy?.family==='satellite'||satPackets.length){
    const x=Math.max(12,w-250),y=w<560?116:72;ctx.fillStyle='#142a35ed';ctx.fillRect(x,y,230,56);ctx.strokeStyle='#3b5b70';ctx.strokeRect(x,y,230,56);
    ctx.fillStyle='#9ac7e8';ctx.font='9px monospace';ctx.fillText('SATELLITE · MODELED TRANSPORT',x+12,y+17);
    ctx.strokeStyle='#507f9e';ctx.beginPath();ctx.moveTo(x+18,y+37);ctx.lineTo(x+212,y+37);ctx.stroke();
    ctx.fillStyle='#96b1bd';ctx.font='8px monospace';ctx.fillText(s.nodes.find(n=>n.id===(satPackets[0]?.packet.source??policy?.source))?.alias??'UAV',x+12,y+50);ctx.fillText('SAT',x+104,y+50);ctx.fillText('BASE',x+195,y+50);
    for(const v of satPackets){ctx.fillStyle=v.kind==='dropped'?'#e4988a':v.kind==='retry'?'#e7be79':'#abd9f1';ctx.beginPath();ctx.arc(x+18+(reduced.matches?.5:v.progress)*194,y+37,v.kind==='flight'?3:6,0,Math.PI*2);if(v.kind==='flight')ctx.fill();else ctx.stroke();}
   }
   for(const v of packets){
    if(v.packet.family==='satellite')continue;
    const a=positions.get(v.from),b=positions.get(v.to);if(!a||!b)continue;
    const point=edgePoint(a,b,reduced.matches?.5:v.progress,v.from,v.to),pos=screen(point.x,point.z);
    ctx.fillStyle=v.kind==='dropped'?'#e4988a':v.kind==='retry'?'#e7be79':v.packet.category==='fl_update'?'#baa4eb':v.packet.category==='command'||v.packet.category==='ack'?'#efd69a':'#9fe1eb';
    if(v.kind==='flight'){
     const next=edgePoint(a,b,Math.min(1,v.progress+.02),v.from,v.to),direction=Math.atan2(next.z-point.z,next.x-point.x);
     ctx.save();ctx.translate(pos.x,pos.y);ctx.rotate(direction);ctx.beginPath();ctx.moveTo(5,0);ctx.lineTo(-3,-3);ctx.lineTo(-3,3);ctx.closePath();ctx.fill();ctx.restore();
    }else{ctx.beginPath();ctx.arc(pos.x,pos.y,10,0,Math.PI*2);ctx.strokeStyle=ctx.fillStyle;ctx.lineWidth=1.5;ctx.stroke();if(v.kind==='dropped'){ctx.font='12px monospace';ctx.fillText('×',pos.x-4,pos.y+4);}}
   }
   ctx.font='11px Inter, sans-serif';const occupied:{x:number;y:number}[]=[];
   for(const n of s.nodes){
    const world=positions.get(n.id)!,pos=screen(world.x,world.z),chosen=n.id===p.selected,target=p.targets?.includes(n.id),near=neighbors.has(n.id);
    const color=n.containment==='quarantined'?'#dc8e78':!n.available?'#65727b':n.containment!=='normal'?'#e4b77c':chosen?'#a4e6c9':'#c6d9e0';
    if(pos.x<-30||pos.y<-30||pos.x>w+30||pos.y>h+30)continue;
    if(chosen||target||near){ctx.beginPath();ctx.arc(pos.x,pos.y,chosen?17:14,0,Math.PI*2);ctx.fillStyle=chosen?'#92d6bf12':'#92d6bf05';ctx.fill();ctx.strokeStyle=target?'#efd69a':chosen?'#9ce1c1':'#536d72';ctx.lineWidth=chosen?1.5:1;ctx.stroke();}
    ctx.save();ctx.translate(pos.x,pos.y);ctx.rotate(n.heading);ctx.strokeStyle=color;ctx.lineWidth=1.3;ctx.beginPath();ctx.moveTo(-5,-5);ctx.lineTo(5,5);ctx.moveTo(-5,5);ctx.lineTo(5,-5);ctx.stroke();
    for(const [x,y] of [[-6,-6],[-6,6],[6,-6],[6,6]]){ctx.beginPath();ctx.arc(x,y,2.8,0,Math.PI*2);ctx.stroke();}ctx.fillStyle=color;ctx.fillRect(-2,-3,4,6);ctx.restore();
    if(chosen||near||s.nodes.length<=30){
     const label={x:pos.x+13,y:pos.y-12};if(chosen||!occupied.some(p=>Math.abs(p.x-label.x)<65&&Math.abs(p.y-label.y)<18)){
      occupied.push(label);ctx.fillStyle=chosen?'#c5eee1':'#9fb4bd';ctx.fillText(n.alias,label.x,label.y);
     }
    }
    if(s.heads?.includes(n.id)){ctx.fillStyle='#e1c58e';ctx.font='9px monospace';ctx.fillText(s.topology==='star'?'L':'H',pos.x-3,pos.y-20);ctx.font='11px Inter, sans-serif';}
    const hop=p.display.hops.indexOf(n.id);if(hop>=0){ctx.fillStyle=p.display.candidate?'#b5a1ea':'#99e1bc';ctx.font='10px monospace';ctx.fillText(String(hop+1),pos.x-20,pos.y+3);ctx.font='11px Inter, sans-serif';}
   }
   if(s.links.some(l=>l.a==='base'||l.b==='base')){const b=screen(s.base_position[0],s.base_position[2]);ctx.fillStyle='#efd69a';ctx.fillRect(b.x-5,b.y-5,10,10);ctx.fillText('GROUND',b.x+12,b.y+4);}
   const scaleMetres=[1,2,5,10,20,50,100,200,500,1000].filter(value=>value*scale<=Math.min(w*.2,120)).at(-1)??1,scaleLeft=w-scaleMetres*scale-20;
   ctx.strokeStyle='#8ba3ac';ctx.fillStyle='#8ba3ac';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(scaleLeft,h-24);ctx.lineTo(w-20,h-24);ctx.stroke();ctx.font='10px monospace';ctx.fillText(`${scaleMetres} m`,scaleLeft,h-34);
   element.dataset.packetVisual='authoritative-packet-events';element.dataset.packetCount=String(packets.filter(v=>v.packet.family!=='satellite').length+satPackets.length);
   const report=sampler.record(now,s.playing&&p.current!==false);if(report){element.dataset.fpsAverage=String(report.averageFps);element.dataset.fpsLow=String(report.lowFps);}
   if(profile&&last&&s.playing&&profile.count<profile.values.length)profile.values[profile.count++]=now-last;last=now;
   frame=requestAnimationFrame(draw);
  }
  let drag:{x:number;y:number;startX:number;startY:number;moved:boolean}|null=null;
  function down(e:PointerEvent){drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};element.setPointerCapture(e.pointerId);}
  function move(e:PointerEvent){
   if(!drag){const bounds=element.getBoundingClientRect();let nearest:string|undefined,distance=20;for(const [id,p] of positions){const point=screen(p.x,p.z),d=Math.hypot(point.x-(e.clientX-bounds.left),point.y-(e.clientY-bounds.top));if(id!=='base'&&d<distance){nearest=id;distance=d;}}const id=nearest;setHover(old=>old?.id===id?old:id?{id,x:Math.min(element.clientWidth-185,Math.max(10,e.clientX-bounds.left+15)),y:Math.max(50,e.clientY-bounds.top-50)}:null);return;}setHover(null);const scale=Math.min(element.clientWidth,element.clientHeight)/camera.current.span;camera.current.x-=(e.clientX-drag.x)/scale;camera.current.z-=(e.clientY-drag.y)/scale;drag.moved ||= Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>4;drag.x=e.clientX;drag.y=e.clientY;}
  function up(e:PointerEvent){
   if(drag&&!drag.moved){const bounds=element.getBoundingClientRect();let nearest:string|undefined,distance=24;
    for(const [id,p] of positions){const point=screen(p.x,p.z),d=Math.hypot(point.x-(e.clientX-bounds.left),point.y-(e.clientY-bounds.top));if(d<distance&&(id!=='base'||latest.current.onGround)){nearest=id;distance=d;}}
    if(nearest==='base')latest.current.onGround?.();
    else if(nearest)latest.current.onSelect(nearest,e.shiftKey||e.ctrlKey||e.metaKey);
    else if(latest.current.onLink){let best:string|undefined,gap=7;for(const link of latest.current.snapshot.links){const a=positions.get(link.a),b=positions.get(link.b);if(!a||!b)continue;for(let step=1;step<12;step++){const point=edgePoint(a,b,step/12,link.a,link.b),pos=screen(point.x,point.z),d=Math.hypot(pos.x-(e.clientX-bounds.left),pos.y-(e.clientY-bounds.top));if(d<gap){best=link.id;gap=d;}}}if(best)latest.current.onLink(best);} 
   }drag=null;
  }
  function cancel(){drag=null;}
  function wheel(e:WheelEvent){e.preventDefault();camera.current.span=Math.max(100,Math.min(6000,camera.current.span*Math.exp(e.deltaY*.001)));}
  element.addEventListener('pointerdown',down);element.addEventListener('pointermove',move);element.addEventListener('pointerup',up);element.addEventListener('pointercancel',cancel);element.addEventListener('wheel',wheel,{passive:false});
  frame=requestAnimationFrame(draw);
  return ()=>{disposed=true;cancelAnimationFrame(frame);element.removeEventListener('pointerdown',down);element.removeEventListener('pointermove',move);element.removeEventListener('pointerup',up);element.removeEventListener('wheel',wheel);element.removeEventListener('pointercancel',cancel);};
 // Camera is a shared mutable controller so A/B pans do not rerender either scene.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[camera]);
 return <div className="network-canvas-wrap"><canvas ref={canvas} aria-label="Authoritative 2D mission network" role="img" tabIndex={0} onKeyDown={e=>{if(e.key==='+'||e.key==='=')camera.current.span*=.8;else if(e.key==='-')camera.current.span*=1.2;else if(e.key==='f')fit();else if(e.key==='Enter'){e.preventDefault();const nodes=props.snapshot.nodes,index=nodes.findIndex(n=>n.id===props.selected);props.onSelect(nodes[(index+1)%nodes.length].id,e.shiftKey);}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const delta=camera.current.span*.05;camera.current.x+=e.key==='ArrowLeft'?-delta:e.key==='ArrowRight'?delta:0;camera.current.z+=e.key==='ArrowUp'?-delta:e.key==='ArrowDown'?delta:0;}}}/>
  {!props.externalTools&&<div className="network-camera-controls"><button aria-label="Zoom in" onClick={()=>camera.current.span*=.8}><Plus size={16}/></button><button aria-label="Zoom out" onClick={()=>camera.current.span*=1.2}><Minus size={16}/></button><button aria-label="Fit network" onClick={()=>fit()}><Crosshair size={16}/></button></div>}
  {props.externalTools&&groundOutside&&<button className="ground-edge-locator" onClick={()=>fit('full')}>Ground control ↗</button>}
  {props.snapshot.routing_mode!=='mesh_reference'&&<div className="network-policy-badge" aria-label="Installed FLARE route"><strong>{installedPolicy?.family.toUpperCase()??'NO INSTALLED ROUTE'}</strong><span>{props.snapshot.nodes.find(n=>n.id===props.selected)?.security?.routing_source==='rl_model'?'DQN decision':props.snapshot.nodes.find(n=>n.id===props.selected)?.security?.routing_source??'Local routing'} · {installedPolicy?'confirmed telemetry policy':'awaiting telemetry policy'}</span></div>}

  {hover&&<div className="network-tooltip" role="tooltip" style={{left:hover.x,top:hover.y}}><strong>{props.snapshot.nodes.find(n=>n.id===hover.id)?.alias}</strong><span>{props.snapshot.nodes.find(n=>n.id===hover.id)?.neighbors.length} neighbors · {props.snapshot.nodes.find(n=>n.id===hover.id)?.containment}</span><small>{hover.id}</small></div>}
  <span className="network-view-label">LOCAL AIRSPACE <span>2D · metres</span></span>
 </div>;
}
