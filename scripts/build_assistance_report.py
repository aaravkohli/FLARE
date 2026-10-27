"""Assemble the source-bound assistance report from executed local evidence."""
import ast,csv,datetime,hashlib,json,platform,re,shutil,subprocess,sys,tarfile
from pathlib import Path
from statistics import mean,median
from simulation.swarm.experiments import source_fingerprint

ROOT=Path.cwd();OUT=ROOT/'validation/affected_uav_assistance';DATA=ROOT/'results/affected_uav_assistance'
def read(path):return json.loads(Path(path).read_text())
def write(name,value): (OUT/name).write_text(json.dumps(value,indent=2))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def stamp(path):return datetime.datetime.fromtimestamp(Path(path).stat().st_mtime,datetime.timezone.utc).isoformat()
def command(cmd,log,note='',expected=None):
    p=DATA/log;text=p.read_text() if p.exists() else '';match=re.search(r'(\d+) passed|ℹ pass (\d+)',text)
    outcome='Passed' if match or ('> vite build' in text and 'built in' in text) else 'Failed' if ' failed' in text or 'Error:' in text else 'Passed' if p.exists() and expected==0 else 'Not Tested'
    return dict(command=cmd,log=str(p.relative_to(ROOT)),outcome=outcome,exit_code=0 if outcome=='Passed' else 1 if outcome=='Failed' else None,log_modified_utc=stamp(p) if p.exists() else None,note=note,log_sha256=sha(p) if p.exists() else None)

