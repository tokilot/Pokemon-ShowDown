# main.py - FastAPI application for Pokemon Livestream

# Fix Windows console encoding for emoji support
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


import asyncio
import os
import random
import time
import traceback
import logging
from typing import List, Dict, Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pages import (
    create_battle_iframe,
    create_error_html,
    create_idle_html,
    ensure_static_assets,
    register_page_routes,
)

# --- Imports for poke_env and agents ---
from poke_env.player import Player
from poke_env import AccountConfiguration
# from poke_env.environment.battle import Battle

# Import the actual agent classes
from agents import OpenAIAgent
from utils import POKEMON_SETTINGS, ensure_last_action_file, write_last_action

# --- Configuration ---
# Use official Smogon Showdown server for battle visualization
from poke_env.ps_client.server_configuration import ShowdownServerConfiguration
custom_config = ShowdownServerConfiguration

# Battle iframe URL - use official Pokemon Showdown
CUSTOM_BATTLE_VIEW_URL_TEMPLATE = "https://play.pokemonshowdown.com/{battle_id}"
DEFAULT_BATTLE_FORMAT = POKEMON_SETTINGS.battle_format
LAST_ACTION_FILE = "last_action.txt" # --- ADDED FOR /last_action --- (Filename)
MATCHMAKING_MODE = POKEMON_SETTINGS.matchmaking_mode  # Supported: "accept", "ladder", "challenge"
CHALLENGE_TARGET_USERNAME: Optional[str] = POKEMON_SETTINGS.challenge_target_username
MATCHES_PER_ACTIVATION = POKEMON_SETTINGS.matches_per_activation

# Define available agents with their corresponding classes
# Credentials are loaded from `pokemon/yaml/config.yaml`.
AGENT_CONFIGS = {}
if POKEMON_SETTINGS.showdown_username and POKEMON_SETTINGS.showdown_password:
    AGENT_CONFIGS[POKEMON_SETTINGS.showdown_username] = {
        "class": OpenAIAgent,
        "password": POKEMON_SETTINGS.showdown_password,
    }

# Filter out agents with missing passwords
AVAILABLE_AGENT_NAMES = [
    name for name, cfg in AGENT_CONFIGS.items()
    if cfg.get("password", "")
]

if not AVAILABLE_AGENT_NAMES:
    print("FATAL ERROR: No valid Pokemon Showdown credentials were loaded. Exiting.")
    exit(1)

# --- Global State Variables ---
active_agent_name: Optional[str] = None
active_agent_instance: Optional[Player] = None
active_agent_task: Optional[asyncio.Task] = None
current_battle_instance = None
background_task_handle: Optional[asyncio.Task] = None

# --- Create FastAPI app ---
app = FastAPI(title="Pokemon Battle Livestream")

# --- Helper Functions ---
def get_active_battle(agent: Player):
    """Returns the first non-finished battle for an agent."""
    if agent and agent._battles:
        active_battles = [b for b in agent._battles.values() if not b.finished]
        if active_battles:
            # Ensure the battle object has a battle_tag before returning
            if hasattr(active_battles[0], 'battle_tag') and active_battles[0].battle_tag:
                 # Check if the battle_tag has the expected format (starts with 'battle-')
                if active_battles[0].battle_tag.startswith("battle-"):
                    return active_battles[0]
                else:
                    # This handles cases where the battle object might exist but tag isn't ready
                    # print(f"DEBUG: Found active battle for {agent.username} but tag '{active_battles[0].battle_tag}' not ready.")
                    return None
            else:
                # print(f"DEBUG: Found active battle for {agent.username} but it has no battle_tag attribute yet.")
                return None
    return None

def get_matchmaking_instruction(agent_name: str) -> str:
    """Returns UI text describing how the active agent is finding battles."""
    if MATCHMAKING_MODE == "accept":
        return f"Please challenge <strong>{agent_name}</strong> to a <strong>{DEFAULT_BATTLE_FORMAT}</strong> battle."
    if MATCHMAKING_MODE == "ladder":
        return f"Agent <strong>{agent_name}</strong> is searching the <strong>{DEFAULT_BATTLE_FORMAT}</strong> ladder for an opponent."
    if MATCHMAKING_MODE == "challenge":
        if CHALLENGE_TARGET_USERNAME:
            return f"Agent <strong>{agent_name}</strong> is challenging <strong>{CHALLENGE_TARGET_USERNAME}</strong> to a <strong>{DEFAULT_BATTLE_FORMAT}</strong> battle."
        return f"Agent <strong>{agent_name}</strong> is configured for challenge mode, but no target username is set."
    return f"Agent <strong>{agent_name}</strong> has an unknown matchmaking mode: <strong>{MATCHMAKING_MODE}</strong>."

