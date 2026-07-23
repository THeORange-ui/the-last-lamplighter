"""LLM-driven combat behavior: enemy turns and ACT/mercy resolution.

Persona enemies (the Gloam, hostile NPCs) take AI-chosen turns and react to the
player's ACT attempts, which can wear down their `resolve` until they become
spareable. Everything degrades gracefully to mechanical behavior on LLM error, so
a fight never stalls waiting on the model.
"""
from __future__ import annotations

from engine.combat import Combat, Combatant, enemy_attack
from llm.client import LLMError, complete_json
from npc.roster import load_character

# Fallback ACT options if an enemy has none defined.
DEFAULT_ACT_OPTIONS = ["Reason with it", "Show empathy", "Offer peace", "Threaten it"]


def act_options(enemy: Combatant) -> list[str]:
    if enemy.persona:
        try:
            opts = load_character(enemy.persona).get("combat", {}).get("act_options")
            if opts:
                return list(opts)
        except KeyError:
            pass
    return DEFAULT_ACT_OPTIONS


def _persona_block(enemy: Combatant) -> str:
    if not enemy.persona:
        return f"You are {enemy.name}, a hostile creature of the ridge dark."
    char = load_character(enemy.persona)
    return (
        f"You are {char['name']} — {char.get('role', '')}.\n"
        f"Personality: {char.get('personality', '')}\n"
        f"What drives you: {'; '.join(char.get('drives', []))}\n"
        f"Backstory: {char.get('backstory', '')}\n"
        f"Speech style: {char.get('speech_style', '')}\n"
        f"Hidden truths about you: {'; '.join(char.get('secrets', []))}"
    )


def _state_block(combat: Combat, enemy: Combatant) -> str:
    p = combat.player()
    return (
        f"Combat so far: {' '.join(combat.log[-4:]) if combat.log else '(it has just begun)'}\n"
        f"Your health: {enemy.hp}/{enemy.max_hp}. Your resolve to keep fighting: "
        f"{enemy.resolve}/100 (at 0 you would rather stop than fight).\n"
        f"The player's health: {p.hp}/{p.max_hp}."
    )


def enemy_turn(combat: Combat, enemy: Combatant) -> str:
    """Take the enemy's turn. Persona enemies choose via the LLM; others just attack."""
    if not enemy.persona:
        return enemy_attack(combat, enemy)

    char = load_character(enemy.persona)
    system = (
        f"{_persona_block(enemy)}\n\n"
        "You are in a turn-based fight with the player. Decide what you do THIS turn, in "
        "character. Reply as JSON: "
        '{\"say\": \"<one short line you speak, in character>\", '
        '\"action\": \"attack\" | \"heavy\" | \"loom\"}. '
        "attack = reach for them (normal), heavy = a stronger blow, loom = do no harm this "
        "turn, only speak or gather. Choose loom sometimes if you are uncertain or reaching "
        "out rather than striking."
    )
    user = _state_block(combat, enemy) + "\n\nWhat do you do?"
    try:
        out = complete_json(system, user, temperature=0.8, max_tokens=160)
    except LLMError:
        return enemy_attack(combat, enemy)

    say = str(out.get("say", "")).strip()
    action = str(out.get("action", "attack")).strip().lower()
    if say:
        combat.add_log(f"{enemy.name}: “{say}”")
    if action == "loom":
        return ""
    return enemy_attack(combat, enemy, heavy=(action == "heavy"))


def mercy_attempt(combat: Combat, enemy: Combatant, approach: str) -> str:
    """The player tries to reach the enemy with an ACT approach. Adjusts resolve and
    may make the enemy spareable. Returns a line describing what happened."""
    if not enemy.persona:
        # simple creatures: empathy/peace wear them down, threats don't
        soft = any(w in approach.lower() for w in ("peace", "empath", "reason", "calm", "warm", "spare"))
        enemy.resolve = max(0, enemy.resolve - (12 if soft else 2))
        if enemy.resolve <= 0:
            enemy.spareable = True
            combat.add_log(f"{enemy.name} falters, no longer eager to fight.")
            return f"{enemy.name} falters."
        combat.add_log(f"You try to reach {enemy.name}. It hesitates.")
        return "It hesitates."

    system = (
        f"{_persona_block(enemy)}\n\n"
        "The player is NOT attacking you this turn — they are trying to reach you with "
        f"words/gesture. Their approach: \"{approach}\".\n"
        "React in character, then judge how much this moves you. Reply as JSON: "
        '{\"reaction\": \"<one short in-character line or moment>\", '
        '\"resolve_delta\": <int -40..10, negative if it genuinely reaches you>, '
        '\"stand_down\": <true only if you are now willing to stop fighting>}. '
        "Let your drives and hidden truths decide — an approach that answers what you "
        "truly want should move you a lot; a hollow or hostile one barely, or the wrong way."
    )
    user = _state_block(combat, enemy) + "\n\nHow does this land?"
    try:
        out = complete_json(system, user, temperature=0.7, max_tokens=180)
    except LLMError:
        enemy.resolve = max(0, enemy.resolve - 8)
        combat.add_log(f"You reach out to {enemy.name}.")
        if enemy.resolve <= 0:
            enemy.spareable = True
        return "You reach out."

    reaction = str(out.get("reaction", "")).strip()
    try:
        delta = int(out.get("resolve_delta", -8))
    except (TypeError, ValueError):
        delta = -8
    delta = max(-40, min(10, delta))
    enemy.resolve = max(0, min(100, enemy.resolve + delta))
    if out.get("stand_down") or enemy.resolve <= 0:
        enemy.spareable = True
        enemy.resolve = 0
    if reaction:
        combat.add_log(f"{enemy.name}: “{reaction}”")
    if enemy.spareable:
        combat.add_log(f"{enemy.name} no longer moves to strike. You could spare it.")
    return reaction or "..."
