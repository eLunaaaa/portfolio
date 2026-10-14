import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app import create_app

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def test_unknown_initial_state_and_assets(self):
        response = self.client.get("/status")
        self.assertIsNone(response.json["t1"]["ca"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.get("/static/images/demo-layout.svg") as asset:
            self.assertEqual(asset.status_code, 200)

    def test_independent_zones_and_types(self):
        self.assertEqual(self.client.get("/t1/fill/ca").status_code, 200)
        data = self.client.get("/status").json
        self.assertTrue(data["t1"]["ca"])
        self.assertIsNone(data["t1"]["ge"])
        self.assertIsNone(data["t2"]["ca"])
        self.client.get("/t1/empty/ca")
        self.assertFalse(self.client.get("/status").json["t1"]["ca"])

    def test_bulk_and_reset(self):
        self.client.get("/t2/fill/al")
        self.assertTrue(all(self.client.get("/status").json["t2"].values()))
        self.assertEqual(self.client.get("/reset").status_code, 405)
        self.assertEqual(self.client.post("/reset").status_code, 200)
        self.assertTrue(all(v is None for row in self.client.get("/status").json.values() for v in row.values()))

    def test_invalid_update_does_not_mutate(self):
        before = self.client.get("/status").json
        for path, code in [("/t3/fill/ca",404),("/t1/garbage/ca",400),("/t1/fill/unknown",400)]:
            self.assertEqual(self.client.get(path).status_code, code)
        self.assertEqual(self.client.get("/status").json, before)

    def test_app_instances_do_not_share_state(self):
        self.client.get("/t1/fill/al")
        self.assertIsNone(create_app().test_client().get("/status").json["t1"]["ca"])

if __name__ == "__main__":
    unittest.main()
