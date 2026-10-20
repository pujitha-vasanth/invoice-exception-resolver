"""Web app for the Invoice Exception Resolver.

Start it with:   streamlit run app.py
"""

import asyncio
import json

import pandas as pd
import streamlit as st

from resolver import config, db, matching, seed_data, workflow

st.set_page_config(page_title="Invoice Exception Resolver", page_icon=":receipt:", layout="wide")


def fresh_sample_data():
    seed_data.reset_database()
    c = db.connect()
    matching.run_matching(c)
    c.close()


if not config.db_path().exists():
    fresh_sample_data()

conn = db.connect()
llm_problems = config.llm_config_problems()

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Settings")
    if llm_problems:
        st.error("LLM not configured. Fix in .env:\n\n- " + "\n- ".join(llm_problems))
    else:
        model = config.AZURE_OPENAI_DEPLOYMENT if config.LLM_PROVIDER == "azure" else config.OPENAI_MODEL
        st.success(f"LLM ready: {config.LLM_PROVIDER} / {model}")
    st.write(f"Human approval needed for any payment, and for any amount at or above "
             f"**{config.money(config.APPROVAL_THRESHOLD)}**.")
    approver = st.text_input("Your name (for approvals)", value=st.session_state.get("approver", ""))
    st.session_state["approver"] = approver
    st.divider()
    if st.button("Reset sample data", help="Deletes all decisions and reloads the sample invoices"):
        conn.close()
        fresh_sample_data()
        st.session_state.pop("last_run", None)
        st.rerun()

st.title("Invoice Exception Resolver")
st.caption("Three-way match runs in code. Only exceptions go to the agents: a triage agent routes each one to a "
           "price, quantity or duplicate specialist. Payments and large amounts always wait for a person.")

tab_inbox, tab_queue, tab_detail, tab_impact = st.tabs(["Inbox", "Approval queue", "Invoice detail", "Impact"])

# ---------------- inbox ----------------
with tab_inbox:
    invoices = db.list_invoices(conn)
    open_ids = [i["invoice_id"] for i in invoices if i["status"] == "EXCEPTION"]

    counts = pd.Series([i["status"] for i in invoices]).value_counts()
    cols = st.columns(4)
    cols[0].metric("Open exceptions", int(counts.get("EXCEPTION", 0)))
    cols[1].metric("Waiting for a person", int(counts.get("AWAITING_APPROVAL", 0)))
    cols[2].metric("Matched in code", int(counts.get("MATCHED", 0)))
    cols[3].metric("Ready to pay", int(counts.get("READY_TO_PAY", 0)))

    left, right = st.columns([2, 1])
    with left:
        chosen = st.multiselect("Open exceptions to resolve", open_ids, default=open_ids)
    with right:
        st.write("")
        st.write("")
        run_clicked = st.button("Resolve with agents", type="primary",
                                disabled=bool(llm_problems) or not chosen, width="stretch")

    if run_clicked:
        from resolver.runner import resolve_invoices

        with st.status(f"Agents working on {len(chosen)} invoice(s)...", expanded=True) as box:
            def progress(outcome):
                if outcome["ok"]:
                    box.write(f"**{outcome['invoice_id']}**: {outcome['agent']} chose `{outcome['action']}` "
                              f"-> {outcome['status']} ({outcome['seconds']}s)")
                else:
                    box.write(f"**{outcome['invoice_id']}**: not resolved. {outcome['error']}")
            results = asyncio.run(resolve_invoices(chosen, on_done=progress))
            box.update(label="Done", state="complete")
        st.session_state["last_run"] = results
        st.rerun()

    if st.session_state.get("last_run"):
        with st.expander("Last run", expanded=True):
            for r in st.session_state["last_run"]:
                if r["ok"]:
                    st.write(f"**{r['invoice_id']}** · {r['agent']} · `{r['action']}` · {r['status']}  \n{r['summary']}")
                else:
                    st.warning(f"{r['invoice_id']}: {r['error']}")

    table = pd.DataFrame(invoices)
    table["flags"] = table["match_flags"].apply(lambda f: ", ".join(json.loads(f)))
    st.dataframe(
        table[["invoice_id", "vendor", "vendor_invoice_no", "po_number", "invoice_date", "total_amount", "status", "flags"]],
        hide_index=True, width="stretch",
        column_config={"total_amount": st.column_config.NumberColumn("total", format="%.2f")},
    )

