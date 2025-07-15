import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect

from config import TAGS
from auth import verify_token
from database import (
    delete_lobby_db,
    load_player_state_by_id,
    save_player_state,
    check_player_ownership,
    create_lobby_db,
    get_lobby_db,
    update_lobby_status_db,
    add_player_to_lobby_db,
    remove_player_from_lobby_db,
    get_username_for_player_id,
    clear_lobby_players_db # New import
)

# Stores active WebSocket connections:
# {user_uuid: {"ws": WebSocket, "role": "player/master", "selected_player_id": "...", "player_status": "...", "current_lobby_id": "..."}}
connections = {}

async def get_active_players_state():
    """
    Retrieves the state of players who are currently connected via WebSocket
    and have a character selected and are in 'ready' or 'in_game' status.
    Returns:
        dict: A dictionary where keys are player_ids and values are their states.
    """
    active_players_data = {}
    for user_uuid, conn_info in connections.items():
        if conn_info["role"] == "player" and "selected_player_id" in conn_info:
            player_id: str = conn_info["selected_player_id"]
            player_status: str = conn_info.get("player_status", "disconnected") # Get current player status
            
            # Only include players who are ready or in-game for the master's view
            if player_status in ["ready", "in_game"]:
                player_state = await load_player_state_by_id(player_id)
                if player_state:
                    # Fetch username for display in master panel
                    username = await get_username_for_player_id(player_id)
                    active_players_data[player_id] = {
                        "state": player_state,
                        "status": player_status,
                        "username": username,
                        "current_lobby_id": conn_info.get("current_lobby_id")
                    }
    return active_players_data

async def broadcast_active_players():
    """
    Periodically broadcasts the state of active players to all connected masters.
    """
    while True:
        active_players = await get_active_players_state()
        # print(f"{TAGS['websocket']} Active players for broadcast: {active_players}") # Uncomment for verbose logging
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
                    print(f"{TAGS['app_error']} Error enviando a master {user_uuid}: {e}")
                    to_remove.append(user_uuid)
        
        # Remove closed master connections
        for user_uuid in to_remove:
            if user_uuid in connections: # Check again in case it was already removed
                del connections[user_uuid]
                print(f"{TAGS['websocket']} Removed disconnected master: {user_uuid}")
                
        await asyncio.sleep(3) # Broadcast every 3 seconds for more responsiveness

