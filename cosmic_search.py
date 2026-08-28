from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd
import requests


API_BASE = "https://api.sam.gov/opportunities/v2/search"

AGENCY_CHOICES = [
    "NASA",
    "DEPT OF THE AIR FORCE",
    "SPACE DEVELOPMENT AGENCY",
    "DARPA",
    "DEPT OF THE NAVY",
    "MISSILE DEFENSE AGENCY",
    "DEPT OF DEFENSE",
]

NAICS_CHOICES = {
    "336414  Space vehicle / missile & space manufacturing": "336414",
    "541330  Engineering services": "541330",
    "541715  R&D engineering/physical sciences": "541715",
    "334511  Search/detection/navigation instruments": "334511",
    "334220  Communications equipment": "334220",
}

KEYWORD_LIBRARY = {
    "Refueling / Depot": [
        "refuel", "refueling", "propellant", "fuel depot", "depot", "tanker", "transfer"
    ],
    "RPO / Docking": [
        "rpo", "rendezvous", "proximity", "rpod", "docking", "berthing", "capture", "mate"
    ],
    "Robotics / Manipulation": [
        "robotic", "robotics", "manipulator", "arm", "grapple", "end effector"
    ],
    "On-orbit servicing": [
        "on-orbit", "on orbit", "servicing", "life extension", "repair", "upgrade"
    ],
    "Manufacturing / Assembly": [
        "manufacturing", "assembly", "in-space manufacturing", "in space manufacturing",
        "additive", "3d print", "welding"
    ],
    "EDL / TPS": [
        "reentry", "tps", "thermal protection", "heat shield", "hypersonic",
        "aerocapture", "aerobrake"
    ],
    "Comms / RF": [
        "rf", "satcom", "antenna", "telemetry", "tt&c", "optical comm", "lasercom"
    ],
    "Power": [
        "power", "nuclear power", "solar", "battery", "eps"
    ],
}

KEYWORD_BUNDLES = {
    "ISAM": [
        "isam", "in-space servicing", "in space servicing", "on-orbit", "on orbit",
        "on-orbit servicing", "rpo", "rendezvous", "proximity operations",
        "in-space assembly", "in space assembly", "assembly",
        "in-space manufacturing", "in space manufacturing", "manufacturing"
    ],
    "Space Systems": [
        "spacecraft", "satellite", "launch", "payload", "cislunar",
        "space vehicle", "leo", "meo", "geo", "ground segment", "orbit"
    ],
    "EDL / TPS / Hypersonics": [
        "edl", "entry descent landing", "reentry", "tps", "thermal protection",
        "heat shield", "aerobrake", "aerocapture", "hypersonic"
    ],
    "Avionics / GNC / Flight SW": [
        "avionics", "gnc", "guidance", "navigation", "control",
        "flight software", "embedded", "firmware", "real-time", "rtos"
    ],
    "Propulsion": [
        "propulsion", "thruster", "hall thruster", "ion", "electric propulsion",
        "chemical propulsion", "rocket engine"
    ],
    "Power / Thermal / Structures": [
        "power", "battery", "eps", "solar array", "thermal", "radiator",
        "structures", "materials", "composites"
    ],
    "Comms / RF": [
        "communications", "rf", "satcom", "antenna", "transceiver", "telemetry", "tt&c"
    ],
}

ENG_TERMS = [
    "engineering", "avionics", "propulsion", "gnc", "guidance", "navigation",
    "control", "thermal", "structures", "materials", "power", "battery",
    "flight software", "embedded", "firmware", "communications", "rf"
]


@dataclass
class SearchConfig:
    days_back: int = 60
    limit_per_query: int = 100
    only_open: bool = True
    strict_eng: bool = False
    domain_weight: int = 2
    eng_weight: int = 1
    isam_boost: int = 5


def _count_hits(text: str, terms: Iterable[str]) -> int:
    t = str(text or "").lower()
    return sum(1 for term in terms if term in t)


