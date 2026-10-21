import discord
import logging
from typing import Optional
from config import COLOR_ACCENT, FOOTER_TEXT
from database import Database

logger = logging.getLogger("framex.staff")

class StaffManager:
    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db

    async def notify_staff_service_request(self, guild: discord.Guild, ticket_channel: discord.TextChannel, 
                                          creator: discord.User, service_type: str):
        """
        Notifies staff about Paid Services or Help & Support requests.
        Dispatches both to configured fallback channel and directly via DM to staff members.
        """
        settings = await self.db.get_guild_settings(guild.id)
        staff_roles = await self.db.get_staff_roles(guild.id)

        title = "💼 NEW PAID SERVICE REQUEST" if service_type == "PAID_SERVICES" else "🆘 NEW HELP & SUPPORT REQUEST"
        desc = (
            f"**User:** {creator.mention} (`{creator.id}`)\n"
            f"**Service:** `{service_type}`\n"
            f"**Ticket Channel:** {ticket_channel.mention}\n\n"
            f"[👉 Click Here to Go to Ticket]({ticket_channel.jump_url})"
        )

        embed = discord.Embed(
            title=title,
            description=desc,
            color=COLOR_ACCENT,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=FOOTER_TEXT)

        # 1. Post to staff notification channel if configured
        if settings and settings.get("staff_notification_channel_id"):
            channel_id = settings["staff_notification_channel_id"]
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Failed to send staff alert to channel {channel_id}: {e}")

        # 2. DM members who have staff roles configured
        for role_entry in staff_roles:
            role = guild.get_role(role_entry["role_id"])
            if not role:
                continue

            for member in role.members:
                if member.bot:
                    continue
                try:
                    await member.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    # User DMs closed or blocked; silently proceed as channel alert serves as fallback
                    pass
