"""Additive bot console on the existing Flask app and administrator session."""
import hmac
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager
from flask import jsonify,request,session

ROOT=Path(__file__).resolve().parent

def sanitize(value,depth=0):
    if depth>8:raise ValueError('too deep')
    if value is None or type(value) is bool:return value
    if type(value) in (int,float):
        if not math.isfinite(value) or abs(value)>10**18:raise ValueError('number')
        return value
    if isinstance(value,str):return value[:300]
    if isinstance(value,list):
        if len(value)>1000:raise ValueError('too many entries')
        return [sanitize(x,depth+1) for x in value]
    if isinstance(value,dict):
        if len(value)>1000:raise ValueError('too many fields')
        return {str(k)[:100]:sanitize(v,depth+1) for k,v in value.items()}
    raise ValueError('type')

def select(obj,keys):
    if not isinstance(obj,dict):return {}
    return {k:obj[k] for k in keys if k in obj}

def clean(data):
    # Store explicit metadata only. Unknown keys (including token/message) discarded.
    if not isinstance(data,dict):raise ValueError('object required')
    data=sanitize(data)
    result=select(data,['sampled','model','runtime_age'])
    result['node']=select(data.get('node'),['os','cores','load','memory_total','memory_used','swap_total','swap_used','disk_total','disk_used','uptime','rx','tx'])
    result['gateway']=select(data.get('gateway'),['reachable','tcp_ms'])
    result['services']={name:select(data.get('services',{}).get(name),['ActiveState','SubState','NRestarts','ExecMainStatus','ActiveEnterTimestamp','Status','Running','Restarting','OOMKilled','ExitCode','StartedAt']) for name in ['qq-openclaw-bridge','ceylan-heartbeat','napcat']}
    n=data.get('napcat',{});n=n if isinstance(n,dict) else {}
    nn=select(n,['error','group_total','friend_total','can_send_image','can_send_record'])
    nn['account']=select(n.get('account'),['user_id','nickname'])
    nn['version']=select(n.get('version'),['app_name','app_version','protocol_version'])
    nn['status']=select(n.get('status'),['online','good'])
    nn['api']=select(n.get('api'),['get_login_info','get_status','get_version_info','get_group_list','get_friend_list','can_send_image','can_send_record'])
    for key,limit,cols in [('groups',300,['group_id','group_name','member_count','max_member_count']),('friends',500,['user_id','nickname','remark'])]:
        nn[key]=[select(row,cols) for row in n[key][:limit]] if isinstance(n.get(key),list) else None
    result['napcat']=nn
    r=data.get('runtime');result['runtime']=None
    if isinstance(r,dict):
        # Accept legacy snapshots long enough to purge them, but never persist or
        # return the actual IDs to the browser.
        blocked={str(g) for g in r.get('blocked',[]) if str(g).isdigit()}
        rr=select(r,['started','sampled','active_models','average_latency','extra_tasks','conversation_locks','model_concurrency','queue_limit'])
        counter_keys=['received','group','private','images','model_calls','model_ok','model_errors','model_cancelled','sent_requests','send_errors','patrol_checks']
        rr['counts']=select(r.get('counts'),counter_keys)
        rr['limits']=select(r.get('limits'),['MAX_HISTORY','ACTIVE_GROUP_CONTEXT_LIMIT','PERIODIC_REVIEW_INTERVAL_SECONDS','GROUP_MEMORY_COMPACT_INTERVAL_SECONDS','GROUP_MEMORY_MIN_NEW_MESSAGES'])
        rr['blocklist_enabled']=bool(r.get('blocklist_enabled') or blocked)
        rr['blocked_group_count']=max(0,min(100000,int(r.get('blocked_group_count') or len(blocked))))
        rr['groups']={k:select(v,['received','sent','last_message','cached','pending','memory_chars','memory_updated']) for k,v in r.get('groups',{}).items() if k.isdigit() and k not in blocked}
        rr['hours']=[select(x,['time']+counter_keys) for x in r.get('hours',[])][-24:]
        for key,cols,limit in [('events',['time','kind','status','group','duration'],60),('recent',['time','kind','group','types'],50)]:
            rr[key]=[select(x,cols) for x in r.get(key,[]) if isinstance(x,dict) and str(x.get('group')) not in blocked][-limit:]
        result['runtime']=rr
    return result

def install(app,base):
    if app.extensions.get('ceylan_monitor'):return
    app.extensions['ceylan_monitor']=True
    dbpath=os.getenv('CEYLAN_TELEMETRY_DB','/var/lib/ceylan-status/bot_telemetry.db')
    @contextmanager
    def connect():
        c=sqlite3.connect(dbpath,timeout=5)
        c.execute('CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), received REAL NOT NULL, payload TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS samples (time REAL PRIMARY KEY, memory REAL, disk REAL)')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:c.close()
    def read():
        with connect() as c:
            row=c.execute('SELECT received,payload FROM snapshot WHERE id=1').fetchone()
            trend=[{'time':r[0],'memory':r[1],'disk':r[2]} for r in c.execute('SELECT time,memory,disk FROM samples ORDER BY time DESC LIMIT 120')][::-1]
        received=row[0] if row else None
        return {'received':received,'fresh':bool(received and 0<=time.time()-received<=180),'data':json.loads(row[1]) if row else None,'trend':trend,'basic':base['get_bot_status'](),'server_time':time.time()}
    @app.post('/api/bot/telemetry')
    def receive_telemetry():
        token=os.getenv('CEYLAN_BOT_TOKEN','')
        if len(token)<32:return jsonify(ok=False,error='not configured'),503
        if not hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+token).encode()):return jsonify(ok=False,error='unauthorized'),401
        if not request.is_json:return jsonify(ok=False,error='JSON required'),415
        raw=request.stream.read(262145)
        if len(raw)>262144:return jsonify(ok=False,error='too large'),413
        try:d=clean(json.loads(raw))
        except (ValueError,TypeError,AttributeError,OverflowError,RecursionError):return jsonify(ok=False,error='invalid metadata'),400
        now=time.time();node=d.get('node',{})
        def ratio(used,total):
            a,b=node.get(used),node.get(total)
            return round(100*a/b,2) if type(a) in (int,float) and type(b) in (int,float) and b>0 and 0<=a<=b else None
        with connect() as c:
            c.execute('INSERT OR REPLACE INTO snapshot VALUES(1,?,?)',(now,json.dumps(d,ensure_ascii=False,allow_nan=False)))
            c.execute('DELETE FROM samples WHERE time < ?',(now-86400,))
            minute=int(now//60)*60
            c.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?)',(minute,ratio('memory_used','memory_total'),ratio('disk_used','disk_total')))
        return jsonify(ok=True)
    @app.get('/admin/api/bot/telemetry')
    def admin_telemetry():
        if not session.get('ceylan_admin'):return jsonify(error='login required'),401
        return jsonify(read())
    @app.after_request
    def headers(response):
        if request.path in ('/api/bot/telemetry','/admin/api/bot/telemetry'):response.headers['Cache-Control']='no-store'
        return response
    @base['admin_required']
    def page():
        return base['render_console']('bot',(ROOT/'bot-console.html').read_text(),bot=base['get_bot_status']())
    app.view_functions['admin_bot']=page
