import asyncio
import logging
import random
import time
import traceback
from typing import Any, Callable, Optional

from poke_env import AccountConfiguration
from poke_env.player import Player
from poke_env.ps_client.server_configuration import ShowdownServerConfiguration

from agents import OpenAIAgent
from pages import create_battle_redirect_html, create_error_html, create_idle_html
from utils import POKEMON_SETTINGS, append_battle_history_record

custom_config = ShowdownServerConfiguration
DEFAULT_BATTLE_FORMAT = POKEMON_SETTINGS.battle_format
MATCHMAKING_MODE = POKEMON_SETTINGS.matchmaking_mode
CHALLENGE_TARGET_USERNAME: Optional[str] = POKEMON_SETTINGS.challenge_target_username
MATCHES_PER_ACTIVATION = POKEMON_SETTINGS.matches_per_activation

AGENT_CONFIGS = {}
if POKEMON_SETTINGS.showdown_username and POKEMON_SETTINGS.showdown_password:
    AGENT_CONFIGS[POKEMON_SETTINGS.showdown_username] = {
        "class": OpenAIAgent,
        "password": POKEMON_SETTINGS.showdown_password,
    }

AVAILABLE_AGENT_NAMES = [
    name for name, cfg in AGENT_CONFIGS.items()
    if cfg.get("password", "")
]


class LifecycleState:
    def __init__(self) -> None:
        self.active_agent_name: Optional[str] = None
        self.active_agent_instance: Optional[Player] = None
        self.active_agent_task: Optional[asyncio.Task] = None
        self.current_battle_instance: Any = None

    def has_active_agent(self) -> bool:
        return self.active_agent_instance is not None

    def has_current_battle(self) -> bool:
        return self.current_battle_instance is not None

    def set_active_agent(self, agent_name: str, agent: Player, task: asyncio.Task) -> None:
        self.active_agent_name = agent_name
        self.active_agent_instance = agent
        self.active_agent_task = task

    def set_current_battle(self, battle: Any) -> None:
        self.current_battle_instance = battle

    def clear_battle(self) -> None:
        self.current_battle_instance = None

    def clear_all(self) -> None:
        self.active_agent_name = None
        self.active_agent_instance = None
        self.active_agent_task = None
        self.current_battle_instance = None


def get_active_battle(agent: Player):
    """Returns the first non-finished battle for an agent."""
    if agent and agent._battles:
        active_battles = [b for b in agent._battles.values() if not b.finished]
        if active_battles:
            if hasattr(active_battles[0], "battle_tag") and active_battles[0].battle_tag:
                if active_battles[0].battle_tag.startswith("battle-"):
                    return active_battles[0]
                return None
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
    return create_idle_html(
        f"Agent Ready: <strong>{agent_name}</strong>",
        get_matchmaking_instruction(agent_name),
    )


def start_matchmaking_task(agent: Player, agent_name: str) -> asyncio.Task:
    """Starts the configured matchmaking behavior for the active agent."""
    if MATCHMAKING_MODE == "accept":
        return asyncio.create_task(
            agent.accept_challenges(None, MATCHES_PER_ACTIVATION),
            name=f"AcceptChallenge_{agent_name}",
        )
    if MATCHMAKING_MODE == "ladder":
        return asyncio.create_task(
            agent.ladder(MATCHES_PER_ACTIVATION),
            name=f"Ladder_{agent_name}",
        )
    if MATCHMAKING_MODE == "challenge":
        if not CHALLENGE_TARGET_USERNAME:
            raise ValueError("CHALLENGE_TARGET_USERNAME must be set when MATCHMAKING_MODE is 'challenge'.")
        return asyncio.create_task(
            agent.send_challenges(CHALLENGE_TARGET_USERNAME, n_challenges=MATCHES_PER_ACTIVATION),
            name=f"Challenge_{agent_name}",
        )
    raise ValueError(f"Unsupported MATCHMAKING_MODE: {MATCHMAKING_MODE}")


