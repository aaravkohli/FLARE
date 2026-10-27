"""Matched reference ordering versus cohort sorting; controlled lossless fixture."""
from collections import deque
import heapq
import json
from pathlib import Path
import random
import time
from simulation.swarm.network import PacketScheduler,Link
from simulation.swarm.topology import pair


def reference_pop(s):
    at=s.heap[0][0];batch=[]
    while s.heap and s.heap[0][0]<=at+1e-12:batch.append(heapq.heappop(s.heap))
    def key(event):
        _,order,identity,kind,generation=event;p=s.packets.get(identity)
        if kind!='ready' or p is None or generation!=p.repair_count:return (-1,0,order)
        if not p.service_kind or p.hop+1>=len(p.route):return (1,0,order)
        a,b=p.route[p.hop:p.hop+2];radios=(('radio',a),('radio',b))
        return (0 if s.protected_due(radios,at) else 2,p.service_deadline or p.expires_at,order)
    index=min(range(len(batch)),key=lambda i:key(batch[i]));event=batch.pop(index)
    for other in batch:heapq.heappush(s.heap,other)
    return event


def sample(reference,count=300):
    rows=[];s=PacketScheduler(dict(ttl_s=3,retries=0,capacity=1000,queue_limit=1000,seed=7),random.Random(7),
        lambda k,d:rows.append((k,d['id'],d['start_at'],d['end_at'],d['completed_at'],d['status'])))
    s.protected_scheduling=True
    if reference:s.pop_event=lambda:reference_pop(s)
    for i in range(count):s.emit('a','b','sensor',512,['a','b'],0,0,service={'kind':'registered','deadline':2} if i%10==0 else None)
    start=time.process_time();s.step(3,{pair('a','b'):Link('a','b',1,-40,30,12,0,0)},lambda *_:True);cpu=time.process_time()-start
    return cpu,rows


def main():
    results=[]
    for _ in range(5):
        a,old=sample(True);b,new=sample(False)
        if old!=new:raise AssertionError('Ordering changed packet events')
        results.append(dict(reference_cpu_s=a,cohort_cpu_s=b,equal_events=len(old)))
    out=Path('results/evidence_resilience/scheduler-profile.json');out.write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))

if __name__=='__main__':main()
