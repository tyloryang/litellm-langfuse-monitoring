import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "litellm"))
from token_router import ContextTooLong, select_tier


class TokenRouterTest(unittest.TestCase):
    def test_shortest_fitting_tier(self):
        self.assertEqual(select_tier(100, 1024).model, "qwen-32k")
        self.assertEqual(select_tier(32000, 1024).model, "qwen-64k")
        self.assertEqual(select_tier(65000, 1024).model, "qwen-128k")
        self.assertEqual(select_tier(131000, 1024).model, "qwen-256k")

    def test_exact_boundary_uses_current_tier(self):
        self.assertEqual(select_tier(32768, 0).model, "qwen-32k")
        self.assertEqual(select_tier(32769, 0).model, "qwen-64k")
        self.assertEqual(select_tier(262144, 0).model, "qwen-256k")

    def test_above_256k_is_rejected(self):
        with self.assertRaises(ContextTooLong):
            select_tier(262144, 1)


if __name__ == "__main__":
    unittest.main()