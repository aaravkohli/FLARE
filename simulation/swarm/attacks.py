"""Conditions injected into modeled channels/messages, never detector verdicts.

Adaptive interference retains the legacy three-arm Q-learning update. All
timers and random choices belong to the mission rather than background threads.
"""
from collections import deque
from copy import deepcopy
from dataclasses import replace
import math
import random

FAMILIES = ('direct', 'satellite', 'mesh')
PROFILES = {
    'ew': ('none', 'spot', 'sweep', 'barrage', 'smart', 'reactive', 'adaptive',
           'fhss', 'spoofing', 'gps_spoofing', 'replay', 'dos'),
    'traffic': ('normal', 'selective_forwarding', 'telemetry_falsification', 'control_flood', 'replay'),
    'fl': ('normal', 'poisoned'),
}
CAPABILITIES = dict(schema_version='mission_attack_capabilities_v1', profiles=PROFILES,
                    families=FAMILIES, max_duration_s=300, evidence='packet_simulation',
                    gps_reference='untrusted_simulation_only', flood_budget_per_s=3000)


class AttackConditions:
    def __init__(self, engine):
        self.engine = engine
        self.rng = random.Random(engine.seed ^ 0xF1A2E)
        self.active = {}
        self.history = deque(maxlen=60)
        self.q = {}
        self.next_update = {}
        self.tokens = {}
        self.last_sequences = {}

    def state(self):
        return dict(schema_version='mission_attacks_v1', session_id=self.engine.session_id,
                    revision=self.engine.revision, simulation_time=self.engine.seconds,
                    capabilities=CAPABILITIES, active=deepcopy(list(self.active.values())),
                    history=deepcopy(list(self.history)))

    def inject(self, request):
        e = self.engine
        if request.profile not in PROFILES[request.group]:
            raise ValueError('unsupported attack profile')
        if len(set(request.targets)) != len(request.targets) or any(n not in e.nodes for n in request.targets):
            raise ValueError('attack targets must be unique current UAV identities')
        if request.group=='ew' and request.profile not in ('none', 'normal', 'barrage', 'sweep', 'gps_spoofing') and not request.paths:
            raise ValueError('select at least one route family')
        old = self.active.get(request.attack_id)
        if old:
            if old['request'] != request.model_dump(): raise ValueError('attack ID already bound to another request')
            return deepcopy(old)
        # Mitigation can coexist with RF conditions on the same target.
        for key, condition in list(self.active.items()):
            if condition['group']!=request.group: continue
            if request.profile=='fhss' and condition['profile']!='fhss': continue
            if request.profile not in ('none','normal','fhss') and condition['profile']=='fhss': continue
            self.clear(attack_id=key, targets=request.targets, reason='replaced')
        if request.profile in ('none', 'normal'):
            return dict(attack_id=request.attack_id, status='cleared', targets=request.targets)
        record = dict(request.model_dump(), request=request.model_dump(), created_at=e.seconds,
                      expires_at=e.seconds+request.duration_s, status='awaiting_participating_round' if request.group=='fl' else 'active',
                      affected_targets=request.targets[:], effective_paths=request.paths[:],
                      evidence_source='packet_simulation', outcomes={})
        self.active[request.attack_id] = record
        e.event('SECURITY', 'attack_injected', {k:v for k,v in record.items() if k!='request'})
        return deepcopy(record)

    def clear(self, attack_id=None, group=None, targets=None, reason='cleared'):
        e = self.engine
        for key, a in list(self.active.items()):
            if attack_id is not None and key != attack_id or group is not None and a['group'] != group: continue
            removed = set(a['affected_targets']) & set(targets or a['affected_targets'])
            if not removed: continue
            a['affected_targets'] = [n for n in a['affected_targets'] if n not in removed]
            if a['group']=='fl':
                from simulation.byzantine_state import set_attack_mode
                for n in removed: set_attack_mode(n, 'normal')
            e.event('SECURITY', 'attack_cleared', dict(attack_id=key, targets=sorted(removed), reason=reason))
            if not a['affected_targets']:
                a = self.active.pop(key); a['status']=reason; a['completed_at']=e.seconds
                if a.get('spatial'): e.jammers=[]
                self.history.append(a)
                for n in a['targets']:
                    self.q.pop((key,n), None); self.next_update.pop((key,n), None); self.tokens.pop((key,n), None)

    def for_node(self, identity):
        return [a for a in self.active.values() if identity in a['affected_targets']]

    def has(self, identity, profile):
        return any(a['profile']==profile for a in self.for_node(identity))

    def step(self, dt):
        e = self.engine
        for key, a in list(self.active.items()):
            if e.seconds >= a['expires_at']:
                self.clear(attack_id=key, reason='expired'); continue
            profile=a['profile']
            if profile=='sweep': a['effective_paths']=[FAMILIES[int((e.seconds-a['created_at'])/4)%3]]
            if profile=='barrage': a['effective_paths']=list(FAMILIES)
            for n in a['affected_targets']:
                if profile not in ('reactive','adaptive'): continue
                state=(key,n)
                if e.seconds < self.next_update.get(state,0): continue
                observed=e.families.get(n,'mesh')
                if profile=='reactive': paths=[observed] if observed in FAMILIES else a['paths']
                else:
                    q=self.q.setdefault(state,dict.fromkeys(FAMILIES,0.))
                    previous=a['outcomes'].get(n,{}).get('jammed_family','direct')
                    reward=1. if observed==previous else -.5
                    q[previous] += .2*(reward+.9*max(q.values())-q[previous])
                    paths=[self.rng.choice(FAMILIES) if self.rng.random()<.1 else max(FAMILIES,key=q.get)]
                a['outcomes'][n]=dict(jammed_family=paths[0] if paths else None, observed_family=observed)
                a['effective_paths']=paths
                self.next_update[state]=e.seconds+(2 if profile=='adaptive' else 1)

    def impair(self, link, family, identities):
        """Channel failures affect actual transmission and candidate measurements."""
        if link is None or not self.active: return link
        candidates={a['attack_id']:a for n in identities for a in self.for_node(n)}
        noise=0.; smart=False
        for a in candidates.values():
            if a.get('spatial'): continue
            paths=a['effective_paths']
            if a['profile'] in ('reactive','adaptive'):
                paths=[a['outcomes'].get(n,{}).get('jammed_family') for n in identities if n in a['affected_targets']]
            if family not in paths: continue
            if a['profile'] in ('spot','sweep','barrage','reactive','adaptive'): noise+=22.
            if a['profile']=='smart': noise+=10.; smart=True
        if not noise: return link
        if any(self.has(n,'fhss') for n in identities): noise*=.1
        sinr=link.sinr-noise
        pdr=1/(1+math.exp(-max(-30,min(30,(sinr-7)/2.5))))
        loss=max(link.loss,1-pdr,.8 if smart else 0.)
        return replace(link,sinr=sinr,loss=loss,bandwidth_mbps=max(.05,link.bandwidth_mbps*max(.02,1-noise/30)),status='jammed' if loss>.2 else 'degraded')

    def generate(self, dt):
        """Budgeted source traffic still consumes scheduler capacity and bandwidth."""
        e=self.engine; budget=math.ceil(3000*dt)
        jobs=[(a,n) for a in self.active.values() if a['profile'] in ('dos','control_flood','replay')
              for n in a['affected_targets'] if n in e.nodes and e.nodes[n].available]
        # Rotate priority so a large target set cannot starve its final identities.
        if jobs:
            offset=int(e.seconds/dt)%len(jobs); jobs=jobs[offset:]+jobs[:offset]
        for a,n in jobs:
            key=(a['attack_id'],n); rate=500 if a['profile']=='dos' else 100 if a['profile']=='control_flood' else 30
            self.tokens[key]=min(rate,self.tokens.get(key,0)+rate*dt)
            count=min(int(self.tokens[key]),budget); self.tokens[key]-=count; budget-=count
            a['outcomes'].setdefault(n,{})['generated_packets']=a['outcomes'].get(n,{}).get('generated_packets',0)+count
            if count<rate*dt: a['outcomes'][n]['rate_capped']=True
            for _ in range(count):
                category='routing' if a['profile']=='control_flood' else 'sensor' if a['profile']=='dos' else 'telemetry'
                families=a['paths'] or [e.families.get(n,'mesh')]
                e.send(n,'base',category,16384 if a['profile']=='dos' else 96, a['attack_id'],family_override=families[int(e.seconds/dt)%len(families)])

    def sequence(self, identity, next_value):
        if self.has(identity,'replay') and identity in self.last_sequences: return self.last_sequences[identity]
        self.last_sequences[identity]=next_value; return next_value

    def relay_drop(self, packet, identity):
        return packet.hop>0 and packet.category not in self.engine.control_traffic and self.has(identity,'selective_forwarding') and self.rng.random()<.8

    def report(self, identity, evidence, paths):
        """Compromised source claims are separate from scheduler observations."""
        evidence=dict(evidence); paths=deepcopy(paths)
        if self.has(identity,'telemetry_falsification') or self.has(identity,'spoofing'):
            evidence['reported_forwarded_packets']=evidence['controller_rx_packets']+20
        if self.has(identity,'spoofing'):
            for p in paths:
                p.update(rssi=-30.,pdr=.99,sinr=32.,latency=5.,packet_loss=.01)
        return evidence,paths

    def position(self, identity, physical):
        return [physical[0]+120,physical[1],physical[2]+120] if self.has(identity,'gps_spoofing') else physical[:]

    def dispatch_fl(self, round_number, participants):
        from simulation.byzantine_state import set_attack_mode
        for a in self.active.values():
            if a['group']!='fl': continue
            for n in set(participants)&set(a['affected_targets']):
                set_attack_mode(n,'poisoned'); a['status']='armed_for_training'
                a['outcomes'][n]=dict(round=round_number,status='armed_for_training')
                self.engine.event('FL','poisoning_armed',dict(attack_id=a['attack_id'],client_id=n,round=round_number,verdict='awaiting_actual_update_analysis'))