async def handle_websocket_connection(websocket: WebSocket):
    """Handles individual WebSocket connections."""
    await websocket.accept()
    user_uuid: str | None = None
    selected_player_id: str | None = None

    try:
        data = await websocket.receive_json()
        if data["type"] == "connect":
            user_uuid = verify_token(data["token"])
            if not user_uuid:
                await websocket.send_json({"error": "Token inválido"})
                await websocket.close()
                print(f"{TAGS['websocket']} Connection rejected: Invalid token.")
                return

            role = data.get("role")
            
            if role == "master":
                print(f"{TAGS['websocket']} connected: {user_uuid} as: master")
                connections[user_uuid] = {"ws": websocket, "role": "master"}
                players_state = await get_active_players_state()
                await websocket.send_json({
                    "type": "players_state",
                    "players": players_state
                })

            elif role == "player":
                player_id_from_data = data.get("player_id")
                if not isinstance(player_id_from_data, str) or not player_id_from_data:
                    await websocket.send_json({"type": "redirect", "url": "/player", "message": "No player_id provided or invalid type for player connection. Redirecting to character selection."})
                    await websocket.close()
                    print(f"{TAGS['websocket']} Player connection rejected: No player_id or invalid type. Redirecting.")
                    return

                selected_player_id = player_id_from_data

                if not await check_player_ownership(selected_player_id, user_uuid):
                    await websocket.send_json({"type": "redirect", "url": "/player", "message": "Player ID does not belong to this user. Redirecting to character selection."})
                    await websocket.close()
                    print(f"{TAGS['websocket']} Player connection rejected: Unauthorized player_id. Redirecting.")
                    return

                # Check if player was already in a game lobby
                current_lobby_id = data.get("lobby_id")
                if current_lobby_id:
                    lobby = await get_lobby_db(current_lobby_id)
                    if lobby and lobby["status"] == "in_progress" and selected_player_id in lobby["players_in_lobby"]:
                        # Player is rejoining an in-progress game
                        player_status = "in_game"
                        print(f"{TAGS['websocket']} Player {selected_player_id} rejoining in-progress lobby {current_lobby_id}")
                    else:
                        # Lobby not found, not in progress, or player not in lobby
                        # Default to connected status if not a valid re-join
                        player_status = "connected"
                        current_lobby_id = None # Clear invalid lobby ID
                        await websocket.send_json({"type": "alert", "message": "No se pudo re-unir a la partida. Volviendo a la selección de personaje."})
                        await websocket.send_json({"type": "redirect", "url": "/player"})
                        await websocket.close()
                        return
                else:
                    player_status = "connected" # Default for new connection

                connections[user_uuid] = {
                    "ws": websocket,
                    "role": "player",
                    "selected_player_id": selected_player_id,
                    "player_status": player_status,
                    "current_lobby_id": current_lobby_id
                }
                print(f"{TAGS['websocket']} connected: {user_uuid} as: player with character: {selected_player_id}, status: {player_status}")
                
                initial_state = await load_player_state_by_id(selected_player_id)
                if initial_state:
                    await websocket.send_json({
                        "type": "player_state_update",
                        "player_id": selected_player_id,
                        "state": initial_state
                    })
                    # If rejoining game, redirect immediately
                    if player_status == "in_game" and current_lobby_id:
                        await websocket.send_json({"type": "game_started", "lobby_id": current_lobby_id})
                else:
                    await websocket.send_json({"type": "redirect", "url": "/player", "message": "Selected character state not found. Redirecting to character selection."})
                    await websocket.close()
                    return

        # Main loop for receiving messages
        while True:
            received_data = await websocket.receive_json()
            print(f"{TAGS['websocket']} Received data from {user_uuid} (Role: {connections.get(user_uuid, {}).get('role')}): {received_data}")

            # --- Player specific messages ---
            if connections.get(user_uuid, {}).get("role") == "player":
                current_selected_player_id_from_conn: str = connections[user_uuid]["selected_player_id"]

                if received_data.get("type") == "player_update":
                    if received_data.get("player_id") == current_selected_player_id_from_conn:
                        new_state = received_data.get("state")
                        if new_state:
                            await save_player_state(current_selected_player_id_from_conn, new_state)
                            print(f"{TAGS['app_log']} Player {current_selected_player_id_from_conn} state updated by {user_uuid}")
                            
                            # Broadcast updated state to all masters
                            updated_player_data = {current_selected_player_id_from_conn: new_state}
                            for master_uuid, master_conn in connections.items():
                                if master_conn["role"] == "master":
                                    try:
                                        await master_conn["ws"].send_json({
                                            "type": "players_state",
                                            "players": updated_player_data # Send only the updated player
                                        })
                                    except Exception as e:
                                        print(f"{TAGS['app_error']} Error broadcasting update to master {master_uuid}: {e}")
                
                elif received_data.get("type") == "player_ready":
                    player_id_ready = current_selected_player_id_from_conn
                    lobby_id_to_join = received_data.get("lobby_id")

                    if lobby_id_to_join:
                        lobby = await get_lobby_db(lobby_id_to_join)
                        # Allow joining if waiting or in_progress
                        if lobby and lobby["status"] in ["waiting", "in_progress"]:
                            await add_player_to_lobby_db(lobby_id_to_join, player_id_ready)
                            connections[user_uuid]["player_status"] = "ready" # Set status to ready
                            connections[user_uuid]["current_lobby_id"] = lobby_id_to_join
                            await websocket.send_json({"type": "ready_ack", "message": f"Listo en lobby {lobby['lobby_name']}!"})
                            print(f"{TAGS['app_log']} Player {player_id_ready} is READY for lobby {lobby_id_to_join}")
                            # If lobby is already in progress, redirect player immediately
                            if lobby["status"] == "in_progress":
                                connections[user_uuid]["player_status"] = "in_game"
                                await websocket.send_json({"type": "game_started", "lobby_id": lobby_id_to_join})
                        else:
                            await websocket.send_json({"type": "error", "message": "Lobby no encontrado o no está disponible para unirse."})
                    else:
                        await websocket.send_json({"type": "error", "message": "No se proporcionó ID de lobby."})

                elif received_data.get("type") == "player_unready":
                    player_id_unready = current_selected_player_id_from_conn
                    current_lobby_id = connections[user_uuid].get("current_lobby_id")

                    if current_lobby_id:
                        lobby = await get_lobby_db(current_lobby_id)
                        if lobby and lobby["status"] == "waiting": # Only unready if lobby is waiting
                            await remove_player_from_lobby_db(current_lobby_id, player_id_unready)
                            connections[user_uuid]["player_status"] = "connected"
                            connections[user_uuid]["current_lobby_id"] = None
                            await websocket.send_json({"type": "unready_ack", "message": "Ya no estás listo para la partida."})
                            print(f"{TAGS['app_log']} Player {player_id_unready} is UNREADY for lobby {current_lobby_id}")
                        else:
                            await websocket.send_json({"type": "error", "message": "No puedes dejar de estar listo en un lobby que ya ha comenzado."})
                    else:
                        await websocket.send_json({"type": "error", "message": "No estás en ningún lobby para dejar de estar listo."})


            # --- Master specific messages ---
            elif connections.get(user_uuid, {}).get("role") == "master":
                if received_data.get("type") == "create_lobby":
                    lobby_name = received_data.get("lobby_name", "Partida Sin Nombre")
                    lobby_id = await create_lobby_db(str(user_uuid), lobby_name)
                    connections[user_uuid]["current_lobby_id"] = lobby_id # Master is now tied to this lobby
                    await websocket.send_json({"type": "lobby_created", "lobby_id": lobby_id, "lobby_name": lobby_name})
                    print(f"{TAGS['app_log']} Master {user_uuid} created lobby: {lobby_id} with name '{lobby_name}'")
                
                elif received_data.get("type") == "start_game":
                    lobby_id_to_start = received_data.get("lobby_id")
                    if lobby_id_to_start:
                        lobby = await get_lobby_db(lobby_id_to_start)
                        if lobby and lobby["master_uuid"] == user_uuid and lobby["status"] == "waiting":
                            await update_lobby_status_db(lobby_id_to_start, "in_progress")
                            print(f"{TAGS['app_log']} Master {user_uuid} started game for lobby: {lobby_id_to_start}")

                            # Notify all players in this lobby to redirect
                            for p_id in lobby["players_in_lobby"]:
                                for u_uuid, conn_info in connections.items():
                                    if conn_info["role"] == "player" and conn_info.get("selected_player_id") == p_id:
                                        conn_info["player_status"] = "in_game" # Update player status
                                        try:
                                            await conn_info["ws"].send_json({"type": "game_started", "lobby_id": lobby_id_to_start})
                                            print(f"{TAGS['websocket']} Sent game_started to player {p_id}")
                                        except Exception as e:
                                            print(f"{TAGS['app_error']} Error sending game_started to player {p_id}: {e}")
                                            # Consider removing player from lobby if connection is broken
                            
                            # Notify the master to redirect
                            await websocket.send_json({"type": "game_started", "lobby_id": lobby_id_to_start})
                        else:
                            await websocket.send_json({"type": "error", "message": "Lobby no encontrado, no está en estado de espera, o no te pertenece."})
                    else:
                        await websocket.send_json({"type": "error", "message": "No se proporcionó ID de lobby para iniciar."})

                elif received_data.get("type") == "end_game":
                    lobby_id_to_end = received_data.get("lobby_id")
                    if lobby_id_to_end:
                        lobby = await get_lobby_db(lobby_id_to_end)
                        if lobby and lobby["master_uuid"] == user_uuid and lobby["status"] == "in_progress":
                            await update_lobby_status_db(lobby_id_to_end, "finished")
                            await clear_lobby_players_db(lobby_id_to_end) # Clear players from lobby DB
                            print(f"{TAGS['app_log']} Master {user_uuid} ended game for lobby: {lobby_id_to_end}")

                            # Notify all players who were in this lobby to redirect to lobby page
                            for p_id in lobby["players_in_lobby"]:
                                for u_uuid, conn_info in connections.items():
                                    if conn_info["role"] == "player" and conn_info.get("selected_player_id") == p_id and conn_info.get("current_lobby_id") == lobby_id_to_end:
                                        conn_info["player_status"] = "connected" # Reset player status
                                        conn_info["current_lobby_id"] = None
                                        try:
                                            await conn_info["ws"].send_json({"type": "game_ended", "message": "La partida ha terminado. Volviendo al lobby."})
                                            print(f"{TAGS['websocket']} Sent game_ended to player {p_id}")
                                        except Exception as e:
                                            print(f"{TAGS['app_error']} Error sending game_ended to player {p_id}: {e}")
                            
                            # Notify the master to redirect
                            await websocket.send_json({"type": "game_ended", "message": "Has terminado la partida. Volviendo al panel de máster."})
                        else:
                            await websocket.send_json({"type": "error", "message": "Lobby no encontrado, no está en curso, o no te pertenece."})
                    else:
                        await websocket.send_json({"type": "error", "message": "No se proporcionó ID de lobby para terminar."})

                elif received_data.get("type") == "delete_lobby":
                    lobby_id_to_delete = received_data.get("lobby_id")
                    if lobby_id_to_delete:
                        lobby = await get_lobby_db(lobby_id_to_delete)
                        if lobby and lobby["master_uuid"] == user_uuid and lobby["status"] == "waiting":
                            # Before deleting, ensure no players are marked as ready in this lobby
                            players_in_lobby = lobby["players_in_lobby"]
                            for p_id in players_in_lobby:
                                for u_uuid, conn_info in connections.items():
                                    if conn_info["role"] == "player" and conn_info.get("selected_player_id") == p_id and conn_info.get("current_lobby_id") == lobby_id_to_delete:
                                        conn_info["player_status"] = "connected"
                                        conn_info["current_lobby_id"] = None
                                        try:
                                            await conn_info["ws"].send_json({"type": "lobby_deleted", "message": "El lobby al que estabas listo ha sido eliminado. Volviendo a la selección de personaje."})
                                        except Exception as e:
                                            print(f"{TAGS['app_error']} Error notifying player {p_id} about lobby deletion: {e}")

                            await clear_lobby_players_db(lobby_id_to_delete) # Clear players from lobby DB
                            await delete_lobby_db(lobby_id_to_delete)
                            await websocket.send_json({"type": "lobby_deleted_ack", "message": "Lobby eliminado exitosamente."})
                            print(f"{TAGS['app_log']} Master {user_uuid} deleted lobby: {lobby_id_to_delete}")
                        else:
                            await websocket.send_json({"type": "error", "message": "Lobby no encontrado, no está en estado de espera, o no te pertenece."})
                    else:
                        await websocket.send_json({"type": "error", "message": "No se proporcionó ID de lobby para eliminar."})
            
    except WebSocketDisconnect:
        if user_uuid and user_uuid in connections:
            role = connections[user_uuid]["role"]
            player_id = connections[user_uuid].get("selected_player_id")
            current_lobby_id = connections[user_uuid].get("current_lobby_id")

            # If player disconnects while in a lobby, remove them from the lobby's player list
            if role == "player":
                if current_lobby_id and player_id:
                    lobby = await get_lobby_db(current_lobby_id)
                    if lobby and lobby["status"] in ["waiting", "in_progress"]:
                        await remove_player_from_lobby_db(current_lobby_id, player_id)
                        print(f"{TAGS['app_log']} Player {player_id} removed from lobby {current_lobby_id} due to disconnect.")
                # Always send player back to character selection on disconnect
                # This message is sent before deleting the connection, so it should be received by the client
                # if the client is still listening before the connection fully closes.
                # However, for a clean redirect, it's better to rely on the client-side
                # WebSocket `onclose` to trigger a redirect to `/player`.
                # await websocket.send_json({"type": "redirect", "url": "/player", "message": "Has sido desconectado. Volviendo a la selección de personaje."})

            del connections[user_uuid]
            print(f"{TAGS['websocket']} Disconnected: {user_uuid} (Role: {role})")
    except Exception as e:
        print(f"{TAGS['app_error']} WebSocket error for {user_uuid}: {e}")
        if user_uuid and user_uuid in connections:
            del connections[user_uuid]