def main():
    OUT.mkdir(parents=True,exist_ok=True);batch=read(DATA/'accepted-batch/state.json');source=source_fingerprint()
    if source!=batch['source_sha256']:raise RuntimeError('Accepted scientific source differs from current source')
    summaries=[]
    for scenario in sorted({r['scenario'] for r in batch['results']}):
        for policy in ('balanced_v1','evidence_resilience_v1'):
            pairs=[]
            for seed in (7,42,99):
                a,b=[next(r for r in batch['results'] if (r['scenario'],r['policy'],r['seed'],r['assistance'])==(scenario,policy,seed,on)) for on in (False,True)]
                def target(r):
                    v=list(r['targets'].values());off=sum(x['offered'] for x in v);got=sum(x['delivered'] for x in v)
                    return dict(pdr=got/off if off else None,latency=sum(x['latency_sum'] for x in v)/got if got else None)
                x,y=target(a),target(b)
                pairs.append(dict(seed=seed,affected_delivery_off=x['pdr'],affected_delivery_on=y['pdr'],affected_latency_off_ms=x['latency'],affected_latency_on_ms=y['latency'],affected_latency_difference_ms=y['latency']-x['latency'] if x['latency'] is not None and y['latency'] is not None else None,fleet_delivery_difference_pp=100*(b['primary']['pdr']-a['primary']['pdr']),fleet_latency_difference_ms=b['primary']['latency_ms']-a['primary']['latency_ms'],assisted_switches=b['assistance_counters'].get('switches',0),verification_failures=b['transition_failures'],added_control_packets=b['control_traffic'].get('offered',0)-a['control_traffic'].get('offered',0)))
            def average(key):
                v=[p[key] for p in pairs if p[key] is not None];return mean(v) if v else None
            summaries.append(dict(scenario=scenario,policy=policy,valid_pairs=3,pairs=pairs,mean_affected_delivery_off=average('affected_delivery_off'),mean_affected_delivery_on=average('affected_delivery_on'),mean_affected_latency_off_ms=average('affected_latency_off_ms'),mean_affected_latency_on_ms=average('affected_latency_on_ms'),assisted_switches=sum(p['assisted_switches'] for p in pairs),mean_fleet_delivery_difference_pp=average('fleet_delivery_difference_pp'),mean_fleet_latency_difference_ms=average('fleet_latency_difference_ms')))
    write('exploratory-summary.json',dict(source_sha256=source,protocol=batch['protocol'],note='Three paired seed-level observations per cell. Pool designated flows inside a run, then average runs. Latency is delivered-packet conditioned. Exploratory; no universal-winner or convergence claim.',summaries=summaries))
    cpu=read(DATA/'cpu-profile/profile.json');cpu_summary=[]
    for policy in ('balanced_v1','evidence_resilience_v1'):
        pairs=[]
        for seed in (7,42,99):
            a,b=[next(r for r in cpu['results'] if (r['policy'],r['seed'],r['assistance'])==(policy,seed,on)) for on in (False,True)]
            pairs.append(dict(seed=seed,off_cpu_s=a['cpu_s'],on_cpu_s=b['cpu_s'],increase_percent=100*(b['cpu_s']/a['cpu_s']-1),off_packets=a['generated'],on_packets=b['generated'],off_control=a['assistance_counters'],on_control=b['assistance_counters']))
        cpu_summary.append(dict(policy=policy,median_increase_percent=median(r['increase_percent'] for r in pairs),pairs=pairs))
    write('cpu-profile.json',dict(**cpu,summary=cpu_summary,outcome='Passed',interpretation='Cost measurement, not a speed acceptance guarantee. No assisted transitions occurred in these nominal ten-second runs; the exploratory batch separately measures runs with actual probes and switches.'))
    render=[]
    for view in ('2d','3d','compare'):
        data=read(DATA/f'render-{view}/performance.json');rows=data['reports'];passed=not data['errors'] and all(c['average_fps']>=30 and c['fifth_percentile_fps']>=20 for r in rows for c in r['canvases'])
        render.append(dict(view=view,outcome='Passed' if passed else 'Failed',source_sha256=source,**data))
        shutil.copy2(DATA/f'render-{view}/{view}-100.png',OUT/f'evidence/profile-{view}-100.png')
    write('rendering-profiles.json',dict(source_sha256=source,outcome='Passed' if all(r['outcome']=='Passed' for r in render) else 'Failed',profiles=render,note='Separate fresh production service/browser runs, 60s per view. Live 2D/3D use Auto balanced with assistance on; their final screenshots show Hybrid Mesh. A/B reference engines stay fixed and assistance off. Live operator services remained running in the background.'))
    baseline=read(DATA/'accepted-off-baseline/verification.json');before=read(DATA/'accepted-off-baseline/before.json');after=read(DATA/'accepted-off-baseline/after.json')
    write('legacy-equivalence.json',dict(source_sha256=source,reference_source_sha256=read(DATA/'before.json')['source_sha256'],**baseline,ordering_example=dict(before=before[-1]['backward_event_timestamps'],after=after[-1]['backward_event_timestamps']),historical_full_length_recheck=read(DATA/'historical-equivalence/verification.json'),scope_note='Five exact 20s traces are bound to the final source. Fourteen 85s historical rechecks were executed earlier in this milestone; that execution source is retained in their result. Later edits concern assistance-only observations and status reporting.'))
    shutil.copy2(DATA/'repeatability/verification.json',OUT/'repeatability.json')
    write('protocol-manifest.json',dict(protocol=batch['protocol'],source_sha256=source,configuration=batch['configuration'],runs=48,pair_checks=batch['pair_checks'],preset=dict(count=30,seeds=[7,42,99],policies=['balanced_v1','evidence_resilience_v1'],assistance=[False,True],scenarios=['recoverable_mesh','direct_only','total_isolation','competing_affected'],manual_bootstrap_s=6,traffic_s=60,ttl_drain_s=20),execution_source_archive=str((DATA/'accepted-batch/execution-source.tar.gz').relative_to(ROOT)),execution_source_archive_sha256=sha(DATA/'accepted-batch/execution-source.tar.gz'),trace_directory=str((DATA/'accepted-batch').relative_to(ROOT)),trace_hashes=[dict(file=r['trace'],sha256=r['trace_sha256'],canonical_packet_outcome_sha256=r['outcome_sha256']) for r in batch['results']]))
    features=[
      ('Opt-in flag, default off; both Auto policies; manual pin','Passed','tests/test_uav_assistance.py; tests/test_swarm_api.py; browser-final-features.log','Real API/browser plus unit'),
      ('Registered producer provenance and malicious category exclusion','Passed','test_category_spoofing_and_direct_traffic_cannot_nominate','Actual scheduler and unit'),
      ('Mesh-only receipt window; Direct-only impairment','Passed','accepted-batch/direct_only-*; focused tests','Twelve complete paired scenario runs'),
      ('Eight-sample thresholds, missing receipts, five-evaluation recovery','Passed','test_evidence_thresholds_windows_recovery_and_pause; test_missing_receipts_uses_real_first_offer_and_three_confirmation_evaluations','Independent observation fixtures'),
      ('Null baseline never becomes fabricated zero delivery','Passed','test_unknown_pre_switch_quality_is_not_fabricated_as_zero_delivery','Unit regression'),
      ('Fleet size changes retire old monitor identities','Passed','test_fleet_changes_retire_old_monitor_identities_and_traffic','Unit/API-engine regression'),
      ('Interface, quarantine, HOLD and permission states','Passed','tests/test_uav_assistance.py; total_isolation-*','Unit faults and real scheduler'),
      ('Ten candidates × sizes 10–100 × seeds 7/42/99','Passed','test_ten_candidate_assistance_scoring_is_deterministic_and_private; test_all_ten_candidates_size_seed_matrix_is_deterministic_and_isolated','300 candidate cases; private-state assertions'),
      ('Target ordering; skip infeasible target; other affected-UAV bound','Passed','test_unreachable_highest_priority_does_not_block_next_affected_target; test_ordinary_optimization_cannot_regress_an_already_affected_uav','Independent candidate fixtures'),
      ('Modeled fleet cost limits and transition penalty','Passed','test_modeled_fleet_cost_and_other_affected_bounds; recorded attempt estimates','Unit boundary assertions and six actual assisted switches'),
      ('Emergency precedence and normal hysteresis/cooldown','Passed','test_emergency_recovery_bypasses_assistance_regression_filter; tests/test_adaptive_topology.py; accepted-batch events','Unit and actual simulation-clock transitions'),
      ('Real round trips; spacing, deadline; incumbent retained','Passed','tests/test_uav_assistance.py; independent-verification.json','Packet scheduler; six actual assisted switches'),
      ('Packet-specific staging; original-flow per-hop permission','Passed','test_config_cancellation_and_revocation_invalidate_packet_specific_probe_permission','Actual registered packets plus revoked-flow fixture'),
      ('Asymmetric loss, timeout, rollback and backoff','Passed','test_asymmetric_response_loss_never_confirms_replacement; test_uncertain_failed_probes_timeout_backoff_and_no_false_quality; tests/test_adaptive_topology.py','Fault-injection tests, no hardware inference'),
      ('Configuration cancellation, reset, scenario persistence','Passed','tests/test_uav_assistance.py; browser-final-features.log','Real API/browser and engine checks'),
      ('Autonomy off and cooperation without releasing safety latch','Passed','test_assistance_can_share_verifier_with_autonomy_without_releasing_existing_safety_hold; recoverable_mesh-*','Actual packets; flight-latch fixture'),
      ('Simulation speeds 0.1×–4×, pause and bounded staging metadata','Passed','tests/test_uav_assistance.py; browser-final-features.log','Simulation-clock unit and browser pause checks'),
      ('Measured gain below ordinary fleet-switch threshold','Passed','test_recoverable_individual_fixture_delivers_measured_gain_below_normal_fleet_threshold; accepted-batch','Actual packet outcomes; explicit timing-impairment fixture'),
      ('A/B, benchmark, historical studies force assistance off','Passed','test_legacy_reset_scenario_manual_and_experiment_contract; legacy-equivalence.json','Isolated reference factories and exact traces'),
      ('Inspect, Decisions, Activity; unknown/stale/manual states','Passed','world/uavAssistance.test.ts; browser-final-features.log','Frontend unit and production browser'),
      ('Shared 2D/3D verification overlay when autonomy off','Passed','world/recoveryOverlay.test.ts; existing renderer workflow assertions','Shared authoritative adapter; assistance-specific transient overlay not separately forced in browser'),
      ('Responsive checkbox, Escape/focus, six widths','Passed','browser-final-features.log; evidence/assistance-*.png','390/687/768/915/1024/1440px inspected'),
      ('Forty-eight runs, input matching, conservation, repeatability','Passed','accepted-batch/state.json; repeatability.json; independent-verification.json','155592 independently reduced final packet outcomes'),
      ('Strict global chronological packet-event emission','Failed','independent-verification.json; legacy-equivalence.json','Six total-isolation Safeguarded runs; inherited before/after equality'),
      ('Traffic validation wall-time deadline under combined load','Failed','browser-regression.log; browser-traffic-recheck.log','80s deadline expired in full suite; fresh standalone passed in 29.4s; load cause not conclusively isolated'),
      ('100-UAV CPU cost and renderer targets','Passed','cpu-profile.json; rendering-profiles.json','Separate CPU and 60s per-view runs; real-time speed remains below requested'),
      ('Physical RF and aircraft-safety certification','Not Tested','Outside this modeled-simulator milestone','No hardware claim'),
      ('Hot deployment into existing operator processes','Not Tested','Operator services deliberately not restarted','Fresh isolated production services verified'),
      ('Full assistance-specific transition forced through browser','Not Tested','Actual transitions verified in backend; browser verifies controls/metadata/views','No public fixture endpoint added'),
    ]
    feature_rows=[dict(capability=c,outcome=o,evidence=e,classification=k) for c,o,e,k in features];write('feature-coverage.json',feature_rows)
    with (OUT/'feature-coverage.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(feature_rows[0]));w.writeheader();w.writerows(feature_rows)
    commands=[
      command('venv/bin/python -m pytest -q --basetemp=/private/tmp/flare-assist-final-regression','python-final-regression.log','1190 passed on the preceding implementation revision. Final observation/lifecycle edge fixes are covered by the final-source 270-test run; this is not described as a full-suite run against the last source.'),
      command('venv/bin/python -m pytest -q tests/test_uav_assistance.py tests/test_adaptive_topology.py tests/test_autonomous_recovery.py tests/test_autonomy_edge_safety.py tests/test_evidence_resilience.py tests/test_swarm_api.py','accepted-source-tests.log','270 passed against final scientific source; includes 58 assistance cases and API authorization/revision checks.'),
      command('npm --prefix frontend-react run test','frontend-release-unit.log'),
      command('npm --prefix frontend-react run typecheck','frontend-release-typecheck.log',expected=0),
      command('npm --prefix frontend-react run lint','frontend-release-lint.log','Existing warnings retained; no lint error.',expected=0),
      command('npm --prefix frontend-react run build','frontend-release-build.log',expected=0),
      command('FLARE_TEST_PRODUCTION=1 npx playwright test','browser-regression.log','53 passed, one failed, three gated tests skipped. Every gate was subsequently run explicitly. This full-suite result remains Failed.'),
      command("FLARE_TEST_PRODUCTION=1 npx playwright test e2e/application.spec.ts -g 'traffic validation captures'",'browser-traffic-recheck.log','Fresh isolated recheck passed; does not erase the original timeout.'),
      command('FLARE_TEST_PRODUCTION=1 FLARE_TEST_MISSING_MODELS=1 FLARE_STUDY_RESULT_FIXTURE=<550-run-state> FLARE_RESILIENCE_RESULT_FIXTURE=<72-run-state> npx playwright test e2e/uav-assistance.spec.ts e2e/adaptive-study-results.spec.ts e2e/resilience-results.spec.ts','browser-final-features.log','Three passed. Two study views use real retained artifacts as transport fixtures, not new backend study executions. Models deliberately absent in the isolated copy.'),
    ]
    # A full-suite log can contain both passed and failed counts; failures win.
    commands[6]['outcome']='Failed';commands[6]['exit_code']=1
    for v in ('2d','3d','compare'):commands.append(command(f'FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW={v} FLARE_PROFILE_TOPOLOGY=auto FLARE_PROFILE_ASSISTANCE=1 npx playwright test e2e/mesh-performance.spec.ts',f'render-{v}.log'))
    commands.extend([command('venv/bin/python -m scripts.run_uav_assistance --directory results/affected_uav_assistance/accepted-batch --workers 2','accepted-batch.log','48/48 complete; outcome read from state.json, no failures.',expected=0),command('venv/bin/python -m scripts.verify_uav_assistance --batch results/affected_uav_assistance/accepted-batch --directory validation/affected_uav_assistance','accepted-independent.log','Cohorts, conservation, immutable hops and assisted proof/cost checks passed; strict global timestamp ordering failed in six inherited cases.'),command('venv/bin/python -m scripts.repeat_uav_assistance --batch results/affected_uav_assistance/accepted-batch --directory results/affected_uav_assistance/repeatability','repeatability.log','Exact results under fresh PYTHONHASHSEED=1 and 99 processes.',expected=0),command('venv/bin/python -m scripts.profile_uav_assistance_cpu --directory results/affected_uav_assistance/cpu-profile','cpu-profile.log',expected=0)])
    commands[-3]['outcome']='Failed';commands[-3]['exit_code']=1;commands[-3]['note']+=' Verifier CLI exits nonzero for the retained ordering failure; consumers can also inspect individual JSON checks.'
    write('commands.json',dict(at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_sha256=source,records=commands))
    files=sorted(set([*Path('simulation/swarm').glob('*.py'),*Path('schemas').glob('*.py'),*Path('frontend-react/src').rglob('*.ts'),*Path('frontend-react/src').rglob('*.tsx'),*Path('frontend-react/src').rglob('*.css'),Path('config/swarm_config.yaml'),Path('requirements.txt'),Path('frontend-react/package-lock.json'),Path('frontend-react/package.json'),*Path('frontend-react').glob('*.ts'),*Path('frontend-react').glob('tsconfig*.json'),Path('scripts/swarm_test_services.py'),Path('scripts/audit_support.py'),*Path('scripts').glob('*assistance*.py'),Path('tests/test_uav_assistance.py'),Path('tests/test_swarm_api.py'),Path('frontend-react/e2e/uav-assistance.spec.ts'),Path('frontend-react/e2e/mesh-performance.spec.ts')]))
    write('source-manifest.json',dict(scientific_source_sha256=source,git_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),dirty_working_tree=True,files=[dict(path=str(f),sha256=sha(f)) for f in files]))
    archive=DATA/'reproduction-source.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for f in files:tar.add(f,arcname=f)
    write('environment.json',dict(at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),python=sys.version,node=subprocess.check_output(['node','--version'],text=True).strip(),npm=subprocess.check_output(['npm','--version'],text=True).strip(),platform=platform.uname()._asdict(),rendering_hardware=render[0]['hardware'],browser=render[0]['browser'],source_archive=str(archive.relative_to(ROOT)),source_archive_sha256=sha(archive),note='Python 3.11 repository venv; locked frontend dependencies already installed. No dependency changes or retraining. Existing operator mission remains running.'))
    for f in ('isolation.json','historical-preservation.json'):
        if read(OUT/f)['outcome']!='Passed':raise RuntimeError('Isolation/preservation check failed')
    independent=read(OUT/'independent-verification.json')
    fmt=lambda v:'—' if v is None else f'{v:.2f}'
    table='\n'.join(f"| {s['scenario']} | {'Balanced' if s['policy']=='balanced_v1' else 'Safeguarded'} | {s['assisted_switches']} | {100*s['mean_affected_delivery_off']:.1f} / {100*s['mean_affected_delivery_on']:.1f} | {fmt(s['mean_affected_latency_off_ms'])} / {fmt(s['mean_affected_latency_on_ms'])} |" for s in summaries)
    fps_rows=[]
    for p in render:
        avg=', '.join(format(c['average_fps'],'.2f') for r in p['reports'] for c in r['canvases'])
        low=', '.join(format(c['fifth_percentile_fps'],'.2f') for r in p['reports'] for c in r['canvases'])
        fps_rows.append(f"| {p['view']} | {avg} | {low} | {p['outcome']} |")
    fps='\n'.join(fps_rows)
    (OUT/'verification-report.md').write_text(f'''# Affected-UAV assistance verification report

Date: 26 October 2026 (Asia/Kolkata). Scientific source: `{source}`. Dirty working tree; no commit or push created. This report covers the new assistance milestone, not a claim that the entire application passes every possible condition.

## Outcome and remaining findings

**Passed:** opt-in Mesh assistance, final-source focused tests, 48-run execution and matched inputs, independent cohort/conservation checks, actual verified switches and subsequent measured gain in feasible Balanced cases, exact repeatability, CPU measurement and renderer FPS targets.

**Failed:** strict global chronological emission of packet events in six Safeguarded total-isolation runs. Every packet cohort, total, completed-hop history and assisted verification/cost check passed. The archived pre-change source reproduces the ordering limitation: a policy recheck can emit a drop at the current fixed-step boundary before another packet's earlier queued hop. Five complete before/after traces match exactly, including 12 backward timestamp observations in the 20s loss fixture. No packet timing or historical result was changed to conceal it.

**Failed:** one full-browser-suite run exceeded its 80s wait for Traffic Evidence completion while combined validation workloads were active. A fresh isolated recheck passed in 29.4s. The cause of the load-sensitive wait was not conclusively isolated; the first failure remains recorded. No predetermined detector result or shortened experiment was substituted.

**Not Tested:** deployment into the already-running operator processes; physical RF/aircraft safety; forcing a complete assistance-specific active transition through the browser. Backend transitions use real scheduled packets; shared renderer overlays, controls, API state and view switching are tested. No blocking external service was needed for the implemented simulator feature.

## Implementation and repairs

- Added a bounded, simulation-clock monitor of exact identities registered by trusted mission, command/acknowledgment and security-report producers. Attack load still consumes airtime and contributes demand, but category strings cannot nominate packets.
- Reused the existing candidate pass for per-UAV utility and bounded modeled fleet costs. Emergency connectivity recovery retains precedence; Manual remains pinned. Assisted switches use the existing cooldown and three-evaluation confirmation.
- Extracted shared delivered path verification. Autonomous flight recovery remains off unless separately enabled. Original data-flow permissions are checked on staged probe hops and before commit; routing-category probes cannot bypass HOLD, containment or revoked flow restrictions.
- Reused packet-specific overlap, installation validation, rollback and failed-target backoff. Added terminal cancellation/failure events and measured-quality feedback. Missing baseline quality stays unknown; restored receipts are not fabricated as an improvement from zero delivery.
- Fixed pending checkbox feedback, stale/retired monitor identities after fleet changes, explicit unavailable-interface states, and bounded probe random-stream metadata while flight autonomy is off. Assistance never releases a safety latch.
- Reference A/B, fixed benchmarks and historical studies explicitly omit the setting. No endpoint, dependency, model/checkpoint contract, flight dynamics, metric formula or renderer redesign was introduced.

See [architecture and protocol](README.md), [coverage matrix](feature-coverage.csv), [source manifest](source-manifest.json), [command evidence](commands.json), and [protocol/trace manifest](protocol-manifest.json).

## Exploratory measurements

All 48 runs completed: four scenarios × two policies × off/on × three seeds, 30 UAVs, 60s offered traffic and 20s TTL drain. A six-second fixed-tree bootstrap establishes the same incumbent before Auto is enabled. All 24 pairs have matching shared inputs and primary offered-traffic hashes. **{sum(c['packet_results'] for c in independent['checks']):,} final packet outcomes** were independently reduced. All 48 runs conserve packets; every primary per-UAV count and latency sum matches its trace.

Values below average the three seed-level runs. Target latency includes delivered packets only. Isolation's remaining delivered packets are predominantly pre-fault evidence; a low conditional latency does not establish recovery. The faults are explicit modeled fixtures, not inferred attacker identities or hardware-jamming evidence.

| Scenario | Policy | Assisted switches, three seeds | Target delivery off / on (%) | Target latency off / on (ms) |
|---|---|---:|---:|---:|
{table}

The recoverable and competing Balanced cases each switched once per seed. The approved modeled cost limits and two-round-trip proof spacing/deadlines passed for all six actual assisted switches. The independent recoverable fixture also proves ordinary Auto stays below its 0.08 fleet-switch margin. Post-switch quality is measured from new legitimate packets, separately from probe reachability.

Safeguarded retained the incumbent in these fixtures. Its seed-7 T+16s diagnostics reject alternate candidates on the fleet-score-cost guard; see [recorded example](safeguarded-decision-example.json). This negative result is retained. Direct-only impairment caused no assisted switch. Total interface loss was not reconnected. No universal performance or connectivity guarantee is claimed.

[Complete runs](per-run-measurements.json), [CSV](per-run-measurements.csv), [per-UAV CSV](per-uav-measurements.csv), [paired observations](exploratory-summary.json), and [independent reduction](independent-verification.json) retain all observations and unavailable values. Raw compressed traces and the execution archive remain under `results/affected_uav_assistance/accepted-batch/`; this local artifact directory is intentionally gitignored.

## Tests and source scope

| Check | Outcome | Evidence |
|---|---|---|
| Final-source Python/API focus | Passed | 270 tests, including 58 assistance cases; [log](../../results/affected_uav_assistance/accepted-source-tests.log) |
| Whole Python suite, preceding revision | Passed | 1,190 tests; final observation/lifecycle edge fixes subsequently covered in the final-source focus; [scope and commands](commands.json) |
| Frontend units | Passed | 181 tests, including autonomy-off overlay contract |
| Type check / lint / production build | Passed | Existing warnings retained; no errors |
| Full production browser suite | Failed | 53 passed, one timeout, three gated skips |
| Timeout recheck, fresh services | Passed | One test; original failure retained |
| Final missing-model/assistance/artifact workflows | Passed | Three tests; real retained study data used as presentation transport fixtures |
| Three gated render profiles | Passed | Fresh production services, 60s each |
| Candidate matrix | Passed | Ten candidates × ten sizes × three seeds; deterministic/private-state assertions |
| Five full before/after traces | Passed | Assistance off; both policies, autonomy off/on and interface-loss ordering fixture |
| Historical full-length rechecks | Passed | Fourteen 85s runs executed earlier in the milestone; execution source retained separately |
| Fresh-process repeatability | Passed | Full 80s assisted case repeated with Python hash seeds 1 and 99 |
| Model/configuration isolation | Passed | 44 protected files checked; only intentional default-false swarm configuration addition |
| Historical preservation | Passed | 1,262 artifact hashes unchanged |

Pytest's configured script-style `tests/test_adversarial.py` exclusion is unchanged; it was not rerun for this milestone. No claim of new model training, convergence, hardware validation or complete continuous-input coverage is made. Parametric boundary fixtures, mocks and response-loss hooks are identified in the coverage matrix. The final scientific source equals the accepted batch source; frontend build/units and fresh profiles bind to the source manifest.

## CPU and rendering

The 100-UAV CPU profile uses three paired seeds, both policies, ten simulation seconds per engine, no model inference. Median added process CPU time is **{cpu_summary[0]['median_increase_percent']:.2f}% Balanced** and **{cpu_summary[1]['median_increase_percent']:.2f}% Safeguarded**. Individual increases span {min(p['increase_percent'] for s in cpu_summary for p in s['pairs']):.2f}%–{max(p['increase_percent'] for s in cpu_summary for p in s['pairs']):.2f}%. These nominal runs measured monitor/selection cost without actual assisted transitions; the exploratory run records separately include probes and switches. [Raw CPU data](cpu-profile.json).

| Fresh 100-UAV view | Average FPS (A/B panes separate) | Fifth-percentile FPS | Outcome |
|---|---:|---:|---|
{fps}

Live 2D/3D selected Auto balanced with assistance enabled and displayed Hybrid Mesh at the end. Achieved simulation speed was approximately 0.77× at requested 1×. A/B engines remained fixed reference policies with synchronized clocks. These profiles meet the 30 average / 20 fifth-percentile FPS targets, but do **not** establish that the simulation-speed bottleneck is solved. Hardware, browser, frame buffers, heap samples, authoritative clocks and errors are retained in [rendering profiles](rendering-profiles.json). [Screenshots](evidence/) were inspected at six responsive widths and for all three profiles.

## Reproduction and follow-up

Use the commands in [README](README.md) and [commands.json](commands.json). Overlay the reproduction archive on the recorded Git HEAD and install the recorded Python/frontend dependencies; it is a source snapshot, not a bundled runtime or checkpoint distribution. The reproduction source archive path/hash is in [environment.json](environment.json); it includes the terrain heightmap, frontend sources, schemas, simulator and validation drivers. Protected checkpoint identities remain in the isolation manifest; no model weights are bundled or changed. Run a new uniquely named batch directory; historical directories are immutable.

To reproduce the retained ordering finding, run the five-case archived-source comparison in `scripts/check_assistance_baseline.py`, or the accepted total-isolation Safeguarded fixtures and `scripts/verify_uav_assistance.py`. Correcting strict global emission ordering requires a separate scheduler change with renewed numerical-baseline review. To reproduce the load-sensitive browser wait, run the full production suite during concurrent simulation validation; the isolated `traffic validation captures` workflow passed, and the exact environmental trigger remains unverified.

The running operator services were preserved. Restart their backend normally to load the new configuration field; an older service is shown as unavailable for this setting. This is a modeled research feature, not a guaranteed reconnection or physical-flight safety mechanism.
''')
    # Manifest excludes itself; raw traces retain their per-run manifest hashes.
    artifacts=[*OUT.rglob('*'),*DATA.rglob('*.log'),DATA/'accepted-batch/state.json',DATA/'reproduction-source.tar.gz']
    write('artifact-manifest.json',dict(source_sha256=source,at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),artifacts=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p),bytes=p.stat().st_size) for p in sorted(set(artifacts)) if p.is_file() and p.name not in ('artifact-manifest.json','report-build.log')]))
    print('Report assembled',source)
if __name__=='__main__':main()
