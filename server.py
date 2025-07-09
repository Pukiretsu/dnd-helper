import asyncio
import json
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

print(">>>servidor corriendo<<<<")

# Configure the password hashing context
# We use bcrypt as the recommended hashing algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration
SECRET_KEY = "un_secreto_super_seguro"  # Change this to a strong, unique secret key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# Global variables (consider using FastAPI's dependency injection or app.state for larger apps)
# connections now stores user_uuid -> {"ws": WebSocket, "role": "player/master", "selected_player_id": "..."}
connections = {}
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

async def load_player_state_by_id(player_id: str):
    """Loads a single player's state from the database by player_id."""
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT state FROM players WHERE player_id = ?", (player_id,))
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

async def get_active_players_state():
    """
    Retrieves the state of players who are currently connected via WebSocket
    and have a character selected.
    Returns:
        dict: A dictionary where keys are player_ids and values are their states.
    """
    active_players_data = {}
    async with aiosqlite.connect(db_file) as db:
        for user_uuid, conn_info in connections.items():
            if conn_info["role"] == "player" and "selected_player_id" in conn_info:
                selected_player_id = conn_info["selected_player_id"]
                cursor = await db.execute(
                    "SELECT player_id, state FROM players WHERE player_id = ? AND owner_uuid = ?",
                    (selected_player_id, user_uuid)
                )
                row = await cursor.fetchone()
                if row:
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
async def get_player_page(
    request: Request,
    access_token: str = Cookie(None),
    selected_player_id: str = Cookie(None, alias="selected_player_id_cookie") # Read the cookie
):
    """
    Renders the player page, showing all characters owned by the authenticated user
    and highlighting the selected one.
    Requires authentication.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")

    user_characters = []
    selected_character = None

    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute(
            "SELECT player_id, state FROM players WHERE owner_uuid = ?", (user_uuid,)
        )
        rows = await cursor.fetchall()
        for row in rows:
            character_data = {"player_id": row[0], "state": json.loads(row[1])}
            user_characters.append(character_data)
            if selected_player_id and row[0] == selected_player_id:
                selected_character = character_data

    return templates.TemplateResponse("player.html", {
        "request": request,
        "token": access_token,
        "characters": user_characters,
        "selected_character": selected_character # Pass the selected character to the template
    })

@app.post("/select-character")
async def select_character_post(
    request: Request,
    player_id: str = Form(...),
    access_token: str = Cookie(None)
):
    """
    Handles the selection of a character by a player.
    Sets a cookie with the selected player's ID.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        raise HTTPException(status_code=401, detail="No autenticado")

    async with aiosqlite.connect(db_file) as db:
        # Verify that the player_id belongs to the authenticated user_uuid
        cursor = await db.execute(
            "SELECT 1 FROM players WHERE player_id = ? AND owner_uuid = ?",
            (player_id, user_uuid)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=403, detail="Personaje no encontrado o no pertenece a este usuario.")

    response = RedirectResponse(url="/player", status_code=303)
    # Set a new cookie for the selected player ID
    response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    print(f"{tags['app_log']} User {user_uuid} selected player: {player_id}")
    return response


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
            "INSERT INTO users (uuid, username, password) VALUES (?, ?, ?)",
            (user_uuid, username, hashed_password) 
        )
        await db.commit() # Commit user creation

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
async def logout(request: Request):
    """Logs out the user by deleting the access token cookie."""
    user_uuid = None
    token = request.cookies.get("access_token")
    if token:
        user_uuid = verify_token(token)
        if user_uuid:
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
    # Also delete the selected_player_id cookie on logout
    response.delete_cookie("selected_player_id_cookie")
    return response

# New routes for character creation
@app.get("/create-character")
async def create_character_page(request: Request, access_token: str = Cookie(None)):
    """Renders the character creation page."""
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    return templates.TemplateResponse("create_character.html", {"request": request, "error": ""})

