"""Metadata-only instrumentation of existing bridge functions; no chat bodies."""
import asyncio
from collections import Counter, deque
from functools import wraps
import json
import os
from pathlib import Path
import tempfile
import time

START = time.time()
COUNTS = Counter()
EVENTS = deque(maxlen=60)
RECENT = deque(maxlen=50)
HOURS = {}
GROUPS = {}
ACTIVE = 0
LATENCIES = deque(maxlen=100)
PATH = Path(os.getenv('CEYLAN_OBSERVER_FILE', '/root/openclaw-qq/observer-status.json'))

def log(kind, status, group=None, duration=None):
    EVENTS.append({'time':time.time(),'kind':kind,'status':status,'group':group,'duration':duration})

def tick(key):
    COUNTS[key] += 1
    hour = int(time.time()//3600)*3600
    HOURS.setdefault(hour,Counter())[key] += 1
    for old in list(HOURS):
        if old < hour-23*3600: del HOURS[old]

def install(ns, runtime):
    if ns.get('_observer_installed'):return
    ns['_observer_installed']=True
    original=ns['handle_event']
    @wraps(original)
    async def handle(ws,event):
        gid=str(event.get('group_id',''))
        if runtime.blocked(gid) or ns['is_ignored_sender'](event):return await original(ws,event)
        kind=event.get('message_type')
        if event.get('post_type')=='message' and kind in ('group','private'):
            tick('received');tick(kind)
            segments=event.get('message',[])
            kinds=sorted({s.get('type','other') for s in segments if isinstance(s,dict)})[:8] if isinstance(segments,list) else ['text']
            if 'image' in kinds:tick('images')
            RECENT.append({'time':time.time(),'kind':kind,'group':gid or None,'types':kinds})
            if gid:
                if gid not in GROUPS and len(GROUPS)>=1000:GROUPS.pop(next(iter(GROUPS)))
                item=GROUPS.setdefault(gid,{'received':0,'sent':0})
                item['received']+=1;item['last_message']=time.time()
        return await original(ws,event)
    ns['handle_event']=handle

    def wrap_model(name):
        fn=ns[name]
        @wraps(fn)
        async def call(*args,**kwargs):
            global ACTIVE
            start=time.monotonic();ACTIVE+=1;tick('model_calls')
            try:
                answer=await fn(*args,**kwargs)
                tick('model_ok');log(name,'ok',duration=round(time.monotonic()-start,3))
                return answer
            except asyncio.CancelledError:
                tick('model_cancelled');log(name,'cancelled');raise
            except Exception as exc:
                tick('model_errors');log(name,type(exc).__name__);raise
            finally:
                LATENCIES.append(round(time.monotonic()-start,3));ACTIVE-=1
        ns[name]=call
    for name in ('call_openclaw','call_mimo_vision'):wrap_model(name)

    class ObservedSocket:
        def __init__(self,ws):self.ws=ws
        def __getattr__(self,key):return getattr(self.ws,key)
        async def send(self,payload):
            try:
                event=json.loads(payload);name=event.get('action','');gid=event.get('params',{}).get('group_id')
                tracked=name in ('send_group_msg','send_private_msg','upload_group_file') and not runtime.blocked(gid)
            except (TypeError,ValueError,AttributeError):tracked=False
            try:result=await self.ws.send(payload)
            except Exception as exc:
                if tracked:tick('send_errors');log(name,type(exc).__name__,str(gid) if gid else None)
                raise
            if tracked:
                tick('sent_requests');log(name,'submitted',str(gid) if gid else None)
                if gid and str(gid) in GROUPS:GROUPS[str(gid)]['sent']+=1
            return result
    def wrap_send(name):
        fn=ns[name]
        @wraps(fn)
        async def send(ws,*args,**kwargs):
            return await fn(ws if isinstance(ws,ObservedSocket) else ObservedSocket(ws),*args,**kwargs)
        ns[name]=send
    for name in ('send_group_msg','send_private_msg','send_group_image','upload_group_file'):wrap_send(name)
    fn=runtime.mark_patrol
    def patrol():
        fn();tick('patrol_checks');log('patrol','checked')
    runtime.mark_patrol=patrol
    snap=runtime.snapshot
    def snapshot():
        snap()
        try:write(ns,runtime)
        except Exception as exc:print('[监控快照] '+type(exc).__name__,flush=True)
    runtime.snapshot=snapshot
    log('bridge','started')

def write(ns,runtime):
    group_ids=set(ns.get('group_message_cache',{}))|set(ns.get('group_memory_summary',{}))|set(GROUPS)
    groups={}
    for gid in sorted(group_ids,key=str)[:1000]:
        if runtime.blocked(gid):continue
        groups[str(gid)]={**GROUPS.get(str(gid),{}),
            'cached':len(ns.get('group_message_cache',{}).get(gid,[])),
            'pending':max(0,ns.get('group_message_seq',{}).get(gid,0)-ns.get('group_last_periodic_seq',{}).get(gid,0)),
            'memory_chars':len(ns.get('group_memory_summary',{}).get(gid,'')),
            'memory_updated':ns.get('group_memory_last_update_ts',{}).get(gid)}
    data={'started':START,'sampled':time.time(),'counts':dict(COUNTS),'active_models':ACTIVE,
        'average_latency':round(sum(LATENCIES)/len(LATENCIES),2) if LATENCIES else None,
        'events':list(EVENTS),'recent':list(RECENT),'groups':groups,
        'hours':[{'time':h,**dict(c)} for h,c in sorted(HOURS.items())],
        'blocklist_enabled':bool(runtime.BLOCKED),'blocked_group_count':len(runtime.BLOCKED),
        'extra_tasks':len(runtime.EXTRA_TASKS),'conversation_locks':len(runtime.LOCKS),
        'limits':{k:ns.get(k) for k in ('MAX_HISTORY','ACTIVE_GROUP_CONTEXT_LIMIT','PERIODIC_REVIEW_INTERVAL_SECONDS','GROUP_MEMORY_COMPACT_INTERVAL_SECONDS','GROUP_MEMORY_MIN_NEW_MESSAGES')},
        'model_concurrency':max(1,min(8,int(os.getenv('CEYLAN_MODEL_CONCURRENCY','2')))),
        'queue_limit':runtime.MAX_PENDING}
    tmp=None
    try:
        with tempfile.NamedTemporaryFile('w',dir=PATH.parent,delete=False) as f:
            tmp=f.name;json.dump(data,f,ensure_ascii=False,allow_nan=False)
        os.replace(tmp,PATH)
    finally:
        if tmp and os.path.exists(tmp):os.unlink(tmp)
