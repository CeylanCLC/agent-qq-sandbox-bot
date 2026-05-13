import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from urllib.parse import unquote
from collections import defaultdict, deque
from pathlib import Path

import httpx
import websockets


ONEBOT_WS = "ws://127.0.0.1:3001"
OPENCLAW_URL = "http://127.0.0.1:29306/v1/chat/completions"

HOST_SHARE_DIR = "/root/openclaw-qq/share"
CONTAINER_SHARE_DIR = "/share"

# 图片理解配置
# url：直接把 NapCat 给的图片 URL 传给模型；省流量，但要求模型服务能访问该 URL
# data_url：服务器先下载图片，再转 base64 data URL 传给模型；更稳，但更耗带宽和 token
IMAGE_SEND_MODE = os.getenv("IMAGE_SEND_MODE", "url").strip().lower()

# 单次用户消息最多带几张图片给模型
MAX_IMAGES_PER_MESSAGE = int(os.getenv("MAX_IMAGES_PER_MESSAGE", "3"))

# 用户说“上图/这图/图片”时，最多从群聊缓存里补几张最近图片
MAX_CONTEXT_IMAGES = int(os.getenv("MAX_CONTEXT_IMAGES", "2"))

# data_url 模式下，单张图片最大下载字节数，默认 4MB
MAX_IMAGE_DOWNLOAD_BYTES = int(os.getenv("MAX_IMAGE_DOWNLOAD_BYTES", str(4 * 1024 * 1024)))
# 群里文字先到、图片后到时，问图消息最多等几秒再找图
IMAGE_WAIT_AFTER_TEXT_SECONDS = float(os.getenv("IMAGE_WAIT_AFTER_TEXT_SECONDS", "2.0"))
# 补图时只取最近多少秒内的图片，避免拿很久以前的图乱答
RECENT_IMAGE_MAX_AGE_SECONDS = float(os.getenv("RECENT_IMAGE_MAX_AGE_SECONDS", "180"))
VISION_IMAGE_URL_FORMAT = os.getenv("VISION_IMAGE_URL_FORMAT", "object").strip().lower()

# 只有这些 QQ 号可以触发“任务模式”
# 改成你自己的 QQ 号；多个管理员就写多个。
ADMIN_QQ_IDS = set(filter(None, os.getenv("ADMIN_QQ_IDS", "").split(",")))

# 群聊关键词触发
TRIGGER_WORDS = ["弹性", "张智豪", "茶"]

# 忽略这些群成员/系统机器人，避免 @ Q群管家
IGNORE_SENDER_NAMES = ["Q群管家", "群管家", "QQ管家", "QQ小冰", "群机器人"]
IGNORE_USER_IDS = set()


# 任务模式最多连续输出几轮
MAX_TASK_STEPS = 6

# 普通聊天上下文轮数
MAX_HISTORY = 30

# 主动触发时，额外给 OpenClaw 看的最近群消息条数
ACTIVE_GROUP_CONTEXT_LIMIT = int(os.getenv("ACTIVE_GROUP_CONTEXT_LIMIT", "25"))

# 每隔多久巡群一次：3小时
PERIODIC_REVIEW_INTERVAL_SECONDS = 60

# 每个群最多缓存多少条最近消息
GROUP_CACHE_MAX_MESSAGES = 250

# 定时巡群要看的群。
# 空集合表示：所有机器人所在且收到过消息的群都巡。
# 如果只想巡指定群，写：PERIODIC_REVIEW_GROUPS = {"GROUP_ID_1", "GROUP_ID_2"}
PERIODIC_REVIEW_GROUPS = set()

# 每轮巡群最多处理多少个群，防止一下子太耗 token
MAX_GROUPS_PER_REVIEW = 20

# 巡群状态落盘文件：保存缓存、last_seq、已发过的巡群回复，防止重启后失忆
PERIODIC_STATE_FILE = Path("/root/openclaw-qq/periodic_state.json")

# 群长期压缩记忆文件：不保存全部历史原文，只保存每个群的长期摘要
GROUP_MEMORY_STATE_FILE = Path("/root/openclaw-qq/group_memory_state.json")

# 每隔多久尝试压缩一次群记忆，默认 5 小时
GROUP_MEMORY_COMPACT_INTERVAL_SECONDS = int(os.getenv("GROUP_MEMORY_COMPACT_INTERVAL_SECONDS", str(5 * 60 * 60)))

# 每个群新增多少条消息后才值得压缩，避免频繁消耗 token
GROUP_MEMORY_MIN_NEW_MESSAGES = int(os.getenv("GROUP_MEMORY_MIN_NEW_MESSAGES", "80"))

# 单次压缩最多取多少条新消息
GROUP_MEMORY_RAW_LIMIT = int(os.getenv("GROUP_MEMORY_RAW_LIMIT", "140"))

# 长期摘要最大字符数，越小越省输入 token
GROUP_MEMORY_MAX_CHARS = int(os.getenv("GROUP_MEMORY_MAX_CHARS", "1800"))

# 普通回复时最多塞入多少字符长期摘要
GROUP_MEMORY_CONTEXT_MAX_CHARS = int(os.getenv("GROUP_MEMORY_CONTEXT_MAX_CHARS", "1200"))


histories = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
# 私聊最近图片缓存：用于用户先发图、后发“看图/这图是什么”时自动补图
private_image_cache = defaultdict(lambda: deque(maxlen=10))
group_message_cache = defaultdict(lambda: deque(maxlen=GROUP_CACHE_MAX_MESSAGES))
# 群聊最近图片缓存：用于群友先发图、后 @ 机器人问“这图是什么”时补图
group_image_cache = defaultdict(lambda: deque(maxlen=50))

group_message_seq = defaultdict(int)
group_last_periodic_seq = defaultdict(int)
periodic_answer_fingerprints = defaultdict(lambda: deque(maxlen=50))
group_memory_summary = defaultdict(str)
group_memory_last_seq = defaultdict(int)
group_memory_last_update_ts = defaultdict(float)
running_tasks = set()
send_lock = asyncio.Lock()



def save_periodic_state():
    try:
        state = {
            "group_message_cache": {
                gid: list(dq)
                for gid, dq in group_message_cache.items()
            },
            "group_message_seq": {
                gid: int(seq)
                for gid, seq in group_message_seq.items()
            },
            "group_last_periodic_seq": {
                gid: int(seq)
                for gid, seq in group_last_periodic_seq.items()
            },
            "periodic_answer_fingerprints": {
                gid: list(dq)
                for gid, dq in periodic_answer_fingerprints.items()
            },
        }

        tmp = PERIODIC_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PERIODIC_STATE_FILE)

    except Exception as e:
        print(f"[巡群状态保存失败] {e}", flush=True)