async def select_and_activate_new_agent(
    state: LifecycleState,
    update_display_html: Callable[[str], Any],
    log_task_exception: Callable[[asyncio.Task], None],
) -> bool:
    """Selects a random available agent, instantiates it, and starts its matchmaking task."""
    if not AVAILABLE_AGENT_NAMES:
        print("Lifecycle: No available agents with passwords set.")
        await update_display_html(create_error_html("No agents available. Check server logs/environment variables."))
        return False

    selected_name = random.choice(AVAILABLE_AGENT_NAMES)
    config = AGENT_CONFIGS[selected_name]
    agent_class = config["class"]
    agent_password = config["password"]
    print(f"Lifecycle: Activating agent '{selected_name}'...")
    await update_display_html(create_idle_html("Selecting Next Agent...", f"Preparing <strong>{selected_name}</strong>..."))

    try:
        account_config = AccountConfiguration(selected_name, agent_password)
        agent = agent_class(
            account_configuration=account_config,
            server_configuration=custom_config,
            battle_format=DEFAULT_BATTLE_FORMAT,
            log_level=logging.WARNING,
            max_concurrent_battles=1,
        )

        task = start_matchmaking_task(agent, selected_name)
        task.add_done_callback(log_task_exception)

        state.set_active_agent(selected_name, agent, task)

        print(f"Lifecycle: Agent '{selected_name}' is active in matchmaking mode '{MATCHMAKING_MODE}'.")
        await update_display_html(create_matchmaking_idle_html(selected_name))
        return True
    except Exception as e:
        error_msg = f"Failed to activate agent '{selected_name}': {e}"
        print(error_msg)
        traceback.print_exc()
        await update_display_html(create_error_html(f"Error activating {selected_name}. Please wait or check logs."))
        state.clear_all()
        return False


async def check_for_new_battle(state: LifecycleState) -> None:
    """Checks if the active agent has started a battle with a valid tag."""
    if state.active_agent_instance:
        battle = get_active_battle(state.active_agent_instance)
        if battle and battle.battle_tag:
            state.set_current_battle(battle)
            print(f"Lifecycle: Agent '{state.active_agent_name}' started battle: {battle.battle_tag}")

            if state.active_agent_task and not state.active_agent_task.done():
                print(f"Lifecycle: Cancelling matchmaking task for {state.active_agent_name} as battle started.")
                state.active_agent_task.cancel()
                # Optional: Wait briefly for cancellation confirmation, but don't block excessively
                # try:
                #     await asyncio.wait_for(state.active_agent_task, timeout=0.5)
                # except (asyncio.CancelledError, asyncio.TimeoutError):
                #     pass # Expected outcomes


