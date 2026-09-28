"""Core logic for the orbital station repair game.

Fixed-seed, CMD-menu driven. State-changing actions push a deep snapshot
before mutating, so undo, save/load and win/lose locking stay consistent
and event processing stays idempotent.
"""

from __future__ import annotations

import copy
import json
import random

FIXED_SEED = 53
O2_PER_CREW = 2
REPAIR_COST = 3
ROBOT_ACTION_COST = 1
VACUUM_ALARM_PENALTY = 5
SOLAR_WIND_DRAIN = 1
MAX_TURNS = 30

SAVE_FIELDS = (
    "seed",
    "turn",
    "oxygen",
    "materials",
    "battery",
    "crew_total",
    "crew_alive",
    "modules",
    "hatches",
    "tasks",
    "event_queue",
    "handled_alarms",
    "state",
)

MENU = (
    "=== Orbital Station Repair ===\n"
    "status                show station status\n"
    "repair <module>       repair a breached module\n"
    "close <a> <b>         close hatch between two modules\n"
    "robot [cost]          run a robot action\n"
    "turn                  advance one turn\n"
    "undo                  undo the last action\n"
    "save <path>           save the game\n"
    "load <path>           load a saved game\n"
    "quit                  exit\n"
)

MODULE_NAMES = ("alpha", "beta", "gamma")


class Game:
    def __init__(self, seed=FIXED_SEED):
        self.seed = seed
        self.rng = random.Random(seed)
        self.turn = 0
        self.oxygen = 100
        self.materials = 10
        self.battery = 5
        self.crew_total = 4
        self.crew_alive = 4
        self.modules = {name: {"breached": False} for name in MODULE_NAMES}
        self.hatches = {name: set() for name in MODULE_NAMES}
        self.connect("alpha", "beta")
        self.connect("beta", "gamma")
        self.tasks = [
            {"id": "t1", "module": "alpha", "done": False},
            {"id": "t2", "module": "gamma", "done": False},
        ]
        self.event_queue = []
        self.handled_alarms = set()
        self.state = "running"
        self._history = []
        self._breach("alpha")
        self._breach("gamma")

    # -- topology ---------------------------------------------------------

    def connect(self, a, b):
        self._require_modules(a, b)
        self.hatches[a].add(b)
        self.hatches[b].add(a)

    def close_hatch(self, a, b):
        """Close the hatch between two modules, severing both directions."""
        self._require_modules(a, b)
        if self.state != "running":
            return False
        if not self.is_connected(a, b):
            return False
        self._snapshot()
        self.hatches[a].discard(b)
        self.hatches[b].discard(a)
        return True

    def is_connected(self, a, b):
        self._require_modules(a, b)
        return b in self.hatches[a]

    def _require_modules(self, *names):
        for name in names:
            if name not in self.modules:
                raise ValueError("unknown module: %r" % name)

    # -- alarms / breaches --------------------------------------------------

    def _alarm_key(self, module):
        return ("vacuum", module)

    def _alarm_queued(self, module):
        return any(
            event.get("type") == "vacuum" and event.get("module") == module
            for event in self.event_queue
        )

    def _breach(self, module):
        """Breach a module; its vacuum alarm is queued at most once."""
        if self.modules[module]["breached"]:
            return
        self.modules[module]["breached"] = True
        key = self._alarm_key(module)
        if key not in self.handled_alarms and not self._alarm_queued(module):
            self.event_queue.append({"type": "vacuum", "module": module})

    # -- actions ------------------------------------------------------------

    def repair(self, module):
        """Repair a breached module; failure never consumes materials."""
        self._require_modules(module)
        if self.state != "running":
            return False
        if not self.modules[module]["breached"]:
            return False
        if self.materials < REPAIR_COST:
            return False
        self._snapshot()
        self.materials -= REPAIR_COST
        self.modules[module]["breached"] = False
        for task in self.tasks:
            if task["module"] == module and not task["done"]:
                task["done"] = True
        self._update_state()
        return True

    def robot_action(self, cost=ROBOT_ACTION_COST):
        """Run a robot action; refused unless the battery covers the cost."""
        if self.state != "running":
            return False
        if cost < 0 or cost > self.battery:
            return False
        self._snapshot()
        self.battery -= cost
        return True

    def trigger_solar_wind(self):
        """Enqueue solar-wind events in deterministic, sorted module order."""
        if self.state != "running":
            return False
        self._snapshot()
        for name in sorted(self.modules):
            self.event_queue.append({"type": "solar_wind", "module": name})
        return True

    # -- turn processing ------------------------------------------------------

    def advance_turn(self):
        """Process the queued events in FIFO order, then spread and drain O2."""
        if self.state != "running":
            return False
        self._snapshot()
        self.turn += 1
        pending = self.event_queue
        self.event_queue = []
        for event in pending:
            self._process_event(event)
        for name in sorted(self.modules):
            if self.modules[name]["breached"]:
                for neighbor in sorted(self.hatches[name]):
                    self._breach(neighbor)
        self.oxygen -= O2_PER_CREW * self.crew_alive
        self._update_state()
        return True

    def _process_event(self, event):
        kind = event.get("type")
        if kind == "vacuum":
            key = self._alarm_key(event["module"])
            if key in self.handled_alarms:
                return
            self.handled_alarms.add(key)
            self.oxygen -= VACUUM_ALARM_PENALTY
        elif kind == "solar_wind":
            self.battery = max(0, self.battery - SOLAR_WIND_DRAIN)

    def _update_state(self):
        if self.oxygen <= 0 or self.crew_alive <= 0 or self.turn >= MAX_TURNS:
            self.state = "lost"
        elif all(task["done"] for task in self.tasks):
            self.state = "won"

    # -- undo -----------------------------------------------------------------

    def _snapshot(self):
        self._history.append(self._state_dict())

    def undo(self):
        """Restore the previous state, including the event queue."""
        if not self._history:
            return False
        self._load_dict(self._history.pop())
        return True

    # -- save / load ------------------------------------------------------------

    def _state_dict(self):
        return {
            "seed": self.seed,
            "turn": self.turn,
            "oxygen": self.oxygen,
            "materials": self.materials,
            "battery": self.battery,
            "crew_total": self.crew_total,
            "crew_alive": self.crew_alive,
            "modules": copy.deepcopy(self.modules),
            "hatches": sorted(
                [a, b] for a in self.hatches for b in self.hatches[a] if a < b
            ),
            "tasks": copy.deepcopy(self.tasks),
            "event_queue": copy.deepcopy(self.event_queue),
            "handled_alarms": sorted(list(key) for key in self.handled_alarms),
            "state": self.state,
        }

    def _load_dict(self, data):
        self.seed = data["seed"]
        self.rng = random.Random(self.seed)
        self.turn = data["turn"]
        self.oxygen = data["oxygen"]
        self.materials = data["materials"]
        self.battery = data["battery"]
        self.crew_total = data["crew_total"]
        self.crew_alive = data["crew_alive"]
        self.modules = copy.deepcopy(data["modules"])
        self.hatches = {name: set() for name in self.modules}
        for a, b in data["hatches"]:
            self.hatches[a].add(b)
            self.hatches[b].add(a)
        self.tasks = copy.deepcopy(data["tasks"])
        self.event_queue = copy.deepcopy(data["event_queue"])
        self.handled_alarms = {tuple(key) for key in data["handled_alarms"]}
        self.state = data["state"]

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._state_dict(), fh, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        game = cls(seed=data["seed"])
        game._history = []
        game._load_dict(data)
        return game


