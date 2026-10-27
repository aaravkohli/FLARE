export interface SimulationPerformance {status:'running'|'paused';achieved_speed:number|null;relative_to_requested:number|null;clipped_wall_time_s:number;sample_limit:number;timings:Record<string,{samples:number;mean_ms:number;p95_ms:number;max_ms:number}>}
export interface SafetyProfile {version:'test_range_safety_v1';geofence_m:number;navigation_confidence:number|null;obstacle_sensor_available:boolean|null;fixture_refresh:boolean;input_freshness_s:number;interface_health:Partial<Record<'direct'|'satellite'|'mesh',boolean|null>>;mission_criticality:'routine'|'critical';caution_battery:number;critical_battery:number;emergency_battery:number;landing_areas:{id:string;x:number;z:number;radius_m:number}[];obstacles:{id:string;x:number;z:number;radius_m:number;height_m:number}[]}
export interface AutonomySnapshot {version:'autonomous_recovery_v1';mode:'off'|'recovery_v1';profile:SafetyProfile;counters:Record<string,number>;pending_profile_epoch:number;nodes:{node:string;state:string;health:string;stable:boolean;disconnected_since:number|null;attempts:number;ground_paths:Record<string,{age_s:number;route:string[]}>;diagnosis:{label:string;confidence:number|null;hypotheses:{cause:string;confidence:number;evidence:(string|number)[]}[]}}[];safety:{node:string;battery_state?:'normal'|'caution'|'critical'|'emergency';latched:boolean;action:string|null;reason:string|null;navigation_confidence:number|null;obstacle_sensor_available:boolean|null;inputs_current:boolean|null;outcome:string|null;unresolved:string[]}[];transition:{target:string;deadline:number;required:number;verified:number}|null;overlay_edges:{a:string;b:string;kind:'verification'|'recovery_control';expires_at:number}[]}
export interface SwarmNode {
 id:string; alias:string; position:number[]; velocity:number[]; heading:number; battery:number; health:number;
 role:string; mode:string; available:boolean; containment:string; trust:number|null; threat:number|null;
 selected_route:string; current_path:string[]; neighbors:string[]; fl_status:string; cluster:number; range_m:number; sent:number; received:number; forwarded:number; dropped:number; current_command:string|null;
 security:{source?:string; routing_source?:string; safety_override?:boolean; scores?:number[]; attack_type?:string; confidence?:number; policy_path?:string; requested_path?:string; observation?:number[]; insider_risk?:number; network_detection?:{status?:string; risk_score?:number;detected_classes?:string[];evidence_source?:string}}|null;
 reported_position?:number[];active_attacks?:{attack_id:string;group:string;profile:string;effective_paths:string[];expires_at:number;status:string}[];
}
export interface SwarmLink {id:string;a:string;b:string;distance_m:number;rssi:number;sinr:number;loss:number;latency_ms:number;bandwidth_mbps:number;status:string;transition_retained?:boolean}
export interface SwarmPacket {id:string;correlation_id?:string;source?:string;destination?:string;route:string[];hop:number;status:string;category:string;start_at:number;end_at:number;family:string;attempt?:number;retries?:number;created_at?:number;completed_at?:number;reason?:string|null;history?:{from_node:string;to_node:string;start_at:number;end_at:number;attempt:number}[]}
export interface PacketEvent {schema_version:'mission_packet_event_v1';session_id:string;cursor:number;simulation_time:number;kind:string;data:SwarmPacket & {from_node?:string;to_node?:string}}
export interface PacketBatch {session_id:string;events:PacketEvent[];cursor:number;event_gap?:boolean}
export interface TopologyMetadata {id:string;label:string;description:string;suggested_formation:string}
export interface TopologyCandidate {topology:string;score:number;net_score:number;transition_penalty:number;factors:{reliability:number;latency:number;capacity:number;efficiency:number};data_pairs:number;control_routes:number;edges:number;admissible:boolean;observed_edges:number;estimated_edges:number;service_admissible?:boolean;resilience?:{feasible_reports:string[];missed_packet_count:number;basis:string}}
export interface ObservabilityStatus {epoch:number;clock:'simulation';freshness_s:number;total:number;eligible:number;fresh:number;statuses:Record<string,number>;nodes:{node:string;status:string;eligible:boolean;reachable:boolean;age_s:number|null;report_id:string|null;captured_at:number|null;received_at:number|null;epoch:number}[];counters:Record<string,number>;protected_airtime:Record<string,number>}
export interface ResiliencePlan {status:string;started_at:number;overlap_deadline:number;overlap_ended_at?:number;target:string;previous:string;captured_packets:number;protected_packets:number;predicted:{missed_packet_count:number;feasible_reports:string[]};realized:{delivered:number;dropped:number;deadline_misses:number}}
export interface AssistanceNode {node:string;status:string;degraded:boolean;pdr?:number|null;latency_ms?:number|null;completed?:number;offered?:number;receipt_age_s?:number|null;feasibility?:string|null}
export interface AssistanceAttempt {node:string;target:string;status:string;started_at:number;committed_at?:number|null;reason?:string|null;pdr?:number|null;latency_ms?:number|null}
export interface UavAssistance {version:'affected_uav_assist_v1';enabled:boolean;reason:string;nodes:AssistanceNode[];attempt:AssistanceAttempt|null;history:AssistanceAttempt[];verification?:{required:number;verified:number;deadline:number}|null;candidates:{topology:string;node:string;utility_gain:number;transition_penalty:number;fleet_delivery:number|null;fleet_latency_ms:number|null;net_fleet_score:number;reason:string|null;estimated:boolean}[];limits:Record<string,number>;counters:Record<string,number>}
export interface TopologySelection {assistance?:UavAssistance;policy_id?:'balanced_v1'|'evidence_resilience_v1';resilience?:{active_bindings:number;protected_share:number;last_plan:ResiliencePlan|null;history:ResiliencePlan[];temporary_edges:string[][]}|null;mode:'auto'|'manual';reason:string;evaluated_at:number|null;switched_at:number|null;transition_state:string;requested_target:string|null;transition_started_at?:number|null;drain_deadline?:number|null;cooldown_remaining_s:number;evidence:string;fallback:string|null;candidates:TopologyCandidate[];blacklisted:{topology:string;until:number}[]}
export interface RouteInspection {session_id:string;network_revision:number;source:string;destination:string;candidate_hops:string[];installed_hops:string[];reachable:boolean;routing_source:string}
export interface NetworkDisplay {packets:boolean;alternatives?:boolean;siteDetails?:boolean;groundLinks?:boolean;installedRoutes?:boolean;qualityColors?:boolean;linkEmphasis?:'subtle'|'standard'|'strong';inactive:boolean;ranges:boolean;hops:string[];candidate?:boolean;replay?:{packetId:string;time:number}}
export interface NetworkCamera {x:number;z:number;span:number}
export interface ExperimentResult {fresh_evidence_ratio?:number|null;report_delivery_ratio?:number|null;command_ack_ratio?:number|null;deadline_misses?:number;transition_dropped?:number;evidence_unrecovered?:boolean;evidence_recovery_ms?:number|null;initial_topology?:string;final_topology?:string;switches?:number;transition_failures?:number;deferred_packets?:number;deferred_dropped?:number;resource_cost?:number|null;shared_input_sha256?:string;topology:string;seed:number;generated:number;delivered:number;dropped:number;pdr:number|null;average_latency_ms:number|null;jitter_ms:number|null;throughput_mbps:number;average_hops:number|null;connected_components:number;failed_routes?:number;route_convergence_ms?:number|null;service_recovery_ms?:number|null;unrecovered_flows?:number;conservation:boolean;recovery:{source:string;destination:string;service_recovery_ms?:number;route_convergence_ms?:number}[]}
export interface ExperimentState {id:string;mode:'compare'|'benchmark';status:string;progress:number;results:ExperimentResult[];runs?:SwarmSnapshot[];error?:string;trace_files?:string[];incomplete_traces?:string[];current_topology?:string;current_seed?:number;source_sha256?:string;simulator_version?:string;routing_policy?:string;terrain_provenance?:Record<string,unknown>;configuration:{topologies:string[];seeds:number[];scenario:string;count:number;range_m:number;duration_s:number;warmup_s:number}}

