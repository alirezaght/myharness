"""Real Chrome coverage for editable workspace proposals and responsive layouts.

The all-diagram-types design is an explicitly test-owned fixture; it is not seeded
into production task state.
"""

import base64
import json
import os
import time
from pathlib import Path

import pytest

from harness.comments import CommentService
from harness.controller import HarnessController
from harness.dashboard.server import DashboardServer
from harness.db import connect, transaction
from harness.design_models import DesignSection
from tests.execution_helpers import approved_execution, started
from tests.sample_design import COMPONENTS, add_sample_design
from tests.test_dashboard import api, serve, start_task
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


def _screenshot_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", str(tmp_path / "ui-validation")))


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 4.0)


def _design_task(db_path: Path) -> str:
    task_id = start_task(db_path, "callback", "retry")
    conn = connect(db_path)
    try:
        workspace = HarnessController(conn).workspace(task_id)
        question = workspace.ask("callback retry", "Which callback failures should retry?")
        workspace.answer(question.id, "Retry temporary provider failures only.")
        add_sample_design(HarnessController(conn), task_id)
    finally:
        conn.close()
    return task_id


def test_workspace_stationary_pointer_release_flushes_source_update(db_path, tmp_path):
    task_id = _small_design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-stationary-pointer",
            f"{server.url}?task={task_id}&workspace=design&tab=diagram",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        wait(call, "Boolean(document.querySelector('.workspace-node'))")
        node_id = evaluate(call, "document.querySelector('.workspace-node').dataset.nodeId")
        _click_at(call, _box(call, f'[data-node-id="{node_id}"]'))
        field = (
            "[...document.querySelectorAll('.workspace-inspector label')]"
            ".find(node=>node.textContent.startsWith('Component label'))"
            ".querySelector('input')"
        )
        evaluate(
            call,
            "(() => {const input="
            + field
            + ";input.value='Retained during stationary drag';"
            + "input.dispatchEvent(new Event('input',{bubbles:true}));"
            + "input.focus();input.setSelectionRange(4,12);})()",
        )
        base_before = evaluate(call, "workspaceData.task.workspace.base")
        point = _box(call, f'[data-node-id="{node_id}"]')
        _mouse(call, "mousePressed", point)
        assert evaluate(call, "workspaceDragging")

        conn = connect(db_path)
        try:
            workspace = HarnessController(conn).workspace(task_id)
            with transaction(conn):
                workspace.state_design(
                    DesignSection.COMPONENTS,
                    "component New source label: updated during stationary pointer hold",
                )
        finally:
            conn.close()

        wait(
            call,
            "workspaceDragging && workspaceData.task.workspace.base!==" + json.dumps(base_before),
        )
        assert not evaluate(
            call,
            "document.querySelector('.workspace-savebar')?.textContent.includes('Draft is stale')",
        )
        evaluate(call, "source.close()")
        _mouse(call, "mouseReleased", point)

        after_release = evaluate(
            call,
            "(() => {const state=workspaceStates.get("
            + json.dumps(task_id)
            + ");const input="
            + field
            + ";const savebar=document.querySelector('.workspace-savebar');"
            + "const controls=savebar?[...savebar.querySelectorAll('button')]:[];"
            + "return {dragging:workspaceDragging,stale:savebar?.textContent"
            + ".includes('Draft is stale')||false,"
            + "discard:controls.some(button=>"
            + "button.textContent==='Discard draft and reload source'),"
            + "value:input.value,buffer:state.fields["
            + json.dumps(f"node-label:{node_id}")
            + "],selection:state.selection,operations:state.operations.map(op=>op.type)};})()",
        )
        assert after_release["stale"] and after_release["discard"], after_release
        assert after_release["dragging"] is False, after_release
        assert after_release["value"] == "Retained during stationary drag", after_release
        assert after_release["buffer"] == "Retained during stationary drag", after_release
        assert after_release["selection"] == node_id, after_release
        assert "node_position" not in after_release["operations"], after_release


def _small_design_task(db_path: Path) -> str:
    task_id = start_task(db_path, "Five component callback retry map")
    conn = connect(db_path)
    try:
        workspace = HarnessController(conn).workspace(task_id)
        with transaction(conn):
            workspace.state_design(
                DesignSection.COMPONENTS,
                "component Client: sends callback attempts\n"
                "component Callback API: receives provider events\n"
                "component Retry queue: bounds temporary retries\n"
                "component Delivery worker: delivers accepted events\n"
                "component Delivery log: records final outcomes",
            )
            workspace.state_design(
                DesignSection.INTERACTIONS,
                "connect Client -> Callback API: submits event\n"
                "connect Callback API -> Retry queue: queues temporary failure\n"
                "connect Retry queue -> Delivery worker: releases retry\n"
                "connect Delivery worker -> Retry queue: returns retry outcome\n"
                "connect Delivery worker -> Delivery log: records attempt",
            )
    finally:
        conn.close()
    return task_id


def _box(call, selector: str) -> dict:
    return evaluate(
        call,
        "(() => { const r=document.querySelector("
        + json.dumps(selector)
        + ").getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()",
    )


def _mouse(call, kind: str, point: dict, click_count: int = 1) -> None:
    call(
        "Input.dispatchMouseEvent",
        {
            "type": kind,
            "x": point["x"],
            "y": point["y"],
            "button": "left",
            "clickCount": click_count,
        },
    )


def _click_at(call, point: dict, click_count: int = 1) -> None:
    _mouse(call, "mousePressed", point, click_count)
    _mouse(call, "mouseReleased", point, click_count)


def _type(call, value: str) -> None:
    evaluate(call, "document.activeElement.select()")
    call("Input.insertText", {"text": value})


def _press(call, key: str, code: str) -> None:
    call("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": code})
    call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code})


def _button(label: str) -> str:
    return (
        "[...document.querySelectorAll('button')].find(node => node.textContent.trim() === "
        + json.dumps(label)
        + ")"
    )


