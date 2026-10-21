import discord
import logging
import asyncio
import re
from typing import Optional, Tuple
from database import Database
from ticket_panel import build_service_panel_embed, ServiceSelectionView

logger = logging.getLogger("framex.ticket_king")

class TicketKingIntegration:
    def __init__(self, bot: discord.Client, db: Database, staff_manager, audit_logger):
        self.bot = bot
        self.db = db
        self.staff_manager = staff_manager
        self.audit_logger = audit_logger
        self._processing_channels = set()

    def is_ticket_name(self, channel_name: str) -> bool:
        """Check if channel name aligns with Ticket King user ticket patterns."""
        name = channel_name.lower()
        excluded_keywords = [
            "closed", "archive", "transcript", "transcripts", "deleted", 
            "general", "chat", "rules", "announcement", "announcements",
            "log", "logs", "management", "admin", "staff", "setup", 
            "panel", "panels", "command", "commands", "cmd"
        ]
        if any(kw in name for kw in excluded_keywords):
            return False

        # Common Ticket King ticket prefix/patterns
        patterns = [
            r"ticket[-_]\d+",
            r"claim[-_]\d+",
            r"order[-_]\d+",
            r"support[-_]\d+",
            r"help[-_]\d+",
            r"^ticket[-_][a-z0-9]+",
            r"^claim[-_][a-z0-9]+",
            r"^order[-_][a-z0-9]+",
            r"^support[-_][a-z0-9]+",
            r"^t-\d+",
            r"^ticket\d+"
        ]
        return any(re.search(p, name) for p in patterns)

    async def inspect_channel_for_ticket_king(self, channel: discord.TextChannel) -> Tuple[bool, Optional[int]]:
        """
        Reliably inspects channel messages, overwrites, and topic to determine
        if this is an active Ticket King ticket channel and who the ticket creator is.
        Returns: (is_ticket, creator_id)
        """
        try:
            # 1. Inspect recent messages (Ticket King posts a welcome embed upon ticket creation)
            async for msg in channel.history(limit=7, oldest_first=True):
                # Check if message is from a bot
                if msg.author.bot:
                    bot_name = msg.author.name.lower()
                    is_tk_bot = ("ticket king" in bot_name) or ("ticket" in bot_name)
                    
                    # Check embed content and components
                    has_tk_embed = False
                    for emb in msg.embeds:
                        emb_text = f"{emb.title or ''} {emb.description or ''} {emb.footer.text if emb.footer else ''}".lower()
                        if any(kw in emb_text for kw in ["ticket king", "ticket created", "support ticket", "ticket #", "close this ticket", "ticket-"]):
                            has_tk_embed = True
                            break

                    # Check interactive components/buttons (e.g. Close, Claim)
                    has_tk_components = False
                    if hasattr(msg, "components") and msg.components:
                        for row in msg.components:
                            for comp in getattr(row, "children", []):
                                label = (getattr(comp, "label", "") or "").lower()
                                cid = (getattr(comp, "custom_id", "") or "").lower()
                                if any(kw in label for kw in ["close", "claim", "lock"]) or any(kw in cid for kw in ["ticket", "close", "claim"]):
                                    has_tk_components = True
                                    break

                    if is_tk_bot or has_tk_embed or has_tk_components:
                        # Find creator mentioned in message or embed
                        creator_id = None
                        if msg.mentions:
                            for m in msg.mentions:
                                if not m.bot:
                                    creator_id = m.id
                                    break

                        if not creator_id:
                            # Try parsing user ID from embed text e.g. <@123456789> or User ID: 123456789
                            for emb in msg.embeds:
                                text_to_search = f"{emb.description or ''} {emb.title or ''}"
                                user_match = re.search(r"<@!?(\d{17,20})>", text_to_search) or re.search(r"user(?:\s*id)?:\s*(\d{17,20})", text_to_search, re.IGNORECASE)
                                if user_match:
                                    creator_id = int(user_match.group(1))
                                    break

                        return True, creator_id

            # 2. Inspect Channel Overwrites
            # Ticket King creates channels where a bot and a single non-admin member have explicit permissions
            candidate_creator_id = None
            has_bot_overwrite = False
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member):
                    if target.bot and ("ticket" in target.name.lower() or target.guild_permissions.manage_channels):
                        has_bot_overwrite = True
                    elif not target.bot and not target.guild_permissions.administrator:
                        if overwrite.view_channel or overwrite.read_messages:
                            candidate_creator_id = target.id

            if (has_bot_overwrite or self.is_ticket_name(channel.name)) and candidate_creator_id:
                return True, candidate_creator_id

            # 3. Channel Topic inspection
            if channel.topic:
                topic_lower = channel.topic.lower()
                if "ticket" in topic_lower or "ticket king" in topic_lower:
                    match = re.search(r"(\d{17,20})", channel.topic)
                    return True, int(match.group(1)) if match else None

            # 4. If channel name strongly indicates a ticket
            if self.is_ticket_name(channel.name):
                return True, candidate_creator_id

            return False, None
        except Exception as e:
            logger.warning(f"Error inspecting channel {channel.id}: {e}")
            return False, None

    async def handle_new_ticket(self, channel: discord.TextChannel, creator_id: Optional[int] = None):
        """
        Posts the FrameX service-selection panel in the Ticket King ticket channel idempotently.
        """
        if channel.id in self._processing_channels:
            return
        self._processing_channels.add(channel.id)

        try:
            # Check if ticket already registered in database
            existing = await self.db.get_ticket(channel.id)
            if existing and existing.get("panel_message_id"):
                return

            guild = channel.guild
            creator_id = creator_id or (existing.get("creator_id") if existing else None) or guild.owner_id

            # Register ticket in SQLite
            await self.db.register_ticket(channel.id, guild.id, creator_id)

            # Post the FrameX Service Selection Embed
            embed = build_service_panel_embed()
            view = ServiceSelectionView(self.bot, self.db, self.staff_manager, self.audit_logger)
            panel_msg = await channel.send(embed=embed, view=view)

            # Record panel message ID
            await self.db.set_ticket_panel_message(channel.id, panel_msg.id)

            await self.audit_logger.log(
                guild,
                "TICKET_DETECTED",
                "Ticket King Ticket Integrated",
                f"Automatically attached FrameX Service Panel in {channel.mention}.",
                channel=channel,
                extra_fields={"Creator ID": creator_id}
            )
            logger.info(f"FrameX panel posted in ticket channel: {channel.name} ({channel.id})")

        except Exception as e:
            logger.error(f"Failed to post panel in ticket {channel.id}: {e}")
        finally:
            self._processing_channels.discard(channel.id)

    async def monitor_and_integrate_channel(self, channel: discord.TextChannel):
        """
        Retries detection across a 15-second window to allow Ticket King
        to set channel permissions and send its welcome embed.
        """
        # Retry delays in seconds
        delays = [1.0, 2.0, 3.0, 4.0, 5.0]
        for delay in delays:
            await asyncio.sleep(delay)
            # Check if already processed
            existing = await self.db.get_ticket(channel.id)
            if existing and existing.get("panel_message_id"):
                return

            is_ticket, creator_id = await self.inspect_channel_for_ticket_king(channel)
            if is_ticket:
                logger.info(f"Ticket King ticket confirmed for #{channel.name} (creator: {creator_id})")
                await self.handle_new_ticket(channel, creator_id)
                return

        logger.debug(f"Channel #{channel.name} was not identified as a Ticket King ticket after retry window.")

    async def reconcile_open_tickets(self, guild: discord.Guild):
        """
        Reconciles existing open Ticket King tickets upon bot boot or restart.
        Ensures panels are never duplicated.
        """
        logger.info(f"Reconciling open tickets for guild: {guild.name} ({guild.id})")
        for channel in guild.text_channels:
            if not self.is_ticket_name(channel.name):
                continue

            existing = await self.db.get_ticket(channel.id)
            if existing and existing.get("panel_message_id"):
                continue

            is_ticket, creator_id = await self.inspect_channel_for_ticket_king(channel)
            if is_ticket:
                logger.info(f"Reconciling existing Ticket King ticket #{channel.name}")
                await self.handle_new_ticket(channel, creator_id)
