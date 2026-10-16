"""The native read-only hook shares migration script-position classification."""
import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('command,expected',[
 ('pnpm migrate:wissen',2),('pnpm migrate:wissen --dry-run',2),
 ('pnpm --filter web run migrations:db',2),('poetry run pnpm migration:db',2),
 ('pnpm migration:verify',0),('pnpm run format:check',0),
 ('pnpm --filter migrate:wissen test',0),("rg 'pnpm migrate:wissen --check' .",0),
])
def test_native_hook_uses_migration_family_policy(command,expected,repo):
    guard=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    result=subprocess.run([sys.executable,str(guard)],cwd=repo,input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),text=True,capture_output=True)
    assert result.returncode==expected,result.stdout+result.stderr
