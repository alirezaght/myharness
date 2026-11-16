import subprocess

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.integration import investigate_with_context
from harness.knowledge.models import ContextRequest
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import query


def test_native_investigation_records_persistent_shared_context(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "service.py").write_text("def calculate():\n    return 3\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    path = tmp_path / "state.db"
    conn = connect(path)
    controller = HarnessController(conn)
    task = controller.create_task("Investigate service calculate")
    result = investigate_with_context(controller, task.id, root)
    assert result["context"]["assertions"]
    assert controller.workspace(task.id).latest_checkpoint("investigation")
    receipt = controller.workspace(task.id).latest_checkpoint("knowledge-context")
    assert receipt
    conn.close()
    conn = connect(path)
    later = query(conn, operator_scope(root), ContextRequest("calculate"))
    assert later.payload["assertions"]
    assert conn.execute("SELECT count(*) FROM knowledge_bundle_pins").fetchone()[0] == 1
    conn.close()
