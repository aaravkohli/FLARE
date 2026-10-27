"""Shared packet-delivered route verifier; independent of flight autonomy."""
import math
from simulation.swarm.network import shortest_path
from simulation.swarm.topology import pair

class RouteVerification:
    def __init__(self,transport):self.transport=transport;self.epoch=0
    def start(self,target,candidate,flows,categories=None,assisted=False):
        a=self.transport;e=a.engine;self.epoch+=1
        a.guard=dict(target=target,tag=f'assist-{self.epoch}-{target}' if assisted else target,started_at=e.seconds,deadline=e.seconds+3,
            required=set(flows),flows=flows,categories=categories or {},receipts={},last_send=-math.inf,candidate=candidate,assisted=assisted)
        a.counters['guard_started']+=1
    def receive(self,message,now):
        g=self.transport.guard
        if not g or message['guard']!=g.get('tag',g['target']) or message['sent_at']<g['started_at']-1e-9:return
        source=message['recipient'];dest=message['peer'];key=source if dest=='base' else source+'|'+dest
        if key not in g['required'] or g['flows'][key]!=(source,dest):return
        path=message['forward_route']
        if not path or path[0]!=source or path[-1]!=dest:return
        receipts=g['receipts'].setdefault(key,[])
        if not receipts or message['sent_at']-receipts[-1]['sent_at']>=1-1e-9:
            receipts.append(dict(sent_at=message['sent_at'],received_at=now,path=path[:]))
    def permitted(self,g,key,path):
        a=self.transport;e=a.engine;source,dest=g['flows'][key];category=g.get('categories',{}).get(key,'routing')
        constraints=e.flow_constraints.get(f'{source}:{dest}:{category}',{})
        return self.flow_permitted((source,dest,category),path) and all(pair(x,y) in g['candidate'].links for x,y in zip(path,path[1:]))
    def flow_permitted(self,flow,path):
        a=self.transport;e=a.engine;source,dest,category=flow
        constraints=e.flow_constraints.get(f'{source}:{dest}:{category}',{})
        if category not in constraints.get('allowed_traffic_classes',[category]) or not a.permitted(path,'mesh',constraints):return False
        if category not in e.control_traffic and any(n in e.nodes and (e.nodes[n].containment in ('restricted','control_only') or e.nodes[n].selected_route=='hold' or e.families.get(n)=='hold') for n in path):return False
        return True
    def step(self):
        a=self.transport;e=a.engine;g=a.guard
        if not g:return
        if e.seconds-g['last_send']>=1-1e-9:
            g['last_send']=e.seconds
            for key in sorted(g['required']):
                source,dest=g['flows'][key];category=g.get('categories',{}).get(key,'routing');constraints=e.flow_constraints.get(f'{source}:{dest}:{category}',{})
                excluded=set(constraints.get('excluded_nodes',[]));blocked={pair(*x) for x in constraints.get('excluded_links',[])}
                path=shortest_path({edge:l for edge,l in g['candidate'].links.items() if not excluded.intersection(edge)},source,dest,e.nodes,e.blocked|e.auto_blocked|blocked,control=category in e.control_traffic)
                if path and self.permitted(g,key,path):a.send(path,'mesh','challenge',peer=dest,guard=g.get('tag',g['target']),original_flow=(source,dest,category) if g.get('assisted') else None)
        ready=all(len(g['receipts'].get(k,[]))>=2 and self.permitted(g,k,g['receipts'][k][-1]['path']) for k in g['required'])
        if ready:a.counters['guard_verified']+=1;return 'ready'
        if e.seconds>=g['deadline']-1e-9:a.counters['prevented_transitions']+=1;return 'failed'
