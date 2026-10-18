"""Modeled radio links, graph routing, and bounded per-hop packet scheduling."""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, asdict, field
import heapq
import hashlib
import math
from simulation.swarm.topology import pair

@dataclass
class Link:
    a: str
    b: str
    distance_m: float
    rssi: float
    sinr: float
    bandwidth_mbps: float
    loss: float
    latency_ms: float
    status: str = 'healthy'
    def snapshot(self): return dict(asdict(self), id=':'.join(pair(self.a,self.b)), encrypted='simulated_policy')

def radio_link(a,b,pa,pb,cfg,terrain,jammers,health=1.,offset=0.):
    distance=math.dist(pa,pb)
    if distance>cfg['range_m'] or health<=0: return None
    attenuation=0.
    for i in range(1,9):
        t=i/9; x=pa[0]+(pb[0]-pa[0])*t; z=pa[2]+(pb[2]-pa[2])*t
        if terrain.height(x,z)+3 > pa[1]+(pb[1]-pa[1])*t: attenuation+=3
    rssi=cfg['reference_rssi_dbm']-10*cfg['path_loss_exponent']*math.log10(max(1,distance))-attenuation+offset
    noise=10**(cfg['noise_dbm']/10); jammed=False
    for jammer in jammers:
        proximity=min(math.dist(pa,jammer['position']),math.dist(pb,jammer['position']))
        if proximity<jammer['radius_m']:
            noise+=10**((jammer['power_dbm']-22*math.log10(max(1,proximity)))/10); jammed=True
    sinr=rssi-10*math.log10(noise)
    pdr=1/(1+math.exp(-max(-30,min(30,(sinr-7)/2.5))))*health
    loss=1-pdr
    status='jammed' if jammed and loss>.2 else 'degraded' if loss>.12 else 'healthy'
    return Link(a,b,distance,max(-130,min(0,rssi)),sinr,max(.05,cfg['bandwidth_mbps']*min(1,max(.02,sinr/25))),loss,cfg['propagation_ms']+distance/299792458*1000,status)

def build_adjacency(links,nodes,blocked=frozenset(),omit=frozenset(),control=False,enforce_security=True):
    adjacency=defaultdict(list)
    for edge,link in links.items():
        if edge in blocked or edge in omit or link.loss>.35: continue
        unavailable=False
        for identity in edge:
            d=nodes.get(identity)
            if d and (not d.available or enforce_security and (d.containment=='quarantined' or ((d.containment in ('restricted','control_only') or d.selected_route=='hold') and not control) or (d.threat or 0)>.8)): unavailable=True
        if unavailable: continue
        trust=min((nodes[n].trust if nodes[n].trust is not None else .8) for n in edge if n in nodes) if any(n in nodes for n in edge) else 1.
        cost=link.latency_ms+100*link.loss+25*(1-trust)
        adjacency[link.a].append((link.b,cost)); adjacency[link.b].append((link.a,cost))
    for neighbors in adjacency.values(): neighbors.sort()
    return adjacency

def shortest_path(links,source,destination,nodes,blocked=frozenset(),omit=frozenset(),control=False,adjacency=None):
    if adjacency is None: adjacency=build_adjacency(links,nodes,blocked,omit,control)
    queue=[(0.,(source,))]; visited=set()
    while queue:
        cost,path=heapq.heappop(queue); current=path[-1]
        if current in visited: continue
        if current==destination: return list(path)
        if current=='base' and current!=source: continue
        visited.add(current)
        for peer,weight in adjacency.get(current,[]):
            if peer not in visited: heapq.heappush(queue,(cost+weight,path+(peer,)))
    return []

@dataclass
class Packet:
    id: str
    source: str
    destination: str
    category: str
    size_bytes: int
    route: list[str]
    created_at: float
    expires_at: float
    revision: int
    family: str='mesh'
    hop: int=0
    start_at: float=0.
    end_at: float=0.
    retries: int=0
    status: str='queued'
    reason: str | None=None
    correlation_id: str | None=None
    sequence: int=0
    claimed_source_mac: str | None=None
    completed_at: float | None=None
    policy_source: str | None=None
    repair_count: int=0
    attempt: int=0
    restrictions: dict = field(default_factory=dict)
    event_at: float=0.
    history: list = field(default_factory=list)
    def snapshot(self):
        return dict(asdict(self),protocol='reliable_simulated' if self.category in ('command','ack','fl_update') else 'udp_simulated')