def load_periodic_state():
    global group_message_cache
    global group_message_seq
    global group_last_periodic_seq
    global periodic_answer_fingerprints

    if not PERIODIC_STATE_FILE.exists():
        return

    try:
        state = json.loads(PERIODIC_STATE_FILE.read_text(encoding="utf-8"))

        group_message_cache = defaultdict(
            lambda: deque(maxlen=GROUP_CACHE_MAX_MESSAGES)
        )

        for gid, items in state.get("group_message_cache", {}).items():
            group_message_cache[str(gid)] = deque(
                items[-GROUP_CACHE_MAX_MESSAGES:],
                maxlen=GROUP_CACHE_MAX_MESSAGES
            )

        group_message_seq = defaultdict(int)
        for gid, seq in state.get("group_message_seq", {}).items():
            group_message_seq[str(gid)] = int(seq)

        group_last_periodic_seq = defaultdict(int)
        for gid, seq in state.get("group_last_periodic_seq", {}).items():
            group_last_periodic_seq[str(gid)] = int(seq)

        periodic_answer_fingerprints = defaultdict(lambda: deque(maxlen=50))
        for gid, items in state.get("periodic_answer_fingerprints", {}).items():
            periodic_answer_fingerprints[str(gid)] = deque(items[-50:], maxlen=50)

        print(
            f"[巡群状态已加载] groups={len(group_message_cache)}",
            flush=True
        )

    except Exception as e:
        print(f"[巡群状态加载失败] {e}", flush=True)


def mark_group_periodic_done(group_id, max_seq):
    group_last_periodic_seq[str(group_id)] = int(max_seq)
    save_periodic_state()


def record_bot_group_message(group_id, text):
    group_id = str(group_id)
    text = (text or "").strip()

    if not group_id or not text:
        return

    if len(text) > 300:
        text = text[:300] + "……"

    ts = time.strftime("%H:%M:%S", time.localtime())

    group_message_seq[group_id] += 1

    group_message_cache[group_id].append({
        "seq": group_message_seq[group_id],
        "time": ts,
        "user_id": "BOT",
        "nickname": "弹性",
        "text": text,
        "is_bot": True,
    })

    print(
        f"[缓存机器人消息] group={group_id} seq={group_message_seq[group_id]} text={text}",
        flush=True
    )

    save_periodic_state()



def save_group_memory_state():
    try:
        state = {
            "group_memory_summary": dict(group_memory_summary),
            "group_memory_last_seq": {
                gid: int(seq)
                for gid, seq in group_memory_last_seq.items()
            },
            "group_memory_last_update_ts": {
                gid: float(ts)
                for gid, ts in group_memory_last_update_ts.items()
            },
        }

        tmp = GROUP_MEMORY_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GROUP_MEMORY_STATE_FILE)

    except Exception as e:
        print(f"[群长期记忆保存失败] {e}", flush=True)


def load_group_memory_state():
    global group_memory_summary
    global group_memory_last_seq
    global group_memory_last_update_ts

    if not GROUP_MEMORY_STATE_FILE.exists():
        return

    try:
        state = json.loads(GROUP_MEMORY_STATE_FILE.read_text(encoding="utf-8"))

        group_memory_summary = defaultdict(str)
        for gid, text in state.get("group_memory_summary", {}).items():
            group_memory_summary[str(gid)] = str(text)

        group_memory_last_seq = defaultdict(int)
        for gid, seq in state.get("group_memory_last_seq", {}).items():
            group_memory_last_seq[str(gid)] = int(seq)

        group_memory_last_update_ts = defaultdict(float)
        for gid, ts in state.get("group_memory_last_update_ts", {}).items():
            group_memory_last_update_ts[str(gid)] = float(ts)

        print(
            f"[群长期记忆已加载] groups={len(group_memory_summary)}",
            flush=True
        )

    except Exception as e:
        print(f"[群长期记忆加载失败] {e}", flush=True)


def format_messages_for_memory(group_id, since_seq=0, limit=140):
    group_id = str(group_id)
    msgs = list(group_message_cache.get(group_id, []))

    if since_seq:
        msgs = [m for m in msgs if int(m.get("seq", 0)) > int(since_seq)]

    msgs = msgs[-limit:]

    lines = []
    max_seq = int(since_seq or 0)

    for m in msgs:
        seq = int(m.get("seq", 0))
        max_seq = max(max_seq, seq)

        is_bot = bool(m.get("is_bot", False))
        name = "弹性" if is_bot else (m.get("nickname") or m.get("user_id") or "群友")
        text = str(m.get("text") or "").strip()
        ts = str(m.get("time") or "")

        img_count = len(list(m.get("image_urls") or []))
        img_note = f" [图片数={img_count}]" if img_count else ""

        if text:
            lines.append(f"seq={seq} [{ts}] {name}: {text}{img_note}")

    return "\n".join(lines), max_seq, len(msgs)


def build_group_memory_context(group_id):
    group_id = str(group_id)
    text = (group_memory_summary.get(group_id) or "").strip()

    if not text:
        return ""

    if len(text) > GROUP_MEMORY_CONTEXT_MAX_CHARS:
        text = text[-GROUP_MEMORY_CONTEXT_MAX_CHARS:]

    return text


async def compact_group_memory_once(group_id, force=False):
    group_id = str(group_id)

    last_seq = int(group_memory_last_seq.get(group_id, 0))
    old_summary = (group_memory_summary.get(group_id) or "").strip()

    raw_text, max_seq, new_count = format_messages_for_memory(
        group_id,
        since_seq=last_seq,
        limit=GROUP_MEMORY_RAW_LIMIT
    )

    if not raw_text.strip() or new_count == 0:
        return False

    if not force and new_count < GROUP_MEMORY_MIN_NEW_MESSAGES:
        print(
            f"[群长期记忆] group={group_id} new_count={new_count}, skip",
            flush=True
        )
        return False

    prompt = f"""
你是QQ群机器人“弹性”的长期记忆压缩器。

请把旧长期摘要和新增聊天记录合并成一份新的“群长期记忆摘要”。

要求：
1. 只保留长期有用的信息，不要逐字复述聊天。
2. 保留群里反复出现的人名/昵称、他们的偏好、项目、课程、作业、梗、争论点、机器人已经说过的重要话。
3. 如果出现图片，只能根据聊天文本中的描述概括，不要凭空描述图片内容。
4. 删除无意义寒暄、刷屏、重复表情、临时情绪。
5. 不要编造没有出现过的信息。
6. 输出中文，尽量结构化，控制在 {GROUP_MEMORY_MAX_CHARS} 字以内。
7. 不要提 OpenClaw、token、接口、后台、工作区、压缩器等内部实现。
8. 这份摘要以后会作为群聊上下文给机器人参考，所以要利于避免重复说话。

旧长期摘要：
{old_summary if old_summary else "暂无"}

新增聊天记录：
{raw_text}

请输出新的群长期记忆摘要：
"""

    messages = [
        {
            "role": "system",
            "content": "你是严谨的群聊长期记忆压缩器，只输出摘要，不输出解释。"
        },
        {
            "role": "user",
            "content": prompt
        }
    ]

    try:
        summary = await call_openclaw(messages, max_tokens=1000, timeout=600)
    except Exception as e:
        print(f"[群长期记忆压缩失败] group={group_id} error={e}", flush=True)
        return False

    summary = (summary or "").strip()

    if not summary:
        print(f"[群长期记忆压缩失败] group={group_id} empty-summary", flush=True)
        return False

    # 防止模型废话太长
    if len(summary) > GROUP_MEMORY_MAX_CHARS:
        summary = summary[:GROUP_MEMORY_MAX_CHARS] + "……"

    group_memory_summary[group_id] = summary
    group_memory_last_seq[group_id] = max_seq
    group_memory_last_update_ts[group_id] = time.time()

    save_group_memory_state()

    print(
        f"[群长期记忆已更新] group={group_id} new_count={new_count} "
        f"last_seq={max_seq} summary_len={len(summary)}",
        flush=True
    )

    return True


