"""The network installer consumes registry data, never arbitrary local package inputs."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def public_url(value, hosts):
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in hosts or url.username or url.password:
        raise ValueError("dependency artifact requires a configured registry setup adapter")


def python_lock(lock):
    for package in lock.get("package", []):
        source = package.get("source", {})
        if "registry" in source and source["registry"] != "https://pypi.org/simple":
            raise ValueError("private Python registries require a configured setup adapter")
        if set(source) - {"registry", "editable", "directory", "virtual"}:
            raise ValueError("non-registry Python dependencies require a setup adapter")
        for artifact in [
            *package.get("wheels", []),
            *([package["sdist"]] if "sdist" in package else []),
        ]:
            public_url(artifact["url"], {"files.pythonhosted.org"})


def node_manifest(raw, root, filename):
    value = json.loads(raw)
    for family in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for version in value.get(family, {}).values():
            if not isinstance(version, str):
                raise ValueError("dependency version must be a string")
            if version.startswith("link:"):
                target = Path(root) / Path(filename).parent / version[5:]
                if not target.resolve().is_relative_to(root):
                    raise ValueError("linked Node dependency escapes repository")
            elif version.startswith("workspace:"):
                continue
            elif version.startswith("https:"):
                public_url(version, {"registry.npmjs.org"})
            elif (
                version.startswith(("/", "./", "../", "~", "\\"))
                or "/" in version
                and not version.startswith("npm:")
                or ":" in version
                and not version.startswith("npm:")
            ):
                raise ValueError("non-registry Node dependencies require a setup adapter")


def node_lock(raw):
    text = raw.decode()
    if (
        re.search(r"(?<![\w/-])(?:file:|git\+|git@)", text)
        or re.search(r"(?:[{\s])directory\s*:", text)
    ):
        raise ValueError("local archives and Git dependencies require a setup adapter")
    for value in re.findall(r"\btarball:\s*[\"']?([^\s,'\"}]+)", text):
        public_url(value, {"registry.npmjs.org"})
