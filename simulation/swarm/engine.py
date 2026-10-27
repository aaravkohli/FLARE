"""Single authoritative mission state and logical SDN data plane."""
from __future__ import annotations
from collections import Counter, deque
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import random
import time
import uuid
import yaml

from schemas.mission import CONTROL_TRAFFIC, FORMATIONS, ROLES, RoutePolicy, MissionCommand, MissionAttack
from simulation.swarm.attacks import AttackConditions
from simulation.swarm.adaptive_topology import AdaptiveTopology
from simulation.swarm.movement import Drone, advance, formation_positions
from simulation.swarm.network import build_adjacency, Link, PacketScheduler, radio_link, shortest_path
from simulation.swarm.terrain import Terrain
from simulation.swarm.topology import pair, topology_edges
from simulation.swarm.session import write_registry
from simulation.swarm.scenarios import DEMO_PRESETS, step_scenario
from simulation.swarm.performance import Performance
from simulation.swarm.observability import Observability
from simulation.swarm.resilience_control import ResilienceControl, ResilienceSettings
from simulation.swarm.autonomy import AutonomousRecovery
from simulation.swarm.uav_assistance import UavAssistance
from simulation.swarm.flight_safety import FlightSafety
from schemas.autonomy import SafetyProfile
from simulation.swarm.recovery_scenarios import PRESETS as RECOVERY_PRESETS,step as step_recovery

BASE_POSITION=[-280.,35.,170.]
SCENARIOS={
 'normal':dict(count=30,topology='manet',formation='distributed'),
 'large_manet':dict(count=50,topology='manet',formation='distributed'),
 'jamming':dict(count=50,topology='manet',formation='grid',preferred_family='mesh'),
 'poisoning':dict(count=30,topology='manet',formation='distributed'),
 'failures':dict(count=30,topology='hybrid_mesh',formation='cluster'),
 'controller_loss':dict(count=30,topology='manet',formation='distributed'),
 'rerouting':dict(count=50,topology='partial_mesh',formation='grid',preferred_family='mesh'),
}

SCENARIOS.update(DEMO_PRESETS)
SCENARIOS.update(RECOVERY_PRESETS)

class RevisionConflict(ValueError): pass