# ---------------- approval queue ----------------
with tab_queue:
    pending = db.pending_approvals(conn)
    if not pending:
        st.info("Nothing waiting for approval.")
    for p in pending:
        with st.container(border=True):
            st.subheader(f"{p['invoice_id']} · {p['vendor']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Recommended action", p["action"])
            c2.metric("Amount at stake", config.money(p["exposure_amount"]))
            c3.metric("Payment if approved", config.money(p["payment_amount"]))
            st.write(p["summary"])
            st.caption(f"Policy: {', '.join(json.loads(p['policy_refs']))} · Agent: {p['agent_name']} · "
                       f"Needs a person because it {p['approval_reason']}")
            note = st.text_input("Note", key=f"note_{p['resolution_id']}")
            b1, b2, _ = st.columns([1, 1, 4])
            for column, label, approve in ((b1, "Approve", True), (b2, "Reject", False)):
                if column.button(label, key=f"{label}_{p['resolution_id']}", type="primary" if approve else "secondary"):
                    if not approver.strip():
                        st.error("Enter your name in the sidebar first.")
                    else:
                        workflow.decide(conn, p["resolution_id"], approve, approver, note)
                        st.rerun()

# ---------------- invoice detail ----------------
with tab_detail:
    all_ids = [i["invoice_id"] for i in db.list_invoices(conn)]
    invoice_id = st.selectbox("Invoice", all_ids, index=all_ids.index("INV-1004") if "INV-1004" in all_ids else 0)
    inv = db.get_invoice(conn, invoice_id)
    st.write(f"**{inv['vendor_name']}** · vendor invoice {inv['vendor_invoice_no']} · PO {inv['po_number']} · "
             f"{inv['invoice_date']} · {config.money(inv['total_amount'])} · status **{inv['status']}**")
    if inv["vendor_note"]:
        st.warning(f"Vendor note: {inv['vendor_note']}")

    analysis = matching.analyze_invoice(conn, invoice_id)
    lines = [l for l in analysis["lines"] if "po_unit_price" in l]
    if lines:
        st.write("Three-way match (calculated in code)")
        st.dataframe(pd.DataFrame(lines)[[
            "line_no", "description", "po_qty", "received_qty", "invoiced_qty", "po_unit_price",
            "invoiced_unit_price", "price_variance_pct", "price_variance_amount", "quantity_variance_amount",
        ]], hide_index=True, width="stretch")

    res = db.latest_resolution(conn, invoice_id)
    if res:
        st.subheader("Agent resolution")
        st.write(f"**{res['agent_name']}** chose `{res['action']}` · status **{res['status']}** · "
                 f"{res['agent_seconds'] or '-'}s")
        st.write(res["summary"])
        st.caption("Policy: " + ", ".join(json.loads(res["policy_refs"])))
        if res["decided_by"]:
            st.caption(f"Decided by {res['decided_by']} at {res['decided_at']}. Note: {res['decision_note'] or '-'}")
        with st.expander("Agent steps (think, act, observe)"):
            for step in json.loads(res["agent_trace"] or "[]"):
                label = {"thought": "Thought", "tool_call": f"Call {step.get('tool', '')}",
                         "tool_result": "Result", "handoff": "Handoff"}[step["kind"]]
                st.markdown(f"**{step['agent']} · {label}**")
                st.code(step["text"], language=None)

    with st.expander("Audit trail"):
        st.dataframe(pd.DataFrame(db.audit_trail(conn, invoice_id)), hide_index=True, width="stretch")

# ---------------- impact ----------------
with tab_impact:
    resolutions = db.rows(conn, "SELECT * FROM resolutions")
    if not resolutions:
        st.info("Run the agents first. Impact numbers appear here after that.")
    else:
        df = pd.DataFrame(resolutions)
        protected = df[df["action"].isin(["reject_duplicate", "hold", "escalate_to_buyer", "short_pay"])]["exposure_amount"].sum()
        timed = df["agent_seconds"].dropna()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Exceptions investigated by agents", len(df))
        c2.metric("Average agent time", f"{timed.mean():.0f}s" if len(timed) else "-")
        c3.metric("Money stopped from wrong payment", config.money(protected))
        c4.metric("Estimated clerk hours saved", f"{len(df) * config.MANUAL_MINUTES_PER_EXCEPTION / 60:.1f}")
        st.caption(f"Hours saved assumes {config.MANUAL_MINUTES_PER_EXCEPTION:.0f} minutes of manual investigation per "
                   "exception (set MANUAL_MINUTES_PER_EXCEPTION in .env). It is an estimate, not a measurement.")
        st.dataframe(df[["invoice_id", "agent_name", "action", "status", "exposure_amount", "payment_amount", "agent_seconds"]],
                     hide_index=True, width="stretch")

conn.close()
