import json
import os
import tempfile
import unittest

from orbital.core import (
    FIXED_SEED,
    MENU,
    O2_PER_CREW,
    REPAIR_COST,
    SAVE_FIELDS,
    VACUUM_ALARM_PENALTY,
    Game,
    handle_command,
)


class BugFixTests(unittest.TestCase):
    """One test per fixed bug."""

    def test_vacuum_alarm_not_counted_twice(self):
        game = Game()
        # Duplicate an already-queued alarm for the same module.
        game.event_queue.append({"type": "vacuum", "module": "alpha"})
        game.advance_turn()
        # alpha + gamma alarms apply exactly once each, despite the duplicate.
        expected = 100 - 2 * VACUUM_ALARM_PENALTY - O2_PER_CREW * game.crew_alive
        self.assertEqual(game.oxygen, expected)
        self.assertIn(("vacuum", "alpha"), game.handled_alarms)

    def test_close_hatch_severs_both_directions(self):
        game = Game()
        self.assertTrue(game.close_hatch("alpha", "beta"))
        self.assertNotIn("beta", game.hatches["alpha"])
        self.assertNotIn("alpha", game.hatches["beta"])
        self.assertFalse(game.is_connected("alpha", "beta"))
        # With all hatches closed, vacuum cannot spread to neighbours.
        game.close_hatch("beta", "gamma")
        game.advance_turn()
        self.assertFalse(game.modules["beta"]["breached"])

    def test_repair_failure_rolls_back_materials(self):
        game = Game()
        game.materials = REPAIR_COST - 1
        self.assertFalse(game.repair("alpha"))
        self.assertEqual(game.materials, REPAIR_COST - 1)
        self.assertTrue(game.modules["alpha"]["breached"])

    def test_oxygen_uses_alive_crew_not_displayed(self):
        game = Game()
        game.event_queue.clear()
        game.crew_total = 4
        game.crew_alive = 2
        game.advance_turn()
        self.assertEqual(game.oxygen, 100 - O2_PER_CREW * 2)

    def test_solar_wind_event_order_is_stable(self):
        game1 = Game()
        game2 = Game()
        game1.trigger_solar_wind()
        game2.trigger_solar_wind()
        order1 = [e["module"] for e in game1.event_queue if e["type"] == "solar_wind"]
        order2 = [e["module"] for e in game2.event_queue if e["type"] == "solar_wind"]
        self.assertEqual(order1, sorted(order1))
        self.assertEqual(order1, order2)

    def test_robot_battery_never_goes_negative(self):
        game = Game()
        game.battery = 1
        self.assertFalse(game.robot_action(5))
        self.assertEqual(game.battery, 1)
        self.assertTrue(game.robot_action(1))
        self.assertEqual(game.battery, 0)
        self.assertFalse(game.robot_action(1))
        self.assertGreaterEqual(game.battery, 0)

    def test_undo_restores_event_queue(self):
        game = Game()
        before = [dict(e) for e in game.event_queue]
        game.trigger_solar_wind()
        self.assertNotEqual(game.event_queue, before)
        self.assertTrue(game.undo())
        self.assertEqual(game.event_queue, before)

    def test_load_does_not_retrigger_handled_alarms(self):
        game = Game()
        game.advance_turn()  # handles the initial alpha/gamma alarms
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            loaded = Game.load(path)
            reloaded = Game.load(path)
        self.assertEqual(loaded.handled_alarms, game.handled_alarms)
        for event in loaded.event_queue:
            if event["type"] == "vacuum":
                self.assertNotIn(("vacuum", event["module"]), loaded.handled_alarms)
        # Loading is idempotent.
        self.assertEqual(loaded._state_dict(), reloaded._state_dict())

    def test_win_locks_further_turns(self):
        game = Game()
        self.assertTrue(game.repair("alpha"))
        self.assertTrue(game.repair("gamma"))
        self.assertEqual(game.state, "won")
        turn, oxygen = game.turn, game.oxygen
        self.assertFalse(game.advance_turn())
        self.assertEqual(game.turn, turn)
        self.assertEqual(game.oxygen, oxygen)


class RegressionTests(unittest.TestCase):
    def test_save_load_roundtrip_keeps_fields(self):
        game = Game()
        game.advance_turn()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            loaded = Game.load(path)
        for field in SAVE_FIELDS:
            self.assertIn(field, data)
        self.assertEqual(loaded._state_dict(), game._state_dict())
        self.assertEqual(loaded.seed, FIXED_SEED)

    def test_undo_restores_materials_and_breach(self):
        game = Game()
        materials = game.materials
        self.assertTrue(game.repair("alpha"))
        self.assertEqual(game.materials, materials - REPAIR_COST)
        self.assertTrue(game.undo())
        self.assertEqual(game.materials, materials)
        self.assertTrue(game.modules["alpha"]["breached"])

    def test_cmd_menu_and_commands(self):
        self.assertIn("repair", MENU)
        self.assertIn("undo", MENU)
        game = Game()
        self.assertIn("oxygen", handle_command(game, "status"))
        self.assertEqual(handle_command(game, "close alpha beta"), "ok")
        self.assertFalse(game.is_connected("alpha", "beta"))


if __name__ == "__main__":
    unittest.main()
