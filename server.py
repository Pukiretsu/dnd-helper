
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi import Form
from fastapi.responses import RedirectResponse
import uvicorn
import json
import asyncio
import aiosqlite
import uuid

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

connections = {}
db_file = "sessions.db"

async def init_db():
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

async def save_player_state(player_id, state):
    async with aiosqlite.connect(db_file) as db:
        await db.execute(
            "INSERT OR REPLACE INTO players (player_id, state) VALUES (?, ?)",
            (player_id, json.dumps(state))
        )
        await db.commit()

async def load_players_state():
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT player_id, state FROM players")
        rows = await cursor.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

@app.on_event("startup")
async def startup_event():
    await init_db()

## Routes

@app.get("/player")
async def get_player_page(request: Request):
    return templates.TemplateResponse("player.html", {"request": request})

@app.get("/master")
async def get_master_page(request: Request):
    return templates.TemplateResponse("master.html", {"request": request})

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    async with aiosqlite.connect(db_file) as db:
        cursor = await db.execute("SELECT password FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        if row and row[0] == password:
            response = RedirectResponse(url=f"/player?username={username}", status_code=303)
            return response
        else:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña inválidos"})

## Websockets

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    player_id = str(uuid.uuid4())
    players_state = await load_players_state()
    state = players_state.get(player_id, {
        "player_id": player_id,
        "vida": 100,
        "mana": 50,
        "rupias": 0,
        "inventario": [],
    })
    connections[player_id] = websocket
    await websocket.send_json(state)

    try:
        while True:
            data = await websocket.receive_json()
            players_state[data["player_id"]] = data
            await save_player_state(data["player_id"], data)
            for conn in connections.values():
                await conn.send_json(players_state)
    except WebSocketDisconnect:
        del connections[player_id]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