async def periodic_group_memory_compaction():
    await asyncio.sleep(120)

    while True:
        try:
            await asyncio.sleep(GROUP_MEMORY_COMPACT_INTERVAL_SECONDS)

            group_ids = list(group_message_cache.keys())

            if PERIODIC_REVIEW_GROUPS:
                group_ids = [gid for gid in group_ids if gid in PERIODIC_REVIEW_GROUPS]

            group_ids = group_ids[:MAX_GROUPS_PER_REVIEW]

            for group_id in group_ids:
                await compact_group_memory_once(group_id, force=False)
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(f"[群长期记忆主循环错误] {e}，60秒后继续", flush=True)
            await asyncio.sleep(60)



def load_bootstrap_prompt():
    try:
        text = Path("/root/.openclaw/workspace/BOOTSTRAP.md").read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception:
        pass

    return """
你是QQ群里的AI助手，网名叫“弹性”，真名叫“张智豪”。
你是浙江农林大学茶学专业大三学生，出身书香门第，说话半文言半白话。
不要提 OpenClaw、BOOTSTRAP.md、IDENTITY.md、USER.md、工作区初始化。
不要说自己没有身份，不要让用户给你起名字。
群聊回复要简洁、自然、有古意。
"""


def get_system_prompt():
    return load_bootstrap_prompt() + """

额外群聊规则：
1. 你现在正在 QQ 群或 QQ 私聊中与用户对话。
2. 不要输出后台文件名，不要说自己缺少身份设定。
3. 你已经知道自己是谁：弹性，真名张智豪。
4. 回复尽量短小自然，像正常群友聊天。
5. 技术问题可以认真答，但不要写成论文。
"""


def get_task_system_prompt():
    return get_system_prompt() + """

你现在处于“任务执行模式”。

任务执行规则：
1. 你要把用户给出的复杂任务拆成若干步执行。
2. 每一轮只输出当前步骤的执行结果，不要一次性写完所有轮次。
3. 如果任务尚未完成，请在回复最后写：[CONTINUE]
4. 如果任务已经完成，请在回复最后写：[DONE]
5. 每轮回复要有实质内容，不要空喊“我正在做”。
6. 不要声称自己执行了不能执行的外部动作。
7. 不要输出危险命令、攻击、盗号、刷屏、绕过风控等内容。
8. 回复仍保持“弹性 / 张智豪”的半文言人设，但任务结果要清楚可用。
"""


def get_periodic_system_prompt():
    return get_system_prompt() + """

你现在处于“定时巡群模式”。

你会看到某个QQ群最近一段时间的群聊摘要或消息片段。你的任务是判断是否值得主动说一句话。

规则：
1. 如果群聊内容没有必要插话，请只输出：SILENCE
2. 如果值得插话，只输出一条简短自然的群聊消息。
3. 不要 @ 任何人。
4. 不要说“我在定时巡群”“我检索了群消息”。
5. 不要长篇总结，不要像公告。
6. 回复要像QQ群友自然接话，最好 1 到 3 句。
7. 保持“弹性 / 张智豪”的半文言茶学书生人设。
8. 不要对隐私、争吵、敏感话题火上浇油。
9. 如果群里只是普通闲聊，可以轻轻接一句；如果没有合适切入点，就 SILENCE。
"""


def get_openclaw_token():
    token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if token:
        return token

    cfg = json.loads(Path("/root/.openclaw/openclaw.json").read_text())
    return cfg["gateway"]["auth"]["token"]


OPENCLAW_TOKEN = get_openclaw_token()

# 带图消息可绕过 OpenClaw，直接调用小米 MiMo OpenAI-compatible API
# 纯文本仍然走 OpenClaw
USE_DIRECT_MIMO_VISION = os.getenv("USE_DIRECT_MIMO_VISION", "1").strip() == "1"
MIMO_VISION_BASE_URL = os.getenv("MIMO_VISION_BASE_URL", "").strip().rstrip("/")
MIMO_VISION_API_KEY = os.getenv("MIMO_VISION_API_KEY", "").strip()
MIMO_VISION_MODEL = os.getenv("MIMO_VISION_MODEL", "mimo-v2.5").strip()



def is_ignored_sender(event):
    user_id = str(event.get("user_id", ""))

    if user_id in IGNORE_USER_IDS:
        return True

    sender = event.get("sender") or {}
    nickname = ""
    card = ""

    if isinstance(sender, dict):
        nickname = str(sender.get("nickname") or "")
        card = str(sender.get("card") or "")

    display = nickname + " " + card

    for name in IGNORE_SENDER_NAMES:
        if name and name in display:
            return True

    return False


def extract_image_url_from_segment(data):
    """
    从 NapCat / OneBot image segment 中提取可给视觉模型使用的图片地址。
    优先使用 url；其次尝试 file/path。
    """
    if not isinstance(data, dict):
        return ""

    candidates = [
        data.get("url"),
        data.get("file"),
        data.get("path"),
    ]

    for value in candidates:
        if not value:
            continue

        value = unquote(str(value).strip())

        if value.startswith("http://") or value.startswith("https://"):
            return value

        if value.startswith("data:image/"):
            return value

    # 调试用：不打印完整 URL，避免日志泄露太多信息
    short = {}
    for k, v in data.items():
        sv = str(v)
        short[k] = sv[:80] + ("..." if len(sv) > 80 else "")

    print(f"[图片段无可用URL] keys={list(data.keys())} data={short}", flush=True)

    return ""


def parse_message(event):
    self_id = str(event.get("self_id", ""))
    msg = event.get("message", [])
    raw = event.get("raw_message", "")

    at_me = False
    texts = []
    image_urls = []

    if isinstance(msg, str):
        at_code = f"[CQ:at,qq={self_id}]"
        at_me = at_code in msg or at_code in raw

        # 兼容 CQ 码格式里的图片 URL
        for m in re.finditer(r"\\[CQ:image,[^\\]]*url=([^,\\]]+)", msg):
            url = m.group(1).strip()
            if url:
                image_urls.append(url)

        text = raw.replace(at_code, "").strip()

        if image_urls and "[图片]" not in text:
            text = (text + " [图片]").strip()

        return at_me, text, image_urls

    for seg in msg:
        typ = seg.get("type")
        data = seg.get("data", {})

        if typ == "at" and str(data.get("qq", "")) == self_id:
            at_me = True

        if typ == "text":
            texts.append(data.get("text", ""))

        if typ == "image":
            url = extract_image_url_from_segment(data)
            if url:
                image_urls.append(url)
            texts.append("[图片]")

        if typ == "face":
            texts.append("[表情]")

    text = "".join(texts).strip()
    return at_me, text, image_urls



