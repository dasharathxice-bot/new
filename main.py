import discord
from discord.ext import commands
import os
import logging
import asyncio
from config import DISCORD_TOKEN, COLOR_SUCCESS, COLOR_WARNING, COLOR_DANGER, COLOR_PRIMARY, FOOTER_TEXT, BOT_STATUS_TEXT
from database import Database
from ticket_king_integration import TicketKingIntegration
from ticket_panel import ServiceSelectionView, TicketClaimView, ProofSubmissionActionsView
from staff_manager import StaffManager
from audit_logger import AuditLogger
from proof_verification import ProofVerifier
from owner_commands import OwnerCommands

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("framex.main")

# Required Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!fx ", intents=intents)

db = Database()
proof_verifier = ProofVerifier()
audit_logger = AuditLogger(bot, db)
staff_manager = StaffManager(bot, db)
ticket_king = TicketKingIntegration(bot, db, staff_manager, audit_logger)

# Set of channel IDs currently performing verification to prevent race conditions
_active_verification_channels = set()

@bot.event
async def on_ready():
    logger.info(f"FrameX Bot connected as {bot.user} (ID: {bot.user.id})")
    
    # Set Rich Presence Status
    try:
        activity = discord.CustomActivity(name=BOT_STATUS_TEXT)
        await bot.change_presence(status=discord.Status.online, activity=activity)
        logger.info(f"Rich presence set to: '{BOT_STATUS_TEXT}'")
    except Exception as e:
        logger.warning(f"Failed to set custom activity: {e}")

    # Initialize SQLite Database schema
    await db.init_db()

    # Register persistent views for button clicks across restarts
    bot.add_view(ServiceSelectionView(bot, db, staff_manager, audit_logger))
    bot.add_view(TicketClaimView(bot, db))
    bot.add_view(ProofSubmissionActionsView(bot, db))

    # Add Owner Cog & Sync Slash Commands
    try:
        await bot.add_cog(OwnerCommands(bot, db, audit_logger))
        synced = await bot.tree.sync()
        logger.info(f"Synchronized {len(synced)} application slash commands.")
    except Exception as e:
        logger.error(f"Error registering/syncing commands: {e}")

    # Seed default permanent proof links and reconcile existing open tickets
    for guild in bot.guilds:
        await db.seed_default_proof_links(guild.id)
        await ticket_king.reconcile_open_tickets(guild)
    
    logger.info("Startup reconciliation complete. Automatic Ticket King detection active.")

@bot.event
async def on_guild_channel_create(channel):
    """
    Automatic detection pipeline when Ticket King creates a new channel.
    Runs delayed inspection to allow Ticket King permissions and welcome embeds to materialize.
    """
    if isinstance(channel, discord.TextChannel):
        logger.info(f"New channel created: #{channel.name} ({channel.id}). Launching Ticket King detection...")
        asyncio.create_task(ticket_king.monitor_and_integrate_channel(channel))

@bot.event
async def on_guild_channel_update(before, after):
    """
    Detects if Ticket King sets channel topic or overwrites post-creation.
    """
    if isinstance(after, discord.TextChannel) and ticket_king.is_ticket_name(after.name):
        existing = await db.get_ticket(after.id)
        if not existing or not existing.get("panel_message_id"):
            is_ticket, creator_id = await ticket_king.inspect_channel_for_ticket_king(after)
            if is_ticket:
                await ticket_king.handle_new_ticket(after, creator_id)

