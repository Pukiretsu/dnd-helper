import asyncio
import json
from time import timezone
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import aiosqlite
import uvicorn
from fastapi import Cookie, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from passlib.context import CryptContext
from jose import JWTError, jwt

# Configure the password hashing context
# We use bcrypt as the recommended hashing algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
SECRET_KEY = "un_secreto_super_seguro"  # Change this to a strong, unique secret key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# Global variables (consider using FastAPI's dependency injection or app.state for larger apps)
connections = {} # Stores active WebSocket connections: {user_uuid: {"ws": WebSocket, "role": "player/master"}}
db_file = "sessions.db"

tags = {
    "server":        "    -->> [\033[96mSERVER\033[0m]   ",
    "db":            "    -->> [\033[93mDATABASE\033[0m] ",
    "websocket":     "    -->> [\033[92mWEBSOCKET\033[0m]",
    "app_error":     "    -->> [\033[91mAPP\033[0m]      ",
    "app_log":       "    -->> [\033[95mAPP\033[0m]      ",
}

## Auth Functions

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """
    Creates a new JWT access token.
    Args:
        data (dict): Data to encode into the token (e.g., {"sub": user_uuid}).
        expires_delta (timedelta | None): Optional timedelta for token expiration.
    Returns:
        str: The encoded JWT token.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    """
    Verifies a JWT token and returns the subject (user_uuid) if valid.
    Args:
        token (str): The JWT token to verify.
    Returns:
        str | None: The user_uuid if the token is valid, otherwise None.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(request: Request):
    """
    Retrieves the current user's UUID from the access token cookie.
    Raises HTTPException if the user is not authenticated.
    """
    token = request.cookies.get("access_token")
    user_uuid = verify_token(token) if token else None
    if not user_uuid:
        raise HTTPException(status_code=303, detail="No autenticado", headers={"Location": "/login"})
    return user_uuid

## Database Functions

async def init_db():
    """Initializes the SQLite database, creating tables if they don't exist."""
    async with aiosqlite.connect(db_file) as db:
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
                username TEXT ,
                password TEXT -- The hashed password will be stored here
            )
        """)
        await db.commit()
    print(f"{tags['db']} Database initialized.")

async def save_player_state(player_id, state):
    """Saves or updates a player's state in the database."""
    async with aiosqlite.connect(db_file) as db:
        await db.execute(
            "INSERT OR REPLACE INTO players (player_id, state) VALUES (?, ?)",
            (player_id, json.dumps(state))
        )
        await db.commit()

async def load_players_state():
    """
    Loads all players' states from the database.
    """
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT player_id, state FROM players")
        rows = await cursor.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

async def get_active_players_state():
    """
    Retrieves the state of players who are currently connected via WebSocket.
    Returns:
        dict: A dictionary where keys are player_ids and values are their states.
    """
    active_players_data = {}
    async with aiosqlite.connect(db_file) as db:
        for user_uuid, conn_info in connections.items():
            if conn_info["role"] == "player":
                cursor = await db.execute(
                    "SELECT player_id, state FROM players WHERE owner_uuid = ?",
                    (user_uuid,)
                )
                rows = await cursor.fetchall()
                for row in rows:
                    active_players_data[row[0]] = json.loads(row[1])
    return active_players_data

async def broadcast_active_players():
    """
    Periodically broadcasts the state of active players to all connected masters.
    """
    while True:
        active_players = await get_active_players_state()
        #print(f"Active players for broadcast: {active_players}")
        to_remove = []
        for user_uuid, conn_info in connections.items():
            if conn_info["role"] == "master":
                ws = conn_info["ws"]
                try:
                    await ws.send_json({
                        "type": "players_state",
                        "players": active_players
                    })
                except Exception as e:
                    print(f"Error enviando a master {user_uuid}: {e}")
                    to_remove.append(user_uuid)
        
        # Remove closed master connections
        for user_uuid in to_remove:
            if user_uuid in connections: # Check again in case it was already removed
                del connections[user_uuid]
                print(f"{tags['websocket']} Removed disconnected master: {user_uuid}")
                
        await asyncio.sleep(5) # Broadcast every 5 seconds

