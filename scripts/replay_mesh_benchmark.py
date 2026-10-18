"""Reproduce an exported benchmark with its recorded fleet, config and source digest."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from queue import Queue
import tempfile
import uuid
from schemas.mission import ExperimentRequest
from simulation.swarm.experiments import _worker, source_fingerprint


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path,nargs='?',default=Path('results/mesh_topology_benchmark_v2.json'))
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    reference=json.loads(args.report.read_text())
    if reference.get('evidence')!='packet_simulation':
        parser.error('input must be a packet-simulation benchmark export')
    if reference.get('source_sha256')!=source_fingerprint():
        parser.error('simulator source differs from the recorded benchmark')
    request=ExperimentRequest.model_validate(reference['configuration'])
    if request.mode!='benchmark': parser.error('only completed benchmark exports can be replayed')
    output=args.output or Path(tempfile.mkdtemp(prefix='flare-mesh-replay-'))
    if output.exists() and any(output.iterdir()): parser.error('output directory must be empty')
    output.mkdir(parents=True,exist_ok=True)
    _worker(uuid.uuid4().hex,request.model_dump(),reference['fleet_records'],reference['simulation_config'],str(output),Queue())
    actual=json.loads((output/'state.json').read_text())
    matched=actual['status']=='completed' and actual.get('results_sha256')==reference.get('results_sha256')
    print(json.dumps(dict(matched=matched,results_sha256=actual.get('results_sha256'),output=str(output),error=actual.get('error')),indent=2))
    if not matched: raise SystemExit(1)


if __name__=='__main__': main()
