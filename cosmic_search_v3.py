from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
import hashlib
import re
import threading
import time

import pandas as pd
import requests

API_BASE = "https://api.sam.gov/opportunities/v2/search"
CACHE_TTL_SECONDS = 1800
_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE: dict[str, tuple[float, pd.DataFrame, list[str]]] = {}

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

CORE_SPACE_TERMS = [
    "space", "orbit", "orbital", "spacecraft", "satellite", "lunar",
    "isam", "microgravity", "cubesat", "deorbit", "on-orbit", "in-space",
]

DIRECT_TERMS = [
    "isam", "servicing", "refuel", "refueling", "propellant transfer",
    "rendezvous", "proximity operations", "docking", "berthing", "capture",
    "grapple", "robotic", "assembly", "manufacturing", "repair",
    "maintenance", "life extension", "inspection",
]

ENABLER_TERMS = [
    "autonomy", "navigation", "guidance", "control", "power", "propulsion",
    "communications", "rf", "antenna", "tracking", "robotics",
    "simulation", "modeling", "interface", "standard",
]

NON_ACTIONABLE_TERMS = [
    "award notice", "justification", "sole source", "cancellation",
    "cancelled", "contract extension",
]

OFF_DOMAIN_TERMS = [
    "hotel", "conference center", "conference space", "lodging", "ballroom",
    "banquet", "meeting room", "event venue", "resort", "catering",
]


@dataclass
class SearchConfig:
    days_back: int = 364
    response_days_forward: int = 364
    limit_per_query: int = 1000
    max_pages: int = 1
    suppress_stale_omnibus: bool = True
    stale_published_days: int = 365
    stale_modified_days: int = 180
    description_fetch_limit: int = 5


