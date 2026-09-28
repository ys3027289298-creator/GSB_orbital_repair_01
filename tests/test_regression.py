import json
import os
import tempfile
import unittest

from orbital.core import (
    O2_PER_CREW,
    REPAIR_COST,
    SAVE_FIELDS,
    VACUUM_ALARM_PENALTY,
    Game,
    handle_command,
)


class ExtraRegressionTests(unittest.TestCase):
    def test_lose_locks_turns_and_actions(self):
        game = Game()
        game.event_queue.clear()
        game.oxygen = O2_PER_CREW * game.crew_alive
        self.assertTrue(game.advance_turn())
        self.assertEqual(game.state, "lost")
        turn, materials = game.turn, game.materials
        self.assertFalse(game.advance_turn())
        self.assertFalse(game.repair("alpha"))
        self.assertEqual(game.turn, turn)
        self.assertEqual(game.materials, materials)
        self.assertEqual(handle_command(game, "turn"), "locked")

    def test_undo_restores_robot_battery(self):
        game = Game()
        self.assertTrue(game.robot_action(3))
        self.assertEqual(game.battery, 2)
        self.assertTrue(game.undo())
        self.assertEqual(game.battery, 5)
        self.assertFalse(game.undo())

    def test_save_file_contains_exactly_save_fields(self):
        game = Game()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(set(data), set(SAVE_FIELDS))

    def test_vacuum_spreads_only_through_open_hatch(self):
        game = Game()
        game.advance_turn()
        # Hatches alpha-beta and beta-gamma stayed open, so beta breaches.
        self.assertTrue(game.modules["beta"]["breached"])
        # beta's alarm is queued for the next turn and timed exactly once.
        game.event_queue[:] = [
            e for e in game.event_queue if e["type"] == "vacuum"
        ]
        game.event_queue.append({"type": "vacuum", "module": "beta"})
        before = game.oxygen
        game.advance_turn()
        self.assertEqual(
            game.oxygen,
            before - VACUUM_ALARM_PENALTY - O2_PER_CREW * game.crew_alive,
        )

    def test_repair_undo_is_symmetric_through_save_load(self):
        game = Game()
        self.assertTrue(game.repair("alpha"))
        self.assertTrue(game.undo())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            loaded = Game.load(path)
        self.assertEqual(loaded._state_dict(), game._state_dict())
        self.assertEqual(loaded.materials, game.materials)
        self.assertEqual(
            loaded.modules["alpha"]["breached"],
            game.modules["alpha"]["breached"],
        )


if __name__ == "__main__":
    unittest.main()
