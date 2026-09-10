from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
import re

import pandas as pd
import requests

API_BASE = "https://api.sam.gov/opportunities/v2/search"

PSC_CHOICES = {
    "AR11  R&D - Space: Basic Research": "AR11",
    "AR12  R&D - Space: Applied Research": "AR12",
    "AR13  R&D - Space: Advanced Development": "AR13",
    "AC11  R&D - Defense Aircraft: Basic Research": "AC11",
    "AC12  R&D - Defense Aircraft: Applied Research": "AC12",
    "AC13  R&D - Defense Aircraft: Advanced Development": "AC13",
    "AC31  R&D - Defense Ships: Basic Research": "AC31",
    "AC32  R&D - Defense Ships: Applied Research": "AC32",
    "AC33  R&D - Defense Ships: Advanced Development": "AC33",
    "1555  Space Vehicles": "1555",
    "1675  Space Vehicle Components": "1675",
    "1677  Space Vehicle Remote Control Systems": "1677",
    "1735  Space Vehicle Maintenance / Servicing Equipment": "1735",
}
DEFAULT_PSC_LABELS = list(PSC_CHOICES.keys())

NOTICE_TYPES = {
    "Solicitation": "o",
    "Presolicitation": "p",
    "Combined Synopsis/Solicitation": "k",
}

SEARCH_TERMS = [
    "space", "orbit", "in-space", "on-orbit", "in-space manufacturing",
    "in-space assembly", "orbit assembly", "orbit manufacturing", "in-space servicing",
    "orbit servicing", "low-earth orbit", "geosynchronous orbit", "in-space mobility",
    "space platform", "in-situ resource utilization", "orbital platform", "space refueling",
    "orbit refueling", "orbital", "isam", "microgravity", "lunar", "spacecraft", "isru",
    "cubesat", "deorbit", "propellant", "power", "sbir",
]
DIRECT_TERMS = [
    "isam", "servicing", "refuel", "refueling", "propellant transfer", "rendezvous",
    "proximity operations", "docking", "berthing", "capture", "grapple", "robotic",
    "assembly", "manufacturing", "repair", "maintenance", "life extension", "inspection",
]
ENABLER_TERMS = [
    "autonomy", "navigation", "guidance", "control", "power", "propulsion",
    "communications", "rf", "antenna", "tracking", "robotics", "simulation",
    "modeling", "interface", "standard",
]
NON_ACTIONABLE_TERMS = [
    "award notice", "justification", "sole source", "cancellation", "cancelled", "contract extension",
]
CORE_SPACE_TERMS = [
    "space", "orbit", "orbital", "spacecraft", "satellite", "lunar", "isam",
    "microgravity", "cubesat", "deorbit", "on-orbit", "in-space",
]
OFF_DOMAIN_MARITIME_TERMS = [
    "maritime", "shipbuilding", "shipyard", "naval manufacturing", "surface vessel",
    "submarine", "industrial base capability",
]


@dataclass
class SearchConfig:
    days_back: int = 365
    response_days_forward: int = 365
    limit_per_query: int = 100
    max_pages: int = 2
    suppress_stale_omnibus: bool = True
    stale_published_days: int = 365
    stale_modified_days: int = 180
    description_fetch_limit: int = 5


def _hits(text, terms):
    text = str(text or "").lower()
    return sum(1 for term in terms if term.lower() in text)


def _phrase_hits(text, terms):
    text = str(text or "").lower()
    return sum(1 for term in terms if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text))


def _normalize_keywords(keywords):
    if not keywords:
        return []
    if isinstance(keywords, str):
        keywords = re.split(r"[,;\n]+", keywords)
    return [str(term).strip().lower() for term in keywords if str(term).strip()]


