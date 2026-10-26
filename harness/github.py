"""GitHub PRs, aggregate Actions runs and latest environment deployment observations."""

import hashlib
import json
from urllib.parse import quote

from harness.github_http import GitHubHTTP


def review_request(value):
    return {
        "iid": value["number"],
        "sha": value["head"]["sha"],
        "source_branch": value["head"]["ref"],
        "target_branch": value["base"]["ref"],
        "state": "merged"
        if value.get("merged_at") or value.get("merged")
        else ("opened" if value["state"] == "open" else "closed"),
        "web_url": value["html_url"],
        "description": value.get("body"),
    }


def status(value):
    if value.get("status") != "completed":
        return "running"
    return {
        "success": "success",
        "neutral": "success",
        "skipped": "success",
        "failure": "failed",
        "timed_out": "failed",
        "cancelled": "failed",
    }.get(value.get("conclusion"), "failed")


class GitHubClient(GitHubHTTP):
    def __init__(self, config, credential_provider=None):
        super().__init__(config, credential_provider)
        self.groups, self.environments = {}, {}

    def branch(self, name):
        value = self.raw_get("/branches/" + quote(name, safe=""))
        return value | {"commit": {"id": value["commit"]["sha"]}}

    def create_branch(self, name, sha):
        value = self.request("POST", "/git/refs", {"ref": "refs/heads/" + name, "sha": sha})[0]
        return {"name": name, "commit": {"id": value["object"]["sha"]}}

    def create_merge_request(self, source, target, title, marker):
        return review_request(
            self.request(
                "POST", "/pulls", {"head": source, "base": target, "title": title, "body": marker}
            )[0]
        )

    def pipeline(self, sha):
        latest = {}
        for run in self.pages("/actions/runs", "workflow_runs", head_sha=sha):
            if run.get("head_sha") != sha:
                continue
            key = run["workflow_id"]
            if key not in latest or (
                run.get("run_number", run["id"]),
                run.get("run_attempt", 1),
            ) > (
                latest[key].get("run_number", latest[key]["id"]),
                latest[key].get("run_attempt", 1),
            ):
                latest[key] = run
        runs = sorted(latest.values(), key=lambda r: r["id"])
        if not runs:
            return None
        identity = [(r["id"], r.get("run_attempt", 1)) for r in runs]
        pid = int(hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:15], 16)
        states = {status(r) for r in runs}
        state = next((s for s in ("failed", "blocked", "running") if s in states), "success")
        value = {
            "id": pid,
            "sha": sha,
            "status": state,
            "workflows": runs,
            "web_url": self.endpoint + "/" + self.config["project"] + "/actions",
        }
        self.groups[pid] = value
        return value

    def group(self, pid):
        previous = self.groups[pid]
        current = self.pipeline(previous["sha"])
        if not current or current["id"] != pid:
            raise ValueError("GitHub workflow attempts changed during observation")
        return current

    def jobs(self, pid):
        rows = []
        for run in self.group(pid)["workflows"]:
            path = f"/actions/runs/{run['id']}/attempts/{run.get('run_attempt', 1)}/jobs"
            for job in self.pages(path, "jobs"):
                conclusion = job.get("conclusion")
                reason = {"failure": "script_failure", "timed_out": "stuck_or_timeout_failure"}.get(
                    conclusion, "unknown_failure"
                )
                rows.append(
                    job
                    | {
                        "status": status(job),
                        "failure_reason": reason,
                        "web_url": job.get("html_url"),
                    }
                )
        return rows

    def environment(self, identifier):
        name = self.environments[identifier]["name"]
        raw = self.raw_get("/environments/" + quote(name, safe=""))
        if raw["id"] != identifier or raw["name"] != name:
            raise ValueError("GitHub environment identity changed")
        deployments = self.pages("/deployments", environment=name)
        latest = max(
            (d for d in deployments if d.get("environment") == name),
            key=lambda d: d["id"],
            default=None,
        )
        deployment = None
        if latest:
            statuses = self.pages(f"/deployments/{latest['id']}/statuses")
            last_status = max(statuses, key=lambda s: s["id"], default={})
            deployment = {
                "id": latest["id"],
                "sha": latest["sha"],
                "status": last_status.get("state", "unknown"),
                "status_id": last_status.get("id"),
                "environment_url": last_status.get("environment_url"),
                "environment": {"id": identifier, "name": name},
            }
        return {
            "id": identifier,
            "name": name,
            "state": "available",
            "last_deployment": deployment,
            "external_url": (deployment or {}).get("environment_url"),
        }

    def get(self, path, **query):
        parts = path.strip("/").split("/")
        if path.startswith("/repository/commits/"):
            value = self.raw_get("/commits/" + parts[-1])
            return value | {"id": value["sha"]}
        if parts[0] == "merge_requests" and len(parts) == 2:
            return review_request(self.raw_get("/pulls/" + parts[1]))
        if parts[0] == "pipelines" and len(parts) >= 2:
            if len(parts) == 3 and parts[2] == "test_report":
                pipeline = self.group(int(parts[1]))
                checks = self.pages("/commits/" + pipeline["sha"] + "/check-runs", "check_runs")
                return {
                    "format": "github_check_summaries",
                    "counts_available": False,
                    "checks": [
                        {
                            "name": c["name"],
                            "conclusion": c.get("conclusion"),
                            "summary": (c.get("output", {}).get("summary") or "")[:4096],
                            "web_url": c.get("html_url"),
                        }
                        for c in checks
                    ],
                }
            return self.group(int(parts[1]))
        if parts[0] == "environments" and len(parts) == 2:
            return self.environment(int(parts[1]))
        if path == "":
            return self.raw_get("")
        raise ValueError("unsupported GitHub delivery read")

    def list_pages(self, path, **query):
        parts = path.strip("/").split("/")
        if path == "/merge_requests":
            args = {"state": query.get("state", "all")}
            if query.get("source_branch"):
                args["head"] = self.owner + ":" + query["source_branch"]
            if query.get("target_branch"):
                args["base"] = query["target_branch"]
            return [review_request(v) for v in self.pages("/pulls", **args)]
        if parts[0] == "merge_requests" and len(parts) == 3 and parts[2] == "pipelines":
            sha = self.get("/merge_requests/" + parts[1])["sha"]
            pipeline = self.pipeline(sha)
            return [pipeline] if pipeline else []
        if parts[0] == "pipelines" and len(parts) == 3 and parts[2] == "jobs":
            return self.jobs(int(parts[1]))
        if path == "/environments":
            rows = self.pages("/environments", "environments")
            self.environments = {e["id"]: e for e in rows}
            return [e for e in rows if not query.get("name") or e["name"] == query["name"]]
        if path == "/deployments":
            match = [e for e in self.environments.values() if e["name"] == query["environment"]]
            if len(match) != 1:
                return []
            deployment = self.environment(match[0]["id"])["last_deployment"]
            return [deployment] if deployment and deployment["status"] == "success" else []
        raise ValueError("unsupported GitHub delivery collection")