def should_reply(at_me, text):
    if at_me:
        return True
    return any(word in text for word in TRIGGER_WORDS)


def extract_task_text(text):
    t = text.strip()

    patterns = [
        r"^(?:弹性|张智豪)?\s*(?:任务|执行任务|agent任务|Agent任务)\s*[:：,，]?\s*(.+)$",
        r"^(?:/任务|#任务)\s*[:：,，]?\s*(.+)$",
    ]

    for pattern in patterns:
        m = re.match(pattern, t)
        if m:
            return m.group(1).strip()

    return ""


def parse_send_asset_command(text):
    t = text.strip()

    m = re.match(r"^(?:弹性|张智豪)?\s*发图\s*[:：]\s*(.+)$", t)
    if m:
        return "image", m.group(1).strip()

    m = re.match(r"^(?:弹性|张智豪)?\s*发文件\s*[:：]\s*(.+)$", t)
    if m:
        return "file", m.group(1).strip()

    return "", ""


def record_group_message(event, text, image_urls=None):
    group_id = str(event.get("group_id", ""))
    user_id = str(event.get("user_id", ""))
    self_id = str(event.get("self_id", ""))

    if not group_id or not user_id or user_id == self_id:
        return

    image_urls = list(image_urls or [])

    text = text.strip()
    if not text and image_urls:
        text = "[图片]"
    if not text:
        return

    if len(text) > 300:
        text = text[:300] + "……"

    nickname = ""
    sender = event.get("sender") or {}
    if isinstance(sender, dict):
        nickname = sender.get("card") or sender.get("nickname") or ""

    ts = time.strftime("%H:%M:%S", time.localtime())

    group_message_seq[group_id] += 1

    group_message_cache[group_id].append({
        "seq": group_message_seq[group_id],
        "time": ts,
        "user_id": user_id,
        "nickname": nickname,
        "text": text,
        "image_urls": image_urls[:MAX_IMAGES_PER_MESSAGE],
        "is_bot": False,
    })

    print(
        f"[缓存群消息] group={group_id} seq={group_message_seq[group_id]} "
        f"user={user_id} nickname={nickname} text={text} images={len(image_urls)}",
        flush=True
    )



def build_group_recent_text(group_id, limit=80, only_new=False):
    group_id = str(group_id)
    msgs = list(group_message_cache.get(group_id, []))

    if only_new:
        last_seq = group_last_periodic_seq.get(group_id, 0)
        msgs = [m for m in msgs if int(m.get("seq", 0)) > last_seq]

    msgs = msgs[-limit:]

    lines = []
    max_seq = group_last_periodic_seq.get(group_id, 0)
    human_new_count = 0
    image_urls = []

    for m in msgs:
        seq = int(m.get("seq", 0))
        max_seq = max(max_seq, seq)

        is_bot = bool(m.get("is_bot", False))
        if not is_bot:
            human_new_count += 1

        name = "弹性" if is_bot else (m.get("nickname") or m.get("user_id"))
        text = m.get("text", "")
        ts = m.get("time", "")

        msg_images = list(m.get("image_urls") or [])
        image_note = f" 图片数={len(msg_images)}" if msg_images else ""

        lines.append(f"[{ts}] {name}: {text}{image_note}")

        for url in msg_images:
            if url:
                image_urls.append(url)

    return "\n".join(lines), max_seq, len(msgs), human_new_count, image_urls


def build_group_context_text(group_id, limit=40):
    group_id = str(group_id)
    msgs = list(group_message_cache.get(group_id, []))[-limit:]

    lines = []

    for m in msgs:
        is_bot = bool(m.get("is_bot", False))
        name = "弹性" if is_bot else (m.get("nickname") or m.get("user_id"))
        text = m.get("text", "")
        ts = m.get("time", "")

        msg_images = list(m.get("image_urls") or [])
        image_note = f" 图片数={len(msg_images)}" if msg_images else ""

        lines.append(f"[{ts}] {name}: {text}{image_note}")

    return "\n".join(lines)


def get_recent_group_image_urls(group_id, user_id=None, limit_msgs=30, max_images=2, max_age_seconds=None):
    """
    获取某个群最近图片 URL。
    优先级：
    1. 同一用户最近图片；
    2. 本群最近图片；
    3. group_message_cache 兜底。
    """
    group_id = str(group_id)
    user_id = str(user_id) if user_id is not None else None
    max_age_seconds = RECENT_IMAGE_MAX_AGE_SECONDS if max_age_seconds is None else float(max_age_seconds)

    now = time.time()
    image_urls = []

    def fresh(item):
        ts_epoch = item.get("ts_epoch")
        if ts_epoch is None:
            return True
        try:
            return now - float(ts_epoch) <= max_age_seconds
        except Exception:
            return True

    # 第一优先级：专门的群图片缓存
    items = [item for item in list(group_image_cache.get(group_id, [])) if fresh(item)]

    # 1.1 优先同一用户最近发的图
    if user_id:
        for item in reversed(items):
            if str(item.get("user_id", "")) != user_id:
                continue

            url = (item.get("url") or "").strip()
            if url and url not in image_urls:
                image_urls.append(url)

            if len(image_urls) >= max_images:
                return list(reversed(image_urls))

    # 1.2 再取本群最近图片
    for item in reversed(items):
        url = (item.get("url") or "").strip()
        if url and url not in image_urls:
            image_urls.append(url)

        if len(image_urls) >= max_images:
            return list(reversed(image_urls))

    # 第二优先级：从普通群消息缓存兜底找 image_urls
    msgs = list(group_message_cache.get(group_id, []))[-limit_msgs:]

    if user_id:
        for m in reversed(msgs):
            if str(m.get("user_id", "")) != user_id:
                continue

            for url in reversed(list(m.get("image_urls") or [])):
                url = (url or "").strip()
                if url and url not in image_urls:
                    image_urls.append(url)

                if len(image_urls) >= max_images:
                    return list(reversed(image_urls))

    for m in reversed(msgs):
        for url in reversed(list(m.get("image_urls") or [])):
            url = (url or "").strip()
            if url and url not in image_urls:
                image_urls.append(url)

            if len(image_urls) >= max_images:
                return list(reversed(image_urls))

    return list(reversed(image_urls))



def record_bot_group_message(group_id, text):
    group_id = str(group_id)
    text = (text or "").strip()

    if not group_id or not text:
        return

    if len(text) > 300:
        text = text[:300] + "……"

    ts = time.strftime("%H:%M:%S", time.localtime())

    group_message_seq[group_id] += 1

    group_message_cache[group_id].append({
        "seq": group_message_seq[group_id],
        "time": ts,
        "user_id": "BOT",
        "nickname": "弹性",
        "text": text,
        "image_urls": [],
        "is_bot": True,
    })

    print(
        f"[缓存机器人消息] group={group_id} seq={group_message_seq[group_id]} text={text}",
        flush=True
    )

    save_periodic_state()



