"""Native positive-cache availability acceptance; retained unchanged from issue #27.

A missing cache is safe but misses this performance/availability obligation (#36).
"""
from al.tests.test_runtime_service import setup, pytestmark, _service_tree, _direct_snapshot


def test_watcher_keeps_the_in_memory_snapshot_current(setup):
    first = setup.wait(lambda: _service_tree(setup))
    assert first == _direct_snapshot(setup)
    (setup.repo / "app.py").write_text("print('changed')\n")
    second = setup.wait(lambda: (tree := _service_tree(setup)) != first and tree)
    assert second == _direct_snapshot(setup)
    # AL's own runtime files and excluded directories never change the tree.
    (setup.repo / "node_modules").mkdir(exist_ok=True)
    (setup.repo / "node_modules/x.js").write_text("x")
    assert setup.wait(lambda: _service_tree(setup)) == second == _direct_snapshot(setup)
