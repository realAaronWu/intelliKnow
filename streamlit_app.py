"""IntelliKnow's five-view Streamlit administration console."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from html import escape

import streamlit as st

from app.ui.client import APIClient, APIError
from app.ui.style import apply_style, page_header, section_title, status_label


st.set_page_config(
    page_title="IntelliKnow Admin",
    page_icon=":material/library_books:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_style()

VIEWS = [
    "Dashboard",
    "Frontend Integration",
    "Knowledge Base",
    "Intent Configuration",
    "Analytics",
]


def _client() -> APIClient:
    return APIClient(st.session_state.api_url, st.session_state.admin_token)


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except APIError as exc:
        st.error(str(exc), icon=":material/error:")
        return None


def _sign_in() -> bool:
    if st.session_state.get("authenticated"):
        return True
    left, middle, right = st.columns([1, 1.15, 1])
    with middle:
        st.markdown("<div style='height:9vh'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(
                "<div class='ik-brand'><div class='ik-brand-mark'>IK</div>"
                "<div><div class='ik-brand-name'>IntelliKnow</div>"
                "<div class='ik-brand-sub'>Knowledge management console</div></div></div>",
                unsafe_allow_html=True,
            )
            with st.form("sign-in"):
                api_url = st.text_input(
                    "API address",
                    value=st.session_state.get(
                        "api_url", os.getenv("INTELLIKNOW_API_URL", "http://127.0.0.1:8000")
                    ),
                )
                password = st.text_input("Admin password", type="password")
                submitted = st.form_submit_button(
                    "Sign in", type="primary", icon=":material/login:", width="stretch"
                )
            if submitted:
                candidate = APIClient(api_url, password)
                try:
                    candidate.get("/admin/session")
                except APIError as exc:
                    st.error(str(exc), icon=":material/lock:")
                else:
                    st.session_state.api_url = api_url.rstrip("/")
                    st.session_state.admin_token = password
                    st.session_state.authenticated = True
                    st.rerun()
    return False


def _sidebar() -> str:
    with st.sidebar:
        st.markdown(
            "<div class='ik-brand'><div class='ik-brand-mark'>IK</div>"
            "<div><div class='ik-brand-name'>IntelliKnow</div>"
            "<div class='ik-brand-sub'>Admin console</div></div></div>",
            unsafe_allow_html=True,
        )
        view = st.radio("Navigation", VIEWS, label_visibility="collapsed")
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        st.caption(st.session_state.api_url)
        if st.button("Sign out", icon=":material/logout:", width="stretch"):
            for key in ("authenticated", "admin_token"):
                st.session_state.pop(key, None)
            st.rerun()
    return view


def _metric_row(items: list[tuple[str, object, str | None]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, delta) in zip(columns, items):
        with column:
            with st.container(border=True):
                st.metric(label, value, delta)


def dashboard() -> None:
    page_header("Dashboard", "System health, knowledge coverage, and a live query check.", "coral")
    data = _call(_client().get, "/admin/dashboard")
    if data is None:
        return
    _metric_row(
        [
            ("Documents", data["document_count"], None),
            ("Knowledge chunks", data["chunk_count"], None),
            ("Queries, 7 days", data["queries_last_7_days"], None),
            ("Failed documents", data["failed_documents"], None),
        ]
    )
    left, right = st.columns([1.25, 1])
    with left:
        with st.container(border=True):
            section_title("Knowledge coverage", "By intent")
            distribution = data["documents_by_intent"]
            if distribution:
                st.bar_chart(distribution, color="#16835f", horizontal=True)
            else:
                st.info("No indexed documents yet.", icon=":material/library_books:")
    with right:
        with st.container(border=True):
            section_title("Connected frontends", "Live status")
            for item in data["integrations"]:
                connected = item["status"] == "connected"
                status_label(connected, item["channel"].title())
                st.caption(item["last_error"] or ("Ready" if connected else "No successful exchange yet"))
            cfg = data["config"]
            st.divider()
            st.caption("ACTIVE AI")
            st.write(f"**{cfg['llm']['provider'].title()}**  ·  {cfg['llm']['model_generate']}")
            st.caption(
                f"Confidence {cfg['orchestrator']['confidence_threshold']:.0%}  ·  "
                f"Relevance {cfg['rag']['relevance_floor']:.0%}"
            )
    with st.container(border=True):
        section_title("Try a query", "Full pipeline")
        with st.form("dashboard-query"):
            question = st.text_input("Question", placeholder="How many days of annual leave do employees receive?")
            run = st.form_submit_button("Run query", type="primary", icon=":material/play_arrow:")
        if run and question.strip():
            with st.spinner("Searching the knowledge base"):
                result = _call(_client().post, "/admin/test-query", json={"question": question.strip()})
            if result:
                cols = st.columns(3)
                cols[0].metric("Intent", result["intent_slug"].title())
                cols[1].metric("Confidence", f"{result['confidence']:.0%}")
                cols[2].metric("Latency", f"{result['latency_ms']} ms")
                if result["status"] == "success":
                    st.success(result["answer"], icon=":material/check_circle:")
                elif result["status"] == "no_match":
                    st.info(result["answer"], icon=":material/search_off:")
                else:
                    st.error(result["error"] or result["answer"], icon=":material/error:")
                for source in result["sources"]:
                    st.markdown(
                        f"<div class='ik-source'><strong>{escape(source['document_title'])}</strong>"
                        f"<br><span class='ik-muted'>{escape(source.get('source_ref') or '')}</span></div>",
                        unsafe_allow_html=True,
                    )


def _credential_fields(channel: str) -> dict[str, str]:
    if channel == "telegram":
        return {"token": st.text_input("Bot token", type="password", key="telegram-token")}
    return {
        "app_id": st.text_input("Application ID", key="teams-id"),
        "app_password": st.text_input("Application password", type="password", key="teams-password"),
    }


def frontend_integration() -> None:
    page_header("Frontend Integration", "Connect and verify Telegram and Microsoft Teams.", "blue")
    items = _call(_client().get, "/admin/integrations")
    if items is None:
        return
    for item in items:
        channel = item["channel"]
        with st.container(border=True):
            title_col, state_col = st.columns([3, 1])
            with title_col:
                section_title(item["display_name"], "Chat frontend")
            with state_col:
                status_label(item["status"] == "connected", item["status"].title())
            masked = item.get("credentials", {})
            if masked:
                values = [f"{key.replace('_', ' ').title()}: {value}" for key, value in masked.items() if key != "source"]
                st.caption("  ·  ".join(values) + f"  ·  {masked.get('source', 'stored').title()}")
            else:
                setup = (
                    "Create a bot with Telegram BotFather, then enter its bot token."
                    if channel == "telegram"
                    else "Register a Microsoft Bot application, then enter its application ID and password."
                )
                st.info(setup, icon=":material/key:")
            if item.get("last_ok_at"):
                st.caption(f"Last successful exchange: {item['last_ok_at']}")
            if item.get("credential_error"):
                st.error(item["credential_error"], icon=":material/lock:")
            with st.expander("Configuration", expanded=not item["configured"]):
                with st.form(f"config-{channel}"):
                    credentials = _credential_fields(channel)
                    enabled = st.toggle("Enabled", value=item["enabled"])
                    saved = st.form_submit_button("Save", type="primary", icon=":material/save:")
                if saved:
                    if any(not value.strip() for value in credentials.values()):
                        st.error("Enter every required credential before saving.")
                    elif _call(
                        _client().put,
                        f"/admin/integrations/{channel}",
                        json={"credentials": credentials, "enabled": enabled},
                    ) is not None:
                        st.success("Configuration saved.")
                        time.sleep(0.25)
                        st.rerun()
            question = st.text_input(
                "Test question",
                value="How many days of annual leave do full-time employees receive?",
                key=f"test-question-{channel}",
            )
            action_cols = st.columns([1, 1, 3])
            if action_cols[0].button("Test", key=f"test-{channel}", type="primary", icon=":material/send:"):
                with st.spinner(f"Testing {item['display_name']}"):
                    result = _call(
                        _client().post,
                        f"/admin/integrations/{channel}/test",
                        json={"question": question},
                    )
                if result:
                    message = f"{result['stage'].title()} · {result['latency_ms']} ms"
                    (st.success if result["ok"] else st.error)(result.get("error") or message)
            if action_cols[1].button("Clear", key=f"clear-{channel}", icon=":material/delete:"):
                st.session_state[f"confirm-clear-{channel}"] = True
            if st.session_state.get(f"confirm-clear-{channel}"):
                st.warning("This removes the stored credentials and disables the channel.")
                confirm, cancel, _ = st.columns([1, 1, 3])
                if confirm.button("Confirm clear", key=f"confirm-clear-action-{channel}", type="primary"):
                    if _call(_client().delete, f"/admin/integrations/{channel}") is not None:
                        st.session_state.pop(f"confirm-clear-{channel}", None)
                        st.rerun()
                if cancel.button("Cancel", key=f"cancel-clear-{channel}"):
                    st.session_state.pop(f"confirm-clear-{channel}", None)
                    st.rerun()
            errors = item.get("recent_errors", [])
            if errors:
                with st.expander(f"Recent errors ({len(errors)})"):
                    for error in errors:
                        st.markdown(f"**{error['created_at']}**  ")
                        st.caption(error["reason"])


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _show_upload_results(results: list[dict]) -> None:
    if not results:
        return
    succeeded = [item for item in results if item["status"] == "indexed"]
    failed = [item for item in results if item["status"] != "indexed"]
    if succeeded:
        names = ", ".join(item["filename"] for item in succeeded)
        st.success(f"Searchable ({len(succeeded)}): {names}")
    for item in failed:
        st.error(f"{item['filename']}: {item['error']}", icon=":material/error:")


def knowledge_base() -> None:
    page_header("Knowledge Base", "Upload, organize, inspect, and refresh source documents.", "green")
    config = _call(_client().get, "/admin/config")
    intents = _call(_client().get, "/admin/intents")
    if config is None or intents is None:
        return
    with st.container(border=True):
        section_title("Upload document", "Build knowledge")
        extensions = [item.lstrip(".").upper() for item in config["ingestion"]["allowed_extensions"]]
        upload_results = st.session_state.pop("document-upload-results", None)
        if upload_results:
            _show_upload_results(upload_results)
        uploader_version = st.session_state.get("document-uploader-version", 0)
        uploaded_files = st.file_uploader(
            f"PDF, DOCX, or XLSX up to {config['ingestion']['max_upload_mb']} MB",
            type=[item.lower() for item in extensions],
            accept_multiple_files=True,
            key=f"document-uploader-{uploader_version}",
        )
        if uploaded_files and st.button("Upload all", type="primary", icon=":material/upload:"):
            progress = st.progress(0, text=f"Uploading 0 of {len(uploaded_files)} files")
            pending: dict[int, str] = {}
            results: list[dict] = []
            client = _client()

            for index, uploaded in enumerate(uploaded_files, start=1):
                progress.progress(
                    int(index / len(uploaded_files) * 45),
                    text=f"Uploading {index} of {len(uploaded_files)}: {uploaded.name}",
                )
                try:
                    result = client.upload(
                        uploaded.name,
                        uploaded.getvalue(),
                        uploaded.type or "application/octet-stream",
                    )
                except APIError as exc:
                    results.append(
                        {"filename": uploaded.name, "status": "failed", "error": str(exc)}
                    )
                else:
                    pending[result["id"]] = uploaded.name

            for attempt in range(60):
                if not pending:
                    break
                completed: list[int] = []
                for doc_id, filename in pending.items():
                    try:
                        detail = client.get(f"/documents/{doc_id}")
                    except APIError as exc:
                        results.append(
                            {"filename": filename, "status": "failed", "error": str(exc)}
                        )
                        completed.append(doc_id)
                        continue
                    if detail["status"] == "indexed":
                        results.append(
                            {"filename": filename, "status": "indexed", "error": None}
                        )
                        completed.append(doc_id)
                    elif detail["status"] == "failed":
                        results.append(
                            {
                                "filename": filename,
                                "status": "failed",
                                "error": detail.get("error_message")
                                or "Document processing failed.",
                            }
                        )
                        completed.append(doc_id)
                for doc_id in completed:
                    pending.pop(doc_id, None)
                progress.progress(
                    min(95, 50 + int((attempt + 1) / 60 * 45)),
                    text=f"Processing {len(pending)} remaining file(s)",
                )
                if pending:
                    time.sleep(0.5)

            for filename in pending.values():
                results.append(
                    {
                        "filename": filename,
                        "status": "pending",
                        "error": "Processing is still running. Check the document library shortly.",
                    }
                )
            progress.progress(100, text="Batch complete")
            st.session_state["document-upload-results"] = results
            st.session_state["document-uploader-version"] = uploader_version + 1
            st.rerun()
    with st.container(border=True):
        section_title("Document library", "Search and filter")
        filter_cols = st.columns([2, 1, 1, 1])
        search = filter_cols[0].text_input("Search", placeholder="Name or keyword")
        format_filter = filter_cols[1].selectbox("Format", ["All", "PDF", "DOCX", "XLSX"])
        intent_filter = filter_cols[2].selectbox("Intent", ["All"] + [item["slug"] for item in intents])
        date_filter = filter_cols[3].selectbox("Uploaded", ["Any time", "7 days", "30 days", "90 days"])
        params = {}
        if search:
            params["q"] = search
        if format_filter != "All":
            params["format"] = format_filter.lower()
        if intent_filter != "All":
            params["intent_space"] = intent_filter
        if date_filter != "Any time":
            days = int(date_filter.split()[0])
            params["date_from"] = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        docs = _call(_client().get, "/documents", params=params)
        if docs is None:
            return
        table = [
            {
                "ID": item["id"],
                "Document Name": item["filename"],
                "Upload Date": item["uploaded_at"][:10],
                "Format": item["ext"].lstrip(".").upper(),
                "Size": _format_size(item["size_bytes"]),
                "Intent": item["intent_slug"],
                "Status": {"indexed": "Processed", "failed": "Error"}.get(item["status"], "Pending"),
            }
            for item in docs
        ]
        if not table:
            st.info("No documents match these filters.", icon=":material/search_off:")
            return
        selection = st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="document-table",
        )
        selected_rows = selection.selection.rows
        selected_id = table[selected_rows[0]]["ID"] if selected_rows else table[0]["ID"]
        detail = _call(_client().get, f"/documents/{selected_id}")
        if detail:
            st.divider()
            st.subheader(detail["filename"])
            meta = st.columns(4)
            meta[0].metric("Status", {"indexed": "Processed", "failed": "Error"}.get(detail["status"], "Pending"))
            meta[1].metric(
                "Intent",
                "Unclassified"
                if detail["intent_slug"] == "unclassified"
                else detail["intent_slug"].title(),
            )
            meta[2].metric("Chunks", detail["chunk_count"])
            meta[3].metric("Size", _format_size(detail["size_bytes"]))
            if detail.get("error_message"):
                st.error(detail["error_message"], icon=":material/error:")
            action_cols = st.columns([1.4, 1.4, 1, 4])
            intent_slugs = [item["slug"] for item in intents]
            current_intent = detail["intent_slug"]
            current_intent_index = (
                intent_slugs.index(current_intent) if current_intent in intent_slugs else None
            )
            new_intent = action_cols[0].selectbox(
                "Assigned intent",
                intent_slugs,
                index=current_intent_index,
                placeholder="Select an intent",
                key=f"assign-{selected_id}",
            )
            intent_selected = new_intent is not None
            intent_changed = intent_selected and new_intent != current_intent
            if action_cols[1].button(
                "Assign" if current_intent == "unclassified" else "Reassign",
                key=f"reassign-{selected_id}",
                icon=":material/drive_file_move:",
                disabled=not intent_changed,
            ):
                if _call(_client().patch, f"/documents/{selected_id}", json={"intent_slug": new_intent}) is not None:
                    st.rerun()
            if action_cols[2].button(
                "Re-process",
                key=f"reparse-{selected_id}",
                icon=":material/refresh:",
                disabled=current_intent == "unclassified",
                help=(
                    "Assign an intent before re-processing this document."
                    if current_intent == "unclassified"
                    else None
                ),
            ):
                if _call(_client().post, f"/documents/{selected_id}/reparse", json={}) is not None:
                    st.success("Re-processing started.")
                    time.sleep(0.35)
                    st.rerun()
            if st.button("Delete document", key=f"delete-{selected_id}", icon=":material/delete:"):
                st.session_state["confirm-delete-doc"] = selected_id
            if st.session_state.get("confirm-delete-doc") == selected_id:
                st.warning(f"Delete {detail['filename']} and all indexed chunks?")
                yes, no, _ = st.columns([1, 1, 4])
                if yes.button("Confirm delete", key=f"confirm-doc-{selected_id}", type="primary"):
                    _call(_client().delete, f"/documents/{selected_id}")
                    st.session_state.pop("confirm-delete-doc", None)
                    st.rerun()
                if no.button("Cancel", key=f"cancel-doc-{selected_id}"):
                    st.session_state.pop("confirm-delete-doc", None)
                    st.rerun()
            with st.expander(f"Extracted content ({len(detail['chunks'])} chunks)"):
                for chunk in detail["chunks"]:
                    st.caption(chunk.get("source_ref") or f"Chunk {chunk['ordinal'] + 1}")
                    st.write(chunk["text"])


def intent_configuration() -> None:
    page_header("Intent Configuration", "Tune routing domains and review classification outcomes.", "purple")
    intents = _call(_client().get, "/admin/intents")
    config = _call(_client().get, "/admin/config")
    if intents is None or config is None:
        return
    cols = st.columns(3)
    for index, item in enumerate(intents):
        with cols[index % 3]:
            with st.container(border=True):
                section_title(item["name"], item["slug"])
                st.write(item["description"])
                accuracy = item.get("reviewed_accuracy")
                accuracy_text = f"{accuracy['accuracy']:.0%}" if accuracy else "Not enough reviewed data"
                st.caption(f"{item['document_count']} documents  ·  Accuracy: {accuracy_text}")
                if item["protected"]:
                    st.caption("Required protected space")
    left, right = st.columns([1.35, 1])
    with left:
        with st.container(border=True):
            section_title("Intent editor", "Create or edit")
            choice = st.selectbox("Intent space", ["Create new"] + [item["slug"] for item in intents])
            existing = next((item for item in intents if item["slug"] == choice), None)
            with st.form("intent-editor"):
                name = st.text_input("Name", value=existing["name"] if existing else "")
                slug = st.text_input(
                    "Slug",
                    value=existing["slug"] if existing else "",
                    disabled=existing is not None,
                    help="Lowercase identifier used by routing. Spaces and capitals are normalized when saved.",
                    placeholder="tech or it-support",
                )
                description = st.text_area("Description", value=existing["description"] if existing else "")
                keywords = st.text_input(
                    "Classification keywords",
                    value=", ".join(existing["keywords"]) if existing else "",
                )
                st.caption("Description and keywords shape the classifier and affect routing accuracy.")
                save = st.form_submit_button("Save intent", type="primary", icon=":material/save:")
            if save:
                payload = {
                    "name": name,
                    "description": description,
                    "keywords": [item.strip() for item in keywords.split(",") if item.strip()],
                }
                if slug:
                    payload["slug"] = slug
                result = (
                    _call(_client().put, f"/admin/intents/{choice}", json=payload)
                    if existing
                    else _call(_client().post, "/admin/intents", json=payload)
                )
                if result:
                    st.success("Intent space saved.")
                    time.sleep(0.3)
                    st.rerun()
            if existing and not existing["protected"]:
                if st.button("Delete intent", icon=":material/delete:"):
                    st.session_state["confirm-delete-intent"] = choice
                if st.session_state.get("confirm-delete-intent") == choice:
                    st.warning(f"Delete {existing['name']}? Assigned documents must be moved first.")
                    yes, no = st.columns(2)
                    if yes.button("Confirm delete", type="primary", key="intent-delete-yes"):
                        if _call(_client().delete, f"/admin/intents/{choice}") is not None:
                            st.session_state.pop("confirm-delete-intent", None)
                            st.rerun()
                    if no.button("Cancel", key="intent-delete-no"):
                        st.session_state.pop("confirm-delete-intent", None)
                        st.rerun()
    with right:
        with st.container(border=True):
            section_title("Routing thresholds", "Live settings")
            confidence = st.slider(
                "Classification confidence",
                0.0,
                1.0,
                float(config["orchestrator"]["confidence_threshold"]),
                0.01,
            )
            relevance = st.slider(
                "Knowledge relevance floor",
                0.0,
                1.0,
                float(config["rag"]["relevance_floor"]),
                0.01,
            )
            if st.button("Save thresholds", type="primary", icon=":material/tune:"):
                result = _call(
                    _client().patch,
                    "/admin/config",
                    json={"confidence_threshold": confidence, "relevance_floor": relevance},
                )
                if result:
                    st.success("Thresholds apply to the next query.")
    with st.container(border=True):
        section_title("Classification log", "Review outcomes")
        queries = _call(_client().get, "/admin/queries", params={"limit": 20})
        if not queries or not queries["items"]:
            st.info("No query classifications have been logged yet.", icon=":material/history:")
            return
        table = [
            {
                "ID": item["id"],
                "Time": item["created_at"],
                "Query": item["question"],
                "Intent": item["intent_slug"],
                "Confidence": f"{(item['confidence'] or 0):.0%}",
                "Status": item["status"].replace("_", " ").title(),
                "Reviewed": "Yes" if item["reviewed_correct"] is not None else "No",
            }
            for item in queries["items"]
        ]
        st.dataframe(table, hide_index=True, width="stretch")
        review_cols = st.columns([1, 2, 2])
        query_id = review_cols[0].selectbox(
            "Query ID", [item["id"] for item in queries["items"]]
        )
        expected = review_cols[1].selectbox("Expected intent", [item["slug"] for item in intents])
        if review_cols[2].button("Record review", type="primary", icon=":material/rate_review:"):
            result = _call(
                _client().put,
                f"/admin/queries/{query_id}/review",
                json={"expected_intent_slug": expected},
            )
            if result:
                verdict = "correct" if result["reviewed_correct"] else "incorrect"
                st.success(f"Review saved: classification was {verdict}.")
                time.sleep(0.3)
                st.rerun()


def _date_params(period: str) -> dict[str, str]:
    days = {"7 days": 7, "30 days": 30, "90 days": 90, "1 year": 365}.get(period)
    if not days:
        return {}
    return {
        "date_from": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    }


def analytics() -> None:
    page_header("Analytics", "Query demand, knowledge usage, quality review, and history.", "coral")
    period = st.segmented_control("Period", ["7 days", "30 days", "90 days", "1 year", "All time"], default="30 days")
    params = _date_params(period or "30 days")
    data = _call(_client().get, "/admin/analytics", params=params)
    intents = _call(_client().get, "/admin/intents")
    if data is None or intents is None:
        return
    reviewed = data["reviewed_accuracy"]
    _metric_row(
        [
            ("Queries", data["query_count"], None),
            ("Reviewed accuracy", f"{reviewed['value']:.0%}" if reviewed["available"] else "Not available", None),
            ("High-confidence share", f"{data['high_confidence_share']:.0%}" if data["high_confidence_share"] is not None else "Not available", None),
            ("Average latency", f"{data['average_latency_ms']} ms" if data["average_latency_ms"] is not None else "Not available", None),
        ]
    )
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            section_title("Intent distribution", "Query volume")
            if data["intent_distribution"]:
                st.bar_chart(data["intent_distribution"], color="#7c3aed", horizontal=True)
            else:
                st.info("No queries in this period.", icon=":material/bar_chart:")
    with right:
        with st.container(border=True):
            section_title("Most accessed documents", "Retrieval count")
            docs = data["most_accessed_documents"]
            if docs:
                st.dataframe(
                    [{"Document": item["document_title"], "Accesses": item["access_count"]} for item in docs[:10]],
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.info("No documents were retrieved in this period.", icon=":material/find_in_page:")
    with st.container(border=True):
        title_col, download_col = st.columns([4, 1])
        with title_col:
            section_title("Query history", "Newest first")
        csv_bytes = _call(_client().csv, "/admin/analytics/export", params=params)
        if csv_bytes is not None:
            download_col.download_button(
                "Export CSV",
                csv_bytes,
                file_name="intelliknow-queries.csv",
                mime="text/csv",
                icon=":material/download:",
                width="stretch",
            )
        filters = st.columns(3)
        intent = filters[0].selectbox("Intent", ["All"] + [item["slug"] for item in intents], key="analytics-intent")
        status = filters[1].selectbox("Status", ["All", "success", "no_match", "failed"], key="analytics-status")
        channel = filters[2].selectbox("Channel", ["All", "admin", "telegram", "teams"], key="analytics-channel")
        query_params = {**params, "limit": 100}
        if intent != "All":
            query_params["intent_slug"] = intent
        if status != "All":
            query_params["status"] = status
        if channel != "All":
            query_params["channel"] = channel
        history = _call(_client().get, "/admin/queries", params=query_params)
        if not history or not history["items"]:
            st.info("No query history matches these filters.", icon=":material/history:")
            return
        rows = [
            {
                "ID": item["id"],
                "Time": item["created_at"],
                "Channel": item["channel"].title(),
                "Question": item["question"],
                "Intent": item["intent_slug"],
                "Confidence": f"{(item['confidence'] or 0):.0%}",
                "Status": item["status"].replace("_", " ").title(),
                "Latency": f"{item['latency_ms']} ms" if item["latency_ms"] is not None else "",
            }
            for item in history["items"]
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
        selected = st.selectbox(
            "Query detail", [item["id"] for item in history["items"]]
        )
        detail = _call(_client().get, f"/admin/queries/{selected}")
        if detail:
            st.markdown(f"**{escape(detail['question'])}**")
            if detail.get("answer"):
                st.write(detail["answer"])
            if detail.get("error"):
                st.error(detail["error"], icon=":material/error:")
            st.caption(f"{detail['intent_slug']} · {detail['latency_ms']} ms · {detail['status']}")
            for citation in detail.get("citations", []):
                st.markdown(
                    f"<div class='ik-source'><strong>{escape(citation.get('document_title', 'Source'))}</strong>"
                    f"<br><span class='ik-muted'>{escape(citation.get('source_ref') or '')}</span></div>",
                    unsafe_allow_html=True,
                )


if _sign_in():
    selected_view = _sidebar()
    {
        "Dashboard": dashboard,
        "Frontend Integration": frontend_integration,
        "Knowledge Base": knowledge_base,
        "Intent Configuration": intent_configuration,
        "Analytics": analytics,
    }[selected_view]()