class SwarmEngine:
    control_traffic = CONTROL_TRAFFIC
    def __init__(self,records,config=None,registry_path=None,isolated=False):
        path=Path(__file__).resolve().parents[2]/'config/swarm_config.yaml'
        self.cfg=deepcopy(config or yaml.safe_load(path.read_text()))
        if self.cfg.get('adaptive_policy','balanced_v1') not in ('balanced_v1','evidence_resilience_v1'): raise ValueError('unknown adaptive policy')
        ResilienceSettings.model_validate(self.cfg.get('resilience',{}))
        if self.cfg.get('autonomy_mode','off') not in ('off','recovery_v1'):raise ValueError('unknown autonomy mode')
        SafetyProfile.model_validate(self.cfg.get('safety_profile',{}))
        self.isolated=isolated
        self.trace_sink=None
        self.registry_path=None if isolated else registry_path
        self.records={r['drone_id']:deepcopy(r) for r in records}
        self.reserved=sorted((r['drone_id'] for r in records if r['enabled']),key=lambda n:self.records[n]['index'])
        self.terrain=Terrain(); self.seed=self.cfg['seed']; self.topology=self.cfg['topology']; self.formation=self.cfg['formation']
        self.topology_mode=self.cfg.get('topology_mode','manual')
        if self.topology_mode not in ('auto','manual'): raise ValueError('unknown topology mode')
        self.manual_topology=self.topology; self._configuring=False
        self.count=self.cfg['default_count']; self.pending_seed=None; self.speed=1.; self.revision=0; self.event_cursor=0
        self.events=deque(maxlen=3000); self.commands={}; self.transfers={}; self.security={}
        self.reset()
    def event(self,category,kind,data=None):
        self.event_cursor+=1
        self.events.append(dict(schema_version='mission_event_v1',cursor=self.event_cursor,session_id=self.session_id,
                                simulation_time=self.scheduler.event_time if hasattr(self,'scheduler') and self.scheduler.event_time is not None else self.seconds,revision=self.revision,evidence_source='packet_simulation',correlation_id=(data or {}).get('correlation_id',(data or {}).get('command_id')),timestamp=time.time(),category=category,kind=kind,data=data or {}))
    def reset(self):
        self.performance=Performance()
        if self.topology_mode=='manual': self.topology=self.manual_topology
        if self.pending_seed is not None:
            self.seed=self.pending_seed; self.cfg['seed']=self.seed; self.pending_seed=None
        import os
        if not self.isolated and os.getenv('FLARE_BYZANTINE_STATE_PATH'):
            from simulation.byzantine_state import reset_attack_state
            reset_attack_state()
        self.session_id=uuid.uuid4().hex; self.seconds=0.; self.playing=False; self.accumulator=0.
        self.rng=random.Random(self.seed); self.nodes={}; self.links={}; self.radio_links={}; self.flows={}; self.forwarding={}; self.blocked=set(); self.auto_blocked=set(); self.jammers=[]
        self._route_cache={}; self._adjacency_cache={}; self._backup_cache={}; self.route_preferences={}; self.families={}; self.flow_constraints={}; self.controller_connected=True; self.commands={}; self.transfers={}; self.security={}
        self.events.clear(); self.revision+=1; self.network_revision=0; self.reroutes=0; self.failed_routes=0
        self.topology_state={}; self.packet_events=deque(maxlen=50000); self.packet_cursor=0
        self.disconnected_links=deque(maxlen=5000); self.recovery={}; self.scenario_actions=set()
        self.last_network=-1.; self.last_traffic=-1.; self.scenario='normal'; self.scenario_fired=False; self.poisoning_client=None
        self.failed_families=set(); self.packet_results=deque(maxlen=100)
        self.offer_sequences=Counter()
        self.attacks=AttackConditions(self)
        self.runtime={'orchestrator':{'status':'waiting_for_play'},'flower':{'status':'waiting_for_play','round':0,'participants':[]}}
        self.scheduler=PacketScheduler(dict(self.cfg['packets'],seed=self.seed),self.rng,self.packet_event)
        scheduler_step=self.scheduler.step
        def timed_step(*args,**kwargs):
            with self.performance.measure('scheduling'): return scheduler_step(*args,**kwargs)
        self.scheduler.step=timed_step
        self.physical_links={}; self.observability=Observability(self); self.resilience=ResilienceControl(self)
        self.scheduler.protected_scheduling=self.resilience.enabled and self.cfg.get('resilience',{}).get('protected_scheduling',True)
        self.scheduler.protected_share=self.cfg.get('resilience',{}).get('protected_airtime_share',.10)
        self.assistance=UavAssistance(self)
        self.adaptive=AdaptiveTopology(self)
        self.autonomy=AutonomousRecovery(self);self.flight_safety=FlightSafety(self)
        self.resize(self.count,initial=True); self.rebuild(); self.event('COMMAND','mission_reset',{'seed':self.seed})
        self.flight_safety.sync()
        if self.autonomy.enabled:self.autonomy.sync()
    def _clear_routes(self):
        self._route_cache.clear(); self._adjacency_cache.clear(); self._backup_cache.clear()
    def check_revision(self,revision):
        if revision!=self.revision: raise RevisionConflict(f'expected revision {self.revision}, received {revision}')
    def resize(self,count,initial=False):
        if not 10<=count<=self.cfg['max_count']: raise ValueError('count outside supported mission capacity')
        if self.autonomy.enabled:
            bound=self.flight_safety.profile.geofence_m
            if any(abs(p[j])+5>bound for p in formation_positions(self.formation,count) for j in (0,2)):
                raise ValueError('Fleet formation exceeds the configured safety geofence')
        while len(self.reserved)<count:
            ordinal=max((r['index'] for r in self.records.values()),default=0)+1
            identity=f'sim_uav_{ordinal:03d}'
            mac=f'02:fa:{(ordinal>>24)&255:02x}:{(ordinal>>16)&255:02x}:{(ordinal>>8)&255:02x}:{ordinal&255:02x}'
            used={r['mac'] for r in self.records.values()}
            if mac in used: raise ValueError('session MAC allocation collision')
            self.records[identity]=dict(drone_id=identity,display_name=f'UAV-{len(self.reserved)+1:03d}',index=ordinal,
                mac=mac,access_port=max((r['access_port'] for r in self.records.values()),default=3)+1,enabled=True,
                rf_profile={'rssi_offset':0.,'pdr_offset':0.,'latency_factor':1.})
            self.reserved.append(identity)
        active=self.reserved[:count]; retired=set(self.nodes)-set(active)
        self.scheduler.retire(retired,self.seconds)
        for identity in retired:
            self.attacks.clear(targets=[identity],reason='node_retired')
            for record in self.commands.values():
                outcome=record['outcomes'].get(identity)
                if outcome and outcome['status'] in ('pending','queued','executed_ack_pending'):
                    outcome.update(status='retired',reason='UAV removed from mission')
            self.nodes.pop(identity); self.families.pop(identity,None); self.route_preferences.pop(identity,None); self.security.pop(identity,None)
            self.event('NETWORK','node_retired',{'drone_id':identity})
        slots=formation_positions(self.formation,count)
        for i,identity in enumerate(active):
            if identity not in self.nodes:
                p=slots[i][:]; p[1]=max(p[1],self.terrain.height(p[0],p[2])+35)
                self.nodes[identity]=Drone(identity,f'UAV-{self.reserved.index(identity)+1:03d}',i+1,p,p[:],range_m=self.cfg['radio']['range_m'])
                self.event('NETWORK','node_joined',{'drone_id':identity})
            self.records[identity]['enabled']=True
        for identity,r in self.records.items(): r['enabled']=identity in active
        self.count=count; self.slots=slots
        self.flight_safety.sync()
        if self.autonomy.enabled:self.autonomy.sync()
        for key,flow in list(self.flows.items()):
            if flow['source'] in retired or flow['destination'] in retired: self.remove_policy(key)
        if self.registry_path: write_registry(Path(self.registry_path),list(self.records.values()))
        if not initial: self.revision+=1; self.rebuild()
    def configure(self,request):
        self.check_revision(request.expected_revision)
        if request.autonomy_mode=='recovery_v1' and not self.autonomy.enabled:
            if not self.controller_connected:raise ValueError('Provision autonomous recovery before command-link loss; reset or restore communication first')
            for n,d in self.nodes.items():
                if not d.available or d.containment=='quarantined':continue
                options=[(self.route(n,'base','mesh',True),'mesh'),([n,'base'],'direct'),([n,'base'],'satellite')]
                if not any(self.autonomy.permitted(path,family) for path,family in options):
                    raise ValueError(f'Provision autonomous recovery before communication loss for {n}')
        if (request.autonomy_mode or self.cfg.get('autonomy_mode','off'))=='recovery_v1':
            profile=request.safety_profile or self.flight_safety.profile;bound=profile.geofence_m
            proposed=formation_positions(request.formation or self.formation,request.count or self.count)
            if any(abs(p[j])+5>bound for p in proposed for j in (0,2)) or any(abs(d.position[j])+5>bound for d in self.nodes.values() for j in (0,2)):
                raise ValueError('Proposed fleet or formation exceeds the configured safety geofence')
        if request.safety_profile is not None:
            bound=request.safety_profile.geofence_m
            if any(abs(d.position[j])+5>bound for d in self.nodes.values() for j in (0,2)):raise ValueError('Current fleet lies outside the proposed geofence')
        if request.topology is not None and self.autonomy.enabled:
            c=self.adaptive.candidate(request.topology,self.permitted_physical_links(),self.adaptive.demands()[0],{})
            self.autonomy.check_candidate(c)
        previous_mode=self.topology_mode
        pending_manual=bool(self.adaptive.transition and not self.adaptive.transition['automatic'])
        self.adaptive.cancel('Configuration changed')
        if request.affected_uav_assist is not None:
            self.cfg['affected_uav_assist']=request.affected_uav_assist;self.assistance.configure()
        self.assistance.proposal=None;self.assistance.winner=None;self.assistance.wins=0
        if request.autonomy_mode is not None:
            reenable=request.autonomy_mode=='recovery_v1' and not self.autonomy.enabled
            self.cfg['autonomy_mode']=request.autonomy_mode
            self.autonomy.guard=None
            if reenable:
                for n in list(self.autonomy.controllers):
                    if not self.flight_safety.supervisors[n].latched:self.autonomy.controllers.pop(n)
            if self.autonomy.enabled:self.flight_safety.sync();self.autonomy.sync()
            self.event('RECOVERY','autonomy_mode_changed',{'mode':request.autonomy_mode,'safety_latches_retained':True})
        if request.safety_profile is not None:
            self.cfg['safety_profile']=request.safety_profile.model_dump()
            self.flight_safety.profile=request.safety_profile.model_copy(deep=True)
            if (self.autonomy.enabled or any(s.latched for s in self.flight_safety.supervisors.values())) and self.seconds>0:self.autonomy.configure_profile(request.safety_profile)
            else:
                for n,s in self.flight_safety.supervisors.items():
                    s.profile=request.safety_profile.model_copy(deep=True);s.home_valid=s.site_valid(s.home[0],s.home[2])
        if request.adaptive_policy is not None and request.adaptive_policy!=self.cfg.get('adaptive_policy','balanced_v1'):
            self.cfg['adaptive_policy']=request.adaptive_policy; self.observability.invalidate()
            self.scheduler.protected_scheduling=self.resilience.enabled and self.cfg.get('resilience',{}).get('protected_scheduling',True)
            self.adaptive.last_evaluation=None; self.adaptive.winner=None; self.adaptive.wins=0
            self.event('OBSERVABILITY','observation_policy_changed',{'policy':request.adaptive_policy,'epoch':self.observability.epoch})
        mode=request.topology_mode or ('manual' if request.topology is not None else self.topology_mode)
        enabling_auto=mode=='auto' and self.topology_mode!='auto'
        self.topology_mode=mode
        if mode=='manual' and request.topology is not None: self.manual_topology=request.topology
        elif mode=='manual' and previous_mode!='manual': self.manual_topology=self.topology
        if enabling_auto:
            self.adaptive.last_evaluation=None; self.adaptive.winner=None; self.adaptive.wins=0
        if request.formation is not None:
            self.formation=request.formation
            for d in self.nodes.values(): d.formation_slot=None
        if request.seed is not None: self.pending_seed=request.seed if request.seed!=self.seed else None # Applied on reset; the active seed remains truthful.
        if request.speed is not None: self.speed=request.speed
        if request.range_m is not None:
            self.cfg['radio']['range_m']=request.range_m
            for d in self.nodes.values(): d.range_m=request.range_m
        self._configuring=True
        try:
            if request.count is not None: self.resize(request.count)
            else: self.revision+=1; self.slots=formation_positions(self.formation,self.count); self.rebuild()
        finally: self._configuring=False
        if mode=='manual' and (request.topology is not None or previous_mode!='manual' or pending_manual):
            self.adaptive.request(self.manual_topology,'Manual override')
        self.rebuild()
        self.event('COMMAND','mission_configured',{'topology':self.topology,'formation':self.formation,'count':self.count,'speed':self.speed})
        return self.snapshot()
    def control(self,request):
        self.check_revision(request.expected_revision)
        if request.action=='reset': self.reset()
        elif request.action in ('play','pause'): self.playing=request.action=='play'; self.accumulator=0.; self.revision+=1
        else:
            self.controller_connected=request.action=='reconnect_controller'; self.revision+=1
            self.event('COMMAND','command_link_restored' if self.controller_connected else 'command_link_lost',{'autonomous':not self.controller_connected})
        if self.playing: self.dispatch_commands()
        return self.snapshot()
    def load_scenario(self,name,revision):
        self.check_revision(revision)
        if name not in SCENARIOS: raise ValueError('unknown scenario')
        preset=SCENARIOS[name]
        if self.autonomy.enabled:
            bound=self.flight_safety.profile.geofence_m
            if any(abs(p[j])+5>bound for p in formation_positions(preset['formation'],preset['count']) for j in (0,2)):
                raise ValueError('Scenario formation exceeds the configured safety geofence')
        self.count=preset['count']; self.formation=preset['formation']
        if self.topology_mode!='manual': self.topology=preset['topology']
        self.reset(); self.scenario=name
        if preset.get('preferred_family'): self.route_preferences={identity:preset['preferred_family'] for identity in self.nodes}
        self.event('COMMAND','scenario_loaded',{'scenario':name,'attack_at_s':8,'preferred_family':preset.get('preferred_family'),'preference_is_safety_constrained':True})
        return self.snapshot()
    def tick(self,wall_delta):
        before=self.seconds
        if not self.playing:
            self.performance.previous=None
            return
        self._tick(wall_delta)
        self.performance.tick(before,self.seconds,wall_delta)
    def _tick(self,wall_delta):
        self.accumulator+=max(0,min(.25,wall_delta))*self.speed
        step=self.cfg['fixed_step_s']
        while self.accumulator+1e-9>=step:
            self.accumulator-=step
            self.scheduler.step(self.seconds+step,self.packet_link,self.packet_allowed,self.attacks.relay_drop,self.repair_packet)
            self.seconds+=step
            supervised=self.autonomy.enabled or any(s.latched for s in self.flight_safety.supervisors.values())
            if supervised:
                with self.performance.measure('flight_safety'):self.flight_safety.step()
            advance(list(self.nodes.values()),step,self.seconds,self.slots,self.terrain,self.flight_safety.supervisors if supervised else None)
            if supervised:
                with self.performance.measure('flight_safety'):self.flight_safety.after_motion()
            self.scenario_step()
            conditions_expired=self.attacks.step(step)
            self.dispatch_commands()
            if conditions_expired or self.seconds-self.last_network>=self.cfg['network_interval_s']-1e-9:
                self.rebuild(); self.last_network=self.seconds
            if self.seconds-self.last_traffic>=1-1e-9:
                self.generate_traffic(); self.last_traffic=self.seconds
            self.attacks.generate(step)
            self.scheduler.step(self.seconds,self.packet_link,self.packet_allowed,self.attacks.relay_drop,self.repair_packet)
            self.expire_commands(); self.refresh_transfers()
            self.observability.step()
            if supervised:
                with self.performance.measure('autonomous_recovery'):self.autonomy.step()
            else:self.autonomy.step()
    def scenario_step(self):
        if step_recovery(self):return
        if step_scenario(self): return
        if self.scenario_fired or self.seconds<8: return
        self.scenario_fired=True; nodes=list(self.nodes.values())
        if self.scenario in ('jamming','rerouting'):
            # Physical interference only. No detector status is injected.
            target=nodes[len(nodes)//2]; self.jammers.append({'position':target.position[:],'radius_m':100.,'power_dbm':-12.})
            self.event('SECURITY','interference_injected',{'position':target.position[:],'evidence':'controlled_simulation'})
            # Presets and manual controls have one lifecycle and clearing path.
            nearby=[d.id for d in nodes if math.dist(d.position,target.position)<100]
            attack=self.inject_attack(MissionAttack(session_id=self.session_id,expected_revision=self.revision,
                attack_id='scenario-interference',group='ew',profile='spot',targets=nearby,paths=['mesh'],duration_s=300),spatial=True)
            # The spatial preset remains the established RF experiment; do not
            # double its noise with a second family-scoped channel impairment.
            self.attacks.active[attack['attack_id']]['spatial']=True
        elif self.scenario=='failures':
            for d in nodes[3:6]: d.available=False; d.health=0
            self.event('NETWORK','node_failures_injected',{'nodes':[d.id for d in nodes[3:6]]})
        elif self.scenario=='controller_loss':
            self.controller_connected=False; self.event('COMMAND','command_link_lost',{'autonomous':True})
        elif self.scenario=='poisoning':
            # The current pool may already have uploaded. Arm the next actual
            # round dispatch, before its clients train, rather than a stale pool.
            self.event('FL','poisoning_pending',{'reason':'awaiting_actual_round_dispatch'})
    def fl_dispatch(self,round_number,participants):
        if not isinstance(participants,list) or not participants or len(set(participants))!=len(participants):
            raise ValueError('Dispatch requires unique participating identities')
        if any(identity not in self.nodes or not self.nodes[identity].available or self.nodes[identity].containment!='normal' for identity in participants):
            raise ValueError('Dispatch includes unavailable mission identities')
        if not self.isolated and self.scenario=='poisoning' and self.scenario_fired and self.poisoning_client is None:
            from simulation.byzantine_state import set_attack_mode
            self.poisoning_client=participants[0]
            set_attack_mode(self.poisoning_client,'poisoned')
            self.event('FL','poisoning_armed',{'client_id':self.poisoning_client,'round':round_number,'verdict':'awaiting_actual_update_analysis'})
            self.inject_attack(MissionAttack(session_id=self.session_id,expected_revision=self.revision,
                attack_id='scenario-poisoning',group='fl',profile='poisoned',targets=[self.poisoning_client],paths=[],duration_s=300))
        if not self.isolated: self.attacks.dispatch_fl(round_number,participants)
        self.runtime['flower']=dict(status='training',round=round_number,participants=participants,timestamp=time.time())
    def inject_attack(self,request,spatial=False):
        if request.session_id!=self.session_id: raise RevisionConflict('stale attack session')
        # Idempotency survives intervening management revisions.
        old=self.attacks.active.get(request.attack_id)
        if old and old['request']==request.model_dump(): return deepcopy(old)
        self.check_revision(request.expected_revision)
        result=self.attacks.inject(request)
        if spatial and request.attack_id in self.attacks.active: self.attacks.active[request.attack_id]['spatial']=True
        self.revision+=1; self.rebuild()
        return result
    def clear_attacks(self,request):
        if request.session_id!=self.session_id: raise RevisionConflict('stale attack session')
        self.check_revision(request.expected_revision)
        self.attacks.clear(request.attack_id,request.group,request.targets)
        if not self.attacks.active.get('scenario-interference'): self.jammers=[]
        self.revision+=1; self.rebuild(); return self.attacks.state()
    def permitted_physical_links(self):
        if 'mesh' in self.failed_families: return {}
        return {edge:impaired for edge,link in self.physical_links.items()
                if edge not in self.blocked|self.auto_blocked
                and (impaired:=self.attacks.impair(link,'mesh',edge)).loss<=self.cfg['radio']['max_loss']}

    def rebuild(self,adapt=True):
        previous=self.resilience.in_rebuild; self.resilience.in_rebuild=True
        try:
            with self.performance.measure('rebuilding'): return self._timed_rebuild(adapt)
        finally: self.resilience.in_rebuild=previous
    def _timed_rebuild(self,adapt=True):
        try: self._rebuild(adapt)
        except (ValueError,RuntimeError) as exc:
            if self.adaptive.transition and (self.adaptive.transition['phase'] in ('validating','verifying') or self.adaptive.transition.get('enhanced')): self.adaptive.fail(str(exc))
            else: raise

    def _rebuild(self,adapt=True):
        adapt=adapt and not self._configuring
        self._clear_routes()
        old=self.links; eligible={}; base=BASE_POSITION
        for i,a in enumerate(self.nodes.values()):
            if not a.available or a.health<=0: continue
            for b in list(self.nodes.values())[i+1:]:
                if not b.available or b.health<=0: continue
                link=radio_link(a.id,b.id,a.position,b.position,dict(self.cfg['radio'],range_m=min(a.range_m,b.range_m)),self.terrain,self.jammers,min(a.health,b.health))
                if link and link.sinr>=self.cfg['radio']['minimum_sinr_db']-(2 if pair(a.id,b.id) in old else 0): eligible[pair(a.id,b.id)]=link
        physical_recovered={e for e in self.auto_blocked if e in eligible and self.attacks.impair(eligible[e],'mesh',e).loss<.15}
        for edge in physical_recovered: self.event('SDN','RESTORE',{'link':list(edge),'reason':'modeled_link_recovery'})
        self.auto_blocked.difference_update(physical_recovered)
        base_links={}
        if not self.cfg.get('mesh_only') or self.cfg.get('observation_base_endpoint'):
            for d in self.nodes.values():
                if not d.available or d.health<=0: continue
                link=radio_link(d.id,'base',d.position,base,dict(self.cfg['radio'],range_m=d.range_m),self.terrain,self.jammers,d.health)
                if link: base_links[pair(d.id,'base')]=link
        self.physical_links=dict(eligible)
        self.physical_links.update(base_links)
        committed=False
        if adapt:
            try: committed=self.adaptive.before_rebuild()
            except (ValueError,RuntimeError) as exc:
                self.adaptive.fail(str(exc)); return
        topology_eligible={e:l for e,l in eligible.items() if e not in self.blocked|self.auto_blocked and self.attacks.impair(l,'mesh',e).loss<=self.cfg['radio']['max_loss']} if self.topology=='self_healing_mesh' else eligible
        previous_edges=self.adaptive.transition['prepared'].edges if committed else set(old)
        edges,heads=topology_edges(self.topology,list(self.nodes.values()),topology_eligible,previous_edges,self.topology_state,self.seconds,self.cfg.get('discovery_timeout_s',.6))
        pending=self.adaptive.transition
        if pending and pending.get('enhanced') and pending['phase']=='overlap':
            prepared=pending['prepared']; edges=prepared.edges & eligible.keys(); heads=prepared.heads
        self.heads=heads; self.radio_links={e:eligible[e] for e in edges}
        self.links={e:self.attacks.impair(eligible[e],'mesh',e) for e in edges}
        for d in self.nodes.values():
            d.neighbors=sorted(b if a==d.id else a for a,b in edges if d.id in (a,b))
            edge=pair(d.id,'base')
            if edge in base_links:
                self.radio_links[edge]=base_links[edge]
                self.links[edge]=self.attacks.impair(base_links[edge],'mesh',edge)
        if pending and pending.get('enhanced') and pending['phase']=='overlap':
            self.resilience.target_links=dict(self.links)
            overlay=self.resilience.overlay(); self.links.update(overlay)
            self.radio_links.update({edge:eligible[edge] for edge in overlay if edge in eligible})
            for d in self.nodes.values(): d.neighbors=sorted(b if a==d.id else a for a,b in self.links if d.id in (a,b) and 'base' not in (a,b))
        for edge in sorted(set(old)-set(self.links)):
            record=dict(old[edge].snapshot(),disconnected_at=self.seconds,status='disconnected')
            self.disconnected_links.append(record)
            self.event('NETWORK','link_disconnected',{'link':list(edge)})
        for edge in sorted(set(self.links)-set(old)):
            self.event('NETWORK','neighbor_discovered',{'link':list(edge)})
        self.network_revision+=1
        invalidated=[]
        for policy_id,flow in list(self.flows.items()):
            if flow['family']!='hold' and not self.valid_route(flow['hops'],flow['family'],flow['traffic_class'] in CONTROL_TRAFFIC):
                key=f"{flow['source']}:{flow['destination']}"
                if key not in self.recovery or self.recovery[key].get('service_recovered_at') is not None:
                    self.recovery[key]=dict(source=flow['source'],destination=flow['destination'],disrupted_at=self.seconds,route_recovered_at=None,service_recovered_at=None)
                invalidated.append(flow)
                self.remove_policy(policy_id); self.failed_routes+=1
        # Locally discovered routes continue even with command station disconnected.
        for d in self.nodes.values():
            if self.cfg.get('mesh_only'): continue
            family=self.families.get(d.id,'mesh')
            self.ensure_route(d.id,'base','telemetry',family)
            self.ensure_route(d.id,'base','heartbeat','mesh')
        # Physical degradation blocks forwarding independently of scenario names.
        newly_unsafe={e for e,l in self.links.items() if l.loss>self.cfg['radio']['max_loss']}
        for edge in newly_unsafe-self.auto_blocked: self.event('SDN','BLOCK',{'link':list(edge),'reason':'modeled_link_loss'})
        recovered={e for e in self.auto_blocked if e in self.links and self.links[e].loss<.15}
        for edge in recovered: self.event('SDN','RESTORE',{'link':list(edge),'reason':'modeled_link_recovery'})
        self.auto_blocked=(self.auto_blocked-recovered)|newly_unsafe
        for flow in invalidated:
            self.ensure_route(flow['source'],flow['destination'],flow['traffic_class'],flow['family'],flow.get('decision_id'))
        if adapt:
            self.adaptive.after_rebuild(committed)
            pending=self.adaptive.transition
            # No clock advance is needed when there are no old mesh packets to drain.
            if pending and pending['phase'] in ('draining','preparing') and not pending['affected'].intersection(self.scheduler.packets):
                self.rebuild()
    def valid_route(self,hops,family,control=False):
        if family=='hold': return False
        if len(hops)<2 or len(set(hops))!=len(hops) or 'base' in hops[1:-1]: return False
        if family in self.failed_families: return False
        for identity in hops:
            d=self.nodes.get(identity)
            if d and (not d.available or d.health<=0 or (d.threat or 0)>.8 or d.containment=='quarantined' or ((d.containment in ('restricted','control_only') or self.families.get(identity)=='hold') and not control)): return False
        if family=='satellite': return hops[-1]=='base' and len(hops)==2 and self.satellite_link(hops[0]).loss<=self.cfg['radio']['max_loss']
        if family=='direct':
            if len(hops)!=2: return False
            edge=pair(*hops)
            return edge not in self.blocked and (link:=self.attacks.impair(self.radio_links.get(edge),family,hops)) is not None and link.loss<=self.cfg['radio']['max_loss']
        for a,b in zip(hops,hops[1:]):
            edge=pair(a,b); link=self.links.get(edge)
            if link is None or edge in self.blocked or edge in self.auto_blocked or not link.loss<=self.cfg['radio']['max_loss']: return False
        return True
    def route(self,source,dest,family='mesh',control=False):
        if source not in self.nodes and source!='base': return []
        if family=='hold' or family in self.failed_families or self.cfg.get('mesh_only') and (family!='mesh' or dest=='base' and not self.cfg.get('observation_base_endpoint')): return []
        if family=='satellite' and dest=='base':
            return [source,dest] if self.valid_route([source,dest],family,control) else []
        if family=='direct': return [source,dest] if self.valid_route([source,dest],family,control) else []
        key=(source,dest,family,control)
        if control not in self._adjacency_cache:
            self._adjacency_cache[control]=build_adjacency(self.resilience.target_links if self.resilience.target_links is not None else self.links,self.nodes,self.blocked|self.auto_blocked,control=control)
        if key not in self._route_cache or self._route_cache[key] and not self.valid_route(self._route_cache[key],family,control):
            self._route_cache[key]=shortest_path(self.links,source,dest,self.nodes,self.blocked|self.auto_blocked,control=control,adjacency=self._adjacency_cache[control])
        return self._route_cache[key][:]
    def policy_route(self,source,dest,category,family='mesh'):
        restrictions=self.flow_constraints.get(f'{source}:{dest}:{category}',{})
        if category not in restrictions.get('allowed_traffic_classes',[category]): return []
        omitted=set(restrictions.get('excluded_nodes',[]))
        blocked={pair(*e) for e in restrictions.get('excluded_links',[])}
        if source in omitted or dest in omitted: return []
        route=self.route(source,dest,family,category in CONTROL_TRAFFIC)
        if not omitted.intersection(route) and not any(pair(a,b) in blocked for a,b in zip(route,route[1:])): return route
        if family!='mesh': return []
        links={edge:link for edge,link in (self.resilience.target_links if self.resilience.target_links is not None else self.links).items() if not omitted.intersection(edge)}
        return shortest_path(links,source,dest,self.nodes,self.blocked|self.auto_blocked|blocked,control=category in CONTROL_TRAFFIC)

    def ensure_route(self,source,dest,category,family='mesh',decision=None):
        key=f'{source}:{dest}:{category}'
        if family=='hold' and category not in CONTROL_TRAFFIC:
            if self.installed_rule(source,dest,category) and self.installed_rule(source,dest,category)['family']=='hold': return []
            self.install_policy(RoutePolicy(policy_id=key,session_id=self.session_id,revision=self.revision,source=source,
                destination=dest,traffic_class=category,family='hold',hops=[],decision_id=decision))
            return []
        hops=self.policy_route(source,dest,category,family)
        old=self.installed_rule(source,dest,category)
        if not hops:
            if old: self.remove_policy(old['policy_id']); self.failed_routes+=1
            return []
        if not old or old['hops']!=hops or old['family']!=family:
            constraints=self.flow_constraints.get(key,{})
            backup=[]
            if family=='mesh':
                backup_key=(tuple(hops),category in CONTROL_TRAFFIC,tuple(sorted(constraints.get('excluded_nodes',[]))),
                            tuple(sorted(pair(*e) for e in constraints.get('excluded_links',[]))))
                if backup_key not in self._backup_cache:
                    backup_links={e:l for e,l in self.links.items() if not set(e).intersection(constraints.get('excluded_nodes',[]))}
                    backup_blocked=self.blocked|self.auto_blocked|{pair(*e) for e in constraints.get('excluded_links',[])}
                    if len(self._backup_cache)>=2048: self._backup_cache.clear()
                    self._backup_cache[backup_key]=shortest_path(backup_links,source,dest,self.nodes,backup_blocked,
                        omit={pair(a,b) for a,b in zip(hops,hops[1:])},control=category in CONTROL_TRAFFIC)
                backup=self._backup_cache[backup_key][:]
            policy=RoutePolicy(policy_id=key,session_id=self.session_id,revision=self.revision,
                source=source,destination=dest,traffic_class=category,family=family,hops=hops,decision_id=decision,
                restrictions=dict(self.flow_constraints.get(key,{}),backup_hops=backup))
            result=self.install_policy(policy)
            if not result.get('applied'):
                self.failed_routes+=1; self.event('SDN','enforcement_failed',{'policy_id':key,'result':result})
                if old and self.valid_route(old['hops'],old['family'],category in CONTROL_TRAFFIC): return old['hops']
                if old: self.remove_policy(old['policy_id'])
                return []
            if old:
                for a,b in zip(old['hops'],old['hops'][1:]): self.event('SDN','REMOVE',{'policy_id':key,'link':[a,b],'superseded':True})
                self.reroutes+=1
        recovery=self.recovery.get(f'{source}:{dest}')
        if recovery and recovery['route_recovered_at'] is None:
            recovery.update(route_recovered_at=self.seconds,route_convergence_ms=(self.seconds-recovery['disrupted_at'])*1000)
            self.event('NETWORK','route_recovered',dict(recovery,hops=hops))
        return hops
    def installed_rule(self,source,destination,category):
        return self.flows.get(self.forwarding.get(f'{source}:{destination}:{category}',''))
    def install_policy(self,policy):
        if policy.session_id!=self.session_id: raise RevisionConflict('stale session')
        self.check_revision(policy.revision)
        if policy.source not in self.nodes and policy.source!='base': raise ValueError('unknown policy source')
        if policy.family=='direct' and len(policy.hops)!=2: raise ValueError('direct policies require a single radio hop')
        if policy.family=='mesh' and self.resilience.target_links is not None and any(pair(a,b) not in self.resilience.target_links for a,b in zip(policy.hops,policy.hops[1:])):
            return {'applied':False,'reason':'retained_edges_are_packet_specific','evidence':'packet_simulation'}
        key=f'{policy.source}:{policy.destination}:{policy.traffic_class}'
        previous=self.installed_rule(policy.source,policy.destination,policy.traffic_class)
        if previous and not self.resilience.in_rebuild and (previous['hops']!=policy.hops or previous['family']!=policy.family or previous.get('restrictions')!=policy.restrictions):
            self.resilience.invalidate_flow(policy.source,policy.destination,policy.traffic_class)
        if policy.family=='hold':
            if policy.source not in self.nodes: raise ValueError('network HOLD requires a UAV source')
            self.families[policy.source]='hold'; self.nodes[policy.source].selected_route='hold'; self._clear_routes()
            old=self.flows.get(policy.policy_id)
            previous=self.installed_rule(policy.source,policy.destination,policy.traffic_class)
            if previous and previous['policy_id']!=policy.policy_id: self.remove_policy(previous['policy_id'])
            for flow_key,flow in list(self.flows.items()):
                if flow['source']==policy.source and flow['traffic_class'] not in CONTROL_TRAFFIC and flow['family']!='hold': self.remove_policy(flow_key)
            self.flows[policy.policy_id]=policy.model_dump()
            self.forwarding[key]=policy.policy_id
            if not old or old['family']!='hold': self.event('SDN','INSTALL',{'policy_id':policy.policy_id,'source':policy.source,'network_action':'hold','decision_id':policy.decision_id})
            return {'schema_version':'enforcement_result_v1','status':'acknowledged','applied':True,'installed_path':'hold','hops':[],'evidence':'packet_simulation','openflow':False}
        restricted=bool(set(policy.hops)&set(policy.restrictions.get('excluded_nodes',[]))) or any(pair(a,b) in {pair(*e) for e in policy.restrictions.get('excluded_links',[])} for a,b in zip(policy.hops,policy.hops[1:]))
        if restricted or not self.valid_route(policy.hops,policy.family,policy.traffic_class in CONTROL_TRAFFIC):
            return {'status':'failed','applied':False,'reason':'unsafe_or_unavailable_hop'}
        previous=self.installed_rule(policy.source,policy.destination,policy.traffic_class)
        if previous and previous['policy_id']!=policy.policy_id: self.remove_policy(previous['policy_id'])
        flow=policy.model_dump(); self.flow_constraints[key]=deepcopy(policy.restrictions); self.flows[policy.policy_id]=flow; self.forwarding[key]=policy.policy_id
        if not self.resilience.in_rebuild:self.assistance.permissions_changed()
        if policy.traffic_class=='telemetry' and policy.source in self.nodes:
            self.families[policy.source]=policy.family; self.nodes[policy.source].selected_route=policy.family
        for a,b in zip(policy.hops,policy.hops[1:]): self.event('SDN','INSTALL',{'policy_id':policy.policy_id,'source':policy.source,'destination':policy.destination,'traffic_class':policy.traffic_class,'hops':policy.hops,'link':[a,b],'family':policy.family,'decision_id':policy.decision_id})
        return {'schema_version':'enforcement_result_v1','session_id':self.session_id,'revision':self.revision,'simulation_time':self.seconds,'status':'acknowledged','applied':True,'installed_path':policy.family,'hops':policy.hops,'policy_id':policy.policy_id,'evidence':'packet_simulation','openflow':False}
    def remove_policy(self,policy_id,forget=False):
        flow=self.flows.pop(policy_id,None)
        if flow:
            key=f"{flow['source']}:{flow['destination']}:{flow['traffic_class']}"
            if forget: self.flow_constraints.pop(key,None)
            if self.forwarding.get(key)==policy_id: self.forwarding.pop(key,None)
            if flow['family']=='hold': self.event('SDN','REMOVE',{'policy_id':policy_id,'source':flow['source'],'network_action':'hold'})
            for a,b in zip(flow['hops'],flow['hops'][1:]): self.event('SDN','REMOVE',{'policy_id':policy_id,'link':[a,b]})
        return {'applied':flow is not None}
    def block_link(self,a,b):
        self._clear_routes()
        self.blocked.add(pair(a,b)); self.event('SDN','BLOCK',{'link':[a,b]})
        self.assistance.permissions_changed()
        for key,flow in list(self.flows.items()):
            if pair(a,b) in {pair(x,y) for x,y in zip(flow['hops'],flow['hops'][1:])}: self.remove_policy(key)
        return {'applied':True}
    def restore_link(self,a,b):
        self._clear_routes()
        self.blocked.discard(pair(a,b)); self.event('SDN','RESTORE',{'link':[a,b]}); return {'applied':True}
    def contain(self,identity,mode,reason):
        if identity not in self.nodes: raise ValueError('unknown node')
        self._clear_routes()
        self.nodes[identity].containment=mode
        self.assistance.permissions_changed()
        for key,flow in list(self.flows.items()):
            if identity in flow['hops'] and not self.valid_route(flow['hops'],flow['family'],flow['traffic_class'] in CONTROL_TRAFFIC): self.remove_policy(key)
        self.event('SDN','ISOLATE' if mode=='quarantined' else 'CONTAINMENT',{'node':identity,'mode':mode,'reason':reason})
        return {'status':'ok','drone_id':identity,'mode':mode,'applied':True,'enforcement':'logical_packet_policy'}
    def select_family(self,identity,family,context=None):
        if identity not in self.nodes: raise ValueError('unknown node')
        d=self.nodes[identity]
        if self.families.get(identity)!=family or context and (d.trust!=context.get('trust') or ((d.threat or 0)>.8)!=((context.get('threat') or 0)>.8)):
            self._clear_routes()
        preference=self.route_preferences.get(identity)
        if preference and family!='hold':
            scores=(context or {}).get('scores')
            allowed=preference=='hold' or not scores or scores[('direct','satellite','mesh').index(preference)]<.8
            if allowed and (preference=='hold' or self.route(identity,'base',preference)): family=preference
            if context: context=dict(context,operator_preference=preference,operator_preference_applied=allowed and family==preference)
        self.families[identity]=family; self.nodes[identity].selected_route=family
        self.assistance.permissions_changed()
        if family!='hold':
            for key,flow in list(self.flows.items()):
                if flow['source']==identity and flow['family']=='hold': self.remove_policy(key)
        if context:
            self.runtime['orchestrator'].update(status='running',timestamp=time.time(),model_source=context.get('routing_source'),routing_contract=context.get('routing_contract'))
            self.security[identity]=context; d=self.nodes[identity]
            d.trust=context.get('trust'); d.threat=context.get('threat')
            self.event('SECURITY','detector_observation',{'node':identity,**context})
            if context.get('attack_type') in ('barrage','sweep','spot') or (context.get('threat') or 0)>.4 or ((context.get('network_detection') or {}).get('risk_score') or 0)>.35:
                self.send(identity,'base','threat',256,context.get('decision_id'))
            self.event('SDN','route_proposed',{'node':identity,'policy_family':context.get('policy_path'),'requested_family':family,
                'candidate_hops':self.route(identity,'base',family),'decision_id':context.get('decision_id'),'confirmed_hops':(self.installed_rule(identity,'base','telemetry') or {}).get('hops',[])})
        hops=self.ensure_route(identity,'base','telemetry',family,context.get('decision_id') if context else None)
        confirmed=self.installed_rule(identity,'base','telemetry')
        installed=confirmed['family'] if confirmed and hops else 'hold'
        if installed=='hold':
            for key,flow in list(self.flows.items()):
                if flow['source']==identity and flow['traffic_class'] not in CONTROL_TRAFFIC and flow['family']!='hold': self.remove_policy(key)
        return {'status':'ok','installed_path':installed,'installed_action_id':('direct','satellite','mesh','hold').index(installed),
                'flow_priority':200 if installed=='hold' else 100,'timestamp':time.time(),'hops':hops,
                'session_id':self.session_id,'note':'LOGICAL SIMULATION ENFORCEMENT','network_revision':self.network_revision,'reason':'explicit_network_hold' if family=='hold' else None if hops else 'no_safe_hops'}
    def packet_link(self,p,a,b):
        if self.autonomy.owns(p):return self.autonomy.packet_link(p,a,b)
        if p.family=='satellite':
            return self.satellite_link(p.source)
        if p.family=='direct': return self.attacks.impair(self.radio_links.get(pair(a,b)),'direct',(a,b))
        return self.links.get(pair(a,b))
    def satellite_link(self,identity):
        return self.attacks.impair(Link(identity,'base',700000.,-65.,18.,2.,.02,85.,'modeled_satellite'),'satellite',(identity,))
    def packet_allowed(self,p,a,b):
        if self.autonomy.owns(p):return self.autonomy.allowed(p,a,b)
        if (self.autonomy.enabled or self.autonomy.interfaces) and not self.autonomy.permitted(p.route[p.hop:],p.family,p.restrictions):return False
        if p.category in ('command','ack') and not self.controller_connected: return False
        if self.resilience.authorizes(p): return True
        if not self.valid_route(p.route[p.hop:],p.family,p.category in CONTROL_TRAFFIC): return False
        flow=self.installed_rule(p.policy_source or p.source,p.destination,p.category)
        expected=p.route
        if p.policy_source!=p.source:
            offset=p.route.index(p.policy_source)
            expected=p.route[offset:]
        return bool(flow and flow['hops']==expected)

    def repair_packet(self,p):
        if p.family!='mesh' or p.hop>=len(p.route)-1: return False
        current=p.route[p.hop]
        omitted=set(p.route[:p.hop])|set(p.restrictions.get('excluded_nodes',[]))
        forbidden={pair(*edge) for edge in p.restrictions.get('excluded_links',[])}
        links={edge:link for edge,link in (self.resilience.target_links if self.resilience.target_links is not None else self.links).items() if not omitted.intersection(edge)}
        suffix=shortest_path(links,current,p.destination,self.nodes,self.blocked|self.auto_blocked|forbidden,control=p.category in CONTROL_TRAFFIC)
        if not suffix: return False
        policy=RoutePolicy(policy_id=f'repair:{p.id}:{p.repair_count}',session_id=self.session_id,revision=self.revision,
            source=current,destination=p.destination,traffic_class=p.category,family='mesh',hops=suffix,restrictions=p.restrictions)
        if not self.install_policy(policy).get('applied'): return False
        p.route=p.route[:p.hop]+suffix; p.policy_source=current; p.revision=self.network_revision
        return True

    def inspect_route(self,source,destination,category='sensor'):
        if source==destination or source not in self.nodes or destination not in self.nodes and destination!='base':
            raise ValueError('choose distinct current source and destination identities')
        if category not in CONTROL_TRAFFIC and category not in ('sensor','telemetry','position','fl_update'):
            raise ValueError('unknown traffic class')
        candidate=self.policy_route(source,destination,category)
        installed=self.installed_rule(source,destination,category)
        return dict(session_id=self.session_id,network_revision=self.network_revision,source=source,destination=destination,
            candidate_hops=candidate,installed_hops=installed['hops'] if installed else [],
            reachable=bool(candidate),routing_source='trust_aware_shortest_path',evidence='packet_simulation')

    def probe(self,request):
        if request.session_id!=self.session_id: raise RevisionConflict('stale probe session')
        self.check_revision(request.expected_revision)
        self.inspect_route(request.source,request.destination)
        packet=self.send(request.source,request.destination,'sensor',request.size_bytes,correlation='probe',family_override='mesh')
        self.revision+=1
        return dict(packet=packet.snapshot(),session_id=self.session_id,revision=self.revision)

    def packet_trace(self,after=0,limit=2000):
        limit=max(1,min(5000,limit))
        rows=[e for e in self.packet_events if e['cursor']>after][:limit]
        oldest=self.packet_events[0]['cursor'] if self.packet_events else self.packet_cursor+1
        return dict(session_id=self.session_id,events=rows,cursor=rows[-1]['cursor'] if rows else max(after,self.packet_cursor),
            latest_cursor=self.packet_cursor,event_gap=after>0 and after<oldest-1,oldest_cursor=oldest)
    def send(self,source,dest,category,size=512,correlation=None,family_override=None,service=None,channel_key=None,expires_at=None,_mission_offer=False):
        family=family_override or (self.families.get(source,'mesh') if dest=='base' and category not in CONTROL_TRAFFIC else 'mesh')
        deferred=family=='mesh' and self.adaptive.transition is not None and self.adaptive.transition['phase']=='draining'
        transition=self.adaptive.transition
        if transition and transition.get('enhanced') and transition['phase']=='preparing':
            deferred=family=='mesh' and (source,dest,category) in transition['affected_flows']
        if service:
            report=self.observability.pending.get(correlation)
            if not (report and report['node']==source and dest=='base' and category=='threat' and channel_key in report['keys']):
                service=None
        command=self.commands.get(correlation)
        if self.resilience.enabled and command and ((category=='command' and source=='base' and dest in command['targets']) or (category in ('ack','emergency') and source in command['targets'] and dest=='base')):
            service={'kind':category,'deadline':command['expires_at']}
            channel_key=f'command:{correlation}:{category}:{source}:{dest}'
        if channel_key is None and (self.resilience.enabled or self.cfg.get('logical_channel_keys')):
            key=(source,dest,category,correlation); self.offer_sequences[key]+=1
            channel_key=f'{source}:{dest}:{category}:{correlation}:{self.offer_sequences[key]}'
        hops=[] if deferred else self.ensure_route(source,dest,category,family)
        recovery=None
        if not hops and not deferred and self.autonomy.enabled and self.families.get(source)!='hold':
            recovery=self.autonomy.recovery_rule(type('RecoveryOffer',(),dict(source=source,destination=dest,category=category,restrictions=self.flow_constraints.get(f'{source}:{dest}:{category}',{})))())
            if recovery:hops=recovery['route'][:];family=recovery['family']
        sequence=self.attacks.sequence(source,self.scheduler.serial+1)
        mac=self.records.get(source,{}).get('mac')
        if self.attacks.has(source,'spoofing'): mac='02:ff:00:00:00:01'
        packet_time=self.scheduler.event_time if self.scheduler.event_time is not None else self.seconds
        if family=='mesh' and (not self.cfg.get('mesh_only') or self.topology_mode=='auto'):
            self.adaptive.offer(source,dest,category,size,packet_time)
        if family=='mesh' and (_mission_offer or command and ((category=='command' and source=='base' and dest in command['targets']) or (category in ('ack','emergency') and source in command['targets'] and dest=='base')) or service and service.get('kind')=='security_report'):
            self.assistance.register(f'p{self.scheduler.serial+1}',source,dest,category,size,packet_time)
        p=self.scheduler.emit(source,dest,category,size,hops,packet_time,self.network_revision,correlation,family,sequence,mac,deepcopy(self.flow_constraints.get(f'{source}:{dest}:{category}',{})),defer=deferred,service=service,channel_key=channel_key,expires_at=expires_at)
        if recovery and p.id in self.scheduler.packets:self.autonomy.bind_forwarding(p,recovery)
        d=self.nodes.get(source)
        if d: d.sent+=1
        return p
    def generate_traffic(self):
        ids=list(self.nodes)
        for i,d in enumerate(self.nodes.values()):
            if not d.available: continue
            self.send(d.id,'base','telemetry',_mission_offer=True); self.send(d.id,'base','heartbeat',64,_mission_offer=True)
            self.send(d.id,ids[(i+1)%len(ids)],'position',128,_mission_offer=True)
            if int(self.seconds)%3==0: self.send(d.id,ids[(i+3)%len(ids)],'sensor',768,_mission_offer=True)
            if int(self.seconds)%5==0 and d.neighbors: self.send(d.id,d.neighbors[0],'discovery',64)
            if int(self.seconds)%7==0 and d.neighbors: self.send(d.id,d.neighbors[0],'routing',96)
    def packet_event(self,kind,data):
        self.packet_cursor+=1
        record=dict(schema_version='mission_packet_event_v1',session_id=self.session_id,cursor=self.packet_cursor,
            simulation_time=data.get('event_at',self.seconds),kind=kind,data=data,evidence_source='packet_simulation')
        self.packet_events.append(record)
        if self.trace_sink: self.trace_sink(record)
        self.assistance.result(kind,data)
        self.autonomy.packet_event(kind,data)
        if kind=='packet_result':
            self.observability.result(data); self.resilience.result(data)
            key=f"{data['source']}:{data['destination']}"
            recovery=self.recovery.get(key)
            if recovery and recovery.get('service_recovered_at') is None and data['status']=='delivered':
                recovery['service_recovered_at']=data['completed_at']
                recovery['service_recovery_ms']=(data['completed_at']-recovery['disrupted_at'])*1000
        # Per-packet traffic is sampled in the operator event rail, never in accounting.
        if kind!='packet_result' or data['category'] in ('command','ack','fl_update','emergency','threat'):
            if data['category'] in ('command','ack','fl_update','emergency','threat') or int(data['id'][1:])%20==0:
                self.event('FL' if data['category']=='fl_update' else 'NETWORK',kind,data)
        if kind=='packet_hop_completed' and data['hop']>0 and data.get('from_node') in self.nodes:
            self.nodes[data['from_node']].forwarded+=1
        if kind=='packet_result':
            if data['status']=='dropped' and data['source'] in self.nodes: self.nodes[data['source']].dropped+=1
            self.packet_results.append(data)
            if data['category']=='fl_update':
                transfer=self.transfers.get(data.get('correlation_id'))
                if transfer and transfer['status']=='pending':
                    transfer['pending']-=1; transfer['failed']+=int(data['status']!='delivered')
            destination=self.nodes.get(data['destination'])
            if destination and data['status']=='delivered': destination.received+=1
            command=self.commands.get(data.get('correlation_id'))
            if command and data['category']=='command':
                outcome=command['outcomes'][data['destination']]
                if data['status']=='delivered':
                    if outcome['status'] in ('expired','retired') or data.get('completed_at',self.seconds)>=command['expires_at']:
                        outcome.update(status='expired',reason='Command expired before execution'); return
                    self.execute_command(data['destination'],command)
                    if outcome.get('result',{}).get('safety_rejected'):
                        outcome.update(status='rejected_safety_ack_pending',rejected_at=data['completed_at'],delivered_at=data['completed_at'],reason='Not executed: '+outcome['result']['reason'])
                    else:
                        outcome['status']='executed_ack_pending'
                        outcome.update(executed_at=data['completed_at'],delivered_at=data['completed_at'],reason='Executed; awaiting acknowledgment')
                    ack=self.send(data['destination'],'base','ack',64,data['correlation_id']); outcome['ack_packet']=ack.id
                else: outcome['status']='delivery_failed'; outcome['reason']=data['reason']
            elif command and data['category']=='ack':
                command['outcomes'][data['source']]['status']='acknowledged' if data['status']=='delivered' else 'executed_ack_failed'
                if command['outcomes'][data['source']].get('result',{}).get('safety_rejected'):
                    command['outcomes'][data['source']]['status']='rejected_safety' if data['status']=='delivered' else 'rejected_safety_ack_failed'
                command['outcomes'][data['source']]['acknowledged_at']=data['completed_at'] if data['status']=='delivered' else None
                command['outcomes'][data['source']]['reason']='Radio delivery, execution and acknowledgment confirmed' if data['status']=='delivered' else data['reason']
                if command['outcomes'][data['source']].get('result',{}).get('safety_rejected'):
                    command['outcomes'][data['source']]['reason']='Not executed: '+command['outcomes'][data['source']]['result']['reason']
                self.event('COMMAND','command_ack',{'command_id':command['command_id'],'node':data['source'],'status':command['outcomes'][data['source']]['status']})
    def command(self,request: MissionCommand):
        if request.session_id is not None and request.session_id!=self.session_id: raise RevisionConflict('stale command session')
        if request.command_id in self.commands:
            old=self.commands[request.command_id]
            if any(old[k]!=v for k,v in request.model_dump().items()): raise ValueError('command ID already bound to different parameters')
            return deepcopy(old)
        if request.expected_revision is not None: self.check_revision(request.expected_revision)
        if len(request.targets)!=len(set(request.targets)) or any(n not in self.nodes for n in request.targets): raise ValueError('targets must be unique current UAV IDs')
        params=request.parameters
        if request.action=='role' and params.get('role') not in ROLES: raise ValueError('unknown role')
        if request.action=='formation' and params.get('formation') not in FORMATIONS: raise ValueError('unknown formation')
        if request.action=='formation' and set(request.targets)!=set(self.nodes): raise ValueError('formation commands target the entire mission fleet')
        if request.action=='route' and params.get('family') not in ('direct','satellite','mesh','hold'): raise ValueError('unknown route family')
        if request.action=='waypoints':
            points=params.get('points')
            if not isinstance(points,list) or not 1<=len(points)<=64 or any(not isinstance(p,list) or len(p)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in p) or not 35<=p[1]<=300 for p in points): raise ValueError('1–64 finite [x,y,z] waypoints with altitude 35–300 required')
            if 'loop' in params and not isinstance(params['loop'],bool): raise ValueError('waypoint loop must be boolean')
            if any(abs(p[0])>10000 or abs(p[2])>10000 for p in points): raise ValueError('horizontal coordinates must be within ±10000 metres')
        if request.action in ('altitude','move'):
            required=('altitude',) if request.action=='altitude' else ('x','y','z')
            if any(not isinstance(params.get(k),(int,float)) or not math.isfinite(params[k]) for k in required): raise ValueError('finite coordinates required')
            height=params.get('altitude',params.get('y'))
            if not 35<=height<=300: raise ValueError('altitude must be 35–300 local metres')
            if request.action=='move' and (abs(params['x'])>10000 or abs(params['z'])>10000): raise ValueError('horizontal coordinates must be within ±10000 metres')
        record=dict(request.model_dump(),simulation_time=self.seconds,evidence_source='packet_simulation',expires_at=self.seconds+request.ttl_s,
                    outcomes={n:{'status':'queued','accepted_at':self.seconds,'reason':'Waiting for mission to play' if not self.playing else 'Awaiting radio dispatch'} for n in request.targets})
        self.commands[request.command_id]=record
        for identity in request.targets:
            if request.action in ('isolate','restore','disconnect','reconnect'):
                self.execute_command(identity,record); record['outcomes'][identity]['status']='management_applied'
                record['outcomes'][identity].update(reason='Simulator management action applied',executed_at=self.seconds)
            elif not self.controller_connected:
                record['outcomes'][identity].update(status='pending',reason='Command link lost; expires without automatic replay')
            # Lost control link leaves a bounded pending command; no false acknowledgment.
        self.event('COMMAND','command_submitted',{'command_id':request.command_id,'action':request.action,'targets':request.targets})
        if self.playing: self.dispatch_commands()
        return deepcopy(record)
    def dispatch_commands(self):
        for record in self.commands.values():
            if self.seconds>=record['expires_at']: continue
            for identity,outcome in record['outcomes'].items():
                if outcome['status']!='queued': continue
                if not self.controller_connected:
                    outcome.update(status='pending',reason='Command link lost; expires without automatic replay'); continue
                # Mark before send: no-route/capacity results are synchronous.
                outcome.update(status='pending',reason='Awaiting radio delivery',dispatched_at=self.seconds)
                packet=self.send('base',identity,'command',128,record['command_id']); packet.expires_at=record['expires_at']; self.scheduler.schedule(packet.expires_at,packet,'expire')
                outcome['packet_id']=packet.id
    def execute_command(self,identity,record):
        d=self.nodes[identity]; action=record['action']; params=record['parameters']
        supervisor=self.flight_safety.supervisors.get(identity)
        if supervisor:
            frame=self.flight_safety.frame(identity);allowed=True;reason=None
            targets=[]
            if action=='move':targets=[[params[k] for k in ('x','y','z')]]
            elif action=='altitude':targets=[[d.position[0],params['altitude'],d.position[2]]]
            elif action=='waypoints':targets=params['points']
            elif action=='formation':targets=[formation_positions(params['formation'],self.count)[list(self.nodes).index(identity)]]
            if self.autonomy.enabled and targets:
                if not supervisor.navigation(frame) or not supervisor.obstacles_known(frame):allowed=False;reason='Current navigation and obstacle evidence required'
                elif any(not supervisor.segment_safe(a,b) for a,b in zip([d.position]+targets,targets)):
                    allowed=False;reason='Flight command leaves the geofence or intersects a declared obstacle'
            if allowed:allowed,reason=supervisor.command_allowed(action,frame)
            if not allowed:
                record['outcomes'][identity].update(result={'safety_rejected':True,'reason':reason})
                self.event('SAFETY','flight_command_rejected',dict(node=identity,action=action,reason=reason));return
        d.current_command=action
        if action=='hold': d.mode='hold'; d.target=d.position[:]
        elif action=='resume': d.mode='patrol'
        elif action in ('return','emergency'):
            self.send(identity,'base','emergency',128,record['command_id'])
            d.mode='return'; d.target=[BASE_POSITION[0]+((d.index-1)%10)*30,70+12*((d.index-1)%3),BASE_POSITION[2]+((d.index-1)//10)*30]
        elif action=='move': d.mode='move'; d.target=[params[k] for k in ('x','y','z')]
        elif action=='altitude': d.mode='move'; d.target=[d.position[0],params['altitude'],d.position[2]]
        elif action=='waypoints': d.waypoints=deepcopy(params['points']); d.waypoint_index=0; d.waypoint_loop=params.get('loop',False); d.mode='waypoints'
        elif action=='formation':
            slots=formation_positions(params['formation'],self.count)
            d.formation_slot=slots[list(self.nodes).index(identity)][:]; d.mode='patrol'
            record['outcomes'][identity]['result']={'formation':params['formation'],'slot':d.formation_slot}
        elif action=='role': d.role=params['role']; self.rebuild()
        elif action=='route':
            self.route_preferences[identity]=params['family']; self.select_family(identity,params['family'])
        elif action=='reroute':
            previous=(self.installed_rule(identity,'base','telemetry') or {}).get('hops',[])[:]
            self._clear_routes(); hops=self.ensure_route(identity,'base','telemetry',self.families.get(identity,'mesh'))
            result={'previous_hops':previous,'installed_hops':hops,'changed':previous!=hops,'reason':'Route recomputed' if previous!=hops else 'Current route remains best'}
            record['outcomes'][identity]['result']=result
            self.event('SDN','route_recomputed',dict(node=identity,command_id=record['command_id'],**result))
        elif action in ('isolate','restore'): self.contain(identity,'quarantined' if action=='isolate' else 'normal','operator_management')
        elif action in ('disconnect','reconnect'): d.available=action=='reconnect'; self.rebuild()
        self.event('COMMAND','command_executed',{'node':identity,'action':action,'command_id':record['command_id']})
    def expire_commands(self):
        for record in self.commands.values():
            if self.seconds>=record['expires_at']:
                for outcome in record['outcomes'].values():
                    if outcome['status'] in ('pending','queued'): outcome.update(status='expired',reason='Flight command TTL elapsed before delivery')
        # Bound completed command history.
        if len(self.commands)>500:
            for identity in list(self.commands)[:-500]: self.commands.pop(identity,None)
    def submit_transfer(self,request):
        if request.session_id!=self.session_id: raise RevisionConflict('stale FL session')
        if request.client_id not in self.nodes: raise ValueError('unknown FL identity')
        key=hashlib.sha256(f'{request.session_id}:{request.round}:{request.client_id}:{request.digest}'.encode()).hexdigest()
        if key in self.transfers: return deepcopy(self.transfers[key])
        # Chunk the actual dense serialized update bytes; all chunks must arrive.
        chunks=math.ceil(request.size_bytes/16384)
        record=dict(request.model_dump(),transfer_id=key,status='pending',pending=chunks,failed=0,created_at=self.seconds)
        self.transfers[key]=record
        d=self.nodes[request.client_id]; d.fl_status='UPLOADING'
        for i in range(chunks): self.send(request.client_id,'base','fl_update',min(16384,request.size_bytes-i*16384),key)
        self.event('FL','upload_started',{'transfer_id':key,'client_id':request.client_id,'round':request.round,'size_bytes':request.size_bytes,'digest':request.digest})
        return deepcopy(record)
    def refresh_transfers(self):
        for t in self.transfers.values():
            if t['status']!='pending': continue
            if t['pending']==0 or self.seconds-t['created_at']>self.cfg['fl']['transfer_timeout_s']:
                t['status']='delivered' if t['pending']==0 and t['failed']==0 else 'delivery_failed'
                t['completed_at']=self.seconds
                if t['client_id'] in self.nodes: self.nodes[t['client_id']].fl_status=t['status'].upper()
                self.event('FL','upload_completed',{'transfer_id':t['transfer_id'],'client_id':t['client_id'],'status':t['status']})
        # Retain receipts for aggregation, then bound completed history.
        completed=[key for key,t in self.transfers.items() if t['status']!='pending' and self.seconds-t.get('completed_at',self.seconds)>600]
        for key in completed[:-256]: self.transfers.pop(key,None)
    def metrics(self):
        return self.observability.metrics() if self.observability.enabled else self.raw_metrics()

    def raw_metrics(self):
        now=time.time(); result={}
        # Candidate RF measurements survive HOLD/containment. Packet enforcement
        # still uses the separate security-aware graph and installed rules.
        measured_graph=build_adjacency(self.links,self.nodes,enforce_security=False)
        window_rows=[r for r in self.scheduler.observations if r[0]>=self.seconds-2]
        outcomes={}; latencies={}
        for at,family,edge,success,latency in self.scheduler.channel_observations:
            if at<self.seconds-2: continue
            key=(family,edge)
            totals=outcomes.setdefault(key,[0,0]);totals[0]+=int(success);totals[1]+=1
            if latency is not None:
                totals=latencies.setdefault(key,[0.,0]);totals[0]+=latency;totals[1]+=1
        for d in self.nodes.values():
            if not d.available: continue
            paths=[]
            for family in ('direct','satellite','mesh'):
                if family=='mesh': hops=shortest_path(self.links,d.id,'base',self.nodes,adjacency=measured_graph)
                else: hops=[d.id,'base'] if family not in self.failed_families and (family=='satellite' or pair(d.id,'base') in self.links) else []
                if (self.autonomy.enabled or self.autonomy.interfaces) and not self.autonomy.permitted(hops,family):hops=[]
                links=[self.links[pair(a,b)] for a,b in zip(hops,hops[1:]) if pair(a,b) in self.links]
                if family=='direct': links=[self.attacks.impair(self.radio_links[pair(d.id,'base')],'direct',(d.id,))] if hops and pair(d.id,'base') in self.radio_links else []
                if family=='satellite' and hops:
                    sat=self.satellite_link(d.id); channel=('satellite',*pair(d.id,'base'))
                    key=(family,pair(d.id,'base'))
                    pdr=outcomes[key][0]/outcomes[key][1] if outcomes.get(key,[0,0])[1]>=8 else 1-sat.loss
                    values=dict(rssi=sat.rssi,pdr=pdr,sinr=sat.sinr,latency=sat.latency_ms+self.scheduler.queued[channel]*2,packet_loss=1-pdr)
                elif links:
                    pdr=math.prod(outcomes[(family,pair(l.a,l.b))][0]/outcomes[(family,pair(l.a,l.b))][1] if outcomes.get((family,pair(l.a,l.b)),[0,0])[1]>=8 else 1-l.loss for l in links)
                    latency=sum(latencies[(family,pair(l.a,l.b))][0]/latencies[(family,pair(l.a,l.b))][1] if (family,pair(l.a,l.b)) in latencies else l.latency_ms+self.scheduler.queued[pair(l.a,l.b)]*2 for l in links)
                    values=dict(rssi=min(l.rssi for l in links),pdr=pdr,sinr=min(l.sinr for l in links),latency=latency,packet_loss=1-pdr)
                else: values=dict(rssi=-125.,pdr=0.,sinr=-10.,latency=1000.,packet_loss=1.)
                paths.append(dict(path_id=family,**values))
            observations=[r for r in window_rows if r[1]==d.id]
            forwarded=sum(r[2]=='relay_ok' for r in observations)
            relay_dropped=sum(r[2]=='relay_drop' for r in observations)
            policy_dropped=sum(r[2]=='relay_drop' and r[3] in ('policy_or_link_removed','node_retired') for r in observations)
            rx=forwarded+relay_dropped; window=max(.05,min(2.,self.seconds))
            attempts=sum(r[2] in ('control','data') for r in observations)
            # The detector's drop counter and RX counter describe the same
            # completed relay cohort. Radio retries are separate observations.
            radio_dropped=max(0,relay_dropped-policy_dropped)
            record=self.records[d.id]
            sequences=[r[3] for r in observations if r[2]=='received_message']
            duplicates=(len(sequences)-len(set(sequences)))/len(sequences) if sequences else 0.
            macs=[r[3] for r in observations if r[2]=='source_identity']
            evidence=dict(reported_tx_packets=attempts,reported_forwarded_packets=forwarded,controller_rx_packets=rx,
                controller_forwarded_packets=forwarded,control_messages_per_s=sum(r[2]=="control" for r in observations)/window,timestamp=now,controller_timestamp=now,
                controller_policy_dropped_packets=policy_dropped,controller_dropped_packets=radio_dropped,
                packet_rate_per_s=attempts/window,counter_scope='aligned_sample_window_v1',report_window_seconds=window,
                flow_window_seconds=window,report_window_start=now-window,flow_window_start=now-window,window_alignment_s=0.,
                duplicate_sequence_ratio=duplicates,observed_source_mac=macs[-1] if macs else None,expected_source_mac=record['mac'],
                provenance=dict(category='synthetic_simulation',observer_id='packet_scheduler',independent=False,collected_at=now,age_s=0.))
            evidence,paths=self.attacks.report(d.id,evidence,paths)
            # Ground-truth labels are absent: measured/model-derived inputs only.
            conditions=self.attacks.for_node(d.id)
            ew=next((a for a in conditions if a['group']=='ew'),None)
            result[d.id]=dict(drone_id=d.id,timestamp=now,source='synthetic',paths=paths,security_evidence=evidence,
                ew_status={'active_attack':ew['profile'] if ew else 'none','jammed_paths':ew['effective_paths'] if ew else []},
                gps={'latitude':40.+self.attacks.position(d.id,d.position)[2]*.00001,'longitude':-105.+self.attacks.position(d.id,d.position)[0]*.00001,
                     'drift_m':math.sqrt(2)*120 if self.attacks.has(d.id,'gps_spoofing') else 0.})
        return result
    def snapshot(self,after=0,include_flows=True):
        with self.performance.measure('snapshot'):
            result=self._snapshot(after,include_flows)
        result['performance']=self.performance.snapshot(self.playing,self.speed)
        return result
    def _snapshot(self,after=0,include_flows=True):
        active=[d for d in self.nodes.values() if d.available]; edges=[l for l in self.links.values() if 'base' not in (l.a,l.b)]
        n=len(self.nodes); p=self.scheduler
        reachable=sum(bool(self.route(d.id,'base','mesh',True)) for d in active)
        return dict(schema_version='mission_snapshot_v1',session_id=self.session_id,revision=self.revision,network_revision=self.network_revision,
            simulation_time=self.seconds,timestamp=time.time(),playing=self.playing,speed=self.speed,seed=self.seed,pending_seed=self.pending_seed,topology=self.topology,
            topology_selection=self.adaptive.snapshot(),
            observability=self.observability.snapshot(),
            autonomy=self.autonomy.snapshot(),
            formation=self.formation,heads=self.heads,scenario=self.scenario,controller_connected=self.controller_connected,evidence='packet_simulation',
            enforcement='simulation_logical_sdn',routing_mode='mesh_reference' if self.cfg.get('mesh_only') else 'flare',range_m=self.cfg['radio']['range_m'],terrain=self.terrain.provenance,base_position=BASE_POSITION,
            nodes=[dict(d.snapshot(),reported_position=self.attacks.position(d.id,d.position),active_attacks=[{k:a[k] for k in ('attack_id','group','profile','effective_paths','expires_at','status')} for a in self.attacks.for_node(d.id)],
                security=self.security.get(d.id),current_path=(self.installed_rule(d.id,'base','telemetry') or {}).get('hops',[])) for d in self.nodes.values()],
            links=[dict(l.snapshot(),schema_version='mission_link_v1',session_id=self.session_id,revision=self.network_revision,simulation_time=self.seconds,evidence_source='packet_simulation',status='blocked' if edge in self.blocked|self.auto_blocked else l.status,transition_retained=self.resilience.target_links is not None and edge not in self.resilience.target_links) for edge,l in self.links.items()],
            packets=[dict(p.snapshot(),schema_version='mission_packet_v1',session_id=self.session_id,simulation_time=self.seconds,evidence_source='packet_simulation') for p in sorted(self.scheduler.packets.values(),key=lambda p:(p.category in ('fl_update','command','ack','threat','emergency'),p.created_at),reverse=True)[:200]],packet_results=list(self.packet_results)[-40:],
            packet_events=[e for e in self.packet_events if e['simulation_time']>=self.seconds-max(.6,self.speed*.4)][-4000:],packet_cursor=self.packet_cursor,
            interference_regions=deepcopy(self.jammers),
            disconnected_links=[e for e in self.disconnected_links if e['disconnected_at']>=self.seconds-3],recovery=list(self.recovery.values()),
            flows=list(self.flows.values()) if include_flows else [],commands=list(self.commands.values())[-30:],attacks=self.attacks.state(),runtime=self.runtime_status(),
            event_gap=bool(self.events and after>0 and (after<self.events[0]['cursor']-1 or sum(e['cursor']>after for e in self.events)>200)),
            oldest_cursor=self.events[0]['cursor'] if self.events else self.event_cursor,events=[e for e in self.events if e['cursor']>after][-200:],cursor=self.event_cursor,
            statistics=dict(total=n,active=len(active),offline=n-len(active),quarantined=sum(d.containment=='quarantined' for d in self.nodes.values()),
                active_links=sum(l.loss<=self.cfg['radio']['max_loss'] and pair(l.a,l.b) not in self.blocked|self.auto_blocked for l in edges),physical_links=len(edges),pending=len(p.packets),average_degree=2*len(edges)/max(1,n),density=2*len(edges)/max(1,n*(n-1)),availability=reachable/max(1,n),
                generated=p.generated,delivered=p.delivered,dropped=p.dropped,pdr=p.delivered/max(1,p.delivered+p.dropped),
                average_latency_ms=p.latency_sum/max(1,p.delivered),throughput_mbps=p.bytes_delivered*8/max(1,self.seconds)/1e6,
                active_routes=len(self.flows),average_hops=sum(max(0,len(f['hops'])-1) for f in self.flows.values())/max(1,len(self.flows)),
                connected_components=self.connected_components(),jitter_ms=self.jitter_ms(),
                reroutes=self.reroutes,failed_routes=self.failed_routes,jammed_links=sum(l.status=='jammed' for l in self.links.values()),
                threats=sum(s.get('attack_type') in ('barrage','sweep','spot') or ((s.get('network_detection') or {}).get('risk_score') or 0)>.35 for s in self.security.values())))
    def connected_components(self):
        adjacency=build_adjacency({e:l for e,l in self.links.items() if 'base' not in e},self.nodes,self.blocked|self.auto_blocked)
        unseen={d.id for d in self.nodes.values() if d.available and d.health>0 and d.containment!='quarantined'}; count=0
        while unseen:
            count+=1; todo=[unseen.pop()]
            while todo:
                for peer,_ in adjacency.get(todo.pop(),[]):
                    if peer in unseen: unseen.remove(peer); todo.append(peer)
        return count

    def jitter_ms(self):
        by_flow={}; differences=[]
        for p in self.scheduler.completions:
            if p['status']!='delivered': continue
            key=(p['source'],p['destination'],p['category'])
            if key in by_flow: differences.append(abs(p['latency_ms']-by_flow[key]))
            by_flow[key]=p['latency_ms']
        return sum(differences)/len(differences) if differences else 0.

    def runtime_status(self):
        now=time.time(); components=deepcopy(self.runtime)
        for value in components.values():
            age=now-value.get('timestamp',0); value['age_s']=age
            if age>5: value['status']='unavailable' if not value.get('timestamp') else 'stale'
            elif not self.playing and value['status'] in ('running','training'): value['status']='paused'
        return dict(components,simulator={'status':'running' if self.playing else 'paused','session_id':self.session_id},
                    enforcement={'status':'installed','rules':len(self.flows),'evidence':'simulation_logical_sdn'},
                    available_paths={d.id:[f for f in ('direct','satellite','mesh') if self.route(d.id,'base',f)] for d in self.nodes.values()})
