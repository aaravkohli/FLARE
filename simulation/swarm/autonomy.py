"""Receipt-driven local recovery and a separately authorized simulator transport.

LocalNetworkController has no engine, graph, attack registry or other UAV state.
The adapter alone enforces physical forwarding and delivers frozen messages.
"""
from collections import Counter,deque
from copy import deepcopy
from dataclasses import dataclass,field
import math
from simulation.swarm.network import shortest_path
from simulation.swarm.topology import pair
from schemas.autonomy import SafetyProfile
from simulation.swarm.route_verification import RouteVerification


@dataclass(frozen=True)
class LocalFrame:
    now: float
    available: bool
    interface_health: tuple
    queue_depth: int
    switching: bool
    safety_latched: bool


@dataclass
class LocalNetworkController:
    identity: str
    roster: tuple
    started_at: float
    state: str='CONNECTED'
    health: str='AWAITING_RECEIPTS'
    last_ground: dict=field(default_factory=dict)
    peers: dict=field(default_factory=dict)
    samples: deque=field(default_factory=lambda:deque(maxlen=256))
    disconnected_since: float | None=None
    recovery_started: float | None=None
    next_attempt: float=0.
    attempts: int=0
    stable_since: float | None=None
    stable: bool=False
    prior_loss: bool=False
    diagnosis: dict=field(default_factory=lambda:dict(label='Undetermined',confidence=None,hypotheses=[]))
    scan: int=0
    seen: deque=field(default_factory=lambda:deque(maxlen=256))
    history: deque=field(default_factory=lambda:deque(maxlen=64))
    ground_vectors: dict=field(default_factory=dict)
    provisioned_mesh: list=field(default_factory=list)
    provisioned_at: float | None=None
    switching_until: float=0.

    def receive(self,message,now):
        identity=message['id']
        if identity in self.seen or now>=message['expires_at']-1e-9:return False
        if message['sender']!='base' and message['sender'] not in self.roster:return False
        self.seen.append(identity)
        if message.get('guard'):
            # A staged policy is local knowledge only after its message arrives.
            # Verification receipts do not refresh ordinary liveness or install it.
            self.switching_until=min(message['expires_at'],now+3)
            return True
        if message['kind']=='response':
            if message['peer']=='base':
                self.last_ground[message['family']]=dict(received_at=now,route=message['forward_route'][:],rtt_s=now-message['sent_at'])
            else:
                peer=message['peer'];previous=self.peers.get(peer,{})
                self.peers[peer]=dict(previous,received_at=now)
        if message.get('advertisement'):
            advert=message['advertisement'];peer=message['sender'];route=advert['route']
            if route and route[0]==peer and route[-1]=='base' and len(set(route))==len(route) and len(route)<=len(self.roster)+1 and self.identity not in route:
                old=self.ground_vectors.get(peer)
                if not old or advert['sequence']>old['sequence']:
                    self.ground_vectors[peer]=dict(route=route[:],sequence=advert['sequence'],expires_at=min(message['expires_at'],now+3))
        return True

    def recent_ground(self,now):return {k:v for k,v in self.last_ground.items() if now-v['received_at']<3-1e-9}
    def advertised_route(self,now):
        options=[([self.identity]+v['route']) for peer,v in self.ground_vectors.items()
                 if v['expires_at']>now and now-self.peers.get(peer,{}).get('received_at',-math.inf)<3]
        return min(options,key=lambda p:(len(p),tuple(p))) if options else []
    def advertisement(self,now,sequence):
        routes=[v['route'] for f,v in self.recent_ground(now).items() if f=='mesh']
        routes += [self.advertised_route(now)] if self.advertised_route(now) else []
        path=min(routes,key=lambda p:(len(p),tuple(p))) if routes else []
        return dict(route=path,sequence=sequence)
    def peer_targets(self,now):
        fresh=sorted((n for n,r in self.peers.items() if now-r['received_at']<3),key=lambda n:(-self.peers[n]['received_at'],n))[:2]
        roster=[n for n in self.roster if n!=self.identity];result=fresh[:]
        for _ in range(len(roster)):
            if len(result)>=4:break
            n=roster[self.scan%len(roster)];self.scan+=1
            if n not in result:result.append(n)
        return result
    def diagnose(self,f):
        rows=[s for s in self.samples if f.now-s['at']<=5];hyp=[]
        failed=[name for name,ok in f.interface_health if ok is False]
        if failed:hyp.append(dict(cause='Interface self-test failure',confidence=1.,evidence=failed))
        if len(rows)>=8:
            lost=sum(not s['success'] for s in rows)/len(rows);sinr=[s['sinr'] for s in rows if s.get('sinr') is not None];rssi=[s['rssi'] for s in rows if s.get('rssi') is not None]
            if f.queue_depth>=32:hyp.append(dict(cause='Congestion',confidence=.8,evidence=['local_queue_depth',f.queue_depth]))
            if rssi and sum(rssi)/len(rssi)<-90:hyp.append(dict(cause='Signal attenuation',confidence=.75,evidence=['low_local_rssi']))
            if lost>.2 and sinr and sum(sinr)/len(sinr)<5 and rssi and sum(rssi)/len(rssi)>-85:
                hyp.append(dict(cause='Suspected intentional jamming',confidence=.7,evidence=['loss_and_low_sinr_despite_received_power']))
                hyp.append(dict(cause='Temporary interference',confidence=.7,evidence=['same_observations_also_admit_unintentional_interference']))
            if f.switching and lost>.2:hyp.append(dict(cause='Software transition disruption',confidence=.7,evidence=['transition_time_correlation_only']))
        hyp.sort(key=lambda h:(-h['confidence'],h['cause']))
        distinct=bool(hyp and (len(hyp)==1 or hyp[0]['confidence']>hyp[1]['confidence']))
        self.diagnosis=dict(label=hyp[0]['cause'] if distinct and hyp[0]['confidence']>=.7 else 'Undetermined',confidence=hyp[0]['confidence'] if hyp else None,hypotheses=hyp,observations=len(rows),basis='local_receipts_measurements_and_self_tests')
    def step(self,f):
        old=self.state;ground=self.recent_ground(f.now);peers={n:r for n,r in self.peers.items() if f.now-r['received_at']<3}
        if not f.available:
            self.health='UNAVAILABLE';self.state='FAILSAFE';self.stable=False
            self.diagnose(f);return None
        if ground:
            if self.stable_since is None:self.stable_since=f.now
            self.stable=f.now-self.stable_since>=5-1e-9
            self.health='CONNECTED'
            if self.prior_loss:self.state='RECONNECTED' if self.stable else 'RECOVERY'
            else:self.state='SWITCHING' if f.switching else 'CONNECTED'
            if self.stable:self.disconnected_since=None
        elif f.now-self.started_at<3:
            self.health='AWAITING_RECEIPTS';self.state='DEGRADED';self.stable=False
        else:
            self.stable_since=None;self.stable=False;self.prior_loss=True
            self.health='PARTIALLY_DISCONNECTED' if peers else 'COMPLETELY_ISOLATED'
            if self.disconnected_since is None:
                self.disconnected_since=f.now;self.recovery_started=f.now;self.state=self.health
            else:self.state='FAILSAFE' if f.now-self.disconnected_since>=10-1e-9 else 'RECOVERY'
        self.diagnose(f)
        if self.state=='CONNECTED' and (f.queue_depth>=32 or any(not s['success'] for s in list(self.samples)[-3:])):self.state='DEGRADED'
        if f.safety_latched and self.state!='RECONNECTED':self.state='FAILSAFE'
        if self.state!=old:
            record=dict(node=self.identity,previous=old,state=self.state,health=self.health,simulation_time=f.now)
            self.history.append(record);return record
        return None
    def snapshot(self,now):
        return dict(node=self.identity,state=self.state,health=self.health,stable=self.stable,disconnected_since=self.disconnected_since,
            attempts=self.attempts,ground_paths={k:dict(v,age_s=now-v['received_at']) for k,v in self.last_ground.items()},
            neighbors=[dict(node=n,age_s=now-v['received_at']) for n,v in sorted(self.peers.items())],
            advertised_route=self.advertised_route(now),diagnosis=deepcopy(self.diagnosis),history=list(self.history))


