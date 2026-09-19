"""CeylanCLC V6 interaction laboratory for the existing Flask console."""
from contextlib import contextmanager
from functools import wraps
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import time

from flask import jsonify, request, session
from ceylan_fun_core import revision, validate_settings

ROOT = Path(__file__).resolve().parent
COUNTERS = {'messages','group_messages','private_messages','images','model_calls','model_ok','model_errors',
            'patrol_model_calls','patrol_skipped_quiet','patrol_skipped_limit','sent_replies','sent_chars','easter_hits'}


def counters(value):
    if not isinstance(value, dict): raise ValueError('统计格式错误')
    result = {}
    for key, item in value.items():
        if key not in COUNTERS or type(item) is not int or not 0 <= item <= 10**15:
            raise ValueError('统计字段无效')
        result[key] = item
    return result


def clean_state(data):
    if not isinstance(data, dict) or type(data.get('sampled')) not in (int, float):
        raise ValueError('快照格式错误')
    if not math.isfinite(data['sampled']) or not time.time()-86400 <= data['sampled'] <= time.time()+60:
        raise ValueError('快照时间错误')
    settings = validate_settings(data.get('settings'))
    if data.get('settings_revision') != revision(settings): raise ValueError('设置版本错误')
    days = []
    if not isinstance(data.get('days'), list) or len(data['days']) > 14: raise ValueError('日期统计过多')
    for row in data['days']:
        if not isinstance(row, dict) or not isinstance(row.get('date'), str) or len(row['date']) != 10:
            raise ValueError('日期格式错误')
        days.append({'date': row['date'], **counters({k:v for k,v in row.items() if k!='date'})})
    groups = []
    if not isinstance(data.get('groups'), list) or len(data['groups']) > 100: raise ValueError('群统计过多')
    for row in data['groups']:
        gid = str(row.get('id',''))
        if not gid.isdigit(): continue
        active=row.get('last_active')
        if active is not None and (type(active) not in (int,float) or not math.isfinite(active) or active<0):
            raise ValueError('群活跃时间错误')
        groups.append({'id': gid, 'last_active': active,
                       **counters({k:row.get(k,0) for k in ('group_messages','images','sent_replies')})})
    mood = data.get('mood') or {}
    if mood.get('key') not in ('night','cloudy','party','spark','cruise','sleep'):
        raise ValueError('心情状态无效')
    achievements = []
    if not isinstance(data.get('achievements'), list) or len(data['achievements']) > 12: raise ValueError('成就格式错误')
    for item in data['achievements']:
        if type(item.get('unlocked')) is not bool: raise ValueError('成就状态错误')
        achievements.append({k:str(item.get(k,''))[:80] for k in ('id','name','description')}|{'unlocked':item['unlocked']})
    clean = {'sampled': data['sampled'], 'started': data.get('started'), 'settings': settings,
             'settings_revision': data['settings_revision'], 'today': counters(data.get('today',{})),
             'totals': counters(data.get('totals',{})), 'days': days, 'groups': groups,
             'mood': {k:str(mood.get(k,''))[:100] for k in ('key','name','note')},
             'title': str(data.get('title',''))[:40], 'quiet_active': data.get('quiet_active') is True,
             'achievements': achievements}
    acks = data.get('acks', [])
    if not isinstance(acks, list) or len(acks) > 50: raise ValueError('回执格式错误')
    clean['_acks'] = [{k:item.get(k) for k in ('id','time','status','message','backup')} for item in acks if isinstance(item,dict)]
    return clean