async def image_url_to_openai_ref(url):
    """
    返回 OpenAI-compatible image_url.url。
    IMAGE_SEND_MODE=url 时直接返回原 URL。
    IMAGE_SEND_MODE=data_url 时，先下载图片再转成 base64 data URL。
    """
    url = (url or "").strip()

    if not url:
        return ""

    if url.startswith("data:image/"):
        return url

    if IMAGE_SEND_MODE != "data_url":
        return url

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)

        if resp.status_code >= 400:
            print(f"[图片下载失败] status={resp.status_code} url={url[:120]}", flush=True)
            return url

        content = resp.content

        if len(content) > MAX_IMAGE_DOWNLOAD_BYTES:
            print(f"[图片过大] size={len(content)} url={url[:120]}", flush=True)
            return url

        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

        if not content_type.startswith("image/"):
            content_type = "image/jpeg"

        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{b64}"

    except Exception as e:
        print(f"[图片转data_url失败] {e} url={url[:120]}", flush=True)
        return url


async def build_user_content(text, image_urls=None):
    image_urls = list(image_urls or [])
    valid_urls = []

    for url in image_urls[:MAX_IMAGES_PER_MESSAGE]:
        ref = await image_url_to_openai_ref(url)
        if ref:
            valid_urls.append(ref)

    if not valid_urls:
        return text

    first = valid_urls[0]
    if first.startswith("data:image/"):
        first_info = f"data_url len={len(first)} prefix={first[:40]}"
    else:
        first_info = f"url={first[:160]}"

    print(
        f"[视觉请求] mode={IMAGE_SEND_MODE} format={VISION_IMAGE_URL_FORMAT} "
        f"images={len(valid_urls)} first={first_info}",
        flush=True
    )

    content = [
        {
            "type": "text",
            "text": text or "请结合图片内容回复。"
        }
    ]

    for ref in valid_urls:
        if VISION_IMAGE_URL_FORMAT == "simple":
            content.append({
                "type": "image_url",
                "image_url": ref
            })
        else:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": ref
                }
            })

    return content


def image_reference_intent(text):
    """
    判断用户这句话是不是在问图片/截图/影像。
    不再只靠单个“图”字，避免“现在可以读图了”这种陈述句误触发。
    """
    t = (text or "").strip()

    if not t:
        return False

    patterns = [
        r"这.*图",
        r"这个.*图",
        r"这张.*图",
        r"上.*图",
        r"刚.*图",
        r"图.*什么",
        r"图片.*什么",
        r"截图.*什么",
        r"影像.*为何",
        r"此影像",
        r"观见.*影像",
        r"描述.*图",
        r"描述.*影像",
        r"看.*图",
        r"看看.*图",
        r"看下.*图",
        r"看一下.*图",
        r"看.*这个",
        r"看.*这是",
        r"这是.*干嘛",
        r"这是.*什么",
        r"这里面.*什么",
        r"里面.*什么",
        r"识别.*图",
        r"读.*图",
        r"认.*图",
        r"画面.*什么",
        r"画面.*内容",
    ]

    for pat in patterns:
        if re.search(pat, t):
            return True

    # 一些短句直接认为是在问图
    exact_like = [
        "看图", "读图", "识图", "这图", "上图",
        "这是什么图", "这图是什么", "图里有什么",
        "这张图里有什么", "这是在干嘛", "这是什么",
    ]

    return any(k in t for k in exact_like)



def remember_private_images(user_id, image_urls):
    user_id = str(user_id)
    image_urls = list(image_urls or [])

    if not user_id or not image_urls:
        return

    ts = time.strftime("%H:%M:%S", time.localtime())

    for url in image_urls:
        url = (url or "").strip()
        if not url:
            continue

        private_image_cache[user_id].append({
            "time": ts,
            "url": url,
        })

    print(
        f"[私聊图片缓存] user={user_id} images={len(image_urls)}",
        flush=True
    )


def get_recent_private_image_urls(user_id, max_images=1):
    user_id = str(user_id)
    items = list(private_image_cache.get(user_id, []))

    image_urls = []

    for item in reversed(items):
        url = (item.get("url") or "").strip()
        if url and url not in image_urls:
            image_urls.append(url)

        if len(image_urls) >= max_images:
            break

    return list(reversed(image_urls))




def remember_group_images(group_id, user_id, image_urls):
    group_id = str(group_id)
    user_id = str(user_id)
    image_urls = list(image_urls or [])

    if not group_id or not user_id or not image_urls:
        return

    ts = time.strftime("%H:%M:%S", time.localtime())
    now = time.time()
    n = 0

    for url in image_urls:
        url = (url or "").strip()
        if not url:
            continue

        group_image_cache[group_id].append({
            "time": ts,
            "ts_epoch": now,
            "user_id": user_id,
            "url": url,
        })
        n += 1

    print(
        f"[群图片缓存] group={group_id} user={user_id} images={n}",
        flush=True
    )



def get_recent_group_image_urls(group_id, user_id=None, limit_msgs=30, max_images=2, max_age_seconds=None):
    """
    获取某个群最近图片 URL。
    优先级：
    1. 同一用户最近图片；
    2. 本群最近图片；
    3. group_message_cache 兜底。
    """
    group_id = str(group_id)
    user_id = str(user_id) if user_id is not None else None
    max_age_seconds = RECENT_IMAGE_MAX_AGE_SECONDS if max_age_seconds is None else float(max_age_seconds)

    now = time.time()
    image_urls = []

    def fresh(item):
        ts_epoch = item.get("ts_epoch")
        if ts_epoch is None:
            return True
        try:
            return now - float(ts_epoch) <= max_age_seconds
        except Exception:
            return True

    # 第一优先级：专门的群图片缓存
    items = [item for item in list(group_image_cache.get(group_id, [])) if fresh(item)]

    # 1.1 优先同一用户最近发的图
    if user_id:
        for item in reversed(items):
            if str(item.get("user_id", "")) != user_id:
                continue

            url = (item.get("url") or "").strip()
            if url and url not in image_urls:
                image_urls.append(url)

            if len(image_urls) >= max_images:
                return list(reversed(image_urls))

    # 1.2 再取本群最近图片
    for item in reversed(items):
        url = (item.get("url") or "").strip()
        if url and url not in image_urls:
            image_urls.append(url)

        if len(image_urls) >= max_images:
            return list(reversed(image_urls))

    # 第二优先级：从普通群消息缓存兜底找 image_urls
    msgs = list(group_message_cache.get(group_id, []))[-limit_msgs:]

    if user_id:
        for m in reversed(msgs):
            if str(m.get("user_id", "")) != user_id:
                continue

            for url in reversed(list(m.get("image_urls") or [])):
                url = (url or "").strip()
                if url and url not in image_urls:
                    image_urls.append(url)

                if len(image_urls) >= max_images:
                    return list(reversed(image_urls))

    for m in reversed(msgs):
        for url in reversed(list(m.get("image_urls") or [])):
            url = (url or "").strip()
            if url and url not in image_urls:
                image_urls.append(url)

            if len(image_urls) >= max_images:
                return list(reversed(image_urls))

    return list(reversed(image_urls))



def make_periodic_reply_fingerprint(text):
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，。！？、,.!?;；:：\[\]【】()（）\"'“”‘’`~]", "", t)

    if not t:
        return ""

    return hashlib.sha1(t.encode("utf-8")).hexdigest()




