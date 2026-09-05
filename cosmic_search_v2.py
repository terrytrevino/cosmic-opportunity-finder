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
DEFAULT_PSC_LABELS = list(PSC_CHOICES)
NOTICE_TYPES = {
    "Solicitation": "o",
    "Presolicitation": "p",
    "Combined Synopsis/Solicitation": "k",
}

SEARCH_TERMS = [
    "space","orbit","in-space","on-orbit","in-space manufacturing",
    "in-space assembly","orbit assembly","orbit manufacturing","in-space servicing",
    "orbit servicing","low-earth orbit","geosynchronous orbit","in-space mobility",
    "space platform","in-situ resource utilization","orbital platform","space refueling",
    "orbit refueling","orbital","isam","microgravity","lunar","spacecraft","isru",
    "cubesat","deorbit","propellant","power","sbir"
]
DIRECT = [
    "isam","servicing","refuel","refueling","propellant transfer","rendezvous",
    "proximity operations","docking","berthing","capture","grapple","robotic",
    "assembly","manufacturing","repair","maintenance","life extension","inspection"
]
ENABLERS = [
    "autonomy","navigation","guidance","control","power","propulsion","communications",
    "rf","antenna","tracking","robotics","simulation","modeling","interface","standard"
]
NON_ACTIONABLE = [
    "award notice","justification","sole source","cancellation","cancelled","contract extension"
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
    t = str(text or "").lower()
    return sum(1 for x in terms if x in t)

def _text(row):
    return " ".join(str(row.get(k,"") or "") for k in
                    ["title","description","type","fullParentPathName","classificationCode","naicsCode"]).lower()

def _utc(s):
    return pd.to_datetime(s, errors="coerce", utc=True)

def _collect(session, api_key, posted_from, posted_to, config, label, psc=None, keyword=None, notice=None):
    records, lines = [], []
    for page in range(config.max_pages):
        params = {
            "api_key": api_key, "postedFrom": posted_from, "postedTo": posted_to,
            "limit": config.limit_per_query, "offset": page * config.limit_per_query,
        }
        if psc: params["ptype"] = psc
        if keyword: params["q"] = keyword
        if notice: params["typeOfNotice"] = notice
        try:
            r = session.get(API_BASE, params=params, timeout=(10,120))
            lines.append(f"{label} | page {page+1} | HTTP {r.status_code}")
            if r.status_code != 200: break
            batch = r.json().get("opportunitiesData", [])
            records.extend(batch)
            if len(batch) < config.limit_per_query: break
        except requests.RequestException as exc:
            lines.append(f"{label} | request error: {exc}")
            break
    return records, lines

def _score(row):
    text = _text(row)
    title = str(row.get("title","") or "").lower()
    desc = str(row.get("description","") or "").lower()
    direct = _hits(text, DIRECT)
    ecosystem = _hits(text, SEARCH_TERMS)
    enablers = _hits(text, ENABLERS)
    title_hits = _hits(title, SEARCH_TERMS)
    desc_hits = _hits(desc, SEARCH_TERMS)
    psc = bool(row.get("psc_match", False))
    sniff = bool(row.get("space_sniff", False))

    score = min(100,
        (25 if psc else 0) +
        min(30, direct*8) +
        min(15, ecosystem*2) +
        min(10, enablers*2) +
        min(10, title_hits*3) +
        min(5, desc_hits) +
        (2 if sniff else 0) +
        (0 if _hits(text, NON_ACTIONABLE) else 3)
    )
    priority = "Very High" if score >= 70 else "High" if score >= 50 else "Medium" if score >= 30 else "Low"
    reasons = []
    if psc: reasons.append("PSC match")
    if direct: reasons.append(f"Direct/ISAM ({direct})")
    if ecosystem: reasons.append(f"Space terms ({ecosystem})")
    if enablers: reasons.append(f"Enablers ({enablers})")
    if title_hits: reasons.append(f"Title ({title_hits})")
    if desc_hits: reasons.append(f"Description ({desc_hits})")
    if sniff: reasons.append("Space sniffer")
    return pd.Series({
        "cosmic_score":score, "cosmic_priority":priority,
        "title_hits":title_hits, "description_hits":desc_hits,
        "cosmic_reason":"; ".join(reasons) or "Weak signal"
    })

def search_sam(api_key, psc_labels, notice_labels, config):
    if not api_key or not api_key.startswith("SAM-"):
        raise ValueError("Missing or invalid SAM_API_KEY.")
    if not psc_labels: raise ValueError("Select at least one PSC.")
    if not notice_labels: raise ValueError("Select at least one notice type.")

    psc_codes = [PSC_CHOICES[x] for x in psc_labels]
    notices = [NOTICE_TYPES[x] for x in notice_labels]
    now = datetime.now(timezone.utc)
    posted_from = (now - timedelta(days=config.days_back)).strftime("%m/%d/%Y")
    posted_to = now.strftime("%m/%d/%Y")
    all_records, lines = [], []

    with requests.Session() as s:
        for psc in psc_codes:
            for notice in notices:
                recs, ls = _collect(s, api_key, posted_from, posted_to, config,
                                    f"PSC {psc} | {notice}", psc=psc, notice=notice)
                for r in recs:
                    r["_psc_hit"], r["_sniff_hit"] = True, False
                all_records += recs; lines += ls
        for notice in notices:
            recs, ls = _collect(s, api_key, posted_from, posted_to, config,
                                f"SPACE SNIFFER | {notice}", keyword="space", notice=notice)
            for r in recs:
                r["_psc_hit"], r["_sniff_hit"] = False, True
            all_records += recs; lines += ls

    df = pd.DataFrame(all_records)
    if df.empty: return df, lines
    if "noticeId" not in df.columns: return pd.DataFrame(), lines

    psc_ids = set(df.loc[df["_psc_hit"].fillna(False), "noticeId"].dropna().astype(str))
    sniff_ids = set(df.loc[df["_sniff_hit"].fillna(False), "noticeId"].dropna().astype(str))
    df = df.drop_duplicates("noticeId").copy()
    ids = df["noticeId"].astype(str)
    df["psc_match"] = ids.isin(psc_ids)
    df["space_sniff"] = ids.isin(sniff_ids)

    df["responseDeadLine_dt"] = _utc(df["responseDeadLine"]) if "responseDeadLine" in df else pd.NaT
    now_ts = pd.Timestamp.now(tz="UTC")
    max_due = now_ts + pd.Timedelta(days=config.response_days_forward)
    df = df[df["responseDeadLine_dt"].notna() &
            (df["responseDeadLine_dt"] >= now_ts) &
            (df["responseDeadLine_dt"] <= max_due)].copy()

    if config.suppress_stale_omnibus and not df.empty:
        pub = _utc(df["postedDate"]) if "postedDate" in df else pd.Series(pd.NaT,index=df.index,dtype="datetime64[ns, UTC]")
        mod = _utc(df["modifiedDate"]) if "modifiedDate" in df else pd.Series(pd.NaT,index=df.index,dtype="datetime64[ns, UTC]")
        stale = (pub.notna() & mod.notna() &
                 (pub < now_ts-pd.Timedelta(days=config.stale_published_days)) &
                 (mod < now_ts-pd.Timedelta(days=config.stale_modified_days)))
        df["stale_omnibus"] = stale
        df = df[~stale].copy()
    else:
        df["stale_omnibus"] = False

    if "title" not in df: df["title"] = ""
    if "description" not in df: df["description"] = ""
    df["local_keyword_hits"] = df.apply(lambda r: _hits(_text(r), SEARCH_TERMS), axis=1)
    df = df[df["psc_match"] | df["space_sniff"] | (df["local_keyword_hits"]>0)].copy()

    scores = df.apply(_score, axis=1)
    df = pd.concat([df, scores], axis=1)
    df["sam_link"] = df["noticeId"].apply(lambda x: f"https://sam.gov/opp/{x}/view")
    return df.sort_values(["cosmic_score","responseDeadLine_dt"], ascending=[False,True]), lines
