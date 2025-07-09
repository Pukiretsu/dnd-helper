// WebSocket logic for player updates (if needed)
const token = "{{ token }}"; // Get token from Jinja2 context
const ws = new WebSocket("ws://" + window.location.host + "/ws");

ws.onopen = (event) => {
    console.log("WebSocket connected!");
    ws.send(JSON.stringify({ type: "connect", token: token, role: "player" }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log("Received:", data);
    if (data.type === "characters_list") {
        // Update UI with the list of characters
        // You might want to dynamically update the character-section here
        console.log("Characters received:", data.characters);
        // For a full dynamic update, you would clear and re-render the list
        // For simplicity, this example just logs it.
        // A full implementation would involve more complex DOM manipulation.
    } else if (data.type === "no_characters") {
        console.log(data.message);
    }
    // Handle other message types (e.g., updates to a specific character's state)
};

ws.onclose = (event) => {
    console.log("WebSocket disconnected.");
};

ws.onerror = (error) => {
    console.error("WebSocket error:", error);
};

// Example of sending a player update (you'd trigger this from a form/button)
// function sendPlayerUpdate(characterId, newState) {
//     ws.send(JSON.stringify({
//         type: "player_update",
//         player_id: characterId,
//         state: newState
//     }));
// }