def is_bad_periodic_answer(text):
    t = (text or "").strip()

    if not t:
        return True

    upper = t.upper()

    # 巡群模式下，只要模型输出 SILENCE，无论前后夹了什么废话，都不发群
    if "SILENCE" in upper:
        return True

    bad_phrases = [
        "No response from OpenClaw",
        "no response from openclaw",
        "No response",
        "吾一时失语",
        "未得佳答",
        "null",
        "None",
        "undefined",
        "BOOTSTRAP.md",
        "IDENTITY.md",
        "USER.md",
        "SOUL.md",
        "Bootstrap cannot be completed",
        "workspace",
        "OpenClaw",
        "初始化",
        "birth certificate",
        "blank templates",
    ]

    for phrase in bad_phrases:
        if phrase.lower() in t.lower():
            return True

    return False


async def call_openclaw(messages, max_tokens=800, timeout=300):
    payload = {
        "model": "openclaw/default",
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            OPENCLAW_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENCLAW_TOKEN}",
            },
            json=payload,
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"OpenClaw HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()

    answer = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    return answer or "吾一时失语，未得佳答。"


async def call_mimo_vision(messages, max_tokens=800, timeout=300):
    if not MIMO_VISION_BASE_URL:
        raise RuntimeError("MIMO_VISION_BASE_URL 未设置，无法直连 MiMo 视觉模型")

    if not MIMO_VISION_API_KEY:
        raise RuntimeError("MIMO_VISION_API_KEY 未设置，无法直连 MiMo 视觉模型")

    url = MIMO_VISION_BASE_URL.rstrip("/") + "/chat/completions"

    payload = {
        "model": MIMO_VISION_MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
    }

    # 关键修复：
    # 不再使用 httpx 的 json=payload，而是手动 ensure_ascii=True。
    # 这样中文、emoji、特殊昵称都会变成 \uXXXX，避免某些 OpenAI-compatible 网关按 ASCII 编码时报错。
    body = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":")
    ).encode("utf-8")

    api_key = MIMO_VISION_API_KEY.strip()
    base_url = MIMO_VISION_BASE_URL.strip()
    model = MIMO_VISION_MODEL.strip()

    # Header 必须是 ASCII。这里不打印 key，只做严格检查。
    for name, value in {
        "MIMO_VISION_BASE_URL": base_url,
        "MIMO_VISION_API_KEY": api_key,
        "MIMO_VISION_MODEL": model,
    }.items():
        try:
            value.encode("ascii")
        except UnicodeEncodeError as e:
            raise RuntimeError(f"{name} 含非 ASCII 字符，请检查 systemd 环境变量") from e

    print(
        f"[直连MiMo视觉] url={url} model={model} body_len={len(body)} ascii_safe=1",
        flush=True
    )

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers=headers,
                content=body,
            )
    except UnicodeEncodeError as e:
        raise RuntimeError(
            "MiMo Vision 本地编码失败：请求里仍有某个字段被当作 ASCII 编码。"
            f" detail={e}"
        ) from e

    if resp.status_code >= 400:
        raise RuntimeError(f"MiMo Vision HTTP {resp.status_code}: {resp.text[:800]}")

    data = resp.json()

    answer = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    return answer or "吾一时失语，未得佳答。"


async def ask_openclaw(user_key, text, group_id=None, image_urls=None):
    history = histories[user_key]
    image_urls = list(image_urls or [])

    messages = [
        {
            "role": "system",
            "content": get_system_prompt()
        }
    ]

    # 主动 @ / 关键词触发时，额外带上群长期压缩记忆 + 当前群最近聊天缓存
    if group_id is not None:
        memory_context = build_group_memory_context(group_id)

        if memory_context.strip():
            messages.append({
                "role": "system",
                "content": (
                    "下面是当前QQ群的长期压缩记忆。"
                    "它不是完整聊天记录，而是较长时间范围内沉淀下来的背景信息。"
                    "你回答时可以参考它，但不要逐字复述。\n\n"
                    + memory_context
                )
            })

        try:
            group_context = build_group_context_text(
                group_id,
                limit=ACTIVE_GROUP_CONTEXT_LIMIT
            )
        except Exception:
            group_context = ""

        if group_context.strip():
            messages.append({
                "role": "system",
                "content": (
                    "下面是当前QQ群最近聊天缓存，包含群友消息，也可能包含你自己之前说过的话。"
                    "其中“图片数=N”表示该消息里带过图片。"
                    "你回答当前用户时要参考这些上下文，避免重复自己刚刚说过的话，"
                    "也不要把缓存逐字复述出来。\n\n"
                    + group_context
                )
            })

        # 如果当前消息没有图片，但用户明显在问“上图/这图/图片”，就补最近图片给模型
        if not image_urls and image_reference_intent(text):
            image_urls = get_recent_group_image_urls(
                group_id,
                limit_msgs=30,
                max_images=MAX_CONTEXT_IMAGES
            )

    for role, content in history:
        messages.append({"role": role, "content": content})

    user_text = (
        "请严格按照“弹性 / 张智豪”的人物设定回答。"
        "不要提 BOOTSTRAP、IDENTITY、USER、SOUL、OpenClaw、模型、工作区初始化。"
        "请结合当前群聊上下文回答下面这句话。"
        "如果本轮消息附带图片，请务必阅读图片内容再回答，不要只说“我看不到图片”。"
        "用户消息："
        + text
    )

    user_content = await build_user_content(user_text, image_urls=image_urls)

    messages.append({
        "role": "user",
        "content": user_content
    })

    if image_urls and USE_DIRECT_MIMO_VISION:
        answer = await call_mimo_vision(messages, max_tokens=800, timeout=300)
    else:
        answer = await call_openclaw(messages, max_tokens=800, timeout=300)

    history.append(("user", text))
    history.append(("assistant", answer))

    return answer




async def send_group_msg(ws, group_id, user_id, text, mention=True):
    if len(text) > 3500:
        text = text[:3500] + "\n\n……辞多不尽，姑止于此。"

    message = []

    if mention and user_id:
        message.append({"type": "at", "data": {"qq": str(user_id)}})
        message.append({"type": "text", "data": {"text": "\n" + text}})
    else:
        message.append({"type": "text", "data": {"text": text}})

    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": int(group_id),
            "message": message,
            "auto_escape": False,
        },
        "echo": str(uuid.uuid4()),
    }

    async with send_lock:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    record_bot_group_message(group_id, text)


async def send_private_msg(ws, user_id, text):
    if len(text) > 3500:
        text = text[:3500] + "\n\n……辞多不尽，姑止于此。"

    payload = {
        "action": "send_private_msg",
        "params": {
            "user_id": int(user_id),
            "message": [{"type": "text", "data": {"text": text}}],
            "auto_escape": False,
        },
        "echo": str(uuid.uuid4()),
    }

    async with send_lock:
        await ws.send(json.dumps(payload, ensure_ascii=False))


