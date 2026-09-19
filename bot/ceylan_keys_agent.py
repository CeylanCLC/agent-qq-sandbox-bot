"""Local credential manager. Only public metadata and ciphertext cross the website."""
import base64
import copy
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from ceylan_keys_common import digest, label, validate
from project_config import api_url

ROOT = Path(os.getenv('CEYLAN_KEYS_DIR', '/root/openclaw-qq/keys-v5'))
CONFIG = Path(os.getenv('CEYLAN_OPENCLAW_CONFIG', '/home/ubuntu/.openclaw/openclaw.json'))
ENV = Path(os.getenv('CEYLAN_MODEL_ENV', '/root/openclaw-qq/model.env'))


class Refused(ValueError):
    pass


def atomic(path, data):
    path = Path(path)
    if path.is_symlink():
        raise Refused('检测到符号链接，未写入')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = path.stat() if path.exists() else None
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.keys-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        if old:
            os.chown(temp, old.st_uid, old.st_gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, allow_nan=False).encode())


def read(path, default=None):
    if not path.exists():
        return default
    if path.is_symlink() or path.stat().st_size > 4000000:
        raise Refused('配置路径或大小不支持')
    return json.loads(path.read_text())


def private_key():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = ROOT / 'transport.pem'
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        atomic(path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def environment():
    result = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, val = line.partition('=')
        if not sep:
            raise Refused('model.env 格式无法安全识别')
        parts = shlex.split(val, comments=True)
        if len(parts) > 1:
            raise Refused('model.env 格式无法安全识别')
        result[key] = parts[0] if parts else ''
    return result


def endpoint(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise Refused('仅支持现有配置中的 HTTPS 供应商接口')
    return url.rstrip('/')


def active_config(proc_root=Path('/proc')):
    """Confirm the running Gateway's config path before touching a known file."""
    paths = set()
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ')
            if b'openclaw' not in cmd or b'gateway' not in cmd:
                continue
            env = dict(part.split(b'=', 1) for part in (proc/'environ').read_bytes().split(b'\0') if b'=' in part)
            home = env.get(b'HOME', pwd.getpwuid(proc.stat().st_uid).pw_dir.encode()).decode()
            state = env.get(b'OPENCLAW_STATE_DIR', (home+'/.openclaw').encode()).decode()
            path = env.get(b'OPENCLAW_CONFIG_PATH', (state+'/openclaw.json').encode()).decode()
            # Profile/CLI-path flags can override the conventional location.
            if b'--profile' in cmd or b'--config' in cmd:
                raise Refused('Gateway 使用独立启动参数，需先确认实际配置路径')
            paths.add(str(Path(path)))
        except (OSError, KeyError, UnicodeError):
            continue
    if paths != {str(CONFIG)}:
        raise Refused('未能唯一确认运行中 Gateway 的配置路径，暂不修改')


def target(name):
    """Return local secrets separately from the public, whitelisted descriptor."""
    if name == 'vision':
        env = environment()
        env['MIMO_VISION_BASE_URL'] = env.get('MIMO_VISION_BASE_URL', '').strip().rstrip('/')
        runtime = read(ROOT / 'vision-runtime.json', {})
        expected = digest({k: env.get(k, default) for k, default in
                           [('MIMO_VISION_API_KEY', ''), ('MIMO_VISION_BASE_URL', ''),
                            ('MIMO_VISION_MODEL', 'mimo-v2.5'), ('USE_DIRECT_MIMO_VISION', '1')]})
        if runtime.get('baseline') != expected:
            raise Refused('Bridge 运行配置与 model.env 不一致或 V5 钩子尚未加载')
        override = read(ROOT / 'vision.json', {})
        base = endpoint(env.get('MIMO_VISION_BASE_URL', ''))
        model = env.get('MIMO_VISION_MODEL', 'mimo-v2.5')
        key = override.get('key', env.get('MIMO_VISION_API_KEY', ''))
        generation = digest({'env': env, 'override': override})
        if env.get('USE_DIRECT_MIMO_VISION', '1') != '1':
            raise Refused('当前未启用视觉直连接口')
        return dict(name=name, base=base, model=model, key=key, revision=generation, change_id=override.get('id'),
                    paths=[ROOT / 'vision.json'], provider='视觉直连', source='网站覆盖值' if override else 'model.env')
    active_config()
    if CONFIG.is_symlink():
        raise Refused('OpenClaw 配置是符号链接，未开放修改')
    conf = read(CONFIG)
    if not isinstance(conf, dict) or '$include' in conf:
        raise Refused('OpenClaw 不是受支持的单文件 JSON 配置')
    agents = conf.get('agents', {})
    primary = agents.get('defaults', {}).get('model', {})
    primary = primary.get('primary') if isinstance(primary, dict) else primary
    if not isinstance(primary, str) or '/' not in primary:
        raise Refused('未识别到默认供应商/模型')
    provider, model = primary.split('/', 1)
    # A per-agent override makes the default model an ambiguous edit target.
    if any(a.get('model') or a.get('agentDir') for a in agents.get('list', []) + agents.get('entries', [])):
        raise Refused('存在独立 Agent 模型配置，需先确认实际聊天 Agent')
    entry = conf.get('models', {}).get('providers', {}).get(provider)
    if not isinstance(entry, dict) or entry.get('api', 'openai-completions') != 'openai-completions':
        raise Refused('当前供应商不是受支持的 OpenAI-compatible 配置')
    key = entry.get('apiKey')
    if not isinstance(key, str) or not key or '${' in key or key.startswith('env:'):
        raise Refused('密钥由环境变量或 SecretRef 管理，当前不直接覆盖')
    if entry.get('headers') or entry.get('authHeader') is False:
        raise Refused('供应商有独立认证头，需先确认认证方式')
    paths = [CONFIG]
    # An auth profile may outrank models.providers.apiKey. Never report a false replacement.
    auth = conf.get('auth', {})
    if any(p.get('provider') == provider for p in auth.get('profiles', {}).values()) or auth.get('order', {}).get(provider):
        raise Refused('存在 OpenClaw 独立认证档案，需核实实际密钥来源')
    for profile in CONFIG.parent.glob('agents/*/agent/auth-profiles.json'):
        obj = read(profile, {})
        if any(p.get('provider') == provider for p in obj.get('profiles', {}).values() if isinstance(p, dict)):
            raise Refused('供应商密钥由 auth-profiles 管理，未覆盖默认配置')
    for generated in CONFIG.parent.glob('agents/*/agent/models.json'):
        obj = read(generated, {})
        other = obj.get('providers', {}).get(provider)
        if other and 'apiKey' in other:
            if other['apiKey'] != key:
                raise Refused('Agent 密钥与主配置不一致，需先核实生效来源')
            paths.append(generated)
    contents = {str(p): read(p) for p in paths}
    return dict(name=name, base=endpoint(entry.get('baseUrl', '')), model=model, key=key,
                provider=provider, revision=digest(contents), paths=paths, source='OpenClaw provider.apiKey')


def probe(info, key):
    start = time.monotonic()
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.post(info['base'] + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'CeylanModelManager/5.0'},
                json={'model': info['model'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                      'max_tokens': 16, 'stream': False})
        if response.status_code != 200:
            raise Refused('供应商测试失败：HTTP ' + str(response.status_code) + '；原配置未修改')
        data = response.json()
        if not isinstance(data, dict) or not data.get('choices'):
            raise Refused('供应商返回格式异常；原配置未修改')
    except Refused:
        raise
    except Exception:
        raise Refused('供应商连接或响应异常；原配置未修改') from None
    return round((time.monotonic() - start) * 1000)


def status():
    pub = private_key().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    cards = []
    for name in ('vision', 'openclaw'):
        try:
            info = target(name)
            backup = read(ROOT / (name + '-last.json'), {})
            row = {k: info[k] for k in ('name', 'base', 'model', 'provider', 'revision', 'source')}
            row.update(editable=True, configured=bool(info['key']), reason='',
                       rollback=backup.get('after') == info['revision'])
            if name == 'vision':
                loaded = read(ROOT / 'vision-loaded.json', {})
                row['load_state'] = ('新覆盖值已被视觉请求读取' if loaded.get('id') == info.get('change_id') and loaded else
                                     '等待下一次视觉请求' if info.get('change_id') else '沿用 Bridge 环境配置')
            else:
                row['load_state'] = '配置文件状态；Gateway 是否采用新 Key 需实际聊天验证'
        except Exception as e:
            row = dict(name=name, editable=False, reason=str(e) if isinstance(e, Refused) else '配置结构暂不支持或读取失败', rollback=False)
        cards.append(row)
    return {'sampled': time.time(), 'public_key': pub, 'cards': cards,
            'vision_loaded': read(ROOT / 'vision-loaded.json', {}),
            'results': list(read(ROOT / 'ledger.json', {}).values())[-30:]}


def restore_values(backup):
    for item in backup['files']:
        path = Path(item['path'])
        if item['data'] is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, base64.b64decode(item['data']))


def execute(command):
    expires = command.get('expires', 0)
    command = validate({k: v for k, v in command.items() if k != 'expires'})
    if not time.time() < expires <= time.time() + 660:
        raise Refused('操作已过期')
    info = target(command['target'])
    if info['revision'] != command['revision']:
        raise Refused('配置已变化，请刷新后重试')
    latest = ROOT / (info['name'] + '-last.json')
    if command['action'] == 'rollback':
        backup = read(latest, {})
        if backup.get('after') != info['revision']:
            raise Refused('没有与当前配置匹配的回滚点')
        restore_values(backup)
        latest.unlink()
        return '已恢复上次替换前配置；等待运行端加载'
    try:
        key = private_key().decrypt(base64.b64decode(command['ciphertext']),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=label(command))).decode()
        if not 8 <= len(key) <= 256 or not all(33 <= ord(c) <= 126 for c in key):
            raise ValueError()
    except Exception:
        raise Refused('密钥解密或格式校验失败，请刷新页面重新输入') from None
    ms = probe(info, key)
    if command['action'] == 'test':
        return f'候选 Key 测试通过 · {ms} ms · 未修改配置'
    # Recheck after network I/O so a concurrent Tencent console edit is not overwritten.
    if target(info['name'])['revision'] != info['revision']:
        raise Refused('测试期间配置发生变化；原配置未修改')
    backup = {'files': [{'path': str(p), 'data': base64.b64encode(p.read_bytes()).decode() if p.exists() else None}
                        for p in info['paths']], 'created': time.time(), 'id': command['id']}
    save(ROOT / ('backup-' + command['id'] + '.json'), backup)
    try:
        if info['name'] == 'vision':
            save(ROOT / 'vision.json', {'key': key, 'id': command['id'], 'changed': time.time()})
        else:
            for path in info['paths']:
                obj = read(path)
                container = obj['models']['providers'] if path == CONFIG else obj['providers']
                container[info['provider']]['apiKey'] = key
                save(path, obj)
        backup['after'] = target(info['name'])['revision']
        save(latest, backup)
    except Exception:
        restore_values(backup)
        raise Refused('写入失败，已尝试恢复原配置；请检查后台状态') from None
    # Keep a small local recovery ring, never upload its content.
    backups = sorted(ROOT.glob('backup-*.json'), key=lambda p: p.stat().st_mtime)
    for path in backups[:-10]:
        path.unlink()
    return f'供应商测试通过 · {ms} ms；已保存，等待' + ('视觉调用加载' if info['name'] == 'vision' else 'OpenClaw 热加载（未验证 Gateway 已采用新 Key）')


