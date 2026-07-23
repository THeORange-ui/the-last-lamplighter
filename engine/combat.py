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


def make_combat(player_hp: int, player_max_hp: int, enemy_ids: list[str],
                allies: list[Combatant] | None = None) -> Combat:
    player = Combatant(id="player", name="You", hp=player_hp, max_hp=player_max_hp,
                       attack=PLAYER_ATK, defense=PLAYER_DEF, side="player")
    combatants = [player]
    combatants += allies or []
    for i, eid in enumerate(enemy_ids):
        combatants.append(make_enemy(eid, uid=f"{eid}_{i}" if len(enemy_ids) > 1 else eid))
    c = Combat(combatants=combatants)
    foe = " and ".join(e.name for e in c.enemies())
    c.add_log(f"{foe} rises before you.")
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


def ally_step(combat: Combat, ally: Combatant) -> str:
    target = next((e for e in combat.enemies()), None)
    if not target:
        return ""
    dmg = _apply_damage(target, ally.attack)
    msg = f"{ally.name} strikes {target.name} for {dmg}."
    combat.add_log(msg)
    combat.check_end()
    return msg


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
