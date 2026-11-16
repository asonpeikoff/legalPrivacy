"""Content safety for generated noise: never search for, or visit, anything that
could embarrass or endanger the user if their traffic were ever read."""
from __future__ import annotations

import re

# Words that disqualify a query outright (adult, gambling, violence/self-harm, weapons,
# illegal drugs, hacking/crime, extremism). Matched on word boundaries.
_BAD_WORDS = r"""
porn\w* xxx nude\w* naked sex sexy sexual\w* erotic\w* escort\w* fetish\w* hentai nsfw onlyfans
camgirl\w* milf\w* nsfw casino\w* gambl\w* betting bookmaker\w* poker slots
suicid\w* self-harm kill\w* murder\w* assassin\w* bomb\w* explosive\w* weapon\w* firearm\w* gun\w* rifle\w*
terror\w* jihad\w* extremis\w* nazi\w* genocide rape\w* molest\w* abuse\w* trafficking
cocaine heroin meth\w* fentanyl lsd mdma ecstasy opioid\w* narcotic\w* overdos\w*
hack\w* crack\w* exploit\w* malware ransomware phishing ddos carding counterfeit\w* forgery
fake\s+id launder\w* darknet dark\s+web
""".split()
_BAD_RE = re.compile(r"\b(?:" + "|".join(_BAD_WORDS) + r")\b", re.I)

_BAD_DOMAIN_PARTS = ("porn", "xxx", "sex", "adult", "nude", "cam4", "xvideos", "xnxx", "xhamster",
                     "casino", "bet365", "gambl", "poker", "slots", "escort", "hentai", "onlyfans",
                     "torrent", "pirate", "warez", "crack")
_INFRA_PARTS = ("akamai", "cloudfront", "amazonaws", "googleapis", "gstatic", "doubleclick",
                "googlesyndication", "googleadservices", "googletagmanager", "google-analytics",
                "fbcdn", "facebook.net", "cdn", "edgekey", "edgesuite", "azure", "windows.net",
                "trafficmanager", "ytimg", "googleusercontent", "googlevideo", "tiktokcdn",
                "byteoversea", "root-servers", "gtld-servers", "ezviz", "hicloudcam", "icloud-content",
                "apple-dns", "aaplimg", "skype.com", "msedge.net", "office.net", "live.com",
                "microsoftonline", "mozilla.net", "sentry", "cloudflare", "fastly", "akadns", "nflxvideo")
_BAD_TLDS = (".gov", ".mil", ".xxx", ".adult", ".porn", ".sex", ".bet", ".casino", ".poker")


def safe_query(q: str) -> bool:
    q = q.strip()
    if not (3 <= len(q) <= 90) or "http" in q.lower() or "www." in q.lower():
        return False
    if not re.fullmatch(r"[A-Za-z0-9 '\-.,?&:/+()$%#!]+", q):
        return False
    return not _BAD_RE.search(q)


def safe_domain(domain: str, denylist: set[str] | None = None) -> bool:
    d = domain.lower().strip(".")
    if not d or d.endswith(_BAD_TLDS) or any(p in d for p in _BAD_DOMAIN_PARTS):
        return False
    if any(p in d for p in _INFRA_PARTS):
        return False
    if denylist:
        parts = d.split(".")
        for i in range(len(parts) - 1):
            if ".".join(parts[i:]) in denylist:
                return False
    return True
