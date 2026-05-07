import tempfile
import unittest
from pathlib import Path

from panel.health_store import NodeHealthStore


class NodeHealthStoreTests(unittest.TestCase):
    def test_record_scores_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = NodeHealthStore(Path(td) / "health.json")

            ok = store.record("node-a", 120, None)
            fail = store.record("node-a", None, "timeout")

            self.assertGreater(ok["score"], fail["score"])
            self.assertEqual(store.snapshot()["node-a"]["success_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
