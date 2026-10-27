"""Versioned peer workload shared by reference comparisons and adaptive studies.

Scenery, model inference and the live mission traffic generator are independent.
v2 congestion used Base-directed control floods and could not load mesh-only RF.
"""
from collections import defaultdict, deque
import hashlib
import json

REFERENCE_WORKLOAD = 'mesh_peer_reference_v3'
LEGACY_LIMITATION = 'v2 congestion was Base-directed and did not load the mesh-only radio scheduler.'


def nominal_demands(engine):
    ids=list(engine.nodes)
    return {(source,ids[(i+len(ids)//2)%len(ids)],'sensor'):512. for i,source in enumerate(ids)}


class PeerTraffic:
    def __init__(self, engine, stop_s):
        self.engine=engine; self.stop_s=stop_s
        self.ids=list(engine.nodes); self.fractions=defaultdict(float)
        self.background_offered=0; self.background_bytes=0
        self.input_hash=hashlib.sha256()
        self.offered=deque()

    def send(self,source,destination,size,correlation):
        now=self.engine.seconds
        while self.offered and self.offered[0][0]<now-10: self.offered.popleft()
        if not self.offered or self.offered[-1][0]!=now: self.offered.append((now,defaultdict(float)))
        self.offered[-1][1][source,destination,'sensor']+=size
        self.input_hash.update(json.dumps([source,destination,self.engine.seconds,size,correlation],separators=(',',':')).encode())
        return self.engine.send(source,destination,'sensor',size,correlation,family_override='mesh')

    def demands(self):
        now=self.engine.seconds
        while self.offered and self.offered[0][0]<now-10: self.offered.popleft()
        if not self.offered: return {} if now>=self.stop_s else nominal_demands(self.engine)
        rates=defaultdict(float)
        for _,offers in self.offered:
            for key,size in offers.items(): rates[key]+=size/max(1.,min(10.,now))
        return rates

    def primary(self):
        e=self.engine
        if e.seconds>=self.stop_s: return
        for i,source in enumerate(self.ids):
            self.send(source,self.ids[(i+len(self.ids)//2)%len(self.ids)],512,'reference')

    def step(self):
        e=self.engine
        if e.scenario!='congestion': return
        # A separate traffic condition, not an injected detector observation.
        for at,action in ((8.,'condition_onset'),(25.,'condition_recovery')):
            if e.seconds+1e-9>=at and action not in e.scenario_actions:
                e.scenario_actions.add(action)
                e.event('NETWORK',action,dict(at=at,condition='congestion',workload=REFERENCE_WORKLOAD,
                    flows=5,packets_per_second=100,size_bytes=4096))
        if e.seconds+1e-9<8 or e.seconds>=25-1e-9 or e.seconds>=self.stop_s: return
        for i,source in enumerate(self.ids[:5]):
            self.fractions[source]+=100*e.cfg['fixed_step_s']
            count=int(self.fractions[source]+1e-9); self.fractions[source]-=count
            for _ in range(count):
                self.send(source,self.ids[(i+len(self.ids)//2)%len(self.ids)],4096,'reference_background')
                self.background_offered+=1; self.background_bytes+=4096


def install(engine, stop_s):
    driver=PeerTraffic(engine,stop_s); original=engine.scenario_step
    engine.generate_traffic=driver.primary
    def scenario_step():
        if engine.scenario!='congestion': original()
        driver.step()
    engine.scenario_step=scenario_step; engine.reference_traffic=driver
    return driver
