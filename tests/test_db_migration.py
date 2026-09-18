import os
import sqlite3
import tempfile
import unittest

from bha import db


class TestDbMigration(unittest.TestCase):
    """Regression test for: sqlite3.OperationalError: no such column:
    confidence -- raised when an older bha.db (created before
    confidence/needs_review/warnings_json existed) is reused with the
    current code. init_db() must upgrade it in place, not just assume
    every database it opens is brand new."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.old_path = db.DB_PATH
        db.DB_PATH = self.tmp.name

    def tearDown(self):
        db.DB_PATH = self.old_path
        os.unlink(self.tmp.name)

    def _create_old_schema_db_with_one_row(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL UNIQUE,
                report_date TEXT,
                chapter_start_page INTEGER,
                chapter_end_page INTEGER,
                status TEXT NOT NULL DEFAULT 'processing',
                raw_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO documents (filename, file_hash, status, created_at, updated_at) "
            "VALUES ('old.pdf', 'abc123', 'done', 'x', 'x')"
        )
        conn.commit()
        conn.close()

    def test_old_database_gets_new_columns_without_losing_data(self):
        self._create_old_schema_db_with_one_row()

        db.init_db()  # should not raise, and should add the missing columns

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT filename, status, confidence, needs_review, warnings_json FROM documents"
            ).fetchone()

        self.assertEqual(row["filename"], "old.pdf")   # original data preserved
        self.assertEqual(row["status"], "done")
        self.assertIsNone(row["confidence"])            # new column, default null
        self.assertEqual(row["needs_review"], 0)         # new column, default 0

    def test_running_init_db_twice_is_a_no_op(self):
        """A second init_db() call (e.g. every app startup) must not error
        on 'duplicate column name' once the migration has already run."""
        db.init_db()
        db.init_db()  # should not raise


if __name__ == "__main__":
    unittest.main()
