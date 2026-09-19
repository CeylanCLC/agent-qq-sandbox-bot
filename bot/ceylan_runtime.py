"""Bounded concurrency and block-list safeguards for the existing bridge."""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import tempfile
import time
from project_config import BLOCKED_GROUP_IDS

BLOCKED = set(BLOCKED_GROUP_IDS)
NAMESPACE = {}
MODEL_LIMIT = asyncio.Semaphore(max(1,min(8,int(os.getenv('CEYLAN_MODEL_CONCURRENCY','2')))))
MAX_PENDING = max(8,min(512,int(os.getenv('CEYLAN_MAX_PENDING','128'))))
EXTRA_TASKS = set()
LOCKS = {}
SEEN = OrderedDict()
LAST_MESSAGE = None
LAST_PATROL = None
SNAPSHOT = Path(os.getenv('CEYLAN_BOT_SNAPSHOT','/root/openclaw-qq/runtime-status.json'))


def blocked(group):
    return str(group).strip() in BLOCKED


def purge():
    for key in ('group_message_cache','group_image_cache','group_message_seq','group_last_periodic_seq','periodic_answer_fingerprints','group_memory_summary','group_memory_last_seq','group_memory_last_update_ts'):
        mapping=NAMESPACE.get(key,{})
        for gid in list(mapping):
            if blocked(gid):mapping.pop(gid,None)
    histories=NAMESPACE.get('histories',{})
    for key in list(histories):
        if any(str(key).startswith('group-'+gid+'-') for gid in BLOCKED):histories.pop(key,None)


def guard(default=None):
    def decorate(fn):
        sig=inspect.signature(fn)
        def denied(args,kwargs):
            bound=sig.bind(*args,**kwargs).arguments
            group=bound.get('group_id')
            event=bound.get('event')
            if isinstance(event,dict):group=event.get('group_id',group)
            return group is not None and blocked(group)
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def wrapper(*args,**kwargs):
                if denied(args,kwargs):return default
                return await fn(*args,**kwargs)
        else:
            @wraps(fn)
            def wrapper(*args,**kwargs):
                if denied(args,kwargs):return default
                return fn(*args,**kwargs)
        return wrapper
    return decorate


def state_io(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        purge()
        result=fn(*args,**kwargs)
        purge()
        return result
    return wrapper


def limit_model(fn):
    @wraps(fn)
    async def wrapper(*args,**kwargs):
        async with MODEL_LIMIT:
            return await fn(*args,**kwargs)
    return wrapper


@asynccontextmanager
async def conversation_lock(key):
    entry=LOCKS.setdefault(key,[asyncio.Lock(),0])
    entry[1]+=1
    try:
        async with entry[0]:yield
    finally:
        entry[1]-=1
        if entry[1]==0:LOCKS.pop(key,None)


def serial_history(fn):
    @wraps(fn)
    async def wrapper(user_key,*args,**kwargs):
        async with conversation_lock(user_key):
            return await fn(user_key,*args,**kwargs)
    return wrapper


def spawn_task(coro):
    if len(EXTRA_TASKS)>=32:
        coro.close()
        return None
    task=asyncio.create_task(coro)
    EXTRA_TASKS.add(task)
    def done(t):
        EXTRA_TASKS.discard(t)
        if not t.cancelled():
            e=t.exception()
            if e:print('[后台任务失败] '+type(e).__name__,flush=True)
    task.add_done_callback(done)
    return task


def accepted(event):
    if not isinstance(event,dict) or event.get('post_type')!='message':return False
    if event.get('message_type') not in ('group','private'):return False
    if blocked(event.get('group_id','')):return False
    if str(event.get('user_id',''))==str(event.get('self_id','')):return False
    mid=event.get('message_id')
    if mid is None:return True
    now=time.monotonic()
    while SEEN and (len(SEEN)>4096 or next(iter(SEEN.values()))<now-600):SEEN.popitem(last=False)
    key=(str(event.get('self_id')),event.get('message_type'),str(event.get('group_id')),str(event.get('user_id')),str(mid))
    if key in SEEN:return False
    SEEN[key]=now
    return True


def snapshot():
    purge()
    memory=NAMESPACE.get('group_memory_summary',{})
    data={'memory':'enabled','memory_count':sum(bool(v) for v in memory.values()),'patrol':'enabled',
          'last_message':LAST_MESSAGE,'last_patrol':LAST_PATROL,
          'snapshot_at':time.time(),'blocklist_enabled':bool(BLOCKED),
          'blocked_group_count':len(BLOCKED)}
    temp=None
    try:
        with tempfile.NamedTemporaryFile('w',dir=SNAPSHOT.parent,prefix='.runtime-',delete=False,encoding='utf-8') as f:
            temp=f.name
            json.dump(data,f,ensure_ascii=False)
        os.replace(temp,SNAPSHOT)
    except OSError as e:
        print('[状态快照失败] '+type(e).__name__,flush=True)
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)


