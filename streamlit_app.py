from __future__ import annotations

from datetime import datetime
import time

import pandas as pd
import requests
import streamlit as st

from cosmic_search import (
    AGENCY_CHOICES,
    KEYWORD_BUNDLES,
    KEYWORD_LIBRARY,
    NAICS_CHOICES,
    SearchConfig,
    search_sam,
)


st.set_page_config(
    page_title="COSMIC Opportunity Finder",
    page_icon="🛰️",
    layout="wide",
)

st.title("COSMIC Opportunity Finder")
st.caption("SAM.gov Space + ISAM Opportunity Search")

# ---------- Secrets ----------
sam_api_key = st.secrets.get("SAM_API_KEY", "")
slack_webhook_url = st.secrets.get("SLACK_WEBHOOK_URL", "")

if not sam_api_key:
    st.warning(
        "SAM_API_KEY is not configured. Add it in Streamlit Secrets before running searches."
    )

# ---------- Session state ----------
if "results" not in st.session_state:
    st.session_state.results = pd.DataFrame()

if "status_lines" not in st.session_state:
    st.session_state.status_lines = []


# ---------- Sidebar controls ----------
with st.sidebar:
    st.header("Search Controls")

    days_back = st.slider("Days back", 7, 365, 60, step=7)
    limit_per_query = st.slider("Limit per query", 10, 200, 100, step=10)

    st.subheader("Search behavior")
    only_open = st.checkbox("Only likely-open", value=True)
    strict_eng = st.checkbox("Require engineering term in title", value=False)

    st.subheader("Scoring")
    domain_weight = st.slider("Topic weight", 0, 5, 2)
    eng_weight = st.slider("Engineering weight", 0, 5, 1)
    isam_boost = st.slider("ISAM boost", 0, 10, 5)

    top_n = st.slider("Top N to post to Slack", 1, 30, 5)


st.subheader("1. Agencies")
agencies = st.multiselect(
    "Select one or more agencies",
    options=AGENCY_CHOICES,
    default=[
        "NASA",
        "DEPT OF THE AIR FORCE",
        "SPACE DEVELOPMENT AGENCY",
        "DARPA",
    ],
)

st.subheader("2. NAICS Industries")
naics_labels = st.multiselect(
    "Select one or more NAICS categories",
    options=list(NAICS_CHOICES.keys()),
    default=[
        "336414  Space vehicle / missile & space manufacturing",
        "541330  Engineering services",
        "541715  R&D engineering/physical sciences",
    ],
)

st.subheader("3. COSMIC Technical Topics")
bundles = st.multiselect(
    "Choose technical topics",
    options=list(KEYWORD_BUNDLES.keys()),
    default=[],
)

st.subheader("4. Optional Focus Terms")
focus_terms = st.multiselect(
    "Add narrower focus phrases if desired",
    options=list(KEYWORD_LIBRARY.keys()),
    default=[],
)

button_col1, button_col2, button_col3 = st.columns([1, 1, 2])

with button_col1:
    run_search_btn = st.button("Run Search", type="primary", use_container_width=True)

with button_col2:
    reset_btn = st.button("Reset Results", use_container_width=True)

if reset_btn:
    st.session_state.results = pd.DataFrame()
    st.session_state.status_lines = []
    st.rerun()

if run_search_btn:
    config = SearchConfig(
        days_back=days_back,
        limit_per_query=limit_per_query,
        only_open=only_open,
        strict_eng=strict_eng,
        domain_weight=domain_weight,
        eng_weight=eng_weight,
        isam_boost=isam_boost,
    )

    try:
        with st.spinner("Searching SAM.gov..."):
            results, status_lines = search_sam(
                api_key=sam_api_key,
                agencies=agencies,
                naics_labels=naics_labels,
                selected_bundles=bundles,
                selected_keywords=focus_terms,
                config=config,
            )

        st.session_state.results = results
        st.session_state.status_lines = status_lines

    except Exception as exc:
        st.error(str(exc))


# ---------- Status ----------
if st.session_state.status_lines:
    with st.expander("API request status"):
        for line in st.session_state.status_lines:
            st.text(line)


# ---------- Results ----------
results = st.session_state.results

if not results.empty:
    st.success(f"{len(results)} filtered opportunities found")

    view_cols = [
        c for c in [
            "score",
            "domain_hits",
            "isam_hits",
            "eng_hits",
            "title",
            "postedDate",
            "responseDeadLine",
            "naicsCode",
            "classificationCode",
            "fullParentPathName",
            "sam_link",
        ]
        if c in results.columns
    ]

    display_df = results[view_cols].copy()

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "sam_link": st.column_config.LinkColumn(
                "SAM.gov",
                display_text="Open opportunity",
            ),
        },
    )

    st.divider()

    export_col, slack_col = st.columns(2)

    with export_col:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        csv_data = results.to_csv(index=False).encode("utf-8")

        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name=f"COSMIC_SAM_Opportunities_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with slack_col:
        if st.button(
            f"Post Top {min(top_n, len(results))} to Slack",
            use_container_width=True,
        ):
            if not slack_webhook_url:
                st.error("SLACK_WEBHOOK_URL is not configured in Streamlit Secrets.")
            elif not slack_webhook_url.startswith(
                "https://hooks.slack.com/triggers/"
            ):
                st.error("SLACK_WEBHOOK_URL is not a Slack Workflow trigger URL.")
            else:
                posted = 0

                with st.spinner("Posting to Slack..."):
                    for _, row in results.head(top_n).iterrows():
                        payload = {
                            "title": row.get("title", ""),
                            "agency": row.get("fullParentPathName", ""),
                            "deadline": row.get("responseDeadLine", ""),
                            "score": str(row.get("score", "")),
                            "link": (row.get("sam_link", "") or "").strip(),
                        }

                        try:
                            response = requests.post(
                                slack_webhook_url,
                                json=payload,
                                timeout=30,
                            )

                            if response.status_code == 200:
                                posted += 1
                            else:
                                st.warning(
                                    f"Slack returned HTTP {response.status_code}"
                                )
                        except requests.RequestException as exc:
                            st.warning(f"Slack post failed: {exc}")

                        time.sleep(1.05)

                st.success(f"Posted {posted}/{min(top_n, len(results))} opportunities.")

else:
    st.info(
        "Choose at least one Technical Topic or Focus Term, then run a search."
    )