@bot.event
async def on_message(message: discord.Message):
    # If a bot sends a message (e.g. Ticket King sending initial welcome embed)
    if message.author.bot and isinstance(message.channel, discord.TextChannel):
        if ticket_king.is_ticket_name(message.channel.name):
            existing = await db.get_ticket(message.channel.id)
            if not existing or not existing.get("panel_message_id"):
                is_ticket, creator_id = await ticket_king.inspect_channel_for_ticket_king(message.channel)
                if is_ticket:
                    logger.info(f"Ticket King message detected in #{message.channel.name}. Auto-posting panel.")
                    await ticket_king.handle_new_ticket(message.channel, creator_id)
        return

    # User message check
    ticket = await db.get_ticket(message.channel.id)
    if not ticket or ticket.get("service_type") != "FREE_KEY":
        await bot.process_commands(message)
        return

    # Check if message contains image attachments
    if message.attachments:
        image_attachments = [
            a for a in message.attachments 
            if a.content_type and any(a.content_type.startswith(t) for t in ["image/png", "image/jpeg", "image/webp"])
        ]
        if not image_attachments:
            return

        # Prevent duplicate simultaneous verification jobs in the same channel
        if message.channel.id in _active_verification_channels:
            return
        _active_verification_channels.add(message.channel.id)

        try:
            status_msg = await message.reply("🔄 Scanning and evaluating uploaded screenshot(s)...")

            # Fetch active proof links and existing submissions
            links = await db.get_proof_links(message.guild.id, only_enabled=True)
            submissions = await db.get_proof_submissions(message.channel.id)
            verified_req_ids = {s["requirement_id"] for s in submissions if s["status"] == "VERIFIED"}

            unverified_links = [l for l in links if l["id"] not in verified_req_ids]

            reupload_needed = False
            rejected_needed = False
            verified_any = False
            feedback_explanations = []

            # Process each attachment
            for attachment in image_attachments:
                try:
                    img_bytes = await proof_verifier.fetch_image(attachment.url)
                    matched_req_id, status, explanation, metadata = await proof_verifier.classify_and_verify(
                        img_bytes, unverified_links
                    )

                    feedback_explanations.append(explanation)

                    if matched_req_id:
                        # Save submission in database
                        await db.update_proof_submission(
                            message.channel.id, message.author.id, matched_req_id,
                            status, attachment.url, explanation
                        )

                        await audit_logger.log(
                            message.guild,
                            "PROOF_SCANNED",
                            f"Proof Evaluated: {matched_req_id}",
                            f"User: {message.author.mention}\nStatus: `{status}`\nExplanation: {explanation}",
                            user=message.author,
                            channel=message.channel
                        )

                        if status == "VERIFIED":
                            verified_any = True
                            unverified_links = [l for l in unverified_links if l["id"] != matched_req_id]
                        elif status == "REJECTED":
                            rejected_needed = True
                        elif status == "NEEDS_REUPLOAD":
                            reupload_needed = True
                    else:
                        if status == "REJECTED":
                            rejected_needed = True
                        else:
                            reupload_needed = True

                except Exception as img_err:
                    logger.error(f"Error analyzing attachment: {img_err}")
                    feedback_explanations.append(f"Image analysis error: {img_err}")
                    reupload_needed = True

            # Re-evaluate all requirements for this ticket
            all_subs = await db.get_proof_submissions(message.channel.id)
            sub_status_map = {s["requirement_id"]: s["status"] for s in all_subs}

            missing_mandatory = []
            for l in links:
                if l["is_mandatory"] and sub_status_map.get(l["id"]) != "VERIFIED":
                    missing_mandatory.append(l)

            # Scenario 1: ALL MANDATORY PROOF CHECKS PASSED
            if links and not missing_mandatory:
                complete_embed = discord.Embed(
                    title="FRAME X // VERIFICATION COMPLETE",
                    description="All required proof checks have passed. Your free optimizer key is being processed.",
                    color=COLOR_SUCCESS
                )
                complete_embed.set_footer(text=FOOTER_TEXT)
                await status_msg.edit(content=None, embed=complete_embed)

                # Atomically claim and deliver key
                key = await db.claim_available_key_atomically(message.guild.id, message.author.id, message.channel.id)
                if key == "ALREADY_CLAIMED":
                    await message.channel.send(f"⚠️ {message.author.mention}, you have already received a free optimizer key in this server.")
                elif not key:
                    await message.channel.send("⚠️ All requirements verified! However, the free key inventory is currently empty. Staff has been notified.")
                    await audit_logger.log(
                        message.guild, "KEY_STOCK_EMPTY", "Empty Inventory Alert",
                        f"User {message.author.mention} verified all proof but no free keys remain in stock.",
                        channel=message.channel
                    )
                else:
                    if isinstance(key, dict):
                        u_val = key.get("user", "N/A")
                        p_val = key.get("pass", "N/A")
                    else:
                        u_val = str(key)
                        p_val = "N/A"

                    dm_success = False
                    try:
                        dm_embed = discord.Embed(
                            title="🎁 YOUR FRAMEX OPTIMIZER ACCESS",
                            description=(
                                f"Congratulations {message.author.name}! Your proof has been successfully verified.\n\n"
                                "**Your Free Optimizer Login Credentials:**\n\n"
                                f"**USER :** `{u_val}`\n"
                                f"**PASS :** `{p_val}`\n\n"
                                "Keep your login credentials secure. Thank you for supporting FrameX!"
                            ),
                            color=COLOR_SUCCESS
                        )
                        dm_embed.set_footer(text=FOOTER_TEXT)
                        await message.author.send(embed=dm_embed)
                        dm_success = True
                    except Exception:
                        dm_success = False

                    if dm_success:
                        await message.channel.send(f"🎉 {message.author.mention}, your login credentials have been securely sent to your Direct Messages!")
                    else:
                        await message.channel.send(
                            f"🎉 {message.author.mention}, your proof is verified! Your DMs are closed, so here are your credentials:\n"
                            f"||**USER :** `{u_val}`  |  **PASS :** `{p_val}`|| *(Save or claim immediately)*"
                        )

                    await audit_logger.log(
                        message.guild, "KEY_ISSUED", "Optimizer Access Issued",
                        f"Optimizer credentials issued to {message.author.mention} (USER: {u_val}).",
                        user=message.author,
                        channel=message.channel
                    )

            # Scenario 2: REJECTED (FAIL) FOR INVALID / IRRELEVANT / WRONG STATUS SCREENSHOT
            elif rejected_needed and not verified_any:
                detail_text = "\n".join(f"• {e}" for e in feedback_explanations if e)
                reject_embed = discord.Embed(
                    title="FRAME X // PROOF REJECTED",
                    description=(
                        f"**Your screenshot was rejected for the following reason:**\n\n"
                        f"{detail_text or 'The uploaded image does not meet our verification criteria.'}\n\n"
                        "Please review our requirements and upload a genuine, uncropped screenshot showing the required account and status."
                    ),
                    color=COLOR_DANGER
                )
                reject_embed.set_footer(text=FOOTER_TEXT)
                await status_msg.edit(content=None, embed=reject_embed)

            # Scenario 3: INCONCLUSIVE / REUPLOAD REQUIRED (UNCLEAR OR BLURRY)
            elif reupload_needed and not verified_any:
                detail_text = "\n".join(f"• {e}" for e in feedback_explanations if e)
                reupload_embed = discord.Embed(
                    title="FRAME X // REUPLOAD REQUIRED",
                    description=(
                        f"**We could not verify your screenshot:**\n\n"
                        f"{detail_text or 'The uploaded image was unclear or incomplete.'}\n\n"
                        "Please upload a new, clear, uncropped screenshot directly from the app or browser."
                    ),
                    color=COLOR_WARNING
                )
                reupload_embed.set_footer(text=FOOTER_TEXT)
                await status_msg.edit(content=None, embed=reupload_embed)

            # Scenario 4: PARTIALLY VERIFIED OR MISSING MANDATORY PROOFS
            else:
                first_missing = missing_mandatory[0] if missing_mandatory else links[0]
                action_word = "following" if "instagram" in first_missing["id"] else "subscribed to"
                notice = "✅ One requirement was verified!\n\n" if verified_any else ""
                proof_embed = discord.Embed(
                    title="FRAME X // ADDITIONAL PROOF REQUIRED",
                    description=(
                        f"{notice}"
                        f"Your **{first_missing['name']}** proof is still required.\n\n"
                        f"Please upload a clear screenshot showing that you are {action_word}:\n"
                        f"[{first_missing['url']}]({first_missing['url']})\n\n"
                        "Your request will continue automatically after the required proof has been submitted."
                    ),
                    color=COLOR_WARNING
                )
                proof_embed.set_footer(text=FOOTER_TEXT)
                await status_msg.edit(content=None, embed=proof_embed)

        finally:
            _active_verification_channels.discard(message.channel.id)

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.warning("DISCORD_TOKEN is not set in the environment or .env file.")
        print("Please configure DISCORD_TOKEN in .env to connect to Discord.")
    else:
        bot.run(DISCORD_TOKEN)