async def periodic_snapshot():
    while True:
        snapshot()
        await asyncio.sleep(20)


def mark_patrol():
    global LAST_PATROL
    LAST_PATROL=time.strftime('%Y-%m-%dT%H:%M:%S%z')


async def run_bridge(ns):
    global LAST_MESSAGE, NAMESPACE
    NAMESPACE=ns
    from ceylan_keys_runtime import install as install_keys_v5
    install_keys_v5(ns)
    from ceylan_control_runtime import install as install_control
    install_control(ns, __import__(__name__))
    from ceylan_observer import install as install_observer
    install_observer(ns, __import__(__name__))
    from ceylan_fun_runtime import install as install_fun_v6
    install_fun_v6(ns, __import__(__name__))
    ns['load_periodic_state']()
    ns['load_group_memory_state']()
    purge()
    # Deployment backs up both state files before this filtered persistence.
    ns['save_periodic_state']()
    ns['save_group_memory_state']()
    while True:
        tasks=set()
        pending=set()
        try:
            print('连接 NapCat OneBot: '+ns['ONEBOT_WS'],flush=True)
            async with ns['websockets'].connect(ns['ONEBOT_WS'],ping_interval=20,ping_timeout=20,max_size=4*1024*1024) as ws:
                print('已连接 NapCat OneBot',flush=True)
                tasks={asyncio.create_task(ns['periodic_group_review'](ws)),asyncio.create_task(ns['periodic_group_memory_compaction']()),asyncio.create_task(periodic_snapshot())}
                async def handle(event):
                    try:
                        await ns['handle_event'](ws,event)
                    except asyncio.CancelledError:raise
                    except Exception as e:print('[消息处理失败] '+type(e).__name__,flush=True)
                async for raw in ws:
                    try:event=json.loads(raw)
                    except (ValueError,TypeError):continue
                    if len(pending)>=MAX_PENDING:
                        # Bound memory and model queue; do not cache or echo dropped content.
                        print('[消息队列已满] 丢弃新事件',flush=True)
                        continue
                    if not accepted(event):continue
                    LAST_MESSAGE=time.strftime('%Y-%m-%dT%H:%M:%S%z')
                    # Make late-arriving images visible while a prior question waits for them.
                    if not ns['is_ignored_sender'](event):
                        _,_,images=ns['parse_message'](event)
                        if images and event.get('message_type')=='group':
                            ns['remember_group_images'](event['group_id'],event['user_id'],images)
                    task=asyncio.create_task(handle(event))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
        except asyncio.CancelledError:raise
        except Exception as e:print('[连接断开] '+type(e).__name__+'；5 秒后重连',flush=True)
        finally:
            all_tasks=tasks|pending|set(EXTRA_TASKS)
            for task in all_tasks:task.cancel()
            if all_tasks:await asyncio.gather(*all_tasks,return_exceptions=True)
            ns['save_periodic_state']()
            ns['save_group_memory_state']()
        await asyncio.sleep(5)
