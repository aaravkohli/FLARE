"""Read-only authenticated WebSocket ordering and reconnect validation."""
import asyncio
import json
import os
from pathlib import Path
import sys
import requests
import websockets


async def main():
    api=os.getenv('FLARE_VALIDATION_API','http://127.0.0.1:18000')
    response=requests.post(api+'/auth/token',data={'username':os.getenv('AJ_ADMIN_USERNAME','admin'),'password':os.getenv('AJ_ADMIN_PASSWORD','antijam2026')},timeout=5)
    response.raise_for_status();token=response.json()['access_token']
    url=api.replace('http','ws',1)+'/ws'
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({'type':'auth','token':'invalid'}))
        try:await asyncio.wait_for(socket.recv(),5)
        except websockets.ConnectionClosed as exc:assert exc.code==4401
        else:raise AssertionError('Unauthenticated stream was accepted')
    frames=[]
    async def read(socket):
        while True:
            message=json.loads(await asyncio.wait_for(socket.recv(),10))
            if message.get('type')=='mission_snapshot':return message['data']
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({'type':'auth','token':token}))
        assert json.loads(await socket.recv())=={'type':'auth','status':'ok'}
        for _ in range(5):frames.append(await read(socket))
    assert len({f['session_id'] for f in frames})==1
    assert [f['simulation_time'] for f in frames]==sorted(f['simulation_time'] for f in frames)
    cursors=[e['cursor'] for f in frames for e in f['events']]
    assert cursors==sorted(set(cursors))
    assert all(len(f['events'])<=200 and len(f['packets'])<=200 for f in frames)
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({'type':'auth','token':token}))
        assert json.loads(await socket.recv())['status']=='ok'
        current=await read(socket)
    assert current['session_id']==frames[-1]['session_id'] and current['simulation_time']>=frames[-1]['simulation_time']
    report={'passed':True,'evidence':'controlled_simulation','unauthorized_close':4401,'frames':len(frames),'events':len(cursors),'ordered':True,'reconnect_resynchronized':True,'session_id':current['session_id'],'node_count':len(current['nodes']),'command':[sys.executable,'-m','scripts.swarm_socket_validation']}
    Path('/private/tmp/swarm-socket-validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

if __name__=='__main__':asyncio.run(main())
