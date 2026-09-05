from __future__ import annotations
from datetime import datetime
import time
import pandas as pd
import requests
import streamlit as st
from cosmic_search import DEFAULT_PSC_LABELS, NOTICE_TYPES, PSC_CHOICES, SearchConfig, search_sam

st.set_page_config(page_title="COSMIC Opportunity Finder", page_icon="🛰️", layout="wide")
st.title("COSMIC Opportunity Finder")
st.caption("SAM.gov Space + ISAM Opportunity Search — v2")
st.info("v2 uses PSCs as the primary retrieval signal and **space** as a secondary sniffer. "
        "Agency and NAICS are no longer search gates. Title + description are scored locally, "
        "and response deadlines must fall inside the selected future window.")

api_key = st.secrets.get("SAM_API_KEY","")
slack_url = st.secrets.get("SLACK_WEBHOOK_URL","")
if "results" not in st.session_state: st.session_state.results = pd.DataFrame()
if "status" not in st.session_state: st.session_state.status = []

with st.sidebar:
    st.header("Search Controls")
    days_back = st.slider("Published lookback (days)",30,730,365,30)
    response_days = st.slider("Response deadline horizon (days)",30,365,365,15)
    limit = st.slider("Results per API page",25,200,100,25)
    pages = st.slider("Max pages per pass",1,5,2)
    stale = st.checkbox("Suppress stale / omnibus notices",True)
    top_n = st.slider("Top N to Slack",1,10,3)

st.subheader("1. Product and Service Codes")
pscs = st.multiselect("Selected PSCs", list(PSC_CHOICES), default=DEFAULT_PSC_LABELS)

st.subheader("2. Notice Types")
notices = st.multiselect("Selected notice types", list(NOTICE_TYPES), default=list(NOTICE_TYPES))

st.subheader("3. Retrieval Logic")
st.write("**Primary:** selected PSCs. **Secondary:** keyword `space`. "
         "Results are deduplicated by Notice ID, checked against the response-date window, "
         "then ranked from title + description + PSC + ISAM/space signals.")

if st.button("Run Search", type="primary", use_container_width=True):
    cfg = SearchConfig(days_back=days_back, response_days_forward=response_days,
                       limit_per_query=limit, max_pages=pages,
                       suppress_stale_omnibus=stale)
    try:
        with st.spinner("Searching SAM.gov..."):
            st.session_state.results, st.session_state.status = search_sam(api_key, pscs, notices, cfg)
    except Exception as exc:
        st.error(str(exc))

if st.session_state.status:
    with st.expander("API request status"):
        for line in st.session_state.status: st.text(line)

results = st.session_state.results
if results.empty:
    st.info("Run a search to see current actionable opportunities.")
else:
    st.success(f"{len(results)} actionable opportunities found")
    cols = [c for c in [
        "cosmic_score","cosmic_priority","title","responseDeadLine","postedDate",
        "classificationCode","naicsCode","fullParentPathName","psc_match","space_sniff",
        "title_hits","description_hits","cosmic_reason","sam_link"
    ] if c in results.columns]
    st.dataframe(results[cols], use_container_width=True, hide_index=True,
        column_config={"sam_link":st.column_config.LinkColumn("SAM.gov",display_text="Open opportunity"),
                       "cosmic_score":st.column_config.NumberColumn("COSMIC Score")})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    left,right = st.columns(2)
    with left:
        st.download_button("Download CSV", results.to_csv(index=False).encode("utf-8"),
                           file_name=f"COSMIC_SAM_v2_{timestamp}.csv", mime="text/csv",
                           use_container_width=True)
    with right:
        if st.button(f"Post Top {min(top_n,len(results))} to Slack", use_container_width=True):
            if not slack_url:
                st.error("SLACK_WEBHOOK_URL is not configured.")
            else:
                posted=0
                for _,row in results.head(top_n).iterrows():
                    payload={"title":row.get("title",""),"agency":row.get("fullParentPathName",""),
                             "deadline":row.get("responseDeadLine",""),
                             "score":str(row.get("cosmic_score","")),
                             "link":str(row.get("sam_link","") or "")}
                    try:
                        r=requests.post(slack_url,json=payload,timeout=30)
                        if r.status_code==200: posted+=1
                        else: st.warning(f"Slack returned HTTP {r.status_code}")
                    except requests.RequestException as exc:
                        st.warning(f"Slack post failed: {exc}")
                    time.sleep(1.05)
                st.success(f"Posted {posted}/{min(top_n,len(results))} opportunities.")
