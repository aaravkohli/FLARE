"""Independent receipt/cohort reduction of pilot packet traces, no simulator imports."""
import gzip
import json
import math
from pathlib import Path


class EvidenceRecovery:
    """Return to >=99% eligible freshness after the declared condition ends.

    Normal has no injected recovery anchor. Loss appearing only after the
    condition has ended cannot retroactively create a condition recovery.
    """
    def __init__(self,scenario):self.scenario=scenario;self.gap=False;self.value=None
    def observe(self,seconds,ratio):
        if self.scenario=='normal' or ratio is None:return
        if 8<=seconds<25 and ratio<.99:self.gap=True
        if seconds>=25 and self.gap and ratio>=.99 and self.value is None:self.value=(seconds-25)*1000
    @property
    def unrecovered(self):return self.gap and self.value is None


def verify_trace(filename,row):
    reports={};results={};queued=set();acks=[];commands=[];primary=[];highest=0;deferred=set()
    with gzip.open(filename,'rt') as trace:
        for index,line in enumerate(trace):
            event=json.loads(line);data=event.get('data',{});kind=event['kind']
            if not kind.startswith('packet_') or 'id' not in data:continue
            highest=max(highest,int(data['id'][1:]))
            if kind=='packet_queued':queued.add(data['id'])
            if kind=='packet_deferred':deferred.add(data['id'])
            if data.get('service_kind')=='security_report' and kind in ('packet_queued','packet_result'):
                report=reports.setdefault(data['correlation_id'],dict(node=data['source'],capture=data['created_at'],deadline=data['service_deadline'],packets={},last_index=index))
                fragment=report['packets'].setdefault(data['id'],{})
                if kind=='packet_result':fragment.update(data);report['last_index']=index
            if kind!='packet_result':continue
            if data['id'] in results:raise ValueError('duplicate final packet result')
            results[data['id']]=data
            if data.get('correlation_id')=='reference' and 5<=data['created_at']<65:primary.append(data)
            if data.get('service_kind')=='ack' and data['status']=='delivered':acks.append(data)
            if data.get('service_kind')=='command':commands.append(data)
    if len(results)!=highest or highest!=row['packet_accounting']['generated']:raise ValueError('packet conservation/trace incompleteness')
    delivered=[p for p in primary if p['status']=='delivered']
    if len(primary)!=row['generated'] or len(delivered)!=row['delivered']:raise ValueError('primary cohort mismatch')
    latency=sum((p['completed_at']-p['created_at'])*1000 for p in delivered)/len(delivered) if delivered else None
    if latency is not None and not math.isclose(latency,row['average_latency_ms'],abs_tol=1e-8):raise ValueError('latency mismatch')
    receipts=[];nodes=[]
    for identity,report in reports.items():
        node=report['node']
        if node not in nodes:nodes.append(node)
        packets=list(report['packets'].values())
        if not packets or any(p.get('status')!='delivered' for p in packets):continue
        at=max(p['completed_at'] for p in packets)
        if at>=report['deadline']-1e-9:continue
        receipts.append((at,report['last_index'],node,report['capture']))
    receipts.sort();cursor=0;latest={};coverage=[];ages=[];gap=False;recovery=None;accepted=0;seconds=0.
    for _ in range(1700):
        seconds+=.05
        while cursor<len(receipts) and receipts[cursor][0]<=seconds+1e-12:
            _,_,node,capture=receipts[cursor];cursor+=1
            if node not in latest or capture>latest[node]:latest[node]=capture;accepted+=1
        if not 5<=seconds<65:continue
        eligible=[n for n in nodes if not (row['scenario']=='single_failure' and n==nodes[0] and seconds+1e-9>=8 and seconds<25-1e-9)]
        if not eligible:continue
        ratio=sum(n in latest and seconds-latest[n]<3-1e-9 for n in eligible)/len(eligible);coverage.append(ratio)
        ages.extend(seconds-latest[n] for n in eligible if n in latest)
        if row['scenario']!='normal' and 8<=seconds<25 and ratio<.99:gap=True
        if seconds>=25 and gap and ratio>=.99 and recovery is None:recovery=(seconds-25)*1000
    fresh=sum(coverage)/len(coverage) if coverage else None
    if fresh is not None and not math.isclose(fresh,row['fresh_evidence_ratio'],abs_tol=1e-9):raise ValueError(f"receipt freshness mismatch: {fresh} != {row['fresh_evidence_ratio']}")
    if accepted!=row['report_counts']['delivered']:raise ValueError('report receipt count mismatch')
    if len(acks)/len(commands)!=row['command_ack_ratio']:raise ValueError('command acknowledgment mismatch')
    return dict(packet_results=len(results),primary_packets=len(primary),report_receipts=accepted,
        fresh_evidence_ratio=fresh,evidence_recovery_ms=recovery,evidence_unrecovered=gap and recovery is None,
        deferred_packets=len(deferred),deferred_dropped=sum(results[p]['status']=='dropped' for p in deferred),
        report_age_recomputed_s=sum(ages)/len(ages) if ages else None)
