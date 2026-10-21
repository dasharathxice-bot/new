import os
from dotenv import load_dotenv

load_dotenv()

# Bot Token & Core Settings
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "framex_data.db")

# Brand / Design tokens
COLOR_PRIMARY = 0x0A0A0C       # Deep onyx
COLOR_ACCENT = 0x5865F2        # Blurple / FrameX neon blue
COLOR_SUCCESS = 0x2ECC71       # Emerald green
COLOR_WARNING = 0xF1C40F       # Amber
COLOR_DANGER = 0xE74C3C        # Crimson

BRAND_TITLE = "FRAME X // SERVICE CENTER"
FOOTER_TEXT = "FrameX Automation System • High Performance Tuning"
BOT_STATUS_TEXT = "Automating Framex"

# Permanent Mandatory Proof Requirements (CANNOT be removed or disabled)
PERMANENT_PROOF_IDS = {"youtube_sub", "instagram_follow"}

DEFAULT_PROOF_LINKS = [
    {
        "id": "youtube_sub",
        "name": "YouTube Subscription",
        "url": "https://youtube.com/@framexfpsz",
        "instructions": "Subscribe to our YouTube channel (@framexfpsz) and upload a full screenshot showing the 'Subscribed' button clearly.",
        "is_mandatory": 1,
        "is_enabled": 1,
        "verification_method": "ocr_vision",
        "verification_policy": "strict"
    },
    {
        "id": "instagram_follow",
        "name": "Instagram Follow",
        "url": "https://instagram.com/framex.hz",
        "instructions": "Follow our Instagram account (@framex.hz) and upload a screenshot showing the 'Following' status clearly.",
        "is_mandatory": 1,
        "is_enabled": 1,
        "verification_method": "ocr_vision",
        "verification_policy": "strict"
    }
]
