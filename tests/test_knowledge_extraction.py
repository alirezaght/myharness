import os

import pytest

from harness.knowledge.extract import extract_source
from harness.knowledge.models import SourceVersion
from harness.knowledge.sources import eligible, read_source


def test_ast_is_static_and_relative_imports_are_precise():
    raw = b'from . import service\nimport os\ndef run():\n    raise RuntimeError("never execute")\n'
    source = SourceVersion('s', 'v', 'pkg/client.py', 'python', '', raw, {})
    assertions = extract_source(source).assertions
    assert any(a.predicate == 'imports' and a.object == 'module:pkg.service' for a in assertions)
    assert any(a.predicate == 'defines' and a.object == 'symbol:pkg.client:run' for a in assertions)
    assert all(a.verification == 'source-backed' for a in assertions)


def test_reader_rejects_aliases_secrets_and_env_examples(tmp_path):
    target = tmp_path / 'real.py'
    target.write_text('safe = True')
    (tmp_path / 'link.py').symlink_to(target)
    os.link(target, tmp_path / 'hard.py')
    for name in ('link.py', 'hard.py', 'real.py'):
        with pytest.raises((ValueError, OSError)):
            read_source(tmp_path, name, 1000)
    assert not eligible('.env.example')
    assert not eligible('nested/.env.production.json')
    assert not eligible('../outside.py')
    (tmp_path / 'secret.py').write_text('api_key = "privatecredential123"')
    with pytest.raises(ValueError):
        read_source(tmp_path, 'secret.py', 1000)
