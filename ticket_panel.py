import discord
from discord import ui
import logging
from typing import Optional, List, Dict, Any
from config import BRAND_TITLE, COLOR_PRIMARY, COLOR_ACCENT, COLOR_SUCCESS, COLOR_WARNING, FOOTER_TEXT
from database import Database

logger = logging.getLogger("framex.panel")

class ServiceSelectionView(ui.View):
    def __init__(self, bot, db: Database, staff_manager, audit_logger):
        super().__init__(timeout=None)
        self.bot = bot
        self.db = db
        self.staff_manager = staff_manager
        self.audit_logger = audit_logger

    @ui.button(label="FREE OPTIMIZER KEY", style=discord.ButtonStyle.primary, emoji="🎁", custom_id="btn_free_key")
    async def free_key_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        ticket = await self.db.get_ticket(interaction.channel_id)
        creator_id = ticket["creator_id"] if ticket else interaction.user.id

        if interaction.user.id != creator_id and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("⚠️ Only the ticket creator or staff can select a service for this ticket.", ephemeral=True)
            return

        # Update service type
        await self.db.set_ticket_service(interaction.channel_id, "FREE_KEY")

        # Seed & fetch active proof links
        await self.db.seed_default_proof_links(interaction.guild_id)
        links = await self.db.get_proof_links(interaction.guild_id, only_enabled=True)

        embed = discord.Embed(
            title="🎁 FRAME X // FREE OPTIMIZER KEY VERIFICATION",
            description=(
                f"Welcome {interaction.user.mention}!\n\n"
                "To claim your **Free FrameX Optimizer Key**, you must complete our mandatory verification steps below.\n"
                "Upload a clear screenshot for each requirement right here in this ticket channel.\n\n"
                "**Status Legend:**\n"
                "⚪ `NOT SUBMITTED` • 🟡 `CHECKING` / `NEEDS REVIEW` • 🟢 `VERIFIED` • 🔴 `NEEDS REUPLOAD`"
            ),
            color=COLOR_PRIMARY
        )

        for item in links:
            req_name = item["name"]
            url = item["url"]
            instructions = item["instructions"]
            mand_tag = "Mandatory" if item.get("is_mandatory") else "Optional"
            embed.add_field(
                name=f"📌 {req_name} ({mand_tag})",
                value=f"**Link:** [Visit Page]({url})\n**Instructions:** {instructions}\n**Current Status:** ⚪ `NOT SUBMITTED`",
                inline=False
            )

        embed.set_footer(text=FOOTER_TEXT)
        view = ProofSubmissionActionsView(self.bot, self.db)
        await interaction.channel.send(embed=embed, view=view)

        await self.audit_logger.log(
            interaction.guild,
            "SERVICE_SELECTED",
            "Free Key Workflow Started",
            f"User {interaction.user.mention} initiated Free Optimizer Key verification.",
            user=interaction.user,
            channel=interaction.channel
        )

    @ui.button(label="PAID SERVICES", style=discord.ButtonStyle.secondary, emoji="💼", custom_id="btn_paid_services")
    async def paid_services_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        ticket = await self.db.get_ticket(interaction.channel_id)
        creator_id = ticket["creator_id"] if ticket else interaction.user.id

        if interaction.user.id != creator_id and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("⚠️ Only the ticket creator can select a service.", ephemeral=True)
            return

        await self.db.set_ticket_service(interaction.channel_id, "PAID_SERVICES")

        embed = discord.Embed(
            title="💼 FRAME X // PAID SERVICES",
            description=(
                f"Thank you for contacting FrameX Support, {interaction.user.mention}!\n\n"
                "Our team offers premium tuning, private builds, and custom optimization packages.\n"
                "Our staff team has been alerted and will assist you shortly in this channel."
            ),
            color=COLOR_ACCENT
        )
        embed.set_footer(text=FOOTER_TEXT)
        view = TicketClaimView(self.bot, self.db)
        await interaction.channel.send(embed=embed, view=view)

        # Dispatch staff notifications
        await self.staff_manager.notify_staff_service_request(
            interaction.guild, interaction.channel, interaction.user, "PAID_SERVICES"
        )
        await self.audit_logger.log(
            interaction.guild,
            "SERVICE_SELECTED",
            "Paid Services Requested",
            f"User {interaction.user.mention} requested Paid Services. Staff notified.",
            user=interaction.user,
            channel=interaction.channel
        )

    @ui.button(label="HELP & SUPPORT", style=discord.ButtonStyle.secondary, emoji="🆘", custom_id="btn_help_support")
    async def help_support_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        ticket = await self.db.get_ticket(interaction.channel_id)
        creator_id = ticket["creator_id"] if ticket else interaction.user.id

        if interaction.user.id != creator_id and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("⚠️ Only the ticket creator can select a service.", ephemeral=True)
            return

        await self.db.set_ticket_service(interaction.channel_id, "HELP_SUPPORT")

        embed = discord.Embed(
            title="🆘 FRAME X // HELP & SUPPORT",
            description=(
                f"Hello {interaction.user.mention},\n\n"
                "Please describe your issue or question in detail below.\n"
                "Include any error messages, logs, or system specs if relevant. Our support staff has been notified."
            ),
            color=COLOR_ACCENT
        )
        embed.set_footer(text=FOOTER_TEXT)
        view = TicketClaimView(self.bot, self.db)
        await interaction.channel.send(embed=embed, view=view)

        # Dispatch staff notifications
        await self.staff_manager.notify_staff_service_request(
            interaction.guild, interaction.channel, interaction.user, "HELP_SUPPORT"
        )
        await self.audit_logger.log(
            interaction.guild,
            "SERVICE_SELECTED",
            "Help & Support Requested",
            f"User {interaction.user.mention} requested Help & Support. Staff notified.",
            user=interaction.user,
            channel=interaction.channel
        )


