"""Topology policy is independent of placement; every edge must pass radio eligibility."""
from __future__ import annotations
import math
from itertools import combinations
from schemas.mission import TOPOLOGIES, LEGACY_TOPOLOGIES

SUGGESTED_FORMATIONS = dict(full_mesh='distributed', partial_mesh='distributed', star='circle',
    ring='circle', tree='v', cluster_mesh='cluster', grid='grid', random_geometric='distributed',
    hybrid_mesh='cluster', self_healing_mesh='distributed')
DESCRIPTIONS = dict(full_mesh='Every reachable pair', partial_mesh='Four nearest reachable neighbors',
    star='Leader and reachable spokes', ring='Stable circular neighbors', tree='Stable binary hierarchy',
    cluster_mesh='Cluster heads and local members', grid='Adjacent grid slots',
    random_geometric='Spatial neighbors within 65% of radio range', hybrid_mesh='Hierarchy with redundant peer links',
    self_healing_mesh='Stable peers, component repair and alternate routes')

def topology_metadata():
    return [dict(id=t, label={'cluster_mesh':'Cluster-based mesh', 'grid':'Grid mesh',
        'random_geometric':'Random geometric mesh', 'self_healing_mesh':'Dynamic self-healing mesh'}.get(t,t.replace('_',' ').title()),
        description=DESCRIPTIONS[t], suggested_formation=SUGGESTED_FORMATIONS[t]) for t in TOPOLOGIES]

def pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a,b)))

def topology_edges(name, drones, eligible, previous=frozenset(), state=None, now=0., discovery_timeout=.6):
    if name not in TOPOLOGIES + LEGACY_TOPOLOGIES:
        raise ValueError('unknown topology')
    state = state if state is not None else {}
    members=sorted(drones,key=lambda d:d.index)
    membership=tuple(d.id for d in members)
    if state.get('membership') != membership or state.get('topology') != name:
        state.clear(); state.update(membership=membership,topology=name)
    nodes=[d for d in members if d.available and d.health>0 and d.containment!='quarantined']
    ids=[d.id for d in members]; live={d.id for d in nodes}; edges=set(); heads=[]
    for d in members: d.cluster=-1
    nearest_cache={}
    def nearest(d,limit):
        if d.id not in nearest_cache:
            peers=sorted((p for p in nodes if p.id!=d.id and pair(d.id,p.id) in eligible),
                         key=lambda p:(math.dist(d.position,p.position),p.index))
            nearest_cache[d.id]=[p.id for p in peers]
        return nearest_cache[d.id][:limit]
    def priority(d):
        return (d.role in ('cluster_head','gateway','leader'),d.trust if d.trust is not None else .8,d.battery,-d.index)
    def elect(key,candidates):
        candidates=[d for d in candidates if d.containment=='normal' and d.selected_route!='hold' and (d.threat or 0)<=.8]
        if not candidates: return None
        candidate=max(candidates,key=priority)
        old=next((d for d in candidates if d.id==state.get(key)),None)
        if old and priority(old)[0]>=priority(candidate)[0]:
            state.pop(key+'_missing',None); return old
        if state.get(key) and not old:
            missing=state.setdefault(key+'_missing',now)
            if now-missing < discovery_timeout-1e-9: return None
        state[key]=candidate.id; state.pop(key+'_missing',None)
        return candidate
    if name=='full_mesh':
        edges=set(eligible)
    elif name in ('partial_mesh','manet','self_healing_mesh'):
        for d in nodes:
            peers=nearest(d,8 if name=='manet' else 4)
            if name in ('manet','self_healing_mesh'):
                retained=sorted(b if a==d.id else a for a,b in previous if d.id in (a,b)
                                and pair(a,b) in eligible and a in live and b in live)
                peers=list(dict.fromkeys(retained+peers))[:8 if name=='manet' else 4]
            edges.update(pair(d.id,p) for p in peers)
        if name=='self_healing_mesh':
            # Kruskal augmentation only joins components of the physically eligible graph.
            forwarding={d.id for d in nodes if d.containment=='normal' and d.selected_route!='hold' and (d.threat or 0)<=.8}
            parent={n:n for n in forwarding}
            def root(n):
                while parent[n]!=n: parent[n]=parent[parent[n]]; n=parent[n]
                return n
            def join(a,b): parent[root(a)]=root(b)
            for a,b in sorted(edges):
                if a in forwarding and b in forwarding: join(a,b)
            positions={d.id:d.position for d in nodes}
            candidates=[e for e in eligible if all(n in forwarding for n in e)]
            for a,b in sorted(candidates,key=lambda e:(math.dist(positions[e[0]],positions[e[1]]),e)):
                if root(a)!=root(b): edges.add(pair(a,b)); join(a,b)
    elif name=='random_geometric':
        lookup={d.id:d for d in nodes}
        edges={e for e in eligible if all(n in lookup for n in e) and
               math.dist(lookup[e[0]].position,lookup[e[1]].position)<=.65*min(lookup[n].range_m for n in e)}
    elif name=='star':
        head=elect('head',nodes)
        if head: heads=[head.id]; edges={pair(head.id,p) for p in live if p!=head.id}
    elif name in ('ring','chain'):
        edges={pair(a,b) for a,b in zip(ids,ids[1:])}
        if name=='ring' and len(ids)>2: edges.add(pair(ids[0],ids[-1]))
    elif name=='tree':
        edges={pair(ids[(i-1)//2],ids[i]) for i in range(1,len(ids))}
    elif name=='grid':
        side=math.ceil(math.sqrt(len(ids)))
        for i,a in enumerate(ids):
            if i%side<side-1 and i+1<len(ids): edges.add(pair(a,ids[i+1]))
            if i+side<len(ids): edges.add(pair(a,ids[i+side]))
    elif name in ('cluster_mesh','hybrid_mesh'):
        for cluster,start in enumerate(range(0,len(members),10)):
            group=members[start:start+10]
            for d in group: d.cluster=cluster
            head=elect(f'head_{cluster}',[d for d in group if d.id in live])
            if head:
                heads.append(head.id)
                edges.update(pair(head.id,d.id) for d in group if d.id!=head.id)
            if name=='hybrid_mesh':
                group_ids={d.id for d in group}
                for d in group:
                    if d.id in live: edges.update(pair(d.id,p) for p in nearest(d,len(nodes)) if p in group_ids and p in nearest(d,2))
        edges.update(pair(a,b) for a,b in combinations(heads,2))
        if name=='hybrid_mesh':
            for d in nodes: edges.update(pair(d.id,p) for p in nearest(d,2))
    return {e for e in edges if e in eligible and all(n in live for n in e)},heads
