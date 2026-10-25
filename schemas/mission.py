"""Additive, versioned swarm interfaces. Existing model contracts stay unchanged."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

TOPOLOGIES = ('full_mesh', 'partial_mesh', 'star', 'ring', 'tree', 'cluster_mesh',
              'grid', 'random_geometric', 'hybrid_mesh', 'self_healing_mesh')
LEGACY_TOPOLOGIES = ('chain', 'manet')
FORMATIONS = ('grid', 'v', 'line', 'circle', 'diamond', 'wedge', 'column', 'distributed', 'cluster')
ROLES = ('scout', 'relay', 'leader', 'sensor', 'gateway', 'surveillance',
         'communication_relay', 'edge_computing', 'cluster_head', 'standard')
TRAFFIC = ('telemetry', 'position', 'heartbeat', 'discovery', 'sensor', 'threat',
           'routing', 'emergency', 'command', 'ack', 'fl_update')
CONTROL_TRAFFIC = frozenset(('heartbeat', 'discovery', 'threat', 'routing', 'emergency', 'command', 'ack'))

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class MissionConfig(Strict):
    expected_revision: int = Field(ge=0)
    count: Optional[int] = Field(default=None, ge=10, le=100)
    topology: Optional[str] = None
    topology_mode: Optional[Literal['auto', 'manual']] = None
    formation: Optional[str] = None
    speed: Optional[Literal[0.1, 0.25, 0.5, 1.0, 2.0, 4.0]] = None
    range_m: Optional[float] = Field(default=None, ge=50, le=1000)
    seed: Optional[int] = Field(default=None, ge=0, le=2**32-1)
    @model_validator(mode='after')
    def enums(self):
        if self.topology is not None and self.topology not in TOPOLOGIES + LEGACY_TOPOLOGIES:
            raise ValueError('unknown topology')
        if self.formation is not None and self.formation not in FORMATIONS:
            raise ValueError('unknown formation')
        return self

class MissionProbe(Strict):
    session_id: str
    expected_revision: int = Field(ge=0)
    source: str
    destination: str
    size_bytes: int = Field(default=512, ge=1, le=65536)

class ExperimentRequest(Strict):
    mode: Literal['compare', 'benchmark'] = 'compare'
    topologies: list[str] = Field(default_factory=lambda: ['partial_mesh', 'self_healing_mesh'], min_length=1, max_length=10)
    count: int = Field(default=30, ge=10, le=100)
    seeds: list[int] = Field(default_factory=lambda: [7, 42, 99], min_length=1, max_length=10)
    scenario: str = 'normal'
    range_m: float = Field(default=350, ge=50, le=1000)
    duration_s: float = Field(default=60, ge=10, le=300)
    warmup_s: float = Field(default=5, ge=0, le=30)
    @model_validator(mode='after')
    def valid_experiment(self):
        if len(set(self.topologies)) != len(self.topologies) or any(t not in TOPOLOGIES for t in self.topologies):
            raise ValueError('select unique primary topologies')
        if self.mode == 'compare' and len(self.topologies) != 2:
            raise ValueError('comparison requires two topologies')
        if len(set(self.seeds))!=len(self.seeds) or any(seed < 0 or seed >= 2**32 for seed in self.seeds) or self.warmup_s >= self.duration_s:
            raise ValueError('invalid seeds or warmup')
        return self

class ExperimentControl(Strict):
    action: Literal['play', 'pause', 'reset', 'cancel']

class MissionControl(Strict):
    action: Literal['play', 'pause', 'reset', 'disconnect_controller', 'reconnect_controller']
    expected_revision: int = Field(ge=0)

class MissionCommand(Strict):
    schema_version: Literal["mission_command_v1"] = "mission_command_v1"
    session_id: Optional[str] = None
    expected_revision: Optional[int] = Field(default=None,ge=0)
    command_id: str = Field(min_length=1, max_length=100)
    targets: list[str] = Field(min_length=1, max_length=100)
    action: Literal['move', 'hold', 'resume', 'return', 'altitude', 'formation', 'route',
                    'reroute', 'role', 'disconnect', 'reconnect', 'isolate', 'restore', 'emergency', 'waypoints']
    parameters: dict = Field(default_factory=dict)
    ttl_s: float = Field(default=10, gt=0, le=60)

class RoutePolicy(Strict):
    schema_version: Literal['sdn_policy_v1'] = 'sdn_policy_v1'
    policy_id: str
    session_id: str
    revision: int = Field(ge=0)
    source: str
    destination: str = 'base'
    traffic_class: str = 'telemetry'
    family: Literal['direct', 'satellite', 'mesh', 'hold']
    hops: list[str]
    restrictions: dict = Field(default_factory=dict)
    decision_id: Optional[str] = None
    @model_validator(mode='after')
    def valid(self):
        if self.traffic_class not in TRAFFIC:
            raise ValueError('unknown traffic class')
        if set(self.restrictions)-{'backup_hops','excluded_nodes','excluded_links','allowed_traffic_classes'}: raise ValueError('unsupported policy restriction')
        for key in ('backup_hops','excluded_nodes'):
            value=self.restrictions.get(key,[])
            if not isinstance(value,list) or any(not isinstance(n,str) for n in value): raise ValueError('node restrictions must be lists of identities')
        edges=self.restrictions.get('excluded_links',[])
        if not isinstance(edges,list) or any(not isinstance(e,list) or len(e)!=2 or any(not isinstance(n,str) for n in e) for e in edges): raise ValueError('excluded links require endpoint pairs')
        allowed=self.restrictions.get('allowed_traffic_classes',list(TRAFFIC))
        if not isinstance(allowed,list) or any(c not in TRAFFIC for c in allowed) or self.traffic_class not in allowed: raise ValueError('traffic class forbidden by policy')
        if self.family=='hold' and (self.hops or self.traffic_class in CONTROL_TRAFFIC): raise ValueError('network HOLD has no hops and suspends data traffic')
        if self.family != 'hold' and (len(self.hops) < 2 or self.hops[0] != self.source or self.hops[-1] != self.destination):
            raise ValueError('route endpoints do not match policy')
        if len(self.hops) != len(set(self.hops)):
            raise ValueError('route contains a loop')
        return self

class FLTransfer(Strict):
    client_id: str
    round: int = Field(ge=1)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(gt=0, le=100_000_000)
    session_id: str


class MissionAttack(Strict):
    schema_version: Literal['mission_attack_v1'] = 'mission_attack_v1'
    session_id: str
    expected_revision: int = Field(ge=0)
    attack_id: str = Field(min_length=1, max_length=100)
    group: Literal['ew', 'traffic', 'fl'] = 'ew'
    profile: str
    targets: list[str] = Field(min_length=1, max_length=100)
    paths: list[Literal['direct', 'satellite', 'mesh']] = Field(default_factory=lambda: ['mesh'])
    duration_s: float = Field(default=15, gt=0, le=300)


class ClearMissionAttacks(Strict):
    session_id: str
    expected_revision: int = Field(ge=0)
    attack_id: Optional[str] = None
    group: Optional[Literal['ew', 'traffic', 'fl']] = None
    targets: Optional[list[str]] = None
