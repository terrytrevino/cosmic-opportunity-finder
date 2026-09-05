from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    "in-space assembly", "orbit assembly", "orbit manufacturing",
    "in-space servicing", "orbit servicing", "low-earth orbit",
    "geosynchronous orbit", "in-space mobility", "space platform",
    "in-situ resource utilization", "orbital platform", "space refueling",
    "orbit refueling", "orbital", "isam", "microgravity", "lunar",
    "spacecraft", "isru", "cubesat", "deorbit", "propellant", "power", "sbir",
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


@dataclass
class SearchConfig:
    days_back: int = 365
    response_days_forward: int = 365
    limit_per_query: int = 100
    max_pages: int = 2
    suppress_stale_omnibus: bool = True
    stale_published_days: int = 365
    stale_modified_days: int = 180


def _hits(text, terms):
    text = str(text or "").lower()
    return sum(1 for term in terms if term.lower() in text)


def _row_text(row):
    fields = [
        row.get("title", ""),
        row.get("description_text", ""),
        row.get("type", ""),
        row.get("fullParentPathName", ""),
        row.get("classificationCode", ""),
        row.get("naicsCode", ""),
    ]
    return " ".join(str(v or "") for v in fields).lower()


def _to_utc(series):
    return pd.to_datetime(series, errors="coerce", utc=True)


def _response_deadline_series(df):
    for column in ("responseDeadLine", "reponseDeadLine"):
        if column in df.columns:
            return _to_utc(df[column])
    return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")


def _fetch_description(session, api_key, description_url):
    if not isinstance(description_url, str) or not description_url.startswith("http"):
        return ""

    sep = "&" if "?" in description_url else "?"
    url = f"{description_url}{sep}api_key={api_key}"

    try:
        r = session.get(url, timeout=(10, 45))
        if r.status_code != 200:
            return ""
        return r.text[:50000]
    except requests.RequestException:
        return ""


def _collect(
    session,
    api_key,
    posted_from,
    posted_to,
    deadline_from,
    deadline_to,
    config,
    label,
    psc=None,
    notice_type=None,
    title=None,
):
    records = []
    status_lines = []

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

        if psc:
            params["ccode"] = psc

        if notice_type:
            params["ptype"] = notice_type

        if title:
            params["title"] = title

        try:
            r = session.get(API_BASE, params=params, timeout=(10, 120))
            status_lines.append(f"{label} | page {page + 1} | HTTP {r.status_code}")

            if r.status_code != 200:
                status_lines.append(f"{label} | SAM response: {r.text[:400]}")
                break

            batch = r.json().get("opportunitiesData", [])
            records.extend(batch)

            if len(batch) < config.limit_per_query:
                break

        except requests.RequestException as exc:
            status_lines.append(f"{label} | request error: {exc}")
            break

    return records, status_lines


def _score(row):
    text = _row_text(row)
    title = str(row.get("title", "") or "").lower()
    desc = str(row.get("description_text", "") or "").lower()

    direct = _hits(text, DIRECT_TERMS)
    ecosystem = _hits(text, SEARCH_TERMS)
    enablers = _hits(text, ENABLER_TERMS)
    title_hits = _hits(title, SEARCH_TERMS)
    description_hits = _hits(desc, SEARCH_TERMS)

    psc_match = bool(row.get("psc_match", False))
    space_sniff = bool(row.get("space_sniff", False))

    score = min(
        100,
        (25 if psc_match else 0)
        + min(30, direct * 8)
        + min(15, ecosystem * 2)
        + min(10, enablers * 2)
        + min(10, title_hits * 3)
        + min(5, description_hits)
        + (2 if space_sniff else 0)
        + (0 if _hits(text, NON_ACTIONABLE_TERMS) else 3),
    )

    priority = (
        "Very High" if score >= 70
        else "High" if score >= 50
        else "Medium" if score >= 30
        else "Low"
    )

    reasons = []
    if psc_match:
        reasons.append("PSC match")
    if direct:
        reasons.append(f"Direct/ISAM ({direct})")
    if ecosystem:
        reasons.append(f"Space terms ({ecosystem})")
    if enablers:
        reasons.append(f"Enablers ({enablers})")
    if title_hits:
        reasons.append(f"Title ({title_hits})")
    if description_hits:
        reasons.append(f"Description ({description_hits})")
    if space_sniff:
        reasons.append("Space title sniffer")

    return pd.Series(
        {
            "cosmic_score": score,
            "cosmic_priority": priority,
            "title_hits": title_hits,
            "description_hits": description_hits,
            "cosmic_reason": "; ".join(reasons) or "Weak signal",
        }
    )


