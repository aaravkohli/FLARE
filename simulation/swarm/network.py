"""Modeled radio links, graph routing, and bounded per-hop packet scheduling."""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, fields, field
from copy import deepcopy
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
    def snapshot(self): return dict({name:getattr(self,name) for name in _LINK_FIELDS}, id=':'.join(pair(self.a,self.b)), encrypted='simulated_policy')

_LINK_FIELDS = tuple(f.name for f in fields(Link))

def radio_link(a,b,pa,pb,cfg,terrain,jammers,health=1.,offset=0.):
    distance=math.dist(pa,pb)
    if distance>cfg['range_m'] or health<=0: return None
    attenuation=0.
    clear = getattr(terrain, 'clears_heightfield', None)
    if not (math.isfinite(distance) and callable(clear) and clear(min(pa[1],pb[1]),3)):
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
    if source!=destination and (source not in adjacency or destination not in adjacency): return []
    queue=[(0.,(source,))]; visited=set(); best={source:(0.,(source,))}
    while queue:
        cost,path=heapq.heappop(queue); current=path[-1]
        if current in visited: continue
        if current==destination: return list(path)
        if current=='base' and current!=source: continue
        visited.add(current)
        for peer,weight in adjacency.get(current,[]):
            if peer not in visited:
                candidate=(cost+weight,path+(peer,))
                if peer not in best or candidate<best[peer]:
                    best[peer]=candidate
                    heapq.heappush(queue,candidate)
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
    service_deadline: float | None = None
    service_kind: str | None = None
    channel_key: str | None = None
    def snapshot(self):
        # Scalar fields are immutable. Copy the three mutable payloads, keeping
        # event history independent without deepcopy/reflection on every scalar.
        row={name:getattr(self,name) for name in _PACKET_FIELDS}
        row['route']=self.route[:]
        row['restrictions']=deepcopy(self.restrictions)
        row['history']=deepcopy(self.history)
        for key in ('service_deadline','service_kind','channel_key'):
            if row[key] is None: row.pop(key)
        return dict(row,protocol='reliable_simulated' if self.category in ('command','ack','fl_update') else 'udp_simulated')