def create_matchmaking_idle_html(agent_name: str) -> str:
    """Creates idle screen HTML tailored to the current matchmaking mode."""
    return create_idle_html(
        f"Agent Ready: <strong>{agent_name}</strong>",
        get_matchmaking_instruction(agent_name)
    )

def start_matchmaking_task(agent: Player, agent_name: str) -> asyncio.Task:
    """Starts the configured matchmaking behavior for the active agent."""
    if MATCHMAKING_MODE == "accept":
        return asyncio.create_task(
            agent.accept_challenges(None, MATCHES_PER_ACTIVATION),
            name=f"AcceptChallenge_{agent_name}"
        )
    if MATCHMAKING_MODE == "ladder":
        return asyncio.create_task(
            agent.ladder(MATCHES_PER_ACTIVATION),
            name=f"Ladder_{agent_name}"
        )
    if MATCHMAKING_MODE == "challenge":
        if not CHALLENGE_TARGET_USERNAME:
            raise ValueError("CHALLENGE_TARGET_USERNAME must be set when MATCHMAKING_MODE is 'challenge'.")
        return asyncio.create_task(
            agent.send_challenges(CHALLENGE_TARGET_USERNAME, n_challenges=MATCHES_PER_ACTIVATION),
            name=f"Challenge_{agent_name}"
        )
    raise ValueError(f"Unsupported MATCHMAKING_MODE: {MATCHMAKING_MODE}")

async def update_display_html(new_html_fragment: str) -> None:
    """Updates the current display HTML fragment and broadcasts to all clients."""
    # Pass the fragment directly
    await manager.update_all(new_html_fragment)
    print("HTML Display FRAGMENT UPDATED and broadcasted.")


# --- Agent Lifecycle Management ---
async def select_and_activate_new_agent():
    """Selects a random available agent, instantiates it, and starts its matchmaking task."""
    global active_agent_name, active_agent_instance, active_agent_task

    if not AVAILABLE_AGENT_NAMES:
        print("Lifecycle: No available agents with passwords set.")
        await update_display_html(create_error_html("No agents available. Check server logs/environment variables."))
        return False

    selected_name = random.choice(AVAILABLE_AGENT_NAMES)
    config = AGENT_CONFIGS[selected_name]
    AgentClass = config["class"]
    agent_password = config["password"]
    print(f"Lifecycle: Activating agent '{selected_name}'...")
    write_last_action(f"Activating agent: {selected_name}")
    # Use HTML tags for slight emphasis if desired
    await update_display_html(create_idle_html("Selecting Next Agent...", f"Preparing <strong>{selected_name}</strong>..."))

    try:
        account_config = AccountConfiguration(selected_name, agent_password)
        agent = AgentClass(
            account_configuration=account_config,
            server_configuration=custom_config,
            battle_format=DEFAULT_BATTLE_FORMAT,
            log_level=logging.WARNING,
            max_concurrent_battles=1
        )

        # Start the configured matchmaking task
        task = start_matchmaking_task(agent, selected_name)
        task.add_done_callback(log_task_exception) # Add callback for errors

        # Update global state
        active_agent_name = selected_name
        active_agent_instance = agent
        active_agent_task = task

        print(f"Lifecycle: Agent '{selected_name}' is active in matchmaking mode '{MATCHMAKING_MODE}'.")
        write_last_action(get_matchmaking_instruction(selected_name).replace("<strong>", "").replace("</strong>", ""))
        await update_display_html(create_matchmaking_idle_html(selected_name))
        return True

    except Exception as e:
        error_msg = f"Failed to activate agent '{selected_name}': {e}"
        print(error_msg)
        traceback.print_exc()
        await update_display_html(create_error_html(f"Error activating {selected_name}. Please wait or check logs."))

        # Clear state if activation failed
        active_agent_name = None
        active_agent_instance = None
        active_agent_task = None
        return False

