"""A real locked environment is prepared in scratch; setup failures never run hooks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
import al_check_setup as Setup
from al_gate_process import Owner


def test_real_locked_uv_setup_installs_local_dependency_and_preserves_inputs(tmp_path,monkeypatch):
    uv=shutil.which('uv')
    if not uv:pytest.skip('uv is required for real locked setup integration')
    root=tmp_path/'checkout';root.mkdir()
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    wheel=root/'pets_dep-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('pets_dep/__init__.py','VALUE = 42\n')
        archive.writestr('pets_dep-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: pets-dep\nVersion: 1.0\n')
        archive.writestr('pets_dep-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('pets_dep-1.0.dist-info/RECORD','')
    (root/'pyproject.toml').write_text('[project]\nname="setup-fixture"\nversion="0.1.0"\nrequires-python=">=3.13"\ndependencies=["pets-dep==1.0"]\n[tool.uv.sources]\npets-dep={path="pets_dep-1.0-py3-none-any.whl"}\n')
    (root/'.gitignore').write_text('.venv/\n')
    env=dict(os.environ,UV_OFFLINE='1',UV_PYTHON_DOWNLOADS='never',UV_CACHE_DIR=str(tmp_path/'lock-cache'))
    result=subprocess.run([uv,'lock','--offline'],cwd=root,env=env,capture_output=True)
    assert result.returncode==0,result.stderr
    subprocess.run(['git','add','.'],cwd=root,check=True)
    before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    monkeypatch.setenv('UV_OFFLINE','1');monkeypatch.setenv('UV_PYTHON_DOWNLOADS','never')
    rows=[];log=tmp_path/'setup.log'
    with Owner(log) as owner:
        Setup.execute(root,'.',owner,log,30,rows)
        result=owner.spawn([str(root/'.venv/bin/python'),'-I','-c','import pets_dep; assert pets_dep.VALUE == 42'],cwd=root)
        assert result.wait(timeout=10)==0
        from al_gate_process import stop
        stop(result)
    assert rows[0]['status']=='PASS' and rows[0]['cleanup']['complete']
    assert all((root/name).read_bytes()==raw for name,raw in before.items())
    assert not (root/'.venv').is_symlink()


def test_missing_manager_records_setup_infra_without_running_hook(tmp_path,monkeypatch):
    root=tmp_path/'checkout';root.mkdir()
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    (root/'package.json').write_text('{}');(root/'package-lock.json').write_text('{}')
    monkeypatch.setattr(Setup,'executable',lambda name: (_ for _ in ()).throw(ValueError('required executable unavailable: '+name)))
    rows=[];log=tmp_path/'setup.log'
    with Owner(log) as owner:
        with pytest.raises(Setup.SetupError,match='unavailable'):
            Setup.execute(root,'.',owner,log,10,rows)
    assert rows[0]['status']=='INFRA' and rows[0]['hook']=='NOT_RUN'


def test_source_backed_setup_does_not_borrow_ambient_node_modules(cli,repo):
    import al_checkouts as C
    import al_store
    from batch13_helpers import start
    (repo/'node_modules').mkdir();(repo/'node_modules/ambient').write_text('not locked')
    (repo/'.gitignore').write_text('node_modules/\n')
    st=start(cli,repo)
    with C.materialize(st,'candidate') as checkout:
        assert not (checkout['root']/'node_modules').exists()


def test_missing_lock_is_a_setup_prerequisite(tmp_path):
    (tmp_path/'package.json').write_text('{}')
    with pytest.raises(Setup.SetupError,match='lockfile'):
        Setup.plan(tmp_path)
