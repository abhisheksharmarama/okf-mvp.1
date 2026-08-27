"""Merge (structural safety) + validate (content safety).

The LLM never gets to define structure. It supplies semantics only; we overlay
them onto the schema our own parser produced. That removes most spec drift by
construction. The Bouncer then catches what remains.
"""

from datetime import datetime, timezone
import okf_spec as S


def _clamp(v, default=0.3):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _enum(v, allowed, default):
    return v if v in allowed else default


def is_kpi(col_name: str, description: str = "") -> bool:
    blob = f"{col_name} {description}".lower()
    return any(k in blob for k in S.KPI_KEYWORDS)


def merge_llm_draft(schema: dict, llm: dict, method: str, dialect: str = "duckdb") -> dict:
    """schema = parser output (authoritative). llm = model JSON (advisory)."""
    lt = {t.get("name"): t for t in (llm.get("tables") or []) if isinstance(t, dict)}

    okf = {
        "okf_version": S.OKF_VERSION,
        "source": {
            "dialect": dialect,
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": method,
        },
        "tables": [],
        "relationships": [],
        "metrics": [],
        "business_rules": [],
    }

    for t in schema["tables"]:
        d = lt.get(t["name"], {})
        lc = {c.get("name"): c for c in (d.get("columns") or []) if isinstance(c, dict)}
        cols = []
        for c in t["columns"]:
            cd = lc.get(c["name"], {})
            conf = _clamp(cd.get("confidence"))
            desc = str(cd.get("description") or "No description inferred.").strip()
            # Deterministic guardrail: KPI meaning is business policy, never metadata.
            if is_kpi(c["name"], desc):
                conf = min(conf, 0.40)
                if "calculation" not in desc.lower():
                    desc += " Calculation method is a business policy and must be confirmed by a human."
            cols.append({
                "name": c["name"],
                "physical_name": c["physical_name"],
                "type": c["type"],
                "description": desc,
                "semantic_role": _enum(cd.get("semantic_role"), S.SEMANTIC_ROLES, "unknown"),
                "confidence": round(conf, 2),
                "status": "draft",
                "provenance": "ai_draft",
                "pii": bool(cd.get("pii", False)),
            })

        valid = {c["name"] for c in cols}
        pk = [p for p in (d.get("primary_key") or []) if p in valid]

        okf["tables"].append({
            "name": t["name"],
            "physical_name": t["physical_name"],
            "description": str(d.get("description") or "No description inferred.").strip(),
            "grain": str(d.get("grain") or "UNKNOWN — a human must confirm what one row represents.").strip(),
            "business_role": _enum(d.get("business_role"), S.TABLE_ROLES, "unknown"),
            "confidence": round(_clamp(d.get("confidence")), 2),
            "status": "draft",
            "provenance": "ai_draft",
            "primary_key": pk,
            "columns": cols,
        })

    # Relationships: only keep those pointing at columns that actually exist.
    index = {t["name"]: {c["name"] for c in t["columns"]} for t in okf["tables"]}

    def resolvable(ref):
        if not isinstance(ref, str) or "." not in ref:
            return False
        tb, _, cl = ref.rpartition(".")
        return tb in index and cl in index[tb]

    for r in (llm.get("relationships") or []):
        if not isinstance(r, dict):
            continue
        if resolvable(r.get("from")) and resolvable(r.get("to")) and r.get("from") != r.get("to"):
            okf["relationships"].append({
                "from": r["from"],
                "to": r["to"],
                "cardinality": _enum(r.get("cardinality"), S.CARDINALITIES, "many_to_one"),
                "confidence": round(_clamp(r.get("confidence")), 2),
                "status": "draft",
                "provenance": "ai_draft",
            })
    return okf


