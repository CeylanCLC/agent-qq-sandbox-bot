#!/usr/bin/env python3
"""Expanded read-only telemetry; preserves the working basic heartbeat."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import time
from glob import glob
from urllib.parse import urlparse
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
import websockets
import heartbeat
from project_config import BLOCKED_GROUP_IDS

def command(args):
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=3)
        return r.stdout.strip() if r.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None

def fields(obj,keys):
    if not isinstance(obj,dict):return {}
    return {k:obj[k][:160] if isinstance(obj[k],str) else obj[k] for k in keys if k in obj and type(obj[k]) in (str,int,float,bool,type(None))}

async def napcat():
    url=os.getenv('ONEBOT_WS','ws://127.0.0.1:3001');parsed=urlparse(url)
    if parsed.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('local OneBot only')
    token=os.getenv('ONEBOT_ACCESS_TOKEN','')
    if not token:
        try:
            configured=os.getenv('CEYLAN_ONEBOT_CONFIG','').strip()
            candidates=[configured] if configured else sorted(glob('/root/openclaw-qq/napcat/config/onebot11_*.json'))
            cfg=json.loads(Path(candidates[0]).read_text()) if candidates else {}
            for s in cfg.get('network',{}).get('websocketServers',[]):
                if s.get('enable') and int(s.get('port',0))==(parsed.port or 80):token=s.get('token','');break
        except (OSError,ValueError,TypeError):pass
    opts={'open_timeout':4,'close_timeout':1,'max_size':4*1024*1024}
    if token:opts['additional_headers' if 'additional_headers' in inspect.signature(websockets.connect).parameters else 'extra_headers']={'Authorization':'Bearer '+token}
    actions={'get_login_info','get_status','get_version_info','get_group_list','get_friend_list','can_send_image','can_send_record'}
    result={'api':{},'groups':None,'friends':None}
    async with websockets.connect(url,**opts) as ws:
        pending=set(actions)
        for a in sorted(actions):await ws.send(json.dumps({'action':a,'params':{},'echo':'monitor:'+a}))
        try:
            async with asyncio.timeout(7):
                while pending:
                    e=json.loads(await ws.recv());echo=e.get('echo') if isinstance(e,dict) else None
                    if not isinstance(echo,str) or not echo.startswith('monitor:'):continue
                    a=echo[8:]
                    if a not in pending:continue
                    pending.remove(a)
                    if e.get('status')!='ok' or e.get('retcode')!=0:
                        result['api'][a]='unsupported_or_failed';continue
                    d=e.get('data');result['api'][a]='ok'
                    if a=='get_login_info':result['account']=fields(d,['user_id','nickname'])
                    elif a=='get_status':result['status']=fields(d,['online','good'])
                    elif a=='get_version_info':result['version']=fields(d,['app_name','app_version','protocol_version'])
                    elif a=='get_group_list' and isinstance(d,list):
                        visible=[x for x in d if str(x.get('group_id','')) not in BLOCKED_GROUP_IDS]
                        result['group_total']=len(visible);result['groups']=[fields(x,['group_id','group_name','member_count','max_member_count']) for x in visible[:300]]
                    elif a=='get_friend_list' and isinstance(d,list):
                        result['friend_total']=len(d);result['friends']=[fields(x,['user_id','nickname','remark']) for x in d[:500]]
                    elif a.startswith('can_send_'):result[a]=fields(d,['yes']).get('yes')
        except TimeoutError:pass
        for a in pending:result['api'][a]='timeout'
    return result

def collect():
    d={'sampled':time.time(),'napcat':{},'node':{},'services':{},'runtime':None}
    try:d['napcat']=asyncio.run(napcat())
    except Exception as e:d['napcat']={'error':type(e).__name__,'groups':None,'friends':None}
    for service in ('qq-openclaw-bridge','ceylan-heartbeat'):
        raw=command(['systemctl','show',service,'--property=ActiveState,SubState,NRestarts,ExecMainStatus,ActiveEnterTimestamp'])
        d['services'][service]=dict(line.split('=',1) for line in raw.splitlines() if '=' in line) if raw else {'ActiveState':'unknown'}
    docker=command(['docker','inspect','--format','{{json .State}}',os.getenv('NAPCAT_CONTAINER','napcat')])
    try:d['services']['napcat']=fields(json.loads(docker),['Status','Running','Restarting','OOMKilled','ExitCode','StartedAt'])
    except (TypeError,ValueError):d['services']['napcat']={'Status':'unknown'}
    port,model=heartbeat.config();d['model']=model[:300] if isinstance(model,str) else None
    start=time.monotonic()
    try:
        with socket.create_connection(('127.0.0.1',int(port)),timeout=2):pass
        d['gateway']={'reachable':True,'tcp_ms':round((time.monotonic()-start)*1000,2)}
    except (OSError,ValueError):d['gateway']={'reachable':False}
    try:
        mem={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()}
        disk=shutil.disk_usage('/');net=[l.split() for l in Path('/proc/net/dev').read_text().splitlines()[2:] if not l.strip().startswith('lo:')]
        d['node']={'os':platform.system()+' '+platform.release(),'cores':os.cpu_count(),'load':list(os.getloadavg()),
            'memory_total':mem['MemTotal'],'memory_used':mem['MemTotal']-mem['MemAvailable'],
            'swap_total':mem['SwapTotal'],'swap_used':mem['SwapTotal']-mem['SwapFree'],
            'disk_total':disk.total,'disk_used':disk.used,'uptime':float(Path('/proc/uptime').read_text().split()[0]),
            'rx':sum(int(x[1]) for x in net),'tx':sum(int(x[9]) for x in net)}
    except (OSError,ValueError,KeyError):pass
    p=Path(os.getenv('CEYLAN_OBSERVER_FILE','/root/openclaw-qq/observer-status.json'))
    try:
        d['runtime_age']=round(time.time()-p.stat().st_mtime,1)
        if p.stat().st_size<=200000 and 0<=d['runtime_age']<=90 and d['services']['qq-openclaw-bridge'].get('ActiveState')=='active':d['runtime']=json.loads(p.read_text())
    except (OSError,ValueError):pass
    return d

def main():
    from project_config import api_url
    token=os.environ['CEYLAN_BOT_TOKEN'];endpoint=api_url('/api/bot/telemetry','CEYLAN_TELEMETRY_URL')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):return None
    req=Request(endpoint,data=json.dumps(collect(),allow_nan=False).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'QQOpenClawMonitor/1.0'},method='POST')
    with build_opener(NoRedirect).open(req,timeout=12) as r:
        if r.status!=200 or json.loads(r.read(4096)).get('ok') is not True:raise RuntimeError('not acknowledged')
    print('Detailed telemetry delivered')

if __name__=='__main__':
    try:main()
    except HTTPError as e:
        print('Detailed telemetry failed: HTTP '+str(e.code));raise SystemExit(1)
    except Exception as e:
        print('Detailed telemetry failed: '+type(e).__name__);raise SystemExit(1)
