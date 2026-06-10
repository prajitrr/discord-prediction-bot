"""
Async SQLite database layer using aiosqlite.

All amounts are stored as floating-point credits (displayed as "$X.XX").
"""

import aiosqlite
import os
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "predictions.db")
STARTING_BALANCE = 1000.0


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path

    async def init(self) -> None:
        """Create tables if they do not exist."""
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id   TEXT NOT NULL,
                    guild_id  TEXT NOT NULL,
                    balance   REAL NOT NULL DEFAULT 1000.0,
                    PRIMARY KEY (user_id, guild_id)
                );

                CREATE TABLE IF NOT EXISTS markets (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT    NOT NULL,
                    question    TEXT    NOT NULL,
                    q_yes       REAL    NOT NULL DEFAULT 0.0,
                    q_no        REAL    NOT NULL DEFAULT 0.0,
                    b           REAL    NOT NULL DEFAULT 100.0,
                    status      TEXT    NOT NULL DEFAULT 'open',
                    resolution  TEXT,
                    creator_id  TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    user_id    TEXT    NOT NULL,
                    guild_id   TEXT    NOT NULL,
                    market_id  INTEGER NOT NULL,
                    yes_shares REAL    NOT NULL DEFAULT 0.0,
                    no_shares  REAL    NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (user_id, guild_id, market_id)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id   INTEGER NOT NULL,
                    timestamp   TEXT    NOT NULL,
                    probability REAL    NOT NULL,
                    action      TEXT
                );
            """)
            await db.commit()

    # ── Users ──────────────────────────────────────────────────────────────────

    async def get_balance(self, user_id: str, guild_id: str) -> float:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT balance FROM users WHERE user_id=? AND guild_id=?",
                (user_id, guild_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.execute(
                    "INSERT INTO users (user_id, guild_id, balance) VALUES (?,?,?)",
                    (user_id, guild_id, STARTING_BALANCE),
                )
                await db.commit()
                return STARTING_BALANCE
            return row[0]

    async def adjust_balance(self, user_id: str, guild_id: str, delta: float) -> float:
        """Add `delta` (can be negative) to a user's balance. Returns new balance."""
        current = await self.get_balance(user_id, guild_id)
        new_bal = current + delta
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET balance=? WHERE user_id=? AND guild_id=?",
                (new_bal, user_id, guild_id),
            )
            await db.commit()
        return new_bal

    async def set_balance(self, user_id: str, guild_id: str, amount: float) -> None:
        await self.get_balance(user_id, guild_id)  # ensures row exists
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET balance=? WHERE user_id=? AND guild_id=?",
                (amount, user_id, guild_id),
            )
            await db.commit()

    async def get_leaderboard(self, guild_id: str, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id, balance FROM users WHERE guild_id=? ORDER BY balance DESC LIMIT ?",
                (guild_id, limit),
            ) as cur:
                return await cur.fetchall()

    # ── Markets ────────────────────────────────────────────────────────────────

    async def create_market(
        self, guild_id: str, question: str, b: float, creator_id: str
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO markets (guild_id, question, q_yes, q_no, b, status, creator_id, created_at)
                   VALUES (?,?,0.0,0.0,?,?,?,?)""",
                (guild_id, question, b, "open", creator_id, now),
            )
            await db.commit()
            market_id = cur.lastrowid
        # Record initial price point
        await self.record_price(market_id, 0.5, "created")
        return market_id

    async def get_market(self, market_id: int, guild_id: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM markets WHERE id=? AND guild_id=?",
                (market_id, guild_id),
            ) as cur:
                return await cur.fetchone()

    async def list_markets(self, guild_id: str, status: str = "open"):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM markets WHERE guild_id=? AND status=? ORDER BY id DESC",
                (guild_id, status),
            ) as cur:
                return await cur.fetchall()

    async def update_market_shares(
        self, market_id: int, q_yes: float, q_no: float
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE markets SET q_yes=?, q_no=? WHERE id=?",
                (q_yes, q_no, market_id),
            )
            await db.commit()

    async def resolve_market(self, market_id: int, resolution: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE markets SET status='resolved', resolution=? WHERE id=?",
                (resolution, market_id),
            )
            await db.commit()

    # ── Positions ──────────────────────────────────────────────────────────────

    async def get_position(self, user_id: str, guild_id: str, market_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT yes_shares, no_shares FROM positions
                   WHERE user_id=? AND guild_id=? AND market_id=?""",
                (user_id, guild_id, market_id),
            ) as cur:
                row = await cur.fetchone()
        return row  # None if no position

    async def upsert_position(
        self,
        user_id: str,
        guild_id: str,
        market_id: int,
        yes_delta: float = 0.0,
        no_delta: float = 0.0,
    ) -> tuple[float, float]:
        """Add deltas to a user's position. Returns (new_yes, new_no)."""
        pos = await self.get_position(user_id, guild_id, market_id)
        if pos is None:
            new_yes, new_no = yes_delta, no_delta
        else:
            new_yes = pos["yes_shares"] + yes_delta
            new_no = pos["no_shares"] + no_delta
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO positions (user_id, guild_id, market_id, yes_shares, no_shares)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id, guild_id, market_id)
                   DO UPDATE SET yes_shares=excluded.yes_shares, no_shares=excluded.no_shares""",
                (user_id, guild_id, market_id, new_yes, new_no),
            )
            await db.commit()
        return new_yes, new_no

    async def get_all_positions_for_market(self, market_id: int, guild_id: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT user_id, yes_shares, no_shares FROM positions
                   WHERE market_id=? AND guild_id=?""",
                (market_id, guild_id),
            ) as cur:
                return await cur.fetchall()

    async def get_user_positions(self, user_id: str, guild_id: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT p.market_id, p.yes_shares, p.no_shares, m.question, m.status,
                          m.q_yes, m.q_no, m.b, m.resolution
                   FROM positions p
                   JOIN markets m ON p.market_id = m.id
                   WHERE p.user_id=? AND p.guild_id=?
                     AND (p.yes_shares > 0.0001 OR p.no_shares > 0.0001)
                   ORDER BY p.market_id DESC""",
                (user_id, guild_id),
            ) as cur:
                return await cur.fetchall()

    # ── Price history ──────────────────────────────────────────────────────────

    async def record_price(
        self, market_id: int, probability: float, action: str = ""
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO price_history (market_id, timestamp, probability, action) VALUES (?,?,?,?)",
                (market_id, now, probability, action),
            )
            await db.commit()

    async def get_price_history(self, market_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT timestamp, probability, action FROM price_history WHERE market_id=? ORDER BY id",
                (market_id,),
            ) as cur:
                return await cur.fetchall()
