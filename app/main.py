# main.py

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, Request # Import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates # Import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException # Import StarletteHTTPException

from config import TAGS
from database import init_db
from websockets_manager import broadcast_active_players, handle_websocket_connection
from routes import router as http_router # Import the APIRouter instance

print(">>>servidor corriendo<<<<")

# Initialize Jinja2Templates here as it's used by the exception handler
templates = Jinja2Templates(directory="templates")

## FastAPI Application Setup

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for application startup and shutdown events.
    Initializes the database and starts the active players broadcast task.
    """
    print(f"{TAGS['server']} Application startup event: Initializing database...")
    await init_db()
    # Start the background task for broadcasting active players
    asyncio.create_task(broadcast_active_players())
    yield
    # Code here runs on application shutdown (e.g., closing database connections)
    print(f"{TAGS['server']} Application shutdown event: Performing cleanup...")

# Initialize FastAPI app with the new lifespan handler
app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include the HTTP router
app.include_router(http_router)

# Fallback exception handler for 404 and other HTTP exceptions
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Custom handler for HTTP exceptions, rendering specific error pages."""
    if exc.status_code == 404:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    else:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

## Websockets

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handles WebSocket connections for player and master state synchronization."""
    await handle_websocket_connection(websocket)

if __name__ == "__main__":
    # Run the FastAPI application using Uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
