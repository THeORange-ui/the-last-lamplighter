"""Turn-based combat: state, combatants, damage, and win/lose resolution.

This module is pure logic (no pygame, no LLM). The UI drives it by calling
player_attack / player_defend / use_item_in_combat / player_spare / enemy_step,
and the LLM layer (npc/combat_agent.py) supplies enemy turn choices and ACT/mercy
outcomes. Mercy is the intended "good" route: ACT attempts lower an enemy's
`resolve`; once an enemy is `spareable`, Spare ends the fight peacefully.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Player base combat stats (HP lives on PlayerState; combat stats are here for now).
PLAYER_ATK = 8
PLAYER_DEF = 2
DEFEND_MULT = 0.4        # incoming damage multiplier while defending


@dataclass
class Combatant:
    id: str                      # 'player' | 'gloam' | 'ally_wren' | ...
    name: str
    hp: int
    max_hp: int
    attack: int
    defense: int
    side: str                    # 'player' | 'enemy' | 'ally'
    persona: str | None = None   # character id for LLM-driven enemies
    resolve: int = 100           # mercy meter; low resolve → spareable
    spareable: bool = False
    defending: bool = False
    alive: bool = True

    @property
    def is_enemy(self) -> bool:
        return self.side == "enemy"


# --- enemy bestiary ----------------------------------------------------------
# Gloam has lots of HP and is meant to be *talked down*, not ground out by force.
ENEMIES: dict[str, dict] = {
    "gloam": {"name": "The Gloam", "hp": 80, "attack": 6, "defense": 2,
              "resolve": 100, "persona": "gloam"},
    "gloamling": {"name": "Gloamling", "hp": 16, "attack": 4, "defense": 1,
                  "resolve": 40, "persona": None},
}


def make_enemy(enemy_id: str, *, uid: str | None = None) -> Combatant:
    spec = ENEMIES[enemy_id]
    return Combatant(
        id=uid or enemy_id, name=spec["name"], hp=spec["hp"], max_hp=spec["hp"],
        attack=spec["attack"], defense=spec["defense"], side="enemy",
        persona=spec.get("persona"), resolve=spec.get("resolve", 60),
    )


def enemies_from_ids(enemy_ids: list[str]) -> list[Combatant]:
    return [make_enemy(eid, uid=(f"{eid}_{i}" if len(enemy_ids) > 1 else eid))
            for i, eid in enumerate(enemy_ids)]


# Default combat stats for a townsperson who turns hostile (or joins as an ally).
NPC_COMBAT_DEFAULTS = {"hp": 24, "attack": 5, "defense": 2, "resolve": 60}


def combatant_from_npc(npc, side: str) -> Combatant:
    """Build a Combatant for an NPC (persona-driven, so it can be talked down)."""
    from npc.roster import character_name, load_character
    try:
        cs = load_character(npc.npc_id).get("combat_stats", {})
    except KeyError:
        cs = {}
    hp = cs.get("hp", NPC_COMBAT_DEFAULTS["hp"])
    return Combatant(
        id=(f"ally_{npc.npc_id}" if side == "ally" else npc.npc_id),
        name=character_name(npc.npc_id), hp=hp, max_hp=hp,
        attack=cs.get("attack", NPC_COMBAT_DEFAULTS["attack"]),
        defense=cs.get("defense", NPC_COMBAT_DEFAULTS["defense"]),
        side=side, persona=npc.npc_id, resolve=cs.get("resolve", NPC_COMBAT_DEFAULTS["resolve"]),
    )


@dataclass
class Combat:
    combatants: list[Combatant]
    log: list[str] = field(default_factory=list)
    over: bool = False
    outcome: str = ""            # 'won' | 'spared' | 'lost' | 'fled'

    # --- queries ----------------------------------------------------------
    def player(self) -> Combatant:
        return next(c for c in self.combatants if c.id == "player")

    def enemies(self, alive_only=True) -> list[Combatant]:
        return [c for c in self.combatants
                if c.side == "enemy" and (c.alive or not alive_only)]

    def allies(self, alive_only=True) -> list[Combatant]:
        return [c for c in self.combatants
                if c.side == "ally" and (c.alive or not alive_only)]

    def add_log(self, msg: str) -> None:
        self.log.append(msg)

    # --- state checks -----------------------------------------------------
    def check_end(self) -> None:
        if not self.enemies():
            self.over, self.outcome = True, "won"
        elif not self.player().alive:
            self.over, self.outcome = True, "lost"


def _apply_damage(target: Combatant, raw: int) -> int:
    dmg = max(1, raw - target.defense)
    if target.defending:
        dmg = max(1, int(dmg * DEFEND_MULT))
    target.hp = max(0, target.hp - dmg)
    if target.hp == 0:
        target.alive = False
    return dmg


def make_combat(player_hp: int, player_max_hp: int, enemies: list[Combatant],
                allies: list[Combatant] | None = None) -> Combat:
    player = Combatant(id="player", name="You", hp=player_hp, max_hp=player_max_hp,
                       attack=PLAYER_ATK, defense=PLAYER_DEF, side="player")
    combatants = [player] + (allies or []) + list(enemies)
    c = Combat(combatants=combatants)
    foe = " and ".join(e.name for e in c.enemies())
    c.add_log(f"{foe} {'stands' if len(c.enemies()) == 1 else 'stand'} against you.")
    if allies:
        verb = "stands" if len(allies) == 1 else "stand"
        c.add_log(f"{', '.join(a.name for a in allies)} {verb} with you.")
    return c


# --- player actions ----------------------------------------------------------
def player_attack(combat: Combat, target: Combatant) -> str:
    p = combat.player()
    p.defending = False
    dmg = _apply_damage(target, p.attack)
    msg = f"You strike {target.name} for {dmg}."
    if not target.alive:
        msg += f" {target.name} is undone."
    combat.add_log(msg)
    combat.check_end()
    return msg


def player_defend(combat: Combat) -> str:
    combat.player().defending = True
    msg = "You steady yourself, bracing for the next blow."
    combat.add_log(msg)
    return msg


def player_spare(combat: Combat, target: Combatant) -> str:
    """End the fight peacefully if the target is willing."""
    if not target.spareable:
        msg = f"{target.name} is not ready to be spared."
        combat.add_log(msg)
        return msg
    target.alive = False   # removed from the fight, but peacefully
    combat.add_log(f"You lower your guard. {target.name} yields.")
    if not combat.enemies():
        combat.over, combat.outcome = True, "spared"
    return f"You spare {target.name}."


def ally_attack(combat: Combat, ally: Combatant, target: Combatant) -> str:
    """A companion strikes a chosen foe (used by the LLM-driven ally turn)."""
    ally.defending = False
    dmg = _apply_damage(target, ally.attack)
    msg = f"{ally.name} strikes {target.name} for {dmg}."
    if not target.alive:
        msg += f" {target.name} is undone."
    combat.add_log(msg)
    combat.check_end()
    return msg


def ally_defend(combat: Combat, ally: Combatant) -> str:
    ally.defending = True
    msg = f"{ally.name} steadies, guarding against the next blow."
    combat.add_log(msg)
    return msg


def ally_spare(combat: Combat, ally: Combatant, target: Combatant) -> str:
    """A companion stays their hand toward a foe already willing to stop."""
    if not target.spareable:
        return ""
    target.alive = False
    combat.add_log(f"{ally.name} lowers their guard. {target.name} yields.")
    if not combat.enemies():
        combat.over, combat.outcome = True, "spared"
    return f"{ally.name} spares {target.name}."


def ally_step(combat: Combat, ally: Combatant) -> str:
    """Mechanical fallback: attack the first standing foe."""
    target = next((e for e in combat.enemies()), None)
    if not target:
        return ""
    return ally_attack(combat, ally, target)


def enemy_attack(combat: Combat, enemy: Combatant, *, heavy: bool = False) -> str:
    """Mechanical enemy strike (used directly or as an LLM fallback)."""
    targets = [combat.player()] + combat.allies()
    targets = [t for t in targets if t.alive]
    if not targets:
        return ""
    target = targets[0]
    raw = enemy.attack + (3 if heavy else 0)
    dmg = _apply_damage(target, raw)
    verb = "lashes out at" if heavy else "reaches for"
    msg = f"{enemy.name} {verb} {target.name} — {dmg} damage."
    combat.add_log(msg)
    # a defender's brace lasts only for the hit it took
    target.defending = False
    combat.check_end()
    return msg