export interface SwarmEvent {cursor:number;simulation_time:number;category:string;kind:string;data:Record<string,unknown>}
export interface SwarmSnapshot {
 performance?:SimulationPerformance;
 autonomy?:AutonomySnapshot;
 schema_version:'mission_snapshot_v1';session_id:string;revision:number;network_revision?:number;simulation_time:number;playing:boolean;speed:number;seed:number;pending_seed?:number|null;
 topology:string;formation:string;heads?:string[];scenario:string;controller_connected:boolean;evidence:string;base_position:number[];
 observability?:ObservabilityStatus|null;
 topology_selection?:TopologySelection;
 nodes:SwarmNode[];links:SwarmLink[];packets:SwarmPacket[];flows:{source:string;destination:string;hops:string[];family:'direct'|'satellite'|'mesh'|'hold';traffic_class?:string}[];
 commands:SwarmCommand[];
 attacks?:{capabilities:{profiles:Record<string,string[]>;families:string[]};active:SwarmAttack[];history:SwarmAttack[]};
 runtime?:Record<string,{status?:string;rules?:number;round?:number;participants?:string[];model_source?:string;fl_model_loaded?:boolean}>;
 packet_events?:PacketEvent[];packet_cursor?:number;packet_received_cursor?:number;packet_event_gap?:boolean;range_m?:number;routing_mode?:'mesh_reference'|'flare';disconnected_links?:(SwarmLink & {disconnected_at:number})[];
 interference_regions?:{position:number[];radius_m:number;power_dbm:number}[];
 recovery?:{source:string;destination:string;disrupted_at:number;route_convergence_ms?:number;service_recovery_ms?:number}[];
 events:SwarmEvent[];cursor:number;timestamp:number;event_gap?:boolean;statistics:Record<string,number>;terrain:Record<string,unknown>;
}
export interface SwarmCommand {command_id:string;action:string;simulation_time?:number;expires_at?:number;outcomes:Record<string,{status:string;reason?:string;result?:{changed?:boolean;reason?:string;installed_hops?:string[]}}>}
export interface SwarmAttack {attack_id:string;group:string;profile:string;targets?:string[];affected_targets:string[];effective_paths:string[];created_at?:number;duration_s?:number;expires_at:number;completed_at?:number;status:string;outcomes:Record<string,{generated_packets?:number;rate_capped?:boolean;status?:string;round?:number}>}
export const TOPOLOGIES=['full_mesh','partial_mesh','star','ring','tree','cluster_mesh','grid','random_geometric','hybrid_mesh','self_healing_mesh'];
export const FORMATIONS=['grid','v','line','circle','diamond','wedge','column','distributed','cluster'];
export const title=(s:string)=>s.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());

