import json
import uuid
from fastapi import APIRouter, Form, Request, HTTPException, Cookie
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import TAGS, ACCESS_TOKEN_EXPIRE_MINUTES
from auth import pwd_context, create_access_token, verify_token, get_current_user, authenticate_user, get_user_info_for_logout
from database import (
    get_user_by_username,
    create_new_user,
    create_initial_player_character,
    get_characters_by_owner_uuid,
    check_player_ownership
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
    user_uuid = get_current_user(request) # This will handle redirection if not authenticated
    return templates.TemplateResponse("lobby.html", {"request": request, "user_uuid": user_uuid})

## Player Routes

@router.get("/player")
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

    user_characters = await get_characters_by_owner_uuid(user_uuid)
    selected_character = None

    if selected_player_id:
        for char in user_characters:
            if char["player_id"] == selected_player_id:
                selected_character = char
                break

    return templates.TemplateResponse("player.html", {
        "request": request,
        "token": access_token,
        "characters": user_characters,
        "selected_character": selected_character # Pass the selected character to the template
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
        raise HTTPException(status_code=401, detail="No autenticado")

    # Verify that the player_id belongs to the authenticated user_uuid
    if not await check_player_ownership(player_id, user_uuid):
        raise HTTPException(status_code=403, detail="Personaje no encontrado o no pertenece a este usuario.")

    response = RedirectResponse(url="/player", status_code=303)
    # Set a new cookie for the selected player ID
    response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    print(f"{TAGS['app_log']} User {user_uuid} selected player: {player_id}")
    return response

## Master Routes

@router.get("/master")
async def get_master_page(request: Request, access_token: str = Cookie(None)):
    """
    Renders the master page, showing only active players.
    Active players are those currently connected via WebSocket.
    """
    user_uuid = verify_token(access_token)
    if not user_uuid:
        return RedirectResponse("/login")
    
    # Note: active_players_data is now managed by the websocket_manager and broadcast.
    # This route will initially render the page, and master.js will fetch active players via WebSocket.
    return templates.TemplateResponse("master.html", {
        "request": request,
        "token": access_token,
        "active_players": {} # Initial empty dict, data will come via WebSocket
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
    # Check if user already exists
    if await get_user_by_username(username):
        print(f"{TAGS['app_error']} El usuario: {username} ya existe.")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "¡El usuario ya existe!"
        })
    
    # Hash the password
    hashed_password = pwd_context.hash(password)
    
    # Assign a UUID for the user
    user_uuid = str(uuid.uuid4())
    
    # Insert into users table with hashed password
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
    response.delete_cookie("selected_player_id_cookie") # Also delete the selected_player_id cookie on logout
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
        await create_initial_player_character(player_id, user_uuid, character_state)
        print(f"{TAGS['app_log']} Personaje '{nombre}' creado por usuario {user_uuid} (Player ID: {player_id}).")
        
        # After creating a character, automatically select it for the user
        response = RedirectResponse(url="/player", status_code=303)
        response.set_cookie(key="selected_player_id_cookie", value=player_id, httponly=True, samesite="strict", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        return response

    except Exception as e:
        print(f"{TAGS['app_error']} Error al crear personaje: {e}")
        return templates.TemplateResponse("create_character.html", {
            "request": request,
            "error": f"Error al crear personaje: {e}"
        })