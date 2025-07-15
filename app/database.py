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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                lobby_id TEXT PRIMARY KEY,
                master_uuid TEXT NOT NULL,
                lobby_name TEXT NOT NULL DEFAULT 'Partida Sin Nombre', -- New column for lobby name
                status TEXT DEFAULT 'waiting', -- 'waiting', 'in_progress', 'finished'
                players_in_lobby TEXT DEFAULT '[]', -- JSON array of player_ids
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# --- Lobby Functions ---

async def create_lobby_db(master_uuid: str, lobby_name: str):
    """Creates a new game lobby in the database."""
    lobby_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO lobbies (lobby_id, master_uuid, lobby_name) VALUES (?, ?, ?)",
            (lobby_id, master_uuid, lobby_name)
        )
        await db.commit()
    return lobby_id

async def get_lobby_db(lobby_id: str):
    """Retrieves a lobby's information by its ID."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT lobby_id, master_uuid, lobby_name, status, players_in_lobby FROM lobbies WHERE lobby_id = ?",
            (lobby_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "lobby_id": row[0],
                "master_uuid": row[1],
                "lobby_name": row[2],
                "status": row[3],
                "players_in_lobby": json.loads(row[4]) # Parse JSON string back to list
            }
        return None

async def update_lobby_status_db(lobby_id: str, status: str):
    """Updates the status of a lobby."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE lobbies SET status = ? WHERE lobby_id = ?",
            (status, lobby_id)
        )
        await db.commit()

async def add_player_to_lobby_db(lobby_id: str, player_id: str):
    """Adds a player to a lobby's players_in_lobby list."""
    async with aiosqlite.connect(DB_FILE) as db:
        # Get current players list
        cursor = await db.execute("SELECT players_in_lobby FROM lobbies WHERE lobby_id = ?", (lobby_id,))
        row = await cursor.fetchone()
        if row:
            players_in_lobby = json.loads(row[0])
            if player_id not in players_in_lobby:
                players_in_lobby.append(player_id)
                await db.execute(
                    "UPDATE lobbies SET players_in_lobby = ? WHERE lobby_id = ?",
                    (json.dumps(players_in_lobby), lobby_id)
                )
                await db.commit()
                return True
        return False

async def remove_player_from_lobby_db(lobby_id: str, player_id: str):
    """Removes a player from a lobby's players_in_lobby list."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT players_in_lobby FROM lobbies WHERE lobby_id = ?", (lobby_id,))
        row = await cursor.fetchone()
        if row:
            players_in_lobby = json.loads(row[0])
            if player_id in players_in_lobby:
                players_in_lobby.remove(player_id)
                await db.execute(
                    "UPDATE lobbies SET players_in_lobby = ? WHERE lobby_id = ?",
                    (json.dumps(players_in_lobby), lobby_id)
                )
                await db.commit()
                return True
        return False

async def get_lobbies_by_master_uuid(master_uuid: str):
    """Retrieves all lobbies created by a specific master."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT lobby_id, lobby_name, status FROM lobbies WHERE master_uuid = ? ORDER BY created_at DESC",
            (master_uuid,)
        )
        rows = await cursor.fetchall()
        return [{"lobby_id": row[0], "lobby_name": row[1], "status": row[2]} for row in rows]

async def get_username_for_player_id(player_id: str):
    """Retrieves the username associated with a player_id."""
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT T2.username FROM players AS T1 JOIN users AS T2 ON T1.owner_uuid = T2.uuid WHERE T1.player_id = ?",
            (player_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else "Unknown Player"

async def delete_lobby_db(lobby_id: str):
    """Deletes a lobby from the database."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM lobbies WHERE lobby_id = ?", (lobby_id,))
        await db.commit()
        return True
    return False

async def clear_lobby_players_db(lobby_id: str):
    """Clears the players_in_lobby list for a given lobby."""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE lobbies SET players_in_lobby = '[]' WHERE lobby_id = ?",
            (lobby_id,)
        )
        await db.commit()
        return True
    return False
