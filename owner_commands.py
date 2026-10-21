import discord
from discord import app_commands
from discord.ext import commands
import logging
import os
import io
import re
from PIL import Image
from typing import Optional
from database import Database
from config import COLOR_ACCENT, COLOR_SUCCESS, COLOR_DANGER, COLOR_PRIMARY, FOOTER_TEXT, PERMANENT_PROOF_IDS

logger = logging.getLogger("framex.owner")

def is_guild_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild and interaction.user.id == interaction.guild.owner_id:
            return True
        await interaction.response.send_message("❌ **Access Denied:** Only the Discord server owner can execute this command.", ephemeral=True)
        return False
    return app_commands.check(predicate)

class OwnerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, audit_logger):
        self.bot = bot
        self.db = db
        self.audit_logger = audit_logger
        os.makedirs("assets/upi", exist_ok=True)

    # ------------------ /freekey Group ------------------
    freekey_group = app_commands.Group(name="freekey", description="Manage free optimizer key inventory")

    @freekey_group.command(name="add", description="Add one or more free optimizer keys (space or comma separated)")
    @is_guild_owner()
    async def freekey_add(self, interaction: discord.Interaction, keys: str):
        await interaction.response.defer(ephemeral=True)
        key_list = [k.strip() for k in keys.replace(",", " ").split() if k.strip()]
        if not key_list:
            await interaction.followup.send("⚠️ No valid keys provided.", ephemeral=True)
            return

        added_count = await self.db.add_free_keys(interaction.guild_id, key_list)
        await interaction.followup.send(f"✅ Successfully added **{added_count}** new keys to the inventory.", ephemeral=True)

        await self.audit_logger.log(
            interaction.guild,
            "KEY_INVENTORY_ADDED",
            "Free Keys Added",
            f"Server owner added {added_count} keys to stock.",
            user=interaction.user
        )

    @freekey_group.command(name="add-account", description="Assign a free optimizer account with USER and PASS (Owner only)")
    @is_guild_owner()
    async def freekey_add_account(self, interaction: discord.Interaction, user: str, password: str):
        await interaction.response.defer(ephemeral=True)
        clean_user = user.strip()
        clean_pass = password.strip()
        if not clean_user or not clean_pass:
            await interaction.followup.send("⚠️ USER and PASS cannot be empty.", ephemeral=True)
            return

        ok = await self.db.add_account_credentials(interaction.guild_id, clean_user, clean_pass)
        if ok:
            await interaction.followup.send(f"✅ Successfully registered optimizer account:\n**USER :** `{clean_user}`\n**PASS :** `{clean_pass}`", ephemeral=True)
            await self.audit_logger.log(
                interaction.guild,
                "KEY_ACCOUNT_ADDED",
                "Optimizer Account Added",
                f"Owner added optimizer account credentials for user `{clean_user}`.",
                user=interaction.user
            )
        else:
            await interaction.followup.send("❌ Error adding account. It may already exist in the inventory.", ephemeral=True)

    @freekey_group.command(name="stock", description="Check current free optimizer key inventory counts")
    @is_guild_owner()
    async def freekey_stock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        stock = await self.db.get_key_stock(interaction.guild_id)
        embed = discord.Embed(
            title="🔑 Optimizer Key Inventory Stock",
            color=COLOR_ACCENT,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Available Stock", value=f"🟢 **{stock['available']}**", inline=True)
        embed.add_field(name="Claimed / Issued", value=f"🔵 **{stock['issued']}**", inline=True)
        embed.add_field(name="Total Keys", value=f"📦 **{stock['total']}**", inline=True)
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------ /proof-link Group ------------------
    proof_link_group = app_commands.Group(name="proof-link", description="Manage external proof link requirements")

    @proof_link_group.command(name="add", description="Add an additional external proof link requirement")
    @is_guild_owner()
    async def proof_link_add(self, interaction: discord.Interaction, req_id: str, name: str, url: str, instructions: str, mandatory: bool = True):
        await interaction.response.defer(ephemeral=True)
        clean_id = req_id.lower().strip()
        if clean_id in PERMANENT_PROOF_IDS:
            await interaction.followup.send("❌ Cannot overwrite permanent system proof requirements (`youtube_sub` or `instagram_follow`).", ephemeral=True)
            return

        if not (url.startswith("https://") or url.startswith("http://")):
            await interaction.followup.send("❌ URL must start with `http://` or `https://`.", ephemeral=True)
            return

        success = await self.db.add_proof_link(interaction.guild_id, clean_id, name, url, instructions, 1 if mandatory else 0)
        if success:
            await interaction.followup.send(f"✅ Additional proof requirement `{name}` (`{clean_id}`) successfully registered.", ephemeral=True)
            await self.audit_logger.log(
                interaction.guild,
                "PROOF_LINK_ADDED",
                "Additional Proof Link Added",
                f"Owner added requirement `{name}` ({url}).",
                user=interaction.user
            )
        else:
            await interaction.followup.send("❌ Could not add proof requirement. Make sure the ID is unique.", ephemeral=True)

    @proof_link_group.command(name="list", description="List all configured proof links")
    @is_guild_owner()
    async def proof_link_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        links = await self.db.get_proof_links(interaction.guild_id)
        if not links:
            await interaction.followup.send("No proof links configured for this server yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔗 Active & Configured Proof Requirements",
            color=COLOR_ACCENT
        )
        for l in links:
            is_perm = l.get("id") in PERMANENT_PROOF_IDS
            badge = "🔒 Permanent Default" if is_perm else ("🟢 Active" if l["is_enabled"] else "🔴 Disabled")
            mand = "Mandatory" if l["is_mandatory"] else "Optional"
            embed.add_field(
                name=f"{l['name']} (`{l['id']}`) — {badge}",
                value=f"**URL:** {l['url']}\n**Type:** {mand}\n**Instructions:** {l['instructions']}",
                inline=False
            )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @proof_link_group.command(name="remove", description="Remove an additional proof requirement (permanent links cannot be removed)")
    @is_guild_owner()
    async def proof_link_remove(self, interaction: discord.Interaction, req_id: str):
        await interaction.response.defer(ephemeral=True)
        clean_id = req_id.lower().strip()
        if clean_id in PERMANENT_PROOF_IDS:
            await interaction.followup.send("❌ **Action Prohibited:** YouTube and Instagram proof links are permanent mandatory requirements and cannot be removed.", ephemeral=True)
            return

        ok = await self.db.remove_proof_link(interaction.guild_id, clean_id)
        if ok:
            await interaction.followup.send(f"🗑️ Additional requirement `{clean_id}` removed.", ephemeral=True)
            await self.audit_logger.log(
                interaction.guild,
                "PROOF_LINK_REMOVED",
                "Proof Link Removed",
                f"Owner removed requirement `{clean_id}`.",
                user=interaction.user
            )
        else:
            await interaction.followup.send(f"❌ Requirement `{clean_id}` not found.", ephemeral=True)

    # ------------------ /staff-role Group ------------------
    staff_role_group = app_commands.Group(name="staff-role", description="Manage staff roles for alerts")

    @staff_role_group.command(name="add", description="Add a staff role to receive ticket alerts")
    @is_guild_owner()
    async def staff_role_add(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        ok = await self.db.add_staff_role(interaction.guild_id, role.id, role.name)
        if ok:
            await interaction.followup.send(f"✅ Staff role {role.mention} added.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Error adding staff role.", ephemeral=True)

    @staff_role_group.command(name="remove", description="Remove a staff role")
    @is_guild_owner()
    async def staff_role_remove(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        ok = await self.db.remove_staff_role(interaction.guild_id, role.id)
        if ok:
            await interaction.followup.send(f"🗑️ Staff role {role.name} removed.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Role not found in staff role registry.", ephemeral=True)

    @staff_role_group.command(name="list", description="List configured staff roles")
    @is_guild_owner()
    async def staff_role_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        roles = await self.db.get_staff_roles(interaction.guild_id)
        if not roles:
            await interaction.followup.send("No staff roles configured yet.", ephemeral=True)
            return

        desc = "\n".join([f"• <@&{r['role_id']}> (`{r['role_id']}`)" for r in roles])
        embed = discord.Embed(
            title="🛡️ Registered Staff Roles",
            description=desc,
            color=COLOR_ACCENT
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------ /upi Group (Owner Only) ------------------
    upi_group = app_commands.Group(name="upi", description="Configure server UPI payment settings")

    @upi_group.command(name="set-id", description="Set or update the FrameX UPI ID (Owner only)")
    @is_guild_owner()
    async def upi_set_id(self, interaction: discord.Interaction, upi_id: str):
        await interaction.response.defer(ephemeral=True)
        clean_upi = upi_id.strip()

        # Validate UPI format: must contain '@' and valid characters
        if not re.match(r"^[\w\.\-]+@[\w\-]+$", clean_upi):
            await interaction.followup.send("❌ Invalid UPI ID format. Example: `username@bank` or `framex@upi`.", ephemeral=True)
            return

        await self.db.set_upi_id(interaction.guild_id, clean_upi, interaction.user.id)
        await interaction.followup.send(f"✅ UPI ID successfully updated to: `{clean_upi}`", ephemeral=True)
        await self.audit_logger.log(
            interaction.guild,
            "UPI_CONFIG_UPDATED",
            "UPI ID Updated",
            f"Server owner updated UPI ID to `{clean_upi}`.",
            user=interaction.user
        )

    @upi_group.command(name="set-qr", description="Upload or replace the UPI QR code image (Owner only)")
    @is_guild_owner()
    async def upi_set_qr(self, interaction: discord.Interaction, image: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        
        # Validation checks
        if not image.content_type or not any(image.content_type.startswith(t) for t in ["image/png", "image/jpeg", "image/webp"]):
            await interaction.followup.send("❌ Invalid file format. Please upload a PNG, JPG, or WEBP image.", ephemeral=True)
            return

        if image.size > 10 * 1024 * 1024:
            await interaction.followup.send("❌ Image file size too large (maximum 10MB).", ephemeral=True)
            return

        try:
            img_bytes = await image.read()
            # Verify image integrity with PIL
            pil_img = Image.open(io.BytesIO(img_bytes))
            pil_img.verify()

            # Atomic file swap: write to temporary path first, then rename
            target_path = os.path.abspath(f"assets/upi/upi_qr_{interaction.guild_id}.png")
            temp_path = f"{target_path}.tmp"

            # Re-open for saving
            pil_img = Image.open(io.BytesIO(img_bytes))
            pil_img.save(temp_path, format="PNG")
            
            # Atomic rename (on Windows os.replace replaces target safely)
            os.replace(temp_path, target_path)

            await self.db.set_upi_qr(interaction.guild_id, target_path, interaction.user.id)
            await interaction.followup.send("✅ UPI QR Code image validated and saved persistently.", ephemeral=True)
            await self.audit_logger.log(
                interaction.guild,
                "UPI_CONFIG_UPDATED",
                "UPI QR Code Updated",
                "Server owner uploaded a new verified UPI QR code image.",
                user=interaction.user
            )
        except Exception as e:
            logger.error(f"Error saving UPI QR code: {e}")
            await interaction.followup.send(f"❌ Failed to process QR image: {e}", ephemeral=True)

    @upi_group.command(name="settings", description="View current UPI payment configuration status (Owner only)")
    @is_guild_owner()
    async def upi_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cfg = await self.db.get_upi_config(interaction.guild_id)
        
        upi_id_display = f"`{cfg['upi_id']}`" if cfg and cfg.get("upi_id") else "❌ *Not Configured*"
        
        qr_status = "❌ *Not Configured*"
        if cfg and cfg.get("qr_file_path") and os.path.exists(cfg["qr_file_path"]):
            file_size_kb = os.path.getsize(cfg["qr_file_path"]) / 1024
            qr_status = f"🟢 Configured ({file_size_kb:.1f} KB stored locally)"

        embed = discord.Embed(
            title="⚙️ FrameX UPI Payment Configuration",
            color=COLOR_ACCENT,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="UPI ID", value=upi_id_display, inline=False)
        embed.add_field(name="QR Code Image", value=qr_status, inline=False)
        if cfg and cfg.get("updated_at"):
            embed.add_field(name="Last Updated", value=str(cfg["updated_at"]), inline=False)
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------ /qrupi (Public Server Command) ------------------
    @app_commands.command(name="qrupi", description="Display FrameX UPI payment details and QR code")
    async def qrupi_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        cfg = await self.db.get_upi_config(interaction.guild_id)

        missing = []
        if not cfg or not cfg.get("upi_id"):
            missing.append("UPI ID")
        if not cfg or not cfg.get("qr_file_path") or not os.path.exists(cfg["qr_file_path"]):
            missing.append("QR Code Image")

        if missing:
            await interaction.followup.send(
                f"⚠️ **UPI Configuration Incomplete**\n"
                f"The UPI payment system has not been fully configured yet by the server administration.\n"
                f"• Missing: **{', '.join(missing)}**\n"
                "Please contact server staff for alternative payment options.",
                ephemeral=True
            )
            return

        upi_id = cfg["upi_id"]
        qr_file_path = cfg["qr_file_path"]

        embed = discord.Embed(
            title="FRAME X // UPI PAYMENT",
            description="Make your payment using the UPI ID or scan the QR code below.",
            color=COLOR_PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="💳 UPI ID", value=f"```{upi_id}```\n*(Tap or click to copy)*", inline=False)
        embed.add_field(
            name="📝 Payment Instructions",
            value=(
                "1. Open your preferred UPI payment application (GPay, PhonePe, Paytm, etc.).\n"
                "2. Scan the displayed QR code or enter the UPI ID manually.\n"
                "3. Confirm the recipient details and amount in your payment application before paying.\n"
                "4. Complete the payment and follow the relevant ticket instructions if payment confirmation is required."
            ),
            inline=False
        )
        embed.set_image(url="attachment://framex_upi_qr.png")
        embed.set_footer(text=FOOTER_TEXT)

        file = discord.File(qr_file_path, filename="framex_upi_qr.png")
        await interaction.followup.send(file=file, embed=embed)

    # ------------------ Optional Staff Channel Fallback ------------------
    @app_commands.command(name="staff-channel", description="Optionally set private fallback notification channel for staff")
    @is_guild_owner()
    async def staff_channel_setup(self, interaction: discord.Interaction, staff_channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        await self.db.update_guild_settings(interaction.guild_id, staff_notification_channel_id=staff_channel.id)
        await interaction.followup.send(f"✅ Staff alert fallback channel set to {staff_channel.mention}.", ephemeral=True)

async def setup(bot: commands.Bot, db: Database, audit_logger):
    await bot.add_cog(OwnerCommands(bot, db, audit_logger))
