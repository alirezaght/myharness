"""Closed publication intent and facts; caller statements remain reported."""

from pathlib import Path
from urllib.parse import urlsplit
import re

import al_proof_json as J
import al_publication_checks as C
import al_store
from al_schema import refuse

RECORD = set(
    "schema_version kind repository_identity session_id run_id source_revision remote authorization_ref policy_refs required_check_bindings observation result reason".split()
)
AUTHORIZATION = set(
    "schema_version kind repository_identity session_id run_id source_revision remote actions source".split()
)
POLICY = {"schema_version", "kind", "allowed_remotes", "required_areas", "actions"}
POLICY_PATH = ".al/publication-policy.json"
ACTION = "publish_review_request"


def identity(st):
    return {
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
    }


def remote(value):
    J.closed(value, {"provider", "identity", "url"}, "publication remote")
    if value["provider"] not in {"github", "gitlab", "local-git"}:
        refuse("unsupported publication remote provider")
    J.text(value["identity"], "publication remote identity")
    J.text(value["url"], "publication URL")
    parsed = urlsplit(value["url"])
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        refuse("publication URL cannot contain credentials or opaque query state")
    if value["provider"] == "local-git":
        if parsed.scheme != "file" or parsed.netloc or not Path(parsed.path).is_absolute():
            refuse("local publication requires an absolute file URL")
    elif parsed.scheme != "https" or not parsed.hostname:
        refuse("publication requires an explicit HTTPS URL")


def version(value, kind):
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != kind
    ):
        refuse("unsupported publication artifact version")


def authorize(st, value, *, current=True):
    J.closed(value, AUTHORIZATION, "publication authorization")
    version(value, "publication-authorization-v1")
    if any(value[key] != expected for key, expected in identity(st).items()):
        refuse("foreign publication authorization")
    if current and value["source_revision"] != C.source_revision(st):
        refuse("publication authorization source revision changed")
    revision = J.closed(
        value["source_revision"],
        {"commit_oid", "candidate_fingerprint"},
        "publication source revision",
    )
    if (
        not isinstance(revision["commit_oid"], str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision["commit_oid"])
        or not isinstance(revision["candidate_fingerprint"], str)
        or not J.SHA.fullmatch(revision["candidate_fingerprint"])
    ):
        refuse("invalid publication source identity")
    remote(value["remote"])
    if value["actions"] != [ACTION]:
        refuse("publication authorization grants an unsupported action")
    source = J.closed(
        value["source"], {"kind", "reference", "text"}, "publication authorization provenance"
    )
    if source["kind"] != "user":
        refuse("explicit user publication authorization provenance is required")
    J.text(source["reference"], "publication authorization reference")
    J.text(source["text"], "publication authorization statement")


def policy(st, references, selected_remote, *, current=True):
    if not isinstance(references, list) or len(references) != 1:
        refuse("publication needs the complete supported repository policy profile")
    reference = references[0]
    if reference.get("kind") != "publication-policy-v1":
        refuse("unsupported publication policy artifact")
    value = J.read(reference)
    J.closed(value, POLICY, "publication policy")
    version(value, "publication-policy-v1")
    path = al_store.REPO_ROOT.resolve() / POLICY_PATH
    if current and (
        path.resolve() != path
        or path.is_symlink()
        or not path.is_file()
        or J.reference(reference) != path.read_bytes()
    ):
        refuse("publication policy differs from the current literal repository policy")
    if not isinstance(value["allowed_remotes"], list) or not value["allowed_remotes"]:
        refuse("publication policy omits remote scope")
    for item in value["allowed_remotes"]:
        remote(item)
    if selected_remote not in value["allowed_remotes"] or value["actions"] != [ACTION]:
        refuse("publication is outside repository policy")
    J.strings(value["required_areas"], "publication policy check areas", nonempty=True)
    return value


def record(st, value, *, current=True):
    J.closed(value, RECORD, "publication record")
    version(value, "publication-record-v1")
    if any(value[key] != expected for key, expected in identity(st).items()):
        refuse("foreign publication record")
    remote(value["remote"])
    reference = value["authorization_ref"]
    if (
        reference not in st.get("publication_authorizations", [])
        or reference.get("kind") != "publication-authorization-v1"
    ):
        refuse("publication authorization was not separately admitted")
    authorization = J.read(reference)
    authorize(st, authorization, current=current)
    if any(value[key] != authorization[key] for key in ("source_revision", "remote")):
        refuse("publication differs from explicit authorization scope")
    if value["result"] not in {"pending", "published", "failed"}:
        refuse("unknown publication result")
    J.text(value["reason"], "publication result rationale")
    observation = J.closed(
        value["observation"],
        {"provenance", "artifact_refs", "observer_binding"},
        "publication observation",
    )
    if not isinstance(observation["artifact_refs"], list):
        refuse("invalid publication observation artifacts")
    for artifact in observation["artifact_refs"]:
        J.reference(artifact)
    if observation["provenance"] != "reported" or observation["observer_binding"] is not None:
        refuse(
            "unsupported independent publication observer profile; caller statements cannot verify publication"
        )
    if current:
        supported = policy(st, value["policy_refs"], value["remote"])
        checks = C.checks(st, supported["required_areas"])
        if not checks["ready"]:
            refuse("publication code readiness refused: " + str(checks["gaps"]))
        if value["required_check_bindings"] != checks["bindings"]:
            refuse("publication required check bindings changed")
    else:
        policy(st, value["policy_refs"], value["remote"], current=False)
        if not isinstance(value["required_check_bindings"], list):
            refuse("invalid retained publication check bindings")
    for check in value["required_check_bindings"]:
        J.closed(
            check,
            {
                "id",
                "reservation",
                "area",
                "command_digest",
                "entry_digest",
                "log_sha256",
                "execution_receipt",
                "execution_association",
            },
            "publication required check binding",
        )
        for key in ("command_digest", "entry_digest", "log_sha256"):
            if not isinstance(check[key], str) or not J.SHA.fullmatch(check[key]):
                refuse("invalid publication required check digest")
        if (
            not isinstance(check["id"], str)
            or not re.fullmatch(r"E[1-9][0-9]*", check["id"])
            or not isinstance(check["reservation"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", check["reservation"])
        ):
            refuse("invalid publication required check reservation")
        J.text(check["area"], "publication required check area")
        for key in ("execution_receipt", "execution_association"):
            if check[key] is not None:
                J.reference(check[key])
    return value
