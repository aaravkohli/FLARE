export interface SwarmNode {
 id:string; alias:string; position:number[]; velocity:number[]; heading:number; battery:number; health:number;
 role:string; mode:string; available:boolean; containment:string; trust:number|null; threat:number|null;
 selected_route:string; current_path:string[]; neighbors:string[]; fl_status:string; cluster:number; range_m:number; sent:number; received:number; forwarded:number; dropped:number; current_command:string|null;
 security:{source?:string; routing_source?:string; safety_override?:boolean; scores?:number[]; attack_type?:string; confidence?:number; policy_path?:string; requested_path?:string; observation?:number[]; insider_risk?:number; network_detection?:{status?:string; risk_score?:number;detected_classes?:string[];evidence_source?:string}}|null;
 reported_position?:number[];active_attacks?:{attack_id:string;group:string;profile:string;effective_paths:string[];expires_at:number;status:string}[];
}
export interface SwarmLink {id:string;a:string;b:string;distance_m:number;rssi:number;sinr:number;loss:number;latency_ms:number;bandwidth_mbps:number;status:string}
export interface SwarmPacket {id:string;route:string[];hop:number;status:string;category:string;start_at:number;end_at:number;family:string}
export interface SwarmEvent {cursor:number;simulation_time:number;category:string;kind:string;data:Record<string,unknown>}
export interface SwarmSnapshot {
 schema_version:'mission_snapshot_v1';session_id:string;revision:number;simulation_time:number;playing:boolean;speed:number;seed:number;
 topology:string;formation:string;scenario:string;controller_connected:boolean;evidence:string;base_position:number[];
 nodes:SwarmNode[];links:SwarmLink[];packets:SwarmPacket[];flows:{source:string;destination:string;hops:string[];family:'direct'|'satellite'|'mesh'|'hold';traffic_class?:string}[];
 commands:SwarmCommand[];
 attacks?:{capabilities:{profiles:Record<string,string[]>;families:string[]};active:SwarmAttack[];history:SwarmAttack[]};
 runtime?:Record<string,{status?:string;rules?:number;round?:number;participants?:string[];model_source?:string;fl_model_loaded?:boolean}>;
 events:SwarmEvent[];cursor:number;timestamp:number;event_gap?:boolean;statistics:Record<string,number>;terrain:Record<string,unknown>;
}
export interface SwarmCommand {command_id:string;action:string;expires_at?:number;outcomes:Record<string,{status:string;reason?:string;result?:{changed?:boolean;reason?:string;installed_hops?:string[]}}>}
export interface SwarmAttack {attack_id:string;group:string;profile:string;affected_targets:string[];effective_paths:string[];expires_at:number;status:string;outcomes:Record<string,{generated_packets?:number;rate_capped?:boolean;status?:string;round?:number}>}
export const TOPOLOGIES=['full_mesh','partial_mesh','star','ring','tree','cluster_mesh','hybrid_mesh','chain','grid','manet'];
export const FORMATIONS=['grid','v','line','circle','diamond','wedge','column','distributed','cluster'];
export const title=(s:string)=>s.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());

let currentMission:SwarmSnapshot|null=null;
const retiredSessions=new Set<string>();
const missionListeners=new Set<()=>void>();
const selectionListeners=new Set<()=>void>();
let selection:{targets:string[];inspected:string}={targets:[],inspected:''};
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
 currentMission=next;for(const listener of missionListeners)listener();
}
export function clearMissionSnapshot(){currentMission=null;retiredSessions.clear();setSelection([],'');for(const listener of missionListeners)listener();}
export const getMissionSnapshot=()=>currentMission;
export function subscribeMission(listener:()=>void){missionListeners.add(listener);return ()=>{missionListeners.delete(listener);};}
