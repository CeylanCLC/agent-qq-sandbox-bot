"""Persistent, content-free activity stats and bounded playful controls."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from ceylan_fun_core import DEFAULTS, revision, validate_command, validate_settings

ROOT = Path(os.getenv('CEYLAN_FUN_DIR', '/root/openclaw-qq/fun-v6'))
SETTINGS = dict(DEFAULTS)
LEDGER = {}
STARTED = time.time()
CST = timezone(timedelta(hours=8))
MODE_NOTES = {
    'balanced': '',
    'friendly': '语气像熟悉的群友，温和自然，可以带一点轻松感，但不要硬凑梗。',
    'playful': '语气轻快机灵，可偶尔用一个贴合语境的小玩笑；不要油腻，不要连续玩梗。',
    'focused': '优先给出准确结论和下一步，少寒暄，避免无关延伸。',
}
STATE = {'version': 1, 'first_seen': time.time(), 'totals': {}, 'days': {}, 'groups': {}}


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.fun-v6-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def day_key(now=None):
    return datetime.fromtimestamp(now or time.time(), CST).date().isoformat()


def bucket():
    key = day_key()
    return STATE['days'].setdefault(key, {})


def add(key, value=1, group=None):
    totals = STATE.setdefault('totals', {})
    totals[key] = int(totals.get(key, 0)) + value
    today = bucket(); today[key] = int(today.get(key, 0)) + value
    if group:
        gid = str(group)
        groups = STATE.setdefault('groups', {})
        if gid not in groups and len(groups) >= 500:
            victim = min(groups, key=lambda x: groups[x].get('last_active', 0))
            groups.pop(victim, None)
        row = groups.setdefault(gid, {})
        row[key] = int(row.get(key, 0)) + value
        row['last_active'] = time.time()


def quiet_active(now=None):
    if not SETTINGS['quiet_enabled']:
        return False
    hour = datetime.fromtimestamp(now or time.time(), CST).hour
    start, end = SETTINGS['quiet_start'], SETTINGS['quiet_end']
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def scope():
    try:
        from ceylan_control_runtime import SCOPE
        return SCOPE.get()
    except (ImportError, AttributeError):
        return 'other'


def trim_state():
    cutoff = (datetime.now(CST).date() - timedelta(days=30)).isoformat()
    STATE['days'] = {k: v for k, v in STATE.get('days', {}).items() if k >= cutoff}


def achievements():
    total, today = STATE.get('totals', {}), bucket()
    definitions = [
        ('hello', '第一次搭话', '完成第一条回复', total.get('sent_replies', 0) >= 1),
        ('crowd', '群聊常驻', '累计收到 500 条群消息', total.get('group_messages', 0) >= 500),
        ('vision', '像素侦探', '遇到 50 条含图消息', total.get('images', 0) >= 50),
        ('streak', '今日高能', '单日处理 100 条消息', today.get('messages', 0) >= 100),
        ('steady', '稳定输出', '模型成功调用 1000 次', total.get('model_ok', 0) >= 1000),
        ('egg', '彩蛋猎人', '触发本地彩蛋 10 次', total.get('easter_hits', 0) >= 10),
    ]
    return [{'id': i, 'name': n, 'description': d, 'unlocked': bool(ok)} for i, n, d, ok in definitions]


def mood():
    today = bucket(); calls = today.get('model_calls', 0); errors = today.get('model_errors', 0)
    if quiet_active(): return {'key': 'night', 'name': '夜航待机', 'note': '安静时段，巡群可自动节流'}
    if calls >= 5 and errors / calls >= .2: return {'key': 'cloudy', 'name': '云层抖动', 'note': '今天模型异常比例偏高'}
    messages = today.get('messages', 0)
    if messages >= 200: return {'key': 'party', 'name': '群聊沸腾', 'note': '今天互动非常热闹'}
    if messages >= 50: return {'key': 'spark', 'name': '灵感在线', 'note': '今天状态不错，稳定营业'}
    if messages: return {'key': 'cruise', 'name': '悠闲巡航', 'note': '有问就答，保持轻盈'}
    return {'key': 'sleep', 'name': '充电摸鱼', 'note': '今天还没有收到新消息'}


def title():
    names = ('赛博夜猫', '群聊修理匠', '像素侦探', '低耗能搭子', '云端气氛组', '记忆巡航员', '问题终结者')
    today = bucket()
    seed = int(day_key().replace('-', '')) + today.get('messages', 0) // 20 + today.get('model_ok', 0) // 10
    return names[seed % len(names)]


def save():
    trim_state(); atomic(ROOT/'activity.json', STATE)


def load():
    global STATE
    try:
        value = json.loads((ROOT/'activity.json').read_text(encoding='utf-8'))
        if isinstance(value, dict) and value.get('version') == 1:
            STATE = value
    except (OSError, ValueError, TypeError):
        pass


def load_settings():
    try:
        value = {**DEFAULTS, **json.loads((ROOT/'settings.json').read_text(encoding='utf-8'))}
        SETTINGS.update(validate_settings(value))
    except FileNotFoundError:
        pass
    except (ValueError, TypeError):
        print('[互动实验室] 设置文件无效，使用安全默认值', flush=True)


def process_commands():
    inbox = ROOT/'inbox'; inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in sorted(inbox.glob('*.json'))[:5]:
        result = {'time': time.time()}
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            if type(raw.get('expires')) not in (int,float) or time.time() > raw['expires']:
                raise ValueError('操作已过期，未执行')
            command = validate_command(raw)
            ident = command['id']; result['id'] = ident
            if ident in LEDGER:
                path.unlink(missing_ok=True); continue
            if command['revision'] != revision(SETTINGS):
                raise ValueError('设置已变化，请刷新后重试')
            backup = ROOT/'backups'/ident; backup.mkdir(parents=True, exist_ok=True, mode=0o700)
            if (ROOT/'settings.json').exists(): shutil.copy2(ROOT/'settings.json', backup/'settings.json')
            atomic(ROOT/'settings.json', command['settings']); SETTINGS.update(command['settings'])
            result.update(status='done', message='互动设置已生效', backup=str(backup))
        except ValueError as exc:
            result.update(status='failed', message=str(exc))
            result.setdefault('id', path.stem)
        except Exception as exc:
            result.update(status='failed', message='执行失败：'+type(exc).__name__)
            result.setdefault('id', path.stem)
        LEDGER[result['id']] = result
        while len(LEDGER) > 100: LEDGER.pop(next(iter(LEDGER)))
        atomic(ROOT/'ledger.json', LEDGER)
        path.unlink(missing_ok=True)
    backups = ROOT/'backups'
    if backups.exists():
        for old in sorted(backups.iterdir(), key=lambda p: p.stat().st_mtime)[:-20]: shutil.rmtree(old)


def snapshot(runtime):
    process_commands(); save()
    days = [{'date': key, **value} for key, value in sorted(STATE.get('days', {}).items())[-14:]]
    groups = []
    for gid, row in STATE.get('groups', {}).items():
        if runtime.blocked(gid): continue
        groups.append({'id': gid, **{k: row.get(k, 0) for k in ('group_messages', 'images', 'sent_replies')},
                       'last_active': row.get('last_active')})
    groups.sort(key=lambda x: (x['group_messages'], x['last_active'] or 0), reverse=True)
    data = {'sampled': time.time(), 'started': STARTED, 'settings': SETTINGS,
            'settings_revision': revision(SETTINGS), 'today': bucket(), 'totals': STATE.get('totals', {}),
            'days': days, 'groups': groups[:100], 'mood': mood(), 'title': title(),
            'quiet_active': quiet_active(), 'achievements': achievements(),
            'acks': list(LEDGER.values())[-50:]}
    atomic(ROOT/'state.json', data)


def install(ns, runtime):
    if ns.get('_fun_v6'): return
    ns['_fun_v6'] = True; ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    load(); load_settings()
    try: LEDGER.update(json.loads((ROOT/'ledger.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError): pass

    group_handler = ns['handle_group_message']
    @wraps(group_handler)
    async def handle_group(ws, event):
        gid = str(event.get('group_id', '')); uid = str(event.get('user_id', ''))
        if runtime.blocked(gid) or uid == str(event.get('self_id', '')) or ns['is_ignored_sender'](event):
            return await group_handler(ws, event)
        segments = event.get('message', [])
        images = sum(1 for x in segments if isinstance(x, dict) and x.get('type') == 'image') if isinstance(segments, list) else 0
        add('messages'); add('group_messages', group=gid)
        if images: add('images', images, gid)
        if SETTINGS['easter_enabled']:
            at_me, text, urls = ns['parse_message'](event)
            hit = next((x for x in SETTINGS['easter_eggs'] if x['trigger'].casefold() == text.strip().casefold()), None)
            if hit:
                ns['record_group_message'](event, text or hit['trigger'], image_urls=urls)
                add('easter_hits'); await ns['send_group_msg'](ws, gid, uid, hit['reply'], mention=at_me)
                return None
        return await group_handler(ws, event)
    ns['handle_group_message'] = handle_group

    private_handler = ns['handle_private_message']
    @wraps(private_handler)
    async def handle_private(ws, event):
        if str(event.get('user_id', '')) != str(event.get('self_id', '')):
            add('messages'); add('private_messages')
            segments = event.get('message', [])
            if isinstance(segments, list): add('images', sum(1 for x in segments if isinstance(x, dict) and x.get('type') == 'image'))
        return await private_handler(ws, event)
    ns['handle_private_message'] = handle_private

    for name in ('call_openclaw', 'call_mimo_vision'):
        fn = ns[name]
        def wrap_model(original):
            @wraps(original)
            async def call(messages, max_tokens=800, timeout=300):
                current = scope(); today = bucket()
                if current == 'patrol' and SETTINGS['quiet_patrol'] and quiet_active():
                    add('patrol_skipped_quiet'); return 'SILENCE'
                limit = SETTINGS['daily_patrol_model_limit']
                if current == 'patrol' and limit and today.get('patrol_model_calls', 0) >= limit:
                    add('patrol_skipped_limit'); return 'SILENCE'
                note = MODE_NOTES.get(SETTINGS['mode'], '')
                copied = [dict(x) for x in messages]
                if note and current in ('chat', 'patrol'):
                    if copied and copied[0].get('role') == 'system' and isinstance(copied[0].get('content'), str):
                        copied[0]['content'] += '\n互动模式补充：' + note
                    else: copied.insert(0, {'role': 'system', 'content': '互动模式补充：' + note})
                add('model_calls')
                if current == 'patrol': add('patrol_model_calls')
                try:
                    result = await original(copied, max_tokens=max_tokens, timeout=timeout)
                    add('model_ok'); return result
                except Exception:
                    add('model_errors'); raise
            return call
        ns[name] = wrap_model(fn)

    for name in ('send_group_msg', 'send_private_msg'):
        fn = ns[name]
        def wrap_send(original, group_send):
            @wraps(original)
            async def send(*args, **kwargs):
                bound = inspect.signature(original).bind(*args, **kwargs); bound.apply_defaults()
                text = str(bound.arguments.get('text', ''))
                gid = str(bound.arguments.get('group_id', '')) if group_send else None
                result = await original(*args, **kwargs)
                add('sent_replies', group=gid); add('sent_chars', len(text))
                return result
            return send
        ns[name] = wrap_send(fn, name == 'send_group_msg')

    old_snapshot = runtime.snapshot
    def extended_snapshot():
        old_snapshot()
        try: snapshot(runtime)
        except Exception as exc: print('[互动实验室快照] '+type(exc).__name__, flush=True)
    runtime.snapshot = extended_snapshot
    snapshot(runtime)
