"""Shared bounded command contract. No arbitrary shell, path, or Python execution."""
import hashlib
import json

DEFAULTS={'reply_tokens':240,'reply_chars':210,'bubbles':3,'delay_ms':700,'patrol_enabled':True,'memory_enabled':True,'style_note':''}
RANGES={'reply_tokens':(80,800),'reply_chars':(60,600),'bubbles':(1,3),'delay_ms':(200,2000),
 'ACTIVE_GROUP_CONTEXT_LIMIT':(3,40),'PERIODIC_REVIEW_INTERVAL_SECONDS':(60,86400),
 'GROUP_MEMORY_COMPACT_INTERVAL_SECONDS':(600,86400),'GROUP_MEMORY_MIN_NEW_MESSAGES':(10,500),
 'GROUP_MEMORY_CONTEXT_MAX_CHARS':(200,3000)}

def revision(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def validate(data):
    if not isinstance(data,dict):raise ValueError('请求格式错误')
    action=data.get('action');rev=data.get('revision')
    if action not in ('settings','memory_set','cache_clear','cache_edit'):raise ValueError('不支持的操作')
    if not isinstance(rev,str) or len(rev)!=64 or any(x not in '0123456789abcdef' for x in rev):raise ValueError('缺少有效版本号，请重新加载')
    result={'action':action,'revision':rev}
    if action=='settings':
        values=data.get('values')
        if not isinstance(values,dict) or not values:raise ValueError('参数为空')
        for k,v in values.items():
            if k=='style_note':
                if not isinstance(v,str) or len(v)>500:raise ValueError('语气偏好最多 500 字')
            elif k in ('patrol_enabled','memory_enabled'):
                if type(v) is not bool:raise ValueError('开关应为布尔值')
            elif k not in RANGES or type(v) is not int or not RANGES[k][0]<=v<=RANGES[k][1]:raise ValueError('参数超出范围：'+str(k))
        result['values']=values
    else:
        gid=str(data.get('group',''))
        if not gid.isdigit() or len(gid)>20:raise ValueError('无效群号')
        result['group']=gid
        if action in ('memory_set','cache_edit'):
            text=data.get('text');limit=20000 if action=='memory_set' else 300
            if not isinstance(text,str) or len(text)>limit:raise ValueError('内容超过长度上限')
            result['text']=text
        if action=='cache_edit':
            seq=data.get('seq')
            if type(seq) is not int or seq<0:raise ValueError('缓存序号错误')
            result['seq']=seq
    return result
