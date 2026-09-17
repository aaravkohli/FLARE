import { RotateCcw, RotateCw, Plus, Minus } from 'lucide-react';
import { placeLabel, compactDroneId } from './labels';
import type { LabelBox } from './labels';
import { selectWorldObject } from './selection';
import { threatVisual } from './threatState';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { MissionFleetDrone, MissionSimulationState } from '../types';
import { createMissionScene } from './createMissionScene';
import { frameClock, resetClock } from './clock';
import type { VisualClock } from './clock';
import { reconcileFleet, STEP } from './dynamics';
import { interpolateDrone } from './projection';
import { cameraPreset, clampCamera, dampCamera } from './cameraRig';
import type { CameraMode } from './cameraRig';
import { MISSION_SATELLITE_ORBIT, satelliteOrbitAt } from './satelliteOrbit';
import { satelliteWorldPosition } from './satelliteWorldPosition';

export interface MissionWorldProps {
  state: MissionSimulationState; drones: MissionFleetDrone[]; selectedDroneId: string; paused: boolean;
  worldClock: RefObject<VisualClock>; onSelectDrone: (id: string) => void;
  onSelectAsset: (asset: 'satellite' | 'base' | 'jammer') => void; onFallback: () => void; onDismiss: () => void;
}
export default function MissionWorldCanvas(props: MissionWorldProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(props);
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const hovered = useRef<string | undefined>(undefined);
  const wake = useRef<() => void>(() => {});
  const desired = useRef(cameraPreset('tactical'));
  const modeRef = useRef<CameraMode>('tactical');
  const [mode, setMode] = useState<CameraMode | 'manual'>('tactical');
  useLayoutEffect(() => { latest.current = props; wake.current(); });
  function choose(next: CameraMode) {
    modeRef.current = next; setMode(next);
    desired.current = cameraPreset(next, props.worldClock.current.world.drones.find(d=>d.id===props.selectedDroneId)?.position);
    wake.current();
  }
  function manual() { if (modeRef.current !== 'follow') setMode('manual'); }
  function adjust(yaw: number, zoom: number) {
    manual();
    desired.current = clampCamera({ ...desired.current, yaw: desired.current.yaw + yaw, span: desired.current.span * zoom }); wake.current();
  }
  useEffect(() => {
    const node = canvas.current!;
    let scene: ReturnType<typeof createMissionScene>;
    try { scene = createMissionScene(node); } catch { latest.current.onFallback(); return; }
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let fleetInput: MissionFleetDrone[] | undefined;
    const before = new Map<string, VisualClock['world']['drones'][number]>();
    let frame = 0, previous = 0, rig = cameraPreset('tactical'), disposed = false;
    const clock = latest.current.worldClock;
    const frozen = () => latest.current.paused || reduced.matches || document.hidden;
    function animate(now: number) {
      frame = 0; if (disposed || document.hidden) return;
      const props = latest.current;
      if (fleetInput !== props.drones) {
        fleetInput = props.drones;
        const world = reconcileFleet(clock.current.world, props.drones.map(d=>d.drone_id));
        if (world !== clock.current.world) clock.current = { ...clock.current, world, previous: world };
      }
      clock.current = frameClock(clock.current, now, frozen());
      const c = clock.current;
      before.clear(); for (const drone of c.previous.drones) before.set(drone.id, drone);
      const drones = c.world.drones.map(d=>interpolateDrone(before.get(d.id)||d,d,c.accumulator/STEP));
      const selected = drones.find(d=>d.id===props.selectedDroneId);
      if (modeRef.current === 'follow') desired.current = { ...desired.current, target: selected ? { ...selected.position } : cameraPreset('tactical').target };
      const dt = previous ? (now-previous)/1000 : 1/60; previous = now;
      rig = dampCamera(rig, desired.current, dt, reduced.matches);
      const orbit = satelliteOrbitAt(MISSION_SATELLITE_ORBIT, MISSION_SATELLITE_ORBIT.config.epochSeconds+c.simulationSeconds);
      scene.draw(drones, props.selectedDroneId, satelliteWorldPosition(orbit.direction), rig, props.state, c.simulationSeconds, reduced.matches);
      const occupied: LabelBox[] = [];
      const viewportWidth=node.clientWidth, viewportHeight=node.clientHeight;
      const priority=(id:string)=>id===props.selectedDroneId?3:id===hovered.current?2:id==='base'||id==='satellite'?1:0;
      const orderedLabels=[...labels.current].sort(([a],[b])=>priority(b)-priority(a));
      for(const [id,label] of orderedLabels) {
        const drone=props.drones.find(d=>d.drone_id===id);
        const selected=id===props.selectedDroneId;
        const text=drone ? (id===hovered.current?drone.display_name:compactDroneId(id,props.drones.indexOf(drone)))+
          (selected?' · '+(props.state.droneId===id?props.state.installedRoute??'Unknown':'Unknown'):'') :
          id==='satellite'?'SAT-01':id==='base'?'GROUND CONTROL':'REPORTED OFFSET';
        if(label.textContent!==text) label.textContent=text;
        label.dataset.selected=String(selected);
        const point=scene.screenPoint(id);
        // Conservative text bounds avoid synchronous DOM measurement in the frame loop.
        const width=text.length*6.8+12, height=22;
        const box=point?.visible?placeLabel(point.x,point.y,width,height,viewportWidth,viewportHeight,occupied):null;
        label.style.display=box?'':'none';
        if(box){occupied.push(box);label.style.transform=`translate(${box.x}px, ${box.y}px)`;}
      }
      const goal = clampCamera(desired.current);
      const unsettled = Math.abs(rig.yaw-goal.yaw)+Math.abs(rig.elevation-goal.elevation)+Math.abs(rig.span-goal.span)+Math.hypot(rig.target.x-goal.target.x,rig.target.y-goal.target.y,rig.target.z-goal.target.z) > .01;
      if (!frozen() || unsettled) frame = requestAnimationFrame(animate);
    }
    function request() { if (!frame && !disposed && !document.hidden) frame=requestAnimationFrame(animate); }
    function reset() { clock.current=resetClock(clock.current); previous=0; cancelAnimationFrame(frame); frame=0; request(); }
    wake.current = request;
    const resize = new ResizeObserver(request); resize.observe(node);
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
      selectWorldObject(id, latest.current.drones.map(d=>d.drone_id), latest.current.onSelectDrone, latest.current.onSelectAsset); } drag=null; };
    const cancel = () => { drag=null; };
    const leave = () => { hovered.current=undefined;request(); };
    node.addEventListener('pointerleave',leave);
    node.addEventListener('pointerdown',down);node.addEventListener('pointermove',move);node.addEventListener('pointerup',up);node.addEventListener('pointercancel',cancel);
    reset();
    return () => { disposed=true;cancelAnimationFrame(frame);resize.disconnect();wake.current=()=>{};clock.current=resetClock(clock.current);
      document.removeEventListener('visibilitychange',reset);reduced.removeEventListener('change',reset);node.removeEventListener('webglcontextlost',lost);
      node.removeEventListener('pointerleave',leave);node.removeEventListener('pointerdown',down);node.removeEventListener('pointermove',move);node.removeEventListener('pointerup',up);node.removeEventListener('pointercancel',cancel);scene.dispose(); };
  }, []);
  const threat = threatVisual(props.state, props.selectedDroneId);
  return <div className="mission-world" data-camera-mode={mode}>
    <div className="mission-camera-controls" role="group" aria-label="Mission camera">
      <button type="button" aria-pressed={mode==='overview'} onClick={()=>choose('overview')}>Overview</button>
      <button type="button" aria-pressed={mode==='tactical'} onClick={()=>choose('tactical')}>Tactical</button>
      <button type="button" aria-pressed={mode==='follow'} disabled={!props.drones.some(d=>d.drone_id===props.selectedDroneId)} onClick={()=>choose('follow')}>Follow {compactDroneId(props.selectedDroneId,props.drones.findIndex(d=>d.drone_id===props.selectedDroneId))}</button>
      <button type="button" onClick={()=>choose('tactical')}>Reset camera</button>
      <button type="button" aria-label="Rotate camera left" onClick={()=>adjust(-.15,1)}><RotateCcw size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Rotate camera right" onClick={()=>adjust(.15,1)}><RotateCw size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Zoom in" onClick={()=>adjust(0,.85)}><Plus size={16} aria-hidden="true" /></button>
      <button type="button" aria-label="Zoom out" onClick={()=>adjust(0,1.15)}><Minus size={16} aria-hidden="true" /></button>
      <button type="button" onClick={props.onFallback}>2D view</button>
    </div>
    <div className="mission-world-viewport"><canvas ref={canvas} className="mission-world-canvas" aria-label="Simulated mission terrain, UAV patrols, satellite and ground station. Use the fleet and camera buttons to interact." role="img" />
      <div className="mission-world-labels" aria-hidden="true">
        {[...props.drones.map(d=>({id:d.drone_id,label:compactDroneId(d.drone_id,props.drones.indexOf(d))})),{id:'base',label:'GROUND CONTROL'},{id:'satellite',label:'SAT-01'},{id:'reported-position',label:'REPORTED OFFSET · SCHEMATIC'}].map(asset=><span key={asset.id} ref={node=>{if(node) labels.current.set(asset.id,node);else labels.current.delete(asset.id);}}>{asset.label}</span>)}
      </div></div>
    <div className="mission-world-assets" role="group" aria-label="Mission assets">
      <button type="button" onClick={()=>props.onSelectAsset('base')}>Ground control</button>
      <button type="button" onClick={()=>props.onSelectAsset('satellite')}>Satellite</button>
      {threat.profile!=='none' && <button type="button" onClick={()=>props.onSelectAsset('jammer')}>Threat details · {props.state.activeAttack.replaceAll('_',' ')}</button>}
    </div>
    <div className="mission-world-fleet" role="group" aria-label="Select mission drone">
      {props.drones.map(d=><button type="button" key={d.drone_id} aria-pressed={d.drone_id===props.selectedDroneId} onClick={()=>props.onSelectDrone(d.drone_id)}>{d.drone_id.replaceAll('_',' ').replace(/^drone/, 'Drone')}</button>)}
    </div>
    {threat.profile!=='none' && <div className="mission-threat-caption" role="status">
      {threat.label}
      {threat.paths.length>0 && <span> · Affected: {threat.paths.join(', ')}</span>}
    </div>}
    {(props.state.installedRoute==='mesh' || props.state.requestedRoute && props.state.requestedRoute!==props.state.installedRoute) && <div className="mission-network-caption" role="status">
      {props.state.installedRoute==='mesh' && <span>Mesh neighborhood · forwarding hops unavailable</span>}
      {props.state.requestedRoute && props.state.requestedRoute !== props.state.installedRoute && <span>Requested: {props.state.requestedRoute}{props.state.sdnError ? ' · update failed' : ' · not confirmed installed'}</span>}

    </div>}
    <div className="mission-world-footer"><span>Visual simulation · 1×</span>
    <details className="mission-world-notes"><summary>Simulation scope</summary><p>Terrain, flight and orbital scale are visual simulations. Mesh lines show a schematic neighborhood; forwarding hops are unavailable. Packet timing is scaled from reported latency; glyph density is not measured traffic.</p></details>
    </div>
  </div>;
}
