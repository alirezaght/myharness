from pathlib import Path

path = Path("docs/peer.md")
if path.exists():
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
