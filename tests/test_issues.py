import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

_database = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + _database.name + "/issues-test.db"
os.environ["SECRET_KEY"] = "issue-tests"

from app import app
from models import Issue, IssueEntry, db


class IssueTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def create_issue(self, **overrides):
        data = dict(title="Search issue", author="Alice", body="First line\nSecond line")
        data.update(overrides)
        return self.client.post("/issues/new", data=data)

    def test_create_reply_close_reopen_and_history_survive_connection_restart(self):
        response = self.create_issue()
        self.assertEqual(response.status_code, 302)
        issue = Issue.query.one()
        issue_id = issue.id
        self.assertEqual(issue.status, "open")
        self.assertEqual(self.client.get(response.location).status_code, 200)
        response = self.client.post(f"/issues/{issue_id}/replies", data={
            "reply-author": "Bob", "reply-body": "I can reproduce this.\nInvestigating.",
        })
        self.assertEqual(response.status_code, 302)
        for status in ("closed", "closed", "open"):
            response = self.client.post(f"/issues/{issue_id}/status", data={
                "status-author": "Alice", "status-status": status,
            })
            self.assertEqual(response.status_code, 302)
        db.session.remove()
        db.engine.dispose()
        issue = db.session.get(Issue, issue_id)
        self.assertEqual(issue.status, "open")
        self.assertEqual(len(issue.entries), 3)
        self.assertEqual([entry.kind for entry in issue.entries], ["reply", "status", "status"])
        self.assertEqual(issue.entries[0].author, "Bob")
        self.assertIn("Investigating.", issue.entries[0].body)
        self.assertEqual(issue.entries[1].body, "Closed this issue.")
        self.assertEqual(issue.entries[2].body, "Reopened this issue.")

    def test_filters_counts_pagination_and_navigation(self):
        self.create_issue(title="Open item")
        self.create_issue(title="Closed item")
        issue = Issue.query.filter_by(title="Closed item").one()
        issue.status = "closed"
        db.session.commit()
        page = self.client.get("/issues")
        self.assertIn(b"Open item", page.data)
        self.assertNotIn(b"Closed item", page.data)
        self.assertLess(page.data.index(b">SDRF Export</a>"), page.data.index(b">Issues</a>"))
        closed = self.client.get("/issues?status=closed")
        self.assertIn(b"Closed item", closed.data)
        self.assertNotIn(b"Open item", closed.data)
        all_issues = self.client.get("/issues?status=all")
        self.assertIn(b"Open item", all_issues.data)
        self.assertIn(b"Closed item", all_issues.data)
        self.assertIn(b"All (2)", all_issues.data)
        self.assertEqual(self.client.get("/issues?status=unknown").status_code, 400)
        db.session.add_all(Issue(title=f"Extra {i}", author="A", body="Description") for i in range(31))
        db.session.commit()
        page = self.client.get("/issues?page=2")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Page 2 of 2", page.data)

    def test_required_fields_and_status_validation_do_not_write(self):
        for overrides in (dict(title="  "), dict(author=" "), dict(body="\n "), dict(title="x" * 201)):
            self.create_issue(**overrides)
        self.assertEqual(Issue.query.count(), 0)
        self.create_issue()
        issue_id = Issue.query.one().id
        reply = self.client.post(f"/issues/{issue_id}/replies", data={
            "reply-author": "Bob", "reply-body": " ",
        })
        self.assertEqual(reply.status_code, 422)
        status = self.client.post(f"/issues/{issue_id}/status", data={
            "status-author": "Bob", "status-status": "deleted",
        })
        self.assertEqual(status.status_code, 422)
        self.assertEqual(IssueEntry.query.count(), 0)
        self.assertEqual(db.session.get(Issue, issue_id).status, "open")

    def test_escape_user_content_and_reply_to_closed_issue(self):
        self.create_issue(title="<script>alert(1)</script>", body="<img src=x onerror=alert(1)>")
        issue = Issue.query.one()
        issue.status = "closed"
        db.session.commit()
        response = self.client.post(f"/issues/{issue.id}/replies", data={
            "reply-author": "<b>Bob</b>", "reply-body": "<script>bad()</script>",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>bad()", response.data)
        self.assertIn(b"&lt;script&gt;bad()", response.data)
        self.assertIn(b"&lt;b&gt;Bob&lt;/b&gt;", response.data)
        self.assertNotIn(b"<img src=x", response.data)

    def test_csrf_and_missing_issue(self):
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            self.assertEqual(self.create_issue().status_code, 400)
            self.assertEqual(Issue.query.count(), 0)
        finally:
            app.config["WTF_CSRF_ENABLED"] = False
        for path in ("/issues/999/replies", "/issues/999/status"):
            self.assertEqual(self.client.post(path).status_code, 404)
        self.assertEqual(self.client.get("/issues/999").status_code, 404)

    def test_existing_database_upgrade_and_fresh_initialization(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            existing_path = Path(directory) / "existing.db"
            with sqlite3.connect(existing_path) as connection:
                # Model an installation from before the issue tables existed.
                schema = (root / "schema.sql").read_text()
                connection.executescript("CREATE TABLE project (" + schema.split("CREATE TABLE project (", 1)[1])
                connection.execute(
                    "INSERT INTO project (code, name, description, user_initials) VALUES (?, ?, ?, ?)",
                    ("KEEP", "Existing project", "Keep this data", "AB"),
                )
            environment = dict(os.environ, DATABASE_URL="sqlite:///" + str(existing_path))
            result = subprocess.run([
                sys.executable, "-c",
                "from app import app; from models import db, Project, Issue, IssueEntry; "
                "ctx = app.app_context(); ctx.push(); "
                "assert db.session.get(Project, 'KEEP').name == 'Existing project'; "
                "assert Issue.query.count() == 0; assert IssueEntry.query.count() == 0",
            ], env=environment, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            environment["DATABASE_URL"] = "sqlite:///" + str(Path(directory) / "fresh.db")
            result = subprocess.run([
                sys.executable, "-m", "flask", "--app", "app", "init-db",
            ], env=environment, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
