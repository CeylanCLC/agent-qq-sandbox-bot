"""Display configuration for the optional Flask control plane."""
import os


SITE_DISPLAY_NAME = os.getenv("SITE_DISPLAY_NAME", "AI Bot Console").strip() or "AI Bot Console"
SITE_TAGLINE = os.getenv("SITE_TAGLINE", "BOT OPERATIONS").strip() or "BOT OPERATIONS"
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "QQ AI 助手").strip() or "QQ AI 助手"
BOT_NODE_LABEL = os.getenv("BOT_NODE_LABEL", "机器人节点").strip() or "机器人节点"
