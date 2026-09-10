from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
import re
import pandas as pd
import requests

API_BASE="https://api.sam.gov/opportunities/v2/search"
PSC_CHOICES={
"AR11  R&D - Space: Basic Research":"AR11","AR12  R&D - Space: Applied Research":"AR12","AR13  R&D - Space: Advanced Development":"AR13",
"AC11  R&D - Defense Aircraft: Basic Research":"AC11","AC12  R&D - Defense Aircraft: Applied Research":"AC12","AC13  R&D - Defense Aircraft: Advanced Development":"AC13",
"AC31  R&D - Defense Ships: Basic Research":"AC31","AC32  R&D - Defense Ships: Applied Research":"AC32","AC33  R&D - Defense Ships: Advanced Development":"AC33",
"1555  Space Vehicles":"1555","1675  Space Vehicle Components":"1675","1677  Space Vehicle Remote Control Systems":"1677","1735  Space Vehicle Maintenance / Servicing Equipment":"1735"}
DEFAULT_PSC_LABELS=list(PSC_CHOICES)
NOTICE_TYPES={"Solicitation":"o","Presolicitation":"p","Combined Synopsis/Solicitation":"k"}
SEARCH_TERMS=["space","orbit","orbital","spacecraft","satellite","lunar","isam","microgravity","cubesat","deorbit","on-orbit","in-space","servicing","refuel","refueling","rendezvous","docking","robotic","assembly","manufacturing","propellant","power"]
DIRECT_TERMS=["isam","servicing","refuel","refueling","propellant transfer","rendezvous","proximity operations","docking","berthing","capture","grapple","robotic","assembly","manufacturing","repair","maintenance","life extension","inspection"]
ENABLER_TERMS=["autonomy","navigation","guidance","control","power","propulsion","communications","rf","antenna","tracking","robotics","simulation","modeling","interface","standard"]
NON_ACTIONABLE=["award notice","justification","sole source","cancellation","cancelled","contract extension"]

@dataclass
class SearchConfig:
    days_back:int=365
    response_days_forward:int=365
    limit_per_query:int=100
    max_pages:int=2
    suppress_stale_omnibus:bool=True
    stale_published_days:int=365
    stale_modified_days:int=180
    description_fetch_limit:int=5

def _clean(v):
    s=unescape(str(v or "")); s=re.sub(r"<[^>]+>"," ",s); return re.sub(r"\s+"," ",s).strip()
def _terms(v):
    if not v:return []
    if isinstance(v,str):v=re.split(r"[,;\n]+",v)
    return [str(x).strip().lower() for x in v if str(x).strip()]
def _hits(text,terms):
    t=str(text or "").lower(); return sum(1 for x in terms if x.lower() in t)
def _phits(text,terms):
    t=str(text or "").lower(); return sum(1 for x in terms if re.search(rf"(?<!\w){re.escape(x)}(?!\w)",t))
def _utc(s):return pd.to_datetime(s,errors="coerce",utc=True)
def _deadline(df):
    for c in ("responseDeadLine","reponseDeadLine"):
        if c in df:return _utc(df[c])
    return pd.Series(pd.NaT,index=df.index,dtype="datetime64[ns, UTC]")
def _notice(v):
    x=str(v or "").strip().lower()
    if x in ("o","solicitation"):return "o"
    if x in ("p","presolicitation"):return "p"
    if x in ("k","combined synopsis/solicitation","combined synopsis solicitation"):return "k"
    return x
def _contacts(v):
    if not v:return ""
    out=[]
    for c in (v if isinstance(v,list) else [v]):
        if not isinstance(c,dict):out.append(_clean(c));continue
        p=[]
        for k in ("type","fullName","title","email","phone","fax"):
            z=_clean(c.get(k,""))
            if z and z not in p:p.append(z)
        if p:out.append(" | ".join(p))
    return "; ".join(out)
def _text(r):return " ".join(str(r.get(c,"") or "") for c in ["title","description_text","type","fullParentPathName","classificationCode","naicsCode"]).lower()
def _desc(session,key,url):
    if not isinstance(url,str) or not url.startswith("http"):return ""
    sep="&" if "?" in url else "?"
    try:
        q=session.get(f"{url}{sep}api_key={key}",timeout=(10,45))
        if q.status_code==429:return "__THROTTLED__"
        return _clean(q.text[:50000]) if q.status_code==200 else ""
    except requests.RequestException:return ""

def _score(r,custom):
    text=_text(r); title=str(r.get("title","") or "").lower(); desc=str(r.get("description_text","") or "").lower()
    direct=_hits(text,DIRECT_TERMS); eco=_hits(text,SEARCH_TERMS); ena=_hits(text,ENABLER_TERMS); th=_hits(title,SEARCH_TERMS); dh=_hits(desc,SEARCH_TERMS); ch=_phits(text,custom)
    score=min(100,(25 if r.get("psc_match",False) else 0)+min(30,direct*8)+min(15,eco*2)+min(10,ena*2)+min(10,th*3)+min(5,dh)+min(24,ch*8)+(2 if r.get("space_sniff",False) else 0)+(0 if _hits(text,NON_ACTIONABLE) else 3))
    priority="Very High" if score>=70 else "High" if score>=50 else "Medium" if score>=30 else "Low"
    why=[]
    if r.get("psc_match",False):why.append("PSC match")
    if direct:why.append(f"Direct/ISAM ({direct})")
    if eco:why.append(f"Space terms ({eco})")
    if ch:why.append(f"User keywords ({ch})")
    if dh:why.append(f"Description ({dh})")
    return pd.Series({"cosmic_score":score,"cosmic_priority":priority,"title_hits":th,"description_hits":dh,"custom_keyword_hits":ch,"cosmic_reason":"; ".join(why) or "Weak signal"})