def process(command):
    ledger = read(ROOT / 'ledger.json', {})
    ident = command.get('id', '')
    if not re.fullmatch('[a-f0-9]{32}', ident):
        return
    if ident in ledger:
        return
    # Persist intent first. A crash must not silently repeat a key mutation.
    ledger[ident] = dict(id=ident, action=command.get('action'), target=command.get('target'),
                         time=time.time(), status='unknown', message='执行中断或尚未完成，请查看配置状态；不会自动重复执行')
    save(ROOT / 'ledger.json', ledger)
    try:
        message = execute(command)
        ledger[ident].update(status='done', message=message)
    except Exception as e:
        ledger[ident].update(status='failed', message=str(e) if isinstance(e, Refused) else '执行失败；未返回任何密钥详情')
    ledger = dict(list(ledger.items())[-100:])
    save(ROOT / 'ledger.json', ledger)


def main():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROOT / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = os.environ['CEYLAN_BOT_TOKEN']
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            def sync():
                response = client.post(api_url('/api/bot/keys/sync', 'CEYLAN_KEYS_URL'), json=status(),
                    headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'QQOpenClawKeys/1.0'})
                if response.status_code != 200:
                    raise Refused('Keys sync HTTP ' + str(response.status_code))
                data = response.json()
                if data.get('ok') is not True:
                    raise Refused('Keys sync not acknowledged')
                return data.get('commands', [])[:1]
            for command in sync():
                process(command)
            sync()
    print('Keys sync delivered')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, Refused) else 'Keys sync failed: ' + type(exc).__name__)
        raise SystemExit(1)
