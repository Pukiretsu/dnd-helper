// WebSocket logic for in-game updates (player side)
let ws;
const token = "{{ token }}";
const selectedPlayerId = "{{ character.player_id }}";
const currentLobbyId = "{{ lobby_id }}";

function connectWebSocket() {
    ws = new WebSocket(`ws://localhost:8000/ws`);

    ws.onopen = (event) => {
        console.log("WebSocket connected (in-game player)!");
        ws.send(JSON.stringify({
            type: "connect",
            token: token,
            role: "player",
            player_id: selectedPlayerId,
            lobby_id: currentLobbyId // Send current lobby ID on connect
        }));
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log("Received (in-game player):", data);
        // Handle in-game specific messages from master or other players
        // e.g., updates to character stats, game events, etc.
        if (data.type === "player_state_update" && data.player_id === selectedPlayerId) {
            // Update UI with new character state
            console.log("My character state updated:", data.state);
            // You would update specific DOM elements here, e.g.:
            // document.getElementById('vida-span').textContent = data.state.vida;
        }
        // If master ends game or kicks player, redirect back to lobby
        // if (data.type === "game_ended" || data.type === "kicked") {
        //     alert("La partida ha terminado o has sido expulsado. Redirigiendo al lobby.");
        //     window.location.href = "/lobby";
        // }
    };

    ws.onclose = (event) => {
        console.log("WebSocket disconnected (in-game player). Reconnecting in 5 seconds...");
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = (error) => {
        console.error("WebSocket error (in-game player):", error);
        ws.close();
    };
}

window.onload = connectWebSocket;

// Example: Function to send a character update from player side
// function sendCharacterUpdate(newHealth) {
//     if (ws && ws.readyState === WebSocket.OPEN) {
//         ws.send(JSON.stringify({
//             type: "player_update",
//             player_id: selectedPlayerId,
//             state: { ...character_data_from_jinja, vida: newHealth } // Assuming you have character data
//         }));
//     }
// }