## FastAPI Application Setup

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for application startup and shutdown events.
    Initializes the database and starts the active players broadcast task.
    """
    print(f"{tags['server']} Application startup event: Initializing database...")
    await init_db()
    # Start the background task for broadcasting active players
    asyncio.create_task(broadcast_active_players())
    yield
    # Code here runs on application shutdown (e.g., closing database connections)
    print(f"{tags['server']} Application shutdown event: Performing cleanup...")

# Initialize FastAPI app with the new lifespan handler
app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

## Routes

@app.get("/")
async def get_index_page(request: Request):
    """Renders the main index page."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/lobby")
async def lobby_page(request: Request):
    """Renders the lobby page, requiring authentication."""
    user_uuid = get_current_user(request) # This will handle redirection if not authenticated
    return templates.TemplateResponse("lobby.html", {"request": request, "user_uuid": user_uuid})

@app.get("/player")
async def get_player_page(request: Request, access_token: str = Cookie(None)):
    """Renders the player page, requiring authentication."""
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    return templates.TemplateResponse("player.html", {"request": request, "token": access_token})

@app.get("/master")
async def get_master_page(request: Request, access_token: str = Cookie(None)):
    """
    Renders the master page, showing only active players.
    Active players are those currently connected via WebSocket.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    
    # Get the states of currently active players
    active_players_data = await get_active_players_state()
    
    return templates.TemplateResponse("master.html", {
        "request": request,
        "token": access_token,
        "active_players": active_players_data # Pass active players data to the template
    })

## HANDLERS FOR AUTH

@app.get("/register")
async def register_page(request: Request):
    """Renders the registration page."""
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})

@app.post("/register")
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Handles user registration, hashing the password and creating initial player state."""
    async with aiosqlite.connect(db_file) as db:
        # Check if user already exists
        cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if await cursor.fetchone():
            print(f"{tags['app_error']} El usuario: {username} ya existe.")
            return templates.TemplateResponse("register.html", {
                "request": request,
                "error": "¡El usuario ya existe!"
                
            })
        
        # Hash the password
        hashed_password = pwd_context.hash(password)
        
        # Assign a UUID for the user
        user_uuid = str(uuid.uuid4())
        
        # Insert into users table with hashed password
        await db.execute(
            "INSERT INTO users (uuid, username, password) VALUES (?, ?, ?)", # Corrected order for uuid and username
            (user_uuid, username, hashed_password) 
        )
        
        # Create initial player state
        player_uuid = str(uuid.uuid4())
        state = {
            "player_id": player_uuid,
            "owner_uuid": user_uuid,
            "vida": 100,
            "mana": 0,
            "dinero": 0,
            "inventario": []
        }
        
        # Insert into players table
        await db.execute(
            "INSERT INTO players (player_id, owner_uuid, state) VALUES (?, ?, ?)",
            (player_uuid, user_uuid, json.dumps(state))
        )
        await db.commit()

    # Redirect to login
    print(f"{tags['app_log']} Usuario: {username} creado con exito. UUID: {user_uuid}")
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login")
async def login_page(request: Request):
    """Renders the login page."""
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """
    Handles user login authentication.
    If the user does not exist, it returns an error.
    """
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT password, uuid FROM users WHERE username = ?", (username, ))
        row = await cursor.fetchone()

        if row:
            # User exists, verify password
            hashed_password = row[0]
            user_uuid = row[1]
            if pwd_context.verify(password, hashed_password):
                token = create_access_token({"sub": user_uuid})
                response = RedirectResponse(url="/lobby", status_code=303)
                response.set_cookie("access_token", token, httponly=True, samesite="strict")
                print(f"{tags['app_log']} User: {username} UUID: {user_uuid} Logged sucessfully.")
                return response
            else:
                print(f"{tags['app_error']} Usuario: {username} o contraseña invalidos.")
                return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña inválidos"})
        else:
            # User does not exist, return an error
            print(f"{tags['app_error']} Usuario: {username} no existe.")
            return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña inválidos"})

