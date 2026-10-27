import type {ExperimentResult, ExperimentState} from './swarm';
export interface StudyResult extends ExperimentResult {scenario:string;policy_id:string;initial_topology:string;final_topology:string;switches:number;transition_failures:number;deferred_packets:number;deferred_dropped:number;resource_cost:number|null;shared_input_sha256:string;timing?:Record<string,unknown>}
export interface PairedStatistic {scenario:string;baseline:string;metric:string;mean_difference:number|null;interval_95:[number,number]|null;valid_pairs:number;missing_pairs:number;comparison:string;unit:string;conditioning:string}
export interface StudyState {configuration?:{mode:string;protocol_id:string;preset:string};simulation_config?:Record<string,unknown>;id:string;mode:'adaptive_study';status:string;progress:number;results:StudyResult[];statistics?:PairedStatistic[];error?:string;protocol?:{id:string;preset:'pilot'|'evidence';total_runs:number;ttl_drain_s?:number;seeds:number[];policies:string[];scenarios:string[]};current_scenario?:string;current_policy?:string;current_seed?:number;source_sha256?:string;simulator_version?:string;trace_files?:string[]}
export const STUDY_SCENARIOS=['normal','mobility','regional_jamming','congestion','partition_reconnection'];
export function studyResultView(job:StudyState,scenario:string):ExperimentState|null {
 if(!job.protocol)return null;
 return {id:job.id,mode:'benchmark',status:job.status,progress:job.progress,source_sha256:job.source_sha256,simulator_version:job.simulator_version,routing_policy:'mesh_only_trust_aware_shortest_path',
 configuration:{topologies:job.protocol.policies,seeds:job.protocol.seeds,scenario,count:30,range_m:350,duration_s:65,warmup_s:5},
 results:job.results.filter(r=>r.scenario===scenario).map(r=>({...r,topology:r.policy_id}))};
}
export function pairedValue(value:number|null|undefined,precision=3):string {return value==null?'—':value.toFixed(precision);}
