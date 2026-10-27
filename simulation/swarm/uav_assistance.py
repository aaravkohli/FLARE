"""Opt-in, receipt-backed individual-UAV service assistance.

Only trusted producers register packet identities. Attack load remains in the
ordinary radio estimator; naming a packet 'telemetry' never registers it here.
"""
from collections import defaultdict, deque
from copy import deepcopy
from schemas.mission import CONTROL_TRAFFIC,TOPOLOGIES

VERSION='affected_uav_assist_v1'
LIMITS=dict(window_s=10,evaluation_s=1,minimum_packets=8,degraded_delivery=.8,degraded_latency_ms=100,
            confirming_evaluations=3,recovered_delivery=.9,recovered_evaluations=5,
            receipt_timeout_s=3,minimum_offers=3,utility_gain=.10,fleet_delivery_loss=.02,
            fleet_latency_increase=.10,fleet_score_loss=.02,affected_utility_loss=.01)

class UavAssistance:
    def __init__(self,engine):
        self.engine=engine;self.pending={};self.buckets=deque(maxlen=20000);self.nodes={};self.flows={}
        self.last_evaluation=None;self.proposal=None;self.winner=None;self.wins=0
        self.reason='Disabled';self.candidates=[];self.attempt=None;self.history=deque(maxlen=20);self.counters=defaultdict(int)
    @property
    def enabled(self):return bool(self.engine.cfg.get('affected_uav_assist',False))
    def allowed(self,flow):
        e=self.engine;a,b,category=flow;c=e.flow_constraints.get(f'{a}:{b}:{category}',{})
        if category not in c.get('allowed_traffic_classes',[category]) or {a,b}&set(c.get('excluded_nodes',[])):return False
        for n in (a,b):
            d=e.nodes.get(n)
            if n!='base' and d is None:return False
            if d and (not d.available or d.health<=0 or d.containment=='quarantined' or (d.threat or 0)>.8):return False
            if d and category not in CONTROL_TRAFFIC and (d.containment in ('restricted','control_only') or d.selected_route=='hold' or e.families.get(n)=='hold'):return False
        return True
    def bucket(self,now):
        stamp=now
        if not self.buckets or self.buckets[-1][0]!=stamp:self.buckets.append((stamp,{}))
        return self.buckets[-1][1]
    def row(self,flow,now):
        bucket=self.bucket(now)
        if flow not in bucket:bucket[flow]=dict(offered=0,bytes=0,completed=0,delivered=0,latency_sum=0.)
        return bucket[flow]
    def register(self,identity,source,destination,category,size,now):
        if not self.enabled:return
        flow=(source,destination,category)
        if not self.allowed(flow):return
        # Producers are bounded by the existing scheduler/fleet capacity.
        if len(self.pending)>=self.engine.cfg['packets']['capacity']:return
        self.pending[identity]=(flow,now)
        row=self.row(flow,now);row['offered']+=1;row['bytes']+=size
        for n in (source,destination):
            if n in self.engine.nodes:self.nodes.setdefault(n,dict(first_offer=now,last_receipt=None,degraded=False,bad=0,good=0,since=None,status='Insufficient evidence'))
            if n in self.nodes and self.nodes[n]['first_offer'] is None:self.nodes[n]['first_offer']=now
    def result(self,kind,data):
        if kind!='packet_result':return
        item=self.pending.pop(data['id'],None)
        if not self.enabled or not item:return
        flow,created=item
        if not self.allowed(flow):return
        self.observe_after(data)
        at=data['completed_at'];row=self.row(flow,at);row['completed']+=1
        if data['status']=='delivered':
            row['delivered']+=1;row['latency_sum']+=(at-created)*1000
            for n in flow[:2]:
                if n in self.nodes:self.nodes[n]['last_receipt']=at
    def configure(self):
        if self.attempt:
            self.attempt.update(status='Assessment cancelled',reason='Assistance setting changed',completed_at=self.engine.seconds)
            self.history.append(deepcopy(self.attempt));self.attempt=None
        self.pending.clear();self.buckets.clear();self.nodes.clear();self.flows.clear();self.proposal=None;self.winner=None;self.wins=0;self.last_evaluation=None
        self.reason='Awaiting Mesh evidence' if self.enabled else 'Disabled'
    def permissions_changed(self):
        e=self.engine;t=e.adaptive.transition
        if not t or not t.get('assisted'):return
        g=e.autonomy.guard
        if g and any(not self.allowed((*flow,g.get('categories',{}).get(key,'routing'))) for key,flow in g['flows'].items()):
            e.adaptive.cancel('Assistance cancelled: relevant permission changed');return
        proofs=[((*g['flows'][key],g.get('categories',{}).get(key,'routing')),rows[-1]['path']) for key,rows in g['receipts'].items() if rows] if g else t.get('verified_flows',[])
        if any(not e.autonomy.verifier.flow_permitted(flow,path) for flow,path in proofs) or self.proposal and not self.allowed((self.proposal['node'],'base','heartbeat')):
            e.adaptive.cancel('Assistance cancelled: relevant permission changed')
    def update(self,now):
        if not self.enabled or self.last_evaluation is not None and now-self.last_evaluation<1-1e-9:return
        self.last_evaluation=now
        self.nodes={n:s for n,s in self.nodes.items() if n in self.engine.nodes}
        self.buckets=deque(((at,rows) for at,rows in self.buckets if now-10<at<=now+1e-9),maxlen=20000)
        flows={}
        for _,bucket in self.buckets:
            for flow,row in bucket.items():
                if not self.allowed(flow):continue
                total=flows.setdefault(flow,dict(offered=0,bytes=0,completed=0,delivered=0,latency_sum=0.))
                for k,v in row.items():total[k]+=v
        self.flows=flows
        aggregate={n:dict(offered=0,bytes=0,completed=0,delivered=0,latency_sum=0.) for n in self.engine.nodes}
        for flow,row in flows.items():
            for n in set(flow[:2])&aggregate.keys():
                for k,v in row.items():aggregate[n][k]+=v
        for n,d in self.engine.nodes.items():
            s=self.nodes.setdefault(n,dict(first_offer=None,last_receipt=None,degraded=False,bad=0,good=0,since=None,status='Insufficient evidence'))
            r=aggregate[n];pdr=r['delivered']/r['completed'] if r['completed'] else None;latency=r['latency_sum']/r['delivered'] if r['delivered'] else None
            latest=s['last_receipt'] if s['last_receipt'] is not None else s['first_offer']
            receipt_age=now-latest if latest is not None else None
            enough=r['completed']>=8;missing=r['offered']>=3 and receipt_age is not None and receipt_age>=3-1e-9
            eligible=any(n in f[:2] for f in flows)
            status='Unavailable' if not d.available or d.health<=0 else 'Quarantined' if d.containment=='quarantined' or (d.threat or 0)>.8 else 'Policy blocked' if n in self.engine.autonomy.revoked else 'Mesh interface unavailable' if self.engine.autonomy.interfaces.get(n,{}).get('mesh',self.engine.flight_safety.supervisors[n].profile.interface_health.get('mesh')) is False or 'mesh' in self.engine.failed_families else 'Policy blocked' if not eligible and (d.containment!='normal' or d.selected_route=='hold' or self.engine.families.get(n)=='hold') else None
            bad=missing or enough and (pdr<=.8 or latency is not None and latency>100)
            good=enough and pdr>=.9 and latency is not None and latency<=100
            was=s['degraded'];s['bad']=s['bad']+1 if bad and not status else 0;s['good']=s['good']+1 if good and not status else 0
            if status:s['degraded']=False;s['since']=None
            elif s['bad']>=3:s['degraded']=True;s['since']=s['since'] if s['since'] is not None else now
            elif s['good']>=5:s['degraded']=False;s['since']=None
            s.update(status=status or ('Missing receipts' if missing else 'Degraded' if s['degraded'] else 'Healthy' if enough and not bad else 'Insufficient evidence'),pdr=pdr,latency_ms=latency,receipt_age_s=receipt_age,missing_receipts=missing,**r)
            if s['degraded']!=was:self.engine.event('NETWORK','uav_assistance_degraded' if s['degraded'] else 'uav_assistance_cleared',dict(node=n,status=s['status'],pdr=pdr,latency_ms=latency))
        self.realized(now)
    def metrics(self,candidate):
        totals=defaultdict(lambda:dict(bytes=0.,reliability=0.,latency=0.,capacity=0.));delivery=delay=weight=delay_weight=0.
        for flow,row in self.flows.items():
            rate=row['bytes'];m=candidate.services.get(flow,{})
            if not rate:continue
            r=m.get('reliability',0.);l=1/(1+m['delay_ms']/50) if m.get('delay_ms') is not None else 0.;cap=m.get('capacity',0.)
            for n in set(flow[:2])&self.engine.nodes.keys():
                t=totals[n];t['bytes']+=rate;t['reliability']+=rate*r;t['latency']+=rate*l;t['capacity']+=rate*cap
            weight+=rate;delivery+=rate*m.get('delivery',0.)
            if m.get('delay_ms') is not None:delay+=rate*m['delay_ms'];delay_weight+=rate
        w=self.engine.adaptive.policy.weights;normalizer=w['reliability']+w['latency']+w['capacity']
        utilities={n:sum(w[k]*t[k] for k in ('reliability','latency','capacity'))/(normalizer*t['bytes']) if normalizer else 0. for n,t in totals.items() if t['bytes']}
        return dict(utilities=utilities,delivery=delivery/weight if weight else None,latency_ms=delay/delay_weight if delay_weight else None)
    def bound(self,c,current,cm,old,affected):
        if not c.admissible:return 'Would remove permitted connectivity'
        if not c.service_admissible:return 'Protected-service budget'
        if c.score-c.penalty<current.score-.02-1e-12:return 'Fleet score cost'
        if old['delivery'] is not None and (cm['delivery'] is None or cm['delivery']<old['delivery']-.02-1e-12):return 'Fleet delivery cost'
        if old['latency_ms'] is not None and (cm['latency_ms'] is None or cm['latency_ms']>old['latency_ms']*1.10+1e-9):return 'Fleet latency cost'
        if any(cm['utilities'].get(n,0)<old['utilities'].get(n,0)-.01-1e-12 for n in affected):return 'Another affected UAV would regress'
        return None
    def choose(self,candidates,current):
        self.proposal=None;self.candidates=[]
        if not self.enabled:return
        if self.engine.topology_mode!='auto':self.reason='Inactive—manual topology pinned';return
        affected=[n for n,s in self.nodes.items() if s['degraded'] and s['status'] not in ('Unavailable','Quarantined','Policy blocked')]
        affected.sort(key=lambda n:(not self.nodes[n]['missing_receipts'],self.nodes[n]['pdr'] if self.nodes[n]['pdr'] is not None else 1.,-(self.nodes[n]['latency_ms'] or 0),self.nodes[n]['since'] or 0,n))
        if not affected:self.reason='No sustained Mesh degradation';self.winner=None;self.wins=0;return
        measured={name:self.metrics(c) for name,c in candidates.items()};old=measured[current.topology]
        for n in affected:
            options=[];diagnostics=[]
            for name,c in candidates.items():
                if name not in TOPOLOGIES or self.engine.adaptive.blacklist.get(name,-1)>self.engine.seconds:continue
                cm=measured[name];gain=cm['utilities'].get(n,0)-old['utilities'].get(n,0)
                reason=self.bound(c,current,cm,old,affected)
                ground=c.services.get((n,'base','heartbeat'),{})
                # Ground Mesh verification is required even if only peer traffic degraded.
                if not ground.get('route'):reason=reason or 'No permitted ground Mesh route'
                if gain<.10+c.penalty-1e-12:reason=reason or 'Target improvement below margin'
                diagnostics.append(dict(topology=name,node=n,utility_gain=gain,transition_penalty=c.penalty,fleet_delivery=cm['delivery'],fleet_latency_ms=cm['latency_ms'],net_fleet_score=c.score-c.penalty,reason=reason,estimated=True))
                if not reason and name!=current.topology:options.append((gain-c.penalty,c.score-c.penalty,-TOPOLOGIES.index(name),c))
            self.candidates=diagnostics[:10]
            if options:
                c=max(options,key=lambda x:x[:3])[-1]
                cm=measured[c.topology]
                self.proposal=dict(target=c.topology,node=n,affected=affected,metrics=deepcopy(self.nodes[n]),gain=cm['utilities'].get(n,0)-old['utilities'].get(n,0),estimates=dict(fleet_net_score_difference=c.score-c.penalty-current.score,fleet_delivery_difference=cm['delivery']-old['delivery'] if cm['delivery'] is not None and old['delivery'] is not None else None,fleet_latency_ratio=cm['latency_ms']/old['latency_ms'] if old['latency_ms'] and cm['latency_ms'] is not None else None));return c
        self.reason='No feasible assisted improvement; retaining permitted topology';self.winner=None;self.wins=0
    def ordinary_allowed(self,candidates,current):
        if not self.enabled:return list(candidates.values())
        affected=[n for n,s in self.nodes.items() if s['degraded']]
        if not affected:return list(candidates.values())
        old=self.metrics(current)
        measured={name:self.metrics(c) for name,c in candidates.items()}
        return [c for name,c in candidates.items() if all(measured[name]['utilities'].get(n,0)>=old['utilities'].get(n,0)-.01-1e-12 for n in affected)]
    def flows_to_verify(self,candidate):
        flows={}
        for n in self.proposal['affected']:
            # Do not let a physically unrecoverable endpoint block other assistance.
            if not candidate.services.get((n,'base','heartbeat'),{}).get('route'):continue
            flows[n]=(n,'base','heartbeat')
            peers=[(f,r) for f,r in self.flows.items() if n in f[:2] and 'base' not in f[:2] and r['offered']]
            if peers:
                flow,_=min(peers,key=lambda item:(item[1]['delivered']/max(1,item[1]['completed']),-item[1]['latency_sum']/max(1,item[1]['delivered']),item[0]))
                flows[flow[0]+'|'+flow[1]]=flow
        return flows
    def requested(self):
        self.counters['attempts']+=1;self.attempt=dict(node=self.proposal['node'],target=self.proposal['target'],started_at=self.engine.seconds,before=deepcopy(self.proposal['metrics']),estimates=self.proposal.get('estimates'),status='Verifying replacement routes',completed_after=0,delivered_after=0,latency_sum_after=0.)
        self.engine.event('NETWORK','uav_assistance_requested',deepcopy(self.attempt))
    def completed(self,success,reason=None,cancelled=False):
        if not self.attempt:return
        self.attempt.update(status='Verified route; quality pending' if success else 'Cancelled' if cancelled else 'Transition failed',committed_at=self.engine.seconds if success else None,reason=reason)
        self.engine.event('NETWORK','uav_assistance_verified' if success else 'uav_assistance_cancelled' if cancelled else 'uav_assistance_failed',deepcopy(self.attempt))
        if success:self.counters['switches']+=1
        else:self.history.append(deepcopy(self.attempt));self.attempt=None
    def observe_after(self,data):
        a=self.attempt
        if not a or a.get('committed_at') is None or data['created_at']<a['committed_at'] or a['node'] not in (data['source'],data['destination']):return
        a['completed_after']+=1
        if data['status']=='delivered':a['delivered_after']+=1;a['latency_sum_after']+=(data['completed_at']-data['created_at'])*1000
    def realized(self,now):
        a=self.attempt
        if not a or a.get('committed_at') is None or now-a['committed_at']<10:return
        enough=a['completed_after']>=8;pdr=a['delivered_after']/a['completed_after'] if enough else None;lat=a['latency_sum_after']/a['delivered_after'] if a['delivered_after'] else None
        old=a['before'];baseline=old.get('completed',0)>=8 and old.get('pdr') is not None
        better=enough and baseline and (pdr>=old['pdr']+.05 or lat is not None and old['latency_ms'] is not None and lat<=old['latency_ms']*.8 and pdr>=old['pdr']-.02)
        a.update(status='Quality improved' if better else 'Receipt evidence available; baseline quality unknown' if enough and not baseline else 'No measured improvement' if enough else 'Insufficient post-switch evidence',pdr=pdr,latency_ms=lat)
        self.history.append(deepcopy(a));self.engine.event('NETWORK','uav_assistance_quality',deepcopy(a));self.attempt=None
    def snapshot(self):
        if not self.enabled:return dict(version=VERSION,enabled=False,reason='Disabled',nodes=[],candidates=[],attempt=None,history=[],limits=LIMITS,counters={})
        g=self.engine.autonomy.guard
        verification=dict(required=len(g['required']),verified=sum(len(g['receipts'].get(k,[]))>=2 for k in g['required']),deadline=g['deadline']) if g and g.get('assisted') else None
        nodes=[]
        for n,s in sorted(self.nodes.items()):
            feasible=any(c.services.get((n,'base','heartbeat'),{}).get('route') for c in self.engine.adaptive.candidates.values()) if s['degraded'] else None
            nodes.append(dict(node=n,**s,feasibility='No permitted alternative route' if feasible is False else 'Modeled alternatives available' if feasible else None))
        return dict(version=VERSION,enabled=True,reason='Inactive—manual topology pinned' if self.engine.topology_mode=='manual' else self.reason,nodes=nodes,candidates=self.candidates,attempt=deepcopy(self.attempt),history=list(self.history),verification=verification,limits=LIMITS,counters=dict(self.counters))