let currentMission:SwarmSnapshot|null=null;
const retiredSessions=new Set<string>();
const missionListeners=new Set<()=>void>();
const selectionListeners=new Set<()=>void>();
let selection:{targets:string[];inspected:string}={targets:[],inspected:''};
export function inspectMissionTarget(id:string){
 if(currentMission?.nodes.some(n=>n.id===id))setSelection(selection.targets,id);
}
export function toggleMissionTarget(id:string){
 if(currentMission?.nodes.some(n=>n.id===id))setSelection(selection.targets.includes(id)?selection.targets.filter(n=>n!==id):[...selection.targets,id],selection.inspected);
}
function setSelection(targets:string[],inspected:string){selection={targets,inspected};for(const listener of selectionListeners)listener();}
export function selectMissionTarget(id:string,additive=false){
 if(!currentMission?.nodes.some(n=>n.id===id))return;
 setSelection(additive?selection.targets.includes(id)?selection.targets.filter(n=>n!==id):[...selection.targets,id]:[id],id);
}
export function selectAllMissionTargets(){setSelection(currentMission?.nodes.map(n=>n.id)??[],selection.inspected||currentMission?.nodes[0]?.id||'');}
export function clearMissionTargets(){setSelection([],selection.inspected);}
export const getMissionSelection=()=>selection;
export function subscribeSelection(listener:()=>void){selectionListeners.add(listener);return ()=>{selectionListeners.delete(listener);};}
export function publishMissionSnapshot(next:SwarmSnapshot){
 if(retiredSessions.has(next.session_id))return;
 if(currentMission?.session_id===next.session_id && (next.revision<currentMission.revision || next.revision===currentMission.revision&&next.simulation_time<currentMission.simulation_time))return;
 if(currentMission && currentMission.session_id!==next.session_id){retiredSessions.add(currentMission.session_id);if(retiredSessions.size>16)retiredSessions.delete(retiredSessions.values().next().value!);}
 const members=new Set(next.nodes?.map(n=>n.id)??[]),reset=currentMission?.session_id!==next.session_id;
 const targets=reset?[]:selection.targets.filter(n=>members.has(n));
 const inspected=members.has(selection.inspected)&&!reset?selection.inspected:next.nodes?.[0]?.id??'';
 if(targets.join('|')!==selection.targets.join('|')||inspected!==selection.inspected)setSelection(targets,inspected);
 if(!reset && currentMission?.packet_events?.length){
  const combined=new Map(currentMission.packet_events.map(e=>[e.cursor,e]));
  for(const event of next.packet_events??[])combined.set(event.cursor,event);
  next={...next,packet_events:[...combined.values()].filter(e=>e.simulation_time>=next.simulation_time-30).sort((a,b)=>a.cursor-b.cursor).slice(-20000)};
 }
 if(!reset&&currentMission)next={...next,packet_received_cursor:currentMission.packet_received_cursor,packet_event_gap:next.packet_event_gap||currentMission.packet_event_gap};
 currentMission=next;for(const listener of missionListeners)listener();
}
export function clearMissionSnapshot(){currentMission=null;retiredSessions.clear();setSelection([],'');for(const listener of missionListeners)listener();}
export const getMissionSnapshot=()=>currentMission;
export function subscribeMission(listener:()=>void){missionListeners.add(listener);return ()=>{missionListeners.delete(listener);};}

export function publishPacketEvents(batch:PacketBatch){
 if(!currentMission||currentMission.session_id!==batch.session_id)return;
 const combined=new Map((currentMission.packet_events??[]).map(e=>[e.cursor,e]));
 for(const event of batch.events)if(event.session_id===batch.session_id)combined.set(event.cursor,event);
 currentMission={...currentMission,packet_events:[...combined.values()].filter(e=>e.simulation_time>=currentMission!.simulation_time-30).sort((a,b)=>a.cursor-b.cursor).slice(-20000),packet_cursor:Math.max(currentMission.packet_cursor??0,batch.cursor),packet_received_cursor:Math.max(currentMission.packet_received_cursor??0,batch.cursor),packet_event_gap:batch.event_gap||currentMission.packet_event_gap};
 for(const listener of missionListeners)listener();
}