@app.get("/logout")
async def logout(request: Request): # Add request parameter to access cookies
    """Logs out the user by deleting the access token cookie."""
    user_uuid = None
    token = request.cookies.get("access_token")
    if token:
        user_uuid = verify_token(token)
        if user_uuid:
            # Optionally, retrieve username from DB if needed for logging
            async with aiosqlite.connect(db_file) as db:
                cursor = await db.execute("SELECT username FROM users WHERE uuid = ?", (user_uuid,))
                username_row = await cursor.fetchone()
                username = username_row[0] if username_row else "Unknown User"
            print(f"{tags['app_log']} User: {username} UUID: {user_uuid} Logged out successfully.")
        else:
            print(f"{tags['app_error']} Logout attempt with invalid token.")
    else:
        print(f"{tags['app_error']} Logout attempt without an access token.")

    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response

# Fallback exception handler for 404 and other HTTP exceptions
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    """Custom handler for HTTP exceptions, rendering specific error pages."""
    if exc.status_code == 404:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    else:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

## Websockets

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handles WebSocket connections for player and master state synchronization."""
    await websocket.accept()
    user_uuid = None
    try:
        data = await websocket.receive_json()
        if data["type"] == "connect":
            user_uuid = verify_token(data["token"])
            if not user_uuid:
                await websocket.send_json({"error": "Token inválido"})
                await websocket.close()
                print(f"{tags['websocket']} Connection rejected: Invalid token.")
                return
            if data["role"] == "master":
                print(f"{tags['websocket']} connected: {user_uuid} as: master")
                connections[user_uuid] = {"ws": websocket, "role": "master"}
                players_state = await get_active_players_state()
                await websocket.send_json({
                    "type": "players_state",
                    "players": players_state
                })

            elif data["role"] == "player":
                connections[user_uuid] = {"ws": websocket, "role": "player"}
                print(f"{tags['websocket']} connected: {user_uuid} as: player")
                # Query characters for the connected player
                async with aiosqlite.connect(db_file) as db:
                    cursor = await db.execute(
                        "SELECT player_id, state FROM players WHERE owner_uuid = ?", (user_uuid,)
                    )
                    rows = await cursor.fetchall()
                    characters = [{"player_id": r[0], "state": json.loads(r[1])} for r in rows]

                await websocket.send_json({
                    "type": "characters_list",
                    "characters": characters
                })

                if not characters:
                    await websocket.send_json({
                        "type": "no_characters",
                        "message": "No tienes personajes aún. Crea uno nuevo."
                    })

        # Keep the connection alive for incoming messages (e.g., player state updates)
        while True:
            # This loop will continue to receive messages from the client
            # For players, this might be state updates. For masters, it might be commands.
            # You'll need to add logic here to handle different message types.
            received_data = await websocket.receive_json()
            print(f"{tags['websocket']} Received data from {user_uuid}: {received_data}")

            # Example: If a player sends an update to their state
            if user_uuid and connections.get(user_uuid, {}).get("role") == "player" and received_data.get("type") == "player_update":
                player_id_to_update = received_data.get("player_id")
                new_state = received_data.get("state")
                if player_id_to_update and new_state:
                    # Ensure the player_id being updated belongs to this user_uuid
                    async with aiosqlite.connect(db_file) as db:
                        cursor = await db.execute("SELECT owner_uuid FROM players WHERE player_id = ?", (player_id_to_update,))
                        owner_row = await cursor.fetchone()
                        if owner_row and owner_row[0] == user_uuid:
                            await save_player_state(player_id_to_update, new_state)
                            print(f"{tags['app_log']} Player {player_id_to_update} state updated by {user_uuid}")
                        else:
                            print(f"{tags['app_error']} Unauthorized state update attempt for player {player_id_to_update} by {user_uuid}")
            
    except WebSocketDisconnect:
        if user_uuid and user_uuid in connections:
            del connections[user_uuid]
            print(f"{tags['websocket']} Disconnected: {user_uuid}")
    except Exception as e:
        print(f"{tags['app_error']} WebSocket error for {user_uuid}: {e}")
        if user_uuid and user_uuid in connections:
            del connections[user_uuid]


if __name__ == "__main__":
    # Run the FastAPI application using Uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