def search_sam(api_key, psc_labels, notice_labels, config):
    if not api_key or not api_key.startswith("SAM-"):
        raise ValueError("Missing or invalid SAM_API_KEY.")
    if not psc_labels:
        raise ValueError("Select at least one PSC.")
    if not notice_labels:
        raise ValueError("Select at least one notice type.")

    psc_codes = [PSC_CHOICES[label] for label in psc_labels]
    notice_codes = [NOTICE_TYPES[label] for label in notice_labels]

    now = datetime.now(timezone.utc)

    lookback_days = min(config.days_back, 365)
    posted_from = (now - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
    posted_to = now.strftime("%m/%d/%Y")

    deadline_from = now.strftime("%m/%d/%Y")
    deadline_to = (
        now + timedelta(days=min(config.response_days_forward, 365))
    ).strftime("%m/%d/%Y")

    all_records = []
    status_lines = []

    with requests.Session() as session:
        for psc in psc_codes:
            for notice_code in notice_codes:
                records, lines = _collect(
                    session=session,
                    api_key=api_key,
                    posted_from=posted_from,
                    posted_to=posted_to,
                    deadline_from=deadline_from,
                    deadline_to=deadline_to,
                    config=config,
                    label=f"PSC {psc} | {notice_code}",
                    psc=psc,
                    notice_type=notice_code,
                )

                for record in records:
                    record["_psc_hit"] = True
                    record["_sniff_hit"] = False

                all_records.extend(records)
                status_lines.extend(lines)

        for notice_code in notice_codes:
            records, lines = _collect(
                session=session,
                api_key=api_key,
                posted_from=posted_from,
                posted_to=posted_to,
                deadline_from=deadline_from,
                deadline_to=deadline_to,
                config=config,
                label=f"SPACE TITLE SNIFFER | {notice_code}",
                title="space",
                notice_type=notice_code,
            )

            for record in records:
                record["_psc_hit"] = False
                record["_sniff_hit"] = True

            all_records.extend(records)
            status_lines.extend(lines)

        df = pd.DataFrame(all_records)
        if df.empty:
            return df, status_lines

        if "noticeId" not in df.columns:
            status_lines.append("SAM response did not include noticeId.")
            return pd.DataFrame(), status_lines

        psc_ids = set(
            df.loc[df["_psc_hit"].fillna(False).astype(bool), "noticeId"]
            .dropna()
            .astype(str)
        )
        sniff_ids = set(
            df.loc[df["_sniff_hit"].fillna(False).astype(bool), "noticeId"]
            .dropna()
            .astype(str)
        )

        df = df.drop_duplicates(subset=["noticeId"]).copy()
        ids = df["noticeId"].astype(str)

        df["psc_match"] = ids.isin(psc_ids)
        df["space_sniff"] = ids.isin(sniff_ids)

        df["responseDeadLine_dt"] = _response_deadline_series(df)

        now_ts = pd.Timestamp.now(tz="UTC")
        max_due = now_ts + pd.Timedelta(days=min(config.response_days_forward, 365))

        df = df[
            df["responseDeadLine_dt"].notna()
            & (df["responseDeadLine_dt"] >= now_ts)
            & (df["responseDeadLine_dt"] <= max_due)
        ].copy()

        if config.suppress_stale_omnibus and not df.empty:
            pub = (
                _to_utc(df["postedDate"])
                if "postedDate" in df.columns
                else pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
            )

            modified_col = None
            for candidate in ("modifiedDate", "updatedDate"):
                if candidate in df.columns:
                    modified_col = candidate
                    break

            if modified_col:
                mod = _to_utc(df[modified_col])
                stale = (
                    pub.notna()
                    & mod.notna()
                    & (pub < now_ts - pd.Timedelta(days=config.stale_published_days))
                    & (mod < now_ts - pd.Timedelta(days=config.stale_modified_days))
                )
            else:
                stale = pd.Series(False, index=df.index)

            df["stale_omnibus"] = stale
            df = df[~df["stale_omnibus"]].copy()
        else:
            df["stale_omnibus"] = False

        df["description_text"] = ""

        if "description" in df.columns and not df.empty:
            fetch_limit = min(len(df), 75)
            for idx in df.head(fetch_limit).index:
                df.at[idx, "description_text"] = _fetch_description(
                    session, api_key, df.at[idx, "description"]
                )

        if "title" not in df.columns:
            df["title"] = ""

        df["local_keyword_hits"] = df.apply(
            lambda row: _hits(_row_text(row), SEARCH_TERMS),
            axis=1,
        )

        df = df[
            df["psc_match"]
            | df["space_sniff"]
            | (df["local_keyword_hits"] > 0)
        ].copy()

        scores = df.apply(_score, axis=1)
        df = pd.concat([df, scores], axis=1)

        df["sam_link"] = df["noticeId"].apply(
            lambda x: f"https://sam.gov/opp/{x}/view"
        )

        return df.sort_values(
            ["cosmic_score", "responseDeadLine_dt"],
            ascending=[False, True],
        ), status_lines
