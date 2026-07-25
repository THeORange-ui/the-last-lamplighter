"""LLM-driven combat behavior: enemy turns and ACT/mercy resolution.

Persona enemies (the Gloam, hostile NPCs) take AI-chosen turns and react to the
player's ACT attempts, which can wear down their `resolve` until they become
spareable. Everything degrades gracefully to mechanical behavior on LLM error, so
a fight never stalls waiting on the model.
"""
from __future__ import annotations

from engine.combat import (Combat, Combatant, ally_attack, ally_defend,
                           ally_spare, ally_step, enemy_attack)
from llm.client import LLMError, complete_json
from npc.roster import load_character

# A companion's words can wear down a foe a little (they can help talk enemies down).
ALLY_SPEAK_RESOLVE_HIT = 6

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


def _last_player_move(combat: Combat) -> str:
    for line in reversed(combat.log):
        if line.startswith("You"):
            return line
    return "(they have not moved yet)"


def _state_block(combat: Combat, enemy: Combatant) -> str:
    p = combat.player()
    return (
        f"Recent combat: {' '.join(combat.log[-4:]) if combat.log else '(it has just begun)'}\n"
        f"The player just did: {_last_player_move(combat)}\n"
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
        "You are in a turn-based fight with the player. In your 'say', REACT in character to "
        "what the player just did (their last move), then take your turn. Reply as JSON: "
        '{\"say\": \"<one short line reacting to their move, in character>\", '
        '\"action\": \"attack\" | \"heavy\" | \"loom\"}. '
        "attack = reach for them (normal), heavy = a committed, stronger blow, loom = hold "
        "back — you speak more than you strike, and the blow that lands is a weak one. "
        "Choose loom when you are uncertain, hurt, or reaching out rather than striking. "
        "You are still a danger: you always reach for them somehow. Always say something."
    )
    user = _state_block(combat, enemy) + "\n\nReact and act."
    try:
        out = complete_json(system, user, temperature=0.8, max_tokens=160)
    except LLMError:
        return enemy_attack(combat, enemy)

    say = str(out.get("say", "")).strip()
    action = str(out.get("action", "attack")).strip().lower()
    if say:
        combat.add_log(f"{enemy.name}: “{say}”")
    # It speaks AND strikes: even holding back, it still reaches for you.
    return enemy_attack(combat, enemy, heavy=(action == "heavy"),
                        restrained=(action == "loom"))


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
        enemy_attack(combat, enemy, restrained=True)   # reaching out is not free
        enemy.acted = True
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
        else:
            enemy_attack(combat, enemy, restrained=True)
        enemy.acted = True
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
    else:
        # Undertale-style: it answers you and still reaches for you in the same breath.
        # The more your words have worn its resolve, the weaker that blow lands.
        enemy_attack(combat, enemy, restrained=True)
    enemy.acted = True          # this WAS its turn; don't let it strike again
    return reaction or "..."


# --- companions (party members fighting at your side) ------------------------
def _ally_state_block(combat: Combat, ally: Combatant) -> str:
    p = combat.player()
    foes = combat.enemies()
    foe_lines = "; ".join(
        f"{e.name} (id={e.id}, hp {e.hp}/{e.max_hp}, resolve {e.resolve}"
        + (", willing to stop" if e.spareable else "") + ")"
        for e in foes
    ) or "no foe still stands"
    return (
        f"You fight alongside the player against: {foe_lines}.\n"
        f"Your health: {ally.hp}/{ally.max_hp}. The player's health: {p.hp}/{p.max_hp}.\n"
        f"Recent combat: {' '.join(combat.log[-4:]) if combat.log else '(it has just begun)'}\n"
        f"The player just did: {_last_player_move(combat)}"
    )


def ally_turn(combat: Combat, ally: Combatant, world=None) -> str:
    """A companion takes their turn, choosing via the LLM from the same options the
    player has. Falls back to a plain attack on any LLM trouble."""
    if not ally.persona:
        return ally_step(combat, ally)

    system = (
        f"{_persona_block(ally)}\n\n"
        "You are in a turn-based fight, standing with the player as their companion. "
        "Choose ONE action for your turn — the same choices the player has. Reply as JSON: "
        '{"say": "<one short in-character line to the player, a foe, or yourself; may be '
        'empty>", "action": "attack" | "defend" | "speak" | "spare", '
        '"target": "<foe id for attack/spare; omit otherwise>"}. '
        "attack = strike a foe. defend = brace against the next blow. speak = only talk this "
        "turn (gentle or understanding words to a FOE can wear down their will to fight). "
        "spare = stay your hand toward a foe already willing to stop. Act the way THIS "
        "character would — some rush in, some hang back, some would rather reach out."
    )
    user = _ally_state_block(combat, ally) + "\n\nWhat do you do?"
    try:
        out = complete_json(system, user, temperature=0.8, max_tokens=180)
    except LLMError:
        return ally_step(combat, ally)

    say = str(out.get("say", "")).strip()
    action = str(out.get("action", "attack")).strip().lower()
    tid = str(out.get("target", "")).strip()
    if say:
        combat.add_log(f"{ally.name}: “{say}”")

    foes = combat.enemies()
    target = next((e for e in foes if e.id == tid), None) or (foes[0] if foes else None)

    if action == "defend":
        return ally_defend(combat, ally)
    if action == "spare" and target is not None:
        return ally_spare(combat, ally, target)
    if action == "speak":
        if target is not None and target.persona:
            target.resolve = max(0, target.resolve - ALLY_SPEAK_RESOLVE_HIT)
            if target.resolve <= 0 and not target.spareable:
                target.spareable = True
                combat.add_log(f"{target.name} falters at {ally.name}'s words.")
        return say or "..."
    # default: attack
    if target is not None:
        return ally_attack(combat, ally, target)
    return ""


def speak_to_ally(combat: Combat, ally: Combatant, said: str, world=None) -> str:
    """The player says something to a companion mid-fight; they answer in character."""
    if not ally.persona:
        combat.add_log(f"{ally.name} nods to you.")
        return "..."
    system = (
        f"{_persona_block(ally)}\n\n"
        "You are fighting alongside the player as their companion. In the middle of the "
        f'battle, the player turns to you and says: "{said}". React in ONE short '
        'in-character line. Reply as JSON: {"reply": "<your line>"}.'
    )
    user = _ally_state_block(combat, ally) + "\n\nRespond."
    try:
        out = complete_json(system, user, temperature=0.8, max_tokens=120)
    except LLMError:
        combat.add_log(f"{ally.name} nods to you.")
        return "..."
    reply = str(out.get("reply", "")).strip()
    if reply:
        combat.add_log(f"{ally.name}: “{reply}”")
    return reply or "..."
