"""Incremental features registered on the existing Ceylan Flask app."""
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from flask import abort, jsonify, render_template_string, request, session
from project_config import SITE_DISPLAY_NAME, BOT_DISPLAY_NAME, BOT_NODE_LABEL

ROOT = Path(__file__).resolve().parent


def install(app, base):
    if app.extensions.get('ceylan_v2'):
        return
    app.extensions['ceylan_v2'] = True
    path = os.environ.get('CEYLAN_CLIPBOARD_DB', '/var/lib/ceylan-status/clipboard.db')

    @contextmanager
    def database():
        conn = sqlite3.connect(path, timeout=8)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS clips (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL)')
            conn.commit()
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def api_login(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            if not session.get('ceylan_admin'):
                return jsonify(error='请重新登录', code='login_required'), 401
            if request.method != 'GET':
                expected = session.get('clipboard_csrf', '')
                supplied = request.headers.get('X-CSRF-Token', '')
                if not expected or not hmac.compare_digest(expected, supplied):
                    return jsonify(error='安全校验失败，请刷新页面'), 403
            return fn(*args, **kwargs)
        return inner

    def body():
        if not request.is_json:
            abort(415)
        raw = request.stream.read(100001)
        if len(raw) > 100000:
            abort(413)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            abort(400)
        if not isinstance(data, dict):
            abort(400)
        return data

    def validate(data):
        text, title = data.get('text'), data.get('title', '')
        ttl = data.get('ttl', 86400)
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 65536:
            abort(400, description='文本不能为空，且不能超过 64 KiB')
        if not isinstance(title, str) or len(title) > 80 or type(ttl) is not int or ttl not in (3600, 86400, 604800):
            abort(400)
        return title.strip(), text, ttl

    @app.get('/admin/clipboard')
    @base['admin_required']
    def clipboard_page():
        session.setdefault('clipboard_csrf', secrets.token_urlsafe(32))
        template = (ROOT / 'clipboard.html').read_text(encoding='utf-8')
        return base['render_console']('clipboard', template, csrf=session['clipboard_csrf'])

    @app.route('/admin/api/clipboard', methods=['GET', 'POST'])
    @api_login
    def clipboard_items():
        now = time.time()
        data = body() if request.method == 'POST' else None
        values = validate(data) if data is not None else None
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM clips WHERE expires <= ?', (now,))
            if data is not None:
                if conn.execute('SELECT COUNT(*) FROM clips').fetchone()[0] >= 100:
                    return jsonify(error='已达 100 条上限，请先删除部分内容'), 409
                title, text, ttl = values
                ident = secrets.token_hex(12)
                conn.execute('INSERT INTO clips VALUES (?,?,?,?,?,?,?)', (ident, title, text, 1, now, now, now+ttl))
                row = dict(conn.execute('SELECT * FROM clips WHERE id=?', (ident,)).fetchone())
                return jsonify(item=row), 201
            rows = conn.execute('SELECT id,title,revision,created,updated,expires,substr(text,1,150) AS preview FROM clips ORDER BY updated DESC').fetchall()
            return jsonify(items=[dict(r) for r in rows], server_time=now)

    @app.route('/admin/api/clipboard/<ident>', methods=['GET', 'PUT', 'DELETE'])
    @api_login
    def clipboard_item(ident):
        data = body() if request.method != 'GET' else None
        values = validate(data) if request.method == 'PUT' else None
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM clips WHERE expires <= ?', (time.time(),))
            row = conn.execute('SELECT * FROM clips WHERE id=?', (ident,)).fetchone()
            if row is None:
                return jsonify(error='内容已删除或过期'), 404
            if request.method == 'GET':
                return jsonify(item=dict(row))
            revision = data.get('revision')
            if type(revision) is not int or revision != row['revision']:
                return jsonify(error='其他设备已修改此条内容。你的输入仍保留，请另存为新条目或加载最新版本。', current_revision=row['revision']), 409
            if request.method == 'DELETE':
                conn.execute('DELETE FROM clips WHERE id=?', (ident,))
                return jsonify(ok=True)
            title, text, ttl = values
            now = time.time()
            conn.execute('UPDATE clips SET title=?,text=?,revision=revision+1,updated=?,expires=? WHERE id=?', (title,text,now,now+ttl,ident))
            return jsonify(item=dict(conn.execute('SELECT * FROM clips WHERE id=?', (ident,)).fetchone()))

    @app.get('/api/bot/public')
    def public_bot():
        bot = base['get_bot_status']()
        # No QQ account, IP, message history, credentials or internal service detail.
        state = 'unknown'
        if bot['fresh']:
            state = 'online' if bot.get('online') is True else 'offline' if bot.get('online') is False else 'unknown'
        return jsonify(state=state, heartbeat_fresh=bot['fresh'], model=bot.get('model') or '尚未上报', updated_at=bot.get('received_at'))

    @app.after_request
    def feature_headers(response):
        if request.path.startswith('/admin') or request.path == '/api/bot/public':
            response.headers['Cache-Control'] = 'no-store'
        if request.path.startswith('/admin/api/clipboard'):
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    # Extend the same navigation and rendering function; no second admin system.
    original_render = base['render_console']
    def render_console(active, body, **context):
        if active != 'clipboard':
            return original_render(active, body, **context)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return render_template_string(base['CONSOLE_BASE'].replace('__BODY__', body).replace('__SCRIPT__', ''),
            title='跨设备粘贴板', eyebrow='CLIPBOARD', subtitle='从手机粘贴，在电脑继续。', active=active,
            now_label=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M'), icons=base['CONSOLE_ICONS'],
            site_name=SITE_DISPLAY_NAME, bot_name=BOT_DISPLAY_NAME, bot_node_label=BOT_NODE_LABEL, **context)
    base['render_console'] = render_console
    base['CONSOLE_ICONS']['clipboard'] = base['CONSOLE_ICONS']['export']
    shell = base['CONSOLE_BASE']
    shell = shell.replace("('bot',bot_name,'/admin/bot')", "('bot',bot_name,'/admin/bot'),('clipboard','粘贴板','/admin/clipboard')")
    shell = shell.replace("'bot':'bot'}", "'bot':'bot','clipboard':'clipboard'}")
    css = (ROOT / 'console-v2.css').read_text(encoding='utf-8')
    base['CONSOLE_BASE'] = shell.replace('</style>', css + '\n</style>')
    base['OVERVIEW_TEMPLATE'] = base['OVERVIEW_TEMPLATE'].replace('<section class="stats">', '<div class="quick-strip"><span>跨设备工作区</span><a href="/admin/clipboard">打开粘贴板 ↗</a><a href="/admin/bot">机器人状态 ↗</a></div><section class="stats">', 1)
