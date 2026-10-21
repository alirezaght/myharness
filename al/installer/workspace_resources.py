"""Literal global Harness host resources preserve the existing approval workflow."""

from pathlib import Path
import shlex

from .guards import resource_adoption
from .engine import R, T
from .registry import digest
from .targets import review_for
from .workspace_environment import launcher


def module_launcher(environment):
    python = R.safe_absolute(environment) / "bin/python"
    return (
        "#!/bin/sh\nexec " + shlex.quote(str(python)) + ' -I -B -m harness.redesign "$@"\n'
    ).encode()


def host_text(command):
    text = command.decode()
    if (
        text.count("uv run python -m harness.dashboard") != 1
        or text.count("uv run python -m harness.redesign") != 2
    ):
        raise ValueError("unsupported Harness host command invocation profile")
    converted = text.replace("uv run python -m harness.dashboard", "harness dashboard")
    converted = converted.replace("uv run python -m harness.redesign", "harness-redesign")
    converted = converted.replace("uv run harness", "harness")
    if "uv run" in converted:
        raise ValueError("unmapped repository-bound Harness command")
    return converted.encode()


def global_resources(contents, home, environment):
    home = R.safe_absolute(home)
    command = contents.get(".claude/commands/harness.md")
    if command is None:
        raise ValueError("approved Harness command resource missing")
    converted = host_text(command)
    body = converted.decode().split("---", 2)
    if len(body) != 3 or body[0] != "":
        raise ValueError("Harness command frontmatter is unsupported")
    instructions = body[2].lstrip()
    if instructions.count('harness start "$ARGUMENTS"') != 1:
        raise ValueError("Harness command request boundary changed")
    instructions = instructions.replace(
        'harness start "$ARGUMENTS"', 'harness start "<the user request as one literal argument>"'
    )
    skill = (
        "---\nname: harness\ndescription: Start and supervise the Engineering Harness when explicitly requested.\n---\n\n"
        "Pass request text as one literal argv argument; do not interpolate it into shell code.\n\n"
        + instructions
    ).encode()
    return {
        str(home / ".local/bin/harness"): (launcher(Path(environment)), 0o755),
        str(home / ".local/bin/harness-redesign"): (module_launcher(Path(environment)), 0o755),
        str(home / ".claude/commands/harness.md"): (converted, 0o644),
        str(home / ".codex/skills/harness/SKILL.md"): (skill, 0o644),
    }


def prepare_resources(resources, target, reviews, adoption, previous=None):
    previous = previous or {}
    if set(previous) - set(resources):
        raise ValueError("unplanned global Harness resource retirement")
    result = {}
    for name, (raw, mode) in resources.items():
        path = R.safe_absolute(name)
        before = T.state(path)
        prior = previous.get(name)
        if prior is not None:
            if prior["state"] != before:
                raise ValueError("managed global Harness resource drift")
        elif before is not None:
            resource_adoption(name, before, adoption)
        reviewed = review_for(path.parent, target["identity"], reviews)
        result[name] = {
            "before": before,
            "after": {"sha256": digest(raw), "mode": mode},
            "instruction_review": reviewed,
        }
    return result