def _clean_text(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _format_contacts(value):
    if not value:
        return ""
    contacts = value if isinstance(value, list) else [value]
    rendered = []
    for contact in contacts:
        if not isinstance(contact, dict):
            rendered.append(_clean_text(contact))
            continue
        parts = []
        for key in ("type", "fullName", "title", "email", "phone", "fax"):
            item = _clean_text(contact.get(key, ""))
            if item and item not in parts:
                parts.append(item)
        if parts:
            rendered.append(" | ".join(parts))
    return "; ".join(rendered)


def _to_utc(series):
    return pd.to_datetime(series, errors="coerce", utc=True)


def _response_deadline_series(df):
    for column in ("responseDeadLine", "reponseDeadLine"):
        if column in df.columns:
            return _to_utc(df[column])
    return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")


def _notice_code(value):
    raw = str(value or "").strip().lower()
    if raw in {"o", "solicitation"}:
        return "o"
    if raw in {"p", "presolicitation"}:
        return "p"
    if raw in {"k", "combined synopsis/solicitation", "combined synopsis solicitation"}:
        return "k"
    return raw


def _row_text(row):
    fields = [
        row.get("title", ""), row.get("description_text", ""), row.get("type", ""),
        row.get("fullParentPathName", ""), row.get("classificationCode", ""), row.get("naicsCode", ""),
    ]
    return " ".join(str(v or "") for v in fields).lower()


def _fetch_description(session, api_key, description_url):
    if not isinstance(description_url, str) or not description_url.startswith("http"):
        return ""
    sep = "&" if "?" in description_url else "?"
    try:
        r = session.get(f"{description_url}{sep}api_key={api_key}", timeout=(10, 45))
        if r.status_code == 429:
            return "__THROTTLED__"
        if r.status_code != 200:
            return ""
        return _clean_text(r.text[:50000])
    except requests.RequestException:
        return ""


def _broad_collect(session, api_key, posted_from, posted_to, deadline_from, deadline_to, config):
    records, status = [], []
    for page in range(config.max_pages):
        params = {
            "api_key": api_key,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "rdlfrom": deadline_from,
            "rdlto": deadline_to,
            "limit": config.limit_per_query,
            "offset": page * config.limit_per_query,
        }
        try:
            r = session.get(API_BASE, params=params, timeout=(10, 120))
            status.append(f"BROAD RETRIEVAL | page {page + 1} | HTTP {r.status_code}")
            if r.status_code == 429:
                status.append(f"THROTTLED | {r.text[:500]}")
                status.append("Search stopped immediately after SAM.gov reported quota exhaustion.")
                break
            if r.status_code != 200:
                status.append(f"SAM response: {r.text[:500]}")
                break
            batch = r.json().get("opportunitiesData", [])
            records.extend(batch)
            if len(batch) < config.limit_per_query:
                break
        except requests.RequestException as exc:
            status.append(f"BROAD RETRIEVAL | request error: {exc}")
            break
    return records, status


def _preliminary_score(row, custom_keywords):
    title = str(row.get("title", "") or "").lower()
    text = " ".join([
        title,
        str(row.get("classificationCode", "") or "").lower(),
        str(row.get("fullParentPathName", "") or "").lower(),
    ])
    return (
        (25 if bool(row.get("psc_match", False)) else 0)
        + min(30, _hits(text, DIRECT_TERMS) * 8)
        + min(20, _hits(text, SEARCH_TERMS) * 3)
        + min(24, _phrase_hits(text, custom_keywords) * 8)
    )


def _score(row, custom_keywords):
    text = _row_text(row)
    title = str(row.get("title", "") or "").lower()
    desc = str(row.get("description_text", "") or "").lower()
    direct = _hits(text, DIRECT_TERMS)
    ecosystem = _hits(text, SEARCH_TERMS)
    enablers = _hits(text, ENABLER_TERMS)
    title_hits = _hits(title, SEARCH_TERMS)
    description_hits = _hits(desc, SEARCH_TERMS)
    custom_hits = _phrase_hits(text, custom_keywords)
    psc_match = bool(row.get("psc_match", False))
    sniff = bool(row.get("space_sniff", False))
    score = min(100,
        (25 if psc_match else 0)
        + min(30, direct * 8)
        + min(15, ecosystem * 2)
        + min(10, enablers * 2)
        + min(10, title_hits * 3)
        + min(5, description_hits)
        + min(24, custom_hits * 8)
        + (2 if sniff else 0)
        + (0 if _hits(text, NON_ACTIONABLE_TERMS) else 3)
    )
    priority = "Very High" if score >= 70 else "High" if score >= 50 else "Medium" if score >= 30 else "Low"
    reasons = []
    if psc_match: reasons.append("PSC match")
    if direct: reasons.append(f"Direct/ISAM ({direct})")
    if ecosystem: reasons.append(f"Space terms ({ecosystem})")
    if enablers: reasons.append(f"Enablers ({enablers})")
    if title_hits: reasons.append(f"Title ({title_hits})")
    if description_hits: reasons.append(f"Description ({description_hits})")
    if custom_hits: reasons.append(f"User keywords ({custom_hits})")
    if sniff: reasons.append("Space sniffer")
    return pd.Series({
        "cosmic_score": score,
        "cosmic_priority": priority,
        "title_hits": title_hits,
        "description_hits": description_hits,
        "custom_keyword_hits": custom_hits,
        "cosmic_reason": "; ".join(reasons) or "Weak signal",
    })


def search_sam(api_key, psc_labels, notice_labels, config, custom_keywords=None, keyword_mode="rank"):
    if not api_key or not api_key.startswith("SAM-"):
        raise ValueError("Missing or invalid SAM_API_KEY.")
    if not psc_labels:
        raise ValueError("Select at least one PSC.")
    if not notice_labels:
        raise ValueError("Select at least one notice type.")
    if keyword_mode not in {"rank", "strict"}:
        raise ValueError("keyword_mode must be 'rank' or 'strict'.")

    selected_pscs = {PSC_CHOICES[label] for label in psc_labels}
    selected_notices = {NOTICE_TYPES[label] for label in notice_labels}
    custom_keywords = _normalize_keywords(custom_keywords)

    now = datetime.now(timezone.utc)
    lookback = min(config.days_back, 364)
    forward = min(config.response_days_forward, 364)
    posted_from = (now - timedelta(days=lookback)).strftime("%m/%d/%Y")
    posted_to = now.strftime("%m/%d/%Y")
    deadline_from = now.strftime("%m/%d/%Y")
    deadline_to = (now + timedelta(days=forward)).strftime("%m/%d/%Y")

    with requests.Session() as session:
        records, status = _broad_collect(
            session, api_key, posted_from, posted_to, deadline_from, deadline_to, config
        )
        if not records:
            return pd.DataFrame(), status

        df = pd.DataFrame(records)
        raw_count = len(df)
        if "noticeId" not in df.columns:
            status.append("SAM response did not include noticeId.")
            return pd.DataFrame(), status

        df = df.drop_duplicates(subset=["noticeId"]).copy()
        dedup_count = len(df)

        if "classificationCode" not in df.columns:
            df["classificationCode"] = ""
        normalized_psc = df["classificationCode"].fillna("").astype(str).str.upper().str.strip().str[:4]
        df["psc_match"] = normalized_psc.isin(selected_pscs)

        if "type" not in df.columns:
            df["type"] = ""
        df["notice_code"] = df["type"].apply(_notice_code)
        df = df[df["notice_code"].isin(selected_notices)].copy()
        notice_count = len(df)

        df["responseDeadLine_dt"] = _response_deadline_series(df)
        now_ts = pd.Timestamp.now(tz="UTC")
        max_due = now_ts + pd.Timedelta(days=forward)
        df = df[
            df["responseDeadLine_dt"].notna()
            & (df["responseDeadLine_dt"] >= now_ts)
            & (df["responseDeadLine_dt"] <= max_due)
        ].copy()
        deadline_count = len(df)

        if "title" not in df.columns:
            df["title"] = ""
        df["space_sniff"] = df["title"].fillna("").astype(str).str.lower().str.contains("space", regex=False)
        df["description_text"] = ""
        df["local_keyword_hits"] = df.apply(lambda row: _hits(_row_text(row), SEARCH_TERMS), axis=1)
        df = df[df["psc_match"] | df["space_sniff"] | (df["local_keyword_hits"] > 0)].copy()
        relevance_count = len(df)

        if config.suppress_stale_omnibus and not df.empty:
            pub = _to_utc(df["postedDate"]) if "postedDate" in df.columns else pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
            modified_col = next((c for c in ("modifiedDate", "updatedDate") if c in df.columns), None)
            if modified_col:
                mod = _to_utc(df[modified_col])
                stale = (
                    pub.notna() & mod.notna()
                    & (pub < now_ts - pd.Timedelta(days=config.stale_published_days))
                    & (mod < now_ts - pd.Timedelta(days=config.stale_modified_days))
                )
