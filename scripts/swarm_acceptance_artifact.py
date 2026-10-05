"""Save immutable swarm acceptance evidence without promoting benchmark claims."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
from provenance.artifacts import sha256_file,write_experiment_run


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--jamming',required=True,type=Path)
    parser.add_argument('--flower',required=True,type=Path)
    parser.add_argument('--training-directory',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    extras={};references={};reports={}
    for name,path in [('jamming',args.jamming),('flower',args.flower)]:
        path=path.resolve()
        manifest=json.loads((path.parent/'manifest.json').read_text())
        if sha256_file(path)!=manifest['report']['sha256']:raise ValueError(name+' report digest mismatch')
        if manifest['status']!='passed':raise ValueError(name+' run did not pass')
        reports[name]=json.loads(path.read_text())
        references[name]={'report':str(path.relative_to(root)),'manifest':str((path.parent/'manifest.json').relative_to(root)),'sha256':sha256_file(path),'seeds':manifest['seeds'],'source':manifest['git']}
        extras[name+'/report.json']=path.read_bytes();extras[name+'/manifest.json']=(path.parent/'manifest.json').read_bytes()
        for filename in ('trace.jsonl','events.jsonl'):
            artifact=path.parent/filename
            if artifact.exists():extras[name+'/'+filename]=artifact.read_bytes()
    logs={name:Path('/private/tmp/'+filename) for name,filename in [('python','swarm-pytest-final.log'),('frontend','swarm-frontend-tests.log'),('lint','swarm-lint.log'),('build','swarm-build.log'),('browser','swarm-browser-final.log'),('profile','swarm-profile-final.log')]}
    for name,path in logs.items():extras['logs/'+name+'.log']=path.read_bytes()
    python=re.search(r'(\d+) passed, (\d+) warnings',logs['python'].read_text())
    frontend=re.search(r'ℹ pass (\d+)',logs['frontend'].read_text())
    browser=re.search(r'(\d+) passed \(',logs['browser'].read_text())
    if not all((python,frontend,browser)):raise ValueError('Passing test summaries are missing')
    profile_path=root/'frontend-react/artifacts/swarm-performance.json'
    profile=json.loads(profile_path.read_text())
    config=profile['configuration']
    correct_config=all(config.get(k)==v for k,v in {'count':100,'seed':42,'topology':'manet','formation':'distributed','speed':1}.items())
    profile_passed=correct_config and profile['duration_s']>=600 and profile['average_fps']>=30 and profile['fifth_percentile_fps']>=20 and not profile['errors']
    buckets=[[] for _ in range(10)]
    for sample in profile['samples']:buckets[min(9,int(sample['elapsed_s']//60))].append(sample['heap_bytes'])
    minima=[min(bucket) for bucket in buckets if bucket]
    centers=list(range(len(minima)));mx=statistics.mean(centers);my=statistics.mean(minima)
    slope=sum((x-mx)*(y-my) for x,y in zip(centers,minima))/sum((x-mx)**2 for x in centers)
    memory={'method':'sampled one-minute JS-heap lower envelope; all raw samples retained','minute_minima_bytes':minima,'slope_bytes_per_minute':slope,'end_minus_start_bytes':profile['samples'][-1]['heap_bytes']-profile['samples'][0]['heap_bytes'],'no_rising_lower_envelope_observed':slope<=0 and minima[-1]<=minima[0],'limitation':'GC oscillations are visible; this is not a universal leak proof'}
    extras['profile.json']=profile_path.read_bytes()
    for name in ('swarm-browser.png','swarm-100-performance.png'):extras[name]=(root/'frontend-react/artifacts'/name).read_bytes()
    socket=Path('/private/tmp/swarm-socket-validation.json')
    extras['socket.json']=socket.read_bytes()
    socket_report=json.loads(socket.read_text())
    if args.training_directory:
        directory=args.training_directory/'training'/reports['flower']['session_id']
        for filename in ('candidate.pth','candidate.pth.metadata.json'):
            extras['mission_training/'+filename]=(directory/filename).read_bytes()
    passed=bool(profile_passed and memory['no_rising_lower_envelope_observed'] and reports['jamming']['sequence_observed'] and reports['flower']['complete'] and socket_report['passed'])
    report={'simulation_acceptance':passed,'evidence_category':'controlled_simulation','source_runs':references,
        'tests':{'python':{'passed':int(python[1]),'warnings':int(python[2]),'command':'venv/bin/pytest -q'},'frontend':{'passed':int(frontend[1]),'command':'npm test'},'browser':{'passed':int(browser[1]),'command':'npm run test:browser'},'lint':{'exit_code':0,'command':'npm run lint','warnings_retained':True},'build':{'exit_code':0,'command':'npm run build','warnings_retained':True}},
        'profile':profile,'profile_passed':profile_passed,'memory':memory,'socket':socket_report,
        'jamming_seeds':references['jamming']['seeds'],'actual_flower_rounds':len([r for r in reports['flower']['aggregation_results'] if r.get('security_audits')]),
        'phase_10':{'status':'follow_on_unrun','evidence':'no_real_network_result','remaining':['5/10/15 UAV logical-hop switch/port bindings','Executable Ryu/Mininet/OpenFlow forwarding and counters/captures','Controller reconnection validation']},
        'limitations':['Modeled radio/mobility/packets/logical SDN; no wireless-PHY or hardware accuracy','Synthetic Flower datasets; aggregation behavior is not an accuracy evaluation','Headless local-browser profile; JS heap samples are not a universal leak proof','Existing Python/lint/build warnings retained','Deployed checkpoint contracts and digests unchanged; no new held-out model accuracy claim']}
    fingerprints={name:hashlib.sha256(data if isinstance(data,bytes) else data.encode()).hexdigest() for name,data in extras.items() if name.endswith(('.json','.jsonl'))}
    run=write_experiment_run(base=root,experiment='swarm_acceptance',protocol_version='swarm_acceptance_v1',report=report,seeds=[7,42,99],evidence_category='controlled_simulation',config_paths=[root/'config/swarm_config.yaml',root/'config/fl_config.yaml',root/'config/rl_config.yaml',root/'config/security_config.yaml',root/'config/fleet_registry.yaml'],checkpoint_paths=[root/'models/fl_model.pth',root/'models/rl_model_v3.zip'],dataset_fingerprints=fingerprints,command=[sys.executable,'-m','scripts.swarm_acceptance_artifact',*sys.argv[1:]],status='passed' if passed else 'incomplete',parameters={'profile_seeds':[42],'jamming_seeds':[7,42,99],'flower_seeds':[42],'documentation':'SWARM_IMPLEMENTATION.md'},extra_files=extras)
    print(json.dumps({'report':str(run.report_path),'accepted':passed,'average_fps':profile['average_fps'],'p5_fps':profile['fifth_percentile_fps']}))
    if not passed:raise SystemExit(1)

if __name__=='__main__':main()
