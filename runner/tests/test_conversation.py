"""Persistence, replay and isolation contracts using actual SQLite connections."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversation_runtime.memory import ConversationMemory


class ConversationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name).resolve() / "private" / "conversation.db"

    def store(self, user="a", project="study"):
        return ConversationMemory(self.path, user, project)

    def test_persistence_correction_and_forgetting(self):
        with self.store() as store:
            self.assertIsNone(store.recall("place"))
            store.remember("place", "blue")
        with self.store() as store:
            self.assertEqual(store.recall("place"), "blue")
            self.assertTrue(store.correct("place", "blue", "green"))
            self.assertFalse(store.correct("place", "blue", "stale"))
            self.assertEqual(store.recall("place"), "green")
            self.assertTrue(store.forget("place"))
            self.assertFalse(store.forget("place"))
            self.assertFalse(store.correct("place", "green", "red"))
        with self.store() as store:
            self.assertIsNone(store.recall("place"))

    def test_previous_generation_and_finished_replays(self):
        with self.store() as store:
            store.start_turn("one")
            store.acknowledge("one", 1, "First ")
            store.acknowledge("one", 2, "sentence.")
            store.start_turn("two")
            store.start_turn("one")
            store.acknowledge("one", 1, "First ")
            with self.assertRaises(ValueError):
                store.acknowledge("one", 3, "never played")
            with self.assertRaises(ValueError):
                store.finish_turn("one", interrupted=False)
            store.finish_turn("two", interrupted=False)
            store.finish_turn("two", interrupted=False)
        with self.store() as store:
            self.assertEqual(store.turn("one"), {"turn_id": "one", "status": "interrupted",
                             "spoken_text": "First sentence.", "acknowledged_chunks": 2})
            self.assertEqual(store.latest_turn(), {"turn_id": "two", "status": "completed",
                             "spoken_text": "", "acknowledged_chunks": 0})

    def test_write_conflicts_and_rejected_receipts_preserve_prefix(self):
        with self.store() as store:
            self.assertIsNone(store.latest_turn())
            with self.assertRaises(ValueError):
                store.acknowledge("unknown", 1, "never played")
            store.start_turn("one")
            store.acknowledge("one", 1, "partial")
            for bad_seq, bad_text in [(1, "rewritten"), (3, "gap"), (True, "partial"),
                                      (2, ""), (2, "x" * 2049)]:
                with self.subTest(bad_seq=bad_seq), self.assertRaises(ValueError):
                    store.acknowledge("one", bad_seq, bad_text)
            store.finish_turn("one", interrupted=True)
        with self.store() as store:
            self.assertEqual(store.latest_turn()["spoken_text"], "partial")
            self.assertEqual(store.latest_turn()["status"], "interrupted")

    def test_scoped_user_project_and_clear(self):
        identities = [("a", "study"), ("b", "study"), ("a", "demo")]
        for user, project in identities:
            tag = user + project
            with self.store(user, project) as store:
                self.assertIsNone(store.recall("place"))
                self.assertIsNone(store.turn("common"))
                store.remember("place", tag)
                store.start_turn("common")
                store.acknowledge("common", 1, tag)
        with self.store() as store:
            store.clear_scope()
            store.clear_scope()
            self.assertIsNone(store.latest_turn())
            self.assertIsNone(store.recall("place"))
        for user, project in identities[1:]:
            with self.store(user, project) as store:
                self.assertEqual(store.recall("place"), user + project)
                self.assertEqual(store.turn("common")["spoken_text"], user + project)

    def test_concurrent_receipts_accept_only_one_conflicting_text(self):
        with self.store() as store:
            store.start_turn("t")
        rendezvous = threading.Barrier(2)
        def write(value):
            with self.store() as store:
                rendezvous.wait(timeout=5)
                try:
                    store.acknowledge("t", 1, value)
                    return value
                except ValueError:
                    return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = [v for v in pool.map(write, ["north", "south"]) if v]
        self.assertEqual(len(accepted), 1)
        with self.store() as store:
            self.assertEqual(store.turn("t")["spoken_text"], accepted[0])
            self.assertEqual(store.turn("t")["acknowledged_chunks"], 1)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            self.store("", "project")
        with self.store() as store:
            for key, value in [(" ", "value"), ("key", "\x00"), ("key", "x" * 4097)]:
                with self.assertRaises(ValueError):
                    store.remember(key, value)
            with self.assertRaises(ValueError):
                store.start_turn(" ")
            store.start_turn("valid")
            with self.assertRaises(ValueError):
                store.finish_turn("valid", interrupted="yes")
            self.assertEqual(store.turn("valid")["status"], "playing")

    def test_file_mode_and_unrelated_database(self):
        with self.store():
            pass
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.path = self.path.with_name("other.db")
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE existing (value)")
        db.close()
        with self.assertRaises(ValueError):
            self.store()
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [("existing",)])
        finally:
            db.close()

    def test_refuse_linked_path_and_sidecars(self):
        with self.store():
            pass
        target = self.path.parent / "unused"
        target.write_text("untouched")
        for suffix in ("-wal", "-shm", "-journal"):
            linked = self.path.with_name(self.path.name + suffix)
            linked.symlink_to(target)
            with self.assertRaises(ValueError):
                self.store()
            linked.unlink()
        alias = self.path.parent / "link.db"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            ConversationMemory(alias, "a", "study")
        self.assertEqual(target.read_text(), "untouched")

    def test_reject_permissive_or_hardlinked_store_and_sidecars(self):
        with self.store():
            pass
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store()
        self.path.chmod(0o600)
        alias = self.path.parent / "copy"
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            self.store()
        alias.unlink()
        target = self.path.parent / "private-other"
        target.touch(mode=0o600)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            os.link(target, sidecar)
            with self.assertRaises(ValueError):
                self.store()
            sidecar.unlink()
            sidecar.touch(mode=0o600)
            sidecar.chmod(0o644)
            with self.assertRaises(ValueError):
                self.store()
            sidecar.unlink()
            os.mkfifo(sidecar, mode=0o600)
            with self.assertRaises(ValueError):
                self.store()
            sidecar.unlink()

    def test_context_snapshot_straddling_a_committed_clear(self):
        with self.store() as original:
            original.remember("first", "one")
            original.remember("second", "two")
            original.start_turn("previous")
        config = sqlite3.connect(self.path)
        try:
            self.assertEqual(config.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        finally:
            config.close()
        with self.store() as reader, self.store() as writer:
            recall = reader.recall

            def clear_after_first_read(key):
                value = recall(key)
                if key == "first":
                    writer.clear_scope()
                return value

            with patch.object(reader, "recall", side_effect=clear_after_first_read):
                state = reader.snapshot(["first", "second"])
            self.assertEqual(state["facts"], {"first": "one", "second": "two"})
            self.assertEqual(state["latest_turn"]["turn_id"], "previous")
            self.assertEqual(reader.snapshot(["first", "second"]),
                             {"facts": {"first": None, "second": None}, "latest_turn": None})
            with self.assertRaises(ValueError):
                reader.snapshot(["key"] * 17)

    def test_pending_restart_rejection_and_replay_identity(self):
        pending = {"proposal_id": "p", "key": "door", "value": "orange", "status": "pending"}
        with self.store() as store:
            store.remember("door", "green")
            self.assertEqual(store.propose("p", "door", "orange"), pending)
            self.assertEqual(store.propose("p", "door", "orange"), pending)
            self.assertEqual(store.recall("door"), "green")
        with self.store() as store:
            self.assertEqual(store.proposal("p"), pending)
            with self.assertRaises(ValueError):
                store.propose("p", "other", "orange")
            with self.assertRaises(ValueError):
                store.decide("p", accept=1)
            closed = store.decide("p", accept=False)
            self.assertEqual(closed, {"proposal_id": "p", "key": "door", "status": "rejected"})
            self.assertEqual(store.decide("p", accept=False), closed)
            self.assertEqual(store.proposal("p"), closed)
            self.assertEqual(store.propose("p", "door", "orange"), closed)
            with self.assertRaises(ValueError):
                store.decide("p", accept=True)
            with self.assertRaises(ValueError):
                store.propose("p", "door", None)
            self.assertEqual(store.recall("door"), "green")

    def test_aba_and_unrelated_writes(self):
        with self.store() as store, self.store() as other:
            store.remember("counter", "before")
            store.propose("aba", "counter", "proposed")
            self.assertTrue(other.correct("counter", "before", "between"))
            other.remember("counter", "before")
            self.assertEqual(store.decide("aba", accept=True)["status"], "stale")
            self.assertEqual(store.recall("counter"), "before")
            store.propose("independent", "counter", "next")
            self.assertFalse(other.correct("counter", "wrong", "unused"))
            other.remember("other", "unchanged")
            self.assertEqual(store.decide("independent", True)["status"], "applied")
            self.assertEqual(store.recall("counter"), "next")
            self.assertEqual(store.recall("other"), "unchanged")
            other.remember("counter", "latest")
            self.assertEqual(store.decide("independent", True)["status"], "applied")
            self.assertEqual(store.recall("counter"), "latest")

    def test_proposed_forget_and_direct_forget_drop_pending_contents(self):
        with self.store() as store:
            store.remember("desk", "tall")
            self.assertIsNone(store.propose("del", "desk", None)["value"])
            self.assertEqual(store.recall("desk"), "tall")
            store.propose("replacement", "desk", "short")
            self.assertEqual(store.decide("del", True)["status"], "applied")
            self.assertIsNone(store.recall("desk"))
            self.assertEqual(store.decide("replacement", True)["status"], "stale")
            store.propose("missing", "desk", "unconfirmed")
            self.assertFalse(store.forget("desk"))
            self.assertNotIn("value", store.proposal("missing"))
            self.assertEqual(store.propose("missing", "desk", "unconfirmed")["status"], "stale")
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("SELECT value, base_hash FROM proposals").fetchall(),
                             [(None, None)] * 3)
        finally:
            db.close()

    def test_proposals_are_scoped_and_cleared(self):
        with self.store() as first, self.store(user="b") as second, self.store(project="q") as third:
            first.propose("p", "color", "blue")
            with self.assertRaises(ValueError):
                second.decide("p", True)
            with self.assertRaises(ValueError):
                third.decide("p", True)
            second.propose("p", "color", "red")
            third.propose("p", "color", "green")
            first.clear_scope()
            with self.assertRaises(ValueError):
                first.proposal("p")
            with self.assertRaises(ValueError):
                first.decide("p", True)
            second.decide("p", True)
            third.decide("p", True)
            self.assertEqual(second.recall("color"), "red")
            self.assertEqual(third.recall("color"), "green")
            self.assertIsNone(first.recall("color"))

    def test_competing_proposals_cannot_both_apply(self):
        updates = ["west", "east"]
        with self.store() as store:
            for value in updates:
                store.propose(value, "place", value)
        barrier = threading.Barrier(2)

        def decide(value):
            with self.store() as store:
                barrier.wait(timeout=5)
                return store.decide(value, True)["status"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(decide, updates))
        self.assertCountEqual(states, ["applied", "stale"])
        with self.store() as store:
            self.assertEqual(store.recall("place"), updates[states.index("applied")])

    def test_upgrade_v1_preserves_scoped_history_and_facts(self):
        # Remove only the v2 addition to reconstruct the original v1 schema on disk.
        with self.store() as store:
            store.remember("original", "kept")
            store.start_turn("v1")
            store.acknowledge("v1", 1, "Original playback.")
        db = sqlite3.connect(self.path)
        try:
            for event in ("INSERT", "UPDATE", "DELETE"):
                db.execute(f"DROP TRIGGER stale_proposals_after_{event}")
            db.execute("DROP TABLE proposals")
            db.execute("PRAGMA user_version=1")
            db.commit()
            # Keep the legacy connection alive across migration. Direct writes
            # model its v1 statements, which know nothing about proposals.
            with self.store() as store:
                self.assertEqual(store.recall("original"), "kept")
                self.assertEqual(store.turn("v1")["spoken_text"], "Original playback.")
                store.propose("legacy-aba", "original", "replace")
                for value in ("intervening", "kept"):
                    with db:
                        db.execute("UPDATE facts SET value=? WHERE key='original'", (value,))
                self.assertEqual(store.decide("legacy-aba", True)["status"], "stale")
                store.propose("fresh", "fresh", "new")
                self.assertEqual(store.decide("fresh", True)["status"], "applied")
        finally:
            db.close()
        with self.store() as store:
            self.assertEqual(store.recall("fresh"), "new")
            self.assertEqual(store.recall("original"), "kept")


if __name__ == "__main__":
    unittest.main()
