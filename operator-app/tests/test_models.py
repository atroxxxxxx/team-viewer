import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operator_app.agent import parse_action
from operator_app.hardware import profile_for, select_profile


class ProfileTests(unittest.TestCase):
    def test_ram_steps(self):
        self.assertEqual(select_profile(4, 0), "low")
        self.assertEqual(select_profile(8, 0), "small")
        self.assertEqual(select_profile(16, 0), "mid")
        self.assertEqual(select_profile(32, 0), "high")

    def test_gpu_raises_profile(self):
        self.assertEqual(select_profile(8, 8), "mid")
        self.assertEqual(select_profile(8, 16), "high")

    def test_high_uses_larger_files(self):
        low = profile_for("low", 4, 0)
        high = profile_for("high", 32, 0)
        self.assertIn("0.5B", low.text.repo)
        self.assertIn("7B", high.text.repo)
        self.assertEqual(len(high.text.files), 2)
        self.assertTrue(high.vision.vision)
        self.assertEqual(high.gpu_layers, 0)
        self.assertEqual(profile_for("mid", 16, 8).gpu_layers, -1)


class ParseTests(unittest.TestCase):
    def test_fenced_json(self):
        action = parse_action('```json\n{"action":"chat","intent":"guide_user","text":"закройте окно"}\n```')
        self.assertEqual(action["action"], "chat")
        self.assertEqual(action["text"], "закройте окно")

    def test_rejects_prose(self):
        with self.assertRaises(RuntimeError):
            parse_action("не знаю")


if __name__ == "__main__":
    unittest.main()