def test_real_workspace_refinements_and_diagram_proposals_persist_without_mutating_sources(
    db_path, tmp_path
):
    task_id = _design_task(db_path)
    other_task = start_task(db_path, "Independent task buffer")
    original = api  # Keep the HTTP read model available after Chrome saves and reloads.
    conn = connect(db_path)
    try:
        workspace = HarnessController(conn).workspace(task_id)
        entry_before = workspace.design_entries()
    finally:
        conn.close()

    with serve(db_path, task_id) as server:
        source_before = original(server)["task"]["workspace"]["source"]
        with chrome(tmp_path / "chrome", f"{server.url}?task={task_id}", 1280, 800) as call:
            wait(call, "Boolean(document.querySelector('.workspace-task'))")
            evaluate(
                call,
                (
                    "(() => {const theme=document.querySelector('#tasks selec"
                    "t[aria-label=Theme]'); theme.value='light'; theme.dispat"
                    "chEvent(new Event('change',{bubbles:true}));})()"
                ),
            )

            # Refine one exact requirement and one real design entry, then persist them.
            evaluate(call, "document.querySelector('#workspace-tab-requirements').click()")
            evaluate(
                call,
                (
                    "document.querySelector('.workspace-inspector textarea')."
                    "value += '\\nClarify transient-only retry behavior.'; doc"
                    "ument.querySelector('.workspace-inspector textarea').dis"
                    "patchEvent(new Event('input',{bubbles:true}));"
                ),
            )
            evaluate(call, _button("Add refinement to proposal") + ".click()")
            assert evaluate(
                call,
                "workspaceUnappliedFields(workspaceStates.get("
                + json.dumps(task_id)
                + ")).length===0",
            )
            assert "Unapplied editor changes remain" not in evaluate(
                call, "document.querySelector('.workspace-savebar').textContent"
            )
            evaluate(call, "document.querySelector('#workspace-tab-plan').click()")
            evaluate(
                call,
                (
                    "document.querySelector('.workspace-inspector textarea')."
                    "value += '\\nKeep idempotency across attempts.'; document"
                    ".querySelector('.workspace-inspector textarea').dispatch"
                    "Event(new Event('input',{bubbles:true}));"
                ),
            )
            evaluate(call, _button("Add refinement to proposal") + ".click()")
            evaluate(call, "document.querySelector('#workspace-tab-diagram').click()")
            assert evaluate(call, "document.querySelectorAll('.workspace-node').length") >= 10
            diagram_data = api(server)["task"]["diagrams"]["current"]
            assert diagram_data["types"] == [
                "component",
                "sequence",
                "data_flow",
                "state",
                "deployment",
            ]
            directory = _screenshot_dir(tmp_path)
            directory.mkdir(parents=True, exist_ok=True)
            evaluate(
                call,
                (
                    "document.querySelector('[data-view-disclosure=workspace-"
                    "notation]').open=true; document.querySelector('[data-vie"
                    "w-disclosure=workspace-notation]').scrollIntoView()"
                ),
            )
            assert (
                evaluate(
                    call, "[...document.querySelectorAll('#diagrams figure[data-diagram]')].length"
                )
                == 5
            )
            large_image = call(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (directory / "workspace-large-all-diagram-types-1280x800.png").write_bytes(
                base64.b64decode(large_image["data"])
            )
            evaluate(call, "window.scrollTo(0,0)")

            # A genuine double click enters inline label editing. Escape cancels; Enter commits.
            first_node = evaluate(call, "document.querySelector('.workspace-node').dataset.nodeId")
            point = _box(call, ".workspace-node")
            _click_at(call, point)
            _click_at(call, point, click_count=2)
            wait(
                call,
                (
                    "Boolean(document.querySelector('[aria-label=\"Edit compon"
                    "ent label on diagram\"]'))"
                ),
            )
            evaluate(
                call,
                (
                    "document.querySelector('[aria-label=\"Edit component labe"
                    "l on diagram\"]').value='Canceled inline label';"
                ),
            )
            _press(call, "Escape", "Escape")
            assert not evaluate(
                call,
                (
                    "Boolean(document.querySelector('[aria-label=\"Edit compon"
                    "ent label on diagram\"]'))"
                ),
            )

            point = _box(call, f'[data-node-id="{first_node}"]')
            _click_at(call, point)
            _click_at(call, point, click_count=2)
            wait(
                call,
                (
                    "Boolean(document.querySelector('[aria-label=\"Edit compon"
                    "ent label on diagram\"]'))"
                ),
            )
            _type(call, "Browser-owned queue")
            _press(call, "Enter", "Enter")
            assert "Browser-owned queue" in evaluate(
                call, "document.querySelector('.workspace-inspector').textContent"
            )

            # Switching task retains this proposal under its original task identity.
            call(
                "Page.navigate",
                {"url": f"{server.url}?task={other_task}&workspace=design&tab=diagram"},
            )
            wait(
                call,
                (
                    "document.querySelector('.workspace-task')?.textContent.i"
                    "ncludes('Independent task buffer')"
                ),
            )
            assert evaluate(call, "workspaceData.task.id===" + json.dumps(other_task))
            assert (
                evaluate(
                    call, "workspaceStates.get(" + json.dumps(other_task) + ").operations.length"
                )
                == 0
            )
            call(
                "Page.navigate",
                {"url": f"{server.url}?task={task_id}&workspace=design&tab=diagram"},
            )
            wait(
                call,
                "document.querySelector('.workspace-task')?.textContent.includes('callback retry')",
            )
            wait(
                call,
                "workspaceStates.get("
                + json.dumps(task_id)
                + ").operations.some(op=>op.type==='node_label')",
            )
            assert evaluate(call, "workspaceData.task.id===" + json.dumps(task_id))
            assert "Browser-owned queue" in evaluate(
                call, "document.querySelector('.workspace-inspector').textContent"
            )

            # F2 is a keyboard alternative to direct pointer label editing.
            second_node = evaluate(
                call, "document.querySelectorAll('.workspace-node')[1].dataset.nodeId"
            )
            evaluate(call, "document.querySelectorAll('.workspace-node')[1].focus()")
            _press(call, "F2", "F2")
            wait(
                call,
                (
                    "Boolean(document.querySelector('[aria-label=\"Edit compon"
                    "ent label on diagram\"]'))"
                ),
            )
            _press(call, "Escape", "Escape")
            assert not evaluate(
                call,
                "workspaceStates.get("
                + json.dumps(task_id)
                + ").operations.some(op=>op.type==='node_label'&&op.target==="
                + json.dumps(second_node)
                + ")",
            )

            # Pointer drag records a bounded negative position and remains a proposal.
            node_selector = f'[data-node-id="{first_node}"]'
            point = _box(call, node_selector)
            _mouse(call, "mousePressed", point)
            _mouse(call, "mouseMoved", {"x": point["x"] - 150, "y": point["y"] + 30})
            _mouse(call, "mouseReleased", {"x": point["x"] - 150, "y": point["y"] + 30})
            wait(
                call,
                "workspaceStates.get("
                + json.dumps(task_id)
                + ").operations.some(op => op.type === 'node_position')",
            )

            # Keep an input buffer, caret and selection through a real task event/SSE refresh.
            evaluate(
                call,
                (
                    "(() => { const label=[...document.querySelectorAll('.wor"
                    "kspace-inspector label')].find(node=>node.textContent.st"
                    "artsWith('Component label')); const field=label.querySel"
                    "ector('input'); field.value='SSE keeps this draft'; fiel"
                    "d.dispatchEvent(new Event('input',{bubbles:true})); fiel"
                    "d.focus(); field.setSelectionRange(4,13); return true; }"
                    ")()"
                ),
            )
            conn = connect(db_path)
            try:
                HarnessController(conn).workspace(task_id).record_event(
                    "test.sse.refresh", {"fixture": True}
                )
            finally:
                conn.close()
            wait(call, "workspaceData.task.latest_activity?.kind==='test.sse.refresh'")
            preserved = evaluate(
                call,
                (
                    "(() => {const field=document.activeElement; return field"
                    ".value==='SSE keeps this draft' && field.selectionStart="
                    "==4 && field.selectionEnd===13 && field.dataset.workspac"
                    "eFocus==="
                )
                + json.dumps(f"node-label:{first_node}")
                + ";})()",
            )
            assert preserved

            # Add a connection, edit its label, and save the final connection_add overlay.
            _press(call, "Escape", "Escape")
            wait(call, "Boolean(document.querySelector('.workspace-node'))")
            _click_at(call, _box(call, ".workspace-node"))
            evaluate(
                call,
                (
                    "(() => { const labels=[...document.querySelectorAll('.wo"
                    "rkspace-inspector label')]; const from=labels.find(x=>x."
                    "textContent.startsWith('From component')).querySelector("
                    "'select'); const to=labels.find(x=>x.textContent.startsW"
                    "ith('To component')).querySelector('select'); const name"
                    "=labels.find(x=>x.textContent.startsWith('Connection lab"
                    "el')).querySelector('input'); from.selectedIndex=0; to.s"
                    "electedIndex=1; from.dispatchEvent(new Event('change',{b"
                    "ubbles:true})); to.dispatchEvent(new Event('change',{bub"
                    "bles:true})); name.value='retry channel'; name.dispatchE"
                    "vent(new Event('input',{bubbles:true})); })()"
                ),
            )
            evaluate(call, _button("Propose connection") + ".click()")
            evaluate(
                call,
                (
                    "(() => { const label=[...document.querySelectorAll('.wor"
                    "kspace-panel label')].find(item=>item.textContent.starts"
                    "With('Select component or connection')); const select=la"
                    "bel.querySelector('select'); select.value=[...select.opt"
                    "ions].find(option=>option.value.startsWith('proposed:'))"
                    ".value; select.dispatchEvent(new Event('change',{bubbles"
                    ":true})); })()"
                ),
            )
            evaluate(
                call,
                (
                    "(() => { const field=[...document.querySelectorAll('.wor"
                    "kspace-inspector label')].find(x=>x.textContent.startsWi"
                    "th('Connection label')).querySelector('input'); field.va"
                    "lue='retry channel with idempotency'; field.dispatchEven"
                    "t(new Event('input',{bubbles:true})); })()"
                ),
            )
            evaluate(call, _button("Propose connection change") + ".click()")
            evaluate(call, "document.querySelector('#workspace-tab-review').click()")
            assert evaluate(
                call,
                (
                    "document.querySelector('.workspace-panel').textContent.i"
                    "ncludes('retry channel with idempotency')"
                ),
            )
            evaluate(
                call,
                (
                    "window.realProposalFetch=window.fetch; window.proposalRe"
                    "tryIds=[]; window.fetch=(url,options)=>{if(String(url)=="
                    "='/api/workspace/proposals'){window.proposalRetryIds.pus"
                    "h(JSON.parse(options.body).request_id); if(window.propos"
                    "alRetryIds.length===1)return Promise.reject(new Error('f"
                    "ixture network outage'));} return realProposalFetch(url,"
                    "options);};"
                ),
            )
            evaluate(call, _button("Save proposal") + ".click()")
            wait(
                call,
                "document.querySelector('.workspace-savebar').textContent.includes('Save failed:')",
            )
            assert evaluate(
                call, "workspaceStates.get(" + json.dumps(task_id) + ").operations.length >= 4"
            )
            assert evaluate(
                call, "Boolean(workspaceStates.get(" + json.dumps(task_id) + ").retry.request_id)"
            )
            evaluate(call, _button("Retry save") + ".click()")
            wait(
                call,
                (
                    "document.querySelector('.workspace-savebar').textContent"
                    ".includes('Saved proposal')"
                ),
            )
            assert evaluate(
                call, "proposalRetryIds.length===2 && proposalRetryIds[0]===proposalRetryIds[1]"
            )
            evaluate(call, "window.fetch=window.realProposalFetch")
            call("Page.reload", {"ignoreCache": True})
            wait(call, "Boolean(document.querySelector('#workspace-tab-review'))")
            evaluate(call, "document.querySelector('#workspace-tab-review').click()")
            wait(
                call,
                (
                    "document.querySelector('.workspace-panel').textContent.i"
                    "ncludes('retry channel with idempotency')"
                ),
            )
            evaluate(call, "document.querySelector('#workspace-tab-diagram').click()")
            _click_at(call, _box(call, ".workspace-node"))
            evaluate(
                call,
                (
                    "(() => { const labels=[...document.querySelectorAll('.wo"
                    "rkspace-inspector label')]; const from=labels.find(x=>x."
                    "textContent.startsWith('From component')).querySelector("
                    "'select'); const to=labels.find(x=>x.textContent.startsW"
                    "ith('To component')).querySelector('select'); const name"
                    "=labels.find(x=>x.textContent.startsWith('Connection lab"
                    "el')).querySelector('input'); from.selectedIndex=1; to.s"
                    "electedIndex=2; from.dispatchEvent(new Event('change',{b"
                    "ubbles:true})); to.dispatchEvent(new Event('change',{bub"
                    "bles:true})); name.value='temporary connection'; name.di"
                    "spatchEvent(new Event('input',{bubbles:true})); })()"
                ),
            )
            evaluate(call, _button("Propose connection") + ".click()")
            temporary = evaluate(
                call,
                "workspaceStates.get("
                + json.dumps(task_id)
                + (
                    ").operations.find(op=>op.type==='connection_add'&&op.lab"
                    "el==='temporary connection').target"
                ),
            )
            evaluate(
                call,
                (
                    "(() => { const label=[...document.querySelectorAll('.wor"
                    "kspace-panel label')].find(item=>item.textContent.starts"
                    "With('Select component or connection')); const select=la"
                    "bel.querySelector('select'); select.value="
                )
                + json.dumps(temporary)
                + "; select.dispatchEvent(new Event('change',{bubbles:true})); })()",
            )
            evaluate(call, _button("Remove connection in proposal") + ".click()")
            assert evaluate(
                call,
                "!workspaceStates.get("
                + json.dumps(task_id)
                + ").operations.some(op=>op.target==="
                + json.dumps(temporary)
                + ")",
            )
            evaluate(call, "document.querySelector('#workspace-tab-review').click()")
            evaluate(call, _button("Save proposal") + ".click()")
            wait(
                call,
                (
                    "document.querySelector('.workspace-savebar').textContent"
                    ".includes('Saved proposal')"
                ),
            )

        after = api(server)["task"]["workspace"]

    conn = connect(db_path)
    try:
        current_entries = HarnessController(conn).workspace(task_id).design_entries()
        assert current_entries == entry_before
    finally:
        conn.close()
    assert source_before["plan"] == after["source"]["plan"]
    assert len(after["proposals"]) == 2
    saved, removed_batch = after["proposals"]
    assert saved["result"]["graph"]["nodes"]
    assert any(
        edge["label"] == "retry channel with idempotency"
        for edge in saved["result"]["graph"]["edges"]
    )
    assert any(op["type"] == "connection_add" for op in saved["operations"])
    assert any(op["type"] == "node_label" for op in saved["operations"])
    assert any(op["type"] == "node_position" and op["x"] < 0 for op in saved["operations"])
    assert any(op["type"] == "requirement" for op in saved["operations"])
    assert any(op["type"] == "plan" for op in saved["operations"])
    assert all(op["target"] != temporary for op in removed_batch["operations"])


@pytest.mark.parametrize(
    "width,height,theme", [(1280, 800, "light"), (390, 844, "dark"), (320, 844, "light")]
)
def test_workspace_layout_is_inspectable_without_page_overflow(
    db_path, tmp_path, width, height, theme
):
    task_id = _small_design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(tmp_path / f"chrome-{width}", f"{server.url}?task={task_id}", width, height) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        evaluate(call, "document.querySelector('#workspace-tab-diagram').click()")
        assert evaluate(call, "document.querySelectorAll('.workspace-node').length") == 5
        evaluate(
            call,
            (
                "(() => { const label=[...document.querySelectorAll('.wor"
                "kspace-panel label')].find(item=>item.textContent.starts"
                "With('Select component or connection')); const select=la"
                "bel.querySelector('select'); select.selectedIndex=1; sel"
                "ect.dispatchEvent(new Event('change',{bubbles:true})); }"
                ")()"
            ),
        )
        opposite = evaluate(
            call,
            "(() => { const edges=workspaceResult(workspaceStates.get("
            + json.dumps(task_id)
            + (
                ")).graph.edges; const edge=edges.find(item=>edges.some(o"
                "ther=>other.from===item.to&&other.to===item.from));"
            )
            + (
                " if(!edge)return null; const reverse=edges.find(other=>o"
                "ther.from===edge.to&&other.to===edge.from);"
            )
            + " const a=document.querySelector('[data-edge-id=\"'+CSS.escape(edge.id)+'\"]');"
            + " const b=document.querySelector('[data-edge-id=\"'+CSS.escape(reverse.id)+'\"]');"
            + (
                " return {samePath:a.querySelector('path').getAttribute('"
                "d')===b.querySelector('path').getAttribute('d'),"
            )
            + (
                "distance:Math.hypot(Number(a.querySelector('text').getAt"
                "tribute('x'))-Number(b.querySelector('text').getAttribut"
                "e('x')),Number(a.querySelector('text').getAttribute('y')"
                ")-Number(b.querySelector('text').getAttribute('y'))),"
            )
            + "from:edge.from,to:edge.to}; })()",
        )
        assert opposite is not None, (
            "test-owned five-node fixture should include opposite directed connections"
        )
        assert not opposite["samePath"] and opposite["distance"] > 20, opposite
        parallel = evaluate(
            call,
            "(() => { const state=workspaceStates.get("
            + json.dumps(task_id)
            + ");"
            + "const edge=workspaceResult(state).graph.edges[0];"
            + "workspaceEdit(state,{type:'connection_add',target:'proposed:parallel-path-fixture',"
            + "from:edge.from,to:edge.to,label:'Parallel readable-path fixture'});"
            + (
                "const edges=workspaceResult(state).graph.edges.filter(it"
                "em=>item.from===edge.from&&item.to===edge.to);"
            )
            + "const a=document.querySelector('[data-edge-id=\"'+CSS.escape(edges[0].id)+'\"]');"
            + "const b=document.querySelector('[data-edge-id=\"'+CSS.escape(edges[1].id)+'\"]');"
            + (
                "return {samePath:a.querySelector('path').getAttribute('d"
                "')===b.querySelector('path').getAttribute('d'),"
            )
            + (
                "distance:Math.hypot(Number(a.querySelector('text').getAt"
                "tribute('x'))-Number(b.querySelector('text').getAttribut"
                "e('x')),"
            )
            + (
                "Number(a.querySelector('text').getAttribute('y'))-Number"
                "(b.querySelector('text').getAttribute('y'))),"
            )
            + (
                "labels:[a.querySelector('text').textContent,b.querySelec"
                "tor('text').textContent]};})()"
            ),
        )
        assert not parallel["samePath"] and parallel["distance"] > 20, parallel
        evaluate(
            call,
            (
                "(() => {const select=[...document.querySelectorAll('#tas"
                "ks select')].find(x=>x.getAttribute('aria-label')==='The"
                "me'); select.value="
            )
            + json.dumps(theme)
            + "; select.dispatchEvent(new Event('change',{bubbles:true}));})()",
        )
        assert evaluate(call, "document.documentElement.dataset.theme") == theme
        assert evaluate(call, "Boolean(document.querySelector('.workspace-inspector h2'))")
        if width <= 390:
            assert evaluate(
                call,
                (
                    "getComputedStyle(document.querySelector('.workspace-mobi"
                    "le-picker')).display !== 'none'"
                ),
            )
            assert evaluate(
                call,
                (
                    'document.querySelector(\'#tasks select[aria-label="Theme"'
                    "]').getBoundingClientRect().width > 0"
                ),
            )
            swipe = evaluate(
                call,
                (
                    "(() => {const canvas=document.querySelector('.workspace"
                    "-canvas'); const before=canvas.getBoundingClientRect();"
                    " canvas.scrollIntoView({block:'center',inline:'nearest'});"
                    " const rect=canvas.getBoundingClientRect(); const start="
                    "{x:Math.min(rect.right-14,rect.left+260),y:rect.top+14};"
                    " return {before:{top:before.top,bottom:before.bottom},"
                    "viewport:innerHeight,rect:{top:rect.top,bottom:rect.bottom},"
                    "box:{left:rect.left,right:rect.right,clientWidth:canvas.clientWidth,"
                    "scrollWidth:canvas.scrollWidth,scrollLeft:canvas.scrollLeft,"
                    "touchAction:{canvas:getComputedStyle(canvas).touchAction,"
                    "svg:getComputedStyle(canvas.querySelector('svg')).touchAction,"
                    "overflowX:getComputedStyle(canvas).overflowX}},"
                    "start,end:{x:rect.left+18,y:rect.top+14},hit:(()=>{let n=document."
                    "elementFromPoint(start.x,start.y),chain=[]"
                    ";for(let i=0;n&&i<6;i++,n=n.parentElement)"
                    "chain.push({tag:n.tagName,class:n.getAttri"
                    "bute('class'),touch:getComputedStyle(n).to"
                    "uchAction});"
                    "return chain})()};})()"
                ),
            )
            assert swipe["rect"]["top"] >= 0 and swipe["rect"]["bottom"] <= swipe["viewport"], swipe
            assert swipe["box"]["scrollWidth"] > swipe["box"]["clientWidth"], swipe
            assert swipe["hit"] and swipe["hit"][0]["tag"], swipe
            evaluate(
                call,
                "(() => {const canvas=document.querySelecto"
                "r('.workspace-canvas');window.__touchCanvas=canvas;"
                "window.__touchActive=false;window.__touchMutations=[];"
                "window.__touchSseMessages=0;source.addEventListener('message',"
                "()=>window.__touchSseMessages++);"
                "new MutationObserver(records=>{const relevant=records.some(record=>"
                "[...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1&&"
                "(node.matches('.workspace-canvas')||node.querySelector('.workspace-canvas'))));"
                "if(relevant)window.__touchMutations.push({during:window.__touchActive,"
                "sse:window.__touchSseMessages,current:document.querySelector('.workspace-canvas')===canvas,"
                "appTouching:workspaceTouching,pending:workspaceRenderPending});"
                "}).observe(document.querySelector('#main'),{childList:true,subtree:true});"
                "window.__touchRenderFlags=[];window.__priorTouchSse=source.onmessage;"
                "source.onmessage=function(event){const active=window.__touchActive;"
                "const result=window.__priorTouchSse.call(this,event);if(active)"
                "window.__touchRenderFlags.push({appTouching:workspaceTouching,"
                "pending:workspaceRenderPending});return result;};"
                "window.__touchProbe"
                "=[];"
                "document.addEventListener('touchstart',()=>window.__touchActive=true,"
                "{capture:true,passive:true});"
                "for(const type of ['touchend','touchcancel'])document.addEventListener(type,"
                "event=>{if(!event.touches?.length)window.__touchActive=false;},"
                "{capture:true,passive:true});"
                "for(const type of ['touchstart','touchmove','touchend','touchcancel',"
                "'pointerdown','pointermove','pointerup','pointercancel'])"
                "canvas.addEventListener(type,event=>window.__touchProbe.push({"
                "type,target:event.target.tagName,"
                "x:event.clientX??event.touches?.[0]?.clientX,"
                "y:event.clientY??event.touches?.[0]?.clientY,"
                "defaultPrevented:event.defaultPrevented}),"
                "{capture:true,passive:true});return true})()",
            )
            call(
                "Emulation.setTouchEmulationEnabled",
                {"enabled": True, "maxTouchPoints": 1},
            )
            call(
                "Input.synthesizeScrollGesture",
                {
                    "x": swipe["start"]["x"],
                    "y": swipe["start"]["y"],
                    "xDistance": -180,
                    "yDistance": 0,
                    "speed": 800,
                    "preventFling": True,
                    "gestureSourceType": "touch",
                },
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not evaluate(
                call, "document.querySelector('.workspace-canvas').scrollLeft > 0"
            ):
                time.sleep(0.05)
            if not evaluate(call, "document.querySelector('.workspace-canvas').scrollLeft > 0"):
                diagnostic = evaluate(
                    call,
                    "(() => {const c=document.querySelector('.workspace-canvas'),"
                    "r=c.getBoundingClientRect();return {"
                    "scrollLeft:c.scrollLeft,scrollWidth:c.scrollWidth,clientWidth:c.clientWidth,"
                    "touchAction:{canvas:getComputedStyle(c).touchAction,"
                    "svg:getComputedStyle(c.querySelector('svg')).touchAction,"
                    "overflowX:getComputedStyle(c).overflowX,"
                    "svgWidth:getComputedStyle(c.querySelector('svg')).width},"
                    "viewport:{width:innerWidth,height:innerHeight},"
                    "rect:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},"
                    "events:window.__touchProbe,bodyScrollLeft:"
                    "document.scrollingElement.scrollLeft};})()",
                )
                pytest.fail(f"Native touch pan did not scroll the diagram canvas: {diagnostic}")
            touch_events = evaluate(
                call,
                "(() => ({sameCanvas:document.querySelector('.workspace-canvas')"
                "===window.__touchCanvas,mutations:window.__touchMutations,"
                "renderFlags:window.__touchRenderFlags,"
                "events:window.__touchProbe.map(event=>event.type)}))()",
            )
            assert not any(mutation["during"] for mutation in touch_events["mutations"]), (
                touch_events
            )
            assert (
                "touchstart" in touch_events["events"] and "touchmove" in touch_events["events"]
            ), touch_events
            if width == 320:
                assert touch_events["renderFlags"], touch_events
                assert all(
                    flag["appTouching"] and flag["pending"] for flag in touch_events["renderFlags"]
                ), touch_events
        layout = evaluate(
            call,
            (
                "({overflow:document.documentElement.scrollWidth>innerWid"
                "th, inspector:document.querySelector('.workspace-inspect"
                "or').getBoundingClientRect().top, canvas:document.queryS"
                "elector('.workspace-canvas').getBoundingClientRect().top"
                ", width:innerWidth})"
            ),
        )
        assert not layout["overflow"], layout
        if width <= 650:
            assert layout["inspector"] >= layout["canvas"], layout
        else:
            assert layout["inspector"] < layout["canvas"], layout
        if width in {1280, 390}:
            directory = _screenshot_dir(tmp_path)
            directory.mkdir(parents=True, exist_ok=True)
            image = call(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            name = (
                "workspace-small-five-node-1280x800.png"
                if width == 1280
                else f"workspace-{theme}-{width}x{height}.png"
            )
            (directory / name).write_bytes(base64.b64decode(image["data"]))


def test_workspace_remains_usable_when_browser_storage_is_blocked(db_path, tmp_path):
    task_id = _design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-storage-blocked",
            f"{server.url}?task={task_id}&workspace=design&tab=requirements",
            1280,
            800,
        ) as call,
    ):
        call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(Storage.prototype,'getItem',{confi"
                    "gurable:true,value(){throw new DOMException('blocked','S"
                    "ecurityError')}}); Object.defineProperty(Storage.prototy"
                    "pe,'setItem',{configurable:true,value(){throw new DOMExc"
                    "eption('blocked','SecurityError')}});"
                )
            },
        )
        call("Page.reload", {"ignoreCache": True})
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        assert evaluate(call, "document.documentElement.dataset.theme") == "system"
        evaluate(
            call,
            (
                "(() => {const field=document.querySelector('.workspace-i"
                "nspector textarea'); field.value += '\\nKeep this proposa"
                "l in page memory.'; field.dispatchEvent(new Event('input"
                "',{bubbles:true}));})()"
            ),
        )
        evaluate(call, _button("Add refinement to proposal") + ".click()")
        assert evaluate(
            call, "workspaceStates.get(" + json.dumps(task_id) + ").operations.length === 1"
        )


