"""Reuse existing administrator session. Store encrypted requests, never plaintext keys."""
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from flask import jsonify, request, session
from ceylan_keys_common import validate


def install(app, base):
    if app.extensions.get('keys_v5'):
        return
    app.extensions['keys_v5'] = True
    dbpath = Path(os.getenv('CEYLAN_KEYS_DB', '/var/lib/ceylan-status/keys_v5.db'))

    @contextmanager
    def db():
        # Create privately before SQLite opens it, including the first request.
        fd = os.open(dbpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600); os.close(fd)
        conn = sqlite3.connect(dbpath, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, received REAL, body TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS ops (id TEXT PRIMARY KEY, created REAL, expires REAL, target TEXT, action TEXT, body TEXT, status TEXT, message TEXT)')
            conn.commit()
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback(); raise
        finally:
            conn.close()

    def cleanup(c):
        c.execute("UPDATE ops SET status='expired',body=NULL,message='已过期，未确认执行' WHERE status='pending' AND expires<?", (time.time(),))
        c.execute('DELETE FROM ops WHERE created<?', (time.time()-30*86400,))

    def fail(message, status):
        return jsonify(error=message), status

    def body(limit):
        if not request.is_json:
            raise ValueError('需要 JSON')
        raw = request.stream.read(limit+1)
        if len(raw)>limit:
            raise ValueError('请求过大')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('请求格式错误')
        return data

    def csrf():
        return bool(session.get('keys_csrf')) and hmac.compare_digest(session['keys_csrf'].encode(), request.headers.get('X-CSRF-Token','').encode())

    @app.get('/admin/bot/models', endpoint='keys_v5_page')
    @base['admin_required']
    def page():
        session.setdefault('keys_csrf', secrets.token_urlsafe(32))
        return base['render_console']('bot', (Path(__file__).parent/'bot-models.html').read_text(), csrf=session['keys_csrf'])

    @app.post('/api/bot/keys/sync', endpoint='keys_v5_sync')
    def sync():
        token=os.getenv('CEYLAN_BOT_TOKEN','')
        if len(token)<32 or not hmac.compare_digest(request.headers.get('Authorization','').encode(), ('Bearer '+token).encode()):
            return fail('unauthorized',401)
        try:
            data=body(40000)
            pub=data.get('public_key','')
            if not isinstance(pub,str) or not pub.startswith('-----BEGIN PUBLIC KEY-----') or len(pub)>1200:
                raise ValueError('无效公钥')
            if type(data.get('sampled')) not in (float,int) or not time.time()-120<=data['sampled']<=time.time()+30:
                raise ValueError('快照时间异常')
            cards=[]
            if not isinstance(data.get('cards'),list) or len(data['cards'])!=2:
                raise ValueError('快照格式异常')
            for row in data['cards']:
                if row.get('name') not in ('vision','openclaw'):
                    raise ValueError('模型目标异常')
                item={k:str(row.get(k,''))[:400] for k in ('name','base','model','provider','revision','source','reason','load_state')}
                item.update({k:row.get(k) is True for k in ('editable','configured','rollback')})
                cards.append(item)
            loaded=data.get('vision_loaded') or {}
            clean={'sampled':data['sampled'],'public_key':pub,'cards':cards,
                   'vision_loaded':{k:loaded[k] for k in ('id','loaded_at','source') if k in loaded}}
            results=data.get('results',[])
            if not isinstance(results,list) or len(results)>100:
                raise ValueError('结果格式异常')
        except (ValueError,TypeError,AttributeError,KeyError):
            return fail('invalid model snapshot',400)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');cleanup(c)
            c.execute('INSERT OR REPLACE INTO state VALUES(1,?,?)',(time.time(),json.dumps(clean)))
            for ack in results:
                if ack.get('status') not in ('done','failed','unknown'):
                    continue
                c.execute("UPDATE ops SET status=?,message=?,body=NULL WHERE id=? AND status IN ('pending','expired')",
                          (ack['status'],str(ack.get('message',''))[:400],ack.get('id')))
            queued=[json.loads(r['body'])|{'expires':r['expires']} for r in c.execute("SELECT body,expires FROM ops WHERE status='pending' ORDER BY created LIMIT 1")]
        return jsonify(ok=True,commands=queued)

    @app.get('/admin/api/bot/models', endpoint='keys_v5_status')
    def status():
        if not session.get('ceylan_admin'):
            return fail('请重新登录',401)
        with db() as c:
            cleanup(c)
            row=c.execute('SELECT * FROM state WHERE id=1').fetchone()
            ops=[dict(x) for x in c.execute('SELECT id,created,target,action,status,message FROM ops ORDER BY created DESC LIMIT 40')]
        state=json.loads(row['body']) if row else None
        fresh=bool(row and 0<=time.time()-row['received']<100 and 0<=time.time()-state['sampled']<100)
        return jsonify(state=state,fresh=fresh,operations=ops)

    @app.post('/admin/api/bot/models', endpoint='keys_v5_enqueue')
    def enqueue():
        if not session.get('ceylan_admin'):
            return fail('请重新登录',401)
        if not csrf():
            return fail('会话安全校验失败，请刷新页面',403)
        try:
            command=validate(body(6000))
        except ValueError as e:
            return fail(str(e),400)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');cleanup(c)
            row=c.execute('SELECT * FROM state WHERE id=1').fetchone()
            if not row:
                return fail('等待机器人接入',409)
            state=json.loads(row['body'])
            if not (0<=time.time()-row['received']<100 and 0<=time.time()-state['sampled']<100):
                return fail('机器人快照已过期，请刷新',409)
            card=next((x for x in state['cards'] if x['name']==command['target']),{})
            if not card.get('editable') or card.get('revision')!=command['revision']:
                return fail('配置不支持修改或版本发生变化，请刷新',409)
            if command['action']=='rollback' and not card.get('rollback'):
                return fail('没有可用回滚点',409)
            if c.execute("SELECT COUNT(*) FROM ops WHERE status='pending'").fetchone()[0]:
                return fail('已有操作等待执行，请等结果回传',409)
            if c.execute('SELECT COUNT(*) FROM ops WHERE created>?',(time.time()-60,)).fetchone()[0]>=5:
                return fail('测试过于频繁，请稍后重试',429)
            try:
                c.execute('INSERT INTO ops VALUES(?,?,?,?,?,?,?,?)',
                          (command['id'],time.time(),time.time()+600,command['target'],command['action'],json.dumps(command),'pending','等待机器人执行'))
            except sqlite3.IntegrityError:
                return fail('操作编号已使用，请刷新重试',409)
        return jsonify(ok=True,id=command['id']),202

    @app.after_request
    def headers(response):
        if '/bot/models' in request.path or '/bot/keys' in request.path:
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    # Both entry pages retain the existing login guard and all previous content.
    for endpoint in ('admin_bot','control_v4_page'):
        old=app.view_functions.get(endpoint)
        if not old:
            continue
        def wrap(fn):
            def view(*args,**kwargs):
                response=app.make_response(fn(*args,**kwargs))
                if response.status_code==200 and response.mimetype=='text/html':
                    content=response.get_data(as_text=True)
                    link='<div class="actions" style="margin:18px 0"><a class="button primary" href="/admin/bot/models">模型与密钥管理 ↗</a></div>'
                    for anchor in ('<section class="bot-hero">','<section class="page-heading">','<section class="control-heading">','<div class="control-heading">'):
                        if anchor in content:
                            content=content.replace(anchor,link+anchor,1);break
                    response.set_data(content)
                return response
            return view
        app.view_functions[endpoint]=wrap(old)
