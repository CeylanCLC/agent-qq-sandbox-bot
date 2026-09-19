import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from ceylan_control_core import validate
from project_config import api_url

ROOT=Path(os.getenv('CEYLAN_CONTROL_DIR','/root/openclaw-qq/control-v4'))

def main():
    p=ROOT/'state.json';state=None
    if p.exists() and p.stat().st_size<=1000000:
        state=json.loads(p.read_text())
    token=os.environ['CEYLAN_BOT_TOKEN']
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):return None
    req=Request(api_url('/api/bot/control/sync','CEYLAN_CONTROL_URL'),data=json.dumps({'state':state},allow_nan=False,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'QQOpenClawControl/1.0'},method='POST')
    with build_opener(NoRedirect).open(req,timeout=15) as response:
        raw=response.read(262145)
        if len(raw)>262144:raise ValueError('response too large')
        data=json.loads(raw)
        if response.status!=200 or data.get('ok') is not True:raise ValueError('not acknowledged')
    inbox=ROOT/'inbox';inbox.mkdir(parents=True,exist_ok=True,mode=0o700)
    for command in data.get('commands',[])[:5]:
        validate(command)
        ident=command.get('id','')
        if not re.fullmatch('[a-f0-9]{32}',ident) or not time.time()<command['expires']<=time.time()+660:continue
        target=inbox/(ident+'.json')
        if target.exists():continue
        fd,tmp=tempfile.mkstemp(dir=inbox)
        try:
            with os.fdopen(fd,'w') as f:json.dump(command,f,ensure_ascii=False)
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    print('Control sync delivered')

if __name__=='__main__':
    try:main()
    except HTTPError as e:print('Control sync HTTP '+str(e.code));raise SystemExit(1)
    except Exception as e:print('Control sync failed: '+type(e).__name__);raise SystemExit(1)