def _clean(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_keywords(keywords):
    if not keywords:
        return []
    if isinstance(keywords, str):
        keywords = re.split(r"[,;\n]+", keywords)
    return [str(term).strip().lower() for term in keywords if str(term).strip()]


def _hits(text, terms):
    haystack = str(text or "").lower()
    return sum(1 for term in terms if term.lower() in haystack)


def _phrase_hits(text, terms):
    haystack = str(text or "").lower()
    return sum(
        1
        for term in terms
        if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", haystack)
    )


def _to_utc(series):
    return pd.to_datetime(series, errors="coerce", utc=True)


def _deadline_series(df):
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


def _format_contacts(value):
    if not value:
        return ""
    contacts = value if isinstance(value, list) else [value]
    rendered = []
    for contact in contacts:
        if not isinstance(contact, dict):
            rendered.append(_clean(contact))
            continue
        parts = []
        for key in ("type", "fullName", "title", "email", "phone", "fax"):
            item = _clean(contact.get(key, ""))
            if item and item not in parts:
                parts.append(item)
        if parts:
            rendered.append(" | ".join(parts))
    return "; ".join(rendered)


def _row_text(row):
    fields = [
        row.get("title", ""),
        row.get("description_text", ""),
        row.get("type", ""),
        row.get("fullParentPathName", ""),
        row.get("classificationCode", ""),
        row.get("naicsCode", ""),
    ]
    return " ".join(str(value or "") for value in fields).lower()


def _fetch_description(session, api_key, description_url):
    if not isinstance(description_url, str) or not description_url.startswith("http"):
        return ""
    separator = "&" if "?" in description_url else "?"
    try:
        response = session.get(
            f"{description_url}{separator}api_key={api_key}",
            timeout=(10, 45),
        )
        if response.status_code == 429:
            return "__THROTTLED__"
        if response.status_code != 200:
            return ""
        return _clean(response.text[:50000])
    except requests.RequestException:
        return ""


def _cache_key(api_key, psc_labels, notice_labels, config, custom_keywords, keyword_mode):
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    payload = "|".join([
        key_hash,
        ",".join(sorted(psc_labels)),
        ",".join(sorted(notice_labels)),
        str(min(config.days_back, 364)),
        str(min(config.response_days_forward, 364)),
        str(min(max(config.limit_per_query, 1), 1000)),
        str(bool(config.suppress_stale_omnibus)),
        ",".join(_normalize_keywords(custom_keywords)),
        keyword_mode,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key):
    now = time.time()
    with _CACHE_LOCK:
        cached = _SEARCH_CACHE.get(key)
        if not cached:
            return None
        created, df, status = cached
        if now - created > CACHE_TTL_SECONDS:
            _SEARCH_CACHE.pop(key, None)
            return None
        return df.copy(deep=True), list(status)


def _cache_set(key, df, status):
    with _CACHE_LOCK:
        _SEARCH_CACHE[key] = (time.time(), df.copy(deep=True), list(status))


def _score(row, custom_keywords):
    text = _row_text(row)
    title = str(row.get("title", "") or "").lower()
    description = str(row.get("description_text", "") or "").lower()

    direct = _hits(text, DIRECT_TERMS)
    core = _hits(text, CORE_SPACE_TERMS)
    enablers = _hits(text, ENABLER_TERMS)
    title_core = _hits(title, CORE_SPACE_TERMS)
    description_core = _hits(description, CORE_SPACE_TERMS)
    custom_hits = _phrase_hits(text, custom_keywords)
    psc_match = bool(row.get("psc_match", False))
    off_domain = _hits(text, OFF_DOMAIN_TERMS)

    score = (
        (40 if psc_match else 0)
        + min(25, direct * 7)
        + min(15, core * 3)
        + min(10, enablers * 2)
        + min(10, title_core * 4)
        + min(5, description_core * 2)
        + min(15, custom_hits * 5)
        - min(35, off_domain * 15)
    )
    score = max(0, min(100, score))

    priority = (
        "Very High" if score >= 70
        else "High" if score >= 50
        else "Medium" if score >= 30
        else "Low"
    )

    domain_valid = psc_match or core > 0 or direct > 0
    reasons = []
    if psc_match:
        reasons.append("PSC match")
    if direct:
        reasons.append(f"Direct/ISAM ({direct})")
    if core:
        reasons.append(f"Core space ({core})")
    if enablers:
        reasons.append(f"Enablers ({enablers})")
    if custom_hits:
        reasons.append(f"User keywords ({custom_hits})")
    if off_domain:
        reasons.append(f"Off-domain penalty ({off_domain})")

    return pd.Series({
        "cosmic_score": score,
        "cosmic_priority": priority,
        "title_hits": title_core,
        "description_hits": description_core,
        "custom_keyword_hits": custom_hits,
        "domain_valid": domain_valid,
        "slack_eligible": bool(score >= 50 and domain_valid and off_domain == 0),
        "cosmic_reason": "; ".join(reasons) or "Weak signal",
    })


def search_sam(
    api_key,
    psc_labels,
    notice_labels,
    config,
    custom_keywords=None,
    keyword_mode="rank",
):
    if not api_key or not api_key.startswith("SAM-"):
        raise ValueError("Missing or invalid SAM_API_KEY.")
    if not psc_labels:
        raise ValueError("Select at least one PSC.")
    if not notice_labels:
        raise ValueError("Select at least one notice type.")
    if keyword_mode not in {"rank", "strict"}:
        raise ValueError("keyword_mode must be 'rank' or 'strict'.")

    custom_keywords = _normalize_keywords(custom_keywords)
    selected_pscs = {PSC_CHOICES[label] for label in psc_labels}
    selected_notices = {NOTICE_TYPES[label] for label in notice_labels}

    cache_key = _cache_key(
        api_key,
        psc_labels,
        notice_labels,
        config,
        custom_keywords,
        keyword_mode,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        df, status = cached
        return df, ["CACHE HIT | shared result reused; no SAM search call made"] + status

    now = datetime.now(timezone.utc)
    lookback = min(config.days_back, 364)
    forward = min(config.response_days_forward, 364)
    limit = min(max(config.limit_per_query, 1), 1000)

    params = {
        "api_key": api_key,
        "postedFrom": (now - timedelta(days=lookback)).strftime("%m/%d/%Y"),
        "postedTo": now.strftime("%m/%d/%Y"),
        "rdlfrom": now.strftime("%m/%d/%Y"),
        "rdlto": (now + timedelta(days=forward)).strftime("%m/%d/%Y"),
        "limit": limit,
        "offset": 0,
    }

    status = []

    with requests.Session() as session:
        try:
            response = session.get(API_BASE, params=params, timeout=(10, 120))
        except requests.RequestException as exc:
            return pd.DataFrame(), [f"BROAD RETRIEVAL | request error: {exc}"]

        status.append(f"BROAD RETRIEVAL | page 1 | HTTP {response.status_code}")

        if response.status_code == 429:
            status.append(f"THROTTLED | {response.text[:400]}")
            status.append("Search stopped immediately after SAM.gov reported quota exhaustion.")
            return pd.DataFrame(), status

        if response.status_code != 200:
            status.append(f"SAM response: {response.text[:400]}")
            return pd.DataFrame(), status

        records = response.json().get("opportunitiesData", [])
        df = pd.DataFrame(records)
        raw_count = len(df)

        if df.empty:
            status.append("COUNTS | raw=0 | final=0")
            _cache_set(cache_key, df, status)
            return df, status

        if "noticeId" not in df.columns:
            return pd.DataFrame(), status + ["SAM response did not include noticeId."]

        df = df.drop_duplicates(subset=["noticeId"]).copy()
        dedup_count = len(df)

        if "classificationCode" not in df.columns:
            df["classificationCode"] = ""
        normalized_psc = (
            df["classificationCode"]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.strip()
            .str[:4]
        )
        df["psc_match"] = normalized_psc.isin(selected_pscs)
        psc_match_count = int(df["psc_match"].sum())

        if "type" not in df.columns:
            df["type"] = ""
        df["notice_code"] = df["type"].apply(_notice_code)
        df = df[df["notice_code"].isin(selected_notices)].copy()
        notice_count = len(df)

        df["responseDeadLine_dt"] = _deadline_series(df)
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
        df["space_sniff"] = (
            df["title"].fillna("").astype(str).str.lower().str.contains("space", regex=False)
        )
        df["description_text"] = ""

        metadata_text = df.apply(_row_text, axis=1)
        core_mask = metadata_text.apply(lambda text: _hits(text, CORE_SPACE_TERMS) > 0)
        direct_mask = metadata_text.apply(lambda text: _hits(text, DIRECT_TERMS) > 0)
        df = df[df["psc_match"] | core_mask | direct_mask | df["space_sniff"]].copy()
        relevance_count = len(df)

        if config.suppress_stale_omnibus and not df.empty:
            published = (
                _to_utc(df["postedDate"])
                if "postedDate" in df.columns
                else pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
            )
            modified_col = next(
                (column for column in ("modifiedDate", "updatedDate") if column in df.columns),
                None,
            )
            if modified_col:
                modified = _to_utc(df[modified_col])
                stale = (
                    published.notna()
                    & modified.notna()
                    & (published < now_ts - pd.Timedelta(days=config.stale_published_days))
                    & (modified < now_ts - pd.Timedelta(days=config.stale_modified_days))
                )
            else:
                stale = pd.Series(False, index=df.index)
            df = df[~stale].copy()
        after_stale_count = len(df)

        if df.empty:
            status.append(
                f"COUNTS | raw={raw_count} | dedup={dedup_count} | psc-matches={psc_match_count} "
                f"| notice={notice_count} | deadline={deadline_count} | relevance={relevance_count} "
                f"| after-stale={after_stale_count} | final=0"
            )
            _cache_set(cache_key, df, status)
            return df, status

        df["preliminary_score"] = df.apply(
            lambda row: (
                (40 if row["psc_match"] else 0)
                + min(25, _hits(_row_text(row), DIRECT_TERMS) * 7)
                + min(15, _hits(_row_text(row), CORE_SPACE_TERMS) * 3)
                + min(15, _phrase_hits(_row_text(row), custom_keywords) * 5)
            ),
            axis=1,
        )
        df = df.sort_values("preliminary_score", ascending=False).copy()

        fetched = 0
        if "description" in df.columns:
            for idx in df.head(min(config.description_fetch_limit, len(df))).index:
                body = _fetch_description(session, api_key, df.at[idx, "description"])
                if body == "__THROTTLED__":
                    status.append("DESCRIPTION ENRICHMENT | HTTP 429 | stopped immediately")
                    break
                df.at[idx, "description_text"] = body
                fetched += 1

        if "pointOfContact" in df.columns:
            df["contact_info"] = df["pointOfContact"].apply(_format_contacts)
        else:
            df["contact_info"] = ""

        if custom_keywords and keyword_mode == "strict":
            strict_hits = df.apply(
                lambda row: _phrase_hits(_row_text(row), custom_keywords),
                axis=1,
            )
            df = df[strict_hits > 0].copy()

        if df.empty:
            status.append(
                f"COUNTS | raw={raw_count} | dedup={dedup_count} | psc-matches={psc_match_count} "
                f"| notice={notice_count} | deadline={deadline_count} | relevance={relevance_count} "
                f"| after-stale={after_stale_count} | descriptions={fetched} | final=0"
            )
            _cache_set(cache_key, df, status)
            return df, status

        scores = df.apply(lambda row: _score(row, custom_keywords), axis=1)
        score_columns = [column for column in scores.columns if column not in df.columns]
        df = pd.concat([df, scores[score_columns]], axis=1)

        df["sam_link"] = df["noticeId"].apply(
            lambda notice_id: f"https://sam.gov/opp/{notice_id}/view"
        )

        # Production display floor. Low-scoring diagnostic noise is suppressed.
        df = df[df["cosmic_score"] >= 30].copy()
        df = df.loc[:, ~df.columns.duplicated()].copy()
        df = df.sort_values(
            ["slack_eligible", "cosmic_score", "responseDeadLine_dt"],
            ascending=[False, False, True],
        )

        status.append(
            f"COUNTS | raw={raw_count} | dedup={dedup_count} | psc-matches={psc_match_count} "
            f"| notice={notice_count} | deadline={deadline_count} | relevance={relevance_count} "
            f"| after-stale={after_stale_count} | descriptions={fetched} | final={len(df)}"
        )
        status.append(
            f"PUBLISHABLE | score>=50 + domain validated = {int(df['slack_eligible'].sum()) if not df.empty else 0}"
        )

        _cache_set(cache_key, df, status)
        return df, status
