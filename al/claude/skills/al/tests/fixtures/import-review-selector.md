# Import review selector discovery fixture

`repository/ci/scripts/import_review/checker.py` contains the complete, verbatim
`affected_services(files, paths)` function extracted from:

- Source: `/Users/alirezaghiastabari/Documents/amber/ci/scripts/import_review/checker.py`
- Source commit: `3af0532325d62e23506f3ebf75a61c6b14a72eea` (2026-10-14)
- Source file SHA-256: `9b53643ba4bb39810252d36d90a5828c07d8b8b2a6a52d0f324b533c40cd1645`
- Function AST SHA-256: `019c1a084a318b0a844add61bfe77cf21aa564a4896c6b1219e40fec1ca7d914`

This fixture is discovery-only data, not an executable import scanner. Its
function retains its production dependencies without supplying scanner adapters
or execution machinery. Tests copy it into disposable repositories so AL can
recognize the supported selection contract from the exact function AST and
include the selector in gate dependency scopes. AL separately analyzes imports
and configured consumers; no selection result is mocked or short-circuited.

These tests cover discovery, AST identity, transitive consumers, uncertainty,
and dependency scope. They do not test production scanner execution. Regenerate
by verifying the source file hash, extracting the complete function using its
AST line range without rewriting it, and checking the function AST hash.