_PACKET_FIELDS = tuple(f.name for f in fields(Packet))

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
        # Measurement cohorts are transport-specific; physical reservations below
        # intentionally remain shared by Direct and Mesh on the same radio.
        self.channel_observations=deque(maxlen=20000)
        self.transmitted=defaultdict(int); self.received=defaultdict(int); self.forwarded=defaultdict(int)
        self.completions=deque(maxlen=20000)
        self.event_time=None
        self.deferred=set()
        self.protected_scheduling=False
        self.protected_share=.10
        self.airtime_usage=defaultdict(dict)
        self.airtime_totals=defaultdict(float)
        self._ready_batch=deque()

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
        if p.channel_key is not None:
            key=f'{seed}:{p.source}:{p.destination}:{p.channel_key}:{edge}:{p.hop}:{p.attempt}:{purpose}'
        return int.from_bytes(hashlib.blake2b(key.encode(),digest_size=8).digest(),'big')/2**64

    def emit(self,source,destination,category,size,route,now,revision,correlation=None,family='mesh',sequence=None,claimed_source_mac=None,restrictions=None,defer=False,service=None,channel_key=None,expires_at=None):
        self.serial+=1; identity=f'p{self.serial}'; self.generated+=1
        p=Packet(identity,source,destination,category,size,route[:],now,now+self.cfg['ttl_s'],revision,
                 correlation_id=correlation,family=family,sequence=self.serial if sequence is None else sequence,
                 claimed_source_mac=claimed_source_mac,policy_source=source,event_at=now,restrictions=restrictions or {})
        if service:
            p.service_kind=service['kind']; p.service_deadline=min(p.expires_at,service['deadline'])
        p.channel_key=channel_key
        if expires_at is not None: p.expires_at=min(p.expires_at,expires_at)
        if len(self.packets)>=self.cfg['capacity']:
            self.finish(p,'dropped','capacity',now); return p
        self.packets[identity]=p
        self.event('packet_queued',p,now)
        if defer:
            # Bound admission by the existing source queue limit as well as global capacity.
            if sum(self.packets[n].source==source for n in self.deferred)>=self.cfg['queue_limit']:
                self.finish(p,'dropped','queue_overflow',now)
            else:
                self.deferred.add(identity); p.status='deferred'
                self.schedule(p.expires_at,p,'expire')
                self.event('packet_deferred',p,now)
        elif len(route)<2: self.finish(p,'dropped','no_route',now)
        else:
            self.schedule(now,p,'ready'); self.schedule(p.expires_at,p,'expire')
        return p

    def resume_deferred(self,p,route,now,revision):
        if p.id not in self.deferred: return
        self.deferred.discard(p.id)
        if now>=p.expires_at:
            self.finish(p,'dropped','expired',p.expires_at); return
        p.route=route[:]; p.revision=revision; p.status='queued'
        self.event('packet_released',p,now)
        if len(route)<2: self.finish(p,'dropped','no_route',now)
        else: self.schedule(now,p,'ready')

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
        self.deferred.discard(p.id)
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

    def protected_due(self,radios,at):
        """A rolling one-second, 50-ms-bucket service deficit, never idle padding."""
        due=True
        for radio in radios:
            buckets=self.airtime_usage[radio]; cutoff=math.floor((at-1)*20)
            for key in list(buckets):
                if key<cutoff: buckets.pop(key)
            protected=sum(v[0] for v in buckets.values()); total=sum(sum(v) for v in buckets.values())
            due&=protected<=self.protected_share*total+1e-12
        return due

    def pop_event(self):
        if not self.protected_scheduling: return heapq.heappop(self.heap)
        at=self._ready_batch[0][0] if self._ready_batch else self.heap[0][0]
        if self._ready_batch and (not self.heap or self.heap[0][0]>at+1e-12): return self._ready_batch.popleft()
        batch=list(self._ready_batch); self._ready_batch.clear()
        while self.heap and self.heap[0][0]<=at+1e-12: batch.append(heapq.heappop(self.heap))
        def key(event):
            _,order,identity,kind,generation=event; p=self.packets.get(identity)
            if kind!='ready' or p is None or generation!=p.repair_count: return (-1,0,order)
            if not p.service_kind or p.hop+1>=len(p.route): return (1,0,order)
            a,b=p.route[p.hop:p.hop+2]
            radios=(('satellite',a),('satellite',b)) if p.family=='satellite' else (('radio',a),('radio',b))
            return (0 if self.protected_due(radios,at) else 2,p.service_deadline or p.expires_at,order)
        # A contended half-duplex radio admits only one start at this timestamp;
        # losers requeue at its exact release. Sort that ready cohort once.
        self._ready_batch=deque(sorted(batch,key=key))
        return self._ready_batch.popleft()

    def step(self,now,links,allowed,relay_drop=None,repair=None):
        if now < self.now-1e-9: raise ValueError('packet clock cannot run backwards')
        self.checking_at=now
        next_events={}
        if self.protected_scheduling:
            for at,_,identity,kind,generation in self.heap:
                packet=self.packets.get(identity)
                if packet and (kind=='expire' or generation==packet.repair_count):
                    next_events[identity]=min(at,next_events.get(identity,at))
        # Validate packets at the observation boundary, including future reservations.
        for p in list(self.packets.values()):
            self.checking_at=min(now,next_events.get(p.id,now))
            if p.id in self.deferred: continue
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
        while self._ready_batch or self.heap and self.heap[0][0]<=now+1e-12:
            at,_,identity,kind,generation=self.pop_event()
            self.checking_at=at
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
                    self.channel_observations.append((at,p.family,pair(a,b),False,None))
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
                self.channel_observations.append((at,p.family,pair(a,b),True,(at-p.start_at)*1000))
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
                limit=self.cfg['queue_limit']
                if self.protected_scheduling and not p.service_kind:
                    reserved=max(1,int(limit*self.protected_share)) if limit>1 else 0
                    protected=sum(1 for identity,(channel,_) in self.reservations.items() if channel==edge and self.packets.get(identity) and self.packets[identity].service_kind)
                    limit-=max(0,reserved-protected)
                if self.queued[edge]>=limit:
                    self.radio_dropped[a]+=1; self.finish(p,'dropped','queue_overflow',at); continue
                self.queued[edge]+=1; self.reservations[p.id]=(edge,radios)
            available=max(at,*(self.radio_busy[r] for r in radios))
            if available>at+1e-12:
                self.schedule(available,p,'ready'); continue
            p.attempt+=1
            airtime=p.size_bytes*8/(link.bandwidth_mbps*1e6)
            if self.protected_scheduling:
                for radio in radios:
                    bucket=math.floor(at*20); usage=self.airtime_usage[radio]
                    for expired in list(usage):
                        if expired<bucket-20: usage.pop(expired)
                    usage.setdefault(bucket,[0.,0.])[0 if p.service_kind else 1]+=airtime
            # Measurement must include unreserved control arms too. This counter
            # does not grant priority or participate in admission decisions.
            self.airtime_totals['protected' if p.service_kind else 'ordinary']+=airtime*len(radios)
            jitter=self.cfg.get('jitter_ms',0)*self.draw(p,edge,'jitter')/1000
            p.start_at=at; p.end_at=at+airtime+link.latency_ms/1000+jitter
            for radio in radios: self.radio_busy[radio]=at+airtime; self.radio_owner[radio]=p.id
            self.busy[edge]=at+airtime; self.transmitted[a]+=1; p.status='in_flight'
            self.observations.append((at,a,'control' if p.category in ('command','ack','routing','discovery','heartbeat','emergency','threat') else 'data',None))
            self.event('packet_hop',p,at,from_node=a,to_node=b)
            self.schedule(p.end_at,p,'arrival')
        self.now=now
        self.checking_at=now
        if len(self.heap)>max(1000,self.cfg['capacity']*4):
            self.heap=[e for e in self.heap if e[2] in self.packets]; heapq.heapify(self.heap)

    def retire(self,identities,now):
        for p in list(self.packets.values()):
            if any(n in identities for n in p.route[p.hop:]) or p.id in self.deferred and (p.source in identities or p.destination in identities): self.finish(p,'dropped','node_retired',now)
        for edge in list(self.busy):
            if any(n in identities for n in edge): self.busy.pop(edge,None); self.queued.pop(edge,None)
