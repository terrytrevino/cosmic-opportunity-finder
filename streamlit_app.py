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

# ------------------------------------------------------------
# Defaults
# ------------------------------------------------------------

DEFAULT_AGENCIES = [
    "NASA",
    "DEPT OF THE AIR FORCE",
    "SPACE DEVELOPMENT AGENCY",
    "DARPA",
]

DEFAULT_NAICS = [
    "336414  Space vehicle / missile & space manufacturing",
    "541330  Engineering services",
    "541715  R&D engineering/physical sciences",
]

DEFAULT_TOPICS = []
DEFAULT_FOCUS = []

# ------------------------------------------------------------
# Secrets
# ------------------------------------------------------------

sam_api_key = st.secrets.get("SAM_API_KEY", "")
slack_webhook_url = st.secrets.get("SLACK_WEBHOOK_URL", "")

# ------------------------------------------------------------
# Session state initialization
# ------------------------------------------------------------

defaults = {
    "results": pd.DataFrame(),
    "status_lines": [],
    "days_back": 60,
    "limit_per_query": 100,
    "only_open": True,
    "strict_eng": False,
    "domain_weight": 2,
    "eng_weight": 1,
    "isam_boost": 5,
    "top_n": 5,
    "agencies": DEFAULT_AGENCIES.copy(),
    "naics_labels": DEFAULT_NAICS.copy(),
    "bundles": DEFAULT_TOPICS.copy(),
    "focus_terms": DEFAULT_FOCUS.copy(),
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


def reset_filters():
    st.session_state.days_back = 60
    st.session_state.limit_per_query = 100
    st.session_state.only_open = True
    st.session_state.strict_eng = False
    st.session_state.domain_weight = 2
    st.session_state.eng_weight = 1
    st.session_state.isam_boost = 5
    st.session_state.top_n = 5
    st.session_state.agencies = DEFAULT_AGENCIES.copy()
    st.session_state.naics_labels = DEFAULT_NAICS.copy()
    st.session_state.bundles = DEFAULT_TOPICS.copy()
    st.session_state.focus_terms = DEFAULT_FOCUS.copy()
    st.session_state.results = pd.DataFrame()
    st.session_state.status_lines = []


# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

st.title("COSMIC Opportunity Finder")
st.caption("SAM.gov Space + ISAM Opportunity Search")

st.info(
    "How to use: choose one or more COSMIC Technical Topics or Focus Terms, "
    "adjust agencies/NAICS as needed, then click **Run Search**. "
    "Review the results, open individual SAM.gov notices, download the full CSV, "
    "or post the top-ranked items to Slack."
)

st.caption(
    "Slack publishing is currently connected to the Space 4 All test workflow. "
    "The production COSMIC Opportunity Marketplace channel can be substituted later "
    "without changing the search engine."
)

if not sam_api_key:
    st.warning(
        "SAM_API_KEY is not configured. Add it in Streamlit Secrets before running searches."
    )

# ------------------------------------------------------------
# Sidebar controls
# ------------------------------------------------------------

with st.sidebar:
    st.header("Search Controls")

    st.slider(
        "Days back",
        7,
        365,
        step=7,
        key="days_back",
    )

    st.slider(
        "Limit per query",
        10,
        200,
        step=10,
        key="limit_per_query",
    )

    st.subheader("Search behavior")

    st.checkbox(
        "Only likely-open",
        key="only_open",
    )

    st.checkbox(
        "Require engineering term in title",
        key="strict_eng",
    )

    st.subheader("Scoring")

    st.slider(
        "Topic weight",
        0,
        5,
        key="domain_weight",
    )

    st.slider(
        "Engineering weight",
        0,
        5,
        key="eng_weight",
    )

    st.slider(
        "ISAM boost",
        0,
        10,
        key="isam_boost",
    )

    st.slider(
        "Top N to post to Slack",
        1,
        30,
        key="top_n",
    )

    st.button(
        "Reset filters",
        on_click=reset_filters,
        use_container_width=True,
    )

# ------------------------------------------------------------
# Main filters
# ------------------------------------------------------------

st.subheader("1. Agencies")
st.multiselect(
    "Select one or more agencies",
    options=AGENCY_CHOICES,
    key="agencies",
)

st.subheader("2. NAICS Industries")
st.multiselect(
    "Select one or more NAICS categories",
    options=list(NAICS_CHOICES.keys()),
    key="naics_labels",
)

st.subheader("3. COSMIC Technical Topics")
st.multiselect(
    "Choose technical topics",
    options=list(KEYWORD_BUNDLES.keys()),
    key="bundles",
)

st.subheader("4. Optional Focus Terms")
st.multiselect(
    "Add narrower focus phrases if desired",
    options=list(KEYWORD_LIBRARY.keys()),
    key="focus_terms",
)

# ------------------------------------------------------------
# Search summary
# ------------------------------------------------------------

with st.expander("Current search setup", expanded=False):
    st.write("**Agencies:**", st.session_state.agencies or "None selected")
    st.write("**NAICS:**", st.session_state.naics_labels or "None selected")
    st.write("**Technical Topics:**", st.session_state.bundles or "None selected")
    st.write("**Focus Terms:**", st.session_state.focus_terms or "None selected")
    st.write(
        f"**Window:** {st.session_state.days_back} days | "
        f"**Only open:** {st.session_state.only_open} | "
        f"**Strict engineering:** {st.session_state.strict_eng}"
    )

# ------------------------------------------------------------
# Search button
# ------------------------------------------------------------

run_search_btn = st.button(
    "Run Search",
    type="primary",
    use_container_width=True,
)

if run_search_btn:
    config = SearchConfig(
        days_back=st.session_state.days_back,
        limit_per_query=st.session_state.limit_per_query,
        only_open=st.session_state.only_open,
        strict_eng=st.session_state.strict_eng,
        domain_weight=st.session_state.domain_weight,
        eng_weight=st.session_state.eng_weight,
        isam_boost=st.session_state.isam_boost,
    )

    try:
        with st.spinner("Searching SAM.gov..."):
            results, status_lines = search_sam(
                api_key=sam_api_key,
                agencies=st.session_state.agencies,
                naics_labels=st.session_state.naics_labels,
                selected_bundles=st.session_state.bundles,
                selected_keywords=st.session_state.focus_terms,
                config=config,
            )

        st.session_state.results = results
        st.session_state.status_lines = status_lines

    except Exception as exc:
        st.error(str(exc))

# ------------------------------------------------------------
# API request status
# ------------------------------------------------------------

if st.session_state.status_lines:
    with st.expander("API request status"):
        for line in st.session_state.status_lines:
            st.text(line)

# ------------------------------------------------------------
# Results
# ------------------------------------------------------------

results = st.session_state.results

if not results.empty:
    st.success(f"{len(results)} filtered opportunities found")

    # Add a plain-language status field for users
    results_view = results.copy()

    if "deadline_passed" in results_view.columns:
        results_view["status"] = results_view["deadline_passed"].map(
            {True: "Deadline passed", False: "Open / likely open"}
        )
    else:
        results_view["status"] = "Open / status unknown"

    if "responseDeadLine" in results_view.columns:
        missing_deadline = (
            results_view["responseDeadLine"].isna()
            | (results_view["responseDeadLine"].astype(str).str.strip() == "")
        )
        results_view.loc[missing_deadline, "status"] = "No deadline listed"

    view_cols = [
        c for c in [
            "cosmic_score",
            "cosmic_priority",
            "search_score",
            "status",
            "title",
            "postedDate",
            "responseDeadLine",
            "naicsCode",
            "classificationCode",
            "fullParentPathName",
            "domain_hits",
            "isam_hits",
            "eng_hits",
            "sam_link",
        ]
        if c in results_view.columns
    ]

    st.caption(
        "Ranking uses two layers: **Search Match** measures how strongly the notice "
        "matches the selected search terms; **COSMIC Score** ranks strategic relevance "
        "to ISAM capabilities, the space ecosystem, enabling technologies, actionability, "
        "and potential member value."
    )

    st.dataframe(
        results_view[view_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "sam_link": st.column_config.LinkColumn(
                "SAM.gov",
                display_text="Open opportunity",
            ),
            "cosmic_score": st.column_config.NumberColumn(
                "COSMIC Score",
                help="Strategic COSMIC relevance on a 0–100 scale."
            ),
            "cosmic_priority": st.column_config.TextColumn("COSMIC Priority"),
            "search_score": st.column_config.NumberColumn(
                "Search Match",
                help="Original keyword/topic match score."
            ),
            "status": st.column_config.TextColumn("Status"),
            "domain_hits": st.column_config.NumberColumn("Topic hits"),
            "isam_hits": st.column_config.NumberColumn("ISAM hits"),
            "eng_hits": st.column_config.NumberColumn("Eng hits"),
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
            f"Post Top {min(st.session_state.top_n, len(results))} to Slack",
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
                    for _, row in results.head(st.session_state.top_n).iterrows():
                        payload = {
                            "title": row.get("title", ""),
                            "agency": row.get("fullParentPathName", ""),
                            "deadline": row.get("responseDeadLine", ""),
                            "score": str(row.get("cosmic_score", row.get("score", ""))),
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

                st.success(
                    f"Posted {posted}/"
                    f"{min(st.session_state.top_n, len(results))} opportunities."
                )

else:
    st.info(
        "Choose at least one Technical Topic or Focus Term, then run a search."
    )
