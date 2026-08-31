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
    # Core spacecraft / aerospace
    "336414  Guided missile and space vehicle manufacturing": "336414",
    "336415  Space vehicle propulsion units and parts": "336415",
    "336419  Other guided missile and space vehicle parts / auxiliary equipment": "336419",

    # Engineering, R&D, test
    "541330  Engineering services": "541330",
    "541715  Physical / engineering / life sciences R&D": "541715",
    "541713  Nanotechnology R&D": "541713",
    "541380  Testing laboratories and services": "541380",

    # Sensors, navigation, communications hardware
    "334511  Search / detection / navigation / guidance instruments": "334511",
    "334220  Wireless communications equipment / satellite hardware": "334220",
    "334413  Semiconductor and related device manufacturing": "334413",
    "334416  Capacitor / resistor / coil / transformer manufacturing": "334416",

    # Satellite communications / tracking
    "517410  Satellite telecommunications": "517410",
    "517810  Satellite tracking / telemetry / specialized telecommunications": "517810",

    # Software, systems integration, digital
    "541511  Custom computer programming services": "541511",
    "541512  Computer systems design services": "541512",
    "541519  Other computer related services": "541519",

    # Broader aerospace ecosystem
    "336411  Aircraft manufacturing": "336411",
    "336412  Aircraft engine and engine parts manufacturing": "336412",
    "336413  Other aircraft parts and auxiliary equipment manufacturing": "336413",

    # Technical / logistics support
    "541690  Other scientific and technical consulting services": "541690",
    "541614  Process, physical distribution, and logistics consulting": "541614",
}

NAICS_GROUPS = {
    "Core COSMIC": [
        "336414  Guided missile and space vehicle manufacturing",
        "336415  Space vehicle propulsion units and parts",
        "336419  Other guided missile and space vehicle parts / auxiliary equipment",
        "541330  Engineering services",
        "541715  Physical / engineering / life sciences R&D",
        "541380  Testing laboratories and services",
        "334511  Search / detection / navigation / guidance instruments",
        "334220  Wireless communications equipment / satellite hardware",
        "517410  Satellite telecommunications",
        "517810  Satellite tracking / telemetry / specialized telecommunications",
    ],

    "Extended Space Ecosystem": [
        "334413  Semiconductor and related device manufacturing",
        "334416  Capacitor / resistor / coil / transformer manufacturing",
        "541713  Nanotechnology R&D",
        "541511  Custom computer programming services",
        "541512  Computer systems design services",
        "541519  Other computer related services",
        "541690  Other scientific and technical consulting services",
        "541614  Process, physical distribution, and logistics consulting",
        "336411  Aircraft manufacturing",
        "336412  Aircraft engine and engine parts manufacturing",
        "336413  Other aircraft parts and auxiliary equipment manufacturing",
    ],
}

NAICS_GROUPS["Core + Extended"] = (
    NAICS_GROUPS["Core COSMIC"]
    + NAICS_GROUPS["Extended Space Ecosystem"]
)

KEYWORD_LIBRARY = {
    "Refueling / Depot": ["refuel", "refueling", "propellant", "fuel depot", "depot", "tanker", "transfer"],
    "RPO / Docking": ["rpo", "rendezvous", "proximity", "rpod", "docking", "berthing", "capture", "mate"],
    "Robotics / Manipulation": ["robotic", "robotics", "manipulator", "arm", "grapple", "end effector"],
    "On-orbit servicing": ["on-orbit", "on orbit", "servicing", "life extension", "repair", "upgrade"],
    "Manufacturing / Assembly": ["manufacturing", "assembly", "in-space manufacturing", "in space manufacturing", "additive", "3d print", "welding"],
    "EDL / TPS": ["reentry", "tps", "thermal protection", "heat shield", "hypersonic", "aerocapture", "aerobrake"],
    "Comms / RF": ["rf", "satcom", "antenna", "telemetry", "tt&c", "optical comm", "lasercom"],
    "Power": ["power", "nuclear power", "solar", "battery", "eps"],
}

