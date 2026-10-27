"""Repeat one full recovery case in fresh hash-seeded processes; no convergence claim."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from scripts.run_autonomous_recovery import run_one
from simulation.swarm.experiments import source_fingerprint


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--child',action='store_true');a=p.parse_args();a.directory.mkdir(parents=True,exist_ok=False)
    batch=json.loads((a.batch/'state.json').read_text());assert batch['source_sha256']==source_fingerprint()
    scenario='recovery_total_loss';seed=7
    if a.child:
        r=run_one(batch['configuration'],scenario,True,seed,a.directory);(a.directory/'row.json').write_text(json.dumps(r,indent=2));return
    reference=next(r for r in batch['results'] if (r['scenario'],r['seed'],r['autonomy'])==(scenario,seed,'on'));checks=[]
    excluded={'cpu_s','wall_s','controller_timings','trace_sha256'}
    for value in ('1','99'):
        path=a.directory/('hash-'+value)
        subprocess.run([sys.executable,'-m','scripts.repeat_autonomous_recovery','--batch',str(a.batch),'--directory',str(path),'--child'],env=dict(os.environ,PYTHONHASHSEED=value),check=True)
        row=json.loads((path/'row.json').read_text());fields={k:row[k]==v for k,v in reference.items() if k not in excluded}
        checks.append(dict(python_hash_seed=value,outcome='Passed' if all(fields.values()) else 'Failed',fields=fields))
        report=dict(source_sha256=source_fingerprint(),scope='One complete 200-s total-interface-loss run, 30 UAVs seed 7, repeated in fresh processes with PYTHONHASHSEED 1 and 99 and compared to the batch. Runtime and gzip-container timestamps are excluded; canonical full-event outcome hashes are compared.',outcome='Passed' if all(c['outcome']=='Passed' for c in checks) else 'Failed',checks=checks)
        (a.directory/'verification.json').write_text(json.dumps(report,indent=2));print(value,checks[-1]['outcome'],flush=True)
        if checks[-1]['outcome']=='Failed':raise RuntimeError('Changed scientific fields: '+str([k for k,v in fields.items() if not v]))

if __name__=='__main__':main()
