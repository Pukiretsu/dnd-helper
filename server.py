import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import aiosqlite
import uvicorn
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

connections = {}
db_file = "sessions.db"

tags = {
    "server": "\033[96mSERVER\033[0m",
    "db": "\033[92mDATABASE\033[0m"
}

async def init_db():
    """Initializes the SQLite database, creating tables if they don't exist."""
    async with aiosqlite.connect(db_file) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                player_id TEXT PRIMARY KEY,
                state TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT
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
    """Loads all players' states from the database."""
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT player_id, state FROM players")
        rows = await cursor.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for application startup and shutdown events.
    This replaces @app.on_event("startup") and @app.on_event("shutdown").
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

@app.get("/player")
async def get_player_page(request: Request, username: str = ""):
    """Renders the player page, redirects to login if no username is provided."""
    if not username:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("player.html", {"request": request, "username": username})

@app.get("/master")
async def get_master_page(request: Request):
    """Renders the master page."""
    return templates.TemplateResponse("master.html", {"request": request})

@app.get("/login")
async def login_page(request: Request):
    """Renders the login page."""
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handles user login authentication."""
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT password FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        if row and row[0] == password:
            response = RedirectResponse(url=f"/player?username={username}", status_code=303)
            return response
        else:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña inválidos"})

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