def test_lost_save_ack_reloads_as_unknown_and_retries_same_proposal(db_path, tmp_path):
    task_id = _design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-lost-ack",
            f"{server.url}?task={task_id}&workspace=design&tab=requirements",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        evaluate(
            call,
            (
                "document.querySelector('.workspace-inspector textarea')."
                "value='Restore exact API behavior.'; document.querySelec"
                "tor('.workspace-inspector textarea').dispatchEvent(new E"
                "vent('input',{bubbles:true}));"
            ),
        )
        evaluate(call, _button("Add refinement to proposal") + ".click()")
        evaluate(
            call,
            (
                "window.realProposalFetch=window.fetch; window.proposalRe"
                "questId=null; window.proposalAcknowledged=false; window."
                "fetch=(url,options)=>{if(String(url)==='/api/workspace/p"
                "roposals'){window.proposalRequestId=JSON.parse(options.b"
                "ody).request_id; window.realProposalFetch(url,options).t"
                "hen(()=>window.proposalAcknowledged=true); return new Pr"
                "omise(()=>{});} return window.realProposalFetch(url,opti"
                "ons);};"
            ),
        )
        evaluate(call, _button("Save proposal") + ".click()")
        wait(
            call,
            (
                "window.proposalAcknowledged && document.querySelector('."
                "workspace-savebar').textContent.includes('Saving proposa"
                "l')"
            ),
        )
        request_id = evaluate(call, "window.proposalRequestId")
        call("Page.reload", {"ignoreCache": True})
        wait(call, "Boolean(document.querySelector('#workspace-tab-review'))")
        assert "Save outcome unknown" in evaluate(
            call, "document.querySelector('.workspace-savebar').textContent"
        )
        assert (
            evaluate(call, "workspaceStates.get(" + json.dumps(task_id) + ").retry.request_id")
            == request_id
        )
        wait(call, _button("Retry save") + " !== null")
        evaluate(call, _button("Retry save") + ".click()")
        wait(
            call,
            "document.querySelector('.workspace-savebar').textContent.includes('Saved proposal')",
        )
        assert len(api(server)["task"]["workspace"]["proposals"]) == 1


