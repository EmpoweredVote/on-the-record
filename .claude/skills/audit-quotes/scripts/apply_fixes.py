"""Gated fix applier. Dry-run (transaction+rollback) by default; --commit persists."""
import argparse, json
from scripts.db import connect

ALLOWED_FIELDS = {"editor_note", "deidentified_text", "quote_text", "topic_key"}

def build_statement(op):
    kind = op["kind"]; qid = op["id"]
    if kind == "set_field":
        if op["field"] not in ALLOWED_FIELDS:
            raise ValueError(f"field not allowed: {op['field']}")
        return (f"UPDATE essentials.quotes SET {op['field']} = %s WHERE id = %s::uuid", [op["value"], qid])
    if kind == "regex_sub":
        if op["field"] not in ALLOWED_FIELDS:
            raise ValueError(f"field not allowed: {op['field']}")
        return (f"UPDATE essentials.quotes SET {op['field']} = regexp_replace({op['field']}, %s, %s) WHERE id = %s::uuid",
                [op["pattern"], op["repl"], qid])
    if kind == "set_live":
        v = op["value"]
        if not isinstance(v, bool):
            raise ValueError(f"set_live value must be a JSON boolean, got {type(v).__name__}: {v!r}")
        return ("UPDATE essentials.quotes SET readrank_selected = %s WHERE id = %s::uuid", [v, qid])
    raise ValueError(f"unknown op kind: {kind}")

def _snapshot(cur, ids):
    cur.execute("SELECT id, topic_key, readrank_selected, left(quote_text,200) qt, "
                "left(deidentified_text,200) dt, left(editor_note,200) en "
                "FROM essentials.quotes WHERE id = ANY(%s::uuid[]) ORDER BY id", (ids,))
    return {r[0]: r for r in cur.fetchall()}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixes_file")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    ops = json.loads(open(a.fixes_file).read())
    ids = sorted({op["id"] for op in ops})
    conn = connect(); conn.autocommit = False
    cur = conn.cursor()
    before = _snapshot(cur, ids)
    for op in ops:
        sql, params = build_statement(op)
        cur.execute(sql, params)
    after = _snapshot(cur, ids)
    missing = [i for i in ids if i not in before]
    if missing:
        print(f"WARNING: {len(missing)} id(s) not found in essentials.quotes: {missing}")
    print("=== DIFF (before → after) ===")
    for i in ids:
        if i in before and before[i] != after[i]:
            print(f"[{i}]\n  before: {before[i][1:]}\n  after:  {after[i][1:]}")
    if a.commit:
        conn.commit(); print("*** COMMITTED ***")
    else:
        conn.rollback(); print("*** DRY RUN — ROLLED BACK ***")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
