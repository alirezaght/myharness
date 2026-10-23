"""Importing the installer engine must not redirect later AL imports to one distribution."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_engine_import_leaves_no_distribution_scripts_on_sys_path():
    claude = ROOT / 'al/claude/skills/al/scripts'
    code = ("import sys;sys.path.insert(0,sys.argv[1]);sys.path.append(sys.argv[2]);import installer.engine;"
            "import al_runtime_resources as R;print(R.__file__);"
            "print(any(p.endswith('codex/skills/al/scripts') for p in sys.path))")
    result = subprocess.run([sys.executable, '-I', '-c', code, str(claude), str(ROOT / 'al')],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    loaded, leaked = result.stdout.split()
    assert Path(loaded).parent == claude and leaked == 'False'