async def check_for_new_battle():
    """Checks if the active agent has started a battle with a valid tag."""
    # --- FIX: Declare intention to use/modify global variables ---
    global active_agent_instance, current_battle_instance, active_agent_name, active_agent_task
    # -------------------------------------------------------------

    if active_agent_instance:
        battle = get_active_battle(active_agent_instance)
        # Check if battle exists AND has a valid battle_tag
        if battle and battle.battle_tag:
            # This line MODIFIES the global variable
            current_battle_instance = battle
            print(f"Lifecycle: Agent '{active_agent_name}' started battle: {battle.battle_tag}")
            write_last_action(f"Battle started: {battle.battle_tag}")

            # Stop the agent's matchmaking task once a battle starts
            if active_agent_task and not active_agent_task.done():
                print(f"Lifecycle: Cancelling matchmaking task for {active_agent_name} as battle started.")
                active_agent_task.cancel()
                # Optional: Wait briefly for cancellation confirmation, but don't block excessively
                # try:
                #     await asyncio.wait_for(active_agent_task, timeout=0.5)
                # except (asyncio.CancelledError, asyncio.TimeoutError):
                #     pass # Expected outcomes
        # else:
            # print(f"DEBUG: get_active_battle returned None or battle without tag.")

async def deactivate_current_agent(reason: str = "cycle"):
    """Cleans up the currently active agent and resets state."""
    global active_agent_name, active_agent_instance, active_agent_task, current_battle_instance

    agent_name_to_deactivate = active_agent_name # Store before clearing
    print(f"Lifecycle: Deactivating agent '{agent_name_to_deactivate}' (Reason: {reason})...")

    # Display appropriate intermediate message
    if reason == "battle_end":
        write_last_action("Battle finished")
        await update_display_html(create_idle_html("Battle Finished!", f"Agent <strong>{agent_name_to_deactivate}</strong> completed the match."))
    elif reason == "cycle":
        write_last_action(f"Cycling agent: {agent_name_to_deactivate}")
        await update_display_html(create_idle_html("Cycling Agents", f"Switching from <strong>{agent_name_to_deactivate}</strong>..."))
    elif reason == "forfeited_private_battle":
         write_last_action(f"Forfeited private battle: {agent_name_to_deactivate}")
         await update_display_html(create_idle_html("Switching Agent", f"Agent <strong>{agent_name_to_deactivate}</strong> forfeited a private battle."))
    else: # Generic reason or error
        write_last_action(f"Resetting agent: {agent_name_to_deactivate} ({reason})")
        await update_display_html(create_idle_html(f"Resetting Agent ({reason})", f"Cleaning up <strong>{agent_name_to_deactivate}</strong>..."))

    # Give users a moment to see the intermediate message
    await asyncio.sleep(3) # Adjust duration as needed

    # Show the "preparing next agent" message before lengthy cleanup
    await update_display_html(create_idle_html("Preparing Next Agent...", "Please wait..."))


    agent = active_agent_instance
    task = active_agent_task

    # Store a local copy of the battle instance before clearing it
    # last_battle_instance = current_battle_instance # Not strictly needed now

    # --- Crucial: Clear global state variables FIRST ---
    # This prevents race conditions where the lifecycle loop might try to
    # access the agent while it's being deactivated.
    active_agent_name = None
    active_agent_instance = None
    active_agent_task = None
    current_battle_instance = None
    print(f"Lifecycle: Global state cleared for '{agent_name_to_deactivate}'.")

    # --- Now perform cleanup actions ---
    # Cancel the matchmaking task if it's still running (it might already be done/cancelled)
    if task and not task.done():
        print(f"Lifecycle: Ensuring task cancellation for {agent_name_to_deactivate} ({task.get_name()})...")
        task.cancel()
        try:
            # Wait briefly for the task to acknowledge cancellation
            await asyncio.wait_for(task, timeout=2.0)
            print(f"Lifecycle: Task cancellation confirmed for {agent_name_to_deactivate}.")
        except asyncio.CancelledError:
             print(f"Lifecycle: Task cancellation confirmation (CancelledError) for {agent_name_to_deactivate}.")
        except asyncio.TimeoutError:
            print(f"Lifecycle: Task did not confirm cancellation within timeout for {agent_name_to_deactivate}.")
        except Exception as e:
            # Catch other potential errors during task cleanup
            print(f"Lifecycle: Error during task cancellation wait for {agent_name_to_deactivate}: {e}")

    # Disconnect the player (ensure agent object exists)
    if agent:
        print(f"Lifecycle: Disconnecting player {agent.username}...")
        try:
            # Check websocket state before attempting disconnection
            if hasattr(agent, '_websocket') and agent._websocket and agent._websocket.open:
                 await agent.disconnect()
                 print(f"Lifecycle: Player {agent.username} disconnected successfully.")
            else:
                 print(f"Lifecycle: Player {agent.username} already disconnected or websocket not available.")
        except Exception as e:
            # Log errors during disconnection but don't halt the process
            print(f"ERROR during agent disconnect ({agent.username}): {e}")
            traceback.print_exc() # Log full traceback for debugging

    # Add a brief delay AFTER deactivation before the loop potentially selects a new agent
    await asyncio.sleep(2) # Reduced from 3, adjust as needed
    print(f"Lifecycle: Agent '{agent_name_to_deactivate}' deactivation complete.")