class AutonomousRecovery:
    """Transport adapter. Global feasibility here is never a local observation."""
    def __init__(self,e):
        self.engine=e;self.controllers={};self.bindings={};self.serial=0;self.last_send=-1.;self.last_monitor=-1.
        self.interfaces={};self.revoked=set();self.forwarding={};self.counters=Counter();self.guard=None;self.profile_epoch=0
        self.channel_sequences=Counter();self.verifier=RouteVerification(self)
    @property
    def enabled(self):return self.engine.cfg.get('autonomy_mode','off')=='recovery_v1'
    def sync(self):
        e=self.engine;roster=tuple(e.nodes)
        for n in list(self.controllers):
            if n not in e.nodes:self.controllers.pop(n);self.forwarding.pop(n,None)
        if not self.enabled:return
        for n in roster:
            if n not in self.controllers:
                self.controllers[n]=LocalNetworkController(n,roster,e.seconds)
            self.controllers[n].roster=roster
            c=self.controllers[n]
            if c.provisioned_at is None and e.physical_links:
                c.provisioned_mesh=e.route(n,'base','mesh',True)[:];c.provisioned_at=e.seconds
    def permitted(self,path,family,restrictions=None):
        e=self.engine;restrictions=restrictions or {}
        if len(path)<2 or len(path)!=len(set(path)) or 'base' in path[1:-1] or family in e.failed_families:return False
        if set(path)&self.revoked:return False
        if set(path)&set(restrictions.get('excluded_nodes',[])):return False
        blocked={pair(*x) for x in restrictions.get('excluded_links',[])}
        for n in path:
            d=e.nodes.get(n)
            if n!='base' and (not d or not d.available or d.health<=0 or d.containment=='quarantined' or (d.threat or 0)>.8):return False
            if d:
                supervisor=e.flight_safety.supervisors.get(n)
                profile=supervisor.profile if supervisor else e.flight_safety.profile
                health=self.interfaces.get(n,{}).get(family,profile.interface_health.get(family))
                if health is False:return False
        if family!='mesh' and (len(path)!=2 or 'base' not in path):return False
        if family=='satellite':return self.link_for(path[0],path[1],family).loss<=e.cfg['radio']['max_loss']
        return all(pair(a,b) not in e.blocked and pair(a,b) not in e.auto_blocked and pair(a,b) not in blocked and
            (l:=self.link_for(a,b,family)) is not None and l.loss<=e.cfg['radio']['max_loss'] for a,b in zip(path,path[1:]))
    def link_for(self,a,b,family):
        e=self.engine
        if family=='satellite':return e.satellite_link(b if a=='base' else a)
        raw=e.physical_links.get(pair(a,b))
        return e.attacks.impair(raw,family,(a,b))
    def packet_link(self,p,a,b):return self.link_for(a,b,p.family)
    def owns(self,p):return p.id in self.bindings
    def allowed(self,p,a,b):
        binding=self.bindings.get(p.id);e=self.engine
        if not binding or binding['session']!=e.session_id or p.route!=binding['path'] or p.channel_key!=binding['key']:return False
        original=binding['message'].get('original_flow')
        if original and not self.verifier.flow_permitted(original,p.route):return False
        if binding['message']['kind']=='profile' and not e.controller_connected:return False
        if getattr(e.scheduler,'checking_at',e.seconds)>=binding['expires_at']-1e-9:return False
        constraints=e.flow_constraints.get(f'{p.source}:{p.destination}:{p.category}',{})
        if p.category not in constraints.get('allowed_traffic_classes',[p.category]):return False
        origin=e.nodes.get(p.source)
        if set(p.route)&self.revoked or set(p.route)&set(constraints.get('excluded_nodes',[])):return False
        if origin and (origin.containment=='quarantined' or (origin.threat or 0)>.8):return False
        if any(pair(a,b) in {pair(*x) for x in constraints.get('excluded_links',[])} for a,b in zip(p.route,p.route[1:])):return False
        if p.category not in e.control_traffic:
            if any(n in e.nodes and (e.nodes[n].containment in ('restricted','control_only') or e.families.get(n)=='hold') for n in [p.source]+p.route[p.hop:]):return False
        return self.permitted(p.route[p.hop:],p.family,constraints)
    def send(self,path,family,kind,peer=None,forward=None,sent_at=None,advertisement=None,guard=False,profile=None,original_flow=None):
        e=self.engine;now=e.scheduler.event_time if e.scheduler.event_time is not None else e.seconds
        if not path or len(path)<2:return None
        self.serial+=1
        capture=now if sent_at is None else sent_at
        logical=(kind,path[0],path[-1],str(guard),family,capture)
        self.channel_sequences[logical]+=1
        key=f'autonomy:{kind}:{path[0]}:{path[-1]}:{guard}:{capture:.6f}:{self.channel_sequences[logical]}:{family}'
        identity=f'p{e.scheduler.serial+1}';expires=(now if sent_at is None else sent_at)+2.9
        message=dict(id=key,sender=path[0],recipient=path[-1],kind=kind,peer=peer,family=family,forward_route=forward or path[:],
            sent_at=now if sent_at is None else sent_at,expires_at=expires,advertisement=deepcopy(advertisement),guard=guard,profile=profile)
        if original_flow:message['original_flow']=original_flow
        self.bindings[identity]=dict(session=e.session_id,path=path[:],key=key,expires_at=expires,message=message)
        category='routing';size=128+32*len(path)+(len(str(profile).encode()) if profile else 0)
        packet=e.scheduler.emit(path[0],path[-1],category,size,path,now,e.network_revision,'autonomy:'+key,family,
            service={'kind':'autonomy_registered','deadline':expires},channel_key=key,expires_at=expires)
        self.counters['offered']+=1;self.counters['bytes']+=size
        e.adaptive.offer(path[0],path[-1],category,size,now)
        return packet
    def packet_event(self,kind,data):
        e=self.engine;binding=self.bindings.get(data.get('id'))
        if not binding:return
        message=binding['message'];now=data.get('event_at',e.seconds)
        if kind in ('packet_hop_completed','packet_attempt_lost'):
            a,b=data.get('from_node'),data.get('to_node');link=self.link_for(a,b,data['family'])
            for n in (a,b):
                if n in self.controllers:self.controllers[n].samples.append(dict(at=now,success=kind=='packet_hop_completed',sinr=link.sinr if link else None,rssi=link.rssi if link else None))
        if kind!='packet_result':return
        self.bindings.pop(data['id'],None)
        if data['status']!='delivered':self.counters['dropped']+=1;return
        self.counters['delivered']+=1
        if now>=binding['expires_at']-1e-9:return
        if message['kind']=='challenge':
            if message['recipient']=='base' and not e.controller_connected:return
            controller=self.controllers.get(message['recipient'])
            if controller:controller.receive(message,now)
            advert=controller.advertisement(now,self.serial) if controller else None
            self.send(list(reversed(binding['path'])),data['family'],'response',peer=message['recipient'],forward=binding['path'],sent_at=message['sent_at'],advertisement=advert,guard=message['guard'],original_flow=message.get('original_flow'))
        elif message['kind']=='response':
            node=message['recipient'];controller=self.controllers.get(node)
            if message['guard']:
                if controller:controller.receive(message,now)
                self.verifier.receive(message,now)
                if controller and message['peer']=='base' and self.guard and message['guard']==self.guard.get('tag',self.guard['target']):controller.provisioned_mesh=message['forward_route'][:]
                return
            if not controller:return
            if controller.receive(message,now) and message['peer']=='base':
                self.forwarding[node]=dict(route=message['forward_route'],family=data['family'],verified_at=now,expires_at=now+3)
        elif message['kind']=='profile':
            node=message['recipient']
            if node in e.flight_safety.supervisors:
                value=message['profile']
                if value['epoch']==self.profile_epoch:
                    e.flight_safety.supervisors[node].profile=SafetyProfile.model_validate(value['data'])
                    e.flight_safety.sensor_at[node]=now
                    s=e.flight_safety.supervisors[node];s.home_valid=s.site_valid(s.home[0],s.home[2])
                    e.event('SAFETY','onboard_profile_applied',dict(node=node,epoch=value['epoch'],received_at=now))
    def step(self):
        e=self.engine;now=e.seconds
        for key in list(self.channel_sequences):
            if key[-1]<now-6:self.channel_sequences.pop(key)
        if not self.enabled and not any(s.latched for s in e.flight_safety.supervisors.values()):return
        self.sync()
        for n,v in list(self.forwarding.items()):
            if now>=v['expires_at'] or not self.permitted(v['route'],v['family']):self.forwarding.pop(n)
        if now-self.last_send>=1-1e-9:
            self.last_send=now
            for n,c in self.controllers.items():
                if self.enabled:
                    for peer in c.peer_targets(now):self.send([n,peer],'mesh','challenge',peer=peer,advertisement=c.advertisement(now,self.serial))
                for family in ('direct','satellite','mesh'):
                    if family=='mesh':
                        path=c.last_ground.get('mesh',{}).get('route',c.provisioned_mesh)
                        if c.disconnected_since is not None:path=c.advertised_route(now) or path
                    else:path=[n,'base']
                    self.send(path,family,'challenge',peer='base')
                if self.enabled and c.disconnected_since is not None and now>=c.next_attempt:
                    c.attempts+=1;c.next_attempt=now+min(4,2**min(2,c.attempts-1))
                    path=c.advertised_route(now)
                    if path:self.send(path,'mesh','challenge',peer='base')
                    e.event('RECOVERY','local_reconnection_attempt',dict(node=n,attempt=c.attempts,next_at=c.next_attempt,basis='delivered_neighbor_advertisements'))
        if now-self.last_monitor<.1-1e-9:return
        self.last_monitor=now
        for n,c in self.controllers.items():
            d=e.nodes[n];health=tuple((f,self.interfaces.get(n,{}).get(f,e.flight_safety.supervisors[n].profile.interface_health.get(f))) for f in ('direct','satellite','mesh'))
            queue=sum(p.source==n for p in e.scheduler.packets.values())
            f=LocalFrame(now,d.available,health,queue,now<c.switching_until,e.flight_safety.supervisors[n].latched)
            record=c.step(f)
            if record:e.event('RECOVERY','local_state_changed',record)
    def recovery_rule(self,p):
        r=self.forwarding.get(p.source)
        if not self.enabled or not r or p.destination!='base' or p.category not in ('telemetry','heartbeat','threat','emergency','ack'):return None
        if self.engine.seconds>=r['expires_at'] or not self.permitted(r['route'],r['family'],p.restrictions):return None
        return r
    def bind_forwarding(self,packet,route):
        # Ordinary traffic gets a captured, expiring route binding, never
        # registered-control priority or a graph-wide recovery permission.
        self.bindings[packet.id]=dict(session=self.engine.session_id,path=route['route'][:],key=packet.channel_key,
            expires_at=route['expires_at'],message=dict(kind='forwarding'))
    def configure_profile(self,profile):
        e=self.engine;self.profile_epoch+=1
        for n in self.controllers:
            path=e.route('base',n,'mesh',True)
            if path:self.send(path,'mesh','profile',profile=dict(epoch=self.profile_epoch,data=profile.model_dump()))
        e.event('SAFETY','profile_delivery_requested',dict(epoch=self.profile_epoch,targets=list(self.controllers),effect='on_receipt_only'))
    def start_guard(self,target,candidate,extra_flows=None):
        e=self.engine;now=e.seconds;required=self.check_candidate(candidate) if self.enabled else set()
        flows={n:(n,'base') for n in required};categories={}
        if self.enabled:
            for n,c in self.controllers.items():
                if n in required:continue
                peers=sorted(p for p,r in c.peers.items() if now-r['received_at']<3)
                if peers:flows[n+'|'+peers[0]]=(n,peers[0])
        for key,(source,dest,category) in (extra_flows or {}).items():flows[key]=(source,dest);categories[key]=category
        self.verifier.start(target,candidate,flows,categories,assisted=extra_flows is not None)
    def check_candidate(self,candidate):
        e=self.engine;now=e.seconds;required={n for n,c in self.controllers.items() if c.recent_ground(now)}
        for n in sorted(required):
            # A mesh transition may not voluntarily discard an existing mesh
            # route without verifying its replacement, even with satellite up.
            path=shortest_path(candidate.links,n,'base',e.nodes,e.blocked|e.auto_blocked,control=True)
            if not path:raise ValueError(f'Topology rejected: no permitted replacement ground route for {n}')
        for n,c in self.controllers.items():
            for peer,r in c.peers.items():
                if now-r['received_at']>=3:continue
                old=shortest_path(e.links,n,peer,e.nodes,e.blocked|e.auto_blocked,control=True)
                if old and not shortest_path(candidate.links,n,peer,e.nodes,e.blocked|e.auto_blocked,control=True):
                    raise ValueError(f'Topology rejected: would partition verified peers {n} and {peer}')
        return required
    def guard_step(self):return self.verifier.step()
    def snapshot(self):
        e=self.engine;g=self.guard
        return dict(version='autonomous_recovery_v1',mode=e.cfg.get('autonomy_mode','off'),nodes=[c.snapshot(e.seconds) for c in self.controllers.values()],
            counters=dict(self.counters),profile=e.flight_safety.profile.model_dump(),safety=e.flight_safety.snapshot(),
            pending_profile_epoch=self.profile_epoch,forwarding=[dict(node=n,**v) for n,v in self.forwarding.items()],
            transition=None if not g else dict(target=g['target'],started_at=g['started_at'],deadline=g['deadline'],required=len(g['required']),verified=sum(len(v)>=2 for v in g['receipts'].values())),
            overlay_edges=[dict(a=a,b=b,kind='verification' if r['message'].get('guard') else 'recovery_control',expires_at=r['expires_at']) for r in self.bindings.values() for a,b in zip(r['path'],r['path'][1:])][:800],
            evidence='modeled_registered_control_traffic',limits=dict(peers_per_uav_per_s=4,receipt_timeout_s=3,stable_recovery_s=5,recovery_budget_s=10))
