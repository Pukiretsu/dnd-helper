import json
import uuid
from contextlib import asynccontextmanager

from jose import JWTError, jwt
from datetime import datetime, timedelta

import aiosqlite
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from passlib.context import CryptContext

# Configure the password hashing context
# We use bcrypt as the recommended hashing algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = "un_secreto_super_seguro"  # cámbialo por uno fuerte
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 día

# Global variables (consider using FastAPI's dependency injection or app.state for larger apps)
connections = {}
db_file = "sessions.db"

tags = {
    "server": "\033[96mSERVER\033[0m",
    "db": "\033[92mDATABASE\033[0m"
}

from jose import JWTError, jwt
from datetime import datetime, timedelta

SECRET_KEY = "un_secreto_super_seguro"  # cámbialo por uno fuerte
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 día

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    user_uuid = verify_token(token) if token else None
    if not user_uuid:
        raise HTTPException(status_code=303, detail="No autenticado", headers={"Location": "/login"})
    return user_uuid

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
    print(f"--> [{tags['db']}] Database initialized.")

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for application startup and shutdown events.
    """
    print(f"--> [{tags['server']}] Application startup event: Initializing database...")
    await init_db()
    yield
    # Code here runs on application shutdown (e.g., closing database connections)
    print(f"--> [{tags['server']}] Application shutdown event: Performing cleanup...")

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
    user_uuid = get_current_user(request)
    return templates.TemplateResponse("lobby.html", {"request": request, "user_uuid": user_uuid})

@app.get("/player")
async def get_player_page(request: Request):
    """Renders the player page (protected by JWT)."""
    user_uuid = get_current_user(request)
    return templates.TemplateResponse("player.html", {"request": request, "user_uuid": user_uuid})

@app.get("/master")
async def get_master_page(request: Request):
    """Renders the master page (protected by JWT)."""
    user_uuid = get_current_user(request)
    return templates.TemplateResponse("master.html", {"request": request, "user_uuid": user_uuid})

## HANDLERS FOR AUTH

@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})

@app.post("/register")
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    async with aiosqlite.connect(db_file) as db:
        # Check if user already exists
        cursor = await db.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if await cursor.fetchone():
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
            "INSERT INTO users (username, uuid, password) VALUES (?, ?, ?)",
            (username, user_uuid, hashed_password) # Corrected: Use hashed_password
        )
        
        # Create initial player state
        player_uuid = str(uuid.uuid4())
        state = {
            "player_id": player_uuid,
            "owner_uuid": user_uuid, # Corrected: Typo fixed from 'ownner_uuid' to 'owner_uuid'
            "vida": 100,
            "mana": 0,
            "dinero": 0,
            "inventario": []
        }
        
        # Insert into players table with correct number of placeholders and values
        await db.execute(
            "INSERT INTO players (player_id, owner_uuid, state) VALUES (?, ?, ?)", # Corrected: Added third placeholder
            (player_uuid, user_uuid, json.dumps(state))
        )
        await db.commit()

    # Redirect to login
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
                return response
            else:
                return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña inválidos"})
        else:
            # User does not exist, return an error
            return templates.TemplateResponse("login.html", {"request": request, "error": "El usuario no existe"})

@app.get("/logout")
async def logout():
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
#FIXME
async def websocket_endpoint(websocket: WebSocket, username: str):
    """Handles WebSocket connections for player state synchronization."""
    await websocket.accept()
    
    # Load player state from DB or initialize new state
    players_state = await load_players_state()
    state = players_state.get(username, {
        "player_id": username,
        "vida": 100,
        "mana": 50,
        "dinero": 0,
        "inventario": [],
    })
    
    # Store the WebSocket connection
    connections[username] = websocket
    
    # Send initial state to the connected player
    await websocket.send_json(state)

    try:
        while True:
            # Receive updated state from the client
            data = await websocket.receive_json()
            players_state[data["player_id"]] = data
            
            # Save the updated state to the database
            await save_player_state(data["player_id"], data)
            
            # Broadcast the updated states to all connected clients
            for conn in connections.values():
                await conn.send_json(players_state)
    except WebSocketDisconnect:
        # Remove the disconnected client from connections
        print(f"WebSocket disconnected: {username}")
        del connections[username]
    except Exception as e:
        print(f"WebSocket error for {username}: {e}")
        # Optionally, remove the connection on other errors too
        if username in connections:
            del connections[username]


if __name__ == "__main__":
    # Run the FastAPI application using Uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)