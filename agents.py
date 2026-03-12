import json
from pathlib import Path

# --- OpenAI ---
from openai import AsyncOpenAI, APIError

# --- Poke-Env ---
from poke_env.player import Player
from typing import Any, Dict
from utils import POKEMON_SETTINGS

# --- Helper Function & Base Class ---
def normalize_name(name: str) -> str:
    """Lowercase and remove non-alphanumeric characters."""
    return "".join(filter(str.isalnum, name)).lower()

SCHEMA_CONFIG = json.loads((Path(__file__).with_name("yaml") / "schema.yaml").read_text(encoding="utf-8"))
STANDARD_TOOL_SCHEMA = SCHEMA_CONFIG["standard_tools"]
OPENAI_TOOL_SCHEMA = SCHEMA_CONFIG["openai_tools"]
SYSTEM_PROMPT = SCHEMA_CONFIG["prompts"]["system"]
USER_PROMPT_TEMPLATE = SCHEMA_CONFIG["prompts"]["user_template"]
GENERATION_CONFIG = SCHEMA_CONFIG["generation"]


class LLMAgentBase(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.standard_tools = STANDARD_TOOL_SCHEMA
        self.battle_history = []

    def _format_battle_state(self, battle) -> str:
        active_pkmn = battle.active_pokemon
        active_pkmn_info = f"Your active Pokemon: {active_pkmn.species} " \
                           f"(Type: {'/'.join(map(str, active_pkmn.types))}) " \
                           f"HP: {active_pkmn.current_hp_fraction * 100:.1f}% " \
                           f"Status: {active_pkmn.status.name if active_pkmn.status else 'None'} " \
                           f"Boosts: {active_pkmn.boosts}"

        opponent_pkmn = battle.opponent_active_pokemon
        opp_info_str = "Unknown"
        if opponent_pkmn:
            opp_info_str = f"{opponent_pkmn.species} " \
                           f"(Type: {'/'.join(map(str, opponent_pkmn.types))}) " \
                           f"HP: {opponent_pkmn.current_hp_fraction * 100:.1f}% " \
                           f"Status: {opponent_pkmn.status.name if opponent_pkmn.status else 'None'} " \
                           f"Boosts: {opponent_pkmn.boosts}"
        opponent_pkmn_info = f"Opponent's active Pokemon: {opp_info_str}"

        available_moves_info = "Available moves:\n"
        if battle.available_moves:
            available_moves_info += "\n".join(
                [f"- {move.id} (Type: {move.type}, BP: {move.base_power}, Acc: {move.accuracy}, PP: {move.current_pp}/{move.max_pp}, Cat: {move.category.name})"
                 for move in battle.available_moves]
            )
        else:
             available_moves_info += "- None (Must switch or Struggle)"

        available_switches_info = "Available switches:\n"
        if battle.available_switches:
              available_switches_info += "\n".join(
                  [f"- {pkmn.species} (HP: {pkmn.current_hp_fraction * 100:.1f}%, Status: {pkmn.status.name if pkmn.status else 'None'})"
                   for pkmn in battle.available_switches]
              )
        else:
            available_switches_info += "- None"

        state_str = f"{active_pkmn_info}\n" \
                    f"{opponent_pkmn_info}\n\n" \
                    f"{available_moves_info}\n\n" \
                    f"{available_switches_info}\n\n" \
                    f"Weather: {battle.weather}\n" \
                    f"Terrains: {battle.fields}\n" \
                    f"Your Side Conditions: {battle.side_conditions}\n" \
                    f"Opponent Side Conditions: {battle.opponent_side_conditions}"
        return state_str.strip()

    def _record_battle_action(self, battle, player_action: str) -> None:
        record = {
            "Battle_tag": battle.battle_tag,
            "Turn": battle.turn,
            "Weather": battle.weather,
            "Terrains": battle.fields,
            "Your Side Conditions": battle.side_conditions,
            "Opponent Side Conditions": battle.opponent_side_conditions,
            "Opponent_last_action": "unknown",
            "Player_action": player_action,
            "Result": "win" if battle.won else "lost" if battle.lost else "finished" if battle.finished else "ongoing",
        }
        self.battle_history.append(record)

    def _find_move_by_name(self, battle, move_name: str):
        normalized_name = normalize_name(move_name)
        # Prioritize exact ID match
        for move in battle.available_moves:
            if move.id == normalized_name:
                return move
        # Fallback: Check display name (less reliable)
        for move in battle.available_moves:
            if move.name.lower() == move_name.lower():
                print(f"Warning: Matched move by display name '{move.name}' instead of ID '{move.id}'. Input was '{move_name}'.")
                return move
        return None

    def _find_pokemon_by_name(self, battle, pokemon_name: str):
        normalized_name = normalize_name(pokemon_name)
        for pkmn in battle.available_switches:
            # Normalize the species name for comparison
            if normalize_name(pkmn.species) == normalized_name:
                return pkmn
        return None

    async def choose_move(self, battle) -> str:
        battle_state_str = self._format_battle_state(battle)
        decision_result = await self._get_llm_decision(battle_state_str)
        print(decision_result)
        decision = decision_result.get("decision")
        error_message = decision_result.get("error")
        action_taken = False
        fallback_reason = ""

        if decision:
            function_name = decision.get("name")
            args = decision.get("arguments", {})
            if function_name == "choose_move":
                move_name = args.get("move_name")
                if move_name:
                    chosen_move = self._find_move_by_name(battle, move_name)
                    if chosen_move and chosen_move in battle.available_moves:
                        action_taken = True
                        chat_msg = f"AI Decision: Using move '{chosen_move.id}'."
                        print(chat_msg)
                        self._record_battle_action(battle, f"move:{chosen_move.id}")
                        return self.create_order(chosen_move)
                    else:
                        fallback_reason = f"LLM chose unavailable/invalid move '{move_name}'."
                else:
                     fallback_reason = "LLM 'choose_move' called without 'move_name'."
            elif function_name == "choose_switch":
                pokemon_name = args.get("pokemon_name")
                if pokemon_name:
                    chosen_switch = self._find_pokemon_by_name(battle, pokemon_name)
                    if chosen_switch and chosen_switch in battle.available_switches:
                        action_taken = True
                        chat_msg = f"AI Decision: Switching to '{chosen_switch.species}'."
                        print(chat_msg)
                        self._record_battle_action(battle, f"switch:{chosen_switch.species}")
                        return self.create_order(chosen_switch)
                    else:
                        fallback_reason = f"LLM chose unavailable/invalid switch '{pokemon_name}'."
                else:
                    fallback_reason = "LLM 'choose_switch' called without 'pokemon_name'."
            else:
                fallback_reason = f"LLM called unknown function '{function_name}'."

        if not action_taken:
            if not fallback_reason:
                 if error_message:
                     fallback_reason = f"API Error: {error_message}"
                 elif decision is None:
                      fallback_reason = "LLM did not provide a valid function call."
                 else:
                      fallback_reason = "Unknown error processing LLM decision."

            print(f"Warning: {fallback_reason} Choosing random action.")
            self._record_battle_action(battle, f"fallback:{fallback_reason}")

            if battle.available_moves or battle.available_switches:
                 return self.choose_random_move(battle)
            else:
                 print("AI Fallback: No moves or switches available. Using Struggle/Default.")
                 return self.choose_default_move(battle)

    async def _get_llm_decision(self, battle_state: str) -> Dict[str, Any]:
        raise NotImplementedError("Subclasses must implement _get_llm_decision")


# --- OpenAI Agent ---
class OpenAIAgent(LLMAgentBase):
    """Uses OpenAI API for decisions."""
    def __init__(self, api_key: str = None, model: str = POKEMON_SETTINGS.openai_model, avatar: str = "giovanni", *args, **kwargs):
        # Set avatar before calling parent constructor
        kwargs['avatar'] = avatar
        kwargs['start_timer_on_battle_start'] = True
        super().__init__(*args, **kwargs)
        self.model = model
        used_api_key = api_key or POKEMON_SETTINGS.openai_api_key
        if not used_api_key:
            raise ValueError("OpenAI API key not provided in pokemon/yaml/config.yaml or via constructor.")
        self.openai_client = AsyncOpenAI(
            api_key=used_api_key,
            base_url=POKEMON_SETTINGS.openai_base_url,
        )

        # Use the OpenAI-specific schema with type field
        self.openai_tools = list(OPENAI_TOOL_SCHEMA.values())

    async def _get_llm_decision(self, battle_state: str) -> Dict[str, Any]:
        system_prompt = SYSTEM_PROMPT
        user_prompt = USER_PROMPT_TEMPLATE.format(battle_state=battle_state)

        try:
            response = await self.openai_client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": system_prompt,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": user_prompt,
                            }
                        ],
                    },
                ],
                tools=self.openai_tools,
                tool_choice=GENERATION_CONFIG["tool_choice"],
                temperature=GENERATION_CONFIG["temperature"],
            )

            # print("OPENAI RESPONSE : ", response)

            function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if function_calls:
                tool_call = function_calls[0]
                function_name = tool_call.name
                try:
                    arguments = json.loads(tool_call.arguments or "{}")
                    if function_name in self.standard_tools:
                        return {"decision": {"name": function_name, "arguments": arguments}}
                    return {"error": f"Model called unknown function '{function_name}'."}
                except json.JSONDecodeError:
                    return {"error": f"Error decoding function arguments: {tool_call.arguments}"}

            output_text = getattr(response, "output_text", "")
            return {"error": f"OpenAI did not return a function call. Response: {output_text}"}

        except APIError as e:
            print(f"Error during OpenAI API call: {e}")
            return {"error": f"OpenAI API Error: {e.status_code} - {e.message}"}
        except Exception as e:
            print(f"Unexpected error during OpenAI API call: {e}")
            return {"error": f"Unexpected error: {e}"}