def test_workspace_sse_preserves_source_question_focus_and_details(db_path, tmp_path):
    task_id = _small_design_task(db_path)
    conn = connect(db_path)
    try:
        HarnessController(conn).workspace(task_id).ask(
            "callback handling", "Which callback result should be recorded as final?"
        )
    finally:
        conn.close()

    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-focus-records",
            f"{server.url}?task={task_id}&workspace=design&tab=requirements",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        evaluate(call, "document.querySelector('.workspace-inspector details summary').click()")
        evaluate(call, "document.querySelector('.workspace-question details summary').click()")
        card = evaluate(
            call,
            (
                "(() => {const item=document.querySelector('.workspace-so"
                "urce-card'); item.focus(); return item.textContent;})()"
            ),
        )
        conn = connect(db_path)
        try:
            HarnessController(conn).workspace(task_id).record_event("test.sse.focus-records", {})
        finally:
            conn.close()
        wait(call, "workspaceData.task.latest_activity?.kind==='test.sse.focus-records'")
        records = evaluate(
            call,
            (
                "(() => ({sourceFocused:document.activeElement.classList."
                "contains('workspace-source-card') && document.activeElem"
                "ent.textContent.includes("
            )
            + json.dumps(card.split("\n")[0])
            + (
                "),sourceDetails:[...document.querySelectorAll('.workspac"
                "e-inspector details')].some(item=>item.open),questionDet"
                "ails:[...document.querySelectorAll('.workspace-question "
                "details')].some(item=>item.open)}))()"
            ),
        )

        evaluate(call, "document.querySelector('#workspace-tab-diagram').click()")
        wait(call, "Boolean(document.querySelector('.workspace-edge'))")
        edge_id = evaluate(call, "document.querySelector('.workspace-edge').dataset.edgeId")
        evaluate(call, "document.querySelector('.workspace-edge').focus()")
        _press(call, "Enter", "Enter")
        wait(
            call,
            "workspaceStates.get(" + json.dumps(task_id) + ").selection===" + json.dumps(edge_id),
        )
        keyboard_focus = evaluate(
            call, "document.activeElement.dataset.edgeId===" + json.dumps(edge_id)
        )
        evaluate(
            call,
            (
                "[...document.querySelectorAll('[data-view-disclosure=wor"
                "kspace-notation] > summary')][0].click()"
            ),
        )
        evaluate(
            call,
            "document.querySelector('.workspace-edge[data-edge-id="
            + json.dumps(edge_id)
            + "]').focus()",
        )
        conn = connect(db_path)
        try:
            HarnessController(conn).workspace(task_id).record_event("test.sse.focus-edge", {})
        finally:
            conn.close()
        wait(call, "workspaceData.task.latest_activity?.kind==='test.sse.focus-edge'")
        edge_refresh = evaluate(
            call,
            "({edgeFocused:document.activeElement.dataset.edgeId==="
            + json.dumps(edge_id)
            + (
                ",notationOpen:document.querySelector('[data-view-disclos"
                "ure=workspace-notation]').open})"
            ),
        )
        assert (
            records["sourceFocused"] and records["sourceDetails"] and records["questionDetails"]
        ), records
        assert keyboard_focus and edge_refresh["edgeFocused"] and edge_refresh["notationOpen"], {
            "keyboard_focus": keyboard_focus,
            **edge_refresh,
        }