KEYWORD_BUNDLES = {
    "ISAM": ["isam","in-space servicing","in space servicing","on-orbit","on orbit",
             "on-orbit servicing","rpo","rendezvous","proximity operations",
             "in-space assembly","in space assembly","assembly",
             "in-space manufacturing","in space manufacturing","manufacturing"],
    "Space Systems": ["spacecraft","satellite","launch","payload","cislunar","space vehicle",
                      "leo","meo","geo","ground segment","orbit"],
    "EDL / TPS / Hypersonics": ["edl","entry descent landing","reentry","tps","thermal protection",
                                "heat shield","aerobrake","aerocapture","hypersonic"],
    "Avionics / GNC / Flight SW": ["avionics","gnc","guidance","navigation","control",
                                   "flight software","embedded","firmware","real-time","rtos"],
    "Propulsion": ["propulsion","thruster","hall thruster","ion","electric propulsion",
                   "chemical propulsion","rocket engine"],
    "Power / Thermal / Structures": ["power","battery","eps","solar array","thermal","radiator",
                                     "structures","materials","composites"],
    "Comms / RF": ["communications","rf","satcom","antenna","transceiver","telemetry","tt&c"],
}

ENG_TERMS = [
    "engineering","avionics","propulsion","gnc","guidance","navigation","control",
    "thermal","structures","materials","power","battery","flight software","embedded",
    "firmware","communications","rf"
]

# -----------------------------------------------------------------
# COSMIC relevance layer
# Based on COSMIC capability/technology taxonomies:
# servicing, RPO/capture/docking, robotic manipulation, refueling,
# assembly/manufacturing, autonomy, power, propulsion, comm/nav,
# verification/validation, mobility/logistics, and surface systems.
# -----------------------------------------------------------------

COSMIC_DIRECT_ISAM = [
    "isam", "in-space servicing", "on-orbit servicing", "satellite servicing",
    "refuel", "refueling", "fluid transfer", "propellant transfer", "fuel depot",
    "rendezvous", "proximity operations", "rpo", "rpod", "docking", "berthing",
    "capture", "grapple", "robotic manipulation", "robotic servicing",
    "in-space assembly", "on-orbit assembly", "structural assembly",
    "in-space manufacturing", "on-orbit manufacturing", "additive manufacturing",
    "repair", "maintenance", "upgrade", "life extension", "relocation",
    "inspection", "metrology", "recycling", "reuse", "repurpose"
]

COSMIC_ECOSYSTEM = [
    "spacecraft", "satellite", "space vehicle", "payload", "launch",
    "access to space", "cislunar", "lunar", "orbital", "orbit",
    "ground systems", "ground segment", "exploration systems",
    "space mobility", "space logistics", "surface infrastructure", "isru"
]

COSMIC_ENABLERS = [
    "autonomy", "autonomous", "automation", "distributed control",
    "control and estimation", "guidance", "navigation", "gnc", "positioning",
    "flight software", "computer vision", "relative navigation",
    "power", "nuclear power", "solar array", "energy storage",
    "propulsion", "thruster", "communications", "laser communication",
    "optical communication", "rf", "antenna", "tracking",
    "verification", "validation", "iv&v", "modeling", "simulation",
    "interface", "standard", "modular", "robotics", "robotic"
]

BROAD_MEMBER_TERMS = [
    "research", "development", "engineering", "technology", "prototype",
    "demonstration", "innovation", "rfi", "sources sought", "solicitation",
    "broad agency announcement", "baa", "commercial", "industry"
]