async def manage_agent_lifecycle():
    """Runs the main loop selecting, running, and cleaning up agents sequentially."""
    # --- FIX: Declare intention to use global variables ---
    global active_agent_name, active_agent_instance, active_agent_task, current_battle_instance
    # ------------------------------------------------------

    print("Background lifecycle manager started.")
    REFRESH_INTERVAL_SECONDS = 3 # How often to check state when idle/in battle
    LOOP_COOLDOWN_SECONDS = 1 # Small delay at end of loop if no other waits occurred
    ERROR_RETRY_DELAY_SECONDS = 10 # Longer delay after errors
    POST_BATTLE_DELAY_SECONDS = 5 # Delay after a battle finishes before selecting next agent

    loop_counter = 0

    while True:
        loop_counter += 1
        loop_start_time = time.monotonic()
        print(f"\n--- Lifecycle Check #{loop_counter} [{time.strftime('%H:%M:%S')}] ---")

        try:
            # ==================================
            # State 1: No agent active
            # ==================================
            # Now Python knows active_agent_instance refers to the global one
            if active_agent_instance is None:
                print(f"[{loop_counter}] State 1: No active agent. Selecting...")
                activated = await select_and_activate_new_agent()
                if not activated:
                    print(f"[{loop_counter}] State 1: Activation failed. Waiting {ERROR_RETRY_DELAY_SECONDS}s before retry.")
                    await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
                else:
                    # Now Python knows active_agent_name refers to the global one set by select_and_activate_new_agent
                    print(f"[{loop_counter}] State 1: Agent '{active_agent_name}' activated successfully.")
                    # No sleep here, proceed to next check immediately if needed

            # ==================================
            # State 2: Agent is active
            # ==================================
            else:
                # Now Python knows active_agent_name refers to the global one
                agent_name = active_agent_name # Cache for logging
                print(f"[{loop_counter}] State 2: Agent '{agent_name}' is active.")

                # --- Sub-state: Check for new battle if none is tracked ---
                # Now Python knows current_battle_instance refers to the global one
                if current_battle_instance is None:
                    print(f"[{loop_counter}] State 2a: Checking for new battle for '{agent_name}'...")
                    await check_for_new_battle() # This updates global current_battle_instance if found

                    # Now Python knows current_battle_instance refers to the global one
                    if current_battle_instance:
                        battle_tag = current_battle_instance.battle_tag
                        print(f"[{loop_counter}] State 2a: *** NEW BATTLE DETECTED: {battle_tag} for '{agent_name}' ***")

                        # Check for non-public/suffixed format only in passive challenge mode.
                        # Ladder/challenge mode can legitimately create battle tags with suffixes.
                        parts = battle_tag.split('-')
                        is_suffixed_format = (
                            MATCHMAKING_MODE == "accept"
                            and len(parts) > 3
                            and parts[2].isdigit()
                        )

                        if is_suffixed_format:
                            # Forfeit immediately if it looks like a private/suffixed battle ID
                            print(f"[{loop_counter}] Detected potentially non-public battle format ({battle_tag}). Forfeiting.")
                            # Don't update display yet, do it before deactivation
                            try:
                                # Now Python knows active_agent_instance refers to the global one
                                if active_agent_instance: # Ensure agent still exists
                                    await active_agent_instance.forfeit(battle_tag)
                                    # await active_agent_instance.send_message("/forfeit", battle_tag) # Alternative
                                    print(f"[{loop_counter}] Sent forfeit command for {battle_tag}.")
                                    await asyncio.sleep(1.5) # Give forfeit time to register
                            except Exception as forfeit_err:
                                print(f"[{loop_counter}] ERROR sending forfeit for {battle_tag}: {forfeit_err}")
                            # Deactivate agent after forfeit attempt
                            await deactivate_current_agent(reason="forfeited_private_battle")
                            continue # Skip rest of the loop for this iteration

                        else:
                            # Public battle format - display the iframe
                            print(f"[{loop_counter}] Public battle format detected. Displaying battle {battle_tag}.")
                            await update_display_html(create_battle_iframe(battle_tag))
                            # Now fall through to monitor this battle in the next section

                    else:
                        # No new battle found, agent remains idle
                        print(f"[{loop_counter}] State 2a: No new battle found. Agent '{agent_name}' remains idle in matchmaking mode '{MATCHMAKING_MODE}'.")
                        write_last_action(get_matchmaking_instruction(agent_name).replace("<strong>", "").replace("</strong>", ""))
                        # Periodically refresh idle screen to ensure consistency
                        idle_html = create_matchmaking_idle_html(agent_name)
                        await update_display_html(idle_html)
                        await asyncio.sleep(REFRESH_INTERVAL_SECONDS) # Wait before next check if idle


                # --- Sub-state: Monitor ongoing battle ---
                 # Now Python knows current_battle_instance refers to the global one
                if current_battle_instance is not None:
                    battle_tag = current_battle_instance.battle_tag
                    print(f"[{loop_counter}] State 2b: Monitoring battle {battle_tag} for '{agent_name}'")

                    # Ensure agent instance still exists before accessing its battles
                    # Now Python knows active_agent_instance refers to the global one
                    if not active_agent_instance:
                        print(f"[{loop_counter}] WARNING: Agent instance for '{agent_name}' disappeared while monitoring battle {battle_tag}! Deactivating.")
                        await deactivate_current_agent(reason="agent_disappeared_mid_battle")
                        continue

                    # Get potentially updated battle object directly from agent's state
                    # Use .get() for safety
                    # Now Python knows active_agent_instance refers to the global one
                    battle_obj = active_agent_instance._battles.get(battle_tag)

                    if battle_obj and battle_obj.finished:
                        print(f"[{loop_counter}] Battle {battle_tag} is FINISHED. Deactivating agent '{agent_name}'.")
                        await deactivate_current_agent(reason="battle_end")
                        print(f"[{loop_counter}] Waiting {POST_BATTLE_DELAY_SECONDS}s post-battle before selecting next agent.")
                        await asyncio.sleep(POST_BATTLE_DELAY_SECONDS)
                        continue # Start next loop iteration to select new agent

                    elif not battle_obj:
                        # This can happen briefly during transitions or if battle ends unexpectedly
                        print(f"[{loop_counter}] WARNING: Battle object for {battle_tag} not found in agent's list for '{agent_name}'. Battle might have ended abruptly. Deactivating.")
                        await deactivate_current_agent(reason="battle_object_missing")
                        continue

                    else:
                        # Battle is ongoing, battle object exists, iframe should be displayed
                        print(f"[{loop_counter}] Battle {battle_tag} ongoing for '{agent_name}'.")
                        # Optionally: Could re-send iframe HTML periodically if needed, but usually not necessary
                        # await update_display_html(create_battle_iframe(battle_tag))
                        await asyncio.sleep(REFRESH_INTERVAL_SECONDS) # Wait before next check

        # --- Global Exception Handling for the main loop ---
        except asyncio.CancelledError:
            print("Lifecycle manager task cancelled.")
            raise # Re-raise to ensure proper shutdown
        except Exception as e:
            print(f"!!! ERROR in main lifecycle loop #{loop_counter}: {e} !!!")
            traceback.print_exc()
             # Now Python knows active_agent_name refers to the global one
            current_agent_name = active_agent_name # Cache name before deactivation attempts
            # Now Python knows active_agent_instance refers to the global one
            if active_agent_instance:
                print(f"Attempting to deactivate agent '{current_agent_name}' due to loop error...")
                try:
                    await deactivate_current_agent(reason="main_loop_error")
                except Exception as deactivation_err:
                    print(f"Error during error-handling deactivation: {deactivation_err}")
                    # Ensure state is cleared even if deactivation fails partially
                    active_agent_name = None
                    active_agent_instance = None
                    active_agent_task = None
                    current_battle_instance = None
            else:
                # Error happened potentially before agent activation or after clean deactivation
                print("No active agent instance during loop error.")
                # Show a generic error on the frontend
                write_last_action(f"Lifecycle manager error: {e}")
                await update_display_html(create_error_html(f"A server error occurred in the lifecycle manager. Please wait. ({e})"))

            # Wait longer after a major error before trying again
            print(f"Waiting {ERROR_RETRY_DELAY_SECONDS}s after loop error.")
            await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
            continue # Go to next loop iteration after error handling

        # --- Delay at end of loop if no other significant waits happened ---
        elapsed_time = time.monotonic() - loop_start_time
        if elapsed_time < LOOP_COOLDOWN_SECONDS:
             await asyncio.sleep(LOOP_COOLDOWN_SECONDS - elapsed_time)

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
register_page_routes(app, LAST_ACTION_FILE)

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