def test_workspace_action_focus_and_unapplied_drafts_survive_sse(db_path, tmp_path):
    task_id = _design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-action-focus",
            f"{server.url}?task={task_id}&workspace=design&tab=requirements",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")

        def event(kind: str) -> None:
            conn = connect(db_path)
            try:
                HarnessController(conn).workspace(task_id).record_event(kind, {})
            finally:
                conn.close()
            wait(call, "workspaceData.task.latest_activity?.kind===" + json.dumps(kind))

        evaluate(
            call,
            "(() => {const input=document.querySelector('.workspace-inspector textarea');"
            "input.value='Requirement refinement A';"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            "document.querySelector('.workspace-inspector button').click();})()",
        )
        assert evaluate(
            call,
            "workspaceUnappliedFields(workspaceStates.get(" + json.dumps(task_id) + ")).length===0",
        )
        assert "Unapplied editor changes remain" not in evaluate(
            call, "document.querySelector('.workspace-savebar').textContent"
        )
        evaluate(
            call,
            "(() => {const input=document.querySelector('.workspace-inspector textarea');"
            "input.value='Requirement refinement B stays local\\n'.repeat(30);"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            "input.scrollTop=input.scrollHeight;input.setSelectionRange(8,15);})()",
        )
        editor_before = evaluate(
            call,
            "(() => {const input=document.querySelector('.workspace-inspector textarea');"
            "return {scrollTop:input.scrollTop,scrollHeight:input.scrollHeight,"
            "start:input.selectionStart,"
            "end:input.selectionEnd};})()",
        )
        add_refinement = _button("Add refinement to proposal")
        evaluate(call, add_refinement + ".focus()")
        event("test.sse.action-add-refinement")
        add_focus = evaluate(call, "document.activeElement.textContent.trim()")
        editor_after = evaluate(
            call,
            "(() => {const input=document.querySelector('.workspace-inspector textarea');"
            "return {scrollTop:input.scrollTop,start:input.selectionStart,"
            "end:input.selectionEnd};})()",
        )

        evaluate(call, "document.querySelector('#workspace-tab-diagram').click()")
        wait(call, "Boolean(document.querySelector('.workspace-node'))")
        _click_at(call, _box(call, ".workspace-node"))
        label_input = (
            "[...document.querySelectorAll('.workspace-inspector label')]"
            ".find(item=>item.textContent.startsWith('Component label')).querySelector('input')"
        )
        evaluate(
            call,
            "(() => {const input=" + label_input + ";input.value='Action focus label';"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            + _button("Propose label")
            + ".click();})()",
        )
        label_button = _button("Propose label")
        evaluate(call, label_button + ".focus()")
        event("test.sse.action-propose-label")
        label_focus = evaluate(call, "document.activeElement.textContent.trim()")

        evaluate(
            call,
            "(() => {const labels=[...document.querySelectorAll('.workspace-inspector label')];"
            "const from=labels.find(item=>item.textContent.startsWith('From component'))"
            ".querySelector('select');const to=labels.find(item=>"
            "item.textContent.startsWith('To component')).querySelector('select');"
            "const name=labels.find(item=>item.textContent.startsWith('Connection label'))"
            ".querySelector('input');from.selectedIndex=0;to.selectedIndex=1;"
            "from.dispatchEvent(new Event('change',{bubbles:true}));"
            "to.dispatchEvent(new Event('change',{bubbles:true}));"
            "name.value='Action focus connection';"
            "name.dispatchEvent(new Event('input',{bubbles:true}));"
            + _button("Propose connection")
            + ".click();})()",
        )
        assert evaluate(
            call,
            "workspaceUnappliedFields(workspaceStates.get("
            + json.dumps(task_id)
            + ")).every(key=>!key.startsWith('edge-'))",
        )
        connection_button = _button("Propose connection")
        evaluate(call, connection_button + ".focus()")
        event("test.sse.action-propose-connection")
        connection_focus = evaluate(call, "document.activeElement.textContent.trim()")

        theme = "document.querySelector('#tasks select[aria-label=Theme]')"
        evaluate(call, theme + ".focus()")
        event("test.sse.action-theme")
        theme_focus = evaluate(
            call,
            "document.activeElement.getAttribute('aria-label')",
        )

        evaluate(call, "document.querySelector('#workspace-tab-review').click()")
        save_button = _button("Save proposal")
        evaluate(call, save_button + ".focus()")
        event("test.sse.action-save")
        save_focus = evaluate(call, "document.activeElement.textContent.trim()")
        evaluate(call, save_button + ".click()")
        wait(call, "workspaceStates.get(" + json.dumps(task_id) + ").status==='Saved proposal'")

        call("Page.reload", {"ignoreCache": True})
        wait(
            call,
            "typeof workspaceData!=='undefined' && workspaceData?.task?.id==="
            + json.dumps(task_id)
            + " && Boolean(document.querySelector('#workspace-tab-review'))",
        )
        local = evaluate(
            call,
            "(() => {const state=workspaceStates.get("
            + json.dumps(task_id)
            + ");return {fields:state.fields,pending:workspaceUnappliedFields(state),"
            "status:document.querySelector('.workspace-savebar').textContent};})()",
        )
        proposals = api(server)["task"]["workspace"]["proposals"]
        saved = proposals[-1]
        requirement_ops = [op for op in saved["operations"] if op["type"] == "requirement"]
        assert (
            add_focus == "Add refinement to proposal"
            and save_focus == "Save proposal"
            and label_focus == "Propose label"
            and connection_focus == "Propose connection"
            and theme_focus == "Theme"
        ), {
            "add_refinement": add_focus,
            "save": save_focus,
            "label": label_focus,
            "connection": connection_focus,
            "theme": theme_focus,
        }
        assert editor_before["scrollTop"] > 0 and editor_after == {
            "scrollTop": editor_before["scrollTop"],
            "start": editor_before["start"],
            "end": editor_before["end"],
        }, {
            "before": editor_before,
            "after": editor_after,
        }
        assert len(requirement_ops) == 1, saved["operations"]
        assert requirement_ops[0]["text"] == "Requirement refinement A", requirement_ops
        assert "Requirement refinement B stays local" in str(local["fields"]), local
        assert local["pending"] == ["refine:request"], local
        assert "unapplied" in local["status"].lower(), local
        directory = _screenshot_dir(tmp_path)
        directory.mkdir(parents=True, exist_ok=True)
        image = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (directory / "workspace-unapplied-editor.png").write_bytes(base64.b64decode(image["data"]))


