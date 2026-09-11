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

DEFAULT_PSC_LABELS = list(PSC_CHOICES)

NOTICE_TYPES = {
    "Solicitation": "o",
    "Presolicitation": "p",
    "Combined Synopsis/Solicitation": "k",
}

SEARCH_TERMS = [
    "space", "orbit", "orbital", "spacecraft", "satellite", "lunar",
    "isam", "microgravity", "cubesat", "deorbit", "on-orbit", "in-space",
    "servicing", "refuel", "refueling", "rendezvous", "docking", "robotic",
    "assembly", "manufacturing", "propellant", "power",
]

DIRECT_TERMS = [
    "isam", "servicing", "refuel", "refueling", "propellant transfer",
    "rendezvous", "proximity operations", "docking", "berthing", "capture",
    "grapple", "robotic", "assembly", "manufacturing", "repair",
    "maintenance", "life extension", "inspection",
]

ENABLER_TERMS = [
    "autonomy", "navigation", "guidance", "control", "power", "propulsion",
    "communications", "rf", "antenna", "tracking", "robotics", "simulation",
    "modeling", "interface", "standard",
]

NON_ACTIONABLE = [
    "award notice", "justification", "sole source", "cancellation",
    "cancelled", "contract extension",
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


def _clean(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _terms(value):
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[,;\n]+", value)
    return [str(x).strip().lower() for x in value if str(x).strip()]


def _hits(text, terms):
    text = str(text or "").lower()
    return sum(1 for term in terms if term.lower() in text)


def _phrase_hits(text, terms):
    text = str(text or "").lower()
    return sum(
        1 for term in terms
        if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text)
    )


def _to_utc(series):
    return pd.to_datetime(series, errors="coerce", utc=True)


def _deadline(df):
    for column in ("responseDeadLine", "reponseDeadLine"):
        if column in df.columns:
            return _to_utc(df[column])
    return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")


def _notice(value):
    value = str(value or "").strip().lower()
    if value in ("o", "solicitation"):
        return "o"
    if value in ("p", "presolicitation"):
        return "p"
    if value in ("k", "combined synopsis/solicitation", "combined synopsis solicitation"):
        return "k"
    return value


def _contacts(value):
    if not value:
        return ""
    out = []
    for contact in value if isinstance(value, list) else [value]:
        if not isinstance(contact, dict):
            out.append(_clean(contact))
            continue
        parts = []
        for key in ("type", "fullName", "title", "email", "phone", "fax"):
            item = _clean(contact.get(key, ""))
            if item and item not in parts:
                parts.append(item)
        if parts:
            out.append(" | ".join(parts))
    return "; ".join(out)


def _row_text(row):
    fields = [
        "title", "description_text", "type", "fullParentPathName",
        "classificationCode", "naicsCode",
    ]
    return " ".join(str(row.get(c, "") or "") for c in fields).lower()


def _fetch_description(session, api_key, url):
    if not isinstance(url, str) or not url.startswith("http"):
        return ""
    separator = "&" if "?" in url else "?"
    try:
        response = session.get(
            f"{url}{separator}api_key={api_key}", timeout=(10, 45)
        )
        if response.status_code == 429:
            return "__THROTTLED__"
        if response.status_code == 200:
            return _clean(response.text[:50000])
        return ""
    except requests.RequestException:
        return ""


def _score(row, custom_keywords):
    text = _row_text(row)
    title = str(row.get("title", "") or "").lower()
    description = str(row.get("description_text", "") or "").lower()

    direct = _hits(text, DIRECT_TERMS)
    ecosystem = _hits(text, SEARCH_TERMS)
    enablers = _hits(text, ENABLER_TERMS)
    title_hits = _hits(title, SEARCH_TERMS)
    description_hits = _hits(description, SEARCH_TERMS)
    custom_hits = _phrase_hits(text, custom_keywords)

    score = min(
        100,
        (25 if bool(row.get("psc_match", False)) else 0)
        + min(30, direct * 8)
        + min(15, ecosystem * 2)
        + min(10, enablers * 2)
        + min(10, title_hits * 3)
        + min(5, description_hits)
        + min(24, custom_hits * 8)
        + (2 if bool(row.get("space_sniff", False)) else 0)
        + (0 if _hits(text, NON_ACTIONABLE) else 3),
    )

    priority = (
        "Very High" if score >= 70
        else "High" if score >= 50
        else "Medium" if score >= 30
        else "Low"
    )

    reasons = []
    if bool(row.get("psc_match", False)):
        reasons.append("PSC match")
    if direct:
        reasons.append(f"Direct/ISAM ({direct})")
    if ecosystem:
        reasons.append(f"Space terms ({ecosystem})")
    if custom_hits:
        reasons.append(f"User keywords ({custom_hits})")
    if description_hits:
        reasons.append(f"Description ({description_hits})")

    return pd.Series({
        "cosmic_score": score,
        "cosmic_priority": priority,
        "title_hits": title_hits,
        "description_hits": description_hits,
        "custom_keyword_hits": custom_hits,
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

    custom = _terms(custom_keywords)
    selected_psc = {PSC_CHOICES[x] for x in psc_labels}
    selected_notice = {NOTICE_TYPES[x] for x in notice_labels}

    if keyword_mode not in {"rank", "strict"}:
        raise ValueError("keyword_mode must be 'rank' or 'strict'.")

    now = datetime.now(timezone.utc)
    back = min(config.days_back, 364)
    forward = min(config.response_days_forward, 364)

    params_base = {
        "api_key": api_key,
        "postedFrom": (now - timedelta(days=back)).strftime("%m/%d/%Y"),
        "postedTo": now.strftime("%m/%d/%Y"),
        "rdlfrom": now.strftime("%m/%d/%Y"),
        "rdlto": (now + timedelta(days=forward)).strftime("%m/%d/%Y"),
        "limit": config.limit_per_query,
    }

    records = []
    status = []

    with requests.Session() as session:
        for page in range(config.max_pages):
            params = dict(params_base)
            params["offset"] = page * config.limit_per_query
            try:
                response = session.get(API_BASE, params=params, timeout=(10, 120))
            except requests.RequestException as exc:
                status.append(f"BROAD RETRIEVAL | request error: {exc}")
                break

            status.append(
                f"BROAD RETRIEVAL | page {page + 1} | HTTP {response.status_code}"
            )

            if response.status_code == 429:
                status.append(f"THROTTLED | {response.text[:400]}")
                status.append(
                    "Search stopped immediately after SAM.gov reported quota exhaustion."
                )
                break

            if response.status_code != 200:
                status.append(f"SAM response: {response.text[:400]}")
                break

            batch = response.json().get("opportunitiesData", [])
            records.extend(batch)
            if len(batch) < config.limit_per_query:
                break

        if not records:
            return pd.DataFrame(), status

        df = pd.DataFrame(records)
        raw_count = len(df)

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
        df["psc_match"] = normalized_psc.isin(selected_psc)

        if "type" not in df.columns:
            df["type"] = ""
        df["notice_code"] = df["type"].apply(_notice)
        df = df[df["notice_code"].isin(selected_notice)].copy()
        notice_count = len(df)

        df["responseDeadLine_dt"] = _deadline(df)
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
            df["title"]
            .fillna("")
            .astype(str)
            .str.lower()
            .str.contains("space", regex=False)
        )
        df["description_text"] = ""
        df["local_keyword_hits"] = df.apply(
            lambda row: _hits(_row_text(row), SEARCH_TERMS), axis=1
        )
        df = df[
            df["psc_match"]
            | df["space_sniff"]
            | (df["local_keyword_hits"] > 0)
        ].copy()
        relevance_count = len(df)

        if config.suppress_stale_omnibus and not df.empty:
            pub = (
                _to_utc(df["postedDate"])
                if "postedDate" in df.columns
                else pd.Series(
                    pd.NaT, index=df.index, dtype="datetime64[ns, UTC]"
                )
            )
            modified_col = next(
                (c for c in ("modifiedDate", "updatedDate") if c in df.columns),
                None,
            )
            if modified_col:
                modified = _to_utc(df[modified_col])
                stale = (
                    pub.notna()
                    & modified.notna()
                    & (
                        pub
                        < now_ts - pd.Timedelta(days=config.stale_published_days)
                    )
                    & (
                        modified
                        < now_ts - pd.Timedelta(days=config.stale_modified_days)
                    )
                )
                df = df[~stale].copy()

        after_stale_count = len(df)

        if df.empty:
            status.append(
                "COUNTS | "
                f"raw={raw_count} | dedup={dedup_count} | notice={notice_count} | "
                f"deadline={deadline_count} | relevance={relevance_count} | "
                f"after-stale={after_stale_count} | final=0"
            )
            return df, status

        df["preliminary_score"] = df.apply(
            lambda row: (
                (25 if bool(row.get("psc_match", False)) else 0)
                + min(30, _hits(_row_text(row), DIRECT_TERMS) * 8)
                + min(24, _phrase_hits(_row_text(row), custom) * 8)
            ),
            axis=1,
        )
        df = df.sort_values("preliminary_score", ascending=False)

        fetched = 0
        if "description" in df.columns:
            for idx in df.head(
                min(config.description_fetch_limit, len(df))
            ).index:
                body = _fetch_description(
                    session, api_key, df.at[idx, "description"]
                )
                if body == "__THROTTLED__":
                    status.append(
                        "DESCRIPTION ENRICHMENT | HTTP 429 | stopped immediately"
                    )
                    break
                df.at[idx, "description_text"] = body
                fetched += 1

        if "pointOfContact" in df.columns:
            df["contact_info"] = df["pointOfContact"].apply(_contacts)
        else:
            df["contact_info"] = ""

        # Strict filtering uses a temporary column so it cannot collide with
        # the scored output column of the same concept.
        if custom and keyword_mode == "strict":
            df["_strict_keyword_hits"] = df.apply(
                lambda row: _phrase_hits(_row_text(row), custom), axis=1
            )
            df = df[df["_strict_keyword_hits"] > 0].copy()
            df = df.drop(columns=["_strict_keyword_hits"], errors="ignore")

        if df.empty:
            status.append(
                "COUNTS | "
                f"raw={raw_count} | dedup={dedup_count} | notice={notice_count} | "
                f"deadline={deadline_count} | relevance={relevance_count} | "
                f"after-stale={after_stale_count} | descriptions={fetched} | final=0"
            )
            return df, status

        scores = df.apply(lambda row: _score(row, custom), axis=1)
        df = pd.concat([df, scores], axis=1)

        # Final safety guard for Streamlit/PyArrow.
        df = df.loc[:, ~df.columns.duplicated()].copy()

        df["sam_link"] = df["noticeId"].apply(
            lambda x: f"https://sam.gov/opp/{x}/view"
        )

        df = df.sort_values(
            ["cosmic_score", "responseDeadLine_dt"],
            ascending=[False, True],
        )

        status.append(
            "COUNTS | "
            f"raw={raw_count} | dedup={dedup_count} | notice={notice_count} | "
            f"deadline={deadline_count} | relevance={relevance_count} | "
            f"after-stale={after_stale_count} | descriptions={fetched} | final={len(df)}"
        )

        return df, status