class PacketScheduler:
    """Exact event times inside fixed movement steps; no per-hop tick rounding.

    A half-duplex radio is reserved for serialization airtime, not propagation.
    Waiting packets acquire airtime at their actual start event. Counters never
    depend on the bounded display trace or on browser animation sampling.
    """
    RELIABLE = frozenset(('command', 'ack', 'fl_update'))

    def __init__(self,cfg,rng,on_event):
        self.cfg=cfg; self.rng=rng; self.on_event=on_event
        self.packets={}; self.serial=0; self.busy=defaultdict(float); self.queued=defaultdict(int)
        self.radio_busy=defaultdict(float); self.radio_owner={}; self.reservations={}; self.heap=[]; self.order=0; self.now=0.
        self.generated=0; self.delivered=0; self.dropped=0; self.bytes_delivered=0; self.latency_sum=0.
        self.policy_dropped=defaultdict(int); self.radio_dropped=defaultdict(int)
        self.observations=deque(maxlen=20000)
        self.transmitted=defaultdict(int); self.received=defaultdict(int); self.forwarded=defaultdict(int)
        self.completions=deque(maxlen=20000)
        self.event_time=None

    def schedule(self,at,p,kind):
        self.order+=1; heapq.heappush(self.heap,(at,self.order,p.id,kind,p.repair_count))

    def event(self,kind,p,at,**extra):
        p.event_at=at
        # Callbacks may emit an acknowledgment at precisely the arrival time.
        previous=self.event_time; self.event_time=at
        try: self.on_event(kind,dict(p.snapshot(),**extra))
        finally: self.event_time=previous

    def draw(self,p,edge,purpose):
        seed=self.cfg.get('seed')
        if seed is None: return self.rng.random()
        key=f'{seed}:{p.source}:{p.destination}:{p.sequence}:{edge}:{p.hop}:{p.attempt}:{purpose}'
        return int.from_bytes(hashlib.blake2b(key.encode(),digest_size=8).digest(),'big')/2**64

    def emit(self,source,destination,category,size,route,now,revision,correlation=None,family='mesh',sequence=None,claimed_source_mac=None,restrictions=None):
        self.serial+=1; identity=f'p{self.serial}'; self.generated+=1
        p=Packet(identity,source,destination,category,size,route[:],now,now+self.cfg['ttl_s'],revision,
                 correlation_id=correlation,family=family,sequence=self.serial if sequence is None else sequence,
                 claimed_source_mac=claimed_source_mac,policy_source=source,event_at=now,restrictions=restrictions or {})
        if len(self.packets)>=self.cfg['capacity']:
            self.finish(p,'dropped','capacity',now); return p
        self.packets[identity]=p
        self.event('packet_queued',p,now)
        if len(route)<2: self.finish(p,'dropped','no_route',now)
        else:
            self.schedule(now,p,'ready'); self.schedule(p.expires_at,p,'expire')
        return p

    def release(self,p,now):
        reservation=self.reservations.pop(p.id,None)
        if reservation:
            edge,radios=reservation
            self.queued[edge]=max(0,self.queued[edge]-1)
            if not self.queued[edge]: self.busy[edge]=now
            if p.status=='in_flight' and now<p.end_at:
                # Cancellation cannot leave this packet's unused reservation behind.
                for radio in radios:
                    if self.radio_owner.get(radio)==p.id:
                        self.radio_busy[radio]=min(self.radio_busy[radio],now); self.radio_owner.pop(radio,None)

    def finish(self,p,status,reason,now):
        if p.status in ('delivered','dropped'): return
        self.release(p,now)
        p.status=status; p.reason=reason; p.completed_at=now
        if status=='delivered':
            self.delivered+=1; self.bytes_delivered+=p.size_bytes; self.latency_sum+=(now-p.created_at)*1000
        else: self.dropped+=1
        if p.hop>0 and p.hop<len(p.route)-1 and status=='dropped':
            self.observations.append((now,p.route[p.hop],'relay_drop',reason))
        self.completions.append(dict(source=p.source,destination=p.destination,category=p.category,
            created_at=p.created_at,completed_at=now,status=status,size_bytes=p.size_bytes,
            latency_ms=(now-p.created_at)*1000,hops=p.hop,reason=reason))
        self.event('packet_result',p,now)
        self.packets.pop(p.id,None)

    @staticmethod
    def channel(p,a,b):
        return ('satellite',*pair(a,b)) if p.family=='satellite' else pair(a,b)

    def step(self,now,links,allowed,relay_drop=None,repair=None):
        if now < self.now-1e-9: raise ValueError('packet clock cannot run backwards')
        # Validate packets at the observation boundary, including future reservations.
        for p in list(self.packets.values()):
            if p.hop>=len(p.route)-1: continue
            a,b=p.route[p.hop:p.hop+2]
            link=links(p,a,b) if callable(links) else links.get(pair(a,b))
            if not link or not allowed(p,a,b):
                self.release(p,now)
                if p.category in self.RELIABLE and repair and p.repair_count<self.cfg['retries'] and repair(p):
                    p.repair_count+=1; p.status='queued'; p.retries=0
                    self.event('packet_rerouted',p,now); self.schedule(now,p,'ready')
                else:
                    self.policy_dropped[a]+=1; self.finish(p,'dropped','policy_or_link_removed',now)
        while self.heap and self.heap[0][0]<=now+1e-12:
            at,_,identity,kind,generation=heapq.heappop(self.heap)
            p=self.packets.get(identity)
            if p is None or kind!='expire' and generation!=p.repair_count: continue
            if kind=='expire': self.finish(p,'dropped','expired',at); continue
            if at>=p.expires_at: self.finish(p,'dropped','expired',p.expires_at); continue
            a,b=p.route[p.hop:p.hop+2]; edge=self.channel(p,a,b)
            link=links(p,a,b) if callable(links) else links.get(pair(a,b))
            if not link or not allowed(p,a,b):
                self.policy_dropped[a]+=1; self.finish(p,'dropped','policy_or_link_removed',at); continue
            if kind=='arrival':
                # Stale arrivals from a canceled/repaired hop are ignored.
                if p.status!='in_flight' or abs(at-p.end_at)>1e-10: continue
                self.release(p,at); p.status='queued'
                if self.draw(p,edge,'loss')<link.loss:
                    self.radio_dropped[a]+=1; self.observations.append((at,a,'attempt_loss',edge))
                    self.event('packet_attempt_lost',p,at,from_node=a,to_node=b)
                    if p.category in self.RELIABLE and p.retries<self.cfg['retries']:
                        p.retries+=1; self.event('packet_retry',p,at,from_node=a,to_node=b)
                        self.schedule(at+self.cfg.get('retry_backoff_ms',2)/1000,p,'ready'); continue
                    self.finish(p,'dropped','radio_loss',at); continue
                self.received[b]+=1
                if p.hop==0:
                    self.observations.append((at,p.source,'received_message',p.sequence))
                    if p.claimed_source_mac: self.observations.append((at,p.source,'source_identity',p.claimed_source_mac))
                self.observations.append((at,a,'attempt_ok',edge))
                self.observations.append((at,a,'hop_latency',(edge,(at-p.start_at)*1000)))
                if p.hop>0:
                    self.forwarded[a]+=1; self.observations.append((at,a,'relay_ok',None))
                p.history.append(dict(from_node=a,to_node=b,start_at=p.start_at,end_at=at,attempt=p.attempt))
                self.event('packet_hop_completed',p,at,from_node=a,to_node=b)
                p.hop+=1; p.retries=0
                if p.hop==len(p.route)-1: self.finish(p,'delivered',None,at)
                else: self.schedule(at,p,'ready')
                continue
            if p.status=='in_flight': continue
            if relay_drop and relay_drop(p,a): self.finish(p,'dropped','relay_discard',at); continue
            radios=(('satellite',a),('satellite',b)) if p.family=='satellite' else (('radio',a),('radio',b))
            if p.id not in self.reservations:
                if self.queued[edge]>=self.cfg['queue_limit']:
                    self.radio_dropped[a]+=1; self.finish(p,'dropped','queue_overflow',at); continue
                self.queued[edge]+=1; self.reservations[p.id]=(edge,radios)
            available=max(at,*(self.radio_busy[r] for r in radios))
            if available>at+1e-12:
                self.schedule(available,p,'ready'); continue
            p.attempt+=1
            airtime=p.size_bytes*8/(link.bandwidth_mbps*1e6)
            jitter=self.cfg.get('jitter_ms',0)*self.draw(p,edge,'jitter')/1000
            p.start_at=at; p.end_at=at+airtime+link.latency_ms/1000+jitter
            for radio in radios: self.radio_busy[radio]=at+airtime; self.radio_owner[radio]=p.id
            self.busy[edge]=at+airtime; self.transmitted[a]+=1; p.status='in_flight'
            self.observations.append((at,a,'control' if p.category in ('command','ack','routing','discovery','heartbeat','emergency','threat') else 'data',None))
            self.event('packet_hop',p,at,from_node=a,to_node=b)
            self.schedule(p.end_at,p,'arrival')
        self.now=now
        if len(self.heap)>max(1000,self.cfg['capacity']*4):
            self.heap=[e for e in self.heap if e[2] in self.packets]; heapq.heapify(self.heap)

    def retire(self,identities,now):
        for p in list(self.packets.values()):
            if any(n in identities for n in p.route[p.hop:]): self.finish(p,'dropped','node_retired',now)
        for edge in list(self.busy):
            if any(n in identities for n in edge): self.busy.pop(edge,None); self.queued.pop(edge,None)
