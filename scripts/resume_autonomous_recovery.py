"""Resume an interrupted source-bound recovery validation without rerunning accepted cases."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.recovery_scenarios import PRESETS
from scripts.run_autonomous_recovery import run_one


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--workers',type=int,choices=(1,2),default=2)
    a=p.parse_args();state=json.loads((a.directory/'state.json').read_text())
    if state['source_sha256']!=source_fingerprint():raise RuntimeError('Refusing to mix source fingerprints')
    if state['status']=='completed' or state['failures']:raise RuntimeError('Requires an interrupted batch without failed cases')
    if (a.directory/'STOP').exists():raise RuntimeError('Remove STOP only after reviewing the recorded source')
    if shutil.disk_usage(a.directory).free<1024**3:raise RuntimeError('At least 1 GiB free required')
    accepted={ (r['scenario'],r['autonomy']=='on',r['seed']) for r in state['results'] }
    pending=[(name,on,seed) for name in PRESETS for seed in (7,42,99) for on in (False,True) if (name,on,seed) not in accepted]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');retained=a.directory/('interrupted-traces-'+stamp)
    moved=[]
    for name,on,seed in pending:
        f=a.directory/f"{name}-{'on' if on else 'off'}-{seed}.jsonl.gz"
        if f.exists():
            retained.mkdir(exist_ok=True);shutil.move(f,retained/f.name);moved.append(f.name)
    state.setdefault('resumptions',[]).append(dict(at=stamp,completed_before=len(accepted),remaining=len(pending),retained_incomplete_traces=moved,source_sha256=source_fingerprint()))
    def save():
        f=a.directory/'state.tmp';f.write_text(json.dumps(state,indent=2));f.replace(a.directory/'state.json')
    save()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures={pool.submit(run_one,state['configuration'],name,on,seed,a.directory):(name,on,seed) for name,on,seed in pending}
        for future in as_completed(futures):
            try:
                r=future.result();state['results'].append(r);print(len(state['results']),r['scenario'],r['autonomy'],r['seed'],round(r['cpu_s'],2),flush=True)
            except Exception as e:state['failures'].append(dict(case=futures[future],error=str(e)))
            save()
    state['status']='completed' if len(state['results'])==60 and not state['failures'] else 'failed'
    if source_fingerprint()!=state['source_sha256']:state['status']='failed';state['failures'].append(dict(error='Source changed during resumption'))
    state['results'].sort(key=lambda r:(r['scenario'],r['seed'],r['autonomy']));save()
    fields=[k for k,v in state['results'][0].items() if not isinstance(v,(dict,list))]
    with (a.directory/'measurements.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(state['results'])
    if state['status']!='completed':raise RuntimeError('Incomplete validation; evidence retained')

if __name__=='__main__':main()
