"""Three HTTP + SQLite integration tests for SMT Stock.

Run inside the same directory as app.py:
    python -m unittest -v test_integration

Only a temporary SQLite database is used; the working database is untouched.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as smt


class SMTStockIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "integration.sqlite3"
        self.path_patch = patch.object(smt, "DB_PATH", self.db_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.tmp.cleanup)

        smt.app.config.update(TESTING=True)
        smt.init_db()
        self.client = smt.app.test_client()

        with smt.db() as con:
            self.operator_id = con.execute(
                "SELECT id FROM users WHERE card_code='OP001'"
            ).fetchone()["id"]

    def login(self, card_code):
        response = self.client.post("/login", data={"card_code": card_code})
        self.assertEqual(response.status_code, 302)

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["_csrf_token"]

    def create_project(self):
        self.login("ADMIN001")
        response = self.client.post(
            "/projects/new",
            data={
                "_csrf_token": self.csrf(),
                "name": "Інтеграційний проєкт",
                "serial": "IT-2026-001",
                "ipc_class": "2",
                "operator_id": str(self.operator_id),
                "component_id[]": "",
                "new_code[]": "RES0201-100R-IT",
                "component_type[]": "RES",
                "case_size[]": "0201",
                "nominal[]": "100 Ω",
                "specification[]": "±1%",
                "required_qty[]": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        with smt.db() as con:
            project = con.execute(
                "SELECT id FROM projects WHERE serial='IT-2026-001'"
            ).fetchone()
            self.assertIsNotNone(project, "Проєкт не збережено в SQLite")
            component = con.execute(
                "SELECT id FROM components WHERE code='RES0201-100R-IT'"
            ).fetchone()
            self.assertIsNotNone(component, "Компонент не збережено в SQLite")
            return project["id"], component["id"]

    def start_project(self, project_id):
        self.login("OP001")
        response = self.client.post(
            f"/projects/{project_id}/start",
            data={"_csrf_token": self.csrf()},
        )
        self.assertEqual(response.status_code, 302)

    def test_it_01_create_project_and_bom(self):
        """Flask form -> project + component + BOM rows in SQLite."""
        project_id, component_id = self.create_project()
        with smt.db() as con:
            p = con.execute(
                "SELECT status, ipc_class, operator_id FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            bom = con.execute(
                "SELECT required_qty FROM project_components WHERE project_id=? AND component_id=?",
                (project_id, component_id),
            ).fetchone()
        self.assertEqual(p["status"], "created")
        self.assertEqual(p["ipc_class"], 2)
        self.assertEqual(p["operator_id"], self.operator_id)
        self.assertIsNotNone(bom)
        self.assertEqual(bom["required_qty"], 1000)

    def test_it_02_start_creates_automatic_bom_request(self):
        """Operator HTTP request -> status change + auto request + items."""
        project_id, component_id = self.create_project()
        self.start_project(project_id)
        with smt.db() as con:
            project = con.execute(
                "SELECT status FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            requests = con.execute(
                "SELECT id, status FROM requests WHERE project_id=?", (project_id,)
            ).fetchall()
            items = con.execute(
                """SELECT ri.component_id, ri.requested_qty FROM request_items ri
                   JOIN requests r ON r.id=ri.request_id WHERE r.project_id=?""",
                (project_id,),
            ).fetchall()
        self.assertEqual(project["status"], "in_production")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "pending")
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["component_id"], items[0]["requested_qty"]),
                         (component_id, 1000))

    def test_it_03_stock_issue_and_operator_confirmation(self):
        """HTTP receiving + issuing -> SQLite quantities -> HTTP confirmation."""
        project_id, component_id = self.create_project()
        self.start_project(project_id)
        self.login("STORE001")
        received = self.client.post(
            "/packages/receive",
            data={
                "_csrf_token": self.csrf(),
                "component_id": str(component_id),
                "quantity": "1200",
                "label": "REEL-IT-001",
                "manufacturer": "Demo Manufacturer",
                "mpn": "DEMO-RES-0201-100R",
                "package_type": "Рулон",
                "packaging_status": "Придатне",
                "location": "A-01",
            },
        )
        self.assertEqual(received.status_code, 302)
        with smt.db() as con:
            pkg = con.execute(
                "SELECT id, quantity FROM packages WHERE label='REEL-IT-001'"
            ).fetchone()
            item = con.execute(
                """SELECT ri.id, r.id request_id FROM request_items ri
                   JOIN requests r ON r.id=ri.request_id
                   WHERE r.project_id=?""",
                (project_id,),
            ).fetchone()
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg["quantity"], 1200)

        issued = self.client.post(
            f"/requests/{item['request_id']}/issue",
            data={
                "_csrf_token": self.csrf(),
                "item_id": str(item["id"]),
                "package_id": str(pkg["id"]),
                "quantity": "1200",  # full reel by default
            },
        )
        self.assertEqual(issued.status_code, 302)
        with smt.db() as con:
            left = con.execute(
                "SELECT quantity FROM packages WHERE id=?", (pkg["id"],)
            ).fetchone()["quantity"]
            allocation = con.execute(
                "SELECT id, issued_qty, confirmed_at FROM allocations WHERE request_item_id=?",
                (item["id"],),
            ).fetchone()
        self.assertEqual(left, 0)
        self.assertIsNotNone(allocation)
        self.assertEqual(allocation["issued_qty"], 1200)
        self.assertIsNone(allocation["confirmed_at"])

        self.login("OP001")
        confirmed = self.client.post(
            f"/allocations/{allocation['id']}/confirm",
            data={"_csrf_token": self.csrf()},
        )
        self.assertEqual(confirmed.status_code, 302)
        with smt.db() as con:
            stamp = con.execute(
                "SELECT confirmed_at FROM allocations WHERE id=?",
                (allocation["id"],),
            ).fetchone()["confirmed_at"]
        self.assertIsNotNone(stamp)


if __name__ == "__main__":
    unittest.main()
