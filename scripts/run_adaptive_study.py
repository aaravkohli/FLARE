"""Run the immutable study in private artifacts; never load/write live registry."""
import argparse
from pathlib import Path
from queue import Queue
import uuid
import yaml
from simulation.swarm.adaptive_study import study_worker

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset',choices=('pilot','evidence'),default='pilot')
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--workers',choices=range(1,5),type=int,default=1,help='Independent CPU workers; API jobs use one')
    parser.add_argument('--pilot-state',type=Path,help='Source-matched pilot state for storage projection only')
    args=parser.parse_args();args.directory.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    config=yaml.safe_load((root/'config/swarm_config.yaml').read_text())
    import json
    sizes=[]
    if args.pilot_state:
        from simulation.swarm.experiments import source_fingerprint
        pilot=json.loads(args.pilot_state.read_text())
        if pilot.get('source_sha256')!=source_fingerprint() or pilot.get('protocol',{}).get('preset')!='pilot':
            raise ValueError('Storage pilot must match the current source and pilot protocol')
        sizes=[a['bytes'] for a in pilot.get('artifact_manifest',[])]
    study_worker(uuid.uuid4().hex,dict(mode='adaptive_study',protocol_id='adaptive_resilience_v1',preset=args.preset),[],config,args.directory,Queue(),workers=args.workers,pilot_sizes=sizes)
    state=json.loads((args.directory/'state.json').read_text())
    print(json.dumps({k:state[k] for k in ('id','status','progress','source_sha256','results_sha256')},indent=2))
    if state['status']!='completed':raise SystemExit(1)

if __name__=='__main__':main()
