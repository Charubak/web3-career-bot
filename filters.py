import html
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

MAX_AGE_DAYS = 45

ROLE_PRESETS = {
    "marketing": [
        "marketing", "growth marketer", "growth manager", "growth lead",
        "growth director", "community", "content", "brand", "gtm",
        "go-to-market", "partnerships", "kol", "social media", "communications",
        " pr ", "public relations", "customer acquisition", "user acquisition",
        "ambassador", "influencer", "campaign", "narrative", "ecosystem",
        "devrel", "developer relations", "demand generation", "product marketing",
        "growth marketing",
    ],
    "engineering": [
        "engineer", "developer", "solidity", "smart contract",
        "blockchain developer", "backend", "frontend", "full stack", "fullstack",
        "rust developer", "typescript developer", "web3 developer",
        "protocol engineer", "infrastructure engineer", "dapp", "evm",
        "zkp", "zero knowledge", "cryptography engineer",
    ],
    "legal": [
        "legal", "compliance", "counsel", "regulatory", "policy",
        "general counsel", "legal officer", "chief legal",
    ],
    "design": [
        "designer", " design", "ux ", "ui/ux", "ui ", "product design",
        "visual design", "graphic designer", "creative director", "motion designer",
    ],
    "product": [
        "product manager", "product lead", "head of product", "product owner",
        "chief product", "vp product", "product director",
    ],
    "operations": [
        "operations manager", "ops manager", "chief of staff", "finance manager",
        "people operations", "head of operations", "treasury", "financial controller",
    ],
    "bd": [
        "business development", "bd manager", "bd lead", "head of bd",
        "account executive", "account manager", "revenue", "partner manager",
        "head of sales", "sales manager", "partnerships manager",
    ],
    "research": [
        "researcher", "research analyst", "protocol researcher",
        "security researcher", "economist", "quantitative researcher",
        "cryptographer", "defi researcher",
    ],
    "data": [
        "data analyst", "data scientist", "data engineer", "analytics engineer",
        "business intelligence", "data lead", "head of data", "quantitative analyst",
    ],
}

ROLE_EXCLUDE_PHRASES = {
    "marketing": [
        "frontend engineer", "backend engineer", "software engineer",
        "engineering manager", "engineering director", "data engineer",
        "principal engineer", "algorithm engineer", "content delivery",
        "content moderator", "content moderation", "human resources",
        "hr lead", "hr manager", "recruiting", "recruiter", "legal counsel",
        "risk manager", "financial analyst", "data analyst", "data scientist",
        "machine learning", "qa engineer", "qa lead", "security engineer",
        "security analyst", "network engineer", "site reliability", "devops",
    ],
    "engineering": [
        "marketing manager", "content manager", "community manager",
        "brand manager", "social media manager", "recruiting", "recruiter",
    ],
    "legal":      ["marketing", "engineering", "design", "recruiter"],
    "design":     ["marketing manager", "engineering", "recruiter"],
    "product":    ["marketing", "recruiter"],
    "operations": ["recruiter", "talent acquisition"],
    "bd":         ["marketing manager", "engineering", "recruiter"],
    "research":   ["recruiter", "marketing manager"],
    "data":       ["recruiter", "marketing manager", "community manager"],
}

_REMOTE_KEYWORDS = ["remote", "worldwide", "global", "anywhere", "distributed"]

US_RESTRICTED_PATTERNS = [
    "us only", "us citizen", "must be in us", "us work authorization",
    "remote - usa", "remote, usa", "remote - us", "remote, us",
    "us / remote", "remote (us)", "remote (usa)", "remote (united states)",
    "united states", "new york", "san francisco", "austin", "los angeles",
    "boston", "chicago", "seattle", "miami", "denver", "nyc", "bay area",
    "silicon valley", "remote - ny", "remote - ca", "california", "texas",
    "washington, d",
]

ONSITE_PATTERNS = ["on-site", "onsite", "in-office"]


def _get_roles() -> list:
    try:
        import prefs
        return prefs.load().get("roles", ["marketing"])
    except Exception:
        try:
            from config import JOB_ROLES
            return JOB_ROLES
        except Exception:
            return ["marketing"]


def _get_location_config() -> tuple:
    try:
        import prefs
        p = prefs.load()
        return p.get("location_type", "remote"), p.get("preferred_locations", [])
    except Exception:
        try:
            from config import LOCATION_TYPE, PREFERRED_LOCATIONS
            return LOCATION_TYPE, PREFERRED_LOCATIONS
        except Exception:
            return "remote", []


def _build_include_keywords(roles: list) -> list:
    kws = []
    for role in roles:
        kws.extend(ROLE_PRESETS.get(role, [role]))
    seen, result = set(), []
    for kw in kws:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def _build_exclude_phrases(roles: list) -> list:
    phrases = []
    for role in roles:
        phrases.extend(ROLE_EXCLUDE_PHRASES.get(role, []))
    seen, result = set(), []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _decode(text: str) -> str:
    return html.unescape(text or "")


def _parse_posted_date(posted: str) -> Optional[datetime]:
    if not posted:
        return None
    posted = str(posted).strip()
    if not posted or posted in ("None", "0", ""):
        return None
    if re.fullmatch(r"\d{13}", posted):
        try:
            return datetime.fromtimestamp(int(posted) / 1000, tz=timezone.utc)
        except Exception:
            pass
    if re.fullmatch(r"\d{10}", posted):
        try:
            return datetime.fromtimestamp(int(posted), tz=timezone.utc)
        except Exception:
            pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",   "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(posted[:19], fmt[:19])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    try:
        return parsedate_to_datetime(posted)
    except Exception:
        pass
    return None


def _is_too_old(job) -> bool:
    dt = _parse_posted_date(job.posted)
    if dt is None:
        return False
    return dt < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _is_location_allowed(job, location_type: str, preferred_locations: list) -> bool:
    if location_type == "any":
        return True
    loc = _decode(job.location or "").lower().strip()
    if not loc:
        return True
    if any(p in loc for p in ONSITE_PATTERNS):
        return False
    if location_type == "remote":
        if any(p in loc for p in US_RESTRICTED_PATTERNS):
            return False
        return any(kw in loc for kw in _REMOTE_KEYWORDS)
    if location_type == "specific":
        if any(kw in loc for kw in _REMOTE_KEYWORDS):
            return not any(p in loc for p in US_RESTRICTED_PATTERNS)
        return any(city in loc for city in preferred_locations)
    return False


def apply_filters(jobs: list) -> list:
    roles           = _get_roles()
    include_kw      = _build_include_keywords(roles)
    exclude_phrases = _build_exclude_phrases(roles)
    location_type, preferred_locations = _get_location_config()

    result = []
    for job in jobs:
        t = _decode(job.title).lower()
        if not any(kw in t for kw in include_kw):
            continue
        excluded = False
        for phrase in exclude_phrases:
            if phrase in t:
                if phrase == "product manager" and ("product marketing" in t or "growth marketing" in t):
                    continue
                excluded = True
                break
        if excluded:
            continue
        if not _is_location_allowed(job, location_type, preferred_locations):
            continue
        if _is_too_old(job):
            continue
        result.append(job)
    return result