# -- CMD menu -----------------------------------------------------------------


def status_text(game):
    return (
        "turn=%d oxygen=%d materials=%d battery=%d crew=%d/%d state=%s"
        % (
            game.turn,
            game.oxygen,
            game.materials,
            game.battery,
            game.crew_alive,
            game.crew_total,
            game.state,
        )
    )


def handle_command(game, line):
    parts = line.split()
    if not parts:
        return ""
    cmd, args = parts[0], parts[1:]
    if cmd == "status":
        return status_text(game)
    if cmd == "repair" and len(args) == 1:
        return "ok" if game.repair(args[0]) else "fail"
    if cmd == "close" and len(args) == 2:
        return "ok" if game.close_hatch(args[0], args[1]) else "fail"
    if cmd == "robot":
        cost = int(args[0]) if args else ROBOT_ACTION_COST
        return "ok" if game.robot_action(cost) else "fail"
    if cmd == "turn":
        return "ok" if game.advance_turn() else "locked"
    if cmd == "undo":
        return "ok" if game.undo() else "fail"
    if cmd == "save" and len(args) == 1:
        game.save(args[0])
        return "ok"
    if cmd == "load" and len(args) == 1:
        with open(args[0], encoding="utf-8") as fh:
            data = json.load(fh)
        game._history = []
        game._load_dict(data)
        return "ok"
    if cmd == "quit":
        return "quit"
    return "unknown command"


def main():
    game = Game()
    print(MENU)
    while True:
        try:
            line = input("orbital> ")
        except EOFError:
            break
        out = handle_command(game, line)
        if out == "quit":
            break
        if out:
            print(out)


if __name__ == "__main__":
    main()
