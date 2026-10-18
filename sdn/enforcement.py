"""One policy representation, explicitly different enforcement capabilities."""
from __future__ import annotations
from abc import ABC, abstractmethod
from schemas.mission import RoutePolicy

class SDNEnforcementAdapter(ABC):
    capabilities: dict
    @abstractmethod
    def install_flow(self, policy: RoutePolicy): ...
    @abstractmethod
    def remove_flow(self, policy_id: str): ...
    @abstractmethod
    def block_link(self, a: str, b: str): ...
    @abstractmethod
    def restore_link(self, a: str, b: str): ...
    @abstractmethod
    def isolate_node(self, identity: str): ...
    @abstractmethod
    def restore_node(self, identity: str): ...
    @abstractmethod
    def get_installed_flows(self): ...
    def apply_route(self, policy): return self.install_flow(policy)

class SimulationSDNAdapter(SDNEnforcementAdapter):
    capabilities={'multi_hop':True,'logical_enforcement':True,'openflow':False}
    def __init__(self, engine): self.engine=engine
    def install_flow(self, policy): return self.engine.install_policy(policy)
    def remove_flow(self, policy_id): return self.engine.remove_policy(policy_id,forget=True)
    def block_link(self,a,b): return self.engine.block_link(a,b)
    def restore_link(self,a,b): return self.engine.restore_link(a,b)
    def isolate_node(self,identity): return self.engine.contain(identity,'quarantined','operator_request')
    def restore_node(self,identity): return self.engine.contain(identity,'normal','operator_recovery')
    def get_installed_flows(self): return list(self.engine.flows.values())

class RyuOpenFlowAdapter(SDNEnforcementAdapter):
    """Bridge to the existing barrier-acknowledged controller. No fabricated hops."""
    capabilities={'multi_hop':False,'logical_enforcement':False,'openflow':True}
    def __init__(self, controller): self.controller=controller
    def install_flow(self,policy):
        if policy.hops not in ([],[policy.source,'base']) or policy.destination!='base':
            return {'status':'unsupported','applied':False,'reason':'arbitrary_mesh_bindings_unavailable','capabilities':self.capabilities}
        return self.controller.apply_routing_decision(policy.family,policy.source)
    def remove_flow(self,policy_id):
        return {'status':'unsupported','applied':False,'reason':'use_existing_hold_or_route_replacement'}
    def block_link(self,a,b):
        return {'status':'unsupported','applied':False,'reason':'arbitrary_mesh_bindings_unavailable'}
    def restore_link(self,a,b): return self.block_link(a,b)
    def isolate_node(self,identity): return self.controller.apply_containment(identity,'quarantined','operator_request')
    def restore_node(self,identity): return self.controller.apply_containment(identity,'normal','operator_recovery')
    def get_installed_flows(self): return dict(self.controller._installed_routes)

class SDNPolicyEngine:
    def __init__(self,adapter): self.adapter=adapter
    def apply(self,raw): return self.adapter.apply_route(RoutePolicy.model_validate(raw))
