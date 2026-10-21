# FrameX Discord Automation Bot

Production-ready Discord automation bot integrating seamlessly with **Ticket King** for the FrameX community. Automatically detects newly created Ticket King tickets with zero manual setup, runs automated screenshot scanning & multi-proof verification, dispenses free optimizer keys atomically, and includes an owner-managed UPI payment system with `/qrupi`.

---

## Features

1. **Automatic Ticket King Integration (Zero Manual Setup)**
   - No category IDs, channel IDs, or manual registration commands required.
   - Listens to Discord Gateway channel creation (`on_guild_channel_create`) with a 15-second delayed inspection window.
   - Actively inspects Ticket King welcome embeds, bot roles, channel permissions, and interactive components.
   - Reconciles existing open tickets upon bot startup or restart with idempotent locks to ensure **exactly one panel** is posted per ticket.

2. **Automated Screenshot Scanning & Proof Verification**
   - **Automatic Platform Association**: Evaluates uploads and automatically maps them to YouTube (`@framexfpsz`) and Instagram (`@framex.hz`).
   - Supports single or multiple image uploads in one message.
   - **Quality & Tamper Checks**: Enforces resolution limits (>= 300x300), valid mobile/desktop aspect ratios, and format validation (PNG, JPG, WEBP).
   - **Dedicated Status Alerts**:
     - `FRAME X // VERIFICATION COMPLETE` — When all mandatory proofs pass checks; proceeds to automated private key issuance.
     - `FRAME X // PROOF REQUIRED` — Identifies missing screenshots with direct clickable platform links.
     - `FRAME X // REUPLOAD REQUIRED` — Explains unreadable, cropped, or invalid platform screenshots.

3. **Owner-Managed UPI Payment System**
   - **Secure Server-Owner Only Controls**:
     - `/upi set-id <upi_id>` — Configures or updates the server UPI ID (e.g., `framex@upi`).
     - `/upi set-qr <attachment>` — Uploads/replaces the UPI QR image using atomic file swap.
     - `/upi settings` — Displays current UPI status, stored file size, and timestamps.
   - **Public `/qrupi` Payment Embed**:
     - Shows the branded `FRAME X // UPI PAYMENT` embed with the configured QR image and copy-friendly UPI ID.
     - Guarded: If either the UPI ID or QR image is unconfigured, displays a clear explanation of what is missing without broken images or placeholders.

4. **Permanent Mandatory Proof Links**
   - **YouTube Subscription**: [https://youtube.com/@framexfpsz](https://youtube.com/@framexfpsz)
   - **Instagram Following**: [https://instagram.com/framex.hz](https://instagram.com/framex.hz)
   - Both requirements are locked at the database level and cannot be disabled or removed.
   - Server owners can still add extra requirements via `/proof-link add` and delete extra ones via `/proof-link remove`.

---

## Command Reference

### Server Owner Commands
- `/upi set-id <upi_id>` — Set or update the UPI ID.
- `/upi set-qr <image>` — Upload or replace the UPI QR code.
- `/upi settings` — View current UPI configuration status.
- `/freekey add-account <user> <password>` — Assign an optimizer account with username and password.
- `/freekey add <keys>` — Add optimizer keys or bulk accounts in `user:pass` format.
- `/freekey stock` — View available and claimed inventory.
- `/proof-link add <id> <name> <url> <instructions> [mandatory]` — Add an additional external requirement.
- `/proof-link list` — View all active proof requirements.
- `/proof-link remove <id>` — Remove an additional requirement (permanent links cannot be removed).
- `/staff-role add <role>` / `/staff-role remove <role>` / `/staff-role list` — Manage staff notification roles.
- `/staff-channel <channel>` — Optional private staff channel for notification fallback.

### Public Server Commands
- `/qrupi` — Display the FrameX UPI payment embed with QR code and instructions.

---

## Running the Bot

```powershell
python main.py
```
