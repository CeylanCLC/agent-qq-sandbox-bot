#!/usr/bin/env python3
"""One-shot authenticated heartbeat; no chat text leaves the robot host."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from glob import glob
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
import websockets


def state(args):
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=5)
        value=r.stdout.strip()
        if value in ('active','true'):return 'online'
        if value in ('inactive','failed','false'):return 'offline'
    except (OSError,subprocess.TimeoutExpired):pass
    return 'unknown'


def config():
    path=Path(os.getenv('CEYLAN_OPENCLAW_CONFIG','/home/ubuntu/.openclaw/openclaw.json'))
    try:
        obj=json.loads(path.read_text())
        model=obj.get('agents',{}).get('defaults',{}).get('model',{})
        if isinstance(model,dict):model=model.get('primary')
        return obj.get('gateway',{}).get('port',31075),model
    except (OSError,ValueError,AttributeError):return 31075,None


async def onebot():
    url=os.getenv('ONEBOT_WS','ws://127.0.0.1:3001')
    parsed=urlparse(url)
    if parsed.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('OneBot must be local')
    token=os.getenv('ONEBOT_ACCESS_TOKEN','')
    if not token:
        try:
            configured=os.getenv('CEYLAN_ONEBOT_CONFIG','').strip()
            candidates=[configured] if configured else sorted(glob('/root/openclaw-qq/napcat/config/onebot11_*.json'))
            cfg=json.loads(Path(candidates[0]).read_text()) if candidates else {}
            for s in cfg.get('network',{}).get('websocketServers',[]):
                if s.get('enable') and int(s.get('port',0))==(parsed.port or 80):
                    token=s.get('token','');break
        except (OSError,ValueError,TypeError):pass
    opts={'open_timeout':4,'close_timeout':1,'max_size':1024*1024}
    if token:
        name='additional_headers' if 'additional_headers' in inspect.signature(websockets.connect).parameters else 'extra_headers'
        opts[name]={'Authorization':'Bearer '+token}
    data={}
    async with websockets.connect(url,**opts) as ws:
        for action in ('get_status','get_login_info','get_group_list'):
            await ws.send(json.dumps({'action':action,'params':{},'echo':'ceylan-heartbeat-'+action}))
        remaining={'get_status','get_login_info','get_group_list'}
        async with asyncio.timeout(6):
            while remaining:
                event=json.loads(await ws.recv())
                echo=event.get('echo')
                if not isinstance(echo,str) or not echo.startswith('ceylan-heartbeat-'):continue
                action=echo.removeprefix('ceylan-heartbeat-')
                if action not in remaining:continue
                remaining.remove(action)
                if event.get('status')!='ok' or event.get('retcode')!=0:continue
                value=event.get('data')
                if action=='get_status' and isinstance(value,dict) and type(value.get('online')) is bool:data['online']=value['online']
                if action=='get_login_info' and isinstance(value,dict) and value.get('user_id'):data['qq']=str(value['user_id'])
                if action=='get_group_list' and isinstance(value,list):data['groups']=len(value)
    return data


def collect():
    port,model=config()
    data={'napcat':state(['docker','inspect','--format','{{.State.Running}}',os.getenv('NAPCAT_CONTAINER','napcat')]),
          'bridge':state(['systemctl','is-active','qq-openclaw-bridge.service'])}
    if isinstance(model,str):data['model']=model[:300]
    try:
        with socket.create_connection(('127.0.0.1',int(port)),timeout=2):data['openclaw']='online'
    except (OSError,ValueError):data['openclaw']='offline'
    try:data.update(asyncio.run(onebot()))
    except Exception:pass  # No result means unknown, never a fabricated online=true.
    try:
        p=Path(os.getenv('CEYLAN_BOT_SNAPSHOT','/root/openclaw-qq/runtime-status.json'))
        if data['bridge']=='online' and 0<=time.time()-p.stat().st_mtime<=60:
            snapshot=json.loads(p.read_text())
            for k in ('memory','memory_count','patrol','last_message','last_patrol'):
                if snapshot.get(k) is not None:data[k]=snapshot[k]
    except (OSError,ValueError,AttributeError):pass
    try:
        mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
        data['memory_percent']=round(100*(1-mem['MemAvailable']/mem['MemTotal']),1)
        data['uptime']=int(float(Path('/proc/uptime').read_text().split()[0]))
        def ticks():
            v=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]));return sum(v),v[3]+v[4]
        a,ai=ticks();time.sleep(.2);b,bi=ticks()
        data['cpu']=round(max(0,min(100,100*(1-(bi-ai)/max(1,b-a)))),1)
    except (OSError,ValueError,KeyError):pass
    return data


def main():
    from project_config import api_url
    endpoint=api_url('/api/bot/status','CEYLAN_HEARTBEAT_URL')
    token=os.environ['CEYLAN_BOT_TOKEN']
    parsed=urlparse(endpoint)
    if parsed.scheme!='https' or parsed.username or parsed.password or len(token)<32:raise ValueError('Invalid endpoint/token')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    req=Request(endpoint,data=json.dumps(collect(),allow_nan=False).encode(),headers={'User-Agent':'QQOpenClawHeartbeat/1.0','Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with build_opener(NoRedirect).open(req,timeout=12) as response:
        if response.status!=200 or json.loads(response.read(4096)).get('ok') is not True:raise RuntimeError('Not acknowledged')
    print('Heartbeat delivered')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Heartbeat failed: '+type(exc).__name__)
        raise SystemExit(1)
