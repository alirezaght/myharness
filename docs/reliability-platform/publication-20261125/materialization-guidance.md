PLAN_STATUS: REVISED

RESOLUTION

**Fixture-only ordinary copying is justified, but simply forcing `_system()` to return `None` or setting `eligible=False` before metadata observation is not equivalent.** Preserve source/parent security observation independently of clone selection.

The safe minimal change is:

- Add keyword-only `allow_clone=True` to `_materialize()` and `copy2()`.
- Keep the existing platform discovery and source/parent metadata-observation block at `source_materialization.py:300–313`.
- Gate only the subsequent reference/clone qualification block with `eligible and allow_clone`.
- Continue through the existing ordinary-copy branch and common finalization/cleanup.
- In `al/tests/conftest.py:109`, use a local callback or `functools.partial(copy2, allow_clone=False)` solely for creation of the disposable source template.
- Leave `write_captured`, production callers, default behavior and real clone tests unchanged. Use an explicit diagnostic reason for deliberate ordinary selection.

Why this distinction matters:

- `context()` at lines 234–250 verifies source identity, source/parent paths and—when profiles were captured—source and destination-parent ACL/xattr/security stability.
- Disabling native observation leaves `source_profile=None`, silently skipping those security checks.
- Concrete counterexample: change the destination parent’s inheritable ACL after its initial ownership/mode check without replacing its inode. `_verify_parent()` checks identity, not ACL stability; the ordinary destination can inherit the changed ACL. Existing observed-parent comparison rejects this.
- `native.observe()` at `source_materialization_metadata.py:355` deliberately repeats ACL/xattr observations and validates metadata stability. Do not cache, abbreviate or remove those checks.

The existing ordinary path retains:

- exclusive no-follow creation and independent, single-link destination custody;
- source-byte/mode validation and post-copy source hashing;
- descriptor-based copy2 metadata treatment;
- file and parent fsync, final identity checks and close failures;
- cleanup restricted to entries whose ownership remains proven.

Its metadata contract is **ordinary copy2 behavior**, including destination ACL inheritance; it does not promise to transplant every source ACL. Clone qualification compares a clone against an ordinary reference precisely to preserve that behavior. Removing the unused clone/reference work does not require an ordinary reference to be recreated.

Required verification:

1. Exercise `copy2(..., allow_clone=False)` with clone support available; prove it never calls `fclonefileat` or creates a reference entry, while copying bytes, mode, timestamps and supported xattrs correctly.
2. Add an explicit-choice parent-security-drift regression and source-security-drift regression. Both must refuse and clean only owned entries.
3. Exercise explicit ordinary selection against existing destination, symlink/FIFO, source drift, parent replacement, raced destination, hardlink custody loss and fsync failure cases.
4. Preserve existing malformed-metadata refusal tests: malformed/operational failures must not become successful ordinary fallback. Retain current genuinely unsupported-platform behavior.
5. Run the materialization suite through the shared runner, preserving actual Darwin clone/ACL tests. Then rerun the affected fixture consumers and the original timing obligation on stable source.

Do not edit while E32’s source capture is active. The measured profile supports removing optional clone qualification work; it does not yet establish the speedup or a passing budget result. This guidance authorizes no timing waiver, cached security assertion or production-default change.