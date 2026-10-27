"""Repeat one full assisted fixture in fresh Python hash-seeded processes."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from scripts.run_uav_assistance import run_one
from simulation.swarm.experiments import source_fingerprint

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--child',action='store_true');a=p.parse_args();a.directory.mkdir(parents=True,exist_ok=False)
    batch=json.loads((a.batch/'state.json').read_text());assert batch['source_sha256']==source_fingerprint()
    if a.child:
        row=run_one(batch['configuration'],'recoverable_mesh',True,'balanced_v1',7,a.directory);(a.directory/'row.json').write_text(json.dumps(row,indent=2));return
    reference=next(r for r in batch['results'] if (r['scenario'],r['assistance'],r['policy'],r['seed'])==('recoverable_mesh',True,'balanced_v1',7));checks=[]
    for value in ('1','99'):
        directory=a.directory/('hash-'+value);subprocess.run([sys.executable,'-m','scripts.repeat_uav_assistance','--batch',str(a.batch),'--directory',str(directory),'--child'],env={**os.environ,'PYTHONHASHSEED':value},check=True)
        row=json.loads((directory/'row.json').read_text());fields={k:row[k]==v for k,v in reference.items() if k not in ('cpu_s','wall_s','timings','trace_sha256')}
        checks.append(dict(python_hash_seed=value,outcome='Passed' if all(fields.values()) else 'Failed',fields=fields))
        (a.directory/'verification.json').write_text(json.dumps(dict(source_sha256=source_fingerprint(),outcome='Passed' if all(c['outcome']=='Passed' for c in checks) else 'Failed',scope='One complete 80s, 30-UAV assisted Mesh fixture, seed 7; fresh processes with PYTHONHASHSEED 1 and 99. Exact canonical full packet trace, measurements and transition events compared; CPU/wall timings and gzip-container timestamp excluded.',checks=checks),indent=2));print(value,checks[-1]['outcome'],flush=True)
        if checks[-1]['outcome']!='Passed':raise SystemExit(1)
if __name__=='__main__':main()
