"""Exact normative values and noncircular accepted requirement lineage."""

import re

import al_task
from al_schema import refuse
import al_proof_json as J


FIELDS = {"id", "artifact", "pointer", "value_sha256"}


def lineage(st):
    return al_task.revision_hashes(st)[-1]


def sources(st):
    return [st["task_package"], *[entry["artifact"] for entry in st["amendments"]]]


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith("/") or re.search(r"~(?![01])", path):
        refuse("invalid normative JSON pointer")
    try:
        for item in path[1:].split("/"):
            item = item.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", item):
                    refuse("invalid normative array pointer")
                value = value[int(item)]
            elif isinstance(value, dict):
                value = value[item]
            else:
                refuse("normative pointer traverses a scalar")
    except (KeyError, IndexError):
        refuse("normative pointer does not resolve")
    return value


def validate(st, binding):
    J.closed(binding, FIELDS, "requirement binding")
    J.text(binding["id"], "requirement ID")
    if binding["artifact"] not in sources(st):
        refuse("requirement does not reference current accepted provenance")
    value = pointer(J.read(binding["artifact"]), binding["pointer"])
    if binding["value_sha256"] != J.digest(value):
        refuse("normative requirement value hash differs")
    return value


def registry(st, field):
    package = J.read(st["task_package"])
    result = []
    for index, value in enumerate(package[field]):
        identifier = value if field == "criteria" else field + "-" + str(index)
        result.append(
            {
                "id": identifier,
                "artifact": st["task_package"],
                "pointer": "/" + field + "/" + str(index),
                "value_sha256": J.digest(value),
            }
        )
    return result


def criteria(st, entries):
    if not isinstance(entries, list) or not entries:
        refuse("missing behavior criteria registry")
    ids = []
    for entry in entries:
        value = validate(st, entry)
        # Labels never replace exact authoritative criterion strings.
        if not isinstance(value, str) or entry["id"] != value:
            refuse("criterion ID differs from exact authoritative value")
        ids.append(entry["id"])
    if len(ids) != len(set(ids)) or not set(J.read(st["task_package"])["criteria"]) <= set(ids):
        refuse("behavior criteria registry omits or duplicates accepted criteria")
    result = {entry["id"]: entry for entry in entries}
    required = registry(st, "criteria")
    for artifact in sources(st)[1:]:
        data = J.read(artifact)
        acceptance = data.get("supplemental_acceptance", [])
        J.strings(acceptance, "accepted supplemental criteria")
        required.extend(
            {
                "id": value,
                "artifact": artifact,
                "pointer": "/supplemental_acceptance/" + str(index),
                "value_sha256": J.digest(value),
            }
            for index, value in enumerate(acceptance)
        )
    if any(result.get(entry["id"]) != entry for entry in required):
        refuse(
            "behavior registry omits exact original or accepted supplemental requirement binding"
        )
    return result
