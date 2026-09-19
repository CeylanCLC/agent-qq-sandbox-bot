from flask import (
    Flask,
    jsonify,
    request,
    session,
    redirect,
    url_for,
    render_template_string,
    Response,
)
from functools import wraps
from werkzeug.security import check_password_hash
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import sqlite3
import psutil
import time
import os
import csv
import io
import re
from project_config import SITE_DISPLAY_NAME, SITE_TAGLINE, BOT_DISPLAY_NAME, BOT_NODE_LABEL


app = Flask(__name__)

app.secret_key = os.environ["CEYLAN_SECRET_KEY"]

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=1800,
)


DB_PATH = os.environ.get("CEYLAN_DB_PATH", "/var/lib/ceylan-status/visitors.db")

ADMIN_USER = os.environ.get(
    "CEYLAN_ADMIN_USER",
    "ceylan"
)

ADMIN_HASH = os.environ["CEYLAN_ADMIN_HASH"]

RETENTION_DAYS = 30

LOGIN_FAILURES = {}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        visit_id TEXT UNIQUE,

        ts TEXT NOT NULL,

        ip TEXT,
        country TEXT,
        colo TEXT,
        http_proto TEXT,
        tls TEXT,
        warp TEXT,

        path TEXT,
        referrer TEXT,

        ua TEXT,
        language TEXT,
        timezone TEXT,
        screen TEXT,
        viewport TEXT,
        platform TEXT,

        lat REAL,
        lon REAL,
        accuracy REAL,
        address TEXT,

        location_shared INTEGER DEFAULT 0
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS
    idx_visits_ts
    ON visits(ts)
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS
    idx_visits_ip
    ON visits(ip)
    """)

    conn.commit()
    conn.close()


init_db()


def real_ip():
    ip = request.headers.get(
        "CF-Connecting-IP"
    )

    if ip:
        return ip[:64]

    forwarded = request.headers.get(
        "X-Forwarded-For",
        ""
    )

    if forwarded:
        return forwarded.split(",")[0].strip()[:64]

    return (
        request.remote_addr
        or "unknown"
    )[:64]


def clean(value, maximum):
    if value is None:
        return ""

    return str(value)[:maximum]


def cleanup_old_rows(conn):
    cutoff = (
        datetime.now(timezone.utc)
        -
        timedelta(days=RETENTION_DAYS)
    ).isoformat()

    conn.execute(
        "DELETE FROM visits WHERE ts < ?",
        (cutoff,)
    )


@app.route("/api/status")
def status():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return jsonify({
        "cpu": round(
            psutil.cpu_percent(interval=0.15),
            1
        ),
        "memory": round(
            memory.percent,
            1
        ),
        "disk": round(
            disk.percent,
            1
        ),
        "load": round(
            psutil.getloadavg()[0],
            2
        ),
        "uptime": int(
            time.time()
            -
            psutil.boot_time()
        )
    })


@app.post("/api/visit")
def record_visit():

    data = request.get_json(
        silent=True
    ) or {}

    visit_id = clean(
        data.get("visit_id"),
        80
    )

    if not visit_id:
        return jsonify({
            "ok": False
        }), 400

    now = datetime.now(
        timezone.utc
    ).isoformat()

    ip = real_ip()

    country = clean(
        data.get("country")
        or
        request.headers.get(
            "CF-IPCountry"
        ),
        32
    )

    colo = clean(
        data.get("colo"),
        32
    )

    http_proto = clean(
        data.get("http"),
        32
    )

    tls = clean(
        data.get("tls"),
        64
    )

    warp = clean(
        data.get("warp"),
        16
    )

    path = clean(
        data.get("path"),
        400
    )

    referrer = clean(
        data.get("referrer"),
        1000
    )

    ua = clean(
        request.headers.get(
            "User-Agent"
        ),
        1500
    )

    language = clean(
        data.get("language"),
        64
    )

    timezone_name = clean(
        data.get("timezone"),
        128
    )

    screen = clean(
        data.get("screen"),
        64
    )

    viewport = clean(
        data.get("viewport"),
        64
    )

    platform = clean(
        data.get("platform"),
        128
    )

    conn = db()

    cleanup_old_rows(conn)

    conn.execute("""
    INSERT OR IGNORE INTO visits (
        visit_id,
        ts,
        ip,
        country,
        colo,
        http_proto,
        tls,
        warp,
        path,
        referrer,
        ua,
        language,
        timezone,
        screen,
        viewport,
        platform
    )
    VALUES (
        ?,?,?,?,?,?,?,?,
        ?,?,?,?,?,?,?,?
    )
    """, (
        visit_id,
        now,
        ip,
        country,
        colo,
        http_proto,
        tls,
        warp,
        path,
        referrer,
        ua,
        language,
        timezone_name,
        screen,
        viewport,
        platform
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True
    })


@app.post("/api/visitor-location")
def save_location():

    data = request.get_json(
        silent=True
    ) or {}

    visit_id = clean(
        data.get("visit_id"),
        80
    )

    try:
        lat = float(
            data["lat"]
        )

        lon = float(
            data["lon"]
        )

        accuracy = float(
            data.get(
                "accuracy",
                0
            )
        )

    except (ValueError, TypeError, KeyError):
        return jsonify({
            "ok": False,
            "error": "invalid coordinates"
        }), 400


    if not (
        -90 <= lat <= 90
        and
        -180 <= lon <= 180
    ):
        return jsonify({
            "ok": False,
            "error": "invalid range"
        }), 400


    address = clean(
        data.get("address"),
        1200
    )


    conn = db()

    cur = conn.execute("""
    UPDATE visits
    SET
        lat=?,
        lon=?,
        accuracy=?,
        address=?,
        location_shared=1
    WHERE visit_id=?
    """, (
        lat,
        lon,
        accuracy,
        address,
        visit_id
    ))

    conn.commit()
    conn.close()


    if cur.rowcount == 0:
        return jsonify({
            "ok": False,
            "error": "visit not found"
        }), 404


    return jsonify({
        "ok": True
    })


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):

        if not session.get(
            "ceylan_admin"
        ):
            return redirect(
                url_for(
                    "admin_login"
                )
            )

        return fn(
            *args,
            **kwargs
        )

    return wrapped


LOGIN_TEMPLATE = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>管理员登录 · CeylanCLC</title><style>\n:root{color-scheme:dark;--bg:#090e17;--panel:#101824;--line:#233042;--muted:#8b9cb3;--text:#e7eef8;--accent:#63d5ef;--green:#6cdcb0;--red:#f99595;--amber:#ebc477}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}a{color:inherit;text-decoration:none}button,input,select{font:inherit}button,a,input,summary,select{ -webkit-tap-highlight-color:transparent}a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}button{cursor:pointer}svg{vertical-align:middle}.sidebar{width:226px;position:fixed;inset:0 auto 0 0;border-right:1px solid var(--line);background:#0c131e;display:flex;flex-direction:column;padding:30px 18px;z-index:10}.brand{font-size:20px;font-weight:800;letter-spacing:1px;padding:0 14px}.brand span{color:var(--accent)}.brand-sub{font-size:10px;letter-spacing:2.8px;color:#70849e;margin:4px 14px 44px}.nav-label{font-size:10px;letter-spacing:2px;color:#6f829c;margin:0 14px 12px}.nav-item{display:flex;align-items:center;gap:13px;padding:12px 14px;margin:5px 0;color:#98a8bd;border-radius:8px;font-size:13px}.nav-item:hover{background:#152130;color:white}.nav-item.active{background:#14303b;color:#83e4f7}.nav-item svg{width:18px;height:18px;flex-shrink:0}.nav-bottom{margin-top:auto}.side-node{border:1px solid var(--line);border-radius:10px;padding:14px;margin:20px 4px;color:#a9b7c9;font-size:12px}.dot{display:inline-block;width:6px;height:6px;background:var(--green);border-radius:100%;margin-right:7px}.main{margin-left:226px;padding:0 38px 30px;max-width:2000px}.topbar{height:78px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px}.top-right{display:flex;align-items:center;gap:20px}.avatar{display:grid;place-items:center;width:31px;height:31px;border:1px solid #34505b;border-radius:50%;color:#97d7e5;background:#172730;font-size:12px}.page-heading{display:flex;justify-content:space-between;align-items:center;margin:28px 0 24px;gap:14px}h1{font-size:27px;letter-spacing:-.7px;line-height:1.35;margin:0 0 7px;font-weight:650}h2{font-size:15px;margin:0;font-weight:600}h3{font-size:13px;margin:0}.muted,.sub{color:var(--muted)}.sub{font-size:12px;margin:0}.eyebrow{font-size:10px;letter-spacing:2px;color:#6e869f;margin:0 0 7px}.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.button{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid #314054;color:#c9d5e6;background:#141e2b;border-radius:7px;padding:8px 13px;font-size:12px;white-space:nowrap}.button:hover{border-color:#69a6b8;background:#1a2b39}.button.primary{background:#83dcef;color:#082531;border-color:#83dcef;font-weight:650}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:20px}.stat{border:1px solid var(--line);border-radius:11px;background:var(--panel);padding:18px 20px}.stat-top{display:flex;justify-content:space-between;align-items:center;color:#a4b3c5;font-size:12px}.stat-top svg{color:#65839b;width:17px;height:17px}.stat strong{display:block;font-size:30px;font-weight:600;line-height:1.4;margin:10px 0 4px;font-variant-numeric:tabular-nums;letter-spacing:-1px}.stat small{font-size:11px;color:var(--muted)}.panel{border:1px solid var(--line);border-radius:11px;background:var(--panel);overflow:hidden}.panel-head{display:flex;justify-content:space-between;align-items:center;padding:19px 22px;gap:12px}.panel-head a{font-size:12px;color:var(--accent)}.panel-body{padding:0 22px 20px}.grid{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(300px,1fr);gap:20px;margin-bottom:20px}.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:5px;font-size:10px;white-space:nowrap;background:#1b2937;color:#a8bed0;border:1px solid #2b3b4d}.pill.ok{color:#7cdeb8;background:#133128;border-color:#224b3d}.pill.warn{color:#ebc477;background:#312b1e;border-color:#50432a}.pill.bad{color:#f99595;background:#331f27;border-color:#523039}.chart{width:100%;height:176px;display:block}.chart text{font-family:inherit;fill:#8193aa;font-size:11px}.chart-note{display:flex;justify-content:space-between;padding:4px 4px 0;font-size:11px;color:var(--muted)}.resource{margin-bottom:16px}.resource-line{display:flex;justify-content:space-between;color:#9aabc0;font-size:12px;margin-bottom:8px}.resource-line b{color:#dbe6f4;font-weight:500;font-variant-numeric:tabular-nums}.track{height:5px;background:#263244;border-radius:4px;overflow:hidden}.track i{height:100%;display:block;background:#66c9df;border-radius:4px}.resource:nth-child(2) .track i{background:#9993e9}.resource:nth-child(3) .track i{background:#7db69c}.resource-foot{border-top:1px solid var(--line);padding-top:13px;margin-top:20px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}.bot-summary{display:flex;align-items:center;gap:12px;margin-bottom:22px}.bot-avatar{width:45px;height:45px;display:grid;place-items:center;border:1px solid #344054;border-radius:12px;background:#1d2538;color:#aba6ee}.bot-avatar svg{width:23px;height:23px}.kv{display:flex;justify-content:space-between;gap:20px;padding:10px 0;border-bottom:1px solid #1d2a3b;font-size:12px}.kv:last-child{border-bottom:0}.kv span{color:var(--muted)}.kv b{font-weight:500;text-align:right;overflow-wrap:anywhere;min-width:0}.service-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px}.service{padding:13px 10px;border:1px solid var(--line);border-radius:7px;font-size:11px}.service span{display:block;color:var(--muted);margin-bottom:8px}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap;text-align:left}th{color:#7f92aa;font-size:11px;font-weight:500;background:#111c2a}th,td{padding:13px 20px;border-bottom:1px solid #202c3c}td{color:#bdcadd}tbody tr:hover{background:#14202f}tbody tr:last-child td{border:0}td small{display:block;color:#768ba5;font-size:10px;margin-top:3px}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px}summary{cursor:pointer;color:var(--accent)}.details{white-space:normal;min-width:280px;max-width:380px;padding-top:10px;overflow-wrap:anywhere}.details .kv{display:block}.details .kv b{display:block;text-align:left}.table-footer{border-top:1px solid var(--line);padding:13px 20px;color:#7f92aa;font-size:11px;display:flex;justify-content:space-between}.empty{text-align:center;padding:46px 20px;color:var(--muted);white-space:normal}.empty b{display:block;color:#c0d0df;margin-bottom:8px}.footer{display:flex;justify-content:space-between;color:#586d86;font-size:10px;letter-spacing:1px;margin-top:25px}.search{background:#0d1521;border:1px solid #2c3c50;border-radius:6px;padding:8px 12px;color:var(--text);width:220px;font-size:12px;outline:none}.search:focus{border-color:var(--accent)}.notice{padding:12px 16px;border:1px solid #494030;background:#211f1a;border-radius:8px;color:#d3bc8c;font-size:12px;margin-bottom:20px}.wide{grid-column:1/-1}.login-page{min-height:100vh;display:grid;grid-template-columns:1.1fr 1fr;background:radial-gradient(ellipse at 20% 60%,#102c3b 0,transparent 55%),#090e17}.login-story{position:relative;display:flex;flex-direction:column;justify-content:center;padding:10vw;border-right:1px solid var(--line);overflow:hidden}.login-story:before{content:\'\';width:570px;height:570px;border:1px solid #244454;border-radius:50%;position:absolute;left:-180px;bottom:-180px;box-shadow:0 0 0 75px #18323e40,0 0 0 150px #18323e20;pointer-events:none}.login-story .brand{padding:0;position:absolute;top:45px;left:50px}.login-story h1{font-size:50px;line-height:1.2;letter-spacing:-2px;margin:20px 0;position:relative}.login-story p{color:#91aabe;position:relative;max-width:320px}.login-form-area{display:grid;place-items:center;padding:40px}.login-card{max-width:370px;width:100%}.login-card h1{font-size:26px;margin:12px 0}.login-card label{display:block;font-size:12px;color:#a8b7ca;margin:23px 0 8px}.login-card input{width:100%;height:47px;padding:0 14px;border:1px solid #2c3b4e;border-radius:7px;color:#e6f3ff;background:#111b29;outline:none}.login-card input:focus{border-color:#65c6dd;box-shadow:0 0 0 3px #63d5ef15}.login-card input:-webkit-autofill{-webkit-text-fill-color:#e6f3ff;-webkit-box-shadow:0 0 0 1000px #111b29 inset}.login-card button{width:100%;padding:12px;margin-top:28px}.login-note{font-size:11px;color:#647c96;line-height:1.9;margin-top:25px}.login-back{display:block;color:#9aafc5;font-size:12px;margin-top:30px}.error{color:#ffb3b3;font-size:12px;margin-top:16px}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.mobile-brand{display:none}@media(min-width:1600px){.main{padding-left:50px;padding-right:50px}.chart{height:205px}.stat{padding:22px}}@media(max-width:1150px){.sidebar{width:194px}.main{margin-left:194px;padding:0 24px 24px}.grid{grid-template-columns:minmax(0,1.4fr) minmax(275px,1fr)}.stats{gap:10px}.stat{padding:15px}.stat strong{font-size:26px}}@media(max-width:900px){.grid{grid-template-columns:1fr}.sidebar{width:180px;padding:26px 10px}.main{margin-left:180px;padding:0 20px 20px}.stats{grid-template-columns:repeat(2,1fr)}.top-right time{display:none}.login-story{padding:7vw}.login-story h1{font-size:40px}}@media(max-width:650px){.sidebar{position:static;width:auto;padding:16px;display:block;border-right:0;border-bottom:1px solid var(--line)}.sidebar .brand{font-size:17px;padding:0}.brand-sub,.nav-label,.nav-bottom,.side-node{display:none}.sidebar nav{display:flex;gap:5px;margin-top:15px}.nav-item{margin:0;font-size:12px;padding:9px 10px}.nav-item svg{width:15px;height:15px}.main{margin-left:0;padding:0 16px 24px}.topbar{height:52px}.page-heading{margin:22px 0 19px;align-items:flex-start;flex-wrap:wrap}h1{font-size:24px}.stats{gap:10px}.stat{padding:14px}.stat strong{font-size:28px}.grid{gap:16px;margin-bottom:16px}.panel-head{padding:16px}.panel-body{padding:0 16px 16px}.footer{letter-spacing:0;gap:10px}.login-page{grid-template-columns:1fr}.login-story{min-height:230px;border:0;padding:85px 30px 25px}.login-story .brand{top:25px;left:30px}.login-story h1{font-size:32px;margin:0}.login-story p{margin-bottom:0}.login-story .eyebrow{display:none}.login-form-area{padding:35px 30px 50px}.search{width:100%}.toolbar{width:100%}.chart{height:140px}.chart text{font-size:19px}.table-footer{gap:12px}.top-right{gap:10px}.service-grid{gap:7px}}\n</style></head><body class="login-page"><section class="login-story"><a class="brand" href="/">CEYLAN<span>CLC</span></a><div class="eyebrow">ENGINEERING LAB / ADMIN</div><h1>每个系统，<br>尽在掌握。</h1><p>从网站访客到弹性机器人。<br>在一个工作空间里，了解系统的每一次变化。</p></section><section class="login-form-area"><form class="login-card" method="post"><div class="eyebrow">WELCOME BACK</div><h1>登录控制台</h1><p class="sub">使用管理员账号进入 CeylanCLC 工作空间</p><label for="username">用户名</label><input id="username" name="username" autocomplete="username" required placeholder="输入管理员用户名"><label for="password">密码</label><input id="password" type="password" name="password" autocomplete="current-password" required placeholder="输入密码"><button class="button primary" type="submit">进入控制台\u3000 →</button>{% if error %}<div class="error" role="alert">{{error}}</div>{% endif %}<p class="login-note">仅限管理员访问。访客记录和位置数据受登录保护。</p><a href="/" class="login-back">← 返回 CeylanCLC 网站</a></form></section></body></html>'

LOGIN_TEMPLATE = (LOGIN_TEMPLATE
    .replace('CEYLAN<span>CLC</span>', '{{ site_name }}')
    .replace('CeylanCLC', '{{ site_name }}')
    .replace('弹性机器人', '{{ bot_name }}'))

@app.route(
    "/admin/login",
    methods=[
        "GET",
        "POST"
    ]
)
def admin_login():

    if session.get(
        "ceylan_admin"
    ):
        return redirect(
            url_for(
                "admin_root"
            )
        )


    error = ""

    ip = real_ip()

    now = time.time()

    record = LOGIN_FAILURES.get(
        ip,
        {
            "count": 0,
            "until": 0
        }
    )


    if record["until"] > now:

        remaining = int(
            record["until"]
            -
            now
        )

        error = (
            f"登录失败次数过多，"
            f"请 {remaining} 秒后重试。"
        )

        return render_template_string(
            LOGIN_TEMPLATE,
            error=error, site_name=SITE_DISPLAY_NAME, bot_name=BOT_DISPLAY_NAME
        ), 429


    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        )

        password = request.form.get(
            "password",
            ""
        )


        if (
            username == ADMIN_USER
            and
            check_password_hash(
                ADMIN_HASH,
                password
            )
        ):

            LOGIN_FAILURES.pop(
                ip,
                None
            )

            session.clear()

            session[
                "ceylan_admin"
            ] = True

            session.permanent = True

            return redirect(
                url_for(
                    "admin_root"
                )
            )


        record["count"] += 1

        if record["count"] >= 5:

            record["until"] = (
                now + 600
            )

            record["count"] = 0


        LOGIN_FAILURES[
            ip
        ] = record

        time.sleep(0.7)

        error = "用户名或密码错误。"


    return render_template_string(
        LOGIN_TEMPLATE,
        error=error, site_name=SITE_DISPLAY_NAME, bot_name=BOT_DISPLAY_NAME
    )


@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for(
            "admin_login"
        )
    )



@app.route("/admin")
@admin_required
def admin_root():
    return render_console('overview', OVERVIEW_TEMPLATE, bot=get_bot_status(), detailed=False, **console_data())


def parse_client_ua(ua):
    """
    Human-readable interpretation of User-Agent.
    UA values are self-reported and can be spoofed.
    """

    ua = ua or ""

    low = ua.lower()


    client_type = "普通浏览器"
    client_icon = "👤"
    bot_name = ""
    bot_level = "normal"


    known_bots = [
        (
            "internetarchive",
            "Internet Archive"
        ),
        (
            "archive.org",
            "Internet Archive"
        ),
        (
            "googlebot",
            "Googlebot"
        ),
        (
            "bingbot",
            "Bingbot"
        ),
        (
            "baiduspider",
            "百度蜘蛛"
        ),
        (
            "yandexbot",
            "YandexBot"
        ),
        (
            "duckduckbot",
            "DuckDuckBot"
        ),
        (
            "facebookexternalhit",
            "Facebook Crawler"
        ),
        (
            "twitterbot",
            "Twitterbot"
        ),
        (
            "slurp",
            "Yahoo Slurp"
        ),
    ]


    for marker, name in known_bots:

        if marker in low:

            client_type = "自动化爬虫"
            client_icon = "🤖"
            bot_name = name
            bot_level = "bot"

            break


    if (
        "internetarchive-nod-thumbnailer"
        in low
    ):

        client_type = "自动化爬虫"

        client_icon = "🤖"

        bot_name = (
            "Internet Archive Thumbnailer"
        )

        bot_level = "bot"


    automation_markers = [
        "headlesschrome",
        "phantomjs",
        "selenium",
        "playwright",
        "puppeteer",
        "webdriver",
    ]


    if (
        bot_level == "normal"
        and
        any(
            x in low
            for x in automation_markers
        )
    ):

        client_type = "疑似自动化浏览器"

        client_icon = "⚙️"

        bot_name = (
            "Headless / Automation"
        )

        bot_level = "suspected"


    tool_markers = [
        (
            "curl/",
            "curl"
        ),
        (
            "wget/",
            "wget"
        ),
        (
            "python-requests",
            "Python Requests"
        ),
        (
            "python-httpx",
            "Python HTTPX"
        ),
        (
            "go-http-client",
            "Go HTTP Client"
        ),
    ]


    if bot_level == "normal":

        for marker, name in tool_markers:

            if marker in low:

                client_type = "程序化请求"

                client_icon = "⚙️"

                bot_name = name

                bot_level = "suspected"

                break


    # ------------------------------
    # Browser
    # ------------------------------

    browser = "未知"


    patterns = [
        (
            r'Edg/([\d.]+)',
            "Microsoft Edge"
        ),
        (
            r'OPR/([\d.]+)',
            "Opera"
        ),
        (
            r'Firefox/([\d.]+)',
            "Firefox"
        ),
        (
            r'Chrome/([\d.]+)',
            "Chrome"
        ),
        (
            r'Version/([\d.]+).*Safari/',
            "Safari"
        ),
    ]


    for pattern, name in patterns:

        m = re.search(
            pattern,
            ua,
            re.I
        )

        if m:

            browser = (
                f"{name} {m.group(1)}"
            )

            break


    # ------------------------------
    # OS
    # ------------------------------

    os_name = "未知"


    if "Windows NT 10.0" in ua:

        os_name = (
            "Windows NT 10.0"
        )


    elif "Windows NT 6.3" in ua:

        os_name = (
            "Windows 8.1"
        )


    elif "Windows NT 6.1" in ua:

        os_name = (
            "Windows 7"
        )


    else:

        mac = re.search(
            r'Mac OS X ([\d_]+)',
            ua
        )

        if mac:

            version = (
                mac.group(1)
                .replace(
                    "_",
                    "."
                )
            )

            os_name = (
                f"macOS {version}"
            )


        elif "Android" in ua:

            m = re.search(
                r'Android ([^;)\s]+)',
                ua
            )

            os_name = (
                "Android "
                +
                (
                    m.group(1)
                    if m
                    else ""
                )
            ).strip()


        elif (
            "iPhone" in ua
            or
            "iPad" in ua
        ):

            m = re.search(
                r'OS ([\d_]+)',
                ua
            )

            if m:

                version = (
                    m.group(1)
                    .replace(
                        "_",
                        "."
                    )
                )

                os_name = (
                    f"iOS/iPadOS {version}"
                )

            else:

                os_name = (
                    "iOS / iPadOS"
                )


        elif "Linux" in ua:

            os_name = "Linux"


    return {
        "client_type":
            client_type,

        "client_icon":
            client_icon,

        "bot_name":
            bot_name,

        "bot_level":
            bot_level,

        "browser_name":
            browser,

        "os_name":
            os_name,

        "is_bot":
            bot_level
            in (
                "bot",
                "suspected"
            )
    }


@app.route("/admin/visitors")
@admin_required
def admin_visitors():
    return render_console('visitors', VISITORS_TEMPLATE, detailed=True, **console_data(300))


@app.route("/admin/export.csv")
@admin_required
def admin_export():

    conn = db()

    rows = conn.execute("""
    SELECT *
    FROM visits
    WHERE ts >= ?
    ORDER BY id DESC
    """, ((datetime.now(timezone.utc)-timedelta(days=RETENTION_DAYS)).isoformat(),)).fetchall()

    conn.close()


    output = io.StringIO()

    writer = csv.writer(
        output
    )


    writer.writerow([
        "time_utc",
        "ip",
        "country",
        "cloudflare_edge",
        "path",
        "referrer",
        "platform",
        "language",
        "timezone",
        "screen",
        "viewport",
        "latitude",
        "longitude",
        "accuracy_m",
        "address",
        "location_shared",
        "user_agent"
    ])


    for r in rows:

        writer.writerow([
            r["ts"],
            r["ip"],
            r["country"],
            r["colo"],
            r["path"],
            r["referrer"],
            r["platform"],
            r["language"],
            r["timezone"],
            r["screen"],
            r["viewport"],
            r["lat"],
            r["lon"],
            r["accuracy"],
            r["address"],
            r["location_shared"],
            r["ua"]
        ])


    return Response(
        "\ufeff"
        +
        output.getvalue(),
        mimetype=(
            "text/csv;"
            "charset=utf-8"
        ),
        headers={
            "Content-Disposition":
            "attachment;"
            "filename=ceylan-visitors.csv"
        }
    )


import json
import hmac
import math
import tempfile

BOT_STATUS_FILE = os.environ.get('CEYLAN_BOT_STATUS_FILE', '/var/lib/ceylan-status/bot_status.json')
BOT_STALE_SECONDS = 180

def get_bot_status():
    keys = ('qq', 'model', 'memory', 'patrol', 'last_message', 'last_patrol', 'tokens', 'groups', 'memory_count', 'cpu', 'memory_percent', 'uptime', 'received_at', 'online')
    result = dict.fromkeys(keys)
    raw = {}
    try:
        with open(BOT_STATUS_FILE, encoding='utf-8') as f:
            value = json.load(f)
            if isinstance(value, dict):
                raw = value
    except (OSError, ValueError):
        pass
    result.update({k: raw.get(k) for k in keys})
    result['fresh'] = False
    result['updated_label'] = '尚未收到心跳'
    try:
        received = datetime.fromisoformat(raw['received_at'])
        age = (datetime.now(timezone.utc) - received).total_seconds()
        result['fresh'] = 0 <= age <= BOT_STALE_SECONDS
        result['updated_label'] = received.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S')
    except (KeyError, ValueError, TypeError):
        pass
    result['label'] = '正常接收' if result['fresh'] else ('心跳超时' if result['received_at'] else '等待接入')
    result['tone'] = 'ok' if result['fresh'] else 'warn'
    states = {'online': '运行中', 'offline': '未运行', 'unknown': '未上报', 'error': '异常'}
    result['components'] = {k: states.get(raw.get(k), '未上报') if isinstance(raw.get(k), str) else '未上报' for k in ('napcat', 'bridge', 'openclaw')}
    result['qq_label'] = ('在线' if raw.get('online') is True else '离线' if raw.get('online') is False else '未上报') if result['fresh'] else '待确认'
    return result

@app.post('/api/bot/status')
def receive_bot_status():
    token = os.environ.get('CEYLAN_BOT_TOKEN', '')
    if len(token) < 32:
        return jsonify(ok=False, error='heartbeat receiver not configured'), 503
    supplied = request.headers.get('Authorization', '')
    if not hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode()):
        return jsonify(ok=False, error='unauthorized'), 401
    if request.content_length is not None and request.content_length > 16384:
        return jsonify(ok=False, error='payload too large'), 413
    # Limit reads even for chunked requests without a Content-Length header.
    payload = request.stream.read(16385)
    if len(payload) > 16384:
        return jsonify(ok=False, error='payload too large'), 413
    if not request.is_json:
        return jsonify(ok=False, error='JSON required'), 415
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return jsonify(ok=False, error='invalid JSON'), 400
    if not isinstance(data, dict):
        return jsonify(ok=False, error='object required'), 400
    data.pop('last_update', None)
    for key in ('napcat', 'bridge', 'openclaw'):
        if data.get(key) == 'running':
            data[key] = 'online'
    allowed = {'online', 'qq', 'model', 'napcat', 'bridge', 'openclaw', 'memory', 'patrol', 'last_message', 'last_patrol', 'groups', 'memory_count', 'tokens', 'cpu', 'memory_percent', 'uptime'}
    if set(data) - allowed:
        return jsonify(ok=False, error='unknown fields'), 400
    for key, value in data.items():
        if value is None:
            continue
        if key == 'online':
            valid = type(value) is bool
        elif key in ('napcat', 'bridge', 'openclaw'):
            valid = isinstance(value, str) and value in ('online', 'offline', 'unknown', 'error')
        elif key in ('groups', 'memory_count', 'tokens', 'uptime'):
            valid = type(value) is int and 0 <= value <= 10**15
        elif key in ('cpu', 'memory_percent'):
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100
        else:
            valid = isinstance(value, str) and len(value) <= 300
        if not valid:
            return jsonify(ok=False, error='invalid field: ' + key), 400
    data['received_at'] = datetime.now(timezone.utc).isoformat()
    temp_path = None
    try:
        # One atomic snapshot, safe for concurrent Gunicorn readers.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(BOT_STATUS_FILE), prefix='.bot-', delete=False) as f:
            temp_path = f.name
            json.dump(data, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, BOT_STATUS_FILE)
    except OSError:
        app.logger.exception('Could not save bot heartbeat')
        return jsonify(ok=False, error='status storage unavailable'), 503
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return jsonify(ok=True, received_at=data['received_at'])

@app.after_request
def protect_admin_response(response):
    if request.path.startswith('/admin') or request.path == '/api/bot/status':
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
    return response

def console_data(limit=6):
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    conn = db()
    try:
        stats = dict(conn.execute('''SELECT COUNT(*) AS total, COUNT(DISTINCT ip) AS unique_ips,
            COALESCE(SUM(location_shared=1),0) AS locations,
            COALESCE(SUM(ts>=?),0) AS today FROM visits WHERE ts>=?''',
            ((now-timedelta(hours=24)).isoformat(), cutoff)).fetchone())
        raw = conn.execute('SELECT * FROM visits WHERE ts>=? ORDER BY id DESC LIMIT ?', (cutoff, limit)).fetchall()
        zone = ZoneInfo('Asia/Shanghai')
        today = now.astimezone(zone).date()
        first = today - timedelta(days=6)
        start = datetime.combine(first, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc).isoformat()
        counts = {r['day']: r['n'] for r in conn.execute("SELECT date(ts,'+8 hours') AS day, COUNT(*) AS n FROM visits WHERE ts>=? GROUP BY day", (start,))}
    finally:
        conn.close()
    rows = []
    for raw_row in raw:
        row = dict(raw_row)
        try:
            row['time_cn'] = datetime.fromisoformat(row['ts']).astimezone(zone).strftime('%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            row['time_cn'] = row['ts']
        row.update(parse_client_ua(row.get('ua')))
        rows.append(row)
    plot = []
    values = [counts.get((first+timedelta(days=i)).isoformat(), 0) for i in range(7)]
    peak = max(values, default=0)
    for i, value in enumerate(values):
        date = first + timedelta(days=i)
        plot.append(dict(x=40+i*96, y=round(141-123*value/max(1,peak),2), value=value, label=date.strftime('%m/%d'), date=date.isoformat()))
    points = ' '.join(f"{p['x']},{p['y']}" for p in plot)
    area = 'M40,141 L' + ' L'.join(f"{p['x']},{p['y']}" for p in plot) + ' L616,141 Z'
    return dict(stats=stats, rows=rows, trend=dict(plot=plot,points=points,area=area,peak=peak,total=sum(values)))

def render_console(active, body, **context):
    metadata = {
        'overview': ('控制台总览', 'OVERVIEW', '网站访问、服务器资源与机器人状态，一览可见。'),
        'visitors': ('访客分析', 'AUDIENCE', '了解访问来源、客户端与访客主动共享的位置。'),
        'bot': (BOT_DISPLAY_NAME, SITE_TAGLINE, '查看机器人节点心跳、服务状态与业务指标。'),
    }
    title, eyebrow, subtitle = metadata[active]
    return render_template_string(CONSOLE_BASE.replace('__BODY__', body).replace('__SCRIPT__', CONSOLE_SCRIPT),
        title=title, eyebrow=eyebrow, subtitle=subtitle, active=active,
        now_label=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M'),
        icons=CONSOLE_ICONS, retention=RETENTION_DAYS, site_name=SITE_DISPLAY_NAME,
        bot_name=BOT_DISPLAY_NAME, bot_node_label=BOT_NODE_LABEL, **context)

@app.route('/admin/bot')
@admin_required
def admin_bot():
    return render_console('bot', BOT_TEMPLATE, bot=get_bot_status())


CONSOLE_BASE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }} · CeylanCLC</title><style>
:root{color-scheme:dark;--bg:#090e17;--panel:#101824;--line:#233042;--muted:#8b9cb3;--text:#e7eef8;--accent:#63d5ef;--green:#6cdcb0;--red:#f99595;--amber:#ebc477}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}a{color:inherit;text-decoration:none}button,input,select{font:inherit}button,a,input,summary,select{ -webkit-tap-highlight-color:transparent}a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}button{cursor:pointer}svg{vertical-align:middle}.sidebar{width:226px;position:fixed;inset:0 auto 0 0;border-right:1px solid var(--line);background:#0c131e;display:flex;flex-direction:column;padding:30px 18px;z-index:10}.brand{font-size:20px;font-weight:800;letter-spacing:1px;padding:0 14px}.brand span{color:var(--accent)}.brand-sub{font-size:10px;letter-spacing:2.8px;color:#70849e;margin:4px 14px 44px}.nav-label{font-size:10px;letter-spacing:2px;color:#6f829c;margin:0 14px 12px}.nav-item{display:flex;align-items:center;gap:13px;padding:12px 14px;margin:5px 0;color:#98a8bd;border-radius:8px;font-size:13px}.nav-item:hover{background:#152130;color:white}.nav-item.active{background:#14303b;color:#83e4f7}.nav-item svg{width:18px;height:18px;flex-shrink:0}.nav-bottom{margin-top:auto}.side-node{border:1px solid var(--line);border-radius:10px;padding:14px;margin:20px 4px;color:#a9b7c9;font-size:12px}.dot{display:inline-block;width:6px;height:6px;background:var(--green);border-radius:100%;margin-right:7px}.main{margin-left:226px;padding:0 38px 30px;max-width:2000px}.topbar{height:78px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px}.top-right{display:flex;align-items:center;gap:20px}.avatar{display:grid;place-items:center;width:31px;height:31px;border:1px solid #34505b;border-radius:50%;color:#97d7e5;background:#172730;font-size:12px}.page-heading{display:flex;justify-content:space-between;align-items:center;margin:28px 0 24px;gap:14px}h1{font-size:27px;letter-spacing:-.7px;line-height:1.35;margin:0 0 7px;font-weight:650}h2{font-size:15px;margin:0;font-weight:600}h3{font-size:13px;margin:0}.muted,.sub{color:var(--muted)}.sub{font-size:12px;margin:0}.eyebrow{font-size:10px;letter-spacing:2px;color:#6e869f;margin:0 0 7px}.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.button{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid #314054;color:#c9d5e6;background:#141e2b;border-radius:7px;padding:8px 13px;font-size:12px;white-space:nowrap}.button:hover{border-color:#69a6b8;background:#1a2b39}.button.primary{background:#83dcef;color:#082531;border-color:#83dcef;font-weight:650}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:20px}.stat{border:1px solid var(--line);border-radius:11px;background:var(--panel);padding:18px 20px}.stat-top{display:flex;justify-content:space-between;align-items:center;color:#a4b3c5;font-size:12px}.stat-top svg{color:#65839b;width:17px;height:17px}.stat strong{display:block;font-size:30px;font-weight:600;line-height:1.4;margin:10px 0 4px;font-variant-numeric:tabular-nums;letter-spacing:-1px}.stat small{font-size:11px;color:var(--muted)}.panel{border:1px solid var(--line);border-radius:11px;background:var(--panel);overflow:hidden}.panel-head{display:flex;justify-content:space-between;align-items:center;padding:19px 22px;gap:12px}.panel-head a{font-size:12px;color:var(--accent)}.panel-body{padding:0 22px 20px}.grid{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(300px,1fr);gap:20px;margin-bottom:20px}.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:5px;font-size:10px;white-space:nowrap;background:#1b2937;color:#a8bed0;border:1px solid #2b3b4d}.pill.ok{color:#7cdeb8;background:#133128;border-color:#224b3d}.pill.warn{color:#ebc477;background:#312b1e;border-color:#50432a}.pill.bad{color:#f99595;background:#331f27;border-color:#523039}.chart{width:100%;height:176px;display:block}.chart text{font-family:inherit;fill:#8193aa;font-size:11px}.chart-note{display:flex;justify-content:space-between;padding:4px 4px 0;font-size:11px;color:var(--muted)}.resource{margin-bottom:16px}.resource-line{display:flex;justify-content:space-between;color:#9aabc0;font-size:12px;margin-bottom:8px}.resource-line b{color:#dbe6f4;font-weight:500;font-variant-numeric:tabular-nums}.track{height:5px;background:#263244;border-radius:4px;overflow:hidden}.track i{height:100%;display:block;background:#66c9df;border-radius:4px}.resource:nth-child(2) .track i{background:#9993e9}.resource:nth-child(3) .track i{background:#7db69c}.resource-foot{border-top:1px solid var(--line);padding-top:13px;margin-top:20px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}.bot-summary{display:flex;align-items:center;gap:12px;margin-bottom:22px}.bot-avatar{width:45px;height:45px;display:grid;place-items:center;border:1px solid #344054;border-radius:12px;background:#1d2538;color:#aba6ee}.bot-avatar svg{width:23px;height:23px}.kv{display:flex;justify-content:space-between;gap:20px;padding:10px 0;border-bottom:1px solid #1d2a3b;font-size:12px}.kv:last-child{border-bottom:0}.kv span{color:var(--muted)}.kv b{font-weight:500;text-align:right;overflow-wrap:anywhere;min-width:0}.service-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px}.service{padding:13px 10px;border:1px solid var(--line);border-radius:7px;font-size:11px}.service span{display:block;color:var(--muted);margin-bottom:8px}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap;text-align:left}th{color:#7f92aa;font-size:11px;font-weight:500;background:#111c2a}th,td{padding:13px 20px;border-bottom:1px solid #202c3c}td{color:#bdcadd}tbody tr:hover{background:#14202f}tbody tr:last-child td{border:0}td small{display:block;color:#768ba5;font-size:10px;margin-top:3px}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px}summary{cursor:pointer;color:var(--accent)}.details{white-space:normal;min-width:280px;max-width:380px;padding-top:10px;overflow-wrap:anywhere}.details .kv{display:block}.details .kv b{display:block;text-align:left}.table-footer{border-top:1px solid var(--line);padding:13px 20px;color:#7f92aa;font-size:11px;display:flex;justify-content:space-between}.empty{text-align:center;padding:46px 20px;color:var(--muted);white-space:normal}.empty b{display:block;color:#c0d0df;margin-bottom:8px}.footer{display:flex;justify-content:space-between;color:#586d86;font-size:10px;letter-spacing:1px;margin-top:25px}.search{background:#0d1521;border:1px solid #2c3c50;border-radius:6px;padding:8px 12px;color:var(--text);width:220px;font-size:12px;outline:none}.search:focus{border-color:var(--accent)}.notice{padding:12px 16px;border:1px solid #494030;background:#211f1a;border-radius:8px;color:#d3bc8c;font-size:12px;margin-bottom:20px}.wide{grid-column:1/-1}.login-page{min-height:100vh;display:grid;grid-template-columns:1.1fr 1fr;background:radial-gradient(ellipse at 20% 60%,#102c3b 0,transparent 55%),#090e17}.login-story{position:relative;display:flex;flex-direction:column;justify-content:center;padding:10vw;border-right:1px solid var(--line);overflow:hidden}.login-story:before{content:'';width:570px;height:570px;border:1px solid #244454;border-radius:50%;position:absolute;left:-180px;bottom:-180px;box-shadow:0 0 0 75px #18323e40,0 0 0 150px #18323e20;pointer-events:none}.login-story .brand{padding:0;position:absolute;top:45px;left:50px}.login-story h1{font-size:50px;line-height:1.2;letter-spacing:-2px;margin:20px 0;position:relative}.login-story p{color:#91aabe;position:relative;max-width:320px}.login-form-area{display:grid;place-items:center;padding:40px}.login-card{max-width:370px;width:100%}.login-card h1{font-size:26px;margin:12px 0}.login-card label{display:block;font-size:12px;color:#a8b7ca;margin:23px 0 8px}.login-card input{width:100%;height:47px;padding:0 14px;border:1px solid #2c3b4e;border-radius:7px;color:#e6f3ff;background:#111b29;outline:none}.login-card input:focus{border-color:#65c6dd;box-shadow:0 0 0 3px #63d5ef15}.login-card input:-webkit-autofill{-webkit-text-fill-color:#e6f3ff;-webkit-box-shadow:0 0 0 1000px #111b29 inset}.login-card button{width:100%;padding:12px;margin-top:28px}.login-note{font-size:11px;color:#647c96;line-height:1.9;margin-top:25px}.login-back{display:block;color:#9aafc5;font-size:12px;margin-top:30px}.error{color:#ffb3b3;font-size:12px;margin-top:16px}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.mobile-brand{display:none}@media(min-width:1600px){.main{padding-left:50px;padding-right:50px}.chart{height:205px}.stat{padding:22px}}@media(max-width:1150px){.sidebar{width:194px}.main{margin-left:194px;padding:0 24px 24px}.grid{grid-template-columns:minmax(0,1.4fr) minmax(275px,1fr)}.stats{gap:10px}.stat{padding:15px}.stat strong{font-size:26px}}@media(max-width:900px){.grid{grid-template-columns:1fr}.sidebar{width:180px;padding:26px 10px}.main{margin-left:180px;padding:0 20px 20px}.stats{grid-template-columns:repeat(2,1fr)}.top-right time{display:none}.login-story{padding:7vw}.login-story h1{font-size:40px}}@media(max-width:650px){.sidebar{position:static;width:auto;padding:16px;display:block;border-right:0;border-bottom:1px solid var(--line)}.sidebar .brand{font-size:17px;padding:0}.brand-sub,.nav-label,.nav-bottom,.side-node{display:none}.sidebar nav{display:flex;gap:5px;margin-top:15px}.nav-item{margin:0;font-size:12px;padding:9px 10px}.nav-item svg{width:15px;height:15px}.main{margin-left:0;padding:0 16px 24px}.topbar{height:52px}.page-heading{margin:22px 0 19px;align-items:flex-start;flex-wrap:wrap}h1{font-size:24px}.stats{gap:10px}.stat{padding:14px}.stat strong{font-size:28px}.grid{gap:16px;margin-bottom:16px}.panel-head{padding:16px}.panel-body{padding:0 16px 16px}.footer{letter-spacing:0;gap:10px}.login-page{grid-template-columns:1fr}.login-story{min-height:230px;border:0;padding:85px 30px 25px}.login-story .brand{top:25px;left:30px}.login-story h1{font-size:32px;margin:0}.login-story p{margin-bottom:0}.login-story .eyebrow{display:none}.login-form-area{padding:35px 30px 50px}.search{width:100%}.toolbar{width:100%}.chart{height:140px}.chart text{font-size:19px}.table-footer{gap:12px}.top-right{gap:10px}.service-grid{gap:7px}}
</style></head><body><aside class="sidebar"><a class="brand" href="/admin">CEYLAN<span>CLC</span></a><div class="brand-sub">ENGINEERING CONSOLE</div><div class="nav-label">WORKSPACE</div><nav>{% for key,label,href in [('overview','总览','/admin'),('visitors','访客分析','/admin/visitors'),('bot','弹性机器人','/admin/bot')] %}<a class="nav-item {{ 'active' if active==key else '' }}" href="{{href}}" {% if active==key %}aria-current="page"{% endif %}>{{ icons[{'overview':'grid','visitors':'visitors','bot':'bot'}[key]]|safe }}{{label}}</a>{% endfor %}</nav><div class="nav-bottom"><div class="side-node"><span class="dot"></span> CeylanCLC 主节点<div class="sub" style="margin-top:5px">Debian · 网站服务</div></div><a class="nav-item" href="/">{{icons.home|safe}}返回网站</a><a class="nav-item" href="/admin/logout">{{icons.logout|safe}}退出登录</a></div></aside><main class="main"><header class="topbar"><span>工作空间 <span style="padding:0 10px;color:#44566d">/</span> {{title}}</span><div class="top-right"><time>{{now_label}} CST</time><a href="/admin/logout" title="退出登录">退出</a><span class="avatar" title="管理员">C</span></div></header>__BODY__<footer class="footer"><span>CEYLANCLC / ENGINEERING LAB</span><span>数据截至 {{now_label}} · 中国标准时间</span></footer></main>__SCRIPT__</body></html>'''

CONSOLE_ICONS = {'grid': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>', 'visitors': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 20v-2a5 5 0 0 1 10 0v2M16 13a5 5 0 0 1 5 5v2"/><circle cx="8" cy="7" r="4"/><path d="M16 3a4 4 0 0 1 0 8"/></svg>', 'bot': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="7" width="16" height="14" rx="4"/><path d="M12 3v4M8 13v2M16 13v2M9 18h6M1 11v6M23 11v6"/></svg>', 'arrow': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>', 'export': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></svg>', 'home': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 10 9-7 9 7v11H3ZM9 21v-8h6v8"/></svg>', 'logout': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3H3v18h6M9 12h12m-5-5 5 5-5 5"/></svg>', 'pulse': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12h5l3-8 4 16 3-8h5"/></svg>', 'globe': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg>', 'clock': '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></svg>'}

CONSOLE_SCRIPT = r'''<script>
const search=document.getElementById('visit-search');if(search){search.addEventListener('input',()=>{const q=search.value.trim().toLocaleLowerCase();let n=0;document.querySelectorAll('[data-visit]').forEach(r=>{r.hidden=!r.dataset.search.toLocaleLowerCase().includes(q);if(!r.hidden)n++});document.getElementById('row-count').textContent=n+' 条匹配记录';document.getElementById('no-results').hidden=n!==0;});}
async function loadResources(){if(!document.getElementById('resource-status'))return;try{const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error('status');const s=await response.json();for(const k of ['cpu','memory','disk']){if(!Number.isFinite(s[k]))throw Error('data');document.getElementById(k+'-value').textContent=s[k]+'%';document.getElementById(k+'-bar').style.width=Math.max(0,Math.min(100,s[k]))+'%';}document.getElementById('uptime').textContent=Math.floor(s.uptime/86400)+'天 '+Math.floor(s.uptime%86400/3600)+'小时';document.getElementById('load').textContent=s.load;const tag=document.getElementById('resource-status');tag.textContent='实时 · 15s';tag.className='pill ok';}catch(e){const tag=document.getElementById('resource-status');tag.textContent='读取失败';tag.className='pill warn';for(const k of ['cpu','memory','disk']){document.getElementById(k+'-value').textContent='—';document.getElementById(k+'-bar').style.width='0';}document.getElementById('uptime').textContent='—';document.getElementById('load').textContent='—';}}
loadResources();setInterval(()=>{if(!document.hidden)loadResources()},15000);
</script>'''

OVERVIEW_TEMPLATE = r'''<section class="page-heading"><div><p class="eyebrow">{{eyebrow}}</p><h1>{{title}}</h1><p class="sub">{{subtitle}}</p></div><div class="actions"><a class="button" href="{{request.path}}">刷新数据</a><a class="button primary" href="/admin/export.csv">{{icons.export|safe}}导出 CSV</a></div></section><section class="stats">{% for label,value,note,ic in [('近 24 小时访问',stats.today,'滚动 24 小时 · 含爬虫请求','pulse'),('30 天访问记录',stats.total,'按访问记录计数','visitors'),('独立公网 IP',stats.unique_ips,'近 30 天 · 按公网 IP 去重','globe'),('主动共享位置',stats.locations,'访客主动授权的记录','clock')] %}<article class="stat"><div class="stat-top">{{label}}{{icons[ic]|safe}}</div><strong>{{value}}</strong><small>{{note}}</small></article>{% endfor %}</section><div class="grid"><section class="panel"><div class="panel-head"><div><h2>访问趋势</h2><p class="sub">最近 7 天 · 每日访问记录</p></div><span class="pill">中国标准时间</span></div><div class="panel-body">{% if trend.total %}<svg class="chart" viewBox="0 0 650 176" preserveAspectRatio="none" role="img" aria-label="最近七天访问量趋势"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#5bd5ef" stop-opacity=".17"/><stop offset="1" stop-color="#5bd5ef" stop-opacity="0"/></linearGradient></defs>{% for y in [18,59,100,141] %}<line x1="35" x2="634" y1="{{y}}" y2="{{y}}" stroke="#233143" stroke-dasharray="3 5"/>{% endfor %}<text x="0" y="22">{{trend.peak}}</text><text x="8" y="145">0</text><path d="{{trend.area}}" fill="url(#area)"/><polyline points="{{trend.points}}" fill="none" stroke="#6adbf0" stroke-width="2.5" vector-effect="non-scaling-stroke"/>{% for p in trend.plot %}<circle cx="{{p.x}}" cy="{{p.y}}" r="3.5" fill="#0f1824" stroke="#6adbf0" stroke-width="2"><title>{{p.date}}：{{p.value}} 次访问</title></circle><text x="{{p.x}}" y="170" text-anchor="middle">{{p.label}}</text>{% endfor %}</svg>{% else %}<div class="empty"><b>近 7 天暂无访问数据</b>收到访问记录后将自动生成趋势。</div>{% endif %}<div class="chart-note"><span>合计 {{trend.total}} 次访问</span><span>每日峰值 {{trend.peak}}</span></div></div></section><section class="panel"><div class="panel-head"><h2>服务器资源</h2><span id="resource-status" class="pill">读取中</span></div><div class="panel-body">{% for key,label in [('cpu','CPU 使用率'),('memory','内存使用率'),('disk','磁盘使用率')] %}<div class="resource"><div class="resource-line"><span>{{label}}</span><b id="{{key}}-value">—</b></div><div class="track"><i id="{{key}}-bar" style="width:0"></i></div></div>{% endfor %}<div class="resource-foot"><span>运行 <b id="uptime">—</b></span><span>负载 <b id="load">—</b></span></div></div></section><section class="panel"><div class="panel-head"><div><h2>最近访客</h2><p class="sub">最新 6 条访问记录</p></div><a href="/admin/visitors">全部记录 ↗</a></div><div class="table-scroll"><table><thead><tr><th>访问时间</th><th>公网 IP / 地区</th><th>客户端</th><th>访问页面</th><th>访问类型</th>{% if detailed %}<th>详情</th>{% endif %}</tr></thead><tbody>{% for r in rows %}<tr data-visit data-search="{{(r.ip or '') ~ ' ' ~ (r.country or '') ~ ' ' ~ r.browser_name ~ ' ' ~ r.os_name ~ ' ' ~ (r.path or '') ~ ' ' ~ r.client_type}}"><td>{{r.time_cn}}<small>中国标准时间</small></td><td><span class="mono">{{r.ip or '—'}}</span><small>{{r.country or '未知地区'}}{% if r.colo %} · {{r.colo}}{% endif %}</small></td><td>{{r.browser_name}}<small>{{r.os_name}}</small></td><td><span class="mono">{{(r.path or '/')[:42]}}</span></td><td><span class="pill {{'warn' if r.bot_level in ['bot','suspected'] else ''}}">{{r.client_type}}</span></td>{% if detailed %}<td><details><summary>查看详情</summary><div class="details">{% for key,label in [('path','完整路径'),('referrer','来源'),('ua','User-Agent'),('language','语言'),('timezone','时区'),('screen','屏幕'),('viewport','视口'),('platform','平台'),('colo','Cloudflare 节点'),('http_proto','HTTP 协议'),('tls','TLS'),('warp','WARP'),('bot_name','爬虫名称')] %}<div class="kv"><span>{{label}}</span><b>{{r[key] or '—'}}</b></div>{% endfor %}<div class="kv"><span>位置共享</span><b>{{'已主动授权' if r.location_shared else '未共享'}}</b></div>{% if r.location_shared %}<div class="kv"><span>纬度 / 经度 / 精度</span><b>{{r.lat}} / {{r.lon}} / {{r.accuracy}} m</b></div><div class="kv"><span>地址</span><b>{{r.address or '—'}}</b></div>{% endif %}</div></details></td>{% endif %}</tr>{% else %}<tr><td colspan="{{6 if detailed else 5}}" class="empty"><b>暂时没有访问记录</b>网站收到新的访问上报后，记录会显示在这里。</td></tr>{% endfor %}</tbody></table></div><div class="table-footer"><span>含自动化与爬虫访问</span><span>记录保留 {{retention}} 天</span></div></section><section class="panel"><div class="panel-head"><h2>弹性机器人</h2><a href="/admin/bot">查看详情 ↗</a></div><div class="panel-body"><div class="bot-summary"><div class="bot-avatar">{{icons.bot|safe}}</div><div><h3>弹性 / QQ AI</h3><div class="sub">腾讯云 · 机器人节点</div></div><span class="pill {{bot.tone}}" style="margin-left:auto">{{bot.label}}</span></div><div class="kv"><span>当前模型</span><b>{{bot.model or '未上报'}}</b></div><div class="kv"><span>QQ 账号</span><b>{{bot.qq or '未上报'}}</b></div><div class="kv"><span>最后心跳</span><b>{{bot.updated_label}}</b></div><div class="service-grid">{% for key,label in [('napcat','NapCat'),('bridge','QQ Bridge'),('openclaw','OpenClaw')] %}<div class="service"><span>{{label}}</span>{{bot.components[key]}}</div>{% endfor %}</div></div></section></div>'''

VISITORS_TEMPLATE = r'''<section class="page-heading"><div><p class="eyebrow">{{eyebrow}}</p><h1>{{title}}</h1><p class="sub">{{subtitle}}</p></div><div class="actions"><a class="button" href="{{request.path}}">刷新数据</a><a class="button primary" href="/admin/export.csv">{{icons.export|safe}}导出 CSV</a></div></section><section class="stats">{% for label,value,note,ic in [('近 24 小时访问',stats.today,'滚动 24 小时 · 含爬虫请求','pulse'),('30 天访问记录',stats.total,'按访问记录计数','visitors'),('独立公网 IP',stats.unique_ips,'近 30 天 · 按公网 IP 去重','globe'),('主动共享位置',stats.locations,'访客主动授权的记录','clock')] %}<article class="stat"><div class="stat-top">{{label}}{{icons[ic]|safe}}</div><strong>{{value}}</strong><small>{{note}}</small></article>{% endfor %}</section><section class="panel"><div class="panel-head" style="flex-wrap:wrap"><div><h2>访问明细</h2><p class="sub">最近 300 条 · 客户端类型基于 UA 推断</p></div><div class="toolbar"><input id="visit-search" class="search" type="search" placeholder="搜索 IP、地区、设备或路径" aria-label="搜索当前访客记录"></div></div><div class="table-scroll"><table><thead><tr><th>访问时间</th><th>公网 IP / 地区</th><th>客户端</th><th>访问页面</th><th>访问类型</th>{% if detailed %}<th>详情</th>{% endif %}</tr></thead><tbody>{% for r in rows %}<tr data-visit data-search="{{(r.ip or '') ~ ' ' ~ (r.country or '') ~ ' ' ~ r.browser_name ~ ' ' ~ r.os_name ~ ' ' ~ (r.path or '') ~ ' ' ~ r.client_type}}"><td>{{r.time_cn}}<small>中国标准时间</small></td><td><span class="mono">{{r.ip or '—'}}</span><small>{{r.country or '未知地区'}}{% if r.colo %} · {{r.colo}}{% endif %}</small></td><td>{{r.browser_name}}<small>{{r.os_name}}</small></td><td><span class="mono">{{(r.path or '/')[:42]}}</span></td><td><span class="pill {{'warn' if r.bot_level in ['bot','suspected'] else ''}}">{{r.client_type}}</span></td>{% if detailed %}<td><details><summary>查看详情</summary><div class="details">{% for key,label in [('path','完整路径'),('referrer','来源'),('ua','User-Agent'),('language','语言'),('timezone','时区'),('screen','屏幕'),('viewport','视口'),('platform','平台'),('colo','Cloudflare 节点'),('http_proto','HTTP 协议'),('tls','TLS'),('warp','WARP'),('bot_name','爬虫名称')] %}<div class="kv"><span>{{label}}</span><b>{{r[key] or '—'}}</b></div>{% endfor %}<div class="kv"><span>位置共享</span><b>{{'已主动授权' if r.location_shared else '未共享'}}</b></div>{% if r.location_shared %}<div class="kv"><span>纬度 / 经度 / 精度</span><b>{{r.lat}} / {{r.lon}} / {{r.accuracy}} m</b></div><div class="kv"><span>地址</span><b>{{r.address or '—'}}</b></div>{% endif %}</div></details></td>{% endif %}</tr>{% else %}<tr><td colspan="{{6 if detailed else 5}}" class="empty"><b>暂时没有访问记录</b>网站收到新的访问上报后，记录会显示在这里。</td></tr>{% endfor %}</tbody></table></div><div id="no-results" class="empty" hidden>当前记录中没有匹配结果</div><div class="table-footer"><span id="row-count">{{rows|length}} 条记录</span><span>数据保留 {{retention}} 天 · 导出包含保留期内全部记录</span></div></section>'''

BOT_TEMPLATE = r'''<section class="page-heading"><div><p class="eyebrow">{{eyebrow}}</p><h1>{{title}}</h1><p class="sub">{{subtitle}}</p></div><div class="actions"><a class="button" href="{{request.path}}">刷新数据</a><a class="button primary" href="/admin/export.csv">{{icons.export|safe}}导出 CSV</a></div></section>{% if not bot.fresh %}<div class="notice">{{ '心跳已超时，下方内容为上次上报快照，不能代表当前在线状态。' if bot.received_at else '等待机器人节点首次上报。连接后，这里将显示最新服务状态。' }}</div>{% endif %}<section class="stats">{% for label,value,note,ic in [('心跳状态',bot.label,bot.updated_label,'pulse'),('QQ 连接',bot.qq_label,bot.qq or '账号未上报','bot'),('已加入群',bot.groups if bot.groups is not none else '—','由机器人业务状态上报','visitors'),('长期记忆数量',bot.memory_count if bot.memory_count is not none else '—','由机器人业务状态上报','grid')] %}<article class="stat"><div class="stat-top">{{label}}{{icons[ic]|safe}}</div><strong style="font-size:24px">{{value}}</strong><small>{{note}}</small></article>{% endfor %}</section><div class="grid"><section class="panel"><div class="panel-head"><h2>弹性机器人</h2><a href="/admin/bot">查看详情 ↗</a></div><div class="panel-body"><div class="bot-summary"><div class="bot-avatar">{{icons.bot|safe}}</div><div><h3>弹性 / QQ AI</h3><div class="sub">腾讯云 · 机器人节点</div></div><span class="pill {{bot.tone}}" style="margin-left:auto">{{bot.label}}</span></div><div class="kv"><span>当前模型</span><b>{{bot.model or '未上报'}}</b></div><div class="kv"><span>QQ 账号</span><b>{{bot.qq or '未上报'}}</b></div><div class="kv"><span>最后心跳</span><b>{{bot.updated_label}}</b></div><div class="service-grid">{% for key,label in [('napcat','NapCat'),('bridge','QQ Bridge'),('openclaw','OpenClaw')] %}<div class="service"><span>{{label}}</span>{{bot.components[key]}}</div>{% endfor %}</div></div></section><section class="panel"><div class="panel-head"><h2>机器人节点资源</h2><span class="pill">{{'最新快照' if bot.fresh else '非实时'}}</span></div><div class="panel-body">{% for key,label in [('cpu','CPU 使用率'),('memory_percent','内存使用率'),('uptime','节点运行时间'),('model','当前模型'),('memory','长期记忆状态'),('patrol','巡群状态'),('last_message','最近消息时间'),('last_patrol','最近巡群时间'),('tokens','累计 Token')] %}<div class="kv"><span>{{label}}</span><b>{{bot[key] if bot[key] is not none else '未上报'}}{{'%' if key in ['cpu','memory_percent'] and bot[key] is not none else ''}}{{' 秒' if key=='uptime' and bot[key] is not none else ''}}</b></div>{% endfor %}</div></section></div>'''



# Apply display branding at runtime. Internal CEYLAN_* variable names remain as
# compatibility aliases for existing deployments; they are not product names.
CONSOLE_BASE = (CONSOLE_BASE
    .replace('CEYLAN<span>CLC</span>', '{{site_name}}')
    .replace('CEYLANCLC / ENGINEERING LAB', '{{site_name}} / ENGINEERING LAB')
    .replace('CeylanCLC 主节点', '{{site_name}} 主节点')
    .replace('CeylanCLC', '{{site_name}}')
    .replace("('bot','弹性机器人','/admin/bot')", "('bot',bot_name,'/admin/bot')"))
OVERVIEW_TEMPLATE = (OVERVIEW_TEMPLATE
    .replace('弹性机器人', '{{bot_name}}')
    .replace('弹性 / QQ AI', '{{bot_name}} / QQ AI')
    .replace('腾讯云 · 机器人节点', '{{bot_node_label}}'))
BOT_TEMPLATE = (BOT_TEMPLATE
    .replace('弹性机器人', '{{bot_name}}')
    .replace('弹性 / QQ AI', '{{bot_name}} / QQ AI')
    .replace('腾讯云 · 机器人节点', '{{bot_node_label}}'))

# V2 extensions reuse the existing Flask instance and administrator session.
from ceylan_extensions import install as _install_ceylan_extensions
_install_ceylan_extensions(app, globals())

from ceylan_bot_console import install as _install_bot_console
_install_bot_console(app, globals())

from ceylan_bot_control import install as _install_bot_control
_install_bot_control(app, globals())

from ceylan_keys_web import install as _install_keys_v5
_install_keys_v5(app, globals())

from ceylan_fun_web import install as _install_fun_v6
_install_fun_v6(app, globals())
