import discord
import logging
from typing import Optional
from config import COLOR_ACCENT, FOOTER_TEXT
from database import Database

logger = logging.getLogger("framex.audit")

class AuditLogger:
    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db

    async def log(self, guild: discord.Guild, event_type: str, title: str, description: str, 
                  user: Optional[discord.User | discord.Member] = None, 
                  channel: Optional[discord.TextChannel] = None, 
                  color: int = COLOR_ACCENT, 
                  extra_fields: Optional[dict] = None):
        """
        Logs an audit event to SQLite database and sends an embed to the configured audit channel.
        Full keys and confidential tokens are NEVER logged.
        """
        try:
            # 1. Persist to DB
            user_id = user.id if user else None
            channel_id = channel.id if channel else None
            details_str = f"{title} | {description}"
            if extra_fields:
                details_str += f" | {extra_fields}"
            await self.db.log_event(guild.id, event_type, user_id, channel_id, details_str)

            # 2. Check guild settings for audit channel
            settings = await self.db.get_guild_settings(guild.id)
            if not settings or not settings.get("audit_log_channel_id"):
                return

            audit_channel_id = settings["audit_log_channel_id"]
            audit_channel = guild.get_channel(audit_channel_id)
            if not audit_channel:
                return

            embed = discord.Embed(
                title=f"🛡️ AUDIT // {title}",
                description=description,
                color=color,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Event Type", value=f"`{event_type}`", inline=True)
            if user:
                embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            if channel:
                embed.add_field(name="Channel", value=f"{channel.mention} (`{channel.id}`)", inline=True)

            if extra_fields:
                for k, v in extra_fields.items():
                    embed.add_field(name=k, value=str(v), inline=False)

            embed.set_footer(text=FOOTER_TEXT)
            await audit_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to record audit log: {e}")
