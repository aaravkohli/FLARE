"""Independent, deterministic kinematic safety supervisor.

Inputs are explicit local simulated sensor frames. Scenery is not an obstacle
sensor, and an absent safe site never becomes a successful landing.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
from schemas.autonomy import SafetyProfile


@dataclass(frozen=True)
class FlightFrame:
    now: float
    position: tuple
    velocity: tuple
    battery: float
    available: bool
    disconnected_since: float | None
    connected_stably: bool
    nearby: tuple
    sensor_at: float


class SafetySupervisor:
    def __init__(self,identity,home,profile,terrain):
        self.identity=identity;self.home=tuple(home);self.profile=deepcopy(profile)
        self.terrain=terrain;self.action=None;self.reason=None;self.latched=False
        self.started_at=None;self.site=None;self.phase=None;self.unresolved=[]
        self.outcome=None;self.last_frame=None;self.counters={k:0 for k in ('geofence_breaches','separation_violations','unsafe_contacts','landings','battery_exhaustions','interventions')}
        self.previous_violations=set();self.home_valid=self.site_valid(self.home[0],self.home[2])
        self.battery_state='normal'

    def site_valid(self,x,z):
        p=self.profile;r=5.
        if abs(x)+r>p.geofence_m or abs(z)+r>p.geofence_m:return False
        if any(math.hypot(x-o.x,z-o.z)<o.radius_m+r for o in p.obstacles):return False
        heights=[self.terrain.height(x+dx,z+dz) for dx,dz in ((0,0),(r,0),(-r,0),(0,r),(0,-r))]
        return max(heights)-min(heights)<=2*r*math.tan(math.radians(5))

    def inputs_fresh(self,f):return f.now-f.sensor_at<=self.profile.input_freshness_s+1e-9
    def navigation(self,f):return self.inputs_fresh(f) and self.profile.navigation_confidence is not None and self.profile.navigation_confidence>=.8
    def obstacles_known(self,f):return self.inputs_fresh(f) and self.profile.obstacle_sensor_available is True
    def segment_safe(self,a,b):
        if any(abs(v)>self.profile.geofence_m for p in (a,b) for v in (p[0],p[2])):return False
        dx,dz=b[0]-a[0],b[2]-a[2];denom=dx*dx+dz*dz
        for o in self.profile.obstacles:
            t=max(0.,min(1.,((o.x-a[0])*dx+(o.z-a[2])*dz)/denom)) if denom else 0.
            x,z=a[0]+t*dx,a[2]+t*dz;y=a[1]+t*(b[1]-a[1])
            if math.hypot(x-o.x,z-o.z)<o.radius_m+5 and y<=self.terrain.height(x,z)+o.height_m+5:return False
        return True

    def choose_site(self,f):
        candidates=[]
        if self.home_valid:candidates.append(('home',self.home[0],self.home[2]))
        candidates += [(s.id,s.x,s.z) for s in self.profile.landing_areas]
        candidates.sort(key=lambda s:(math.hypot(s[1]-f.position[0],s[2]-f.position[2]),s[0]))
        if f.battery>self.profile.critical_battery and self.profile.mission_criticality=='routine':candidates.sort(key=lambda s:s[0]!='home')
        for identity,x,z in candidates:
            ground=self.terrain.height(x,z);cruise=max(f.position[1],ground+35)
            target=(x,cruise,z)
            if not self.site_valid(x,z) or not self.segment_safe(f.position,target):continue
            if any(math.hypot(p[0]-x,p[2]-z)<10 and p[1]<ground+35 for _,p,_ in f.nearby):continue
            # Conservative time/resource proxy, using existing battery-rate units.
            duration=math.hypot(x-f.position[0],z-f.position[2])/8+max(0,cruise-ground)+20
            required=duration*(.000008+.000001*16)+.01
            if f.battery<required:continue
            return dict(id=identity,x=x,z=z,ground=ground,cruise=cruise,required_battery=required)
        return None

    def step(self,f,enabled,network_state):
        self.last_frame=f;events=[]
        if self.outcome in ('landed','unsafe_ground_contact'):return events
        level='emergency' if f.battery<=self.profile.emergency_battery else 'critical' if f.battery<=self.profile.critical_battery else 'caution' if f.battery<=self.profile.caution_battery else 'normal'
        if level!=self.battery_state:
            self.battery_state=level;events.append(('battery_state_changed',dict(node=self.identity,level=level,battery=f.battery)))
        reason=None
        if enabled and f.available:
            if f.battery<=self.profile.critical_battery:reason='critical_battery'
            elif f.disconnected_since is not None:reason='control_loss'
            elif any(abs(f.position[j])>self.profile.geofence_m for j in (0,2)):reason='outside_geofence'
            elif not self.navigation(f) or not self.obstacles_known(f):reason='safety_inputs_unavailable'
        if reason and not self.latched:
            self.latched=True;self.started_at=f.now;self.action='safety_hold';self.reason=reason
            self.counters['interventions']+=1;events.append(('safety_latched',self.snapshot()))
        if not self.latched:return events
        if f.battery<=0:
            if self.action!='uncontrolled_descent':self.counters['battery_exhaustions']+=1;events.append(('battery_exhausted',dict(node=self.identity)))
            self.action='uncontrolled_descent';self.reason='powered_flight_unavailable';return events
        self.unresolved=[]
        if not self.navigation(f):self.unresolved.append('Navigation confidence unavailable, stale or below 0.8')
        if not self.obstacles_known(f):self.unresolved.append('Obstacle evidence unavailable or stale')
        expired=f.disconnected_since is not None and f.now-f.disconnected_since>=10-1e-9
        urgent=f.battery<=self.profile.critical_battery or self.reason=='outside_geofence'
        if self.action in ('safety_return','landing') and self.unresolved:
            self.action='safety_hold';self.site=None;events.append(('safety_route_suspended',dict(node=self.identity,reasons=self.unresolved[:])))
        # Reconnection does not release a latch or cancel landing.
        if (expired or urgent) and self.action=='safety_hold' and not self.unresolved:
            site=self.choose_site(f)
            if site:
                self.site=site;self.action='safety_return';self.phase='approach'
                events.append(('safety_return_started',dict(node=self.identity,site=site,energy_basis='uncalibrated_existing_battery_proxy')))
            else:self.unresolved.append('No feasible declared landing site or modeled battery reserve')
        if self.action=='safety_return' and self.site:
            s=self.site
            if not self.segment_safe(f.position,(s['x'],s['cruise'],s['z'])):
                self.action='safety_hold';self.site=None;self.unresolved.append('Return corridor obstructed')
            elif math.hypot(f.position[0]-s['x'],f.position[2]-s['z'])<1.5 and math.hypot(f.velocity[0],f.velocity[2])<.3:
                self.action='landing';self.phase='descent';events.append(('landing_started',dict(node=self.identity,site=s['id'])))
        if self.action=='landing' and self.site:
            s=self.site
            if any(math.hypot(p[0]-s['x'],p[2]-s['z'])<10 and p[1]<s['ground']+35 for _,p,_ in f.nearby):
                self.action='safety_hold';self.unresolved.append('Landing footprint occupied');self.site=None
        return events

    def command_allowed(self,action,f):
        if not self.latched:return True,None
        if action in ('role','route','reroute','isolate','restore','disconnect','reconnect'):return True,None
        if action=='resume' and self.action not in ('landing','landed','uncontrolled_descent') and self.outcome is None:
            if f.connected_stably and self.navigation(f) and self.obstacles_known(f) and f.battery>self.profile.critical_battery:
                self.latched=False;self.action=None;self.reason=None;self.site=None;return True,None
            return False,'Resume requires stable delivered connectivity and current safe flight inputs'
        return False,'Flight safety latch active; delivered Resume required; landing cannot be cancelled'

    def target(self,position):
        if self.action=='safety_return' and self.site:return [self.site['x'],self.site['cruise'],self.site['z']]
        if self.action=='landing' and self.site:return [self.site['x'],self.site['ground'],self.site['z']]
        return list(position)

    def snapshot(self):
        f=self.last_frame
        return dict(node=self.identity,latched=self.latched,action=self.action,reason=self.reason,started_at=self.started_at,
            home=list(self.home),home_valid=self.home_valid,site=deepcopy(self.site),phase=self.phase,outcome=self.outcome,
            navigation_confidence=self.profile.navigation_confidence,obstacle_sensor_available=self.profile.obstacle_sensor_available,
            inputs_current=self.inputs_fresh(f) if f else None,unresolved=self.unresolved[:],counters=self.counters.copy(),
            battery_state=self.battery_state,evidence='explicit_simulated_safety_fixtures',energy_model='uncalibrated_existing_battery_proxy')


class FlightSafety:
    def __init__(self,engine):self.engine=engine;self.supervisors={};self.sensor_at={};self.profile=SafetyProfile.model_validate(engine.cfg.get('safety_profile',{}))
    def sync(self):
        e=self.engine
        for n in list(self.supervisors):
            if n not in e.nodes:self.supervisors.pop(n);self.sensor_at.pop(n,None)
        for n,d in e.nodes.items():
            if n not in self.supervisors:
                self.supervisors[n]=SafetySupervisor(n,d.position,self.profile.model_copy(deep=True),e.terrain);self.sensor_at[n]=e.seconds
    def frame(self,n):
        e=self.engine;d=e.nodes[n];supervisor=self.supervisors[n]
        if supervisor.profile.fixture_refresh:self.sensor_at[n]=e.seconds
        local=e.autonomy.controllers.get(n)
        nearby=tuple((peer.id,tuple(peer.position),tuple(peer.velocity)) for peer in e.nodes.values() if peer.id!=n and math.dist(peer.position,d.position)<=100) if supervisor.profile.obstacle_sensor_available is True else ()
        return FlightFrame(e.seconds,tuple(d.position),tuple(d.velocity),d.battery,d.available,
            local.disconnected_since if local else None,local.stable if local else False,nearby,self.sensor_at[n])
    def step(self):
        self.sync();e=self.engine
        for n,s in self.supervisors.items():
            f=self.frame(n)
            for kind,data in s.step(f,e.autonomy.enabled,e.autonomy.controllers[n].state if n in e.autonomy.controllers else None):e.event('SAFETY',kind,data)
            if s.latched:
                d=e.nodes[n];d.mode=s.action;d.target=s.target(d.position)
    def after_motion(self):
        e=self.engine
        for n,s in self.supervisors.items():
            d=e.nodes[n];ground=e.terrain.height(d.position[0],d.position[2]);violations=set()
            if any(abs(d.position[j])>s.profile.geofence_m+1e-6 for j in (0,2)):violations.add('geofence_breaches')
            if any(math.dist(d.position,other.position)<5 for other in e.nodes.values() if other.id!=n):violations.add('separation_violations')
            if d.position[1]<=ground+1e-6 and s.outcome is None:
                if s.action=='landing' and s.site and math.hypot(d.position[0]-s.site['x'],d.position[2]-s.site['z'])<2 and abs(d.velocity[1])<=1.01:
                    s.outcome='landed';s.action='landed';s.phase='touchdown';s.counters['landings']+=1
                    d.mode='landed';d.velocity=[0.,0.,0.];d.position[1]=ground
                    e.event('SAFETY','landing_completed',dict(node=n,site=s.site['id'],simulation_time=e.seconds))
                else:
                    s.outcome='unsafe_ground_contact';s.action='failed_ground_contact';violations.add('unsafe_contacts')
                    d.mode='failed_ground_contact';d.velocity=[0.,0.,0.];d.position[1]=ground
                    e.event('SAFETY','unsafe_ground_contact',dict(node=n,simulation_time=e.seconds))
            for v in sorted(violations-s.previous_violations):s.counters[v]+=1;e.event('SAFETY',v,dict(node=n))
            s.previous_violations=violations
    def snapshot(self):return [s.snapshot() for s in self.supervisors.values()]
