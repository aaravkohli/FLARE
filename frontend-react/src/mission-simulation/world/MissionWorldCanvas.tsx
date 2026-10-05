import type { SwarmSnapshot } from '../swarm';
import { createDrone } from './dynamics';
import { RotateCcw, RotateCw, Plus, Minus } from 'lucide-react';
import { placeLabel, compactDroneId, craftMarkerBox, worldDroneLabel } from './labels';
import type { LabelBox } from './labels';
import { selectWorldObject } from './selection';
import { threatQueuePath, threatVisual } from './threatState';
import { networkRouteState } from './networkState';
import { packetFlowEnabled } from './PacketStream';
import { requestedAnnotation, routeAppearance } from './routeVisual';
import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { MissionFleetDrone, MissionSimulationState } from '../types';
import { createMissionScene } from './createMissionScene';
import { frameClock, resetClock } from './clock';
import type { VisualClock } from './clock';
import { createFrameRateSampler, FRAME_RATE_WINDOW_MS } from './framePerformance';
import { createAutoQualityDecision } from './renderQuality';
import type { RenderQuality } from './renderQuality';
import { angleDifference, reconcileFleet, STEP } from './dynamics';
import { interpolateDrone } from './projection';
import { cameraPreset, clampCamera, dampCamera } from './cameraRig';
import type { CameraMode, NetworkFrame } from './cameraRig';
import { MISSION_SATELLITE_ORBIT, satelliteOrbitAt } from './satelliteOrbit';
import { satelliteWorldPosition } from './satelliteWorldPosition';
import { TERRAIN_DISCLOSURE, TERRAIN_PROVENANCE } from './terrain';

