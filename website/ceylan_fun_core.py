"""Shared schema for CeylanCLC V6 interaction settings."""
import hashlib
import json

MODES = ('balanced', 'friendly', 'playful', 'focused')
DEFAULTS = {
    'mode': 'balanced',
    'quiet_enabled': False,
    'quiet_start': 0,
    'quiet_end': 7,
    'quiet_patrol': True,
    'daily_patrol_model_limit': 0,
    'daily_model_warning': 300,
    'easter_enabled': False,
    'easter_eggs': [],
}


def revision(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('设置字段不完整')
    result = dict(value)
    if result['mode'] not in MODES:
        raise ValueError('互动模式无效')
    for key in ('quiet_enabled', 'quiet_patrol', 'easter_enabled'):
        if type(result[key]) is not bool:
            raise ValueError(key + ' 必须是开关')
    for key in ('quiet_start', 'quiet_end'):
        if type(result[key]) is not int or not 0 <= result[key] <= 23:
            raise ValueError('安静时段必须是 0～23 点')
    if type(result['daily_patrol_model_limit']) is not int or not 0 <= result['daily_patrol_model_limit'] <= 500:
        raise ValueError('巡群模型调用上限必须是 0～500')
    if type(result['daily_model_warning']) is not int or not 10 <= result['daily_model_warning'] <= 10000:
        raise ValueError('模型调用提醒值必须是 10～10000')
    eggs = result['easter_eggs']
    if not isinstance(eggs, list) or len(eggs) > 12:
        raise ValueError('彩蛋最多 12 个')
    clean = []
    seen = set()
    for row in eggs:
        if not isinstance(row, dict) or set(row) != {'trigger', 'reply'}:
            raise ValueError('彩蛋格式错误')
        trigger = row['trigger'].strip() if isinstance(row['trigger'], str) else ''
        reply = row['reply'].strip() if isinstance(row['reply'], str) else ''
        if not 1 <= len(trigger) <= 30 or not 1 <= len(reply) <= 160:
            raise ValueError('彩蛋触发词 1～30 字，回复 1～160 字')
        folded = trigger.casefold()
        if folded in seen:
            raise ValueError('彩蛋触发词不能重复')
        seen.add(folded)
        clean.append({'trigger': trigger, 'reply': reply})
    result['easter_eggs'] = clean
    return result


def validate_command(value):
    if not isinstance(value, dict) or value.get('action') != 'settings':
        raise ValueError('只支持更新互动设置')
    if not isinstance(value.get('id'), str) or len(value['id']) != 32 or any(c not in '0123456789abcdef' for c in value['id']):
        raise ValueError('操作编号无效')
    rev = value.get('revision')
    if not isinstance(rev, str) or len(rev) != 64:
        raise ValueError('设置版本无效')
    return {'id': value['id'], 'action': 'settings', 'revision': rev,
            'settings': validate_settings(value.get('settings'))}