def validate_okf(okf: dict) -> list:
    """Returns a list of blocking errors. Empty list = the OKF conforms."""
    e = []
    if not isinstance(okf, dict):
        return ["OKF is not an object."]

    for k in S.TOP_LEVEL_REQUIRED:
        if k not in okf:
            e.append(f"Missing top-level key: {k}")
    if okf.get("okf_version") != S.OKF_VERSION:
        e.append(f"okf_version must be '{S.OKF_VERSION}', got '{okf.get('okf_version')}'")
    for k in ("dialect", "ingested_at", "method"):
        if k not in (okf.get("source") or {}):
            e.append(f"Missing source.{k}")
    if not okf.get("tables"):
        e.append("OKF contains no tables.")

    seen_t = set()
    for t in okf.get("tables", []):
        tn = t.get("name", "?")
        if tn in seen_t:
            e.append(f"Duplicate table name: {tn}")
        seen_t.add(tn)
        for k in S.TABLE_REQUIRED:
            if k not in t:
                e.append(f"Table '{tn}' missing key: {k}")
        if t.get("business_role") not in S.TABLE_ROLES:
            e.append(f"Table '{tn}' business_role '{t.get('business_role')}' not in {S.TABLE_ROLES}")
        if t.get("status") not in S.STATUSES:
            e.append(f"Table '{tn}' status '{t.get('status')}' not in {S.STATUSES}")
        if t.get("provenance") not in S.PROVENANCES:
            e.append(f"Table '{tn}' provenance '{t.get('provenance')}' invalid")
        if not isinstance(t.get("confidence"), (int, float)) or not 0 <= t["confidence"] <= 1:
            e.append(f"Table '{tn}' confidence out of range: {t.get('confidence')}")
        if not t.get("columns"):
            e.append(f"Table '{tn}' has no columns.")

        seen_c = set()
        for c in t.get("columns", []):
            cn = c.get("name", "?")
            if cn in seen_c:
                e.append(f"Table '{tn}' duplicate column: {cn}")
            seen_c.add(cn)
            for k in S.COLUMN_REQUIRED:
                if k not in c:
                    e.append(f"Column '{tn}.{cn}' missing key: {k}")
            if c.get("semantic_role") not in S.SEMANTIC_ROLES:
                e.append(f"Column '{tn}.{cn}' semantic_role '{c.get('semantic_role')}' invalid")
            if c.get("type") not in S.TYPES:
                e.append(f"Column '{tn}.{cn}' type '{c.get('type')}' invalid")
            if not isinstance(c.get("pii"), bool):
                e.append(f"Column '{tn}.{cn}' pii must be true/false")
            if not isinstance(c.get("confidence"), (int, float)) or not 0 <= c["confidence"] <= 1:
                e.append(f"Column '{tn}.{cn}' confidence out of range")
            # KPI cap applies to AI drafts only. A human signing off overrides it.
            if (c.get("provenance") == "ai_draft"
                    and is_kpi(cn, c.get("description", ""))
                    and c.get("confidence", 1) > 0.40):
                e.append(f"Column '{tn}.{cn}' is an AI-drafted KPI but confidence > 0.40")

    index = {t["name"]: {c["name"] for c in t.get("columns", [])} for t in okf.get("tables", [])}
    for r in okf.get("relationships", []):
        for k in S.RELATIONSHIP_REQUIRED:
            if k not in r:
                e.append(f"Relationship missing key: {k}")
        for side in ("from", "to"):
            ref = r.get(side, "")
            tb, _, cl = str(ref).rpartition(".")
            if tb not in index or cl not in index.get(tb, set()):
                e.append(f"Relationship {side} '{ref}' does not resolve to a real column")
        if r.get("cardinality") not in S.CARDINALITIES:
            e.append(f"Relationship cardinality '{r.get('cardinality')}' invalid")
    return e


def review_queue(okf: dict) -> list:
    """Lowest-confidence items first — drives the Tinder UI ordering."""
    items = []
    for ti, t in enumerate(okf.get("tables", [])):
        items.append({"kind": "table", "ti": ti, "ci": None,
                      "conf": t.get("confidence", 0), "label": t["name"]})
        for ci, c in enumerate(t.get("columns", [])):
            items.append({"kind": "column", "ti": ti, "ci": ci,
                          "conf": c.get("confidence", 0),
                          "label": f"{t['name']}.{c['name']}"})
    items.sort(key=lambda x: (x["conf"], x["kind"] == "column"))
    return items


def approval_stats(okf: dict) -> dict:
    tot = app = low = 0
    for t in okf.get("tables", []):
        for obj in [t] + t.get("columns", []):
            tot += 1
            if obj.get("status") in ("approved", "edited"):
                app += 1
            if obj.get("confidence", 0) < S.REVIEW_THRESHOLD:
                low += 1
    return {"total": tot, "approved": app, "low_confidence": low,
            "pct": round(100 * app / tot) if tot else 0}