def test_existing_change_and_comment_links_open_their_workspace_content(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution_id)
    add_sample_design(controller, task_id)
    comments = CommentService(controller, task_id)
    comments.diagrams.sync()
    comments.add(
        1, "component:web-dashboard", "clarification", "Older source notation remains linked."
    )
    with transaction(controller.conn):
        controller.workspace(task_id).state_design(
            DesignSection.COMPONENTS,
            COMPONENTS + "\ncomponent New component: records an additive revision",
        )
    comments.diagrams.sync()

    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-links",
            f"{server.url}?task={task_id}&workspace=run&tab=overview",
            1280,
            800,
        ) as call,
    ):
        wait(
            call,
            (
                "Boolean([...document.querySelectorAll('#execution a')].f"
                "ind(link=>link.textContent==='View logical change diagra"
                "m'))"
            ),
        )
        evaluate(
            call,
            (
                "[...document.querySelectorAll('#execution a')].find(link"
                "=>link.textContent==='View logical change diagram').clic"
                "k()"
            ),
        )
        wait(
            call,
            "workspaceStates.get(" + json.dumps(task_id) + ").tab==='diagram'",
        )
        assert evaluate(
            call, "document.querySelector('[data-view-disclosure=workspace-notation]').open"
        )

        call(
            "Page.navigate",
            {"url": f"{server.url}?task={task_id}&workspace=run&tab=history&details=records"},
        )
        wait(call, "Boolean(document.querySelector('#comments a[href*=version]'))")
        href = evaluate(
            call, "document.querySelector('#comments a[href*=version]').getAttribute('href')"
        )
        from urllib.parse import unquote

        assert "version=1" in href and "component:web-dashboard" in unquote(href)
        evaluate(call, "document.querySelector('#comments a[href*=version]').click()")
        wait(
            call,
            "workspaceStates.get(" + json.dumps(task_id) + ").tab==='diagram'",
        )
        assert evaluate(
            call, "document.querySelector('[data-view-disclosure=workspace-notation]').open"
        )
        assert "version 1" in evaluate(call, "document.querySelector('#diagrams').textContent")
        evaluate(call, "document.querySelector('#workspace-tab-spec').click(); true")
        wait(call, "new URLSearchParams(location.search).get('tab')==='spec'")
        call("Page.navigate", {"url": evaluate(call, "location.href")})
        wait(
            call,
            "document.querySelector('#workspace-tab-spec')?.getAttribute('aria-selected')==='true'",
        )


