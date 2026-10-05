"""Structural networking algorithms; physical eligibility is always enforced."""
from __future__ import annotations
import math
from itertools import combinations
from schemas.mission import TOPOLOGIES

def pair(a: str, b: str) -> tuple[str,str]: return tuple(sorted((a,b)))

def topology_edges(name, drones, eligible, previous=frozenset()):
    if name not in TOPOLOGIES: raise ValueError('unknown topology')
    nodes=sorted((d for d in drones if d.available and d.health>0 and d.containment!='quarantined'),key=lambda d:d.index)
    ids=[d.id for d in nodes]; edges=set(); heads=[]
    def nearest(d, limit):
        peers=sorted((p for p in nodes if p.id!=d.id and pair(d.id,p.id) in eligible),key=lambda p:(math.dist(d.position,p.position),p.index))
        return [p.id for p in peers[:limit]]
    if name=='full_mesh': edges={pair(a,b) for a,b in combinations(ids,2)}
    elif name in ('partial_mesh','manet'):
        for d in nodes:
            peers=nearest(d,4 if name=='partial_mesh' else 8)
            if name=='manet':
                # Retain eligible discovered peers first, preventing churn at equal quality.
                retained=[p for p in d.neighbors if pair(d.id,p) in eligible and p in ids]
                peers=list(dict.fromkeys(retained+peers))[:8]
            edges.update(pair(d.id,p) for p in peers)
    elif name=='star' and nodes:
        head=max(nodes,key=lambda d:(d.role in ('gateway','leader'),d.trust if d.trust is not None else .8,d.battery,-d.index))
        heads=[head.id]; edges={pair(head.id,p) for p in ids if p!=head.id}
    elif name in ('ring','chain'):
        edges={pair(a,b) for a,b in zip(ids,ids[1:])}
        if name=='ring' and len(ids)>2: edges.add(pair(ids[0],ids[-1]))
    elif name=='tree': edges={pair(ids[(i-1)//2],ids[i]) for i in range(1,len(ids))}
    elif name=='grid':
        side=math.ceil(math.sqrt(len(ids)))
        for i,a in enumerate(ids):
            if i%side<side-1 and i+1<len(ids): edges.add(pair(a,ids[i+1]))
            if i+side<len(ids): edges.add(pair(a,ids[i+side]))
    elif name in ('cluster_mesh','hybrid_mesh'):
        remaining=nodes[:]
        while remaining:
            head=max(remaining,key=lambda d:(d.role in ('cluster_head','gateway'),d.trust if d.trust is not None else .8,d.battery,-d.index))
            members=sorted(remaining,key=lambda d:(math.dist(head.position,d.position),d.index))[:10]
            cluster=len(heads); heads.append(head.id)
            for d in members:
                d.cluster=cluster
                if d.id!=head.id: edges.add(pair(head.id,d.id))
            member_ids={d.id for d in members}; remaining=[d for d in remaining if d.id not in member_ids]
        edges.update(pair(a,b) for a,b in combinations(heads,2))
        if name=='hybrid_mesh':
            for d in nodes: edges.update(pair(d.id,p) for p in nearest(d,4))
    return edges.intersection(eligible), heads
