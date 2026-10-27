"""Bounded motion and independently selectable physical formations."""
from __future__ import annotations
import math
from dataclasses import dataclass, field, asdict

@dataclass
class Drone:
    id: str
    alias: str
    index: int
    position: list[float]
    target: list[float]
    velocity: list[float] = field(default_factory=lambda: [0., 0., 0.])
    heading: float = 0.
    battery: float = 1.
    health: float = 1.
    role: str = 'standard'
    cluster: int = 0
    mode: str = 'patrol'
    available: bool = True
    containment: str = 'normal'
    trust: float | None = None
    threat: float | None = None
    range_m: float = 350.
    selected_route: str = 'mesh'
    current_command: str | None = None
    waypoints: list[list[float]] = field(default_factory=list)
    waypoint_index: int = 0
    waypoint_loop: bool = False
    fl_status: str = 'NOT_PARTICIPATING'
    neighbors: list[str] = field(default_factory=list)
    sent: int = 0
    received: int = 0
    forwarded: int = 0
    dropped: int = 0
    formation_slot: list[float] | None = None
    def snapshot(self): return asdict(self)

def formation_positions(name: str, count: int, spacing: float = 42.) -> list[list[float]]:
    side = math.ceil(math.sqrt(count)); points = []
    for i in range(count):
        row, col = divmod(i, side)
        if name == 'line': x,z = (i-(count-1)/2)*spacing,0
        elif name == 'column': x,z = 0,(i-(count-1)/2)*spacing
        elif name == 'circle':
            radius = max(90, count*spacing/(2*math.pi)); a=i*2*math.pi/count; x,z=radius*math.cos(a),radius*math.sin(a)
        elif name in ('v', 'wedge'):
            rank=(i+1)//2; x,z=rank*spacing*(1 if i%2 else -1),rank*spacing
            if name == 'wedge': x *= .65
        elif name == 'diamond':
            a=i*2*math.pi/count; radius=max(100,count*spacing/(2*math.pi)); x,z=radius*math.cos(a),radius*math.sin(a)
            denominator=abs(math.cos(a))+abs(math.sin(a)); x/=denominator; z/=denominator
        elif name == 'cluster':
            cluster, member=divmod(i,10); a=member*2*math.pi/10
            x=(cluster%4-1.5)*210+75*math.cos(a); z=(cluster//4-.5)*210+75*math.sin(a)
        else:
            x,z=(col-(side-1)/2)*spacing,(row-(side-1)/2)*spacing
            if name == 'distributed': x += 8*math.sin(i*2.4); z += 8*math.cos(i*1.7)
        points.append([x,80.+(i%3)*12,z])
    return points

def advance(drones: list[Drone], dt: float, seconds: float, slots: list[list[float]], terrain, supervisors=None) -> None:
    # Neighbor buckets avoid quadratic separation work in sparse, large missions.
    buckets: dict[tuple[int,int],list[Drone]] = {}
    for d in drones: buckets.setdefault((int(d.position[0]//70),int(d.position[2]//70)),[]).append(d)
    changes=[]
    for i,d in enumerate(drones):
        supervisor=supervisors.get(d.id) if supervisors else None
        if supervisor and supervisor.latched and d.mode in ('landed','failed_ground_contact'):
            changes.append((d,d.position[:],[0.,0.,0.]));continue
        if supervisor and supervisor.latched and d.mode=='uncontrolled_descent':
            velocity=[0.,max(-12.,d.velocity[1]-9.81*dt),0.]
            changes.append((d,[d.position[j]+velocity[j]*dt for j in range(3)],velocity));continue
        if not d.available or d.health <= 0: changes.append((d,d.position[:],[0.,0.,0.])); continue
        if d.mode == 'patrol':
            slot=d.formation_slot or slots[i]; d.target=[slot[0]+45*math.sin(seconds/45),slot[1],slot[2]+45*math.cos(seconds/45)]
        elif d.mode=='waypoints' and d.waypoints:
            d.target=d.waypoints[d.waypoint_index][:]
            if math.dist(d.position,d.target)<5:
                if d.waypoint_index+1<len(d.waypoints): d.waypoint_index+=1
                elif d.waypoint_loop: d.waypoint_index=0
                else: d.mode='hold'
        target=d.position if d.mode in ('hold','safety_hold') else d.target
        offset=[target[j]-d.position[j] for j in range(3)]; distance=math.dist(target,d.position)
        desired=[v/max(1,distance)*min(16.,distance*.55) for v in offset]
        desired[1]=max(-2.,min(2.,desired[1]))
        if supervisor and d.mode=='landing':desired[1]=max(-1.,desired[1])
        cx,cz=int(d.position[0]//70),int(d.position[2]//70)
        for bx in ([] if d.mode=='hold' else range(cx-1,cx+2)):
            for bz in range(cz-1,cz+2):
                for other in buckets.get((bx,bz),[]):
                    if other is d: continue
                    diff=[d.position[j]-other.position[j] for j in range(3)]; gap=math.sqrt(sum(v*v for v in diff))
                    if gap<40:
                        if gap<.01: diff=[1. if d.id<other.id else -1.,0.,0.]; gap=1.
                        strength=(40-gap)/40*16
                        for j in (0,2): desired[j] += diff[j]/gap*strength
        floor=terrain.height(d.position[0],d.position[2])+(0 if supervisor and d.mode=='landing' and supervisor.site else 35)
        if supervisor and d.mode=='landing':desired[1]=-min(1.,max(0.,d.position[1]-floor)/dt)
        if not (supervisor and d.mode=='landing') or d.position[1]<floor:
            desired[1]=max(desired[1],min(2.,(floor-d.position[1])*2))
        speed=math.sqrt(sum(v*v for v in desired))
        if speed>16: desired=[v*16/speed for v in desired]
        dv=[desired[j]-d.velocity[j] for j in range(3)]; size=math.sqrt(sum(v*v for v in dv)); scale=min(1.,4*dt/max(size,1e-9))
        velocity=[d.velocity[j]+dv[j]*scale for j in range(3)]
        horizontal=math.hypot(velocity[0],velocity[2])
        if horizontal>.05:
            heading=math.atan2(velocity[2],velocity[0]); change=math.atan2(math.sin(heading-d.heading),math.cos(heading-d.heading))
            bounded=d.heading+max(-.55*dt,min(.55*dt,change))
            turn=[horizontal*math.cos(bounded),velocity[1],horizontal*math.sin(bounded)]
            delta=math.dist(turn,d.velocity)
            if delta>4*dt: turn=[d.velocity[j]+(turn[j]-d.velocity[j])*4*dt/delta for j in range(3)]
            velocity=turn
        position=[d.position[j]+velocity[j]*dt for j in range(3)]
        if supervisor and d.mode=='landing':
            position[1]=max(floor,position[1]);velocity[1]=max(-1.,velocity[1])
        if supervisor and not supervisor.segment_safe(d.position,position):
            position=d.position[:];velocity=[0.,0.,0.]
            supervisor.unresolved=list(dict.fromkeys(supervisor.unresolved+['Flight segment blocked by geofence or declared obstacle']))
        changes.append((d,position,velocity))
    for d,p,v in changes:
        d.position=p; d.velocity=v
        if math.hypot(v[0],v[2])>.05: d.heading=math.atan2(v[2],v[0])
        d.battery=max(0,d.battery-dt*(.000008+.000001*math.sqrt(sum(x*x for x in v))))
        if d.battery<.08 and d.mode=='patrol': d.mode='return'; d.target=[-260.,70.,180.]
