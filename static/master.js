// master.js
const ws = new WebSocket("ws://" + window.location.host + "/ws");

ws.onopen = () => {
    const token = window.TOKEN || null;
    ws.send(JSON.stringify({
        type: "connect",
        role: "master",
        token: token
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === "players_state") {
        renderPlayers(data.players);
    } else if (data.error) {
        console.error("Error:", data.error);
        ws.close();
    }
};

ws.onclose = () => {
    console.log("WebSocket connection closed");
};

function renderPlayers(players) {
    const container = document.getElementById("players-container");
    container.innerHTML = "";
    
    if (Object.keys(players).length === 0) {
    container.textContent = "No hay jugadores activos conectados.";
    }

    Object.entries(players).forEach(([player_id, state]) => {
        const div = document.createElement("div");
        div.textContent = `ID: ${player_id} - Vida: ${state.vida} - Mana: ${state.mana} - Dinero: ${state.dinero}`;
        container.appendChild(div);
    });
}
