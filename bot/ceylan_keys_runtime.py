"""Hot-load vision key at the next model call without restarting Bridge."""
from functools import wraps
import json
from pathlib import Path
import os
import tempfile
import time
from ceylan_keys_common import digest

ROOT = Path(os.getenv('CEYLAN_KEYS_DIR', '/root/openclaw-qq/keys-v5'))


def install(ns):
    if ns.get('_keys_v5'):
        return
    ns['_keys_v5'] = True
    original_key = ns.get('MIMO_VISION_API_KEY', '')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    baseline = digest({'MIMO_VISION_API_KEY': original_key,
                       'MIMO_VISION_BASE_URL': ns.get('MIMO_VISION_BASE_URL', ''),
                       'MIMO_VISION_MODEL': ns.get('MIMO_VISION_MODEL', 'mimo-v2.5'),
                       'USE_DIRECT_MIMO_VISION': '1' if ns.get('USE_DIRECT_MIMO_VISION', True) else '0'})
    fd, tmp = tempfile.mkstemp(dir=ROOT)
    try:
        with os.fdopen(fd, 'w') as stream: json.dump({'baseline': baseline, 'started': time.time()}, stream)
        os.replace(tmp, ROOT / 'vision-runtime.json')
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    original = ns['call_mimo_vision']
    @wraps(original)
    async def vision(*args, **kwargs):
        path = ROOT / 'vision.json'
        config = json.loads(path.read_text()) if path.exists() else {}
        ns['MIMO_VISION_API_KEY'] = config.get('key', original_key)
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=ROOT)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'id': config.get('id'), 'loaded_at': time.time(), 'source': '网站覆盖值' if config else 'model.env'}, stream)
            os.replace(tmp, ROOT / 'vision-loaded.json')
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        try:
            return await original(*args, **kwargs)
        except Exception:
            # Some providers echo request details in error bodies. Do not let
            # the original Bridge log a credential-bearing upstream exception.
            raise RuntimeError('视觉请求失败，请查看模型工作室状态') from None
    ns['call_mimo_vision'] = vision
