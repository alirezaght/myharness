"""Byte identity of directory trees copied from per-process test templates (#36)."""
import hashlib
import os
from pathlib import Path
import stat


def tree_digest(root):
    """Digest of a directory tree: relative paths, file bytes, permission bits and symlink targets."""
    root = Path(root)
    h = hashlib.sha256()
    if not root.exists():
        h.update(b"absent")
        return h.hexdigest()
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + names):
            path = Path(directory) / name
            info = os.lstat(path)
            rel = path.relative_to(root).as_posix().encode()
            h.update(rel + b"\0" + oct(stat.S_IMODE(info.st_mode)).encode() + b"\0")
            if stat.S_ISLNK(info.st_mode):
                h.update(b"L" + os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                h.update(b"F" + hashlib.sha256(path.read_bytes()).digest())
            else:
                h.update(b"D")
            h.update(b"\n")
    return h.hexdigest()
