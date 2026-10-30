"""Isolated host services for integration and Playwright; never touch operator artifacts."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from simulation.swarm.session import bootstrap
from scripts.audit_support import runtime_copy


def isolated_auth(environment):
    """Never reuse operator or development credentials in disposable services."""
    supplied = json.loads(Path(environment['FLARE_TEST_CREDENTIALS_FILE']).read_text()) if environment.get('FLARE_TEST_CREDENTIALS_FILE') else {}
    credentials = {
        'username': environment.get('FLARE_TEST_OPERATOR') or supplied.get('username') or 'isolated-browser-operator',
        'password': environment.get('FLARE_TEST_PASSWORD') or supplied.get('password') or secrets.token_urlsafe(32),
        'sdnToken': environment.get('FLARE_TEST_SDN_TOKEN') or supplied.get('sdnToken') or secrets.token_urlsafe(32),
    }
    return credentials, {
        'AJ_ENV': 'test', 'AJ_ADMIN_USERNAME': credentials['username'],
        'AJ_ADMIN_PASSWORD': credentials['password'], 'AJ_SDN_TOKEN': credentials['sdnToken'],
        'AJ_SECRET_KEY': secrets.token_urlsafe(48),
    }


def write_credentials(directory, credentials):
    filename = directory / 'credentials.json'
    with open(filename, 'w', opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
        json.dump(credentials, handle)
    filename.chmod(0o600)
    return filename


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=Path(os.environ['FLARE_TEST_DIRECTORY']) if os.getenv('FLARE_TEST_DIRECTORY') else None)
    parser.add_argument('--flower-rounds',type=int,default=0)
    parser.add_argument('--audit-smoke-training',action='store_true',help='Private copy only: one local epoch, five clients')
    parser.add_argument('--production',action='store_true',default=os.getenv('FLARE_TEST_PRODUCTION')=='1')
    for name,default in [('simulation',18080),('api',18000),('frontend',15173),('flower',18090)]:parser.add_argument('--'+name+'-port',type=int,default=default)
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,11): raise RuntimeError('Integration services require repository venv/bin/python (Python 3.11)')
    root=Path(__file__).resolve().parents[1];directory=args.directory or Path(tempfile.mkdtemp(prefix='flare-swarm-repair-'));directory.mkdir(parents=True,exist_ok=True)
    runtime_root=runtime_copy(root,directory/'project')
    if os.getenv('FLARE_TEST_MISSING_MODELS')=='1':
        # Explicit fault fixture in the audit-owned copy; operator weights stay intact.
        for model in (runtime_root/'models').rglob('*'):
            if model.is_file() and model.suffix in ('.pth','.pt','.onnx','.pkl','.zip'): model.unlink()
    if args.audit_smoke_training:
        import yaml
        filename=runtime_root/'config/fl_config.yaml';configuration=yaml.safe_load(filename.read_text())
        configuration['training']['local_epochs']=1
        configuration['federation'].update(min_clients=5,fraction_fit=1.0)
        filename.write_text(yaml.safe_dump(configuration,sort_keys=False))
    bootstrap(runtime_root/'config/fleet_registry.yaml',directory/'fleet_registry.yaml')
    credentials, auth_env = isolated_auth(os.environ)
    write_credentials(directory, credentials)
    env=os.environ.copy();env.update(auth_env);env.update({'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MODE':'simulation','FLARE_SWARM_ENABLED':'1','SDN_PORT':str(args.simulation_port),'SDN_HOST':'127.0.0.1',
        'FLARE_SIMULATION_URL':f'http://127.0.0.1:{args.simulation_port}','FLARE_FLEET_REGISTRY':str(directory/'fleet_registry.yaml'),
        'DATABASE_PATH':str(directory/'decisions.db'),'FL_SERVER_ADDRESS':f'127.0.0.1:{args.flower_port}',
        'FLARE_FL_POOL_PLAN':str(directory/'pool.json'),'FLARE_CLIENT_MANAGER_STATUS':str(directory/'fl_clients.json'),
        'FLARE_CLIENT_STATE_DIR':str(directory/'client_state'),'FLARE_PERSONALIZED_DIR':str(directory/'personalized'),
        'FLARE_FL_CANDIDATE':str(directory/'candidate.pth'),'FLARE_FL_METRICS_JSON':str(directory/'fl_metrics.json'),
        'FLARE_FL_METRICS_CSV':str(directory/'fl_metrics.csv'),'FLARE_FL_TRUST_STATE':str(directory/'trust.json'),
        'FLARE_BYZANTINE_STATE_PATH':str(directory/'byzantine.json'),'VITE_API_BASE_URL':f'http://127.0.0.1:{args.api_port}','AJ_CORS_ORIGINS':f'http://127.0.0.1:{args.frontend_port},http://localhost:{args.frontend_port}'})
    processes=[];owned=[]
    def launch(name,command,working_directory=None):
        log=(directory/(name+'.log')).open('w');p=subprocess.Popen(command,cwd=working_directory or runtime_root,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);log.close()
        processes.append(p);owned.append({'name':name,'pid':p.pid,'command':command})
        (directory/'owned-processes.json').write_text(json.dumps({'supervisor':os.getpid(),'children':owned},indent=2))
        return p
    def stop(*_):
        for p in reversed(processes):
            if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        launch('simulation',[sys.executable,'-m','uvicorn','sdn.mock_sdn:app','--port',str(args.simulation_port)])
        import requests
        for _ in range(100):
            try:
                if requests.get(f'http://127.0.0.1:{args.simulation_port}/simulation/state',headers={'Authorization':'Bearer '+env['AJ_SDN_TOKEN']},timeout=1).json().get('schema_version')=='mission_snapshot_v1':break
            except requests.RequestException:pass
            time.sleep(.2)
        else:raise RuntimeError('Simulator startup failed')
        launch('api',[sys.executable,'-m','uvicorn','api.server:app','--port',str(args.api_port)])
        launch('orchestrator',[sys.executable,'-m','orchestrator.loop'])
        # Playwright watches the frontend URL. Expose that listener only after
        # API startup (including models) has completed, preventing a first-login
        # race against uvicorn's still-unbound API socket.
        for _ in range(200):
            if any(p.poll() is not None for p in processes):raise RuntimeError('An owned service exited; inspect '+str(directory))
            try:
                if requests.get(f'http://127.0.0.1:{args.api_port}/health',timeout=1).ok:break
            except requests.RequestException:pass
            time.sleep(.2)
        else:raise RuntimeError('API readiness failed')
        # A healthy HTTP listener alone does not prove browser login and the
        # simulator proxy are ready. Exercise both with this run's credentials.
        response=requests.post(f'http://127.0.0.1:{args.api_port}/auth/token',
            data={'username':credentials['username'],'password':credentials['password']},timeout=10)
        response.raise_for_status()
        browser_headers={'Authorization':'Bearer '+response.json()['access_token']}
        for _ in range(100):
            if any(p.poll() is not None for p in processes):raise RuntimeError('An owned service exited; inspect '+str(directory))
            try:
                response=requests.get(f'http://127.0.0.1:{args.api_port}/mission/simulation',headers=browser_headers,timeout=3)
                if response.ok and response.json().get('schema_version')=='mission_snapshot_v1':break
            except (requests.RequestException,ValueError):pass
            time.sleep(.2)
        else:raise RuntimeError('Authenticated mission readiness failed')
        frontend_command=['npm','--prefix','frontend-react','run','dev','--','--host','127.0.0.1','--port',str(args.frontend_port),'--strictPort']
        if args.production:
            output=directory/'frontend-dist'
            with (directory/'frontend-build.log').open('w') as log:
                subprocess.run(['npm','--prefix','frontend-react','run','build','--','--outDir',str(output),'--emptyOutDir'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            frontend_command=['npm','--prefix','frontend-react','run','preview','--','--host','127.0.0.1','--port',str(args.frontend_port),'--strictPort','--outDir',str(output)]
        (directory/'frontend-mode.json').write_text(json.dumps({'mode':'production' if args.production else 'development','api_url':env['VITE_API_BASE_URL'],'command':frontend_command},indent=2))
        launch('frontend',frontend_command,root)
        if args.flower_rounds:
            launch('flower',[sys.executable,'fl/server.py','--rounds',str(args.flower_rounds)])
            launch('clients',[sys.executable,'-m','fl.client_manager','--server-address',f'127.0.0.1:{args.flower_port}','--reconcile-interval','.2'])
        for _ in range(200):
            if any(p.poll() is not None for p in processes[:4]):raise RuntimeError('An owned service exited; inspect '+str(directory))
            try:
                if requests.get(f'http://127.0.0.1:{args.api_port}/health',timeout=1).ok and requests.get(f'http://127.0.0.1:{args.frontend_port}',timeout=1).ok:break
            except requests.RequestException:pass
            time.sleep(.2)
        else:raise RuntimeError('API/frontend readiness failed')
        print(f'Isolated services: http://127.0.0.1:{args.frontend_port}; artifacts '+str(directory),flush=True)
        while all(p.poll() is None for p in processes[:4]):time.sleep(1)
    finally:
        stop()
        for p in processes:
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)

if __name__=='__main__':main()