def test_workspace_retains_scroll_and_draft_when_source_becomes_stale(db_path, tmp_path):
    task_id = _design_task(db_path)
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "chrome-stale",
            f"{server.url}?task={task_id}&workspace=design&tab=diagram",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.workspace-task'))")
        field = (
            "[...document.querySelectorAll('.workspace-inspector label')]"
            ".find(node=>node.textContent.startsWith('Component label'))"
            ".querySelector('input')"
        )
        input_script = (
            f"(() => {{const input={field}; input.value='Unsaved local alias'; "
            "input.dispatchEvent(new Event('input',{bubbles:true})); "
            "input.focus({preventScroll:true}); input.setSelectionRange(3,12); "
            "document.querySelector('.workspace-inspector').scrollTop=90; "
            "window.scrollTo(0,320);})()"
        )
        evaluate(
            call,
            input_script,
        )
        before = evaluate(
            call,
            "({y:scrollY, inspector:document.querySelector('.workspace-inspector').scrollTop})",
        )

        conn = connect(db_path)
        try:
            workspace = HarnessController(conn).workspace(task_id)
            with transaction(conn):
                workspace.state_design(
                    DesignSection.COMPONENTS,
                    "component New source label: source changed while editing",
                )
        finally:
            conn.close()

        wait(
            call,
            "document.querySelector('.workspace-savebar')?.textContent.includes('Draft is stale')",
        )
        retained = evaluate(
            call,
            f"(() => {{const input={field}; return {{value:input.value,"
            "active:document.activeElement===input,start:input.selectionStart,"
            "end:input.selectionEnd,disabled:input.disabled,readOnly:input.readOnly,y:scrollY,"
            "inspector:document.querySelector('.workspace-inspector').scrollTop,"
            "discard:[...document.querySelectorAll('button')].some(button=>"
            "button.textContent==='Discard draft and reload source')}})()",
        )
        assert retained["value"] == "Unsaved local alias"
        assert retained["active"] and retained["start"] == 3 and retained["end"] == 12
        assert retained["disabled"] is False and retained["readOnly"] and retained["discard"]
        assert retained["y"] == before["y"] and retained["inspector"] == before["inspector"]
        assert evaluate(
            call, "workspaceStates.get(" + json.dumps(task_id) + ").operations.length===0"
        )

        component_id = evaluate(call, "document.querySelector('.workspace-node').dataset.nodeId")
        _press(call, "Enter", "Enter")
        assert evaluate(
            call,
            "workspaceStates.get("
            + json.dumps(task_id)
            + ").fields["
            + json.dumps(f"node-label:{component_id}")
            + "]==='Unsaved local alias'",
        )
        conn = connect(db_path)
        try:
            HarnessController(conn).workspace(task_id).record_event("test.sse.stale-label", {})
        finally:
            conn.close()
        wait(call, "workspaceData.task.latest_activity?.kind==='test.sse.stale-label'")
        stale_label = evaluate(
            call,
            "(() => {const input="
            + field
            + "; const state=workspaceStates.get("
            + json.dumps(task_id)
            + (
                "); return {value:input.value,active:document.activeEleme"
                "nt===input,start:input.selectionStart,end:input.selectio"
                "nEnd,"
            )
            + "readOnly:input.readOnly,disabled:input.disabled,buffer:state.fields["
            + json.dumps(f"node-label:{component_id}")
            + "],saveUnavailable:![...document.querySelectorAll('.workspace-savebar button')]"
            + ".some(item=>/^(Save proposal|Retry save)$/.test(item.textContent)"
            + "&&!item.disabled)};})()",
        )
        assert stale_label == {
            "value": "Unsaved local alias",
            "active": True,
            "start": 3,
            "end": 12,
            "readOnly": True,
            "disabled": False,
            "buffer": "Unsaved local alias",
            "saveUnavailable": True,
        }, stale_label

        evaluate(call, _button("Discard draft and reload source") + ".click()")
        assert "New source label" in evaluate(
            call, "document.querySelector('.workspace-panel').textContent"
        )
        assert not evaluate(
            call, "document.querySelector('.workspace-savebar')?.textContent.includes('stale')"
        )

        # Inline editing obeys the same stale guard: Enter and a later SSE do not drop its buffer.
        node_id = evaluate(call, "document.querySelector('.workspace-node').dataset.nodeId")
        _click_at(call, _box(call, f'[data-node-id="{node_id}"]'))
        _click_at(call, _box(call, f'[data-node-id="{node_id}"]'), click_count=2)
        wait(
            call,
            "Boolean(document.querySelector('[aria-label=\"Edit component label on diagram\"]'))",
        )
        evaluate(
            call,
            "(() => {const input=document.querySelector("
            "'[aria-label=\"Edit component label on diagram\"]'); "
            "input.value='Inline draft after stale source'; "
            "input.dispatchEvent(new Event('input',{bubbles:true})); "
            "input.focus();input.setSelectionRange(7,12);})()",
        )
        conn = connect(db_path)
        try:
            workspace = HarnessController(conn).workspace(task_id)
            with transaction(conn):
                workspace.state_design(
                    DesignSection.COMPONENTS,
                    "component Another source label: second source change while editing",
                )
        finally:
            conn.close()
        wait(
            call,
            "document.querySelector('.workspace-savebar')?.textContent.includes('Draft is stale')",
        )
        _press(call, "Enter", "Enter")
        inline_key = f"inline-label:{node_id}"
        inline_buffer = evaluate(
            call,
            "workspaceStates.get("
            + json.dumps(task_id)
            + ").fields["
            + json.dumps(inline_key)
            + "]",
        )
        assert inline_buffer == "Inline draft after stale source"
        conn = connect(db_path)
        try:
            HarnessController(conn).workspace(task_id).record_event("test.sse.stale-inline", {})
        finally:
            conn.close()
        wait(call, "workspaceData.task.latest_activity?.kind==='test.sse.stale-inline'")
        inline_state = evaluate(
            call,
            (
                "(() => {const input=document.querySelector('[aria-label="
                '"Edit component label on diagram"]\');'
            )
            + "const state=workspaceStates.get("
            + json.dumps(task_id)
            + "); return {value:input.value,"
            + (
                "active:document.activeElement===input,start:input.select"
                "ionStart,end:input.selectionEnd,"
            )
            + "readOnly:input.readOnly,buffer:state.fields["
            + json.dumps(inline_key)
            + "],"
            + "saveUnavailable:![...document.querySelectorAll('.workspace-savebar button')]"
            + ".some(item=>/^(Save proposal|Retry save)$/.test(item.textContent)"
            + "&&!item.disabled)};})()",
        )
        assert inline_state == {
            "value": "Inline draft after stale source",
            "active": True,
            "start": 7,
            "end": 12,
            "readOnly": True,
            "buffer": "Inline draft after stale source",
            "saveUnavailable": True,
        }, inline_state

    conn = connect(db_path)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM workspace_proposals WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()