class TicketClaimView(ui.View):
    def __init__(self, bot, db: Database):
        super().__init__(timeout=None)
        self.bot = bot
        self.db = db

    @ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="🙋‍♂️", custom_id="btn_claim_ticket")
    async def claim_button(self, interaction: discord.Interaction, button: ui.Button):
        ticket = await self.db.get_ticket(interaction.channel_id)
        if ticket and ticket.get("claimed_by_id"):
            claimed_by = interaction.guild.get_member(ticket["claimed_by_id"])
            claimer_name = claimed_by.mention if claimed_by else f"<@{ticket['claimed_by_id']}>"
            await interaction.response.send_message(f"⚠️ This ticket has already been claimed by {claimer_name}.", ephemeral=True)
            return

        await self.db.claim_ticket(interaction.channel_id, interaction.user.id)
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"✅ {interaction.user.mention} has claimed this ticket and will be handling your request!")


class ProofSubmissionActionsView(ui.View):
    def __init__(self, bot, db: Database):
        super().__init__(timeout=None)
        self.bot = bot
        self.db = db

    @ui.button(label="Check Verification Progress", style=discord.ButtonStyle.primary, emoji="🔍", custom_id="btn_check_progress")
    async def check_progress(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        submissions = await self.db.get_proof_submissions(interaction.channel_id)
        links = await self.db.get_proof_links(interaction.guild_id, only_enabled=True)

        sub_map = {s["requirement_id"]: s for s in submissions}
        all_verified = True

        embed = discord.Embed(
            title="📊 Current Proof Verification Status",
            color=COLOR_PRIMARY
        )

        for l in links:
            req_id = l["id"]
            name = l["name"]
            is_mand = bool(l["is_mandatory"])
            sub = sub_map.get(req_id)
            status = sub["status"] if sub else "NOT_SUBMITTED"

            if is_mand and status != "VERIFIED":
                all_verified = False

            status_icons = {
                "NOT_SUBMITTED": "⚪ `NOT SUBMITTED`",
                "RECEIVED": "🟡 `RECEIVED`",
                "CHECKING": "🟡 `CHECKING`",
                "NEEDS_REUPLOAD": "🔴 `NEEDS REUPLOAD`",
                "NEEDS_REVIEW": "🟠 `NEEDS REVIEW (Staff)`",
                "VERIFIED": "🟢 `VERIFIED`",
                "REJECTED": "❌ `REJECTED`"
            }
            icon_str = status_icons.get(status, f"`{status}`")
            note_str = f"\n*Notes: {sub['review_notes']}*" if sub and sub.get("review_notes") else ""

            embed.add_field(
                name=f"{name} {'(Mandatory)' if is_mand else '(Optional)'}",
                value=f"Status: {icon_str}{note_str}",
                inline=False
            )

        if all_verified and len(links) > 0:
            embed.description = "🎉 **All mandatory requirements have been verified!** Your key will be issued shortly."
        else:
            embed.description = "Please upload missing or requested screenshots directly to this channel."

        await interaction.followup.send(embed=embed, ephemeral=True)


def build_service_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=BRAND_TITLE,
        description=(
            "Welcome to FrameX Support.\n"
            "Select the service you need below. Follow the instructions provided for your selected service.\n\n"
            "**Permanent Mandatory Free Key Proofs:**\n"
            "• **YouTube Subscription:** [youtube.com/@framexfpsz](https://youtube.com/@framexfpsz)\n"
            "• **Instagram Follow:** [instagram.com/framex.hz](https://instagram.com/framex.hz)\n\n"
            "**Select an option below:**\n"
            "• **FREE OPTIMIZER KEY** — Automated verification & key distribution.\n"
            "• **PAID SERVICES** — Custom tweaks, premium builds, and priority staff support.\n"
            "• **HELP & SUPPORT** — General assistance and troubleshooting."
        ),
        color=COLOR_PRIMARY
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed
