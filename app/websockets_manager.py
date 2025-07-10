import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect

from config import TAGS
from auth import verify_token
from database import load_player_state_by_id, save_player_state, check_player_ownership # Import check_player_ownership

# Stores active WebSocket connections: {user_uuid: {"ws": WebSocket, "role": "player/master", "selected_player_id": "..."}}
connections = {}

async def get_active_players_state():
    """
    Retrieves the state of players who are currently connected via WebSocket
    and have a character selected.
    Returns:
        dict: A dictionary where keys are player_ids and values are their states.
    """
    active_players_data = {}
    for user_uuid, conn_info in connections.items():
        if conn_info["role"] == "player" and "selected_player_id" in conn_info:
            selected_player_id: str = conn_info["selected_player_id"] # Ensure type is str
            player_state = await load_player_state_by_id(selected_player_id)
            if player_state:
                active_players_data[selected_player_id] = player_state
    return active_players_data

async def broadcast_active_players():
    """
    Periodically broadcasts the state of active players to all connected masters.
    """
    while True:
        active_players = await get_active_players_state()
        # print(f"Active players for broadcast: {active_players}") # Uncomment for verbose logging
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
                
        await asyncio.sleep(5) # Broadcast every 5 seconds

async def handle_websocket_connection(websocket: WebSocket):
    """Handles individual WebSocket connections."""
    await websocket.accept()
    user_uuid: str | None = None # Explicitly type as str or None
    selected_player_id: str | None = None # Explicitly type as str or None

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
                if not isinstance(player_id_from_data, str) or not player_id_from_data: # Ensure it's a non-empty string
                    await websocket.send_json({"error": "No player_id provided or invalid type for player connection."})
                    await websocket.close()
                    print(f"{TAGS['websocket']} Player connection rejected: No player_id or invalid type.")
                    return

                selected_player_id = player_id_from_data # Now selected_player_id is definitely a str

                # Verify that the selected_player_id belongs to this user_uuid
                if not await check_player_ownership(selected_player_id, user_uuid):
                    await websocket.send_json({"error": "Player ID does not belong to this user."})
                    await websocket.close()
                    print(f"{TAGS['websocket']} Player connection rejected: Unauthorized player_id.")
                    return

                connections[user_uuid] = {"ws": websocket, "role": "player", "selected_player_id": selected_player_id}
                print(f"{TAGS['websocket']} connected: {user_uuid} as: player with character: {selected_player_id}")
                
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
            print(f"{TAGS['websocket']} Received data from {user_uuid} (Role: {connections.get(user_uuid, {}).get('role')}): {received_data}")

            # Handle player state updates
            if connections.get(user_uuid, {}).get("role") == "player" and received_data.get("type") == "player_update":
                # Retrieve the selected_player_id from the connections dict, which is guaranteed to be a string here
                current_selected_player_id_from_conn: str = connections[user_uuid]["selected_player_id"]

                # Ensure the update is for the currently selected character
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
                else:
                    print(f"{TAGS['app_error']} Unauthorized update attempt: player_id mismatch for {user_uuid}")
            # Add more logic here for other message types (e.g., master commands)
            
    except WebSocketDisconnect:
        if user_uuid and user_uuid in connections:
            role = connections[user_uuid]["role"]
            del connections[user_uuid]
            print(f"{TAGS['websocket']} Disconnected: {user_uuid} (Role: {role})")
    except Exception as e:
        print(f"{TAGS['app_error']} WebSocket error for {user_uuid}: {e}")
        if user_uuid and user_uuid in connections:
            del connections[user_uuid]
