"""Repeat congested control/combined runs in fresh processes with different hashes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.resilience_study import run_one


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--child',choices=('balanced_control','combined'));args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=False)
    if args.child:
        row=run_one([],yaml.safe_load(Path('config/swarm_config.yaml').read_text()),'congestion',args.child,7,args.directory)
        (args.directory/'row.json').write_text(json.dumps(row,indent=2));return
    source=source_fingerprint();checks=[]
    for policy in ('balanced_control','combined'):
        rows=[]
        for hashseed in ('1','99'):
            path=args.directory/(policy+'-'+hashseed)
            subprocess.run([sys.executable,'-m','scripts.check_resilience_repeatability','--directory',str(path),'--child',policy],
                env=dict(os.environ,PYTHONHASHSEED=hashseed),check=True)
            rows.append(json.loads((path/'row.json').read_text()))
        fields={k:rows[0][k]==value for k,value in rows[1].items() if k!='timing'}
        checks.append(dict(policy=policy,scenario='congestion',seed=7,python_hash_seeds=[1,99],equal=all(fields.values()),fields=fields))
        (args.directory/'verification.json').write_text(json.dumps(dict(source_sha256=source,checks=checks),indent=2))
        print(policy,all(fields.values()),flush=True)
        if not all(fields.values()):raise RuntimeError('Scientific outputs differ: '+str([k for k,v in fields.items() if not v]))
    if source_fingerprint()!=source:raise RuntimeError('Source changed during verification')


if __name__=='__main__':main()
