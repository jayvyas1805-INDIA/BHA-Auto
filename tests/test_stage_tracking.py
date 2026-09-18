import os
import tempfile
import unittest

from bha import db


class TestStageTracking(unittest.TestCase):
    """Covers db.create_placeholder / set_stage / mark_failed / save_result
    and their interaction -- specifically the requirement that a failure
    preserves the last real stage reached instead of overwriting it, which
    is what the live progress page (frontend/upload.js) relies on to show
    which step actually failed."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.old_path = db.DB_PATH
        db.DB_PATH = self.tmp.name
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_path
        os.unlink(self.tmp.name)

    def _status(self, document_id):
        with db.get_conn() as conn:
            return dict(conn.execute(
                "SELECT status, stage, error FROM documents WHERE id = ?", (document_id,)
            ).fetchone())

    def test_create_placeholder_starts_queued(self):
        doc_id = db.create_placeholder("report.pdf", "hash1")
        s = self._status(doc_id)
        self.assertEqual(s["status"], "processing")
        self.assertEqual(s["stage"], "queued")

    def test_set_stage_updates_only_stage(self):
        doc_id = db.create_placeholder("report.pdf", "hash2")
        db.set_stage(doc_id, "reading_pdf")
        s = self._status(doc_id)
        self.assertEqual(s["stage"], "reading_pdf")
        self.assertEqual(s["status"], "processing")  # unaffected

    def test_failure_preserves_last_real_stage_not_a_generic_failed_value(self):
        """Regression test: mark_failed() used to set stage='failed',
        which throws away exactly the information a progress UI needs
        (which step died). A failure fast enough that nothing polls
        in between (the real-world case: an unreadable PDF fails inside
        read_pdf() almost immediately) must still report stage=
        'reading_pdf' when queried afterwards, not 'failed' and not
        the stale 'queued' default."""
        doc_id = db.create_placeholder("broken.pdf", "hash3")
        db.set_stage(doc_id, "reading_pdf")

        db.mark_failed(doc_id, "No /Root object! - Is this really a PDF?")

        s = self._status(doc_id)
        self.assertEqual(s["status"], "failed")
        self.assertEqual(s["stage"], "reading_pdf")   # NOT "failed", NOT "queued"
        self.assertIn("Root object", s["error"])

    def test_stage_never_regresses_to_queued_on_failure_at_any_point(self):
        """Same guarantee at a later stage, so a UI trusting `stage` on
        failure is correct regardless of when in the pipeline it died."""
        doc_id = db.create_placeholder("report.pdf", "hash4")
        for stage in ("reading_pdf", "locating_chapter", "parsing", "validating"):
            db.set_stage(doc_id, stage)
        db.mark_failed(doc_id, "boom")

        s = self._status(doc_id)
        self.assertEqual(s["stage"], "validating")

    def test_save_result_sets_stage_done_and_clears_prior_error(self):
        """A document that failed once, then succeeds on reprocessing,
        shouldn't still show the old error message once status is done."""
        doc_id = db.create_placeholder("report.pdf", "hash5")
        db.set_stage(doc_id, "reading_pdf")
        db.mark_failed(doc_id, "transient failure")
        self.assertEqual(self._status(doc_id)["error"], "transient failure")

        db.requeue(doc_id)
        db.set_stage(doc_id, "saving")
        db.save_result(doc_id, {"date": "d", "wellbores": []}, validation={"confidence": "high", "warnings": []})

        s = self._status(doc_id)
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["stage"], "done")
        self.assertIsNone(s["error"])   # stale error from the earlier failure is gone

    def test_requeue_resets_stage_and_clears_error(self):
        doc_id = db.create_placeholder("report.pdf", "hash6")
        db.set_stage(doc_id, "parsing")
        db.mark_failed(doc_id, "some error")

        db.requeue(doc_id)

        s = self._status(doc_id)
        self.assertEqual(s["status"], "processing")
        self.assertEqual(s["stage"], "queued")
        self.assertIsNone(s["error"])


if __name__ == "__main__":
    unittest.main()
