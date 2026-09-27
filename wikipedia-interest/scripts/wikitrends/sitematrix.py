"""Language codes -> Wikipedia projects (sitematrix), main pages (siteinfo).

Format (formatversion=2, checked live): sitematrix = {"count": N, "0": {"code", "name", "localname",
"site": [...]}, ..., "specials": [...]}. Every Wikipedia that users can read, `simple` included, is
in a language entry; `specials` holds test, private and other special wikis. The subdomain is taken
from the site URL, not from the entry code or dbname: Cantonese is entry `yue` with
https://zh-yue.wikipedia.org, and be-tarask still has dbname be_x_oldwiki.
"""
from __future__ import annotations

import difflib
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .errors import WikitrendsError

# Meta-wiki is outside the required domains (architecture §3); every WMF wiki serves sitematrix.
SITEMATRIX_API = "https://en.wikipedia.org/w/api.php"

# Codes users type that differ from Wikipedia subdomains (pywikibot family.code_aliases,
# wikipedia_family: yue -> zh-yue); tickets in the comments there.
ALIASES = {
    "nb": "no",
    "dk": "da",
    "jp": "ja",
    "gsw": "als",
    "lzh": "zh-classical",
    "rup": "roa-rup",
    "sgs": "bat-smg",
    "vro": "fiu-vro",
    "nan": "zh-min-nan",
    "minnan": "zh-min-nan",
    "yue": "zh-yue",
    "be-x-old": "be-tarask",
    "zh-cn": "zh",
    "zh-tw": "zh",
}


@dataclass(frozen=True)
class Site:
    code: str  # Wikipedia subdomain: "uk", "be-tarask", "simple"
    dbname: str  # Wikidata sitelink key: "ukwiki"
    name: str  # language name for suggestions

    @property
    def project(self) -> str:
        """AQS project."""
        return f"{self.code}.wikipedia"

    @property
    def api(self) -> str:
        return f"https://{self.code}.wikipedia.org/w/api.php"


def _subdomain(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host.split(".")[0]


def parse_sitematrix(data: Dict[str, Any]) -> Dict[str, Site]:
    """Open Wikipedias by subdomain."""
    sites: Dict[str, Site] = {}
    for key, value in data["sitematrix"].items():
        if key in ("count", "specials"):
            continue
        name = value.get("localname") or value.get("name") or value.get("code", "")
        for site in value.get("site", []):
            if site.get("code") != "wiki" or site.get("closed") or site.get("private") or site.get("fishbowl"):
                continue
            code = _subdomain(site["url"])
            sites[code] = Site(code, site["dbname"], name)
    return sites


class SiteMatrix:
    def __init__(self, sites: Dict[str, Site]) -> None:
        self.sites = sites

    @classmethod
    def load(cls, client: Any, cache: Any) -> "SiteMatrix":
        data = cache.get_meta("sitematrix", "wikipedia")
        if data is None:
            data = client.get_action(
                SITEMATRIX_API,
                {"action": "sitematrix", "smtype": "language|special", "smlangprop": "code|name|localname|site",
                 "smsiteprop": "url|dbname|code"},
            )
            cache.put_meta("sitematrix", "wikipedia", data)
        return cls(parse_sitematrix(data))

    def resolve(self, code: str) -> Site:
        wanted = code.strip().lower().replace("_", "-")
        wanted = ALIASES.get(wanted, wanted)
        site = self.sites.get(wanted)
        if site is not None:
            return site
        raise WikitrendsError(
            "unknown_lang",
            f"Unknown Wikipedia language code {code!r}",
            hint="Use one of the similar codes or ask the user",
            similar=self.similar(code),
        )

    def resolve_many(self, codes: Sequence[str]) -> List[Site]:
        return [self.resolve(c) for c in codes]

    def similar(self, code: str, limit: int = 5) -> List[str]:
        by_name = {s.name.lower(): s.code for s in self.sites.values()}
        codes = difflib.get_close_matches(code.lower(), list(self.sites), n=limit, cutoff=0.6)
        names = [by_name[n] for n in difflib.get_close_matches(code.lower(), list(by_name), n=limit, cutoff=0.6)]
        return list(dict.fromkeys(codes + names))[:limit]


def main_page(client: Any, cache: Any, site: Site) -> str:
    """Main page title (siteinfo), 1 request per language, cached with the metadata TTL."""
    title = cache.get_meta("mainpage", site.code)
    if title is None:
        data = client.get_action(site.api, {"action": "query", "meta": "siteinfo", "siprop": "general"})
        title = data["query"]["general"]["mainpage"]
        cache.put_meta("mainpage", site.code, title)
    return title