def _build_terms(selected_bundles: list[str], selected_keywords: list[str]) -> list[str]:
    terms: list[str] = []
    for bundle in selected_bundles:
        terms.extend(KEYWORD_BUNDLES[bundle])
    for keyword_group in selected_keywords:
        terms.extend(KEYWORD_LIBRARY[keyword_group])
    return sorted(set(terms))


def _deadline_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _request_json(
    session: requests.Session,
    api_key: str,
    posted_from: str,
    posted_to: str,
    agency: str,
    naics: str,
    limit: int,
) -> tuple[int, dict]:
    params = {
        "api_key": api_key,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "limit": limit,
        "offset": 0,
        "organizationName": agency,
        "ncode": naics,
    }
    response = session.get(API_BASE, params=params, timeout=(10, 120))
    if response.status_code != 200:
        return response.status_code, {}
    return response.status_code, response.json()


def search_sam(
    api_key: str,
    agencies: list[str],
    naics_labels: list[str],
    selected_bundles: list[str],
    selected_keywords: list[str],
    config: SearchConfig,
) -> tuple[pd.DataFrame, list[str]]:
    if not api_key or not api_key.startswith("SAM-"):
        raise ValueError("Missing or invalid SAM_API_KEY.")

    if not agencies:
        raise ValueError("Select at least one agency.")

    if not naics_labels:
        raise ValueError("Select at least one NAICS code.")

    domain_terms = _build_terms(selected_bundles, selected_keywords)
    if not domain_terms:
        raise ValueError("Select at least one Topic or Focus Term.")

    naics_codes = [NAICS_CHOICES[label] for label in naics_labels]
    isam_terms = KEYWORD_BUNDLES["ISAM"]

    today = datetime.today()
    posted_from = (today - timedelta(days=config.days_back)).strftime("%m/%d/%Y")
    posted_to = today.strftime("%m/%d/%Y")

    status_lines: list[str] = []
    all_records: list[dict] = []

    with requests.Session() as session:
        for agency in agencies:
            for naics in naics_codes:
                try:
                    status, data = _request_json(
                        session,
                        api_key,
                        posted_from,
                        posted_to,
                        agency,
                        naics,
                        config.limit_per_query,
                    )
                    status_lines.append(f"{agency} | {naics} | HTTP {status}")
                    if status == 200:
                        all_records.extend(data.get("opportunitiesData", []))
                except requests.RequestException as exc:
                    status_lines.append(f"{agency} | {naics} | request error: {exc}")

    df = pd.DataFrame(all_records)
    if df.empty:
        return pd.DataFrame(), status_lines

    if "noticeId" in df.columns:
        df = df.drop_duplicates(subset=["noticeId"]).copy()

    if "title" not in df.columns:
        df["title"] = ""

    df["domain_hits"] = df["title"].fillna("").apply(lambda x: _count_hits(x, domain_terms))
    df["isam_hits"] = df["title"].fillna("").apply(lambda x: _count_hits(x, isam_terms))
    df["eng_hits"] = df["title"].fillna("").apply(lambda x: _count_hits(x, ENG_TERMS))

    out = df[df["domain_hits"] > 0].copy()

    if config.strict_eng:
        out = out[out["eng_hits"] > 0].copy()

    if "responseDeadLine" in out.columns:
        out["responseDeadLine_dt"] = _deadline_dt(out["responseDeadLine"])
        now_utc = pd.Timestamp.utcnow()
        out["deadline_passed"] = (
            out["responseDeadLine_dt"].notna()
            & (out["responseDeadLine_dt"] < now_utc)
        )
    else:
        out["deadline_passed"] = False

    if config.only_open:
        out = out[out["deadline_passed"] == False].copy()

    out["score"] = (
        out["domain_hits"] * config.domain_weight
        + out["eng_hits"] * config.eng_weight
        + out["isam_hits"] * config.isam_boost
    )

    if "noticeId" in out.columns:
        out["sam_link"] = out["noticeId"].apply(
            lambda x: f"https://sam.gov/opp/{x}/view" if pd.notnull(x) else ""
        )

    sort_cols = [c for c in ["score", "postedDate"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    return out, status_lines