async def deactivate_current_agent(state: LifecycleState, reason: str, update_display_html: Callable[[str], Any]) -> None:
    """Cleans up the currently active agent and resets state."""
    agent_name_to_deactivate = state.active_agent_name
    print(f"Lifecycle: Deactivating agent '{agent_name_to_deactivate}' (Reason: {reason})...")

    if reason == "battle_end":
        await update_display_html(create_idle_html("Battle Finished!", f"Agent <strong>{agent_name_to_deactivate}</strong> completed the match."))
    elif reason == "cycle":
        await update_display_html(create_idle_html("Cycling Agents", f"Switching from <strong>{agent_name_to_deactivate}</strong>..."))
    elif reason == "forfeited_private_battle":
        await update_display_html(create_idle_html("Switching Agent", f"Agent <strong>{agent_name_to_deactivate}</strong> forfeited a private battle."))
    else:
        await update_display_html(create_idle_html(f"Resetting Agent ({reason})", f"Cleaning up <strong>{agent_name_to_deactivate}</strong>..."))

    await asyncio.sleep(3)
    await update_display_html(create_idle_html("Preparing Next Agent...", "Please wait..."))

    agent = state.active_agent_instance
    task = state.active_agent_task

    if reason == "battle_end" and agent and state.current_battle_instance:
        battle = state.current_battle_instance
        battle_tag = getattr(battle, "battle_tag", "unknown-battle")
        battle_result = "finished"
        if hasattr(battle, "won") and battle.won is True:
            battle_result = "win"
        elif hasattr(battle, "lost") and battle.lost is True:
            battle_result = "loss"

        history_entries = getattr(agent, "battle_history", [])
        append_battle_history_record(
            {
                "battle_tag": battle_tag,
                "result": battle_result,
                "turns": history_entries,
            }
        )
        if hasattr(agent, "battle_history"):
            agent.battle_history = []

    state.clear_all()
    print(f"Lifecycle: Global state cleared for '{agent_name_to_deactivate}'.")

    if task and not task.done():
        print(f"Lifecycle: Ensuring task cancellation for {agent_name_to_deactivate} ({task.get_name()})...")
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
            print(f"Lifecycle: Task cancellation confirmed for {agent_name_to_deactivate}.")
        except asyncio.CancelledError:
            print(f"Lifecycle: Task cancellation confirmation (CancelledError) for {agent_name_to_deactivate}.")
        except asyncio.TimeoutError:
            print(f"Lifecycle: Task did not confirm cancellation within timeout for {agent_name_to_deactivate}.")
        except Exception as e:
            print(f"Lifecycle: Error during task cancellation wait for {agent_name_to_deactivate}: {e}")

    if agent:
        print(f"Lifecycle: Disconnecting player {agent.username}...")
        try:
            if hasattr(agent, "_websocket") and agent._websocket and agent._websocket.open:
                await agent.disconnect()
                print(f"Lifecycle: Player {agent.username} disconnected successfully.")
            else:
                print(f"Lifecycle: Player {agent.username} already disconnected or websocket not available.")
        except Exception as e:
            print(f"ERROR during agent disconnect ({agent.username}): {e}")
            traceback.print_exc()

    await asyncio.sleep(2)
    print(f"Lifecycle: Agent '{agent_name_to_deactivate}' deactivation complete.")


