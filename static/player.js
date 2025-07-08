
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    document.getElementById("player-info").textContent = JSON.stringify(data, null, 2);
};
