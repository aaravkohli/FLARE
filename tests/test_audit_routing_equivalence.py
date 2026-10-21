"""Shortest-path optimization compared with the retained unpruned algorithm."""
import heapq
import random
import pytest
from simulation.swarm.network import shortest_path


def unpruned_reference(graph,source,destination):
    heap=[(0.,(source,))];settled=set()
    while heap:
        cost,path=heapq.heappop(heap);node=path[-1]
        if node in settled:continue
        if node==destination:return list(path)
        if node=='base' and node!=source:continue
        settled.add(node)
        for peer,weight in graph.get(node,[]):
            if peer not in settled:heapq.heappush(heap,(cost+weight,path+(peer,)))
    return []

@pytest.mark.parametrize('seed',[7,42,99])
def test_optimized_routes_preserve_cost_ties_partitions_and_ground_endpoint_rules(seed):
    rng=random.Random(seed);ids=['base']+[f'u{i}' for i in range(18)]
    for density in [.05,.3,1.]:
        graph={n:[] for n in ids}
        for i,a in enumerate(ids):
            for b in ids[i+1:]:
                if rng.random()<density:
                    weight=rng.choice([0.,1.,2.,3.,rng.uniform(.05,20.)]);graph[a].append((b,weight));graph[b].append((a,weight))
        for peers in graph.values():peers.sort()
        for a in ids:
            for b in ids:
                expected=unpruned_reference(graph,a,b)
                actual=shortest_path({},a,b,{},adjacency=graph)
                assert actual==expected
                assert 'base' not in actual[1:-1]
