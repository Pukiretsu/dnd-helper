// player.js
const ws = new WebSocket("ws://" + window.location.host + "/ws");

ws.onopen = () => {
    // Obtenemos el token de la cookie o desde una variable inyectada (si cookie HttpOnly, pasa el token en template)
    // Aquí se asume que el token viene inyectado en el template en window.TOKEN
    const token = window.TOKEN || null;

    ws.send(JSON.stringify({
        type: "connect",
        role: "player",
        token: token
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === "characters_list") {
        renderCharacters(data.characters);
    } else if (data.type === "no_characters") {
        alert(data.message);
        // Aquí podrías mostrar un botón para crear personaje nuevo
        showCreateCharacterButton();
    } else if (data.error) {
        console.error("Error:", data.error);
        ws.close();
    }
};

ws.onclose = () => {
    console.log("WebSocket connection closed");
};

function renderCharacters(characters) {
    const list = document.getElementById("characters-list");
    list.innerHTML = "";
    characters.forEach(char => {
        const li = document.createElement("li");
        li.textContent = `ID: ${char.player_id} - Vida: ${char.state.vida} - Mana: ${char.state.mana} - dinero ${char.state.dinero}`;
        list.appendChild(li);
    });
}

function showCreateCharacterButton() {
    const btn = document.getElementById("create-character-btn");
    btn.style.display = "block";
    btn.onclick = () => {
        // Lógica para crear personaje nuevo vía fetch / API o WebSocket
        alert("Función crear personaje no implementada aún");
    };
}