def install(app, base):
    if app.extensions.get('fun_v6'): return
    app.extensions['fun_v6'] = True
    dbpath = Path(os.getenv('CEYLAN_FUN_DB', '/var/lib/ceylan-status/bot_fun.db'))

    @contextmanager
    def db():
        fd = os.open(dbpath, os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600); os.fchmod(fd,0o600); os.close(fd)
        conn = sqlite3.connect(dbpath, timeout=8); conn.row_factory = sqlite3.Row
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), received REAL, body TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS ops (id TEXT PRIMARY KEY, created REAL, expires REAL, body TEXT, status TEXT, message TEXT)')
            conn.commit(); yield conn; conn.commit()
        except BaseException: conn.rollback(); raise
        finally: conn.close()

    def cleanup(conn):
        conn.execute("UPDATE ops SET status='expired',body=NULL,message='操作过期，未确认执行' WHERE status='pending' AND expires<?",(time.time(),))
        conn.execute('DELETE FROM ops WHERE created<?',(time.time()-30*86400,))

    def fail(message, status): return jsonify(error=message), status
    def authorized():
        token = os.getenv('CEYLAN_BOT_TOKEN','')
        return len(token)>=32 and hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+token).encode())
    def csrf():
        return bool(session.get('fun_csrf')) and hmac.compare_digest(session['fun_csrf'].encode(),request.headers.get('X-CSRF-Token','').encode())
    def body(limit):
        if not request.is_json: raise ValueError('需要 JSON')
        raw=request.stream.read(limit+1)
        if len(raw)>limit: raise ValueError('请求过大')
        data=json.loads(raw)
        if not isinstance(data,dict): raise ValueError('请求格式错误')
        return data

    @app.post('/api/bot/fun/sync', endpoint='fun_v6_sync')
    def sync():
        if not authorized(): return fail('unauthorized',401)
        try: state=clean_state(body(400000).get('state'))
        except (ValueError,TypeError,AttributeError,KeyError,RecursionError): return fail('invalid fun snapshot',400)
        acks=state.pop('_acks')
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE');cleanup(conn)
            conn.execute('INSERT OR REPLACE INTO state VALUES(1,?,?)',(time.time(),json.dumps(state,ensure_ascii=False)))
            for ack in acks:
                if ack.get('status') not in ('done','failed'): continue
                message=str(ack.get('message',''))[:300]
                if ack.get('backup'): message+='；备份：'+str(ack['backup'])[:180]
                conn.execute("UPDATE ops SET status=?,body=NULL,message=? WHERE id=? AND status IN ('pending','expired')",
                             (ack['status'],message,ack.get('id')))
            commands=[json.loads(row['body'])|{'expires':row['expires']} for row in conn.execute("SELECT body,expires FROM ops WHERE status='pending' ORDER BY created LIMIT 3")]
        return jsonify(ok=True,commands=commands)

    def telemetry():
        path=Path(os.getenv('CEYLAN_TELEMETRY_DB','/var/lib/ceylan-status/bot_telemetry.db'))
        if not path.is_file(): return None,{}
        conn=None
        try:
            conn=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2)
            row=conn.execute('SELECT received,payload FROM snapshot WHERE id=1').fetchone()
            if not row:return None,{}
            value=json.loads(row[1]); names={str(x.get('group_id')):str(x.get('group_name',''))[:80] for x in value.get('napcat',{}).get('groups') or []}
            summary={'received':row[0],'gateway':value.get('gateway',{}),'services':value.get('services',{}),'node':value.get('node',{}),'runtime':value.get('runtime')}
            return summary,names
        except (sqlite3.Error,ValueError,TypeError,AttributeError): return None,{}
        finally:
            if conn:conn.close()

    def health(state,fresh,tele):
        score=0; warnings=[]
        if fresh:score+=40
        else:warnings.append('互动快照已超时，暂不接受设置修改')
        basic=base['get_bot_status']()
        if basic.get('fresh'):score+=20
        else:warnings.append('基础心跳不是最新状态')
        today=state.get('today',{}) if state else {};calls=today.get('model_calls',0);errors=today.get('model_errors',0)
        if not calls or errors/calls<.05:score+=20
        elif errors/calls<.15:score+=10;warnings.append('今天模型异常比例超过 5%')
        else:warnings.append('今天模型异常比例超过 15%')
        if tele and time.time()-tele.get('received',0)<180:score+=10
        else:warnings.append('详细监控未连接或数据过期')
        disk=(tele or {}).get('node',{});used,total=disk.get('disk_used'),disk.get('disk_total')
        if type(used) in (int,float) and type(total) in (int,float) and total:
            if used/total<.85:score+=10
            else:warnings.append('机器人节点磁盘使用率超过 85%')
        else:score+=5
        if state and calls>=state['settings']['daily_model_warning']:warnings.append('今天模型调用已达到提醒值')
        return {'score':score,'grade':'优秀' if score>=90 else '良好' if score>=75 else '注意' if score>=55 else '待检查','warnings':warnings}

    @app.get('/admin/bot/play', endpoint='fun_v6_page')
    @base['admin_required']
    def page():
        session.setdefault('fun_csrf',secrets.token_urlsafe(32))
        return base['render_console']('bot',(ROOT/'bot-play.html').read_text(),csrf=session['fun_csrf'])

    @app.get('/admin/api/bot/play', endpoint='fun_v6_status')
    def status():
        if not session.get('ceylan_admin'):return fail('请重新登录',401)
        with db() as conn:
            cleanup(conn);row=conn.execute('SELECT * FROM state WHERE id=1').fetchone()
            ops=[dict(x) for x in conn.execute('SELECT id,created,status,message FROM ops ORDER BY created DESC LIMIT 30')]
        state=json.loads(row['body']) if row else None
        fresh=bool(row and state and 0<=time.time()-row['received']<100 and 0<=time.time()-state['sampled']<100)
        tele,names=telemetry()
        if state:
            for group in state['groups']:group['name']=names.get(group['id'])
        return jsonify(state=state,fresh=fresh,received=row['received'] if row else None,operations=ops,
                       telemetry=tele,health=health(state,fresh,tele),server_time=time.time())

    @app.post('/admin/api/bot/play', endpoint='fun_v6_enqueue')
    def enqueue():
        if not session.get('ceylan_admin'):return fail('请重新登录',401)
        if not csrf():return fail('安全校验失败，请刷新页面',403)
        try:
            data=body(30000); settings=validate_settings(data.get('settings'))
            rev=data.get('revision')
            if not isinstance(rev,str) or len(rev)!=64:raise ValueError('设置版本无效')
        except (ValueError,TypeError) as exc:return fail(str(exc),400)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE');cleanup(conn);row=conn.execute('SELECT * FROM state WHERE id=1').fetchone()
            if not row:return fail('等待机器人接入互动通道',409)
            state=json.loads(row['body'])
            if not (0<=time.time()-row['received']<100 and 0<=time.time()-state['sampled']<100):return fail('机器人状态过期，暂不修改',409)
            if rev!=state['settings_revision']:return fail('设置已变化，请刷新后重试',409)
            if conn.execute("SELECT COUNT(*) FROM ops WHERE status='pending'").fetchone()[0]:return fail('已有设置等待执行',409)
            ident=secrets.token_hex(16);now=time.time();command={'id':ident,'action':'settings','revision':rev,'settings':settings}
            conn.execute('INSERT INTO ops VALUES(?,?,?,?,?,?)',(ident,now,now+600,json.dumps(command,ensure_ascii=False),'pending','等待机器人执行'))
        return jsonify(ok=True,id=ident),202

    @app.after_request
    def headers(response):
        if '/bot/play' in request.path or '/bot/fun' in request.path:
            response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        return response

    for endpoint in ('admin_bot','control_v4_page','keys_v5_page'):
        old=app.view_functions.get(endpoint)
        if not old:continue
        def wrap(fn):
            @wraps(fn)
            def view(*args,**kwargs):
                response=app.make_response(fn(*args,**kwargs))
                if response.status_code==200 and response.mimetype=='text/html':
                    content=response.get_data(as_text=True)
                    link='<div class="actions" style="margin:18px 0"><a class="button primary" href="/admin/bot/play">互动实验室 ✦</a></div>'
                    for anchor in ('<section class="bot-hero">','<section class="page-heading">','<section class="control-heading">','<div class="control-heading">','<section class="model-head">'):
                        if anchor in content:content=content.replace(anchor,link+anchor,1);break
                    response.set_data(content)
                return response
            return view
        app.view_functions[endpoint]=wrap(old)