NON_ACTIONABLE_TERMS = [
    "award notice", "justification", "exception to fair opportunity",
    "intent to sole source", "sole source", "cancellation", "cancelled"
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
    terms = []
    for bundle in selected_bundles:
        terms.extend(KEYWORD_BUNDLES[bundle])
    for keyword_group in selected_keywords:
        terms.extend(KEYWORD_LIBRARY[keyword_group])
    return sorted(set(terms))


def _deadline_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _combined_text(row: pd.Series) -> str:
    fields = [
        row.get("title", ""),
        row.get("type", ""),
        row.get("fullParentPathName", ""),
        row.get("classificationCode", ""),
    ]
    return " ".join(str(x or "") for x in fields).lower()


def _cosmic_relevance(row: pd.Series) -> pd.Series:
    text = _combined_text(row)

    direct_hits = _count_hits(text, COSMIC_DIRECT_ISAM)
    ecosystem_hits = _count_hits(text, COSMIC_ECOSYSTEM)
    enabler_hits = _count_hits(text, COSMIC_ENABLERS)
    member_hits = _count_hits(text, BROAD_MEMBER_TERMS)
    non_actionable_hits = _count_hits(text, NON_ACTIONABLE_TERMS)

    # 100-point scale:
    # 40 direct ISAM, 20 ecosystem, 20 enabling tech,
    # 10 actionability, 10 broad member applicability.
    direct_score = min(40, direct_hits * 10)
    ecosystem_score = min(20, ecosystem_hits * 5)
    enabler_score = min(20, enabler_hits * 4)

    # Start actionable unless the notice clearly looks like a result/justification.
    actionability_score = 10
    if non_actionable_hits:
        actionability_score = 2

    # Passed deadlines should not rank as actionable.
    if bool(row.get("deadline_passed", False)):
        actionability_score = 0

    member_score = min(10, member_hits * 3)
    if direct_hits or ecosystem_hits or enabler_hits:
        member_score = max(member_score, 4)

    cosmic_score = (
        direct_score
        + ecosystem_score
        + enabler_score
        + actionability_score
        + member_score
    )

    if cosmic_score >= 70:
        priority = "Very High"
    elif cosmic_score >= 50:
        priority = "High"
    elif cosmic_score >= 30:
        priority = "Medium"
    else:
        priority = "Low"

    reasons = []
    if direct_hits:
        reasons.append(f"Direct ISAM ({direct_hits})")
    if ecosystem_hits:
        reasons.append(f"Space ecosystem ({ecosystem_hits})")
    if enabler_hits:
        reasons.append(f"Enabling tech ({enabler_hits})")
    if non_actionable_hits:
        reasons.append("Low actionability notice")
    if not reasons:
        reasons.append("Weak COSMIC signal")

    return pd.Series({
        "cosmic_score": cosmic_score,
        "cosmic_priority": priority,
        "cosmic_direct_isam": direct_score,
        "cosmic_ecosystem": ecosystem_score,
        "cosmic_enablers": enabler_score,
        "cosmic_actionability": actionability_score,
        "cosmic_member_value": member_score,
        "cosmic_reason": "; ".join(reasons),
    })


def _request_json(session, api_key, posted_from, posted_to, agency, naics, limit):
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


def search_sam(api_key, agencies, naics_labels, selected_bundles, selected_keywords, config):
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

    status_lines, all_records = [], []

    with requests.Session() as session:
        for agency in agencies:
            for naics in naics_codes:
                try:
                    status, data = _request_json(
                        session, api_key, posted_from, posted_to,
                        agency, naics, config.limit_per_query
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

    # Layer 1: search-match score
    out["search_score"] = (
        out["domain_hits"] * config.domain_weight
        + out["eng_hits"] * config.eng_weight
        + out["isam_hits"] * config.isam_boost
    )

    # Keep old score name for compatibility with Slack and existing exports.
    out["score"] = out["search_score"]

    # Layer 2: COSMIC strategic relevance score
    cosmic_cols = out.apply(_cosmic_relevance, axis=1)
    out = pd.concat([out, cosmic_cols], axis=1)

    if "noticeId" in out.columns:
        out["sam_link"] = out["noticeId"].apply(
            lambda x: f"https://sam.gov/opp/{x}/view" if pd.notnull(x) else ""
        )

    # COSMIC score is now the primary ranking; search score breaks ties.
    sort_cols = [c for c in ["cosmic_score", "search_score", "postedDate"] if c in out.columns]
    out = out.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    return out, status_lines
