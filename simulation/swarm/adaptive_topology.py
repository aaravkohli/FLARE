"""Deterministic, simulation-clock topology policy and bounded transition coordinator.

Scores are counterfactual radio/traffic estimates, not calibrated energy measures.
Candidate evaluation owns copies of topology state and never draws randomness.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import copy, deepcopy
from dataclasses import dataclass, field
import heapq
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas.mission import CONTROL_TRAFFIC, TOPOLOGIES
from simulation.swarm.network import build_adjacency, shortest_path
from simulation.swarm.topology import pair, topology_edges


class AdaptivePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    evaluation_s: float = Field(default=1, ge=1)
    window_s: float = Field(default=10, ge=1, le=60)
    minimum_observations: int = Field(default=8, ge=1)
    improvement: float = Field(default=.08, ge=0, le=1)
    consecutive_wins: int = Field(default=3, ge=1)
    cooldown_s: float = Field(default=15, ge=0)
    emergency_loss: float = Field(default=.10, gt=0, le=1)
    emergency_rebuilds: int = Field(default=2, ge=2)
    emergency_interval_s: float = Field(default=2, ge=2)
    blacklist_s: float = Field(default=30, ge=0)
    drain_s: float = Field(default=1, gt=0, le=1)
    validation_rebuilds: int = Field(default=2, ge=2)
    headroom: float = Field(default=.20, ge=0, lt=1)
    transition_penalty: float = Field(default=.05, ge=0, le=.05)
    weights: dict[str, float] = Field(default_factory=lambda: dict(reliability=.35, latency=.25, capacity=.25, efficiency=.15))

    @model_validator(mode='after')
    def weights_sum(self):
        if set(self.weights) != {'reliability', 'latency', 'capacity', 'efficiency'} or any(not 0 <= x <= 1 for x in self.weights.values()) or not math.isclose(sum(self.weights.values()), 1):
            raise ValueError('balanced weights must be nonnegative and sum to one')
        return self


@dataclass
class Candidate:
    topology: str
    edges: set
    heads: list
    state: dict
    clusters: dict
    links: dict
    data_pairs: set = field(default_factory=set)
    control_nodes: set = field(default_factory=set)
    required: set = field(default_factory=set)
    factors: dict = field(default_factory=dict)
    score: float = 0.
    penalty: float = 0.
    observed_edges: int = 0
    modeled_edges: int = 0
    admissible: bool = True
    resilience: dict | None = None
    service_admissible: bool = True
    services: dict = field(default_factory=dict)

    @property
    def connectivity(self):
        return len(self.data_pairs) + len(self.control_nodes)

    def diagnostic(self):
        result=dict(topology=self.topology, score=self.score, net_score=self.score-self.penalty,
                    transition_penalty=self.penalty, factors=self.factors,
                    data_pairs=len(self.data_pairs), control_routes=len(self.control_nodes),
                    edges=len(self.edges), admissible=self.admissible,
                    observed_edges=self.observed_edges, estimated_edges=self.modeled_edges)
        if self.resilience is not None: result.update(resilience=self.resilience,service_admissible=self.service_admissible)
        return result


def paths_from(adjacency, source, targets=None):
    """Same trust-aware cost/tie ordering as routing; base is never a relay."""
    best={source:(0., (source,))}; queue=[(0., (source,))]; done={}
    while queue:
        cost,path=heapq.heappop(queue); node=path[-1]
        if node in done: continue
        done[node]=path
        if targets is not None and targets<=done.keys(): break
        if node=='base' and source!='base': continue
        for peer,weight in adjacency.get(node, ()):
            if peer in done: continue
            next_cost=cost+weight
            previous=best.get(peer)
            if previous is not None and next_cost>previous[0]: continue
            candidate=(next_cost, path+(peer,))
            if previous is None or candidate<previous:
                best[peer]=candidate; heapq.heappush(queue,candidate)
    return done


def paths_to_base(adjacency):
    """One reverse distance calculation, retaining forward lexical tie-breaking."""
    distances={'base':0.}; queue=[(0.,'base')]
    while queue:
        cost,node=heapq.heappop(queue)
        if cost>distances[node]: continue
        for peer,weight in adjacency.get(node,()):
            candidate=cost+weight
            if candidate<distances.get(peer,math.inf):
                distances[peer]=candidate;heapq.heappush(queue,(candidate,peer))
    routes={}
    for source in sorted(distances):
        path=[source]
        while path[-1]!='base':
            node=path[-1]
            choices=[peer for peer,weight in adjacency.get(node,()) if peer in distances and distances[peer]<distances[node] and math.isclose(weight+distances[peer],distances[node],rel_tol=0,abs_tol=1e-10)]
            if not choices: break
            path.append(min(choices))
        if path[-1]=='base': routes[source]=tuple(path)
    return routes


def demand_paths(adjacency, rates):
    targets=defaultdict(set)
    for source,dest,_ in rates:
        if dest!='base': targets[source].add(dest)
    result={source:paths_from(adjacency,source,dests) for source,dests in sorted(targets.items())}
    for source,route in paths_to_base(adjacency).items(): result.setdefault(source,{})['base']=route
    return result


def redundant_components(adjacency):
    """Bridge-free components identify pairs with an edge-disjoint alternative."""
    discovered={}; low={}; bridges=set(); serial=0
    def visit(node, parent=None):
        nonlocal serial
        serial+=1; discovered[node]=low[node]=serial
        for peer,_ in adjacency.get(node, ()):
            if peer==parent: continue
            if peer not in discovered:
                visit(peer,node); low[node]=min(low[node],low[peer])
                if low[peer]>discovered[node]: bridges.add(pair(node,peer))
            else: low[node]=min(low[node],discovered[peer])
    for node in sorted(adjacency):
        if node not in discovered: visit(node)
    components={}
    for node in sorted(adjacency):
        if node in components: continue
        stack=[node]; components[node]=node
        while stack:
            current=stack.pop()
            for peer,_ in adjacency.get(current, ()):
                if peer not in components and pair(current,peer) not in bridges:
                    components[peer]=node; stack.append(peer)
    return components


def connectivity_state(links, nodes, blocked):
    """Linear graph traversal for rebuild-level alarms, with no base transit."""
    # Reachability does not require trust-weighted Dijkstra costs. Keep the same
    # eligibility rules without recomputing weights at every 200 ms rebuild.
    unsafe={n for n,d in nodes.items() if not d.available or d.containment=='quarantined' or (d.threat or 0)>.8}
    data_unsafe=unsafe|{n for n,d in nodes.items() if d.containment in ('restricted','control_only') or d.selected_route=='hold'}
    data=defaultdict(list);control=defaultdict(list)
    for edge,link in links.items():
        if edge in blocked or link.loss>.35: continue
        a,b=edge
        if a not in unsafe and b not in unsafe:
            control[a].append((b,0));control[b].append((a,0))
            if a not in data_unsafe and b not in data_unsafe:
                data[a].append((b,0));data[b].append((a,0))
    return connectivity_from_adjacencies(data,control,nodes)


def connectivity_from_adjacencies(data,control,nodes):
    seen=set(); pairs=set()
    for node in sorted(nodes):
        if node in seen: continue
        component={node}; stack=[node]; seen.add(node)
        while stack:
            current=stack.pop()
            for peer,_ in data.get(current,()):
                if peer!='base' and peer not in seen:
                    component.add(peer); seen.add(peer); stack.append(peer)
        pairs.update((a,b) for a in component for b in component if a<b)
    reachable=set(); stack=['base']
    while stack:
        current=stack.pop()
        for peer,_ in control.get(current,()):
            if peer not in reachable and peer!='base': reachable.add(peer); stack.append(peer)
    return pairs,reachable


class AdaptiveTopology:
    def __init__(self, engine):
        self.engine=engine
        self.policy=AdaptivePolicy.model_validate(engine.cfg.get('adaptive_topology', {}))
        # Aggregate offers sharing an exact simulation timestamp. Busy attack
        # traffic cannot evict earlier demand and make congestion look lighter.
        self.offered=deque()
        self.hypotheses={}; self.candidates={}; self.blacklist={}
        self.last_evaluation=None; self.last_switch=None; self.last_emergency=None
        self.winner=None; self.wins=0; self.loss_rebuilds=0
        self.reason='Manual override' if engine.topology_mode=='manual' else 'Evaluating initial radio graph'
        self.transition=None; self.last_transition='idle'; self.fallback=None

    def offer(self, source, destination, category, size, at):
        cutoff=at-self.policy.window_s
        while self.offered and self.offered[0][0]<cutoff: self.offered.popleft()
        if not self.offered or self.offered[-1][0]!=at: self.offered.append((at,defaultdict(float)))
        self.offered[-1][1][source,destination,category]+=size

    def demands(self):
        rates,evidence=self.base_demands()
        if self.engine.observability.enabled:
            rates=dict(rates)
            for key,value in self.engine.observability.demands().items(): rates.setdefault(key,value)
        return rates,evidence

    def base_demands(self):
        e=self.engine; cutoff=e.seconds-self.policy.window_s
        while self.offered and self.offered[0][0]<cutoff: self.offered.popleft()
        if self.offered:
            rates=defaultdict(float)
            duration=max(1., min(self.policy.window_s,e.seconds))
            for _,offers in self.offered:
                for key,size in offers.items(): rates[key]+=size/duration
            return rates, 'offered_mesh_traffic'
        if e.cfg.get('reference_nominal_demand'):
            if e.seconds>=e.cfg.get('traffic_stop_s',float('inf')): return {}, 'no_offered_reference_traffic'
            from simulation.swarm.reference_workload import nominal_demands
            return nominal_demands(e), 'nominal_reference_schedule_estimate'
        rates=defaultdict(float); ids=list(e.nodes)
        # Long-run rates of the existing mission traffic schedule, not new traffic.
        for i,n in enumerate(ids):
            if not e.nodes[n].available: continue
            rates[n,ids[(i+1)%len(ids)],'position']+=128
            rates[n,ids[(i+3)%len(ids)],'sensor']+=768/3
            if not e.cfg.get('mesh_only'):
                rates[n,'base','heartbeat']+=64
                if e.families.get(n,'mesh')=='mesh': rates[n,'base','telemetry']+=512
            peers=sorted(peer for edge in e.physical_links if n in edge for peer in edge if peer!=n and peer!='base')
            if peers:
                rates[n,peers[0],'discovery']+=64/5
                rates[n,peers[0],'routing']+=96/7
        return rates, 'nominal_schedule_estimate'

    def candidate(self, name, physical, rates, measurements, impaired=None):
        e=self.engine
        rates=dict(rates)
        assist=e.assistance.enabled and any(n['degraded'] for n in e.assistance.nodes.values())
        if assist:
            for flow in e.assistance.flows:rates.setdefault(flow,0.)
            for n,state in e.assistance.nodes.items():
                if state['degraded']:rates.setdefault((n,'base','heartbeat'),0.)
        # Idle installed policies still constrain connectivity, without adding
        # invented offered traffic or biasing the traffic-weighted score.
        for flow in e.flows.values():
            if flow['family']=='mesh': rates.setdefault((flow['source'],flow['destination'],flow['traffic_class']),0.)
        # Topology generation writes only the scalar cluster field. Nested flight
        # state is read-only here; a private shallow node preserves that contract.
        clones=[copy(node) for node in e.nodes.values()]
        previous,state=self.hypotheses.get(name,(set(),{}))
        if name==e.topology: previous,state=set(e.links),e.topology_state
        state=deepcopy(state)
        # Structural rules use the same raw radio graph as the live generator;
        # forwarding eligibility is enforced separately in every scored path.
        raw=e.physical_links
        permitted={edge:link for edge,link in (physical if name=='self_healing_mesh' else raw).items() if 'base' not in edge}
        edges,heads=topology_edges(name,clones,permitted,previous,state,e.seconds,e.cfg.get('discovery_timeout_s',.6))
        impaired=impaired if impaired is not None else {edge:e.attacks.impair(link,'mesh',edge) for edge,link in raw.items()}
        links={edge:impaired[edge] for edge in edges}
        links.update({edge:link for edge,link in impaired.items() if 'base' in edge})
        if 'mesh' in e.failed_families: links={}
        c=Candidate(name,edges,heads,state,{n.id:n.cluster for n in clones},links)
        # Evaluation state is private. It supports stable hypothetical head discovery.
        self.hypotheses[name]=(set(edges),deepcopy(state))
        nodes={n.id:n for n in clones}
        adj=build_adjacency(links,nodes, e.blocked|e.auto_blocked)
        control=build_adjacency(links,nodes,e.blocked|e.auto_blocked,control=True)
        paths=demand_paths(adj,rates)
        controls=paths if control==adj else demand_paths(control,rates)
        c.data_pairs,c.control_nodes=connectivity_from_adjacencies(adj,control,nodes)
        peer_adj={n:[(p,w) for p,w in peers if p!='base'] for n,peers in adj.items() if n!='base'}
        redundant=redundant_components(peer_adj); base_redundant=redundant_components(adj)
        control_redundant=base_redundant if control==adj else redundant_components(control)
        control_peer=redundant if control==adj else redundant_components({n:[(p,w) for p,w in peers if p!='base'] for n,peers in control.items() if n!='base'})
        total=sum(rates.values()) or 1.; utilities=defaultdict(float)
        radios=defaultdict(float); transit=defaultdict(float)
        observed=set(); estimated=set(); demand_routes={}
        for (source,dest,category),rate in sorted(rates.items()):
            is_control=category in CONTROL_TRAFFIC
            route=list((controls if is_control else paths).get(source,{}).get(dest,()))
            constraints=e.flow_constraints.get(f'{source}:{dest}:{category}',{})
            excluded=set(constraints.get('excluded_nodes',[])); blocked={pair(*edge) for edge in constraints.get('excluded_links',[])}
            if excluded.intersection(route) or any(pair(a,b) in blocked for a,b in zip(route,route[1:])):
                route=shortest_path({edge:l for edge,l in links.items() if not excluded.intersection(edge)},source,dest,nodes,e.blocked|e.auto_blocked|blocked,control=is_control)
            if category not in constraints.get('allowed_traffic_classes',[category]): route=[]
            if assist and route and (e.autonomy.enabled or e.autonomy.interfaces) and not e.autonomy.permitted(route,'mesh',constraints):
                permitted={edge:link for edge,link in links.items() if e.autonomy.permitted(list(edge),'mesh') and not excluded.intersection(edge)}
                route=shortest_path(permitted,source,dest,nodes,e.blocked|e.auto_blocked|blocked,control=is_control)
            if len(route)<2 or 'mesh' in e.failed_families: continue
            c.required.add((source,dest,category))
            demand_routes[source,dest,category]=route
            delivery=1.; delay=0.
            for a,b in zip(route,route[1:]):
                edge=pair(a,b); link=links[edge]; samples=measurements.get(edge,())
                airtime=rate*8/(max(.000001,link.bandwidth_mbps)*1e6)
                radios[a]+=airtime; radios[b]+=airtime
                if a!=source: transit[a]+=airtime
                if b!=dest: transit[b]+=airtime
                if len(samples)>=self.policy.minimum_observations:
                    successful=[latency for ok,latency in samples if ok and latency is not None]
                    delivery*=sum(ok for ok,_ in samples)/len(samples)
                    delay+=sum(successful)/len(successful) if successful else link.latency_ms
                    observed.add(edge)
                else:
                    delivery*=1-link.loss
                    delay+=link.latency_ms+512*8/(max(.000001,link.bandwidth_mbps)*1000)+e.cfg['packets'].get('jitter_ms',0)/2
                    estimated.add(edge)
            components=(control_redundant if is_control else base_redundant) if 'base' in (source,dest) else (control_peer if is_control else redundant)
            alternative=source in components and components.get(source)==components.get(dest)
            if assist:c.services[source,dest,category]=dict(delivery=delivery,reliability=delivery*(.8+.2*alternative),delay_ms=delay,route=route[:],capacity=0.)
            utilities['reliability']+=rate*delivery*(.8+.2*alternative)/total
            utilities['latency']+=rate/(1+delay/50)/total
        capacity=0.
        for key,rate in rates.items():
            if key not in c.required: continue
            route=demand_routes[key]
            load=max((radios[n] for n in route),default=0.)
            utility=min(1.,(1-self.policy.headroom)/max(1e-12,load))
            if assist:c.services[key]['capacity']=utility
            capacity+=rate*utility/total
        available=sum(n.available and n.health>0 and n.containment!='quarantined' for n in clones)
        maintenance=min(1.,len(edges)/max(1,8*available))
        burden=min(1.,max((use/max(.01,nodes[n].battery) for n,use in transit.items() if n in nodes),default=0.))
        c.factors={k:max(0.,min(1.,v)) for k,v in dict(reliability=utilities['reliability'],latency=utilities['latency'],capacity=capacity,efficiency=1-(maintenance+burden)/2).items()}
        c.score=sum(self.policy.weights[k]*v for k,v in c.factors.items())
        incumbent={edge for edge in e.links if 'base' not in edge}
        c.penalty=self.policy.transition_penalty*len(edges^incumbent)/max(1,len(edges|incumbent)) if name!=e.topology and (e.seconds>0 or e.scheduler.generated) else 0.
        c.observed_edges=len(observed); c.modeled_edges=len(estimated)
        return c

    def evaluate(self, force=False):
        e=self.engine; now=e.seconds
        if not force and self.last_evaluation is not None and now-self.last_evaluation<self.policy.evaluation_s-1e-9: return
        with e.performance.measure('selection'): return self._evaluate(now)
    def _evaluate(self,now):
        e=self.engine
        self.last_evaluation=now
        e.assistance.update(now)
        rates,self.demand_evidence=self.demands(); measurements=defaultdict(list)
        for at,family,edge,ok,latency in e.scheduler.channel_observations:
            if family=='mesh' and now-self.policy.window_s<=at<=now: measurements[edge].append((ok,latency))
        physical=e.permitted_physical_links()
        names=list(TOPOLOGIES)+([e.topology] if e.topology not in TOPOLOGIES else [])
        impaired={edge:e.attacks.impair(link,'mesh',edge) for edge,link in e.physical_links.items()}
        self.candidates={name:self.candidate(name,physical,rates,measurements,impaired) for name in names}
        incumbent=self.candidates[e.topology]
        for c in self.candidates.values():
            c.admissible=incumbent.data_pairs<=c.data_pairs and incumbent.control_nodes<=c.control_nodes and incumbent.required<=c.required
        if e.resilience.enabled:
            assessments={name:e.resilience.assess(c) for name,c in self.candidates.items()}
            current=assessments[e.topology]
            for name,c in self.candidates.items():
                assessment=assessments[name]
                c.service_admissible=(not e.resilience.packet_aware or assessment['missed_packets']<=current['missed_packets']) and (not e.resilience.observation_guard or current['feasible_reports']<=assessment['feasible_reports'])
                c.resilience=e.resilience.public_assessment(assessment)
        e.assistance.choose(self.candidates,incumbent)
        return self.best()

    def best(self,emergency=False):
        e=self.engine
        permitted=self.candidates.values() if emergency else e.assistance.ordinary_allowed(self.candidates,self.candidates[e.topology])
        choices=[c for c in permitted if c.admissible and (c.service_admissible or emergency) and c.topology in TOPOLOGIES and self.blacklist.get(c.topology,-1)<=e.seconds]
        if emergency and e.resilience.enabled:
            return max(choices,key=lambda c:(c.connectivity,-(c.resilience or {}).get('missed_packet_count',0),len((c.resilience or {}).get('feasible_reports',[])),c.score-c.penalty,c.topology==e.topology,-TOPOLOGIES.index(c.topology)),default=None)
        return max(choices,key=lambda c:(c.connectivity,c.score-c.penalty,c.topology==e.topology,-TOPOLOGIES.index(c.topology)),default=None)

    def request(self, target, reason, automatic=False):
        e=self.engine
        if target==e.topology:
            self.reason=reason; return
        if self.transition: self.cancel('Superseded by a new topology request')
        guarded=None
        assisted=bool(e.assistance.proposal and e.assistance.proposal['target']==target and automatic and reason.startswith('Affected UAV'))
        if e.autonomy.enabled or assisted:
            guarded=self.candidate(target,e.permitted_physical_links(),self.demands()[0],{})
            try:e.autonomy.start_guard(target,guarded,e.assistance.flows_to_verify(guarded) if assisted else None)
            except ValueError as exc:
                if not automatic:raise
                self.last_transition='rejected';self.reason=str(exc);self.blacklist[target]=e.seconds+self.policy.blacklist_s
                e.autonomy.counters['prevented_transitions']+=1
                e.event('RECOVERY','automatic_transition_rejected',dict(target=target,reason=str(exc)));return
        self.transition=dict(target=target,previous=e.topology,previous_state=deepcopy(e.topology_state),
                             phase='draining',started=e.seconds,deadline=e.seconds+self.policy.drain_s,
                             affected={p.id for p in e.scheduler.packets.values() if p.family=='mesh'},
                             checks=0,reason=reason,automatic=automatic,prepared=None,assisted=assisted)
        if assisted:e.assistance.requested()
        self.last_transition='draining'; self.reason=reason; e.revision+=1
        e.event('NETWORK','topology_transition_started',dict(target=target,previous=e.topology,reason=reason,deadline=self.transition['deadline']))
        try:
            self.transition['prepared']=guarded or self.candidate(target,e.permitted_physical_links(),self.demands()[0],{})
            if guarded:
                self.transition['phase']='verifying';self.last_transition='verifying'
            elif e.resilience.packet_aware:
                e.resilience.prepare(self.transition,self.transition['prepared']); self.last_transition='preparing'
        except (ValueError,RuntimeError) as exc: self.fail(str(exc))

    def cancel(self, reason):
        if not self.transition: return
        if self.transition.get('assisted'):self.engine.assistance.completed(False,reason,cancelled=True)
        self.engine.resilience.complete('cancelled')
        self.engine.autonomy.guard=None
        self.transition=None; self.last_transition='cancelled'; self.winner=None; self.wins=0
        self.engine.event('NETWORK','topology_transition_cancelled',{'reason':reason})
        self.release_deferred()

    def release_deferred(self):
        e=self.engine
        identities=list(e.scheduler.deferred)
        # Set iteration depends on process hash randomization. Admission order
        # affects shared-radio contention, so use stable original offer order.
        # Apply to both policies: the control must be reproducible as well.
        identities.sort(key=lambda identity:(e.scheduler.packets[identity].created_at,int(identity[1:])) if identity in e.scheduler.packets else (float('inf'),0))
        for identity in identities:
            p=e.scheduler.packets.get(identity)
            if not p: continue
            try: route=e.ensure_route(p.source,p.destination,p.category,p.family)
            except (ValueError, RuntimeError): route=[]
            e.scheduler.resume_deferred(p,route,e.seconds,e.network_revision)

    def before_rebuild(self):
        t=self.transition; e=self.engine
        if t and t['phase']=='verifying':
            outcome=e.autonomy.guard_step()
            if outcome=='failed':raise RuntimeError('Replacement path round trips were not confirmed before deadline')
            if outcome!='ready':return False
            t['verified_paths']=[r[-1]['path'][:] for r in e.autonomy.guard['receipts'].values() if r]
            g=e.autonomy.guard
            t['verified_flows']=[((*g['flows'][key],g.get('categories',{}).get(key,'routing')),r[-1]['path'][:]) for key,r in g['receipts'].items() if r]
            if t.get('assisted') and e.assistance.attempt:
                e.assistance.attempt.update(verified_at=e.seconds,verified_routes=len(g['required']),minimum_probe_spacing_s=min((rows[1]['sent_at']-rows[0]['sent_at'] for rows in g['receipts'].values() if len(rows)>=2),default=None))
            t['deadline']=e.seconds+self.policy.drain_s
            e.resilience.prepare(t,t['prepared']);e.autonomy.guard=None;self.last_transition='preparing'
        if t and t.get('enhanced'):
            if t['phase']=='overlap':
                if e.seconds>=t['deadline']-1e-9 or not set(e.resilience.leases).intersection(e.scheduler.packets):
                    e.resilience.retire(t); t['committed_at']=e.seconds
                    self.last_switch=e.seconds; self.last_transition='validating'; e.revision+=1
                    return True
                return False
            if t['phase']=='preparing':
                c=self.candidate(t['target'],e.permitted_physical_links(),self.demands()[0],{})
                if (e.autonomy.enabled or t.get('assisted')) and any(not e.autonomy.permitted(path,'mesh') or any(pair(a,b) not in c.links for a,b in zip(path,path[1:])) for path in t.get('verified_paths',[])):
                    raise RuntimeError('Verified replacement changed before forwarding commit')
                if t.get('assisted') and any(not e.autonomy.verifier.flow_permitted(flow,path) for flow,path in t.get('verified_flows',[])):
                    raise RuntimeError('Verified flow permission changed before forwarding commit')
                if t.get('assisted'):
                    incumbent=self.candidate(e.topology,e.permitted_physical_links(),self.demands()[0],{})
                    old,new=e.assistance.metrics(incumbent),e.assistance.metrics(c)
                    affected=[n for n,s in e.assistance.nodes.items() if s['degraded']]
                    if e.assistance.bound(c,incumbent,new,old,affected):raise RuntimeError('Assisted cost limits changed before commit')
                    node=e.assistance.attempt['node']
                    if new['utilities'].get(node,0)-old['utilities'].get(node,0)<.10+c.penalty-1e-12:raise RuntimeError('Assisted target improvement no longer exceeds transition margin')
                if t['automatic']:
                    incumbent=self.candidate(e.topology,e.permitted_physical_links(),self.demands()[0],{})
                    if not (incumbent.data_pairs<=c.data_pairs and incumbent.control_nodes<=c.control_nodes and incumbent.required<=c.required):
                        raise RuntimeError('Prepared topology no longer preserves permitted connectivity')
                    new,old=e.resilience.assess(c),e.resilience.assess(incumbent)
                    safe=(not e.resilience.packet_aware or new['missed_packets']<=old['missed_packets']) and (not e.resilience.observation_guard or old['feasible_reports']<=new['feasible_reports'])
                    if not safe and not t.get('emergency'):
                        raise RuntimeError('Prepared transition no longer preserves modeled service budgets')
                t['prepared']=c; e.resilience.begin(t,c); e.revision+=1
                self.last_transition=t['phase']
                if t['phase']=='validating': self.last_switch=e.seconds; t['committed_at']=e.seconds
                return True
            return False
        if not t or t['phase']!='draining': return False
        if t['affected'].intersection(e.scheduler.packets) and e.seconds<t['deadline']-1e-9: return False
        # Re-prepare against current physical conditions, never a stale saved graph.
        c=self.candidate(t['target'],e.permitted_physical_links(),self.demands()[0],{})
        if t['automatic']:
            incumbent=self.candidate(e.topology,e.permitted_physical_links(),self.demands()[0],{})
            if not (incumbent.data_pairs<=c.data_pairs and incumbent.control_nodes<=c.control_nodes and incumbent.required<=c.required):
                raise RuntimeError('Prepared topology no longer preserves permitted connectivity')
        t['prepared']=c; t['phase']='validating'; t['committed_at']=e.seconds
        e.topology=c.topology; e.topology_state=deepcopy(c.state)
        self.last_switch=e.seconds; self.last_transition='validating'; e.revision+=1
        return True

    def after_rebuild(self, committed=False):
        e=self.engine; t=self.transition
        e.assistance.update(e.seconds)
        if t and t['phase'] in ('validating','overlap'):
            try:
                # Reinstall mesh routes together; no packet history or counters are restored.
                failed=False
                for flow in list(e.flows.values()):
                    if flow['family']!='mesh': continue
                    if flow['traffic_class']=='telemetry' and flow['destination']=='base' and e.families.get(flow['source'],'mesh')!='mesh': continue
                    expected=e.policy_route(flow['source'],flow['destination'],flow['traffic_class'])
                    installed=e.ensure_route(flow['source'],flow['destination'],flow['traffic_class'])
                    failed|=bool(expected and installed!=expected)
                for source,dest,category in t['prepared'].required:
                    current=e.installed_rule(source,dest,category)
                    if current and current['family']!='mesh': continue
                    if category=='telemetry' and dest=='base' and e.families.get(source,'mesh')!='mesh': continue
                    expected=e.policy_route(source,dest,category)
                    if expected and not e.ensure_route(source,dest,category): failed=True
                    if not expected and t['automatic']:
                        constraints=e.flow_constraints.get(f'{source}:{dest}:{category}',{})
                        omit=set(constraints.get('excluded_nodes',[]))
                        blocked={pair(*edge) for edge in constraints.get('excluded_links',[])}
                        possible=shortest_path({edge:l for edge,l in e.permitted_physical_links().items() if not omit.intersection(edge)},source,dest,e.nodes,e.blocked|e.auto_blocked|blocked,control=category in CONTROL_TRAFFIC)
                        if possible: failed=True
                if failed: raise RuntimeError('Forwarding installation was not confirmed')
            except (ValueError, RuntimeError) as exc:
                self.fail(str(exc)); return
            if t['phase']=='overlap':
                self.release_deferred()
                return
            if committed:
                e.event('NETWORK','topology_transition_committed',dict(topology=e.topology,previous=t['previous']))
                self.release_deferred()
            else: t['checks']+=1
            if t['checks']>=self.policy.validation_rebuilds:
                if t.get('assisted'):e.assistance.completed(True)
                self.last_transition='idle'; self.transition=None
                self.reason=t['reason']; self.winner=None; self.wins=0; self.fallback=None
                e.resilience.complete('completed')
                e.event('NETWORK','topology_transition_completed',dict(topology=e.topology,reason=self.reason))
            return
        if e.topology_mode!='auto': return
        # Rebuild-level loss detection precedes the slower score evaluation cadence.
        connected,control=connectivity_state(e.links,e.nodes,e.blocked|e.auto_blocked)
        feasible,critical=connectivity_state(e.permitted_physical_links(),e.nodes,e.blocked|e.auto_blocked)
        available=sum(n.available and n.health>0 for n in e.nodes.values())
        constrained=2*len(feasible)<available*(available-1) or (not e.cfg.get('mesh_only') and len(critical)<available)
        loss=bool(critical-control) or 2*(len(feasible)-len(connected))/max(1,len(e.nodes)*(len(e.nodes)-1))>=self.policy.emergency_loss
        self.loss_rebuilds=self.loss_rebuilds+1 if loss else 0
        emergency=self.loss_rebuilds>=self.policy.emergency_rebuilds and (self.last_emergency is None or e.seconds-self.last_emergency>=self.policy.emergency_interval_s-1e-9)
        if emergency: self.last_emergency=e.seconds
        first=self.last_evaluation is None
        best=self.evaluate(force=first or emergency)
        if emergency and (e.resilience.enabled or e.assistance.enabled):
            best=self.best(emergency=True)
        if not best or self.transition: return
        current=self.candidates[e.topology]
        if best.topology==e.topology and not e.assistance.proposal:
            self.winner=None; self.wins=0
            self.reason=f'{len(e.nodes)} UAVs · {len(current.edges)} links; best balance of delivery, delay and relay load' if not (loss or constrained) else 'Physical or policy constraints limit connectivity; retaining permitted graph'
            if e.resilience.enabled and any(not c.service_admissible for c in self.candidates.values()):
                self.reason+=' · alternatives would worsen modeled deadlines or evidence coverage'
            return
        if first and e.seconds==0 and not e.scheduler.generated:
            self.request(best.topology,f'Initial balance for {len(e.nodes)} UAVs · {len(best.edges)} links · nominal traffic estimates',True)
        elif first and e.topology not in TOPOLOGIES:
            self.request(best.topology,'Auto initialized from legacy topology · best permitted primary candidate',True)
        elif emergency and best.connectivity>current.connectivity:
            reason=f'Emergency recovery · restores {len(best.data_pairs-current.data_pairs)} peer pairs and {len(best.control_nodes-current.control_nodes)} control routes'
            if e.resilience.enabled and not best.service_admissible:
                reason+=' · modeled service budgets remain unmet; least harmful connectivity recovery'
            self.request(best.topology,reason,True)
            if self.transition: self.transition['emergency']=True
        elif e.assistance.enabled and e.assistance.proposal:
            a=e.assistance;target=a.proposal['target'];token=(a.proposal['node'],target)
            a.wins=a.wins+1 if a.winner==token else 1;a.winner=token
            a.reason=f'Assistance {a.wins}/3 evaluations; cooldown {self.cooldown_remaining():.1f}s'
            if a.wins>=3 and self.cooldown_remaining()<=1e-9:
                self.request(target,f'Affected UAV {a.proposal["node"]} · estimated Mesh service improvement',True)
                a.winner=None;a.wins=0
            else:self.reason=a.reason
        elif best.score-best.penalty-current.score>=self.policy.improvement-1e-12:
            self.wins=self.wins+1 if self.winner==best.topology else 1; self.winner=best.topology
            if self.wins>=self.policy.consecutive_wins and self.cooldown_remaining()<=1e-9:
                gains=sorted(self.policy.weights,key=lambda k:self.policy.weights[k]*(best.factors.get(k,0)-current.factors.get(k,0)),reverse=True)
                self.request(best.topology,f'Higher {gains[0]} utility sustained across {self.policy.consecutive_wins} evaluations',True)
            else: self.reason=f'Challenger improving · {self.wins}/{self.policy.consecutive_wins} evaluations; cooldown {self.cooldown_remaining():.1f}s'
        else:
            self.winner=None; self.wins=0; self.reason=('Physical/policy constraints · ' if constrained else 'Retaining topology · ')+'improvement below switching threshold'

    def fail(self, reason):
        e=self.engine; t=self.transition
        if not t: return
        if t.get('assisted'):e.assistance.completed(False,reason)
        self.blacklist[t['target']]=e.seconds+self.policy.blacklist_s
        e.resilience.complete('failed')
        e.autonomy.guard=None
        self.transition=None; self.last_transition='failed'; self.fallback=reason
        self.reason=f'Transition failed; restored {t["previous"].replace("_"," ")}'
        e.topology=t['previous']; e.topology_state=deepcopy(t['previous_state']); e.revision+=1
        if 'committed_at' in t: self.last_switch=e.seconds
        e.event('NETWORK','topology_transition_failed',dict(target=t['target'],reason=reason,blacklisted_until=self.blacklist[t['target']]))
        # Rebuild the previous topology using today's radio/policy conditions.
        try: e.rebuild(adapt=False)
        except (ValueError,RuntimeError) as exc:
            self.fallback=f'{reason}; previous forwarding unavailable: {exc}'
            for identity,flow in list(e.flows.items()):
                if flow['family']!='hold' and not e.valid_route(flow['hops'],flow['family'],flow['traffic_class'] in CONTROL_TRAFFIC): e.remove_policy(identity)
        restored=self.candidate(e.topology,e.permitted_physical_links(),self.demands()[0],{})
        self.evaluate(force=True); best=self.best()
        if best and best.connectivity>restored.connectivity:
            self.request(best.topology,'Fallback to highest-connectivity permitted candidate',True)
        self.release_deferred()
        e.event('NETWORK','topology_fallback',dict(topology=e.topology,reason=self.reason))

    def cooldown_remaining(self):
        return max(0.,self.policy.cooldown_s-(self.engine.seconds-self.last_switch)) if self.last_switch is not None else 0.

    def snapshot(self):
        t=self.transition
        return dict(mode=self.engine.topology_mode,policy_id=self.engine.cfg.get('adaptive_policy','balanced_v1'),reason=self.reason,evaluated_at=self.last_evaluation,
                    switched_at=self.last_switch,transition_state=t['phase'] if t else self.last_transition,
                    requested_target=t['target'] if t else None,transition_started_at=t['started'] if t else None,
                    drain_deadline=t['deadline'] if t else None,cooldown_remaining_s=self.cooldown_remaining(),
                    evidence=getattr(self,'demand_evidence','nominal_schedule_estimate'),fallback=self.fallback,
                    candidates=[self.candidates[n].diagnostic() for n in TOPOLOGIES if n in self.candidates],
                    assistance=self.engine.assistance.snapshot(),
                    resilience=self.engine.resilience.snapshot() if self.engine.resilience.enabled else None,
                    blacklisted=[dict(topology=n,until=at) for n,at in sorted(self.blacklist.items()) if at>self.engine.seconds])
