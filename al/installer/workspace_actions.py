"""Exact planned preimages remain mandatory through the last mutation boundary."""

from pathlib import Path

from . import journal as J
from .engine import R, T
from .guards import check_instructions
from .targets import review_for


def prepare(directory, desired, preimages):
    if set(desired) != set(preimages):
        raise ValueError("workspace action/preimage membership differs")
    result = []
    for name, (raw, mode) in desired.items():
        if T.state(R.safe_absolute(name)) != preimages[name]:
            raise ValueError("workspace planned preimage changed: " + name)
        item = J.action(directory, name, raw, mode, kind="workspace-resource")
        if (item is not None and item["target"] != preimages[name]) or (
            item is None and T.state(name) != preimages[name]
        ):
            raise ValueError("workspace preimage changed during action preparation")
        if item:
            result.append(item)
    return result


def recheck(value, journal, preimages):
    expected = dict(preimages)
    for item in journal["ancillary_actions"]:
        if item["status"] == "APPLIED":
            expected[item["target_path"]] = item["source"]
    for name, state in expected.items():
        if T.state(R.safe_absolute(name)) != state:
            raise ValueError("workspace ancillary resource changed: " + name)
    reviews = value["inputs"]["reviews"]["value"]
    if check_instructions(value["target"], reviews) != value["instruction_reviews"]:
        raise ValueError("workspace instruction review changed")
    for name, row in value["resources"].items():
        if (
            review_for(Path(name).parent, value["target"]["identity"], reviews)
            != row["instruction_review"]
        ):
            raise ValueError("workspace host instruction review changed")
