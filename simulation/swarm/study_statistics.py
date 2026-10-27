"""Paired seed-level research summaries, independent of simulation randomness."""
import hashlib
import numpy as np

METRICS=('pdr','average_latency_ms','jitter_ms','throughput_mbps','average_hops',
         'connected_components','route_convergence_ms','service_recovery_ms',
         'unrecovered_flows','switches','transition_failures','resource_cost')


def paired_summary(rows, scenario, baseline, metric='pdr', resamples=10000):
    selected=[r for r in rows if r['scenario']==scenario and r['policy_id'] in ('auto',baseline)]
    protocols={(r['protocol_id'],r['reference_workload'],r.get('design_sha256')) for r in selected}
    if len(protocols)>1: raise ValueError('incompatible study protocols or workloads')
    by_seed={}
    for row in selected:
        key=(row['seed'],row['policy_id'])
        if key in by_seed: raise ValueError('duplicate policy/seed observations')
        by_seed[key]=row
    differences=[]; missing=0
    for seed in sorted({r['seed'] for r in selected}):
        a=by_seed.get((seed,'auto')); b=by_seed.get((seed,baseline))
        if a is None or b is None: missing+=1; continue
        if (a['fleet_sha256'],a['shared_input_sha256'])!=(b['fleet_sha256'],b['shared_input_sha256']):
            raise ValueError('incompatible paired fleet or shared inputs')
        x,y=a.get(metric),b.get(metric)
        if x is None or y is None: missing+=1; continue
        differences.append((x-y)*(100 if metric=='pdr' else 1))
    mean=float(np.mean(differences)) if differences else None
    interval=None
    if len(differences)>=2:
        key=f'{scenario}:{baseline}:{metric}:paired_percentile_v1'
        rng=np.random.default_rng(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big'))
        draws=rng.choice(np.asarray(differences),size=(resamples,len(differences)),replace=True).mean(axis=1)
        interval=[float(v) for v in np.percentile(draws,[2.5,97.5])]
    return dict(scenario=scenario,baseline=baseline,metric=metric,mean_difference=mean,
        interval_95=interval,valid_pairs=len(differences),missing_pairs=missing,resamples=resamples,
        unit='percentage points' if metric=='pdr' else 'native metric units',
        comparison='primary' if baseline=='self_healing_mesh' and metric=='pdr' else 'exploratory',
        conditioning='delivered packets' if metric in ('average_latency_ms','jitter_ms','average_hops') else
            'received acknowledgments only; consult command_ack_ratio' if metric=='ack_latency_ms' else
            'recovered eligible evidence only; consult evidence_unrecovered per run' if metric=='evidence_recovery_ms' else
            'recovered flows only; consult unrecovered_flows' if metric in ('route_convergence_ms','service_recovery_ms') else 'run-level')


def statistical_report(rows, scenarios, baselines):
    return [paired_summary(rows,s,b,m) for s in scenarios for b in baselines for m in METRICS]
