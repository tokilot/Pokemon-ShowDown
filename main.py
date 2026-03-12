# main.py - FastAPI application for Pokemon Livestream

# Fix Windows console encoding for emoji support
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


import asyncio
import traceback
import logging
from contextlib import asynccontextmanager
from typing import Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pages import (
    create_idle_html,
    ensure_static_assets,
    register_page_routes,
)

from utils import ensure_battle_history_file
from lifecycle import (
    AVAILABLE_AGENT_NAMES,
    LifecycleState,
    manage_agent_lifecycle,
)

if not AVAILABLE_AGENT_NAMES:
    print("FATAL ERROR: No valid Pokemon Showdown credentials were loaded. Exiting.")
    exit(1)

lifecycle_state = LifecycleState()
background_task_handle: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    global background_task_handle

    static_dir = "static"
    ensure_static_assets(app, static_dir)
    ensure_battle_history_file()

    print("🚀 Starting background tasks")
    background_task_handle = asyncio.create_task(
        manage_agent_lifecycle(lifecycle_state, update_display_html, log_task_exception),
        name="LifecycleManager"
    )
    background_task_handle.add_done_callback(log_task_exception)
    print("✅ Background tasks started")

    try:
        yield
    finally:
        print("\n🔌 Shutting down application. Cleaning up...")

        if background_task_handle and not background_task_handle.done():
            print("Cancelling background task...")
            background_task_handle.cancel()
            try:
                await asyncio.wait_for(background_task_handle, timeout=5.0)
                print("Background task cancelled successfully.")
            except asyncio.CancelledError:
                print("Background task cancellation confirmed (CancelledError).")
            except asyncio.TimeoutError:
                print("Background task did not finish cancelling within timeout.")
            except Exception as e:
                print(f"Error during background task cancellation: {e}")

        agent_to_disconnect = lifecycle_state.active_agent_instance
        if agent_to_disconnect:
            agent_name = agent_to_disconnect.username if hasattr(agent_to_disconnect, 'username') else 'Unknown Agent'
            print(f"Disconnecting active agent '{agent_name}'...")
            try:
                if hasattr(agent_to_disconnect, '_websocket') and agent_to_disconnect._websocket and agent_to_disconnect._websocket.open:
                    await agent_to_disconnect.disconnect()
                    print(f"Agent '{agent_name}' disconnected.")
                else:
                    print(f"Agent '{agent_name}' already disconnected or websocket not available.")
            except Exception as e:
                print(f"Error during agent disconnect on shutdown for '{agent_name}': {e}")
            finally:
                lifecycle_state.clear_all()

        print(f"Closing {len(manager.active_connections)} client WebSocket connections...")
        close_tasks = [
            conn.close(code=1000, reason="Server shutting down")
            for conn in list(manager.active_connections)
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        print("✅ Cleanup complete. Application shutdown.")

# --- Create FastAPI app ---
app = FastAPI(title="Pokemon Battle Livestream", lifespan=lifespan)

async def update_display_html(new_html_fragment: str) -> None:
    """Updates the current display HTML fragment and broadcasts to all clients."""
    # Pass the fragment directly
    await manager.update_all(new_html_fragment)
    print("HTML Display FRAGMENT UPDATED and broadcasted.")

def log_task_exception(task: asyncio.Task):
    """Callback to log exceptions from background tasks (like accept_challenges)."""
    try:
        if task.cancelled():
            # Don't log cancellation as an error, it's often expected
            print(f"Task '{task.get_name()}' was cancelled.")
            return
        # Accessing result will raise exception if task failed
        task.result()
        print(f"Task '{task.get_name()}' completed successfully.")
    except asyncio.CancelledError:
         print(f"Task '{task.get_name()}' confirmed cancelled (exception caught).")
         pass # Expected
    except Exception as e:
        # Log actual errors
        print(f"!!! Exception in background task '{task.get_name()}': {e} !!!")
        traceback.print_exc()
        # Optionally: Trigger some recovery or notification here if needed


# --- WebSocket connection manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        # Initialize with the idle HTML fragment
        self.current_html_fragment: str = create_idle_html("Initializing...", "Setting up Pokémon Battle Stream")

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        print(f"Client connected. Sending current state. Total clients: {len(self.active_connections)}")
        # Send current state (HTML fragment) to newly connected client
        try:
            await websocket.send_text(self.current_html_fragment)
        except Exception as e:
             print(f"Error sending initial state to new client: {e}")
             # Consider removing the connection if initial send fails
             await self.disconnect(websocket)


    async def disconnect(self, websocket: WebSocket):
        # Use discard() to safely remove even if not present
        self.active_connections.discard(websocket)
        print(f"Client disconnected. Total clients: {len(self.active_connections)}")

    async def update_all(self, html_fragment: str):
        """Update the current HTML fragment and broadcast to all clients."""
        if self.current_html_fragment == html_fragment:
             # print("Skipping broadcast, HTML fragment unchanged.")
             return # Avoid unnecessary updates if content is identical

        self.current_html_fragment = html_fragment
        if not self.active_connections:
             # print("No active connections to broadcast update to.")
             return

        print(f"Broadcasting update to {len(self.active_connections)} clients...")

        # Create a list of tasks to send updates concurrently
        # Make a copy of the set for safe iteration during potential disconnects
        send_tasks = [
             connection.send_text(html_fragment)
             for connection in list(self.active_connections) # Iterate over a copy
        ]

        # Use asyncio.gather to send to all clients, collecting results/exceptions
        results = await asyncio.gather(*send_tasks, return_exceptions=True)

        # Handle potential errors during broadcast (e.g., client disconnected abruptly)
        # Iterate over connections again, checking results
        connections_to_remove = set()
        for i, result in enumerate(results):
            connection = list(self.active_connections)[i] # Assumes order is maintained
            if isinstance(result, Exception):
                print(f"Error sending update to client: {result}. Marking for removal.")
                connections_to_remove.add(connection)

        # Disconnect clients that failed
        for connection in connections_to_remove:
            await self.disconnect(connection)


manager = ConnectionManager()
register_page_routes(app)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive. Client doesn't send messages in this setup.
            # FastAPI's WebSocket implementation handles ping/pong internally usually.
            # If needed, you could implement explicit keepalive here.
            data = await websocket.receive_text()
            # We don't expect messages from the client in this design,
            # but log if received for debugging.
            print(f"Received unexpected message from client: {data}")
            # Or simply keep listening:
            # await asyncio.sleep(60) # Example keepalive interval if needed
    except WebSocketDisconnect as e:
         print(f"WebSocket disconnected: Code {e.code}, Reason: {getattr(e, 'reason', 'N/A')}")
         await manager.disconnect(websocket) # Use await here
    except Exception as e:
        # Catch other potential errors on the connection
        print(f"WebSocket error: {e}")
        traceback.print_exc()
        await manager.disconnect(websocket) # Ensure disconnect on error

# For direct script execution
if __name__ == "__main__":
    import uvicorn

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # Reduce noise from poke_env's default INFO logging if desired
    logging.getLogger('poke_env').setLevel(logging.WARNING)
    logging.getLogger('websockets.client').setLevel(logging.INFO) # Show websocket connection attempts

    print("Starting Pokemon Battle Livestream Server...")
    print("="*60)

    available_agent_names = AVAILABLE_AGENT_NAMES
    if not available_agent_names:
        print("█████████████████████ FATAL ERROR █████████████████████")
        print(" No agents found with configured passwords!")
        print(" Please configure Pokemon Showdown credentials in pokemon/yaml/config.yaml")
        print("="*60)
        exit("Exiting due to missing agent passwords.")
    else:
        print("✨ Available Agents Found:")
        for name in available_agent_names:
            print(f"  - {name}")
    print("="*60)
    print("Server will run on http://0.0.0.0:6007")
    print("="*60)

    # Run with uvicorn
    uvicorn.run(
        "main:app", # Point to the FastAPI app instance
        host="0.0.0.0",
        port=6007,
        reload=False, # Disable reload for production/stable testing
        log_level="info" # Uvicorn's log level
    )
