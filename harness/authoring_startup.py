"""Transaction-owned startup inspection, independent of binding initialization."""


def start_existing(service, host):
    import json

    from harness.authoring_contract import AuthoringConflict
    from harness.authoring_jobs import queue_job

    checkpoint = service.controller.workspace(service.task_id).latest_checkpoint("investigation")
    kind = "generate" if checkpoint else "investigate"
    prior = service.conn.execute(
        "SELECT * FROM authoring_jobs WHERE task_id=? AND project=? AND kind=? "
        "ORDER BY rowid DESC LIMIT 1",
        (service.task_id, service.project, kind),
    ).fetchone()
    result = {"resumed": True, "host": host}
    if prior:
        if prior["host"] != host:
            raise AuthoringConflict(
                "Existing startup job uses "
                + prior["host"]
                + "; resume with that host or explicitly cancel/review the existing job "
                "before requesting another response"
            )
        result["host"] = prior["host"]
        state = prior["state"]
        # A no-op resume must preserve the immutable invocation base.
        inputs = json.loads(prior["input_json"])
        compatible = inputs["base"] == service.store.base()
        if state in {"queued", "running"} and not compatible:
            from harness.authoring_jobs import settle

            settle(
                service,
                dict(prior),
                "stale",
                "Input revision changed before resume",
                settlement=prior["settlement"],
            )
            state = "stale"
        result.update(
            job=prior["id"],
            state=state,
            retry_required=state in {"failed", "cancelled", "stale", "interrupted"},
        )
        if compatible and state in {"queued", "running"}:
            # _mutate must not invalidate an existing immutable invocation on a no-op resume.
            result["_no_bump"] = True
        return result
    result.update(
        job=queue_job(service, kind, host, next_generation=True),
        state="queued",
        retry_required=False,
    )
    return result
