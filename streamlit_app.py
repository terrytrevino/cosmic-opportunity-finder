from __future__ import annotations

from datetime import datetime
import time
import pandas as pd
import requests
import streamlit as st

import base64
from pathlib import Path


def get_base64_image(path):
    image_path = Path(path)

    if not image_path.exists():
        return ""

    return base64.b64encode(
        image_path.read_bytes()
    ).decode()


background_b64 = get_base64_image(
    "assets/cosmic_background.png"
)


st.markdown(
    f"""
    <style>

    /* Main app background */
    .stApp {{
        background:
            linear-gradient(
                rgba(3, 12, 26, 0.78),
                rgba(3, 12, 26, 0.90)
            ),
            url("data:image/png;base64,{background_b64}");

        background-size: cover;
        background-position: center top;
        background-attachment: fixed;
    }}

    /* Main content width */
    .block-container {{
        max-width: 1500px;
        padding-top: 2rem;
        padding-bottom: 4rem;
    }}

    /* Sidebar */
    section[data-testid="stSidebar"] {{
        background-color: rgba(7, 20, 38, 0.92);
        border-right: 1px solid rgba(90, 160, 255, 0.25);
    }}

    /* Text */
    h1, h2, h3, p, label {{
        color: #f4f7fb;
    }}

    /* Inputs */
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div {{
        background-color: rgba(12, 29, 50, 0.88);
    }}

    /* Dataframe area */
    div[data-testid="stDataFrame"] {{
        background: rgba(7, 20, 38, 0.88);
        border-radius: 12px;
    }}

    /* Info / success / warning boxes */
    div[data-testid="stAlert"] {{
        background-color: rgba(10, 28, 49, 0.88);
        border-radius: 12px;
    }}

    /* Primary buttons */
    div.stButton > button[kind="primary"] {{
        border-radius: 8px;
        font-weight: 700;
    }}

    </style>
    """,
    unsafe_allow_html=True,
)

from cosmic_search import (
    AGENCY_CHOICES,
    KEYWORD_BUNDLES,
    KEYWORD_LIBRARY,
    NAICS_CHOICES,
    NAICS_GROUPS,
    SearchConfig as LegacySearchConfig,
    search_sam as search_sam_legacy,
)

from cosmic_search_v2 import (
    DEFAULT_PSC_LABELS,
    NOTICE_TYPES,
    PSC_CHOICES,
    SearchConfig as V2SearchConfig,
    search_sam as search_sam_v2,
)

st.set_page_config(
    page_title="COSMIC Opportunity Finder",
    page_icon="🛰️",
    layout="wide",
)

