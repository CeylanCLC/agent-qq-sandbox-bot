"""Public, environment-driven branding and endpoint configuration."""
import os


def csv_set(name, default=""):
    return {item.strip() for item in os.getenv(name, default).split(",") if item.strip()}


BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "QQ AI 助手").strip() or "QQ AI 助手"
BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", BOT_DISPLAY_NAME).strip() or BOT_DISPLAY_NAME
BOT_PERSONA = os.getenv(
    "BOT_PERSONA",
    "你是群聊里的 AI 助手。说话自然、友善、简洁，不暴露内部实现。",
).strip()
BOT_TRIGGER_WORDS = csv_set("BOT_TRIGGER_WORDS", BOT_DISPLAY_NAME)
BLOCKED_GROUP_IDS = csv_set("BLOCKED_GROUP_IDS")
PUBLIC_BASE_URL = os.getenv("BOT_CONTROL_BASE_URL", "").strip().rstrip("/")


def api_url(path, explicit_env):
    explicit = os.getenv(explicit_env, "").strip()
    if explicit:
        return explicit
    if not PUBLIC_BASE_URL:
        raise RuntimeError(f"Set {explicit_env} or BOT_CONTROL_BASE_URL")
    return PUBLIC_BASE_URL + path
