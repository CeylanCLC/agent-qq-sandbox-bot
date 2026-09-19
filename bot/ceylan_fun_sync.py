#!/usr/bin/env python3
"""One-shot V6 state sync. The robot remains outbound-only."""
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ceylan_fun_core import validate_command
from project_config import api_url

ROOT = Path(os.getenv('CEYLAN_FUN_DIR', '/root/openclaw-qq/fun-v6'))


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.fun-command-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o600); os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main():
    token = os.environ['CEYLAN_BOT_TOKEN']
    state_path = ROOT/'state.json'
    if not state_path.is_file() or state_path.stat().st_size > 300000:
        raise RuntimeError('互动快照尚未生成或过大')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    endpoint = api_url('/api/bot/fun/sync', 'CEYLAN_FUN_URL')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    request = Request(endpoint, data=json.dumps({'state': state}, ensure_ascii=False, allow_nan=False).encode(),
                      headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json',
                               'User-Agent': 'QQOpenClawFun/1.0'}, method='POST')
    with build_opener(NoRedirect).open(request, timeout=15) as response:
        body = json.loads(response.read(262144))
        if response.status != 200 or body.get('ok') is not True: raise RuntimeError('未收到网站确认')
    now = time.time()
    for raw in body.get('commands', [])[:3]:
        command = validate_command(raw)
        if not now <= raw.get('expires', 0) <= now + 900: continue
        command['expires'] = raw['expires']
        atomic(ROOT/'inbox'/(command['id']+'.json'), command)
    print('Fun sync delivered')


if __name__ == '__main__':
    try: main()
    except HTTPError as exc:
        print('Fun sync failed: HTTP '+str(exc.code)); raise SystemExit(1)
    except Exception as exc:
        print('Fun sync failed: '+type(exc).__name__); raise SystemExit(1)
