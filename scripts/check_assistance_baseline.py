"""Compare assistance-off outcomes with the captured pre-change simulator source."""
import argparse,gzip,hashlib,json,os,subprocess,sys,tarfile,tempfile
from pathlib import Path

def child(directory):
    # Executed with cwd/sys.path pointing at either the archived or current tree.
    sys.path.insert(0,str(Path.cwd()))
    import yaml
    from schemas.mission import MissionConfig
    from simulation.swarm.engine import SwarmEngine
    rows=[]
    for policy in ('balanced_v1','evidence_resilience_v1'):
        for autonomy in ('off','recovery_v1'):
            cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text());cfg.pop('affected_uav_assist',None)
            cfg.update(default_count=10,topology='full_mesh',topology_mode='manual',adaptive_policy=policy,autonomy_mode=autonomy,logical_channel_keys=True)
            cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110)
            e=SwarmEngine([],cfg,isolated=True);digest=hashlib.sha256();count=0
            def trace(record):
                nonlocal count
                row={k:record[k] for k in ('kind','simulation_time','data')};digest.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n');count+=1
            e.trace_sink=trace;e.playing=True
            for step in range(400):
                if step==120:e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
                e.tick(.05)
            rows.append(dict(policy=policy,autonomy=autonomy,trace_sha256=digest.hexdigest(),trace_events=count,generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets),topology=e.topology,positions=[n.position for n in e.nodes.values()]))
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text());cfg.pop('affected_uav_assist',None)
    cfg.update(default_count=10,topology='tree',topology_mode='manual',adaptive_policy='evidence_resilience_v1',autonomy_mode='off',logical_channel_keys=True)
    cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110)
    e=SwarmEngine([],cfg,isolated=True);digest=hashlib.sha256();previous=-1;backward=0;count=0
    def isolated_trace(record):
        nonlocal count,previous,backward
        now=record['data']['event_at'];backward+=int(now<previous-1e-8);previous=now;count+=1
        digest.update(json.dumps({k:record[k] for k in ('kind','simulation_time','data')},sort_keys=True,separators=(',',':')).encode()+b'\n')
    e.trace_sink=isolated_trace;e.playing=True
    for step in range(400):
        if step==160:e.autonomy.interfaces[list(e.nodes)[0]]=dict(direct=False,satellite=False,mesh=False)
        e.tick(.05)
    rows.append(dict(policy='evidence_resilience_v1',autonomy='off',fixture='total_interface_loss',backward_event_timestamps=backward,trace_sha256=digest.hexdigest(),trace_events=count,generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets)))
    Path(directory).write_text(json.dumps(rows,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--archive',type=Path);p.add_argument('--child',action='store_true');a=p.parse_args()
    if a.child:child(a.directory);return
    root=Path.cwd();a.directory.mkdir(exist_ok=False,parents=True)
    with tempfile.TemporaryDirectory(prefix='flare-assist-before-') as temp:
        old=Path(temp)
        with tarfile.open(a.archive) as tar:tar.extractall(old,filter='data')
        for path in root.iterdir():
            if path.name not in ('simulation','schemas','config'):(old/path.name).symlink_to(path,target_is_directory=path.is_dir())
        for name in ('simulation','schemas','config'):
            for path in (root/name).iterdir():
                dest=old/name/path.name
                if not dest.exists():dest.symlink_to(path,target_is_directory=path.is_dir())
        script=str(Path(__file__).resolve());paths=[]
        for label,cwd in [('before',old),('after',root)]:
            output=(a.directory/(label+'.json')).resolve();subprocess.run([sys.executable,script,'--child','--directory',str(output)],cwd=cwd,env={**os.environ,'PYTHONHASHSEED':'7','PYTHONPATH':str(cwd)},check=True);paths.append(output)
        before,after=[json.loads(f.read_text()) for f in paths]
        report=dict(outcome='Passed' if before==after else 'Failed',scope='Five complete 20s traces: balanced/safeguarded × autonomy off/on with a delivered manual ring transition at T+6s, plus a safeguarded total-interface-loss case. Assistance omitted/disabled. Full packet events, coordinates and outcomes compared exactly; cross-packet backward timestamp counts explicitly retained.',checks=[dict(policy=b['policy'],autonomy=b['autonomy'],equal=b==n,trace_before=b['trace_sha256'],trace_after=n['trace_sha256']) for b,n in zip(before,after)])
        (a.directory/'verification.json').write_text(json.dumps(report,indent=2));print(report['outcome'])
        if report['outcome']!='Passed':raise SystemExit(1)
if __name__=='__main__':main()
