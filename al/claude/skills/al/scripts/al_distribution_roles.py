"""Protocol-dependent role changes remain separate from skill resources."""
import hashlib
from pathlib import Path
from al_distribution import sha,repo_identity,instructions
from al_distribution_registry import safe_absolute,stable


def plan_roles(items):
    out=[]
    for item in items:
        source=safe_absolute(item['source']);target=safe_absolute(item['target'])
        if not item.get('protocol_reason') or not item.get('instruction_review',{}).get('reviewed'):
            raise ValueError('role change needs protocol rationale and instruction review')
        identity=repo_identity(target.parent)
        rules=instructions(target.parent,identity)
        if item['instruction_review'].get('instructions')!=rules:raise ValueError('role instruction review mismatch')
        if not source.is_file() or source.is_symlink() or target.is_symlink():raise ValueError('unsafe role source/target')
        out.append(dict(item,source=str(source),target=str(target),source_sha256=sha(source),target_sha256=sha(target),
                        source_mode=source.stat().st_mode & 0o777,target_mode=target.stat().st_mode & 0o777 if target.exists() else None,identity=identity,instructions=rules))
    return out


def validate_roles(items):
    for item in items:
        target=Path(item['target']);source=Path(item['source'])
        safe_absolute(source);safe_absolute(target)
        if sha(source)!=item['source_sha256'] or sha(target)!=item['target_sha256'] or (target.stat().st_mode & 0o777 if target.exists() else None)!=item['target_mode']:raise ValueError('role drift')
        if stable(repo_identity(target.parent))!=stable(item['identity']) or instructions(target.parent,item['identity'])!=item['instructions']:raise ValueError('role repository/instructions changed')


def verify_roles(items):
    for item in items:
        target=Path(item['target'])
        if sha(target)!=item['source_sha256'] or target.stat().st_mode & 0o777 != item['source_mode']:raise ValueError('role installation mismatch')