@app.post("/create-character")
async def create_character_post(
    request: Request,
    access_token: str = Cookie(None),
    nombre: str = Form(...),
    clase: str = Form(...),
    nivel: int = Form(...),
    fuerza: int = Form(...),
    ingenio: int = Form(...),
    corazon: int = Form(...),
    vida: int = Form(...),
    mana: int = Form(...),
    dinero: int = Form(...),
    defensa: int = Form(...),
    arma_equipada: str = Form(...),
    armadura_equipada: str = Form(...),
    inventario: str = Form(""), # Expect comma-separated string
    habilidades: str = Form(""), # Expect comma-separated string
    canciones_aprendidas: str = Form(""), # Expect comma-separated string
):
    """Handles the creation of a new character."""
    user_uuid = verify_token(access_token)
    if not user_uuid:
        raise HTTPException(status_code=401, detail="No autenticado")

    player_id = str(uuid.uuid4())
    
    # Parse comma-separated strings into lists
    parsed_inventario = [item.strip() for item in inventario.split(',') if item.strip()]
    parsed_habilidades = [item.strip() for item in habilidades.split(',') if item.strip()]
    parsed_canciones_aprendidas = [item.strip() for item in canciones_aprendidas.split(',') if item.strip()]

    character_state = {
        "player_id": player_id, # Redundant but kept for consistency with current state structure
        "nombre": nombre,
        "clase": clase,
        "nivel": nivel,
        "fuerza": fuerza,
        "ingenio": ingenio,
        "corazon": corazon,
        "vida": vida,
        "mana": mana,
        "dinero": dinero,
        "defensa": defensa,
        "arma_equipada": arma_equipada,
        "armadura_equipada": armadura_equipada,
        "inventario": parsed_inventario,
        "habilidades": parsed_habilidades,
        "canciones_aprendidas": parsed_canciones_aprendidas,
    }

    try:
        async with aiosqlite.connect(db_file) as db:
            await db.execute(
                "INSERT INTO players (player_id, owner_uuid, state) VALUES (?, ?, ?)",
                (player_id, user_uuid, json.dumps(character_state))
            )
            await db.commit()
        print(f"{tags['app_log']} Personaje '{nombre}' creado por usuario {user_uuid} (Player ID: {player_id}).")
        
        # After creating a character, automatically select it for the user
        response = RedirectResponse(url="/player", status_code=303)
        response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        return response

    except Exception as e:
        print(f"{tags['app_error']} Error al crear personaje: {e}")
        return templates.TemplateResponse("create_character.html", {
            "request": request,
            "error": f"Error al crear personaje: {e}"
        })


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
    selected_player_id = None # Initialize selected_player_id

    try:
        data = await websocket.receive_json()
        if data["type"] == "connect":
            user_uuid = verify_token(data["token"])
            if not user_uuid:
                await websocket.send_json({"error": "Token inválido"})
                await websocket.close()
                print(f"{tags['websocket']} Connection rejected: Invalid token.")
                return

            role = data.get("role")
            
            if role == "master":
                print(f"{tags['websocket']} connected: {user_uuid} as: master")
                connections[user_uuid] = {"ws": websocket, "role": "master"}
                players_state = await get_active_players_state()
                await websocket.send_json({
                    "type": "players_state",
                    "players": players_state
                })

            elif role == "player":
                selected_player_id = data.get("player_id")
                if not selected_player_id:
                    await websocket.send_json({"error": "No player_id provided for player connection."})
                    await websocket.close()
                    print(f"{tags['websocket']} Player connection rejected: No player_id.")
                    return

                # Verify that the selected_player_id belongs to this user_uuid
                async with aiosqlite.connect(db_file) as db:
                    cursor = await db.execute(
                        "SELECT 1 FROM players WHERE player_id = ? AND owner_uuid = ?",
                        (selected_player_id, user_uuid)
                    )
                    if not await cursor.fetchone():
                        await websocket.send_json({"error": "Player ID does not belong to this user."})
                        await websocket.close()
                        print(f"{tags['websocket']} Player connection rejected: Unauthorized player_id.")
                        return

                connections[user_uuid] = {"ws": websocket, "role": "player", "selected_player_id": selected_player_id}
                print(f"{tags['websocket']} connected: {user_uuid} as: player with character: {selected_player_id}")
                
                # Send the initial state of the selected character
                initial_state = await load_player_state_by_id(selected_player_id)
                if initial_state:
                    await websocket.send_json({
                        "type": "player_state_update",
                        "player_id": selected_player_id,
                        "state": initial_state
                    })
                else:
                    await websocket.send_json({"error": "Selected character state not found."})
                    await websocket.close()
                    return

        # Keep the connection alive for incoming messages (e.g., player state updates)
        while True:
            received_data = await websocket.receive_json()
            print(f"{tags['websocket']} Received data from {user_uuid} (Role: {connections.get(user_uuid, {}).get('role')}): {received_data}")

            # Handle player state updates
            if connections.get(user_uuid, {}).get("role") == "player" and received_data.get("type") == "player_update":
                # Ensure the update is for the currently selected character
                if received_data.get("player_id") == selected_player_id:
                    new_state = received_data.get("state")
                    if new_state:
                        await save_player_state(selected_player_id, new_state)
                        print(f"{tags['app_log']} Player {selected_player_id} state updated by {user_uuid}")
                        
                        # Broadcast updated state to all masters
                        updated_player_data = {selected_player_id: new_state}
                        for master_uuid, master_conn in connections.items():
                            if master_conn["role"] == "master":
                                try:
                                    await master_conn["ws"].send_json({
                                        "type": "players_state",
                                        "players": updated_player_data # Send only the updated player
                                    })
                                except Exception as e:
                                    print(f"Error broadcasting update to master {master_uuid}: {e}")
                else:
                    print(f"{tags['app_error']} Unauthorized update attempt: player_id mismatch for {user_uuid}")
            # Add more logic here for other message types (e.g., master commands)
            
    except WebSocketDisconnect:
        if user_uuid and user_uuid in connections:
            role = connections[user_uuid]["role"]
            del connections[user_uuid]
            print(f"{tags['websocket']} Disconnected: {user_uuid} (Role: {role})")
    except Exception as e:
        print(f"{tags['app_error']} WebSocket error for {user_uuid}: {e}")
        if user_uuid and user_uuid in connections:
            del connections[user_uuid]


if __name__ == "__main__":
    # Run the FastAPI application using Uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