st.markdown(
    """
    <div style="
        padding: 28px 32px;
        margin-bottom: 18px;
        border-radius: 18px;
        background: rgba(6, 20, 38, 0.78);
        border: 1px solid rgba(93, 161, 255, 0.28);
        backdrop-filter: blur(8px);
    ">
        <div style="
            font-size: 48px;
            font-weight: 800;
            letter-spacing: 4px;
            color: white;
        ">
            COSMIC
        </div>

        <div style="
            font-size: 23px;
            letter-spacing: 5px;
            color: #dceaff;
            margin-top: -4px;
        ">
            OPPORTUNITY FINDER
        </div>

        <div style="
            font-size: 14px;
            letter-spacing: 3px;
            color: #8fc7ff;
            margin-top: 12px;
        ">
            SPACE • ISAM • INNOVATION • IMPACT
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

link_col1, link_col2, _ = st.columns(
    [1, 1, 3]
)

with link_col1:
    st.link_button(
        "Join COSMIC",
        "https://cosmicspace.org/membership/",
        use_container_width=True,
    )

with link_col2:
    st.link_button(
        "COSMIC Website",
        "https://cosmicspace.org/",
        use_container_width=True,
    )

search_mode = st.radio(
    "Search mode",
    ["PSC + Deadline Search v2", "Legacy Agency + NAICS Search v1"],
    index=0,
    horizontal=True,
)

sam_api_key = st.secrets.get("SAM_API_KEY", "")
slack_webhook_url = st.secrets.get("SLACK_WEBHOOK_URL", "")

if not sam_api_key:
    st.warning("SAM_API_KEY is not configured in Streamlit Secrets.")


def post_rows_to_slack(results: pd.DataFrame, top_n: int, score_col: str):
    if results is None or results.empty:
        st.warning("No results available to post.")
        return
    if not slack_webhook_url:
        st.error("SLACK_WEBHOOK_URL is not configured.")
        return

    posted = 0
    with st.spinner("Posting to Slack..."):
        for _, row in results.head(top_n).iterrows():
            payload = {
                "title": row.get("title", ""),
                "agency": row.get("fullParentPathName", ""),
                "deadline": row.get("responseDeadLine", ""),
                "score": str(row.get(score_col, row.get("score", ""))),
                "link": str(row.get("sam_link", "") or "").strip(),
            }
            try:
                r = requests.post(slack_webhook_url, json=payload, timeout=30)
                if r.status_code == 200:
                    posted += 1
                else:
                    st.warning(f"Slack returned HTTP {r.status_code}: {r.text[:160]}")
            except requests.RequestException as exc:
                st.warning(f"Slack post failed: {exc}")
            time.sleep(1.05)

    st.success(f"Posted {posted}/{min(top_n, len(results))} opportunities.")


def render_v2():
    st.info(
        "Recommended search. v2 uses **PSC codes** as the primary retrieval signal "
        "and **space** as a secondary sniffer. Agency and NAICS remain visible as metadata "
        "but are no longer search gates."
    )

    if "v2_results" not in st.session_state:
        st.session_state.v2_results = pd.DataFrame()
    if "v2_status" not in st.session_state:
        st.session_state.v2_status = []

    with st.sidebar:
        st.header("v2 Search Controls")
        days_back = st.slider("Published lookback (days)", 30, 730, 365, 30, key="v2_days")
        response_days = st.slider("Response deadline horizon (days)", 30, 365, 365, 15, key="v2_due")
        limit_per_query = st.slider("Results per API page", 25, 200, 100, 25, key="v2_limit")
        max_pages = st.slider("Max pages per search pass", 1, 5, 2, key="v2_pages")
        suppress_stale = st.checkbox("Suppress stale / omnibus notices", True, key="v2_stale")
        top_n = st.slider("Top N to post to Slack", 1, 10, 3, key="v2_topn")

    st.subheader("1. Product and Service Codes")
    selected_pscs = st.multiselect(
        "Select PSCs",
        list(PSC_CHOICES.keys()),
        default=DEFAULT_PSC_LABELS,
        key="v2_pscs",
    )

    st.subheader("2. Notice Types")
    selected_notices = st.multiselect(
        "Select notice types",
        list(NOTICE_TYPES.keys()),
        default=list(NOTICE_TYPES.keys()),
        key="v2_notices",
    )

    st.subheader("3. Retrieval Logic")
    st.write(
        "**Primary:** PSC codes. **Secondary:** `space` keyword sniffer. "
        "Results are deduplicated by Notice ID, filtered by response date, "
        "then ranked using PSC, title, description, ISAM, space, and enabling signals."
    )

    if st.button("Run v2 Search", type="primary", use_container_width=True, key="run_v2"):
        cfg = V2SearchConfig(
            days_back=days_back,
            response_days_forward=response_days,
            limit_per_query=limit_per_query,
            max_pages=max_pages,
            suppress_stale_omnibus=suppress_stale,
        )
        try:
            with st.spinner("Searching SAM.gov with v2 logic..."):
                results, status = search_sam_v2(
                    api_key=sam_api_key,
                    psc_labels=selected_pscs,
                    notice_labels=selected_notices,
                    config=cfg,
                )
            st.session_state.v2_results = results
            st.session_state.v2_status = status
        except Exception as exc:
            st.error(str(exc))

    if st.session_state.v2_status:
        with st.expander("v2 API request status"):
            for line in st.session_state.v2_status:
                st.text(line)

    results = st.session_state.v2_results
    if results is None or results.empty:
        st.info("Run the v2 search to see current actionable opportunities.")
        return

    st.success(f"{len(results)} actionable opportunities found")

    cols = [c for c in [
        "cosmic_score", "cosmic_priority", "title", "responseDeadLine", "postedDate",
        "classificationCode", "naicsCode", "fullParentPathName", "psc_match", "space_sniff",
        "title_hits", "description_hits", "cosmic_reason", "sam_link"
    ] if c in results.columns]

    st.dataframe(
        results[cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "sam_link": st.column_config.LinkColumn("SAM.gov", display_text="Open opportunity"),
            "cosmic_score": st.column_config.NumberColumn("COSMIC Score"),
            "cosmic_priority": st.column_config.TextColumn("Priority"),
            "psc_match": st.column_config.CheckboxColumn("PSC"),
            "space_sniff": st.column_config.CheckboxColumn("Space Sniffer"),
        },
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    left, right = st.columns(2)

    with left:
        st.download_button(
            "Download v2 CSV",
            results.to_csv(index=False).encode("utf-8"),
            file_name=f"COSMIC_SAM_v2_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with right:
        if st.button(f"Post Top {min(top_n, len(results))} to Slack", use_container_width=True, key="post_v2"):
            post_rows_to_slack(results, top_n, "cosmic_score")


def render_legacy():
    st.warning(
        "Legacy mode is retained for comparison and rollback. "
        "It uses the original Agency + NAICS retrieval logic."
    )

    default_agencies = [
        "NASA",
        "DEPT OF THE AIR FORCE",
        "SPACE DEVELOPMENT AGENCY",
        "DARPA",
    ]
    default_naics = NAICS_GROUPS["Core COSMIC"].copy()

    if "legacy_results" not in st.session_state:
        st.session_state.legacy_results = pd.DataFrame()
    if "legacy_status" not in st.session_state:
        st.session_state.legacy_status = []
    if "legacy_agencies" not in st.session_state:
        st.session_state.legacy_agencies = default_agencies.copy()
    if "legacy_naics" not in st.session_state:
        st.session_state.legacy_naics = default_naics.copy()
    if "legacy_bundles" not in st.session_state:
        st.session_state.legacy_bundles = []
    if "legacy_focus" not in st.session_state:
        st.session_state.legacy_focus = []

    with st.sidebar:
        st.header("Legacy v1 Controls")
        days_back = st.slider("Days back", 7, 365, 60, 7, key="legacy_days")
        limit_per_query = st.slider("Limit per query", 10, 200, 100, 10, key="legacy_limit")
        only_open = st.checkbox("Only likely-open", True, key="legacy_open")
        strict_eng = st.checkbox("Require engineering term in title", False, key="legacy_strict")
        domain_weight = st.slider("Topic weight", 0, 5, 2, key="legacy_domain")
        eng_weight = st.slider("Engineering weight", 0, 5, 1, key="legacy_eng")
        isam_boost = st.slider("ISAM boost", 0, 10, 5, key="legacy_isam")
        top_n = st.slider("Top N to post to Slack", 1, 10, 3, key="legacy_topn")

    st.subheader("1. Agencies")
    st.multiselect("Select agencies", AGENCY_CHOICES, key="legacy_agencies")

    st.subheader("2. NAICS Industries")
    st.multiselect("Select NAICS categories", list(NAICS_CHOICES.keys()), key="legacy_naics")

    st.subheader("3. Technical Topics")
    st.multiselect("Select technical topics", list(KEYWORD_BUNDLES.keys()), key="legacy_bundles")

    st.subheader("4. Focus Terms")
    st.multiselect("Select focus terms", list(KEYWORD_LIBRARY.keys()), key="legacy_focus")

    if st.button("Run Legacy Search", type="primary", use_container_width=True, key="run_legacy"):
        cfg = LegacySearchConfig(
            days_back=days_back,
            limit_per_query=limit_per_query,
            only_open=only_open,
            strict_eng=strict_eng,
            domain_weight=domain_weight,
            eng_weight=eng_weight,
            isam_boost=isam_boost,
        )
        try:
            with st.spinner("Searching SAM.gov with legacy logic..."):
                results, status = search_sam_legacy(
                    api_key=sam_api_key,
                    agencies=st.session_state.legacy_agencies,
                    naics_labels=st.session_state.legacy_naics,
                    selected_bundles=st.session_state.legacy_bundles,
                    selected_keywords=st.session_state.legacy_focus,
                    config=cfg,
                )
            st.session_state.legacy_results = results
            st.session_state.legacy_status = status
        except Exception as exc:
            st.error(str(exc))

    if st.session_state.legacy_status:
        with st.expander("Legacy API request status"):
            for line in st.session_state.legacy_status:
                st.text(line)

    results = st.session_state.legacy_results
    if results is None or results.empty:
        st.info("Run the legacy search to see results.")
        return

    st.success(f"{len(results)} legacy opportunities found")

    cols = [c for c in [
        "cosmic_score", "cosmic_priority", "search_score", "title", "postedDate",
        "responseDeadLine", "naicsCode", "classificationCode", "fullParentPathName",
        "domain_hits", "isam_hits", "eng_hits", "sam_link"
    ] if c in results.columns]

    st.dataframe(
        results[cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "sam_link": st.column_config.LinkColumn("SAM.gov", display_text="Open opportunity"),
            "cosmic_score": st.column_config.NumberColumn("COSMIC Score"),
            "search_score": st.column_config.NumberColumn("Search Match"),
        },
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    left, right = st.columns(2)

    with left:
        st.download_button(
            "Download legacy CSV",
            results.to_csv(index=False).encode("utf-8"),
            file_name=f"COSMIC_SAM_legacy_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with right:
        if st.button(f"Post Top {min(top_n, len(results))} to Slack", use_container_width=True, key="post_legacy"):
            post_rows_to_slack(results, top_n, "cosmic_score")


if search_mode == "PSC + Deadline Search v2":
    render_v2()
else:
    render_legacy()
