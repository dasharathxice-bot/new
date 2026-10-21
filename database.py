import aiosqlite
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import DATABASE_PATH, DEFAULT_PROOF_LINKS, PERMANENT_PROOF_IDS

logger = logging.getLogger("framex.database")

class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")

            # Guild configuration table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    ticket_king_role_id INTEGER,
                    staff_notification_channel_id INTEGER,
                    audit_log_channel_id INTEGER,
                    ticket_category_ids TEXT,
                    auto_panel_enabled INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Staff roles table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS staff_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL UNIQUE,
                    role_name TEXT,
                    notify_dm INTEGER DEFAULT 1,
                    notify_channel INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Tickets tracked
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    service_type TEXT,
                    panel_message_id INTEGER,
                    status TEXT DEFAULT 'OPEN',
                    claimed_by_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Proof requirements table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proof_links (
                    id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    instructions TEXT,
                    is_mandatory INTEGER DEFAULT 1,
                    is_enabled INTEGER DEFAULT 1,
                    is_permanent INTEGER DEFAULT 0,
                    verification_method TEXT DEFAULT 'ocr_vision',
                    verification_policy TEXT DEFAULT 'strict',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Proof submissions tracking per ticket requirement
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proof_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    requirement_id TEXT NOT NULL,
                    attachment_url TEXT,
                    status TEXT DEFAULT 'NOT_SUBMITTED', 
                    review_notes TEXT,
                    submitted_at TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES tickets(channel_id) ON DELETE CASCADE,
                    UNIQUE(channel_id, requirement_id)
                );
            """)

            # Free Optimizer Keys inventory
            await db.execute("""
                CREATE TABLE IF NOT EXISTS free_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    key_value TEXT NOT NULL UNIQUE,
                    is_assigned INTEGER DEFAULT 0,
                    assigned_to_user_id INTEGER,
                    assigned_channel_id INTEGER,
                    assigned_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Key issuance audit log (prevents duplicate issuance for same user/ticket)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS key_issuances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    key_id INTEGER NOT NULL,
                    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, user_id),
                    FOREIGN KEY (key_id) REFERENCES free_keys(id)
                );
            """)

            # System Audit Log
            await db.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    event_type TEXT NOT NULL,
                    user_id INTEGER,
                    channel_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # UPI Configuration table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS upi_config (
                    guild_id INTEGER PRIMARY KEY,
                    upi_id TEXT,
                    qr_file_path TEXT,
                    updated_by_user_id INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.commit()
            
            # Safe schema migrations for existing databases
            try:
                await db.execute("ALTER TABLE proof_links ADD COLUMN is_permanent INTEGER DEFAULT 0;")
            except Exception:
                pass

            try:
                await db.execute("ALTER TABLE free_keys ADD COLUMN account_user TEXT;")
                await db.execute("ALTER TABLE free_keys ADD COLUMN account_pass TEXT;")
            except Exception:
                pass

            await db.commit()
            logger.info("Database schema initialized successfully.")

    async def seed_default_proof_links(self, guild_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            for item in DEFAULT_PROOF_LINKS:
                await db.execute("""
                    INSERT INTO proof_links 
                    (id, guild_id, name, url, instructions, is_mandatory, is_enabled, is_permanent, verification_method, verification_policy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        is_mandatory = 1,
                        is_enabled = 1,
                        is_permanent = 1,
                        url = excluded.url,
                        instructions = excluded.instructions
                """, (
                    item["id"], guild_id, item["name"], item["url"],
                    item["instructions"], item["is_mandatory"], item["is_enabled"],
                    item["verification_method"], item["verification_policy"]
                ))
            await db.commit()

    # Guild settings methods
    async def get_guild_settings(self, guild_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_guild_settings(self, guild_id: int, **kwargs):
        async with aiosqlite.connect(self.db_path) as db:
            fields = []
            values = []
            for k, v in kwargs.items():
                fields.append(f"{k} = ?")
                values.append(v)
            values.append(guild_id)
            query = f"INSERT INTO guild_settings (guild_id, {', '.join(kwargs.keys())}) VALUES (?, {', '.join(['?']*len(kwargs))}) ON CONFLICT(guild_id) DO UPDATE SET {', '.join(fields)}"
            await db.execute(query, [guild_id] + list(kwargs.values()) + list(kwargs.values()))
            await db.commit()

    # Ticket tracking methods
    async def register_ticket(self, channel_id: int, guild_id: int, creator_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("""
                    INSERT INTO tickets (channel_id, guild_id, creator_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(channel_id) DO NOTHING
                """, (channel_id, guild_id, creator_id))
                await db.commit()
                return True
            except Exception as e:
                logger.error(f"Error registering ticket {channel_id}: {e}")
                return False

    async def get_ticket(self, channel_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_ticket_service(self, channel_id: int, service_type: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tickets SET service_type = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id = ?
            """, (service_type, channel_id))
            await db.commit()
            return True

    async def set_ticket_panel_message(self, channel_id: int, message_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tickets SET panel_message_id = ? WHERE channel_id = ?
            """, (message_id, channel_id))
            await db.commit()

    async def claim_ticket(self, channel_id: int, staff_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tickets SET claimed_by_id = ?, status = 'CLAIMED', updated_at = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (staff_id, channel_id))
            await db.commit()
            return True

    # Proof Links Management
    async def get_proof_links(self, guild_id: int, only_enabled: bool = False) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            sql = "SELECT * FROM proof_links WHERE guild_id = ?"
            if only_enabled:
                sql += " AND is_enabled = 1"
            cursor = await db.execute(sql, (guild_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def add_proof_link(self, guild_id: int, req_id: str, name: str, url: str, instructions: str, is_mandatory: int = 1) -> bool:
        if req_id in PERMANENT_PROOF_IDS:
            return False  # Cannot overwrite permanent proof IDs
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO proof_links (id, guild_id, name, url, instructions, is_mandatory, is_enabled, is_permanent)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0)
            """, (req_id, guild_id, name, url, instructions, is_mandatory))
            await db.commit()
            return True

    async def update_proof_link_state(self, guild_id: int, req_id: str, is_enabled: int) -> bool:
        if req_id in PERMANENT_PROOF_IDS:
            logger.warning(f"Attempt to modify permanent proof requirement state rejected: {req_id}")
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE proof_links SET is_enabled = ? WHERE guild_id = ? AND id = ? AND is_permanent = 0
            """, (is_enabled, guild_id, req_id))
            await db.commit()
            return cursor.rowcount > 0

    async def remove_proof_link(self, guild_id: int, req_id: str) -> bool:
        if req_id in PERMANENT_PROOF_IDS:
            logger.warning(f"Attempt to delete permanent proof requirement rejected: {req_id}")
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM proof_links WHERE guild_id = ? AND id = ? AND is_permanent = 0
            """, (guild_id, req_id))
            await db.commit()
            return cursor.rowcount > 0

    # Proof Submissions
    async def get_proof_submissions(self, channel_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT ps.*, pl.name, pl.url, pl.is_mandatory
                FROM proof_submissions ps
                JOIN proof_links pl ON ps.requirement_id = pl.id
                WHERE ps.channel_id = ?
            """, (channel_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_proof_submission(self, channel_id: int, user_id: int, requirement_id: str, 
                                     status: str, attachment_url: str = None, notes: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO proof_submissions (channel_id, user_id, requirement_id, status, attachment_url, review_notes, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id, requirement_id) DO UPDATE SET
                    status = excluded.status,
                    attachment_url = COALESCE(excluded.attachment_url, proof_submissions.attachment_url),
                    review_notes = COALESCE(excluded.review_notes, proof_submissions.review_notes),
                    submitted_at = CURRENT_TIMESTAMP
            """, (channel_id, user_id, requirement_id, status, attachment_url, notes))
            await db.commit()

    # Free Key Inventory & Atomic Issuance
    async def add_account_credentials(self, guild_id: int, user: str, password: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            clean_u = user.strip()
            clean_p = password.strip()
            key_val = f"{clean_u}:{clean_p}"
            try:
                await db.execute("""
                    INSERT INTO free_keys (guild_id, key_value, account_user, account_pass, is_assigned)
                    VALUES (?, ?, ?, ?, 0)
                """, (guild_id, key_val, clean_u, clean_p))
                await db.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding account credentials: {e}")
                return False

    async def add_free_keys(self, guild_id: int, keys: List[str]) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            count = 0
            for key in keys:
                key_clean = key.strip()
                if not key_clean:
                    continue
                account_u = None
                account_p = None
                if ":" in key_clean:
                    parts = key_clean.split(":", 1)
                    account_u = parts[0].strip()
                    account_p = parts[1].strip()
                try:
                    await db.execute("""
                        INSERT INTO free_keys (guild_id, key_value, account_user, account_pass, is_assigned)
                        VALUES (?, ?, ?, ?, 0)
                    """, (guild_id, key_clean, account_u, account_p))
                    count += 1
                except Exception:
                    pass
            await db.commit()
            return count

    async def get_key_stock(self, guild_id: int) -> Dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    COUNT(CASE WHEN is_assigned = 0 THEN 1 END) as available,
                    COUNT(CASE WHEN is_assigned = 1 THEN 1 END) as issued,
                    COUNT(*) as total
                FROM free_keys WHERE guild_id = ?
            """, (guild_id,))
            row = await cursor.fetchone()
            return {"available": row[0] or 0, "issued": row[1] or 0, "total": row[2] or 0}

    async def claim_available_key_atomically(self, guild_id: int, user_id: int, channel_id: int) -> Any:
        """
        Atomically selects and marks an available free optimizer key.
        Returns:
            dict: {"user": account_user, "pass": account_pass, "key": key_val}
            str: "ALREADY_CLAIMED"
            None: If out of stock
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE;")
            try:
                # Check if user already claimed a key in this guild
                cursor = await db.execute("""
                    SELECT key_id FROM key_issuances WHERE guild_id = ? AND user_id = ?
                """, (guild_id, user_id))
                existing = await cursor.fetchone()
                if existing:
                    await db.execute("ROLLBACK;")
                    return "ALREADY_CLAIMED"

                # Find 1 unassigned key
                cursor = await db.execute("""
                    SELECT id, key_value, account_user, account_pass FROM free_keys
                    WHERE guild_id = ? AND is_assigned = 0
                    LIMIT 1
                """, (guild_id,))
                key_row = await cursor.fetchone()
                if not key_row:
                    await db.execute("ROLLBACK;")
                    return None  # Out of stock

                key_id, key_val, account_u, account_p = key_row

                # Parse fallback if user/pass columns are empty
                if not account_u or not account_p:
                    if ":" in key_val:
                        parts = key_val.split(":", 1)
                        account_u = parts[0].strip()
                        account_p = parts[1].strip()
                    else:
                        account_u = key_val
                        account_p = "N/A"

                # Mark key as assigned
                await db.execute("""
                    UPDATE free_keys 
                    SET is_assigned = 1, assigned_to_user_id = ?, assigned_channel_id = ?, assigned_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND is_assigned = 0
                """, (user_id, channel_id, key_id))

                # Insert into key_issuances table
                await db.execute("""
                    INSERT INTO key_issuances (guild_id, user_id, channel_id, key_id)
                    VALUES (?, ?, ?, ?)
                """, (guild_id, user_id, channel_id, key_id))

                await db.commit()
                return {"user": account_u, "pass": account_p, "key": key_val}
            except Exception as e:
                await db.execute("ROLLBACK;")
                logger.error(f"Atomic key issuance failed: {e}")
                return None

    # Staff Roles
    async def add_staff_role(self, guild_id: int, role_id: int, role_name: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("""
                    INSERT INTO staff_roles (guild_id, role_id, role_name)
                    VALUES (?, ?, ?)
                    ON CONFLICT(role_id) DO UPDATE SET role_name = excluded.role_name
                """, (guild_id, role_id, role_name))
                await db.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding staff role {role_id}: {e}")
                return False

    async def remove_staff_role(self, guild_id: int, role_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM staff_roles WHERE guild_id = ? AND role_id = ?
            """, (guild_id, role_id))
            await db.commit()
            return cursor.rowcount > 0

    async def get_staff_roles(self, guild_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM staff_roles WHERE guild_id = ?", (guild_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # Audit Logging
    async def log_event(self, guild_id: Optional[int], event_type: str, user_id: Optional[int] = None, 
                        channel_id: Optional[int] = None, details: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO audit_logs (guild_id, event_type, user_id, channel_id, details)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, event_type, user_id, channel_id, details))
            await db.commit()

    # UPI Configuration Methods
    async def get_upi_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM upi_config WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_upi_id(self, guild_id: int, upi_id: str, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO upi_config (guild_id, upi_id, updated_by_user_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id) DO UPDATE SET
                    upi_id = excluded.upi_id,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = CURRENT_TIMESTAMP
            """, (guild_id, upi_id, user_id))
            await db.commit()

    async def set_upi_qr(self, guild_id: int, qr_path: str, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO upi_config (guild_id, qr_file_path, updated_by_user_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id) DO UPDATE SET
                    qr_file_path = excluded.qr_file_path,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = CURRENT_TIMESTAMP
            """, (guild_id, qr_path, user_id))
            await db.commit()

