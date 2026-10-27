"""Bounded make-before-retire transitions and modeled service-feasibility checks.

Leases authorize only captured packet identities. They never override current
security/physical checks or promise delivery under an unknown future channel.
"""
from collections import Counter, deque
from copy import deepcopy
from simulation.swarm.network import shortest_path
from simulation.swarm.topology import pair
from pydantic import BaseModel, ConfigDict, Field


class ResilienceSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    freshness_s: float=Field(default=3.,ge=1.,le=20.)
    fragment_bytes: int=Field(default=4096,ge=64,le=65536)
    max_report_bytes: int=Field(default=65536,ge=4096,le=65536)
    protected_airtime_share: float=Field(default=.10,gt=0,le=.5)
    packet_transitions: bool=True
    observation_guard: bool=True
    protected_scheduling: bool=True


class ResilienceControl:
    def __init__(self,engine):
        self.engine=engine; self.leases={}; self.target_links=None; self.history=deque(maxlen=32)
        self.last_plan=None; self.tracked={}; self.counters=Counter(); self.in_rebuild=False

    @property
    def enabled(self): return self.engine.cfg.get('adaptive_policy','balanced_v1')=='evidence_resilience_v1'
    @property
    def packet_aware(self): return self.enabled and self.engine.cfg.get('resilience',{}).get('packet_transitions',True)
    @property
    def observation_guard(self): return self.enabled and self.engine.cfg.get('resilience',{}).get('observation_guard',True)

    def estimate(self,links):
        e=self.engine; missed=set(); protected=0; delays={}
        queue_bytes=Counter()
        for p in e.scheduler.packets.values():
            if p.hop+1<len(p.route):
                for node in p.route[p.hop:p.hop+2]: queue_bytes[node]+=p.size_bytes
        for p in e.scheduler.packets.values():
            if not p.service_kind or p.status in ('delivered','dropped'): continue
            protected+=1; current=p.route[p.hop] if p.route else p.source
            excluded=set(p.restrictions.get('excluded_nodes',[]))|set(p.route[:p.hop])
            blocked={pair(*x) for x in p.restrictions.get('excluded_links',[])}
            path=shortest_path({k:v for k,v in links.items() if not excluded.intersection(k)},current,p.destination,e.nodes,
                e.blocked|e.auto_blocked|blocked,control=True)
            delay=0.
            if current==p.destination: path=[current]
            if not path: missed.add(p.id); continue
            for a,b in zip(path,path[1:]):
                link=links[pair(a,b)]; tries=1+max(0,e.cfg['packets']['retries']-p.retries) if p.category in e.scheduler.RELIABLE else 1
                airtime=p.size_bytes*8/(link.bandwidth_mbps*1e6)
                queued=max(queue_bytes[a],queue_bytes[b])*8/(link.bandwidth_mbps*1e6)
                reservation=max(0.,e.scheduler.radio_busy.get(('radio',a),0)-e.seconds,e.scheduler.radio_busy.get(('radio',b),0)-e.seconds)
                delay+=reservation+queued+tries*(airtime+link.latency_ms/1000+e.cfg['packets'].get('jitter_ms',0)/1000)+(tries-1)*e.cfg['packets'].get('retry_backoff_ms',2)/1000
            delays[p.id]=delay
            if e.seconds+delay>=min(p.expires_at,p.service_deadline or p.expires_at): missed.add(p.id)
        return dict(missed=missed,protected=protected,completion_estimates_s=delays)

    def assess(self,candidate):
        observations=self.engine.observability.predict(candidate.links)
        packets=self.estimate(candidate.links)
        return dict(feasible_reports=observations['feasible'],reachable_reports=observations['reachable'],
            missed_packets=packets['missed'],protected_packets=packets['protected'],
            maximum_report_utilization=observations['maximum_report_utilization'],
            basis='modeled_queue_radio_and_retry_estimates')

    def prepare(self,transition,candidate):
        e=self.engine; transition['enhanced']=True; transition['phase']='preparing'
        affected={}
        for p in e.scheduler.packets.values():
            if p.family!='mesh' or p.id in e.scheduler.deferred: continue
            route=e.installed_rule(p.policy_source or p.source,p.destination,p.category)
            expected=p.route[p.route.index(p.policy_source):] if p.policy_source!=p.source and p.policy_source in p.route else p.route
            if not route or route['hops']!=expected: continue
            remaining={pair(a,b) for a,b in zip(p.route[p.hop:],p.route[p.hop+1:])}
            target=shortest_path(candidate.links,p.source,p.destination,e.nodes,e.blocked|e.auto_blocked,control=p.category in e.control_traffic)
            if not remaining<=candidate.links.keys() or target!=p.route:
                affected[p.id]=dict(route=p.route[:],policy_source=p.policy_source,restrictions=deepcopy(p.restrictions),
                    family=p.family,flow=deepcopy(route),expires_at=min(p.expires_at,transition['deadline']))
        transition['bindings']=affected; transition['affected']=set(affected)
        transition['affected_flows']={(p.source,p.destination,p.category) for p in e.scheduler.packets.values() if p.id in affected}
        plan=dict(started_at=e.seconds,target=candidate.topology,previous=e.topology,predicted=self.public_assessment(self.assess(candidate)),
            captured_packets=len(affected),protected_packets=sum(bool(e.scheduler.packets[n].service_kind) for n in affected),
            overlap_deadline=transition['deadline'],realized=dict(delivered=0,dropped=0,deadline_misses=0),status='preparing')
        self.last_plan=plan
        for identity in affected: self.tracked[identity]=plan

    @staticmethod
    def public_assessment(assessment):
        result={k:sorted(v)[:100] if isinstance(v,set) else v for k,v in assessment.items()}
        result['missed_packet_count']=len(assessment['missed_packets'])
        return result

    def begin(self,transition,candidate):
        e=self.engine
        self.target_links=dict(candidate.links); self.leases=deepcopy(transition['bindings'])
        self.leases={n:v for n,v in self.leases.items() if n in e.scheduler.packets}
        transition['phase']='overlap' if self.leases else 'validating'
        self.last_plan['status']=transition['phase']; self.counters['transitions']+=1
        if not self.leases:
            e.topology=candidate.topology; e.topology_state=deepcopy(candidate.state)
            self.target_links=None
        e.event('NETWORK','topology_transition_overlap',dict(previous=transition['previous'],target=candidate.topology,
            captured_packets=len(self.leases),overlap_deadline=transition['deadline'],prediction=self.last_plan['predicted']))

    def retire(self,transition):
        e=self.engine; self.leases.clear(); self.target_links=None
        e.topology=transition['target']; e.topology_state=deepcopy(transition['prepared'].state)
        transition['phase']='validating'; self.last_plan['status']='validating'
        self.last_plan['overlap_ended_at']=e.seconds

    def overlay(self):
        e=self.engine; edges={}
        for identity,lease in list(self.leases.items()):
            p=e.scheduler.packets.get(identity)
            if not p or e.seconds>=lease['expires_at']-1e-9: continue
            for a,b in zip(p.route[p.hop:],p.route[p.hop+1:]):
                edge=pair(a,b); link=e.physical_links.get(edge)
                if link and edge not in e.blocked|e.auto_blocked:
                    edges[edge]=e.attacks.impair(link,'mesh',edge)
        return edges

    def authorizes(self,p):
        lease=self.leases.get(p.id); e=self.engine
        if not lease: return False
        now=getattr(e.scheduler,'checking_at',e.seconds)
        if now>=lease['expires_at']-1e-9 or p.route!=lease['route'] or p.policy_source!=lease['policy_source']: return False
        current=e.flow_constraints.get(f'{p.source}:{p.destination}:{p.category}',{})
        omitted=set(current.get('excluded_nodes',[])); blocked={pair(*x) for x in current.get('excluded_links',[])}
        if p.category not in current.get('allowed_traffic_classes',[p.category]) or omitted.intersection(p.route[p.hop:]): return False
        if any(pair(a,b) in blocked for a,b in zip(p.route[p.hop:],p.route[p.hop+1:])): return False
        return e.valid_route(p.route[p.hop:],p.family,p.category in e.control_traffic)

    def invalidate_flow(self,source,destination,category):
        for identity in list(self.leases):
            p=self.engine.scheduler.packets.get(identity)
            if p and (p.source,p.destination,p.category)==(source,destination,category): self.leases.pop(identity)

    def complete(self,status):
        if self.last_plan and self.last_plan['status'] not in ('completed','failed','cancelled'):
            self.last_plan['status']=status; self.last_plan['finished_at']=self.engine.seconds
            self.history.append(self.last_plan)
            self.counters[status]+=1
        self.leases.clear(); self.target_links=None

    def result(self,data):
        self.leases.pop(data['id'],None)
        plan=self.tracked.pop(data['id'],None)
        if plan:
            plan['realized'][data['status']]+=1
            self.counters['captured_'+data['status']]+=1
            if data.get('service_deadline') is not None and data['completed_at']>=data['service_deadline']:
                plan['realized']['deadline_misses']+=1

    def snapshot(self):
        return dict(protected_share=self.engine.scheduler.protected_share,active_bindings=len(self.leases),
            last_plan=deepcopy(self.last_plan),history=list(self.history),counters=dict(self.counters),
            temporary_edges=[list(edge) for edge in sorted(self.overlay()) if not self.target_links or edge not in self.target_links])
