# Agent instructions

## Verifying AL changes

After a change under `al/`, run the fast tier and do not wait on the slow tier:

```sh
uv run python al/verify_changed.py
```

The fast tier is the default. A typical change takes about 1 minute; the worst case (every fast file) about 3. It runs the affected fast test files of `al/tests` and
both host suites in parallel, plus the byte-parity, consistency and docs guards. It ends with one `PASS` or `FAIL`
line, and failures are printed with their pytest output and log paths. Add `--base REF` to compare against a ref
other than `main`, or `--path FILE` to name the changed files.

Do not run plain `pytest` serially per host (`cd al/<host>/skills/al && pytest ...`).

The slow tier is listed in `al/verification-tiers.json` together with the acceptance tier. It covers end-to-end
journeys, real Git-repository and runtime-pin fixtures, validator and launcher children, and installer and release
flows. It runs only in `python3 al/release_verify.py --full` (release) or nightly. Run
`verify_changed.py --tier all` only when you are asked to, or when you changed one of those slow flows on purpose.
Elapsed time is reported, never judged.

See [al/README.md](al/README.md#verifying-a-change).