async def manage_agent_lifecycle(
    state: LifecycleState,
    update_display_html: Callable[[str], Any],
    log_task_exception: Callable[[asyncio.Task], None],
) -> None:
    """Runs the main loop selecting, running, and cleaning up agents sequentially."""
    print("Background lifecycle manager started.")
    refresh_interval_seconds = 3
    loop_cooldown_seconds = 10
    error_retry_delay_seconds = 15
    post_battle_delay_seconds = 10

    loop_counter = 0

    while True:
        loop_counter += 1
        loop_start_time = time.monotonic()
        print(f"\n--- Lifecycle Check #{loop_counter} [{time.strftime('%H:%M:%S')}] ---")

        try:
            if not state.has_active_agent():
                print(f"[{loop_counter}] State 1: No active agent. Selecting...")
                activated = await select_and_activate_new_agent(state, update_display_html, log_task_exception)
                if not activated:
                    print(f"[{loop_counter}] State 1: Activation failed. Waiting {error_retry_delay_seconds}s before retry.")
                    await asyncio.sleep(error_retry_delay_seconds)
                else:
                    print(f"[{loop_counter}] State 1: Agent '{state.active_agent_name}' activated successfully.")
            else:
                agent_name = state.active_agent_name
                print(f"[{loop_counter}] State 2: Agent '{agent_name}' is active.")

                if not state.has_current_battle():
                    print(f"[{loop_counter}] State 2a: Checking for new battle for '{agent_name}'...")
                    await check_for_new_battle(state)

                    if state.has_current_battle():
                        battle_tag = state.current_battle_instance.battle_tag
                        print(f"[{loop_counter}] State 2a: *** NEW BATTLE DETECTED: {battle_tag} for '{agent_name}' ***")

                        parts = battle_tag.split("-")
                        is_suffixed_format = (
                            MATCHMAKING_MODE == "accept"
                            and len(parts) > 3
                            and parts[2].isdigit()
                        )

                        if is_suffixed_format:
                            print(f"[{loop_counter}] Detected potentially non-public battle format ({battle_tag}). Forfeiting.")
                            try:
                                if state.active_agent_instance:
                                    await state.active_agent_instance.forfeit(battle_tag)
                                    print(f"[{loop_counter}] Sent forfeit command for {battle_tag}.")
                                    await asyncio.sleep(1.5)
                            except Exception as forfeit_err:
                                print(f"[{loop_counter}] ERROR sending forfeit for {battle_tag}: {forfeit_err}")
                            await deactivate_current_agent(state, reason="forfeited_private_battle", update_display_html=update_display_html)
                            continue
                        else:
                            print(f"[{loop_counter}] Public battle format detected. Displaying battle {battle_tag}.")
                            await update_display_html(create_battle_redirect_html(battle_tag))
                            await asyncio.sleep(0.2)
                    else:
                        print(f"[{loop_counter}] State 2a: No new battle found. Agent '{agent_name}' remains idle in matchmaking mode '{MATCHMAKING_MODE}'.")
                        idle_html = create_matchmaking_idle_html(agent_name)
                        await update_display_html(idle_html)
                        await asyncio.sleep(refresh_interval_seconds)

                if state.has_current_battle():
                    battle_tag = state.current_battle_instance.battle_tag
                    print(f"[{loop_counter}] State 2b: Monitoring battle {battle_tag} for '{agent_name}'")

                    if not state.active_agent_instance:
                        print(f"[{loop_counter}] WARNING: Agent instance for '{agent_name}' disappeared while monitoring battle {battle_tag}! Deactivating.")
                        await deactivate_current_agent(state, reason="agent_disappeared_mid_battle", update_display_html=update_display_html)
                        continue

                    battle_obj = state.active_agent_instance._battles.get(battle_tag)

                    if battle_obj and battle_obj.finished:
                        print(f"[{loop_counter}] Battle {battle_tag} is FINISHED. Deactivating agent '{agent_name}'.")
                        await deactivate_current_agent(state, reason="battle_end", update_display_html=update_display_html)
                        print(f"[{loop_counter}] Waiting {post_battle_delay_seconds}s post-battle before selecting next agent.")
                        await asyncio.sleep(post_battle_delay_seconds)
                        continue
                    if not battle_obj:
                        print(f"[{loop_counter}] WARNING: Battle object for {battle_tag} not found in agent's list for '{agent_name}'. Battle might have ended abruptly. Deactivating.")
                        await deactivate_current_agent(state, reason="battle_object_missing", update_display_html=update_display_html)
                        continue

                    print(f"[{loop_counter}] Battle {battle_tag} ongoing for '{agent_name}'.")
                    await asyncio.sleep(refresh_interval_seconds)

        except asyncio.CancelledError:
            print("Lifecycle manager task cancelled.")
            raise
        except Exception as e:
            print(f"!!! ERROR in main lifecycle loop #{loop_counter}: {e} !!!")
            traceback.print_exc()
            current_agent_name = state.active_agent_name
            if state.has_active_agent():
                print(f"Attempting to deactivate agent '{current_agent_name}' due to loop error...")
                try:
                    await deactivate_current_agent(state, reason="main_loop_error", update_display_html=update_display_html)
                except Exception as deactivation_err:
                    print(f"Error during error-handling deactivation: {deactivation_err}")
                    state.clear_all()
            else:
                print("No active agent instance during loop error.")
                await update_display_html(create_error_html(f"A server error occurred in the lifecycle manager. Please wait. ({e})"))

            print(f"Waiting {error_retry_delay_seconds}s after loop error.")
            await asyncio.sleep(error_retry_delay_seconds)
            continue

        elapsed_time = time.monotonic() - loop_start_time
        if elapsed_time < loop_cooldown_seconds:
            await asyncio.sleep(loop_cooldown_seconds - elapsed_time)

