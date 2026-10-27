"""Reexecute representative full-length runs against immutable accepted evidence.

Temporary new traces are independently reduced and compared byte-for-byte after
decompression, then removed. Their hashes and all per-field comparisons remain.
Original accepted traces and measurements are never modified.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tempfile

from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.resilience_analysis import verify_trace
from simulation.swarm.resilience_study import run_one, SCENARIOS


def trace_digest(path):
    digest = hashlib.sha256()
    with gzip.open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    args = p.parse_args()
    state = json.loads((args.input/'state.json').read_text())
    if state.get('status') != 'completed' or state.get('protocol',{}).get('id') != 'evidence_resilience_pilot_v1':
        p.error('requires a completed accepted resilience pilot')
    args.directory.mkdir(parents=True,exist_ok=False)
    source = source_fingerprint();checks=[]
    cases = [(s,a,7) for s in SCENARIOS for a in ('balanced_control','combined')]
    cases += [('normal','combined',seed) for seed in (42,99)]
    for scenario,arm,seed in cases:
        previous = next(r for r in state['results'] if (r['scenario'],r['policy_id'],r['seed']) == (scenario,arm,seed))
        with tempfile.TemporaryDirectory(prefix='trace-',dir=args.directory) as temporary:
            row = run_one(state['fleet_records'],state['simulation_config'],scenario,arm,seed,temporary)
            fields = {k:row[k] == v for k,v in previous.items() if k != 'timing'}
            independent = verify_trace(Path(temporary)/row['trace_file'],row)
            before = trace_digest(args.input/previous['trace_file'])
            after = trace_digest(Path(temporary)/row['trace_file'])
            passed = all(fields.values()) and before == after
            checks.append(dict(scenario=scenario,policy=arm,seed=seed,outcome='Passed' if passed else 'Failed',
                 scientific_fields=fields,trace_sha256_before=before,trace_sha256_after=after,
                 independent_verification=independent,timing=row['timing']))
        output = dict(outcome='Passed' if all(c['outcome']=='Passed' for c in checks) else 'Failed',
             baseline_source_sha256=state['source_sha256'],optimized_source_sha256=source,
             scope='14 full 85-second runs: all six scenarios, control/combined seed 7, and normal combined seeds 42/99. No new model inference.',
             note='Each new compressed trace was temporary; exact decompressed content matched retained accepted traces. Runtime timing is excluded from scientific comparisons.',checks=checks)
        (args.directory/'verification.json').write_text(json.dumps(output,indent=2))
        print(scenario,arm,seed,output['outcome'],flush=True)
        if not passed:raise RuntimeError('Changed output: '+str([k for k,v in fields.items() if not v]))
    if source_fingerprint() != source:raise RuntimeError('source changed during verification')


if __name__ == '__main__':main()
