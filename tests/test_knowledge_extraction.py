import os

import pytest

from harness.knowledge.extract import extract_source
from harness.knowledge.models import SourceVersion
from harness.knowledge.sources import eligible, read_source


def test_ast_is_static_and_relative_imports_are_precise():
    raw = b'from . import service\nimport os\ndef run():\n    raise RuntimeError("never execute")\n'
    source = SourceVersion("s", "v", "pkg/client.py", "python", "", raw, {})
    assertions = extract_source(source).assertions
    assert any(a.predicate == "imports" and a.object == "module:pkg.service" for a in assertions)
    assert any(a.predicate == "defines" and a.object == "symbol:pkg.client:run" for a in assertions)
    assert all(a.verification == "source-backed" for a in assertions)


def test_reader_rejects_aliases_secrets_and_env_examples(tmp_path):
    target = tmp_path / "real.py"
    target.write_text("safe = True")
    (tmp_path / "link.py").symlink_to(target)
    os.link(target, tmp_path / "hard.py")
    for name in ("link.py", "hard.py", "real.py"):
        with pytest.raises((ValueError, OSError)):
            read_source(tmp_path, name, 1000)
    assert not eligible(".env.example")
    assert not eligible("nested/.env.production.json")
    assert not eligible("../outside.py")
    (tmp_path / "secret.py").write_text('api_key = "privatecredential123"')
    with pytest.raises(ValueError):
        read_source(tmp_path, "secret.py", 1000)


@pytest.mark.parametrize("name", ["Makefile", "bin/run", "src/example.custom", "src/example.py"])
def test_ordinary_reader_formats_preserve_index_eligibility(tmp_path, name):
    from harness.knowledge.models import RefreshRequest
    from harness.knowledge.sources import inventory_names, read_ordinary_text
    from tests.sample_repo import git_init

    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Public engineering text\nall: build\n")
    git_init(tmp_path)
    assert read_ordinary_text(tmp_path, name, 1024) == path.read_bytes()
    names, _ = inventory_names(tmp_path, RefreshRequest())
    assert (name in names) == eligible(name) == name.endswith(".py")
    if eligible(name):
        assert read_source(tmp_path, name, 1024) == read_ordinary_text(tmp_path, name, 1024)
    else:
        with pytest.raises(ValueError, match="excluded"):
            read_source(tmp_path, name, 1024)


@pytest.mark.parametrize(
    "name",
    [
        ".agents/run",
        ".netrc",
        ".git-credentials",
        ".mcp.json",
        ".npmrc",
        ".pypirc",
        ".env.example",
        "a/.ENV.json",
        "credentials/data",
        "logs/data",
        ".harness/data",
        "transcripts/data",
        ".aws/data",
        "a/key.pem",
        "a/service.key",
        "../escape",
        "/absolute",
        "a//b",
        "./Makefile",
        "a/../Makefile",
        "",
    ],
)
def test_ordinary_reader_union_path_restrictions(tmp_path, name):
    from harness.knowledge.sources import read_ordinary_text

    with pytest.raises((ValueError, OSError)):
        read_ordinary_text(tmp_path, name, 1024)


@pytest.mark.parametrize("kind", ["symlink", "directory-link", "hardlink", "fifo", "directory"])
def test_ordinary_reader_refuses_aliases_and_special_files(tmp_path, kind):
    from harness.knowledge.sources import read_ordinary_text

    (tmp_path / "safe").write_text("public")
    name = "input"
    if kind == "symlink":
        (tmp_path / name).symlink_to("safe")
    elif kind == "directory-link":
        (tmp_path / "real").mkdir()
        (tmp_path / "real/input").write_text("public")
        (tmp_path / "alias").symlink_to("real", target_is_directory=True)
        name = "alias/input"
    elif kind == "hardlink":
        os.link(tmp_path / "safe", tmp_path / name)
    elif kind == "fifo":
        os.mkfifo(tmp_path / name)
    else:
        (tmp_path / name).mkdir()
    with pytest.raises((ValueError, OSError)):
        read_ordinary_text(tmp_path, name, 1024)


@pytest.mark.parametrize(
    "raw", [b"public\xff", b"public\0", b"x" * 200 + b'api_key="publiccanary123"']
)
def test_ordinary_reader_screens_entire_text(tmp_path, raw):
    from harness.knowledge.sources import read_ordinary_text

    (tmp_path / "input").write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)):
        read_ordinary_text(tmp_path, "input", 1024)


def test_ordinary_reader_size_and_stability(tmp_path, monkeypatch):
    from harness.knowledge.sources import SourceSizeCapped, read_ordinary_text

    path = tmp_path / "input"
    path.write_bytes(b"public")
    with pytest.raises(SourceSizeCapped):
        read_ordinary_text(tmp_path, "input", 3)
    original = os.read
    changed = False

    def changing(fd, length):
        nonlocal changed
        result = original(fd, length)
        if result and not changed:
            changed = True
            with path.open("ab") as stream:
                stream.write(b"changed")
        return result

    monkeypatch.setattr(os, "read", changing)
    with pytest.raises(ValueError, match="unstable source"):
        read_ordinary_text(tmp_path, "input", 1024)


def test_described_by_text_is_compact_but_searchable():
    """#31: the stored described-by copy keeps each word once, not 8 KiB of source."""
    body = "".join(f"def handler_{i}(request):\n    return render(request)\n" for i in range(400))
    raw = ('"""Order service."""\n' + body).encode()
    source = SourceVersion("s", "v", "pkg/orders.py", "python", "", raw, {})
    described = next(a for a in extract_source(source).assertions if a.predicate == "described-by")
    assert len(described.text) <= 2048 and described.text.startswith("Order service")
    assert "handler_0" in described.text and described.text.count("request") == 1
    assert (described.start_byte, described.end_byte) == (0, 8192)
