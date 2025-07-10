import aiosqlite
import json
import uuid

from config import DB_FILE, TAGS

async def init_db():
    """Initializes the SQLite database, creating tables if they don't exist."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                player_id TEXT PRIMARY KEY,
                owner_uuid TEXT,
                state TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                uuid TEXT PRIMARY KEY,
                username TEXT UNIQUE, 
                password TEXT
            )
        """)
        await db.commit()
    print(f"{TAGS['db']} Database initialized.")

async def save_player_state(player_id: str, state: dict):
    """Saves or updates a player's state in the database."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO players (player_id, state) VALUES (?, ?)",
            (player_id, json.dumps(state))
        )
        await db.commit()

async def load_player_state_by_id(player_id: str):
    """Loads a single player's state from the database by player_id."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT state FROM players WHERE player_id = ?", (player_id,))
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

async def get_user_by_username(username: str):
    """Retrieves a user's password hash and UUID by username."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT password, uuid FROM users WHERE username = ?", (username,))
        return await cursor.fetchone()

async def get_username_by_uuid(user_uuid: str):
    """Retrieves a username by user UUID."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT username FROM users WHERE uuid = ?", (user_uuid,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def create_new_user(username: str, hashed_password: str, user_uuid: str):
    """Inserts a new user into the database."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO users (uuid, username, password) VALUES (?, ?, ?)",
            (user_uuid, username, hashed_password)
        )
        await db.commit()

async def create_initial_player_character(player_id: str, owner_uuid: str, character_state: dict):
    """Inserts a new player character into the database."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO players (player_id, owner_uuid, state) VALUES (?, ?, ?)",
            (player_id, owner_uuid, json.dumps(character_state))
        )
        await db.commit()

async def get_characters_by_owner_uuid(owner_uuid: str):
    """Retrieves all characters owned by a specific user UUID."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT player_id, state FROM players WHERE owner_uuid = ?", (owner_uuid,)
        )
        rows = await cursor.fetchall()
        return [{"player_id": r[0], "state": json.loads(r[1])} for r in rows]

async def check_player_ownership(player_id: str, owner_uuid: str):
    """Checks if a player_id belongs to a specific owner_uuid."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT 1 FROM players WHERE player_id = ? AND owner_uuid = ?",
            (player_id, owner_uuid)
        )
        return await cursor.fetchone() is not None