def search_sam(api_key,psc_labels,notice_labels,config,custom_keywords=None,keyword_mode="rank"):
    if not api_key or not api_key.startswith("SAM-"):raise ValueError("Missing or invalid SAM_API_KEY.")
    if not psc_labels:raise ValueError("Select at least one PSC.")
    if not notice_labels:raise ValueError("Select at least one notice type.")
    custom=_terms(custom_keywords); selected_psc={PSC_CHOICES[x] for x in psc_labels}; selected_notice={NOTICE_TYPES[x] for x in notice_labels}
    now=datetime.now(timezone.utc); back=min(config.days_back,364); fwd=min(config.response_days_forward,364)
    params_base={"api_key":api_key,"postedFrom":(now-timedelta(days=back)).strftime("%m/%d/%Y"),"postedTo":now.strftime("%m/%d/%Y"),"rdlfrom":now.strftime("%m/%d/%Y"),"rdlto":(now+timedelta(days=fwd)).strftime("%m/%d/%Y"),"limit":config.limit_per_query}
    records=[]; status=[]
    with requests.Session() as session:
        for page in range(config.max_pages):
            params=dict(params_base); params["offset"]=page*config.limit_per_query
            try:r=session.get(API_BASE,params=params,timeout=(10,120))
            except requests.RequestException as e:status.append(f"BROAD RETRIEVAL | request error: {e}");break
            status.append(f"BROAD RETRIEVAL | page {page+1} | HTTP {r.status_code}")
            if r.status_code==429:
                status.append(f"THROTTLED | {r.text[:400]}");status.append("Search stopped immediately after SAM.gov reported quota exhaustion.");break
            if r.status_code!=200:status.append(f"SAM response: {r.text[:400]}");break
            batch=r.json().get("opportunitiesData",[]);records.extend(batch)
            if len(batch)<config.limit_per_query:break
        if not records:return pd.DataFrame(),status
        df=pd.DataFrame(records); raw=len(df)
        if "noticeId" not in df:return pd.DataFrame(),status+["SAM response did not include noticeId."]
        df=df.drop_duplicates("noticeId").copy();dedup=len(df)
        if "classificationCode" not in df:df["classificationCode"]=""
        psc=df["classificationCode"].fillna("").astype(str).str.upper().str.strip().str[:4];df["psc_match"]=psc.isin(selected_psc)
        if "type" not in df:df["type"]=""
        df["notice_code"]=df["type"].apply(_notice);df=df[df["notice_code"].isin(selected_notice)].copy();nnotice=len(df)
        df["responseDeadLine_dt"]=_deadline(df);nowts=pd.Timestamp.now(tz="UTC");maxdue=nowts+pd.Timedelta(days=fwd)
        df=df[df["responseDeadLine_dt"].notna()&(df["responseDeadLine_dt"]>=nowts)&(df["responseDeadLine_dt"]<=maxdue)].copy();ndead=len(df)
        if "title" not in df:df["title"]=""
        df["space_sniff"]=df["title"].fillna("").astype(str).str.lower().str.contains("space",regex=False);df["description_text"]=""
        df["local_keyword_hits"]=df.apply(lambda x:_hits(_text(x),SEARCH_TERMS),axis=1)
        df=df[df["psc_match"]|df["space_sniff"]|(df["local_keyword_hits"]>0)].copy();nrel=len(df)
        if config.suppress_stale_omnibus and not df.empty:
            pub=_utc(df["postedDate"]) if "postedDate" in df else pd.Series(pd.NaT,index=df.index,dtype="datetime64[ns, UTC]");mc=next((c for c in ("modifiedDate","updatedDate") if c in df),None)
            stale=(pub.notna()&_utc(df[mc]).notna()&(pub<nowts-pd.Timedelta(days=config.stale_published_days))&(_utc(df[mc])<nowts-pd.Timedelta(days=config.stale_modified_days))) if mc else pd.Series(False,index=df.index)
            df=df[~stale].copy()
        nstale=len(df)
        if df.empty:return df,status+[f"COUNTS | raw={raw} | dedup={dedup} | notice={nnotice} | deadline={ndead} | relevance={nrel} | final={nstale}"]
        df["preliminary_score"]=df.apply(lambda x:(25 if x["psc_match"] else 0)+min(30,_hits(_text(x),DIRECT_TERMS)*8)+min(24,_phits(_text(x),custom)*8),axis=1);df=df.sort_values("preliminary_score",ascending=False)
        fetched=0
        if "description" in df:
            for idx in df.head(min(config.description_fetch_limit,len(df))).index:
                body=_desc(session,api_key,df.at[idx,"description"])
                if body=="__THROTTLED__":status.append("DESCRIPTION ENRICHMENT | HTTP 429 | stopped immediately");break
                df.at[idx,"description_text"]=body;fetched+=1
        if "pointOfContact" in df:df["contact_info"]=df["pointOfContact"].apply(_contacts)
        else:df["contact_info"]=""
        if custom:
            df["custom_keyword_hits"]=df.apply(lambda x:_phits(_text(x),custom),axis=1)
            if keyword_mode=="strict":df=df[df["custom_keyword_hits"]>0].copy()
        scores=df.apply(lambda x:_score(x,custom),axis=1);df=pd.concat([df,scores],axis=1)
        df["sam_link"]=df["noticeId"].apply(lambda x:f"https://sam.gov/opp/{x}/view")
        df=df.sort_values(["cosmic_score","responseDeadLine_dt"],ascending=[False,True])
        status.append(f"COUNTS | raw={raw} | dedup={dedup} | notice={nnotice} | deadline={ndead} | relevance={nrel} | after-stale={nstale} | descriptions={fetched} | final={len(df)}")
        return df,status
