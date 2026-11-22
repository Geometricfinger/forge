"""Exercise the normal client against a real, completed reference mission."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER))
from research_runner import core, runner


class ClientLookup(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        records = []
        for name, line in (("guide", "Test delay was 204 ms. Pickup at the north door."),
                           ("other", "Pickup at the brass door.")):
            raw = line.encode()
            (self.sources / name).write_bytes(raw)
            records.append({"id": name, "path": name, "kind": "documentation",
                            "origin": "fixture://" + name, "capture": "synthetic_fixture",
                            "family": "workshop", "size": len(raw), "sha256": core.sha(raw)})
        manifest = {"schema": 1, "objective": "Locate a pickup point", "sources": records,
                    "questions": [{"id": "initial", "intent": "reference", "query": "pickup",
                                   "sources": ["guide", "other"]}],
                    "budgets": {"max_sources": 2, "max_source_bytes": 4000, "max_total_bytes": 8000,
                                "max_questions": 6, "packet_bytes": 8192}}
        (self.root / "manifest.json").write_bytes(core.canonical(manifest))
        runner.run(manifest, self.sources, self.root / "run", RUNNER.parent)
        profile = {"project": "workshop", "mission": {"manifest": "manifest.json",
                   "source_root": "sources", "work": "run", "sources": ["guide"]}}
        (self.root / "profile.json").write_bytes(core.canonical(profile))

    def call(self, request, expected=0, channel="host"):
        process = subprocess.run([sys.executable, "-B", str(RUNNER / "forge_converse.py"),
                                  "--db", str(self.root / "state.sqlite"), "--user", "speaker",
                                  "--profile", str(self.root / "profile.json"), "--channel", channel],
                                 input=core.canonical(request), capture_output=True)
        self.assertEqual(process.returncode, expected, process.stdout)
        return json.loads(process.stdout)

    def test_cites_selected_source_and_rechecks_changed_source_on_replay(self):
        req = {"action": "context", "query": "test delay", "anchors": ["204 ms"]}
        hit = self.call(req)["evidence"]["passage"]
        self.assertEqual((hit["origin"], hit["start_line"], hit["end_line"]),
                         ("fixture://guide", 1, 1))
        self.assertEqual(hit["source_sha256"], core.sha((self.sources / "guide").read_bytes()))
        self.assertIn("204 ms", hit["text"])
        self.assertEqual(self.call(dict(req, anchors=["4 ms"]))["evidence"]["status"],
                         "NO_MATCH_IN_REVIEWED_PASSAGES")
        (self.sources / "guide").write_text("Revised.")
        self.assertFalse(self.call(req, expected=2)["ok"])

    def test_request_cannot_broaden_scope(self):
        req = {"action": "context", "query": "pickup door", "anchors": ["brass door"]}
        self.assertIsNone(self.call(req)["evidence"]["passage"])
        self.assertEqual(self.call(dict(req, sources=["other"]), expected=2)["reason"], "REQUEST_FIELDS")
        self.assertEqual(self.call(dict(req, user="another"), expected=2)["reason"], "REQUEST_FIELDS")

    def test_context_and_playback_survive_separate_processes(self):
        self.call({"action": "remember", "key": "place", "value": "north"})
        self.call({"action": "start", "turn_id": "first"})
        self.call({"action": "acknowledge", "turn_id": "first", "sequence": 1, "text": "Go north."})
        self.call({"action": "finish", "turn_id": "first", "interrupted": True})
        ctx = self.call({"action": "context", "keys": ["place"], "query": "pickup point"})
        self.assertEqual(ctx["facts"], {"place": "north"})
        self.assertEqual(ctx["last_turn"]["spoken_text"], "Go north.")
        self.assertFalse(ctx["evidence"]["claims_verified"])

    def test_proposal_requires_a_separate_host_selected_review(self):
        suggest = {"action": "propose", "proposal_id": "p1", "key": "room", "value": "blue"}
        accept = {"action": "decide", "proposal_id": "p1", "accept": True}
        recall = {"action": "context", "keys": ["room"]}
        self.assertEqual(self.call(suggest, channel="speech")["proposal"]["status"], "pending")
        preview = {"action": "preview", "proposal_id": "p1"}
        self.assertEqual(self.call(preview, channel="review")["proposal"]["value"], "blue")
        self.assertFalse(self.call(preview, expected=2, channel="speech")["ok"])
        self.assertFalse(self.call(accept, expected=2, channel="speech")["ok"])
        self.assertFalse(self.call(dict(accept, channel="review"), expected=2, channel="speech")["ok"])
        self.assertIsNone(self.call(recall)["facts"]["room"])
        self.assertEqual(self.call(accept, channel="review")["proposal"]["status"], "applied")
        self.assertEqual(self.call(recall)["facts"]["room"], "blue")
        self.assertFalse(self.call({"action": "clear_scope"}, expected=2, channel="speech")["ok"])
        self.assertFalse(self.call(recall, expected=2, channel="review")["ok"])
