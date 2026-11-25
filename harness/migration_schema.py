"""Constraint-preserving SQLite schema identities for recognized migration histories."""

import hashlib
import json
import re

# Keep literals/quoted identifiers intact; normalize only unquoted whitespace/comments.
_TOKEN = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|\[[^\]]*\]|--[^\n]*|/\*.*?\*/|\s+|[^\s'\"`\[]+",
    re.S,
)


def normalized_sql(sql):
    return " ".join(
        t for t in _TOKEN.findall(sql or "") if not t.isspace() and not t.startswith(("--", "/*"))
    )


def schema(conn):
    result = {}
    for kind, name, table, sql in conn.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_schema WHERE name NOT GLOB 'sqlite_*' "
        "AND name!='schema_migrations' ORDER BY name"
    ):
        value = {"type": kind, "table": table, "sql": normalized_sql(sql)}
        if kind == "table":
            # Table-valued PRAGMAs use bound arguments; no identifier interpolation.
            value["columns"] = [
                list(r)
                for r in conn.execute("SELECT * FROM pragma_table_xinfo(?, 'main')", (name,))
            ]
            value["foreign_keys"] = [
                list(r)
                for r in conn.execute("SELECT * FROM pragma_foreign_key_list(?, 'main')", (name,))
            ]
            value["indexes"] = []
            for row in conn.execute("SELECT * FROM pragma_index_list(?, 'main')", (name,)):
                # SQLite-owned autoindex names are not stable across table renames.
                value["indexes"].append(
                    {
                        "unique": row[2],
                        "origin": row[3],
                        "partial": row[4],
                        "columns": [
                            list(r)
                            for r in conn.execute(
                                "SELECT * FROM pragma_index_xinfo(?, 'main')", (row[1],)
                            )
                        ],
                    }
                )
            value["indexes"].sort(key=lambda v: json.dumps(v, sort_keys=True))
        result[name] = value
    return result


def identities(conn):
    return {
        name: hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for name, value in schema(conn).items()
    }
