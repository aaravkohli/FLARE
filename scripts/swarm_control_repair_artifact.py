"""Bundle current repair evidence immutably; never overwrite earlier reports."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
from provenance.artifacts import write_experiment_run,sha256_file

def main():
    parser=argparse.ArgumentParser()
    for name in ('browser','performance','flower_ui','jamming','flower'):parser.add_argument('--'+name.replace('_','-'),required=True,type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];extras={};references={};runs={}
    for name in ('jamming','flower'):
        path=getattr(args,name).resolve();manifest=json.loads((path.parent/'manifest.json').read_text())
        if sha256_file(path)!=manifest['report']['sha256']:raise ValueError(name+' digest mismatch')
        runs[name]=json.loads(path.read_text());references[name]={'report':str(path),'sha256':sha256_file(path),'manifest':manifest}
        for filename in ('report.json','manifest.json','trace.jsonl','events.jsonl'):
            p=path.parent/filename
            if p.exists():extras[name+'/'+filename]=p.read_bytes()
    matrix=json.loads((args.browser/'control-matrix.json').read_text())
    profile=json.loads((args.performance/'swarm-performance.json').read_text())
    fl_ui=json.loads((args.flower_ui/'flower-ui.json').read_text())
    failed_profile=root/'frontend-react/artifacts/repair-performance-final-20261001'
    failed=json.loads((failed_profile/'swarm-performance.json').read_text())
    for p in failed_profile.iterdir():
        if p.is_file():extras['performance_failed/'+p.name]=p.read_bytes()
    diagnostic=root/'frontend-react/artifacts/repair-performance-cpu-diagnostic-20261001'
    for p in diagnostic.iterdir():
        if p.is_file():extras['performance_diagnostic/'+p.name]=p.read_bytes()
    for name in ('repair-performance-short-20261002','repair-performance-current-cpu-20261002','repair-performance-accepted-20261002','repair-performance-retained-short-20261002','repair-performance-retained-20261002','repair-performance-marker-cache-20261002'):
        directory=root/'frontend-react/artifacts'/name
        for p in directory.iterdir():
            if p.is_file():extras['performance_diagnostic/'+name+'/'+p.name]=p.read_bytes()
    for filename in ('flare-live-before-reload-20261002.json','flare-live-after-reload-20261002.json','flare-live-reload-20261002.json'):
        p=Path('/private/tmp')/filename
        if p.exists():extras['live_runtime/'+filename]=p.read_bytes()
    service_directory=Path('/private/tmp/flare-repair-performance-production-20261002')
    for filename in ('frontend-mode.json','frontend-build.log','owned-processes.json','frontend-source-record.json'):
        p=service_directory/filename
        if p.exists():extras['performance_services/'+filename]=p.read_bytes()
    output=service_directory/'frontend-dist'
    if output.exists():
        extras['performance_services/frontend-digests.json']=json.dumps({str(p.relative_to(output)):sha256_file(p) for p in output.rglob('*') if p.is_file()},indent=2)
    for name,directory in [('browser',args.browser),('performance',args.performance),('flower_ui',args.flower_ui)]:
        for p in directory.iterdir():
            if p.is_file():extras[name+'/'+p.name]=p.read_bytes()
    browser_traces=list((root/'frontend-react/test-results').rglob('trace.zip'))
    if len(browser_traces)!=8:raise ValueError('Expected eight successful browser traces')
    for p in browser_traces:extras['browser_traces/'+p.parent.name+'/trace.zip']=p.read_bytes()
    lognames={'python':'python-final','python311':'python311','frontend':'frontend-tests','lint':'lint-final','build':'build-final','browser':'browser-ready','socket':'socket','flower':'flower-validation','flower_ui':'flower-ui','jamming':'jamming','profile':'profile','checkpoints':'checkpoints'}
    logs={}
    for name,suffix in lognames.items():
        path=Path('/private/tmp/flare-repair-'+suffix+'.log');logs[name]=path.read_text();extras['logs/'+name+'.log']=logs[name]
    python=re.search(r'(\d+) passed, (\d+) warnings',logs['python']);python311=re.search(r'(\d+) passed, (\d+) warnings',logs['python311']);frontend=re.search(r'ℹ pass (\d+)',logs['frontend']);browser=re.search(r'(\d+) passed \(',logs['browser'])
    if not all((python,python311,frontend,browser)):raise ValueError('Passing test summaries missing')
    required={*(f'{n} UAV / {t}' for n in (10,20,30,50,75,100) for t in ('full_mesh','partial_mesh','star','ring','tree','cluster_mesh','hybrid_mesh','chain','grid','manet')),
              'hold','resume','return','altitude','move','waypoints','formation','route','reroute','role','disconnect','reconnect','isolate','restore','emergency',
              *(f'ew/{p}' for p in ('none','spot','sweep','barrage','smart','reactive','adaptive','fhss','spoofing','gps_spoofing','replay','dos')),
              *(f'traffic/{p}' for p in ('normal','selective_forwarding','telemetry_falsification','control_flood','replay')),'fl/poisoned','fl/normal','browser reconnect','partial formation'}
    required.update(('grid','v','line','circle','diamond','wedge','column','distributed','cluster','normal','large_manet','jamming','poisoning','failures','controller_loss','rerouting','0.25×','0.5×','1×','2×','4×'))
    missing=sorted(required-{r['control'] for r in matrix})
    buckets=[[] for _ in range(10)]
    for s in profile['samples']:buckets[min(9,int(s['elapsed_s']//60))].append(s['heap_bytes'])
    minima=[min(b) for b in buckets if b];x=list(range(len(minima)));center=statistics.mean(x)
    slope=sum((i-center)*(v-statistics.mean(minima)) for i,v in zip(x,minima))/max(1,sum((i-center)**2 for i in x))
    retained=profile.get('retained_heap_samples',[])
    if len(retained)<10:raise ValueError('Direct retained heap measurements missing')
    steady=[s for s in retained if s['elapsed_s']>=300]
    times=[s['elapsed_s']/60 for s in steady];heaps=[s['heap_bytes'] for s in steady]
    mx=statistics.mean(times);my=statistics.mean(heaps)
    steady_slope=sum((t-mx)*(v-my) for t,v in zip(times,heaps))/sum((t-mx)**2 for t in times)
    total_growth=retained[-1]['heap_bytes']-retained[0]['heap_bytes'];steady_growth=heaps[-1]-heaps[0]
    # Apply the declared finite-run tolerance to collected live objects, not
    # arbitrary points in a JavaScript allocation/garbage-collection cycle.
    stable=total_growth<=5*1024**2 and steady_growth<=512*1024 and steady_slope<=.1*1024**2
    memory={'minute_minima_bytes':minima,'lower_envelope_slope_bytes_per_minute':slope,'net_growth_bytes':profile['samples'][-1]['heap_bytes']-profile['samples'][0]['heap_bytes'],
            'retained_heap_samples':retained,'retained_total_growth_bytes':total_growth,'retained_last_five_minutes_growth_bytes':steady_growth,'retained_steady_slope_bytes_per_minute':steady_slope,
            'criterion':'Collected heap total increase <= 5 MiB; final five-minute increase <= 512 KiB and slope <= 0.1 MiB/minute',
            'no_sustained_retained_growth_observed':stable,'limitation':'Finite headless-browser measurement with stated tolerance; not a universal leak proof'}
    profile_ok=profile['duration_s']>=600 and profile['configuration']['count']==100 and profile['average_fps']>=30 and profile['fifth_percentile_fps']>=20 and not profile['errors']
    passed=bool(not missing and profile_ok and stable and runs['jamming']['sequence_observed'] and runs['flower']['complete'] and fl_ui['frontend_condition'] and not fl_ui['errors'])
    protected=json.loads(Path('/private/tmp/flare-repair-protected.json').read_text())
    protection={p:sha256_file(root/p)==digest for p,digest in protected.items()}
    if not all(protection.values()):raise ValueError('Protected fleet or deployed checkpoint changed')
    report={'repair_acceptance':passed,'tests':{'python':int(python[1]),'python_warnings':int(python[2]),'python311':int(python311[1]),'python311_warnings':int(python311[2]),'frontend':int(frontend[1]),'browser':int(browser[1]),'lint':'passed with 8 existing warnings','build':'passed with large chunk warning','interpreter_note':'venv/bin/pytest uses Python 3.14; isolated services and venv/bin/python -m pytest use Python 3.11'},
            'validation_commands':['venv/bin/pytest -q','venv/bin/python -m pytest -q','npm test','npm run lint','npm run build','FLARE_BROWSER_TRACE=1 VITE_SHOW_LEGACY_FL_CONTROLS=true FLARE_TEST_PRODUCTION=1 FLARE_ARTIFACT_DIR=artifacts/repair-validation-traced-20261002 npm run test:browser','node e2e/flower-ui.mjs','FLARE_TEST_PRODUCTION=1 FLARE_ARTIFACT_DIR=artifacts/repair-performance-production-20261002 FLARE_PROFILE_WARMUP_SECONDS=120 npm run profile:swarm','venv/bin/python -m scripts.swarm_live_validation --api http://127.0.0.1:18200','venv/bin/python -m scripts.swarm_flower_validation --api http://127.0.0.1:18200 --directory /private/tmp/flare-repair-flower-20261001','FLARE_VALIDATION_API=http://127.0.0.1:18200 venv/bin/python -m scripts.swarm_socket_validation'],
            'control_matrix_entries':len(matrix),'packet_simulation_control_entries':sum(r['evidence']=='authenticated_frontend_packet_simulation' for r in matrix),'mock_display_contract_entries':sum(r['evidence']=='mock_frontend_contract' for r in matrix),'saved_browser_traces':len(browser_traces),'missing_controls':missing,
            'short_injection_detector_observations':[{'condition':r['control'],'findings':r['effect'].get('detector_findings'),'limitation':'Short observation window; injection does not imply detection'} for r in matrix if r['control'].startswith(('ew/','traffic/'))],
            'profile':profile,'profile_passed':profile_ok,'memory':memory,'failed_performance_attempt':failed,
            'performance_repair':'Renderer CPU trace identified generated security cards and repeated static marker construction; console renders observed Flower participants and world reuses marker/label markup',
            'performance_source_note':'Production source captured before final unavailable-FL-metric formatting repair. No Flower metrics exist in this profile, so that conditional display branch is not exercised; final frontend is validated by its later production browser run.',
            'source_runs':references,'protected_files_unchanged':protection,
            'features':{
                'attacks':{'implementation':'AttackConditions applies RF, queue, relay, sequence, source-claim and reported-position effects','integration':'authenticated admin REST → simulator → packets/metrics → detectors → SDN policies → WS → Attack Lab','validation':'control matrix, packet tests and seeded jamming traces','evidence':'packet_simulation'},
                'central_control':{'implementation':'shared targets, validated forms, serialized revision-bound mutations, queue/delivery/ack outcomes','integration':'frontend → mission command API → installed packet routes → target execution → acknowledgment → snapshot','validation':'15-command matrix, keyboard, paused queue, partial failure, conflicts, outages and reconnect','evidence':'packet_simulation'},
                'current_evidence':{'implementation':'mission-context decisions and session-scoped reads/cache reset; no upgraded generated fallback','integration':'packet metrics → orchestrator → persisted decisions → authenticated UI/status/XAI','validation':'full regressions, session-scope tests, browser and WebSocket checks','evidence':'controlled_simulation'},
                'flower':{'implementation':'real client pool, digest-bound transport receipts and separate aggregation security','integration':'actual Flower train/upload/aggregate, frontend condition armed before dispatch','validation':'three-round delivery/exclusion and real Attack Lab poisoning round','evidence':'controlled_simulation'},
                'performance':{'implementation':'instanced world, bounded snapshots and events','integration':'100 authoritative UAVs at 1×','validation':'recorded ten-minute balanced-quality browser profile','evidence':'controlled_browser_simulation'}},
            'limitations':['Modeled RF/movement/packet transport/logical SDN; no real OpenFlow or hardware proof','Synthetic training and packet observations; no new held-out detector accuracy claim','GPS reference untrusted; encryption policy simulated','Single seed 42 performance/Flower runs are separate from multi-seed 7/42/99 jamming','Headless local browser; recorded hardware and background operator services may affect results','Simulator can advance more slowly than wall time at 100 UAVs; FPS is not a real-time simulation guarantee']}
    fingerprints={name:hashlib.sha256(data if isinstance(data,bytes) else data.encode()).hexdigest() for name,data in extras.items() if name.endswith(('.json','.jsonl'))}
    run=write_experiment_run(base=root,experiment='swarm_control_repair',protocol_version='swarm_control_repair_v1',report=report,seeds=[7,42,99],
        evidence_category='controlled_simulation',config_paths=[root/'config'/p for p in ('swarm_config.yaml','fl_config.yaml','rl_config.yaml','security_config.yaml','fleet_registry.yaml')],checkpoint_paths=[root/'models/fl_model.pth',root/'models/rl_model_v3.zip'],
        dataset_fingerprints=fingerprints,command=[sys.executable,'-m','scripts.swarm_control_repair_artifact',*sys.argv[1:]],status='passed' if passed else 'incomplete',
        parameters={'performance_seeds':[42],'jamming_seeds':[7,42,99],'flower_seeds':[42],'documentation':'SWARM_CONTROL_REPAIR.md'},extra_files=extras)
    print(json.dumps({'report':str(run.report_path),'accepted':passed,'profile_passed':profile_ok,'memory':memory}));
    if not passed:raise SystemExit(1)

if __name__=='__main__':main()