@app.on_event("startup")
async def startup_event():
    """Start background tasks when the application starts."""
    global background_task_handle

    # Mount static files directory (make sure 'static' folder exists)
    # Place your 'pokemon_huggingface.png' inside this 'static' folder
    static_dir = "static"
    ensure_static_assets(app, static_dir)

    # --- ADDED FOR /last_action --- Check if last_action.txt exists ---
    if not os.path.exists(LAST_ACTION_FILE):
        print(f"WARN: '{LAST_ACTION_FILE}' not found. Creating an empty file.")
    ensure_last_action_file()
    # --- END ADDED SECTION ---

    print("🚀 Starting background tasks")
    write_last_action("Starting background tasks")
    # Start the main lifecycle manager task
    background_task_handle = asyncio.create_task(manage_agent_lifecycle(), name="LifecycleManager")
    # Add the exception logging callback
    background_task_handle.add_done_callback(log_task_exception)
    print("✅ Background tasks started")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up tasks when shutting down."""
    global background_task_handle, active_agent_instance

    print("\n🔌 Shutting down application. Cleaning up...")

    # 1. Cancel the main lifecycle manager task
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

    # 2. Deactivate and disconnect any currently active agent
    #    Use a copy of the instance in case it gets cleared elsewhere during shutdown.
    agent_to_disconnect = active_agent_instance
    if agent_to_disconnect:
        agent_name = agent_to_disconnect.username if hasattr(agent_to_disconnect, 'username') else 'Unknown Agent'
        print(f"Disconnecting active agent '{agent_name}'...")
        try:
             # Check websocket status before disconnecting
             if hasattr(agent_to_disconnect, '_websocket') and agent_to_disconnect._websocket and agent_to_disconnect._websocket.open:
                 await agent_to_disconnect.disconnect()
                 print(f"Agent '{agent_name}' disconnected.")
             else:
                 print(f"Agent '{agent_name}' already disconnected or websocket not available.")
        except Exception as e:
            print(f"Error during agent disconnect on shutdown for '{agent_name}': {e}")

    # 3. Close all active WebSocket connections cleanly
    print(f"Closing {len(manager.active_connections)} client WebSocket connections...")
    # Create tasks to close all connections concurrently
    close_tasks = [
         conn.close(code=1000, reason="Server shutting down") # 1000 = Normal Closure
         for conn in list(manager.active_connections) # Iterate over a copy
    ]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True) # Log potential errors during close

    print("✅ Cleanup complete. Application shutdown.")


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
    logging.getLogger('websockets.client').setLevel(logging.WARNING) # Show websocket connection attempts

    print("Starting Pokemon Battle Livestream Server...")
    print("="*60)

    if not AVAILABLE_AGENT_NAMES:
        print("█████████████████████ FATAL ERROR █████████████████████")
        print(" No agents found with configured passwords!")
        print(" Please configure Pokemon Showdown credentials in pokemon/yaml/config.yaml")
        print("="*60)
        exit("Exiting due to missing agent passwords.")
    else:
        print("✨ Available Agents Found:")
        for name in AVAILABLE_AGENT_NAMES:
            print(f"  - {name}")
    print("="*60)
    print("Server will run on http://0.0.0.0:6007")
    print("Last action log available at http://0.0.0.0:6007/last_action") # --- ADDED INFO ---
    print("="*60)

    # Run with uvicorn
    uvicorn.run(
        "main:app", # Point to the FastAPI app instance
        host="0.0.0.0",
        port=6007,
        reload=False, # Disable reload for production/stable testing
        log_level="info" # Uvicorn's log level
    )
