"""Receipt-backed, bounded simulated security reports, never detector verdicts.

Wire timestamps use a declared simulation epoch for reproducible byte counts.
Wall capture times are separate diagnostics, not report bytes or scientific inputs.
"""
from collections import Counter
from copy import deepcopy
import json
import math
import time
from simulation.swarm.network import build_adjacency, shortest_path
from simulation.swarm.topology import pair

SIMULATION_EPOCH=1700000000.

class Observability:
    def __init__(self,engine):
        self.engine=engine; self.epoch=0; self.last_capture=-1.; self.serial=0
        self.pending={}; self.latest={}; self.last_sizes={}; self.wall_captures={}
        self.report_packets={}; self.counters=Counter(); self.status_integral=Counter(); self.last_sample=0.

    @property
    def enabled(self):
        return self.engine.cfg.get('adaptive_policy','balanced_v1')=='evidence_resilience_v1'

    @property
    def freshness(self): return self.engine.cfg.get('resilience',{}).get('freshness_s',3.)

    def invalidate(self):
        self.epoch+=1; self.pending.clear(); self.latest.clear(); self.report_packets.clear(); self.wall_captures.clear()
        self.last_capture=self.engine.seconds-1.; self.last_sample=self.engine.seconds

    def eligible(self,node):
        return node.available and node.health>0 and node.containment!='quarantined'

    def step(self):
        if not self.enabled: return
        e=self.engine
        for identity,report in list(self.pending.items()):
            if e.seconds>=report['deadline']-1e-9:
                self.pending.pop(identity); self.counters['expired']+=1
        for identity in list(self.report_packets):
            if identity not in e.scheduler.packets: self.report_packets.pop(identity)
        rows=self.statuses(); dt=max(0,e.seconds-self.last_sample)
        for row in rows.values(): self.status_integral[row['status']]+=dt
        self.last_sample=e.seconds
        stop=e.cfg.get('observation_stop_s',float('inf'))
        if e.seconds>=stop-1e-9 or e.seconds-self.last_capture<1-1e-9: return
        self.last_capture=e.seconds
        raw=e.raw_metrics()
        for node in e.nodes.values():
            if not self.eligible(node): continue
            self.capture(node.id,raw[node.id])

    def capture(self,node,payload):
        e=self.engine; self.serial+=1; identity=f'report:{self.epoch}:{node}:{self.serial}'
        payload=deepcopy(payload); payload.pop('ew_status',None)
        stamp=SIMULATION_EPOCH+e.seconds; payload['timestamp']=stamp
        evidence=payload.get('security_evidence') or {}
        # Preserve sampled claims/counters but no injected condition identity.
        evidence.pop('simulation_profile',None)
        for key in ('timestamp','controller_timestamp'): evidence[key]=stamp
        for key,window in (('report_window_start','report_window_seconds'),('flow_window_start','flow_window_seconds')):
            if key in evidence: evidence[key]=stamp-evidence.get(window,0)
        if evidence.get('provenance'): evidence['provenance'].update(collected_at=stamp,age_s=0.)
        wire=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        maximum=e.cfg.get('resilience',{}).get('max_report_bytes',65536)
        if len(wire)>maximum:
            self.counters['oversize']+=1; return
        fragment_size=e.cfg.get('resilience',{}).get('fragment_bytes',4096)
        sizes=[len(wire[i:i+fragment_size]) for i in range(0,len(wire),fragment_size)]
        report=dict(id=identity,node=node,captured_at=e.seconds,deadline=e.seconds+self.freshness,
                    epoch=self.epoch,payload=payload,remaining=len(sizes),received=set(),size_bytes=len(wire),failed=False,
                    wall_captured_at=time.time(),keys={f'security-report:{node}:{e.seconds:.6f}:{index}' for index in range(len(sizes))})
        self.pending[identity]=report; self.wall_captures[node]=time.time(); self.last_sizes[node]=len(wire)
        self.counters['offered']+=1; self.counters['bytes_offered']+=len(wire)
        # Correlation registration precedes emit: rejection callbacks can be synchronous.
        for index,size in enumerate(sizes):
            key=f'security-report:{node}:{e.seconds:.6f}:{index}'
            p=e.send(node,'base','threat',size,identity,family_override='mesh',
                     service={'kind':'security_report','deadline':report['deadline']},channel_key=key,expires_at=report['deadline'])
            if p.id in e.scheduler.packets: self.report_packets[p.id]=identity
        e.event('OBSERVABILITY','report_offered',dict(report_id=identity,node=node,captured_at=e.seconds,bytes=len(wire),fragments=len(sizes),epoch=self.epoch))

    def result(self,data):
        report=self.pending.get(data.get('correlation_id'))
        if not report or report['epoch']!=self.epoch or data.get('channel_key') not in report['keys']: return
        packet=data['id']
        if packet in report['received']: return
        report['received'].add(packet); report['remaining']-=1
        report['failed']|=data['status']!='delivered'
        if report['remaining']: return
        self.pending.pop(report['id'],None)
        at=data['completed_at']
        if report['failed'] or at>=report['deadline']-1e-9:
            self.counters['failed']+=1; return
        previous=self.latest.get(report['node'])
        if previous and previous['captured_at']>=report['captured_at']:
            self.counters['out_of_order']+=1; return
        report['received_at']=at; self.latest[report['node']]=report; self.counters['delivered']+=1
        self.engine.event('OBSERVABILITY','report_received',dict(report_id=report['id'],node=report['node'],
            captured_at=report['captured_at'],received_at=at,age_s=at-report['captured_at'],epoch=self.epoch))

    def statuses(self):
        e=self.engine; rows={}
        for node in e.nodes.values():
            report=self.latest.get(node.id); age=e.seconds-report['captured_at'] if report else None
            reachable=bool(e.route(node.id,'base','mesh',True)) if self.eligible(node) else False
            status=('Quarantined' if node.containment=='quarantined' else 'Unavailable' if not node.available or node.health<=0
                    else 'Fresh' if age is not None and age<self.freshness-1e-9 else 'Stale' if report
                    else 'Pending' if reachable else 'Unreachable')
            rows[node.id]=dict(node=node.id,status=status,eligible=self.eligible(node),reachable=reachable,age_s=age,
                report_id=report['id'] if report else None,captured_at=report['captured_at'] if report else None,
                received_at=report.get('received_at') if report else None,epoch=self.epoch)
        return rows

    def metrics(self):
        rows={}; statuses=self.statuses()
        for node,status in statuses.items():
            if status['status']!='Fresh': continue
            report=self.latest[node]; row=deepcopy(report['payload'])
            row['source_age_s']=status['age_s']; row['source_collected_at']=report['payload']['timestamp']
            row['observation']=dict(report_id=report['id'],epoch=self.epoch,clock='simulation',
                captured_at=report['captured_at'],received_at=report['received_at'],simulation_time=self.engine.seconds,
                wall_captured_at=report['wall_captured_at'],freshness_s=self.freshness)
            rows[node]=row
        return rows

    def demands(self):
        return {(n.id,'base','threat'):float(self.last_sizes.get(n.id,2048)) for n in self.engine.nodes.values() if self.eligible(n)}

    def predict(self,links):
        e=self.engine; adj=build_adjacency(links,e.nodes,e.blocked|e.auto_blocked,control=True)
        routes={}; utilization=Counter(); delays={}
        for source,_,_ in self.demands():
            constraints=e.flow_constraints.get(f'{source}:base:threat',{})
            omitted=set(constraints.get('excluded_nodes',[])); blocked={pair(*edge) for edge in constraints.get('excluded_links',[])}
            route=shortest_path({k:v for k,v in links.items() if not omitted.intersection(k)},source,'base',e.nodes,
                e.blocked|e.auto_blocked|blocked,control=True) if omitted or blocked else shortest_path(links,source,'base',e.nodes,adjacency=adj,control=True)
            if 'threat' not in constraints.get('allowed_traffic_classes',['threat']): route=[]
            if not route: continue
            routes[source]=route; delay=0.; size=self.last_sizes.get(source,2048)
            for a,b in zip(route,route[1:]):
                link=links[pair(a,b)]; airtime=size*8/(link.bandwidth_mbps*1e6)
                utilization[a]+=airtime; utilization[b]+=airtime
                backlog=sum(p.size_bytes*8/(link.bandwidth_mbps*1e6) for p in e.scheduler.packets.values()
                            if p.status!='deferred' and p.hop+1<len(p.route) and set((a,b)).intersection(p.route[p.hop:p.hop+2]))
                delay+=airtime+link.latency_ms/1000+e.cfg['packets'].get('jitter_ms',0)/1000+backlog
            delays[source]=delay
        share=e.cfg.get('resilience',{}).get('protected_airtime_share',.10)
        feasible={n for n,path in routes.items() if delays[n]+max((utilization[p]/share for p in path),default=0)<self.freshness}
        return dict(feasible=feasible,reachable=set(routes),predicted_delays_s=delays,
                    maximum_report_utilization=max(utilization.values(),default=0.),basis='modeled_queue_and_serialization_estimate')

    def snapshot(self):
        if not self.enabled: return None
        rows=self.statuses(); counts=Counter(r['status'] for r in rows.values())
        return dict(epoch=self.epoch,clock='simulation',freshness_s=self.freshness,report_interval_s=1.,
            total=len(rows),eligible=sum(r['eligible'] for r in rows.values()),fresh=counts['Fresh'],
            statuses=dict(counts),nodes=list(rows.values()),counters=dict(self.counters),
            protected_airtime=dict(self.engine.scheduler.airtime_totals),
            evidence='modeled_packet_delivery_not_detector_verdict')
