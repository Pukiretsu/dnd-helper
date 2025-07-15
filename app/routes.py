# routes.py

import json
import uuid
import aiosqlite
from fastapi import APIRouter, Form, Request, HTTPException, Cookie
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import TAGS, ACCESS_TOKEN_EXPIRE_MINUTES
from auth import pwd_context, create_access_token, verify_token, get_current_user, authenticate_user, get_user_info_for_logout, get_username_by_uuid # Import get_username_by_uuid
from database import (
    get_user_by_username,
    create_new_user,
    create_initial_player_character,
    get_characters_by_owner_uuid,
    check_player_ownership,
    get_lobbies_by_master_uuid,
    get_lobby_db,
    load_player_state_by_id,
    get_username_for_player_id
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

## General Routes

@router.get("/")
async def get_index_page(request: Request):
    """Renders the main index page."""
    return templates.TemplateResponse("index.html", {"request": request})

@router.get("/lobby")
async def lobby_page(request: Request):
    """Renders the lobby page, requiring authentication."""
    user_uuid = get_current_user(request)
    return templates.TemplateResponse("lobby.html", {"request": request, "user_uuid": user_uuid})

## Player Routes

@router.get("/player")
async def get_player_page(
    request: Request,
    access_token: str = Cookie(None),
    selected_player_id: str = Cookie(None, alias="selected_player_id_cookie")
):
    """
    Renders the player page, showing all characters owned by the authenticated user
    and highlighting the selected one.
    Requires authentication.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")

    # Get username for welcome message
    username = await get_username_by_uuid(user_uuid)

    user_characters = await get_characters_by_owner_uuid(user_uuid)
    selected_character = None

    if selected_player_id:
        for char in user_characters:
            if char["player_id"] == selected_player_id:
                selected_character = char
                break
    
    # Get active lobbies for player to join
    active_lobbies = []
    async with aiosqlite.connect("data/sessions.db") as db:
        cursor = await db.execute("SELECT lobby_id, master_uuid, lobby_name, status FROM lobbies WHERE status IN ('waiting', 'in_progress')")
        lobbies_data = await cursor.fetchall()

    for row in lobbies_data:
        master_username = await get_username_by_uuid(row[1]) # Use get_username_by_uuid for master's name
        active_lobbies.append({
            "lobby_id": row[0],
            "master_uuid": row[1],
            "lobby_name": row[2],
            "status": row[3],
            "master_username": master_username if master_username else "Unknown Master"
        })

    return templates.TemplateResponse("player.html", {
        "request": request,
        "token": access_token,
        "username": username, # Pass username to template
        "characters": user_characters,
        "selected_character": selected_character,
        "active_lobbies": active_lobbies
    })

@router.post("/select-character")
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
        return RedirectResponse("/login")

    if not await check_player_ownership(player_id, user_uuid):
        username = await get_username_by_uuid(user_uuid)
        user_characters = await get_characters_by_owner_uuid(user_uuid)
        active_lobbies = []
        lobbies_data = []
        async with aiosqlite.connect("data/sessions.db") as db:
            cursor = await db.execute("SELECT lobby_id, master_uuid, lobby_name, status FROM lobbies WHERE status IN ('waiting', 'in_progress')")
            lobbies_data = await cursor.fetchall()

        for row in lobbies_data:
            master_username = await get_username_by_uuid(row[1])
            active_lobbies.append({
                "lobby_id": row[0],
                "master_uuid": row[1],
                "lobby_name": row[2],
                "status": row[3],
                "master_username": master_username if master_username else "Unknown Master"
            })

        return templates.TemplateResponse("player.html", {
            "request": request,
            "error": "Personaje no encontrado o no pertenece a este usuario.",
            "username": username,
            "characters": user_characters,
            "token": access_token,
            "selected_character": None,
            "active_lobbies": active_lobbies
        })

    response = RedirectResponse(url="/player", status_code=303)
    response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    print(f"{TAGS['app_log']} User {user_uuid} selected player: {player_id}")
    return response

## Master Routes

@router.get("/master")
async def get_master_page(request: Request, access_token: str = Cookie(None)):
    """
    Renders the master page, showing active lobbies and players ready to join.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    
    master_lobbies = await get_lobbies_by_master_uuid(user_uuid)

    return templates.TemplateResponse("master.html", {
        "request": request,
        "token": access_token,
        "master_uuid": user_uuid,
        "master_lobbies": master_lobbies
    })

## Game Routes

@router.get("/game-player")
async def game_player_page(
    request: Request,
    access_token: str = Cookie(None),
    selected_player_id: str = Cookie(None, alias="selected_player_id_cookie"),
    lobby_id: str = ""
):
    """
    Renders the in-game page for a player.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    
    if not selected_player_id:
        return RedirectResponse("/player", status_code=303)

    player_character_state = await load_player_state_by_id(selected_player_id)
    if not player_character_state or not await check_player_ownership(selected_player_id, user_uuid):
        return RedirectResponse("/player", status_code=303)

    # Verify lobby_id and status if player is trying to join a game
    if lobby_id:
        lobby_info = await get_lobby_db(lobby_id)
        if not lobby_info or lobby_info["status"] not in ["in_progress"]: # Only allow if in_progress
             return RedirectResponse("/player", status_code=303) # Redirect if lobby not active
        # Ensure player is actually in this lobby's player list (for robustness)
        if selected_player_id not in lobby_info["players_in_lobby"]:
            return RedirectResponse("/player", status_code=303)


    return templates.TemplateResponse("game_player.html", {
        "request": request,
        "token": access_token,
        "character": player_character_state,
        "lobby_id": lobby_id
    })

@router.get("/game-master")
async def game_master_page(
    request: Request,
    access_token: str = Cookie(None),
    lobby_id: str = ""
):
    """
    Renders the in-game administration page for the master.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")

    if not lobby_id:
        return RedirectResponse("/master", status_code=303)

    lobby_info = await get_lobby_db(lobby_id)
    if not lobby_info or lobby_info["master_uuid"] != user_uuid or lobby_info["status"] not in ["in_progress"]:
        return RedirectResponse("/master", status_code=303)

    players_in_lobby_details = []
    for player_id in lobby_info["players_in_lobby"]:
        player_state = await load_player_state_by_id(player_id)
        username = await get_username_for_player_id(player_id)
        if player_state:
            players_in_lobby_details.append({
                "player_id": player_id,
                "username": username,
                "state": player_state
            })

    return templates.TemplateResponse("game_master.html", {
        "request": request,
        "token": access_token,
        "lobby_id": lobby_id,
        "lobby_info": lobby_info,
        "players_in_lobby_details": players_in_lobby_details
    })


## Auth Handlers

@router.get("/register")
async def register_page(request: Request):
    """Renders the registration page."""
    return templates.TemplateResponse("register.html", {"request": request, "error": ""})

@router.post("/register")
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Handles user registration, hashing the password and creating initial player state."""
    if await get_user_by_username(username):
        print(f"{TAGS['app_error']} El usuario: {username} ya existe.")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "¡El usuario ya existe!"
        })
    
    hashed_password = pwd_context.hash(password)
    user_uuid = str(uuid.uuid4())
    await create_new_user(username, hashed_password, user_uuid)

    print(f"{TAGS['app_log']} Usuario: {username} creado con exito. UUID: {user_uuid}")
    return RedirectResponse(url="/login", status_code=303)

@router.get("/login")
async def login_page(request: Request):
    """Renders the login page."""
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})

@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """
    Handles user login authentication.
    """
    user_uuid, error_message = await authenticate_user(username, password)

    if error_message:
        return templates.TemplateResponse("login.html", {"request": request, "error": error_message})
    
    token = create_access_token({"sub": user_uuid})
    response = RedirectResponse(url="/lobby", status_code=303)
    response.set_cookie("access_token", token, httponly=True, samesite="strict")
    print(f"{TAGS['app_log']} User: {username} UUID: {user_uuid} Logged sucessfully.")
    return response

@router.get("/logout")
async def logout(request: Request):
    """Logs out the user by deleting the access token cookie."""
    user_uuid, username = await get_user_info_for_logout(request)
    
    if user_uuid:
        print(f"{TAGS['app_log']} User: {username} UUID: {user_uuid} Logged out successfully.")
    else:
        print(f"{TAGS['app_error']} Logout attempt with invalid token or no token.")

    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    response.delete_cookie("selected_player_id_cookie")
    return response

## Character Creation Routes

@router.get("/create-character")
async def create_character_page(request: Request, access_token: str = Cookie(None)):
    """Renders the character creation page."""
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    return templates.TemplateResponse("create_character.html", {"request": request, "error": ""})

@router.post("/create-character")
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
    inventario: str = Form(""),
    habilidades: str = Form(""),
    canciones_aprendidas: str = Form(""),
):
    """Handles the creation of a new character."""
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")

    player_id = str(uuid.uuid4())
    
    parsed_inventario = [item.strip() for item in inventario.split(',') if item.strip()]
    parsed_habilidades = [item.strip() for item in habilidades.split(',') if item.strip()]
    parsed_canciones_aprendidas = [item.strip() for item in canciones_aprendidas.split(',') if item.strip()]

    character_state = {
        "player_id": player_id,
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
        await create_initial_player_character(player_id, user_uuid, character_state)
        print(f"{TAGS['app_log']} Personaje '{nombre}' creado por usuario {user_uuid} (Player ID: {player_id}).")
        
        response = RedirectResponse(url="/player", status_code=303)
        response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        return response

    except Exception as e:
        print(f"{TAGS['app_error']} Error al crear personaje: {e}")
        return templates.TemplateResponse("create_character.html", {
            "request": request,
            "error": f"Error al crear personaje: {e}"
        })
