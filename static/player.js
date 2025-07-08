const urlParams = new URLSearchParams(window.location.search);
const username = urlParams.get("username");
const ws = new WebSocket(`ws://${location.host}/ws?username=${username}`);

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    document.getElementById("player-info").textContent = JSON.stringify(data, null, 2);
};