async def send_group_image(ws, group_id, image_name, text="", mention=False, user_id=None):
    safe_name = os.path.basename(image_name.strip())
    host_path = os.path.join(HOST_SHARE_DIR, safe_name)

    if not safe_name:
        await send_group_msg(ws, group_id, user_id, "未指定图片名。", mention=mention)
        return

    if not os.path.exists(host_path):
        await send_group_msg(ws, group_id, user_id, f"未找到图片：{safe_name}", mention=mention)
        return

    message = []

    if mention and user_id:
        message.append({"type": "at", "data": {"qq": str(user_id)}})
        if text:
            message.append({"type": "text", "data": {"text": "\n" + text + "\n"}})
    elif text:
        message.append({"type": "text", "data": {"text": text + "\n"}})

    message.append({
        "type": "image",
        "data": {
            "file": f"{CONTAINER_SHARE_DIR}/{safe_name}"
        }
    })

    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": int(group_id),
            "message": message,
            "auto_escape": False,
        },
        "echo": str(uuid.uuid4()),
    }

    async with send_lock:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    record_bot_group_message(group_id, f"[图片] {safe_name}")


async def upload_group_file(ws, group_id, file_name, mention=False, user_id=None):
    safe_name = os.path.basename(file_name.strip())
    host_path = os.path.join(HOST_SHARE_DIR, safe_name)

    if not safe_name:
        await send_group_msg(ws, group_id, user_id, "未指定文件名。", mention=mention)
        return

    if not os.path.exists(host_path):
        await send_group_msg(ws, group_id, user_id, f"未找到文件：{safe_name}", mention=mention)
        return

    if mention and user_id:
        await send_group_msg(ws, group_id, user_id, f"吾这便上传文件：{safe_name}", mention=True)

    payload = {
        "action": "upload_group_file",
        "params": {
            "group_id": int(group_id),
            "file": f"{CONTAINER_SHARE_DIR}/{safe_name}",
            "name": safe_name,
        },
        "echo": str(uuid.uuid4()),
    }

    async with send_lock:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    record_bot_group_message(group_id, f"[文件] {safe_name}")

async def run_agent_task(ws, group_id, user_id, task_text, mention=False):
    task_key = f"{group_id}:{user_id}"

    if task_key in running_tasks:
        await send_group_msg(ws, group_id, user_id, "汝前一任务尚未终了，且待片刻。", mention=mention)
        return

    running_tasks.add(task_key)

    try:
        await send_group_msg(
            ws,
            group_id,
            user_id,
            "吾已领命，且分步为之。若事繁，吾将逐轮回报。",
            mention=mention,
        )

        messages = [
            {"role": "system", "content": get_task_system_prompt()},
            {"role": "user", "content": f"复杂任务如下：{task_text}\n请开始执行第一步。"},
        ]

        for step in range(1, MAX_TASK_STEPS + 1):
            try:
                answer = await call_openclaw(messages, max_tokens=1200, timeout=600)
            except Exception as e:
                await send_group_msg(ws, group_id, user_id, f"任务中道有阻：{e}", mention=False)
                return

            done = "[DONE]" in answer
            cont = "[CONTINUE]" in answer

            clean = answer.replace("[DONE]", "").replace("[CONTINUE]", "").strip()

            if not clean:
                clean = "此轮无可陈之辞。"

            await send_group_msg(
                ws,
                group_id,
                user_id,
                f"【任务第{step}轮】\n{clean}",
                mention=False,
            )

            if done:
                await send_group_msg(ws, group_id, user_id, "事毕。", mention=False)
                return

            messages.append({"role": "assistant", "content": answer})
            messages.append({
                "role": "user",
                "content": (
                    "继续执行下一步。"
                    "若任务已经完成，请总结并以 [DONE] 结尾；"
                    "若尚未完成，请继续执行并以 [CONTINUE] 结尾。"
                ),
            })

            if not cont and step >= 2:
                await send_group_msg(ws, group_id, user_id, "吾暂止于此。若需续行，请再下新令。", mention=False)
                return

            await asyncio.sleep(1)

        await send_group_msg(
            ws,
            group_id,
            user_id,
            f"吾已行满 {MAX_TASK_STEPS} 轮，恐扰群中清听，暂止于此。若需续行，请再下新令。",
            mention=False,
        )

    finally:
        running_tasks.discard(task_key)


async def periodic_group_review(ws):
    await asyncio.sleep(30)

    while True:
        try:
            await asyncio.sleep(PERIODIC_REVIEW_INTERVAL_SECONDS)

            group_ids = list(group_message_cache.keys())

            if PERIODIC_REVIEW_GROUPS:
                group_ids = [gid for gid in group_ids if gid in PERIODIC_REVIEW_GROUPS]

            group_ids = group_ids[:MAX_GROUPS_PER_REVIEW]

            for group_id in group_ids:
                try:
                    recent_text, max_seq, total_new_count, human_new_count, periodic_image_urls = build_group_recent_text(
                        group_id,
                        limit=80,
                        only_new=True
                    )

                    if not recent_text.strip() or total_new_count == 0:
                        print(f"[定时巡群] group={group_id} no-new-message", flush=True)
                        continue

                    # 如果新增内容只有机器人自己的发言，就消费掉，避免自己触发自己
                    if human_new_count == 0:
                        print(
                            f"[定时巡群] group={group_id} only-bot-message "
                            f"total_new_count={total_new_count}",
                            flush=True
                        )
                        mark_group_periodic_done(group_id, max_seq)
                        continue

                    # 群友新消息太少时先不处理，也不更新 last_seq，让它继续累计到 3 条
                    # 机器人自己的发言只作为上下文，不参与凑触发门槛
                    if human_new_count < 3:
                        print(
                            f"[定时巡群] group={group_id} "
                            f"human_new_count={human_new_count}, "
                            f"total_new_count={total_new_count}, accumulate",
                            flush=True
                        )
                        continue

                    group_memory_context = build_group_memory_context(group_id)

                    prompt = f"""
下面是某QQ群的长期压缩记忆，以及自上次巡群后新增的消息片段。

你要基于“长期压缩记忆 + 新增消息”判断是否值得作为“弹性 / 张智豪”主动接一句。

规则：
1. 如果不值得插话，严格只输出 SILENCE。
2. 如果值得插话，只输出一条简短群聊消息。
3. 不要重复你以前已经说过的话。
4. 不要 @ 任何人。
5. 不要说“我在巡群”“我检索了群消息”。
6. 不要长篇总结，不要像公告。
7. 保持半文言、茶学书生、QQ群友风格。

长期压缩记忆：
{group_memory_context if group_memory_context else "暂无"}

新增消息如下：

{recent_text}
"""

                    periodic_user_content = await build_user_content(
                        prompt,
                        image_urls=periodic_image_urls[:MAX_IMAGES_PER_MESSAGE]
                    )

                    messages = [
                        {"role": "system", "content": get_periodic_system_prompt()},
                        {"role": "user", "content": periodic_user_content},
                    ]

                    try:
                        if periodic_image_urls and USE_DIRECT_MIMO_VISION:
                            answer = await call_mimo_vision(messages, max_tokens=300, timeout=300)
                        else:
                            answer = await call_openclaw(messages, max_tokens=300, timeout=300)
                    except Exception as e:
                        print(f"[定时巡群失败] group={group_id} error={e}", flush=True)
                        mark_group_periodic_done(group_id, max_seq)
                        continue

                    clean = answer.strip()

                    if is_bad_periodic_answer(clean):
                        print(f"[定时巡群] group={group_id} bad-answer: {clean}", flush=True)
                        mark_group_periodic_done(group_id, max_seq)
                        continue

                    if "SILENCE" in clean.upper():
                        print(f"[定时巡群] group={group_id} silence", flush=True)
                        mark_group_periodic_done(group_id, max_seq)
                        continue

                    fp = make_periodic_reply_fingerprint(clean)

                    if fp and fp in periodic_answer_fingerprints[group_id]:
                        print(f"[定时巡群] group={group_id} duplicate-reply: {clean}", flush=True)
                        mark_group_periodic_done(group_id, max_seq)
                        continue

                    if fp:
                        periodic_answer_fingerprints[group_id].append(fp)

                    print(f"[定时巡群发言] group={group_id} text={clean}", flush=True)

                    await send_group_msg(ws, group_id, None, clean, mention=False)

                    mark_group_periodic_done(group_id, max_seq)

                    await asyncio.sleep(2)

                except Exception as e:
                    print(f"[定时巡群内部错误] group={group_id} error={e}", flush=True)
                    continue

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(f"[定时巡群主循环错误] error={e}，5秒后继续", flush=True)
            await asyncio.sleep(5)


