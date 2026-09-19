"""Apply bounded administrator commands on the Bridge event loop."""
import asyncio
from collections import Counter,deque
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from ceylan_control_core import DEFAULTS,RANGES,revision,validate

ROOT=Path(os.getenv('CEYLAN_CONTROL_DIR','/root/openclaw-qq/control-v4'))
SCOPE=ContextVar('reply_scope',default='other')
BUSY=Counter();OPTIONS=dict(DEFAULTS);LEDGER={};BATCH_LOCKS={}

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def chunks(text,options=None):
    opts=options or OPTIONS;text=str(text).strip()
    if not text:return []
    text=text[:opts['reply_chars']]
    # Every bubble contains one sentence/fragment; long sentences also have a bound.
    parts=[x.strip() for x in re.findall(r'[^。！？!?\n]+[。！？!?]?|[。！？!?]',text) if x.strip()]
    limit=max(25,(opts['reply_chars']+opts['bubbles']-1)//opts['bubbles'])
    pieces=[]
    for part in parts:
        pieces.extend(part[i:i+limit] for i in range(0,len(part),limit))
    return pieces[:opts['bubbles']]

def settings(ns):return {**OPTIONS,**{k:ns.get(k) for k in RANGES if k in ns}}
def backup(ns,action,ident):
    folder=ROOT/'backups'/ident;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    paths=[ROOT/'settings.json'] if action=='settings' else [Path(ns['GROUP_MEMORY_STATE_FILE'])] if action=='memory_set' else [Path(ns['PERIODIC_STATE_FILE'])]
    for p in paths:
        if p.is_file():shutil.copy2(p,folder/p.name)
    # Keep the exact pre-change in-memory state as well as its last disk copy.
    if action=='memory_set':atomic(folder/'live-memory.json',dict(ns['group_memory_summary']))
    if action.startswith('cache_'):atomic(folder/'live-cache.json',{k:list(v) for k,v in ns['group_message_cache'].items()})
    old=sorted((ROOT/'backups').iterdir(),key=lambda p:p.stat().st_mtime)
    for p in old[:-30]:shutil.rmtree(p)
    return str(folder)

def apply(ns,rt,command):
    data=validate(command);act=data['action'];gid=data.get('group')
    if gid and (rt.blocked(gid) or BUSY[gid]):raise ValueError('该群被屏蔽或正在处理消息/整理记忆，请稍后重试')
    if gid and gid not in ns['group_message_cache'] and gid not in ns['group_memory_summary']:raise ValueError('该群暂无运行状态，请先加载有效群')
    if act=='settings':
        if data['revision']!=revision(settings(ns)):raise ValueError('设置已变化，请重新加载')
    elif act=='memory_set':
        if data['revision']!=revision(ns['group_memory_summary'].get(gid,'')):raise ValueError('记忆已变化，请重新加载后编辑')
    elif act=='cache_clear':
        if data['revision']!=revision(list(ns['group_message_cache'].get(gid,[]))):raise ValueError('缓存已变化，请重新加载后确认')
    else:
        row=next((x for x in ns['group_message_cache'].get(gid,[]) if x.get('seq')==data['seq']),None)
        if row is None or revision(row)!=data['revision']:raise ValueError('缓存条目已变化或被淘汰')
    b=backup(ns,act,command['id'])
    if act=='settings':
        values={**settings(ns),**data['values']};atomic(ROOT/'settings.json',values)
        for k,v in data['values'].items():
            if k in ns:ns[k]=v
            else:OPTIONS[k]=v
    elif act=='memory_set':
        summaries=dict(ns['group_memory_summary']);summaries[gid]=data['text']
        seqs=dict(ns['group_memory_last_seq']);seqs[gid]=ns['group_message_seq'].get(gid,0)
        timestamps=dict(ns['group_memory_last_update_ts']);timestamps[gid]=time.time()
        atomic(Path(ns['GROUP_MEMORY_STATE_FILE']),{'group_memory_summary':summaries,'group_memory_last_seq':seqs,'group_memory_last_update_ts':timestamps})
        ns['group_memory_summary'][gid]=data['text'];ns['group_memory_last_seq'][gid]=seqs[gid];ns['group_memory_last_update_ts'][gid]=timestamps[gid]
    else:
        caches={k:list(v) for k,v in ns['group_message_cache'].items()}
        if act=='cache_clear':caches[gid]=[]
        else:caches[gid]=[{**x,'text':data['text']} if x.get('seq')==data['seq'] else x for x in caches[gid]]
        done=dict(ns['group_last_periodic_seq'])
        if act=='cache_clear':done[gid]=ns['group_message_seq'].get(gid,0)
        atomic(Path(ns['PERIODIC_STATE_FILE']),{'group_message_cache':caches,'group_message_seq':dict(ns['group_message_seq']),'group_last_periodic_seq':done,'periodic_answer_fingerprints':{k:list(v) for k,v in ns['periodic_answer_fingerprints'].items()}})
        cache=ns['group_message_cache'][gid];cache.clear();cache.extend(caches[gid])
        if act=='cache_clear':
            ns['group_image_cache'].pop(gid,None);ns['group_last_periodic_seq'][gid]=done[gid]
            for key in list(ns['histories']):
                if key.startswith('group-'+gid+'-'):ns['histories'].pop(key,None)
    return b

def poll(ns,rt):
    inbox=ROOT/'inbox';inbox.mkdir(parents=True,exist_ok=True,mode=0o700)
    for path in sorted(inbox.glob('*.json'))[:10]:
        cmd=json.loads(path.read_text());ident=cmd.get('id','')
        if not re.fullmatch('[a-f0-9]{32}',ident):path.unlink();continue
        if ident not in LEDGER:
            result={'id':ident,'time':time.time()}
            try:
                if time.time()>cmd['expires']:raise ValueError('操作已过期，未执行')
                result['backup']=apply(ns,rt,cmd);result.update(status='done',message='已执行并保存')
            except ValueError as e:result.update(status='failed',message=str(e))
            except Exception as e:result.update(status='failed',message='执行失败：'+type(e).__name__)
            LEDGER[ident]=result
            while len(LEDGER)>200:LEDGER.pop(next(iter(LEDGER)))
            atomic(ROOT/'ledger.json',LEDGER)
        path.unlink()
    groups=[];used=0
    ids=set(ns['group_message_cache'])|set(ns['group_memory_summary'])
    for gid in sorted(ids):
        if rt.blocked(gid):continue
        memory=ns['group_memory_summary'].get(gid,'');cache=list(ns['group_message_cache'].get(gid,[]))
        rows=[{'seq':x.get('seq'),'time':x.get('time'),'nickname':x.get('nickname'),'user_id':x.get('user_id'),'text':x.get('text',''),'is_bot':bool(x.get('is_bot')),'image_count':len(x.get('image_urls',[])),'revision':revision(x)} for x in cache[-30:]]
        g={'id':gid,'memory':memory[:20000],'memory_complete':len(memory)<=20000,'memory_revision':revision(memory),'memory_updated':ns['group_memory_last_update_ts'].get(gid),'cache':rows,'cache_total':len(cache),'cache_revision':revision(cache),'busy':bool(BUSY[gid])}
        size=len(json.dumps(g,ensure_ascii=False).encode())
        if used+size>800000:break
        groups.append(g);used+=size
    atomic(ROOT/'state.json',{'sampled':time.time(),'settings':settings(ns),'settings_revision':revision(settings(ns)),'groups':groups,'group_total':len([x for x in ids if not rt.blocked(x)]),'acks':list(LEDGER.values())[-100:]})

def install(ns,rt):
    if ns.get('_control_v4'):return
    ns['_control_v4']=True;ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (ROOT/'settings.json').exists():
        values=json.loads((ROOT/'settings.json').read_text());validate({'action':'settings','revision':'0'*64,'values':values})
        for k,v in values.items():
            if k in ns:ns[k]=v
            else:OPTIONS[k]=v
    if (ROOT/'ledger.json').exists():LEDGER.update(json.loads((ROOT/'ledger.json').read_text()))
    def scoped(name,scope,group=False):
        fn=ns[name]
        @wraps(fn)
        async def wrapper(*args,**kwargs):
            if scope=='memory' and not OPTIONS['memory_enabled']:return False
            import inspect
            bound=inspect.signature(fn).bind(*args,**kwargs).arguments if group else {}
            gid=str(bound.get('group_id','')) if scope=='memory' else str(bound.get('event',{}).get('group_id','')) if group else ''
            token=SCOPE.set(scope)
            if gid:BUSY[gid]+=1
            try:return await fn(*args,**kwargs)
            finally:
                SCOPE.reset(token)
                if gid:
                    BUSY[gid]-=1
                    if BUSY[gid]<=0:BUSY.pop(gid,None)
        ns[name]=wrapper
    for name,scope,group in [('handle_group_message','chat',True),('handle_private_message','chat',False),('periodic_group_review','patrol',False),('compact_group_memory_once','memory',True),('run_agent_task','task',False)]:scoped(name,scope,group)
    for name in ('call_openclaw','call_mimo_vision'):
        def wrap(fn):
            @wraps(fn)
            async def call(messages,max_tokens=800,timeout=300):
                if SCOPE.get() in ('chat','patrol'):
                    max_tokens=min(max_tokens,OPTIONS['reply_tokens'])
                    instruction='聊天回复用自然口语，最多2到3个简短句子，每句另起一行，每句不超过60字。直接回应重点，不重复，不写长段总结；短问题可以只答一句。保留SILENCE判断规则。'
                    if OPTIONS.get('style_note'):instruction+='\n管理员补充的语气偏好：'+OPTIONS['style_note']
                    copied=[dict(m) for m in messages]
                    if copied and copied[0].get('role')=='system' and isinstance(copied[0].get('content'),str):copied[0]['content']+='\n'+instruction
                    else:copied.insert(0,{'role':'system','content':instruction})
                    answer=await fn(copied,max_tokens=max_tokens,timeout=timeout)
                    return '\n'.join(chunks(answer))
                return await fn(messages,max_tokens=max_tokens,timeout=timeout)
            return call
        ns[name]=wrap(ns[name])
    def outgoing(name):
        fn=ns[name]
        @wraps(fn)
        async def send(ws,*args,**kw):
            if SCOPE.get() not in ('chat','patrol'):return await fn(ws,*args,**kw)
            import inspect
            bound=inspect.signature(fn).bind(ws,*args,**kw);bound.apply_defaults()
            gid=bound.arguments.get('group_id')
            if gid is not None and rt.blocked(gid):return None
            if SCOPE.get()=='patrol' and not OPTIONS['patrol_enabled']:return None
            messages=chunks(bound.arguments['text'])
            key=('g',str(gid)) if gid is not None else ('p',str(bound.arguments.get('user_id')))
            entry=BATCH_LOCKS.setdefault(key,[asyncio.Lock(),0]);entry[1]+=1
            try:
                async with entry[0]:
                    for i,message in enumerate(messages):
                        if i:await asyncio.sleep(OPTIONS['delay_ms']/1000)
                        bound.arguments['text']=message
                        if i and 'mention' in bound.arguments:bound.arguments['mention']=False
                        await fn(*bound.args,**bound.kwargs)
            finally:
                entry[1]-=1
                if not entry[1]:BATCH_LOCKS.pop(key,None)
        ns[name]=send
    outgoing('send_group_msg');outgoing('send_private_msg')
    recent=ns['build_group_recent_text']
    @wraps(recent)
    def review(*a,**kw):return recent(*a,**kw) if OPTIONS['patrol_enabled'] else ('',0,0,0,[])
    ns['build_group_recent_text']=review
    old=rt.snapshot
    def snapshot():
        old()
        try:poll(ns,rt)
        except Exception as e:print('[控制同步] '+type(e).__name__,flush=True)
    rt.snapshot=snapshot
