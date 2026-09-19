"""Bounded encrypted credential commands; no user supplied file paths or URLs."""
import base64
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('请求格式错误')
    keys = {'id', 'action', 'target', 'revision', 'ciphertext'}
    if set(data) - keys:
        raise ValueError('不支持的字段；禁止提交明文密钥')
    if not re.fullmatch(r'[a-f0-9]{32}', str(data.get('id', ''))):
        raise ValueError('操作编号无效')
    if data.get('action') not in ('test', 'replace', 'rollback'):
        raise ValueError('操作不支持')
    if data.get('target') not in ('vision', 'openclaw'):
        raise ValueError('模型目标不支持')
    if not re.fullmatch(r'[a-f0-9]{64}', str(data.get('revision', ''))):
        raise ValueError('配置版本无效，请刷新')
    cipher = data.get('ciphertext', '')
    if data['action'] == 'rollback':
        if cipher:
            raise ValueError('回滚不需要密钥')
    else:
        try:
            if not isinstance(cipher, str) or len(base64.b64decode(cipher, validate=True)) != 512:
                raise ValueError()
        except Exception:
            raise ValueError('需要浏览器加密后的密钥') from None
    return {k: data[k] for k in keys if k in data}


def label(command):
    return ':'.join(command[k] for k in ('id', 'action', 'target', 'revision')).encode()
