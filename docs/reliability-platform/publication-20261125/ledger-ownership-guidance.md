PLAN_STATUS: REVISED

RESOLUTION

**Confirmed: ownership includes the migration ledger’s structure and every trigger/index attached to a managed table, regardless of object name.** This is a narrow correction required by the accepted protocol.

Source findings:

- `migration_schema.schema()` explicitly excludes `schema_migrations`.
- `migration_compatibility.validate_history()` reads ledger rows into a dictionary before validating ledger structure, then selects schema objects only by name/prefix. Consequently, an arbitrary-name ledger trigger is omitted and a ledger without its primary key can appear valid.
- Published `harness/db.py` at checkpoint parent `2ad8bed9`, main `fef2cd1`, and the original introduction (`cfd62ad`, identified through Git history) uses the same ledger declaration:

```sql
version INTEGER PRIMARY KEY,
name TEXT NOT NULL,
applied_at TEXT NOT NULL
```

No legitimate published alternative ledger structure or ledger trigger was identified. Formatting differences in fixture SQL are not schema variants.

Minimal repair:

1. Validate a present ledger **before reading its rows**: actual table, exact columns/types/nullability/defaults/primary-key semantics, no additional constraints or attached objects. Use the existing constraint-preserving schema representation and a source-backed ledger signature; do not rely solely on column names.
2. Permit ledger absence only for a genuinely fresh recognized schema. Preserve the legitimate empty canonical ledger state before migration 0001.
3. Select managed schema objects by either their own existing name/prefix ownership **or their `tbl_name` referring to a managed table**, including `schema_migrations`. Unknown attached triggers/indexes must therefore produce a schema mismatch.
4. Preserve unrelated extension tables and objects attached solely to those tables. Do not ban the entire unrelated schema.
5. Keep validation at the initial read-only check and again after acquiring the write lock. No dropping objects, ledger reconstruction, backfill or automatic repair.
6. Avoid the current SQL `LIKE 'sqlite_%'` wildcard accidentally excluding arbitrary names such as `sqliteXhook`: SQLite’s reserved prefix is literal `sqlite_`. Attached-object validation must not be defeated by that filter.
7. For connection-local TEMP objects capable of shadowing the ledger or attaching triggers to managed main tables, refuse those conflicts too; validating only `main.sqlite_schema` does not cover them.

Tests:

- Retain both new `test_owned_ledger_schema_refuses_before_upgrade_side_effects` cases. Assert unchanged complete ledger rows, `applied_at`, application data, schema and empty `extension_notes`.
- Add an arbitrarily named trigger attached to another managed table and an unrelated extension-table trigger control that remains allowed.
- Cover extra ledger constraints/indexes, ledger-as-view, duplicate-version-capable structure and fresh/empty canonical ledger acceptance.
- Preserve all supported main/checkpoint prefixes, current-database no-write-lock behavior, repeat upgrades and manifest reproducibility.

Replace `stop_relocation` in `test_failed_remap_rolls_back_and_retries`; it is now correctly an invalid input schema. Use a disposable test connection with SQLite’s progress-handler interruption, armed after an actual relocation update has completed, or equivalent connection-level fault injection. Prove that at least one relocation occurred inside the transaction before interruption, then assert rollback restores **all** original rows/timestamps/data. Remove the fault and prove successful retry. Do not merely inject failure before the first update, and do not exempt the test trigger from production validation.

This is implementation guidance, not an approval or a claim that the new tests have passed.