async def handle_group_message(ws, event):
    self_id = str(event.get("self_id", ""))
    user_id = str(event.get("user_id", ""))

    if user_id == self_id:
        return

    if is_ignored_sender(event):
        sender = event.get("sender") or {}
        nickname = sender.get("nickname", "") if isinstance(sender, dict) else ""
        card = sender.get("card", "") if isinstance(sender, dict) else ""
        print(f"[忽略群管家] user={user_id} nickname={nickname} card={card}", flush=True)
        return

    group_id = str(event.get("group_id", ""))
    at_me, text, image_urls = parse_message(event)

    if not text:
        text = "你好"

    # 先记录所有群消息，供每3小时巡群使用
    record_group_message(event, text, image_urls=image_urls)

    if not should_reply(at_me, text):
        return

    asset_type, asset_name = parse_send_asset_command(text)

    if asset_type == "image":
        print(f"[发图命令] group={group_id} user={user_id} file={asset_name}", flush=True)
        await send_group_image(
            ws,
            group_id,
            asset_name,
            text="图来。",
            mention=at_me,
            user_id=user_id,
        )
        return

    if asset_type == "file":
        print(f"[发文件命令] group={group_id} user={user_id} file={asset_name}", flush=True)
        await upload_group_file(
            ws,
            group_id,
            asset_name,
            mention=at_me,
            user_id=user_id,
        )
        return

    task_text = extract_task_text(text)

    if task_text:
        if user_id not in ADMIN_QQ_IDS:
            await send_group_msg(ws, group_id, user_id, "此乃任务模式，非执令之人不可启也。", mention=at_me)
            return

        print(f"[收到任务] group={group_id} user={user_id} task={task_text}", flush=True)
        asyncio.create_task(run_agent_task(ws, group_id, user_id, task_text, mention=at_me))
        return

    user_key = f"group-{group_id}-user-{user_id}"
    trigger = "@" if at_me else "关键词"

    print(f"[收到{trigger}] group={group_id} user={user_id} text={text}", flush=True)

    try:
        if not image_urls and image_reference_intent(text):
            image_urls = get_recent_group_image_urls(
                group_id,
                user_id=user_id,
                max_images=MAX_CONTEXT_IMAGES,
                max_age_seconds=RECENT_IMAGE_MAX_AGE_SECONDS
            )

            # QQ/NapCat 有时会先上报文字、后上报图片；没找到图就稍等再找一次
            if not image_urls and IMAGE_WAIT_AFTER_TEXT_SECONDS > 0:
                print(
                    f"[群聊等图] group={group_id} user={user_id} text={text} wait={IMAGE_WAIT_AFTER_TEXT_SECONDS}s",
                    flush=True
                )
                await asyncio.sleep(IMAGE_WAIT_AFTER_TEXT_SECONDS)

                image_urls = get_recent_group_image_urls(
                    group_id,
                    user_id=user_id,
                    max_images=MAX_CONTEXT_IMAGES,
                    max_age_seconds=RECENT_IMAGE_MAX_AGE_SECONDS
                )

            if image_urls:
                print(
                    f"[群聊补图] group={group_id} user={user_id} text={text} images={len(image_urls)}",
                    flush=True
                )
            else:
                print(
                    f"[群聊无图可补] group={group_id} user={user_id} text={text}",
                    flush=True
                )

        answer = await ask_openclaw(user_key, text, group_id=group_id, image_urls=image_urls)
    except Exception as e:
        answer = f"吾调用模型失利：{e}"

    print(f"[回复] {answer}", flush=True)

    await send_group_msg(ws, group_id, user_id, answer, mention=at_me)


async def handle_private_message(ws, event):
    user_id = str(event.get("user_id", ""))
    self_id = str(event.get("self_id", ""))

    if user_id == self_id:
        return

    _at_me, text, image_urls = parse_message(event)

    if not text:
        text = event.get("raw_message", "").strip()

    if not text:
        text = "你好"

    # 当前私聊消息带图：先缓存图片
    if image_urls:
        remember_private_images(user_id, image_urls)

    # 当前消息没图，但用户在问“看图/这图/上图”：自动取最近私聊图片
    if not image_urls and image_reference_intent(text):
        image_urls = get_recent_private_image_urls(
            user_id,
            max_images=MAX_CONTEXT_IMAGES
        )

        if image_urls:
            print(
                f"[私聊补图] user={user_id} text={text} images={len(image_urls)}",
                flush=True
            )

    user_key = f"private-user-{user_id}"

    print(f"[收到私聊] user={user_id} text={text} images={len(image_urls)}", flush=True)

    try:
        answer = await ask_openclaw(user_key, text, image_urls=image_urls)
    except Exception as e:
        answer = f"吾调用模型失利：{e}"

    print(f"[私聊回复] {answer}", flush=True)

    await send_private_msg(ws, user_id, answer)


async def handle_event(ws, event):
    if event.get("post_type") != "message":
        return

    message_type = event.get("message_type")

    if message_type == "group":
        await handle_group_message(ws, event)
        return

    if message_type == "private":
        await handle_private_message(ws, event)
        return


async def main():
    load_periodic_state()

    while True:
        periodic_task = None
        memory_task = None

        try:
            print(f"连接 NapCat OneBot: {ONEBOT_WS}", flush=True)

            async with websockets.connect(
                ONEBOT_WS,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                print("已连接 NapCat OneBot", flush=True)

                periodic_task = asyncio.create_task(periodic_group_review(ws))
                memory_task = asyncio.create_task(periodic_group_memory_compaction())

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue

                    if "post_type" not in event:
                        continue

                    asyncio.create_task(handle_event(ws, event))

        except Exception as e:
            print(f"连接断开：{e}，5秒后重连", flush=True)
            await asyncio.sleep(5)

        finally:
            if periodic_task:
                periodic_task.cancel()
            if memory_task:
                memory_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
PY