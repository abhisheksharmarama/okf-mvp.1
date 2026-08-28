import json
import yaml
import pandas as pd
import streamlit as st

import okf_spec as S
import parsers
import validator
import mockdata
import safety
import suggest
import llm
import selftest

st.set_page_config(page_title="OKF — governed answers from scattered data",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root { --ink:#12161C; --muted:#5B6572; --rule:#E6EAEF; --wash:#F7F8FA;
        --lo:#B4472A; --mid:#C77B21; --hi:#2E6B4F; --brand:#23305E; }
.block-container { padding-top:2.2rem; max-width:1180px; }
h1,h2,h3 { color:var(--ink); letter-spacing:-0.015em; }
h1 { font-size:2.05rem; margin-bottom:.15rem; }
.mono, code { font-family:'IBM Plex Mono',monospace !important; }
.eyebrow { font-family:'IBM Plex Mono',monospace; font-size:.7rem; letter-spacing:.14em;
           text-transform:uppercase; color:var(--muted); margin-bottom:.2rem; }
.lede { color:var(--muted); font-size:.95rem; max-width:62ch; margin-bottom:1.1rem; }
.card { border:1px solid var(--rule); border-left:3px solid var(--brand); border-radius:6px;
        padding:1rem 1.25rem; background:#fff; margin-bottom:.55rem;
        transition:border-color .15s ease, box-shadow .15s ease; }
.card:hover { box-shadow:0 1px 3px rgba(18,22,28,.06); }
.card.lo { border-left-color:var(--lo); } .card.mid { border-left-color:var(--mid); }
.card.hi { border-left-color:var(--hi); }
.ident { font-family:'IBM Plex Mono',monospace; font-size:1.1rem; font-weight:600; color:var(--ink); }
.phys { font-family:'IBM Plex Mono',monospace; font-size:.75rem; color:var(--muted); }
.gauge { height:5px; background:var(--rule); border-radius:3px; overflow:hidden; margin:.5rem 0 .25rem; }
.gauge > div { height:100%; border-radius:3px; }
.conf { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--muted); }
.pill { font-family:'IBM Plex Mono',monospace; font-size:.66rem; padding:2px 7px;
        border:1px solid var(--rule); border-radius:10px; color:var(--muted);
        margin-right:5px; background:var(--wash); }
.trace { display:flex; gap:.5rem; align-items:baseline; font-family:'IBM Plex Mono',monospace;
         font-size:.78rem; padding:.55rem .85rem; border:1px solid var(--rule);
         border-radius:6px; margin-bottom:.3rem; background:#fff; }
.trace .n { color:var(--muted); }
.trace.pass { border-left:3px solid var(--hi); }
.trace.fail { border-left:3px solid var(--lo); }
.answer { border-left:3px solid var(--brand); background:var(--wash); border-radius:0 6px 6px 0;
          padding:.9rem 1.15rem; font-size:1.02rem; color:var(--ink); margin:.6rem 0 .2rem; }
.sub { color:var(--muted); font-size:.8rem; }
</style>""", unsafe_allow_html=True)


def band(c):
    return "lo" if c < 0.45 else ("mid" if c < S.REVIEW_THRESHOLD else "hi")


def gauge(c):
    col = {"lo": "#B4472A", "mid": "#C77B21", "hi": "#2E6B4F"}[band(c)]
    return (f'<div class="gauge"><div style="width:{int(c*100)}%;background:{col}"></div></div>'
            f'<div class="conf">confidence {c:.2f}</div>')


def trace(n, label, detail, ok=True):
    st.markdown(f'<div class="trace {"pass" if ok else "fail"}">'
                f'<span class="n">{n}</span><strong>{label}</strong>'
                f'<span class="sub">{detail}</span></div>', unsafe_allow_html=True)


DEFAULTS = {"step": 1, "schema": None, "okf": None, "qi": 0, "con": None,
            "counts": {}, "raw": "", "result": None, "selftest": False,
            "chart": None, "pending": None}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

DEMO_SCHEMA = """08 E-Commerce Orders,Order ID,varchar
08 E-Commerce Orders,Order Date,timestamp
08 E-Commerce Orders,Customer ID,varchar
08 E-Commerce Orders,Region,varchar
08 E-Commerce Orders,Product Name,varchar
08 E-Commerce Orders,Category,varchar
08 E-Commerce Orders,Quantity,int
08 E-Commerce Orders,Unit Price,decimal
08 E-Commerce Orders,Discount %,int
08 E-Commerce Orders,Total Revenue,decimal
08 E-Commerce Orders,Ship Mode,varchar
crm_customers,Customer ID,varchar
crm_customers,Customer Name,varchar
crm_customers,Email Address,varchar
crm_customers,Country,varchar
crm_customers,Signup Date,date
crm_customers,Account Status,varchar"""


def ask(text):
    """Queue a question. The widget key cannot be written after instantiation,
    so it is applied on the next run, before the text box is created."""
    st.session_state.pending = text
    st.session_state.chart = None
    st.rerun()


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown('<div class="eyebrow">Pipeline</div>', unsafe_allow_html=True)
    for n, label in [(1, "Read the schema"), (2, "Approve the meaning"), (3, "Ask a question")]:
        mark = "●" if st.session_state.step == n else ("✓" if st.session_state.step > n else "○")
        st.markdown(f'<div class="mono">{mark} {n}. {label}</div>', unsafe_allow_html=True)
    st.divider()

    if st.session_state.okf:
        s = validator.approval_stats(st.session_state.okf)
        st.metric("Approved", f'{s["pct"]}%', f'{s["approved"]}/{s["total"]} definitions')
        st.metric("Need review", s["low_confidence"], "below 0.60", delta_color="off")
        st.download_button("Save this OKF (.yaml)",
                           yaml.dump(st.session_state.okf, sort_keys=False, allow_unicode=True),
                           "okf.yaml", "text/yaml", width="stretch")

    saved = st.file_uploader("Load a saved OKF", type=["yaml", "yml"],
                             help="Restores an approved semantic layer without redoing review.")
    if saved is not None and st.button("Restore it", width="stretch"):
        try:
            loaded = yaml.safe_load(saved.getvalue().decode("utf-8"))
        except Exception as ex:
            st.error(f"That file is not readable YAML: {ex}")
            loaded = None
        if loaded:
            errs = validator.validate_okf(loaded)
            if errs:
                st.error("That file does not match the OKF spec:")
                for e in errs[:6]:
                    st.markdown(f"- {e}")
            else:
                con, counts = mockdata.build_database(loaded)
                st.session_state.update(okf=loaded, con=con, counts=counts,
                                        step=3, result=None, qi=0)
                st.rerun()

    st.divider()
    st.caption("Row data is never sent to the model. Schema metadata only.")
    if st.button("Run self-test", width="stretch"):
        st.session_state.selftest = True
        st.rerun()
    if st.button("Start over", width="stretch"):
        for k, v in DEFAULTS.items():
            st.session_state[k] = v
        st.rerun()

st.markdown('<div class="eyebrow">Governed analytics</div>', unsafe_allow_html=True)
st.title("Answers your analyst would sign off on")

# ------------------------------------------------------------------ self-test
if st.session_state.selftest:
    st.markdown('<div class="lede">Every rule this product relies on, checked live in the '
                'deployed app. No model calls — this is the deterministic half.</div>',
                unsafe_allow_html=True)
    results = selftest.run_all()
    failed = [r for r in results if not r["ok"]]
    (st.error if failed else st.success)(
        f'{len(results) - len(failed)} of {len(results)} checks passed'
        + (f' — {len(failed)} FAILED' if failed else ''))
    groups = {}
    for r in results:
        groups.setdefault(r["group"], []).append(r)
    for g, items in groups.items():
        bad = sum(1 for i in items if not i["ok"])
        with st.expander(f'{g} — {len(items) - bad}/{len(items)}', expanded=bad > 0):
            for i in items:
                st.markdown(f'{"PASS" if i["ok"] else "**FAIL**"} · {i["name"]}'
                            + (f'  \n`{i["detail"]}`' if i["detail"] else ''))
    if st.button("Back to the app"):
        st.session_state.selftest = False
        st.rerun()
    st.stop()

# ------------------------------------------------------------------ step 1
if st.session_state.step == 1:
    st.markdown('<div class="lede">Paste a schema. Table names, column names and types only — '
                'no rows, no credentials, no connection. The model drafts what each field '
                'means and how sure it is. You approve it in the next step.</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns([3, 2])
    with c1:
        text = st.text_area("Paste DDL, or CSV lines of `table,column,type`",
                            value=st.session_state.raw, height=230,
                            placeholder="CREATE TABLE ...\n-- or --\norders,Order ID,varchar")
    with c2:
        up = st.file_uploader("Or upload a CSV/Excel file", type=["csv", "xlsx", "xls"])
        st.caption("Only the header row is read. Rows are discarded before anything is sent.")
        if st.button("Load the demo schema", width="stretch"):
            st.session_state.raw = DEMO_SCHEMA
            st.rerun()

    if st.button("Draft the OKF", type="primary"):
        try:
            schema = parsers.parse_uploaded_file(up) if up else parsers.parse_text_input(text)
        except Exception as ex:
            st.error(f"Could not read that input: {ex}")
            st.stop()
        if not schema["tables"]:
            st.error("No tables found. Paste `CREATE TABLE` statements, or lines of "
                     "`table,column,type`.")
            st.stop()
        if len(schema["tables"]) > 30:
            st.error(f'{len(schema["tables"])} tables found. This preview handles 30 at a time.')
            st.stop()

        with st.spinner(f'Reading {len(schema["tables"])} tables…'):
            raw = llm.call_llm(llm.build_okf_prompt(schema), json_mode=True)
            try:
                draft = json.loads(safety.strip_fences(raw))
            except json.JSONDecodeError:
                st.error("The model did not return valid JSON. Press the button again.")
                st.stop()
            okf = validator.merge_llm_draft(schema, draft,
                                            "uploaded_file" if up else "pasted_ddl")
            errs = validator.validate_okf(okf)
            if errs:
                st.error("The draft failed the spec check and was not accepted:")
                for e in errs[:10]:
                    st.markdown(f"- {e}")
                st.stop()
            st.session_state.update(schema=schema, okf=okf, step=2, qi=0, result=None)
            st.rerun()

# ------------------------------------------------------------------ step 2
elif st.session_state.step == 2:
    okf = st.session_state.okf
    queue = validator.review_queue(okf)
    stats = validator.approval_stats(okf)

    st.markdown(f'<div class="lede"><strong>{len(okf["tables"])} tables · '
                f'{stats["total"]} definitions drafted.</strong> Lowest confidence first — '
                f'the ones most likely to be wrong are the ones you see first.</div>',
                unsafe_allow_html=True)
    st.progress(stats["pct"] / 100, text=f'{stats["approved"]} of {stats["total"]} approved')

    a, b, c = st.columns([1, 1, 2])
    if a.button("Approve everything above 0.80"):
        for t in okf["tables"]:
            for obj in [t] + t["columns"]:
                if obj["confidence"] >= 0.80:
                    obj.setdefault("ai_confidence", obj["confidence"])
                    obj["status"] = "approved"
        for r in okf["relationships"]:
            if r["confidence"] >= 0.80:
                r.setdefault("ai_confidence", r["confidence"])
                r["status"] = "approved"
        st.rerun()
    if b.button("Skip review (demo only)"):
        for t in okf["tables"]:
            for obj in [t] + t["columns"]:
                obj.setdefault("ai_confidence", obj["confidence"])
                obj["status"] = "approved"
        for r in okf["relationships"]:
            r.setdefault("ai_confidence", r["confidence"])
            r["status"] = "approved"
        st.rerun()
    if c.button("Done — start asking questions", type="primary"):
        with st.spinner("Generating a matching dataset…"):
            con, counts = mockdata.build_database(okf)
            st.session_state.update(con=con, counts=counts, step=3, result=None)
        st.rerun()

    view = st.radio("View", ["One at a time", "Full list"], horizontal=True,
                    label_visibility="collapsed")

    def edit_card(item, key):
        t = okf["tables"][item["ti"]]
        obj = t if item["kind"] == "table" else t["columns"][item["ci"]]
        st.markdown(
            f'<div class="card {band(obj["confidence"])}">'
            f'<div class="ident">{item["label"]}</div>'
            f'<div class="phys">source name: {obj["physical_name"]}</div>'
            f'{gauge(obj["confidence"])}'
            f'<span class="pill">{item["kind"]}</span>'
            f'<span class="pill">{obj.get("business_role") or obj.get("semantic_role")}</span>'
            f'<span class="pill">status: {obj["status"]}</span>'
            f'{"<span class=pill>PII</span>" if obj.get("pii") else ""}'
            f'</div>', unsafe_allow_html=True)

        obj["description"] = st.text_area("What this means", obj["description"],
                                          key=f"d{key}", height=80)
        if item["kind"] == "table":
            obj["grain"] = st.text_input("One row represents", obj["grain"], key=f"g{key}")
            obj["business_role"] = st.selectbox(
                "Role", S.TABLE_ROLES, index=S.TABLE_ROLES.index(obj["business_role"]),
                key=f"r{key}")
        else:
            c1, c2 = st.columns(2)
            obj["semantic_role"] = c1.selectbox(
                "Role in queries", S.SEMANTIC_ROLES,
                index=S.SEMANTIC_ROLES.index(obj["semantic_role"]), key=f"r{key}")
            obj["pii"] = c2.checkbox("Contains personal data", obj["pii"], key=f"p{key}")
        return obj

    def sign_off(obj, status):
        """A human decision replaces the model's, but never erases it."""
        obj.setdefault("ai_confidence", obj["confidence"])
        obj["status"] = status
        obj["confidence"] = 1.0
        if status == "edited":
            obj["provenance"] = "human_edited"

    if view == "One at a time":
        if st.session_state.qi >= len(queue):
            st.success("Every definition has been through review.")
            if st.button("Back to the start of the queue"):
                st.session_state.qi = 0
                st.rerun()
        else:
            item = queue[st.session_state.qi]
            st.caption(f"{st.session_state.qi + 1} of {len(queue)}")
            obj = edit_card(item, f'{item["kind"]}{item["ti"]}_{item["ci"]}')
            k1, k2, k3 = st.columns(3)
            if k1.button("Approve", type="primary", width="stretch"):
                sign_off(obj, "approved")
                st.session_state.qi += 1
                st.rerun()
            if k2.button("Save my edit", width="stretch"):
                sign_off(obj, "edited")
                st.session_state.qi += 1
                st.rerun()
            if k3.button("Not usable", width="stretch"):
                obj.setdefault("ai_confidence", obj["confidence"])
                obj["status"] = "rejected"
                st.session_state.qi += 1
                st.rerun()
    else:
        for i, item in enumerate(queue):
            with st.expander(f'{item["conf"]:.2f}  ·  {item["label"]}  ·  {item["kind"]}',
                             expanded=item["conf"] < 0.45):
                obj = edit_card(item, f"l{i}")
                if st.button("Approve", key=f"a{i}"):
                    sign_off(obj, "approved")
                    st.rerun()

    st.divider()
    st.markdown('<div class="eyebrow">Certified metrics</div>', unsafe_allow_html=True)
    st.caption("A metric your analyst writes once. Every future question that needs it reuses "
               "this exact expression instead of re-deriving it.")
    for m in okf.get("metrics", []):
        st.markdown(f'<div class="card hi"><span class="ident">{m["name"]}</span>'
                    f'<div class="phys">{m["expression"]} on {m["table"]}</div>'
                    f'<div>{m["description"]}</div></div>', unsafe_allow_html=True)
    with st.form("metric", clear_on_submit=True):
        f1, f2 = st.columns(2)
        mn = f1.text_input("Metric name", placeholder="net_revenue")
        mt = f2.selectbox("On table", [t["name"] for t in okf["tables"]])
        me = st.text_input("SQL expression", placeholder='SUM("total_revenue")')
        md = st.text_input("Definition in plain English",
                           placeholder="Gross revenue after discounts, excluding cancelled orders.")
        if st.form_submit_button("Certify this metric") and mn and me:
            okf.setdefault("metrics", []).append({
                "name": parsers.normalise_identifier(mn, "m"), "table": mt,
                "expression": me, "description": md, "status": "approved",
                "provenance": "human_authored"})
            st.rerun()

    if okf["relationships"]:
        st.markdown('<div class="eyebrow">Proposed joins</div>', unsafe_allow_html=True)
        for i, r in enumerate(okf["relationships"]):
            cA, cB = st.columns([4, 1])
            cA.markdown(f'<div class="card {band(r["confidence"])}">'
                        f'<span class="ident">{r["from"]} = {r["to"]}</span>'
                        f'<div class="phys">{r["cardinality"]} · status {r["status"]}</div>'
                        f'{gauge(r["confidence"])}</div>', unsafe_allow_html=True)
            if cB.button("Approve", key=f"rel{i}"):
                r.setdefault("ai_confidence", r["confidence"])
                r["status"] = "approved"
                r["confidence"] = 1.0
                st.rerun()

# ------------------------------------------------------------------ step 3
else:
    okf = st.session_state.okf
    con = st.session_state.con
    st.markdown(f'<div class="sub">{" · ".join(f"{k} ({v:,} rows)" for k, v in st.session_state.counts.items())}</div>',
                unsafe_allow_html=True)

    ideas = suggest.initial_suggestions(okf, con)
    if ideas:
        st.markdown('<div class="eyebrow">What this semantic layer can answer</div>',
                    unsafe_allow_html=True)
        cols = st.columns(len(ideas))
        for i, s_ in enumerate(ideas):
            if cols[i].button(s_, key=f"sug{i}", width="stretch"):
                ask(s_)

    if st.session_state.pending:
        st.session_state.q_input = st.session_state.pending
        st.session_state.pending = None
    q = st.text_input("Ask a question", key="q_input",
                      placeholder="Which region generated the most revenue?")

    res = st.session_state.result
    if q and (res is None or res.get("q") != q):
        stages = []
        with st.spinner("Writing SQL from the approved definitions…"):
            sql = safety.strip_fences(llm.call_llm(llm.build_sql_prompt(okf, q)))

        if sql.startswith(llm.NO_ANSWER):
            st.session_state.result = {"q": q, "refused": sql.split(":", 1)[-1].strip()}
            st.rerun()

        stages.append(("1", "Coder", "SQL written from the approved OKF only", True))
        ok, errs, safe_sql = safety.check_sql(sql, okf)
        if not ok:
            stages.append(("2", "Critic", f"rejected — {errs[0]}", False))
            with st.spinner("Rewriting…"):
                sql = safety.strip_fences(llm.call_llm(
                    llm.build_repair_prompt(okf, q, sql, errs)))
            ok, errs, safe_sql = safety.check_sql(sql, okf)

        if not ok:
            st.session_state.result = {"q": q, "blocked": errs, "stages": stages}
            st.rerun()

        eok, emsg = safety.explain_ok(con, safe_sql)
        if not eok:
            stages.append(("2", "Critic", f"EXPLAIN failed — {emsg}", False))
            st.session_state.result = {"q": q, "blocked": [emsg], "stages": stages}
            st.rerun()

        stages.append(("2", "Critic", "read-only · joins approved · EXPLAIN clean", True))
        df = con.execute(safe_sql).df()
        stages.append(("3", "Executed", f"{len(df)} aggregated rows returned", True))
        with st.spinner("Reading the result…"):
            answer = llm.call_llm(llm.build_explain_prompt(
                q, safe_sql, df.head(20).to_markdown(index=False))) if not df.empty else \
                "The query ran and matched no rows."
        st.session_state.result = {"q": q, "sql": safe_sql, "df": df,
                                   "answer": answer.strip(), "stages": stages}
        st.session_state.chart = None
        st.rerun()

    res = st.session_state.result
    if res and res.get("q") == q and q:
        if res.get("refused"):
            st.warning(f'**Not in your data.** {res["refused"]}')
            st.caption("This is an upstream collection gap, not a query failure. Nothing in "
                       "the warehouse can answer it.")
            st.stop()

        st.markdown('<div class="eyebrow">Validation</div>', unsafe_allow_html=True)
        for n, label, detail, ok_ in res["stages"]:
            trace(n, label, detail, ok_)

        if res.get("blocked"):
            st.error("Blocked before execution. Nothing was run against the database.")
            for e in res["blocked"]:
                st.markdown(f"- {e}")
            st.stop()

        df = res["df"]
        st.markdown(f'<div class="answer">{res["answer"]}</div>', unsafe_allow_html=True)

        if not df.empty:
            default = suggest.suggest_chart(df)
            options = ["Bar", "Line", "Table"]
            if st.session_state.chart not in options:
                st.session_state.chart = default
            choice = st.radio("View as", options, horizontal=True,
                              index=options.index(st.session_state.chart),
                              key="chart_choice", label_visibility="collapsed")
            st.session_state.chart = choice

            x = df.columns[0]
            y = df.columns[1] if df.shape[1] > 1 else None
            plottable = y is not None and pd.api.types.is_numeric_dtype(df[y])
            if choice == "Bar" and plottable:
                st.bar_chart(df, x=x, y=y, height=330)
            elif choice == "Line" and plottable:
                st.line_chart(df, x=x, y=y, height=330)
            st.dataframe(df, width="stretch", hide_index=True)

            nxt = suggest.followups(okf, res["sql"], con)
            if nxt:
                st.markdown('<div class="eyebrow">Next</div>', unsafe_allow_html=True)
                fcols = st.columns(len(nxt))
                for i, s_ in enumerate(nxt):
                    if fcols[i].button(s_, key=f"nx{i}", width="stretch"):
                        ask(s_)

        with st.expander("Show the SQL that ran"):
            st.code(res["sql"], language="sql")
            st.caption("Only aggregated results were returned. No raw rows left the database.")