export interface MissionWorldProps {
  state: MissionSimulationState; drones: MissionFleetDrone[]; selectedDroneId: string; paused: boolean;
  /** Phase 9 may supply a receipt-freshness decision; omitted means current for existing callers. */
  evidenceCurrent?: boolean;
  mission?: SwarmSnapshot;
  selectedDroneIds?: string[];
  worldClock: RefObject<VisualClock>; onSelectDrone: (id: string, additive?: boolean) => void;
  onSelectAsset: (asset: 'satellite' | 'base' | 'jammer') => void; onFallback: () => void; onDismiss: () => void;
}
// Marker markup is independent of packet, motion and inference snapshots.
// Its live position/selection text is updated by the frame loop below.
const FleetOverlays = memo(function FleetOverlays({drones,markers,labels}:{
  drones:MissionFleetDrone[];markers:RefObject<Map<string,HTMLSpanElement>>;labels:RefObject<Map<string,HTMLSpanElement>>;
}) {
  return <>
    <div className="mission-world-craft-markers" aria-hidden="true">
      {drones.map(drone=><span key={drone.drone_id} ref={node=>{if(node) markers.current.set(drone.drone_id,node);else markers.current.delete(drone.drone_id);}}>
        <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path d="M6 6l12 12M18 6 6 18" className="mission-craft-arms" />
          <circle cx="5" cy="5" r="2.6" /><circle cx="19" cy="5" r="2.6" />
          <circle cx="5" cy="19" r="2.6" /><circle cx="19" cy="19" r="2.6" />
          <path d="M10 10.5 12 7.5l2 3v5.5l-2 1.5-2-1.5z" className="mission-craft-body" />
        </svg>
      </span>)}
    </div>
    <div className="mission-world-labels" aria-hidden="true">
      {[...drones.map((d,index)=>({id:d.drone_id,label:compactDroneId(d.drone_id,index)})),{id:'base',label:'GROUND CONTROL'},{id:'satellite',label:'SAT-01'},{id:'reported-position',label:'REPORTED GPS ◇ · SCHEMATIC'}].map(asset=><span key={asset.id} ref={node=>{if(node) labels.current.set(asset.id,node);else labels.current.delete(asset.id);}}>{asset.label}</span>)}
    </div>
  </>;
},(before,after)=>before.markers===after.markers&&before.labels===after.labels&&before.drones.length===after.drones.length&&before.drones.every((d,index)=>d.drone_id===after.drones[index].drone_id));
export default function MissionWorldCanvas(props: MissionWorldProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(props);
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const craftMarkers = useRef(new Map<string, HTMLSpanElement>());
  const requestedRouteAnnotation = useRef<HTMLSpanElement>(null);
  const queueAnnotation = useRef<HTMLSpanElement>(null);
  const uplinkAnnotation = useRef<HTMLSpanElement>(null);
  const downlinkAnnotation = useRef<HTMLSpanElement>(null);
  const hovered = useRef<string | undefined>(undefined);
  const alternatives = useRef(false);
  const wake = useRef<() => void>(() => {});
  const applyQuality = useRef<(quality: RenderQuality) => void>(() => {});
  const qualityRef = useRef<RenderQuality>(props.mission ? 'balanced' : 'high');
  const manualHighOverride = useRef(false);
  const desired = useRef(cameraPreset('overview'));
  const modeRef = useRef<CameraMode | 'manual'>('overview');
  const networkFrame = useRef<NetworkFrame>('unknown');
  const [mode, setMode] = useState<CameraMode | 'manual'>('overview');
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [quality, setQuality] = useState<RenderQuality>(props.mission ? 'balanced' : 'high');
  const missionReceipt=useRef({previous:props.mission,current:props.mission,at:0});
  useLayoutEffect(() => {
    if(props.mission!==missionReceipt.current.current) missionReceipt.current={previous:missionReceipt.current.current,current:props.mission,at:performance.now()};
    latest.current = props; wake.current();
  });
  function installedFrame(): NetworkFrame {
    const route = props.state.droneId === props.selectedDroneId ? props.state.installedRoute : null;
    return route === 'direct' || route === 'mesh' || route === 'satellite' ? route : 'unknown';
  }
  function choose(next: CameraMode) {
    modeRef.current = next; setMode(next);
    if (next === 'network') networkFrame.current = installedFrame();
    desired.current = cameraPreset(next,
      props.worldClock.current.world.drones.find(d=>d.id===props.selectedDroneId), networkFrame.current);
    wake.current();
  }
  function manual() { modeRef.current = 'manual'; setMode('manual'); }
  function adjust(yaw: number, zoom: number) {
    manual();
    desired.current = clampCamera({ ...desired.current, yaw: desired.current.yaw + yaw, span: desired.current.span * zoom }); wake.current();
  }
  function toggleAlternatives() {
    alternatives.current = !alternatives.current;
    setShowAlternatives(alternatives.current);
    wake.current();
  }
  function chooseQuality(next: RenderQuality) {
    qualityRef.current = next;
    manualHighOverride.current = next === 'high';
    setQuality(next);
    applyQuality.current(next);
    wake.current();
  }
  useEffect(() => {
    const node = canvas.current!;
    let scene: ReturnType<typeof createMissionScene>;
    try { scene = createMissionScene(node); } catch { latest.current.onFallback(); return; }
    scene.setQuality(qualityRef.current);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let fleetInput: MissionFleetDrone[] | undefined;
    const before = new Map<string, VisualClock['world']['drones'][number]>();
    let frame = 0, previous = 0, rig = cameraPreset('overview'), disposed = false;
    let cameraSelection = latest.current.selectedDroneId;
    const clock = latest.current.worldClock;
    const profiling=new URLSearchParams(location.search).has('profile');
    const profile=profiling?{values:new Float64Array(72000),count:0}:null;
    if(profile)(node as HTMLCanvasElement & {__flareFrameTimes:typeof profile}).__flareFrameTimes=profile;
    let lastProfileFrame=0;
    const frameRate = createFrameRateSampler();
    const autoQuality = createAutoQualityDecision();
    node.dataset.fpsLowBasis = '5th-percentile-frame-fps';
    node.dataset.fpsWindowMs = String(FRAME_RATE_WINDOW_MS);
    function clearFrameRate() {
      delete node.dataset.fpsAverage;
      delete node.dataset.fpsLow;
      delete node.dataset.fpsSamples;
    }
    const frozen = () => latest.current.paused || reduced.matches || document.hidden;
    function animate(now: number) {
      frame = 0; if (disposed || document.hidden) return;
      const props = latest.current;
      if (fleetInput !== props.drones) {
        fleetInput = props.drones;
        const world = reconcileFleet(clock.current.world, props.drones.map(d=>d.drone_id));
        if (world !== clock.current.world) clock.current = { ...clock.current, world, previous: world };
      }
      if(!props.mission) clock.current = frameClock(clock.current, now, frozen());
      const c = clock.current;
      before.clear(); for (const drone of c.previous.drones) before.set(drone.id, drone);
      const receipt=missionReceipt.current;
      const mix=props.mission?.playing && !reduced.matches ? Math.min(1,Math.max(0,(now-receipt.at)/200)) : 1;
      const past=new Map(receipt.previous?.nodes.map(n=>[n.id,n]));
      const drones = props.mission ? props.mission.nodes.map(n=>{
        const old=past.get(n.id); const d={...(before.get(n.id)||createDrone(n.id))};
        d.position={x:n.position[0],y:n.position[1],z:n.position[2]};
        if(old && receipt.previous?.session_id===props.mission?.session_id) d.position={x:old.position[0]+(n.position[0]-old.position[0])*mix,y:old.position[1]+(n.position[1]-old.position[1])*mix,z:old.position[2]+(n.position[2]-old.position[2])*mix};
        d.heading=n.heading; return d;
      }) : c.world.drones.map(d=>interpolateDrone(before.get(d.id)||d,d,c.accumulator/STEP));
      if(props.mission){
        c.world={drones,tick:Math.floor(props.mission.simulation_time*20)};
        // Interpolate modeled packet/orbit timing between authoritative frames.
        // Counters, outcomes and the mission clock remain backend-owned.
        c.simulationSeconds=props.mission.simulation_time+(props.mission.playing&&!reduced.matches?Math.min(.2,Math.max(0,(now-receipt.at)/1000))*props.mission.speed:0);
        if(modeRef.current==='overview'){
          const extent=Math.max(360,...drones.map(d=>Math.hypot(d.position.x,d.position.z)));
          desired.current={...cameraPreset('overview'),span:Math.min(6000,extent*2.6)};
        }
      }
      const selected = drones.find(d=>d.id===props.selectedDroneId);
      if (cameraSelection !== props.selectedDroneId) {
        cameraSelection = props.selectedDroneId;
        if (modeRef.current === 'network') {
          const route = props.state.droneId === props.selectedDroneId ? props.state.installedRoute : null;
          networkFrame.current = route === 'direct' || route === 'mesh' || route === 'satellite' ? route : 'unknown';
          desired.current = cameraPreset('network', selected, networkFrame.current);
        }
      }
      if (modeRef.current === 'follow' && !selected) {
        modeRef.current = 'overview'; setMode('overview'); desired.current = cameraPreset('overview');
      } else if (selected && (modeRef.current === 'follow' || modeRef.current === 'tactical' ||
        modeRef.current === 'network' && networkFrame.current === 'direct')) {
        desired.current = cameraPreset(modeRef.current, selected, networkFrame.current);
      }
      const dt = previous ? (now-previous)/1000 : 1/60; previous = now;
      rig = dampCamera(rig, desired.current, dt, reduced.matches, props.mission?6000:1350);
      const orbit = satelliteOrbitAt(MISSION_SATELLITE_ORBIT, MISSION_SATELLITE_ORBIT.config.epochSeconds+c.simulationSeconds);
      const rendered = scene.draw(drones, props.selectedDroneId, satelliteWorldPosition(orbit.direction), rig, props.state, c.simulationSeconds, reduced.matches, hovered.current, alternatives.current, props.evidenceCurrent !== false, props.mission, props.selectedDroneIds);
      const eligibleFrame = rendered && !frozen();
      if(profile&&eligibleFrame){
        if(lastProfileFrame && profile.count<profile.values.length)profile.values[profile.count++]=now-lastProfileFrame;
        lastProfileFrame=now;
      }else lastProfileFrame=0;
      const frameReport = frameRate.record(now, eligibleFrame);
      if (!eligibleFrame) { clearFrameRate(); autoQuality.reset(); }
      else if (frameReport) {
        node.dataset.fpsAverage = frameReport.averageFps.toFixed(1);
        node.dataset.fpsLow = frameReport.lowFps.toFixed(1);
        node.dataset.fpsSamples = String(frameReport.sampleCount);
        if (autoQuality.observe(now, frameReport.averageFps, qualityRef.current, manualHighOverride.current)) {
          qualityRef.current = 'balanced';
          scene.setQuality('balanced');
          setQuality('balanced');
          frameRate.reset();
          clearFrameRate();
        }
      }
      const occupied: LabelBox[] = [];
      const viewportWidth=node.clientWidth, viewportHeight=node.clientHeight;
      for (const [id, marker] of craftMarkers.current) {
        const point=scene.screenPoint(id);
        const selected=id===props.selectedDroneId, hover=id===hovered.current && !selected;
        marker.dataset.selected=String(selected);
        marker.dataset.hovered=String(hover);
        marker.style.display=point?.visible?'':'none';
        if (!point?.visible) continue;
        const box=craftMarkerBox(point.x,point.y,selected,hover);
        marker.style.width=`${box.width}px`;
        marker.style.height=`${box.height}px`;
        marker.style.transform=`translate(${box.x}px, ${box.y}px)`;
        occupied.push(box);
      }
      const directStatus=networkRouteState(props.state,'direct',props.selectedDroneId);
      const satelliteStatus=networkRouteState(props.state,'satellite',props.selectedDroneId);
      function placeRouteAnnotation(node: HTMLSpanElement | null, text: string, route: 'direct' | 'satellite' | undefined, progress: number) {
        if (!node) return;
        const point=route?scene.routeScreenPoint(route,progress):null;
        const width=text.length*6.2+14, height=21;
        const box=point?.visible?placeLabel(point.x,point.y,width,height,viewportWidth,viewportHeight,occupied):null;
        node.style.display=box?'':'none';
        if (box) {
          if (node.textContent!==text) node.textContent=text;
          node.style.transform=`translate(${box.x}px, ${box.y}px)`;
          occupied.push(box);
        }
      }
      const requested=directStatus.candidate?'direct':satelliteStatus.candidate?'satellite':undefined;
      placeRouteAnnotation(requestedRouteAnnotation.current,
        requestedAnnotation(!!props.state.sdnError), requested, requested==='satellite'?.22:.46);
      const currentPath = props.evidenceCurrent === false ? null
        : directStatus.forwarding ? 'direct' : satelliteStatus.forwarding ? 'satellite' : null;
      const queuePath = threatQueuePath(threatVisual(props.state,props.selectedDroneId),currentPath);
      placeRouteAnnotation(queueAnnotation.current,'ILLUSTRATIVE QUEUE',queuePath ?? undefined,.82);
      placeRouteAnnotation(uplinkAnnotation.current,'UPLINK',satelliteStatus.installed?'satellite':undefined,.24);
      placeRouteAnnotation(downlinkAnnotation.current,'DOWNLINK',satelliteStatus.installed?'satellite':undefined,.73);
      const priority=(id:string)=>id===props.selectedDroneId?3:id===hovered.current||id==='reported-position'?2:id==='base'||id==='satellite'?1:0;
      const orderedLabels=[...labels.current].sort(([a],[b])=>priority(b)-priority(a));
      for(const [id,label] of orderedLabels) {
        const drone=props.drones.find(d=>d.drone_id===id);
        const selected=id===props.selectedDroneId;
        const hover=id===hovered.current && !selected;
        if(props.mission && props.drones.length>30 && drone && !selected && !hover){label.style.display='none';continue;}
        const motion=drone?drones.find(item=>item.id===id):undefined;
        const text=drone ? worldDroneLabel(
          hover?drone.display_name:props.mission?.nodes.find(n=>n.id===id)?.alias||compactDroneId(id,props.drones.indexOf(drone)),
          motion?.position.y ?? 0, selected, hover,
          selected && props.state.droneId===id ? props.state.installedRoute : null,
        ) :
          id==='satellite'?'SAT-01':id==='base'?'GROUND CONTROL':'REPORTED GPS ◇ · SCHEMATIC';
        if(label.textContent!==text) label.textContent=text;
        label.dataset.selected=String(selected);
        label.dataset.hovered=String(hover);
        label.dataset.altitude=String(!!drone && (selected || hover));
        label.dataset.threat=String(id==='reported-position');
        const point=scene.screenPoint(id);
        // Conservative text bounds avoid synchronous DOM measurement in the frame loop.
        const lines=text.split('\n');
        const width=Math.max(...lines.map(line=>line.length))*6.8+16;
        const height=lines.length>1?42:22;
        const box=point?.visible?placeLabel(point.x,point.y,width,height,viewportWidth,viewportHeight,occupied,selected||id==='reported-position'):null;
        label.style.display=box?'':'none';
        if(box){occupied.push(box);label.style.transform=`translate(${box.x}px, ${box.y}px)`;}
      }
      const goal = clampCamera(desired.current,props.mission?6000:1350);
      const unsettled = Math.abs(angleDifference(rig.yaw,goal.yaw))+Math.abs(rig.elevation-goal.elevation)+Math.abs(rig.span-goal.span)+Math.hypot(rig.target.x-goal.target.x,rig.target.y-goal.target.y,rig.target.z-goal.target.z) > .01;
      if (!frozen() || unsettled) frame = requestAnimationFrame(animate);
    }
    function request() { if (!frame && !disposed && !document.hidden) frame=requestAnimationFrame(animate); }
    function reset() { clock.current=resetClock(clock.current); frameRate.reset(); autoQuality.reset(); clearFrameRate(); previous=0; cancelAnimationFrame(frame); frame=0; request(); }
    wake.current = request;
    applyQuality.current = next => { scene.setQuality(next); frameRate.reset(); autoQuality.reset(); clearFrameRate(); };
    const resize = new ResizeObserver(() => { frameRate.reset(); autoQuality.reset(); clearFrameRate(); request(); }); resize.observe(node);
    document.addEventListener('visibilitychange',reset); reduced.addEventListener('change',reset);
    const lost = (event: Event) => { event.preventDefault(); latest.current.onFallback(); };
    node.addEventListener('webglcontextlost',lost);
    let drag: {x:number;y:number;moved:boolean} | null = null;
    const down = (e: PointerEvent) => { if (e.button!==0) return; drag={x:e.clientX,y:e.clientY,moved:false}; node.setPointerCapture(e.pointerId); };
    const move = (e: PointerEvent) => { if (!drag) {
      const rect=node.getBoundingClientRect();
      const id=scene.pick((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);
      if(hovered.current!==id){hovered.current=id;node.style.cursor=id?'pointer':'grab';request();}
      return;
    } const dx=e.clientX-drag.x,dy=e.clientY-drag.y; if(Math.abs(dx)+Math.abs(dy)>2) { if (!drag.moved) manual(); drag.moved=true; }
      desired.current=clampCamera({...desired.current,yaw:desired.current.yaw-dx*.004,elevation:desired.current.elevation+dy*.004}); drag.x=e.clientX;drag.y=e.clientY;request(); };
    const up = (e: PointerEvent) => { if(drag && !drag.moved) { const rect=node.getBoundingClientRect(); const id=scene.pick((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);
      if(!id) latest.current.onDismiss();
      selectWorldObject(id, latest.current.drones.map(d=>d.drone_id), id=>latest.current.onSelectDrone(id,e.shiftKey||e.ctrlKey||e.metaKey), latest.current.onSelectAsset); } drag=null; };
    const cancel = () => { drag=null; };
    const leave = () => { hovered.current=undefined;request(); };
    node.addEventListener('pointerleave',leave);
    node.addEventListener('pointerdown',down);node.addEventListener('pointermove',move);node.addEventListener('pointerup',up);node.addEventListener('pointercancel',cancel);
    reset();
    return () => { disposed=true;cancelAnimationFrame(frame);resize.disconnect();wake.current=()=>{};applyQuality.current=()=>{};clock.current=resetClock(clock.current);
      document.removeEventListener('visibilitychange',reset);reduced.removeEventListener('change',reset);node.removeEventListener('webglcontextlost',lost);
      node.removeEventListener('pointerleave',leave);node.removeEventListener('pointerdown',down);node.removeEventListener('pointermove',move);node.removeEventListener('pointerup',up);node.removeEventListener('pointercancel',cancel);scene.dispose(); };
  }, []);
  const threat = threatVisual(props.state, props.selectedDroneId);
  const directStatus = networkRouteState(props.state, 'direct', props.selectedDroneId);
  const satelliteStatus = networkRouteState(props.state, 'satellite', props.selectedDroneId);
  const satelliteAvailable = props.state.routes.find(route => route.id === 'satellite')?.available ?? false;
  const satelliteVisible = routeAppearance(satelliteStatus,
    props.state.droneId === props.selectedDroneId, satelliteAvailable, showAlternatives) !== 'hidden';
  const holdVisible = props.state.droneId === props.selectedDroneId && props.state.installedRoute === 'hold';
  const flowVisible = props.state.sdnApplied !== false
    && packetFlowEnabled(directStatus.forwarding || satelliteStatus.forwarding, props.evidenceCurrent !== false);
  const forwardingPath = !flowVisible ? null : directStatus.forwarding ? 'direct' : 'satellite';
  const threatSceneNote = threat.profile === 'dos' && !threatQueuePath(threat, forwardingPath)
    ? 'DOS REPORTED · QUEUE UNAVAILABLE\nNO CONFIRMED FORWARDING PATH' : threat.sceneNote;
  return <div className="mission-world" data-camera-mode={mode} data-quality={quality}>
    <div className="mission-camera-controls" role="group" aria-label="Mission camera">
      <button type="button" aria-pressed={mode==='overview'} onClick={()=>choose('overview')}>Overview</button>
      <button type="button" aria-pressed={mode==='tactical'} onClick={()=>choose('tactical')}>Tactical</button>
      <button type="button" aria-pressed={mode==='follow'} disabled={!props.drones.some(d=>d.drone_id===props.selectedDroneId)} onClick={()=>choose('follow')}>Follow {props.mission?.nodes.find(n=>n.id===props.selectedDroneId)?.alias||compactDroneId(props.selectedDroneId,props.drones.findIndex(d=>d.drone_id===props.selectedDroneId))}</button>
      <button type="button" aria-pressed={mode==='network'} onClick={()=>choose('network')}>Network camera</button>
      <button type="button" onClick={()=>choose('overview')}>Reset camera</button>
      <button type="button" aria-label="Rotate camera left" onClick={()=>adjust(-.15,1)}><RotateCcw size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Rotate camera right" onClick={()=>adjust(.15,1)}><RotateCw size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Zoom in" onClick={()=>adjust(0,.85)}><Plus size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Zoom out" onClick={()=>adjust(0,1.15)}><Minus size={16} aria-hidden="true" /></button>
      <label className="mission-quality-control">Quality
        <select aria-label="Mission visual quality" title="Balanced lowers render resolution and shadow detail; simulation timing and routes are unchanged" value={quality} onChange={event=>chooseQuality(event.target.value as RenderQuality)}>
          <option value="high">High</option>
          <option value="balanced">Balanced</option>
        </select>
      </label>
      <button type="button" onClick={props.onFallback}>2D view</button>
      <span className="mission-terrain-source" title={TERRAIN_DISCLOSURE}>
        {TERRAIN_PROVENANCE === 'usgs-3dep'
          ? 'USGS 3DEP core · simulated local positions'
          : 'Representative mission terrain · simulated local positions'}
      </span>
    </div>
    <div className="mission-world-viewport"><canvas ref={canvas} className="mission-world-canvas" aria-label="Simulated mission terrain, UAV patrols, satellite and ground station. Use the fleet and camera buttons to interact." role="img" />
      <FleetOverlays drones={props.drones} markers={craftMarkers} labels={labels}/>
      <div className="mission-world-route-annotations" aria-hidden="true">
        <span ref={requestedRouteAnnotation} data-kind="requested">REQUESTED</span>
        <span ref={queueAnnotation} data-kind="queue">ILLUSTRATIVE QUEUE</span>
        <span ref={uplinkAnnotation}>UPLINK</span>
        <span ref={downlinkAnnotation}>DOWNLINK</span>
      </div>
      {(holdVisible || satelliteVisible || flowVisible || threat.profile!=='none') && <div className="mission-world-scene-notes">
        {holdVisible && <span className="mission-world-hold-note" aria-hidden="true">HOLD · FORWARDING SUSPENDED</span>}
        {threat.profile!=='none' && <span className="mission-world-threat-note">{threatSceneNote}</span>}
        {satelliteVisible && <span className="mission-world-orbit-note">ORBIT DISPLAY NOT TO SCALE</span>}
        {flowVisible && <span className="mission-world-flow-note">Metric-derived flow · not packet capture</span>}
      </div>}
    </div>
    <div className="mission-world-assets" role="group" aria-label="Mission assets and network display">
      <button type="button" onClick={()=>props.onSelectAsset('base')}>Ground control</button>
      <button type="button" onClick={()=>props.onSelectAsset('satellite')}>Satellite</button>
      {threat.profile!=='none' && <button type="button" onClick={()=>props.onSelectAsset('jammer')}>Threat details · {props.state.activeAttack.replaceAll('_',' ')}</button>}
      <button type="button" aria-pressed={showAlternatives} onClick={toggleAlternatives}>Alternative routes</button>
    </div>
    {!props.mission&&<div className="mission-world-fleet" role="group" aria-label="Select mission drone">
      {props.drones.map(d=><button type="button" key={d.drone_id} aria-pressed={d.drone_id===props.selectedDroneId} onClick={()=>props.onSelectDrone(d.drone_id)}>{d.drone_id.replaceAll('_',' ').replace(/^drone/, 'Drone')}</button>)}
    </div>}
    {threat.profile!=='none' && <div className="mission-threat-caption" role="status">
      {threat.label}
      {threat.paths.length>0 && <span> · Affected: {threat.paths.join(', ')}</span>}
    </div>}
    {props.state.droneId===props.selectedDroneId && (props.state.installedRoute==='hold' || props.state.installedRoute==='mesh' || props.state.requestedRoute && props.state.requestedRoute!==props.state.installedRoute) && <div className="mission-network-caption" role="status">
      {props.state.installedRoute==='hold' && <span className="mission-network-caption__hold">HOLD · forwarding suspended</span>}
      {props.state.installedRoute==='mesh' && <span>{props.mission?`Installed mesh hops: ${props.mission.nodes.find(n=>n.id===props.selectedDroneId)?.current_path.join(' → ')||'suspended'}`:'Mesh neighborhood · forwarding hops unavailable'}</span>}
      {props.state.requestedRoute && props.state.requestedRoute !== props.state.installedRoute && <span>Requested: {props.state.requestedRoute}{props.state.sdnError ? ' · update failed' : ' · not confirmed installed'}</span>}

    </div>}
    <div className="mission-world-footer"><span>{props.mission?`Authoritative packet simulation · ${props.mission.speed}×`:'Visual simulation · 1×'}</span>
    <details className="mission-world-notes"><summary>Simulation scope</summary><p>{props.mission?'Backend terrain, motion, radio links and packet forwarding are modeled. Installed mesh hops and packet events come from the simulator. Encryption indicators describe simulated policy. No RF calibration, real OpenFlow forwarding or UAV field validation is implied.':<>{TERRAIN_DISCLOSURE}. Screen-size UAV symbols aid identification and do not show aircraft dimensions. SIM ALT labels show local simulation altitude, not AGL or GPS. The DEM does not drive flight or RF calculations. Orbital scale is compressed. Alternative routes are optional schematic context; mesh lines show a neighborhood and forwarding hops are unavailable. Packet pulses visualize reported latency and loss, not captured packets or measured traffic volume. Threat fields show the injected affected area around the selected UAV ground projection, never an attacker location. GPS marker direction and spacing are schematic because there is no trusted position reference. DoS queue marks are fixed illustrative glyphs, not measured queue depth.</>}</p></details>
    </div>
  </div>;
}
