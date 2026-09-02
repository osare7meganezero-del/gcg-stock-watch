#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
import unicodedata
import urllib.robotparser
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "config" / "cards.json"
SHOPS_PATH = ROOT / "config" / "shops.json"
MONITOR_PATH = ROOT / "config" / "monitor.json"
SOURCE_STATE_PATH = ROOT / "config" / "source_state.json"
PUBLIC_STATE_PATH = ROOT / "site" / "data" / "state.json"
PUBLIC_SHOPS_PATH = ROOT / "site" / "data" / "shops.json"
PUBLIC_CARDS_PATH = ROOT / "site" / "data" / "cards.json"

TIMEOUT = 20
TRANSITION_HOURS = 24
DEFAULT_FRESH_HOURS = 6
DEFAULT_MIN_DELAY = 4.0
MATCHER_VERSION = "v1.12-variant-strict-1"

repo = os.environ.get("GITHUB_REPOSITORY", "your-account/gcg-stock-watch")
UA = (
    f"GCG-Stock-Watch/1.12 (+https://github.com/{repo}; "
    "low-rate availability monitor; no purchase automation)"
)

POSITIVE_MARKERS = (
    "カートに入れる", "カートに追加", "購入する", "在庫あり", "add to cart", "available",
)
NEGATIVE_MARKERS = (
    "在庫なし", "在庫切れ", "売り切れ", "売切れ", "完売", "品切れ", "sold out", "在庫数×",
)
EXCLUDED_MARKERS = (
    "特価", "傷あり", "傷在り", "傷有", "傷在", "キズ", "訳あり", "状態b", "b品",
)
BETA_MARKERS = ("ver.β", "verβ", "β版", "ベータ版", "リミテッドbox", "limited box")
CARD_CODE_RE = re.compile(r"\b(?:GD|ST|EB|EXB)\d{2}-\d{3}\b", re.I)


@dataclass
class Hit:
    found: bool
    availability: str = "unknown"  # in_stock | out_of_stock | unknown
    stock: int = 0
    price: int | None = None
    url: str | None = None
    evidence: str | None = None
    reason: str | None = None


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(fallback)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", norm(text)).lower()


def iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def age_hours(value: str | None, now: datetime) -> float:
    dt = parse_dt(value)
    if not dt:
        return 999999.0
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def parse_price(text: str) -> int | None:
    vals = []
    t = norm(text)
    pats = [
        r"(?:¥|￥)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,7})",
        r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,7})\s*(?:円|JPY)",
    ]
    for pat in pats:
        for m in re.finditer(pat, t, re.I):
            raw = m.group(1).replace(",", "")
            try:
                value = int(raw)
            except ValueError:
                continue
            if 10 <= value <= 9_999_999:
                vals.append(value)
    vals = [v for v in vals if v >= 30]
    return max(vals) if vals else None


def parse_stock(text: str) -> int:
    t = norm(text)
    patterns = [
        r"在庫数\s*[:：]?\s*(\d+)\s*(?:枚|個|点)?",
        r"在庫\s*[:：]?\s*(\d+)\s*(?:枚|個|点)?",
        r"残りあと\s*(\d+)\s*(?:個|点|枚)?",
        r"/\s*(\d{1,3})(?:\D|$)",
    ]
    values = []
    for pat in patterns:
        for m in re.finditer(pat, t, re.I):
            try:
                values.append(int(m.group(1)))
            except ValueError:
                pass
    if values:
        return max(values)
    low = compact(t)
    if any(compact(x) in low for x in POSITIVE_MARKERS):
        return 1
    return 0


def rarity_matches(text: str, card: dict) -> bool:
    t = compact(text)
    rarity = compact(card.get("rarity", ""))
    if card.get("rarity") == "LR++":
        # Shops use LR++, 金縁, or LR + "パラレル++" for the same top parallel.
        return ("lr++" in t) or ("パラレル++" in t) or ("金縁" in t)
    return rarity in t


def beta_matches(text: str, card: dict) -> bool:
    if card.get("group") != "β版パラレル":
        return True
    t = compact(text)
    return any(compact(x) in t for x in BETA_MARKERS)


def set_matches(text: str, card: dict) -> bool:
    if card.get("group") == "β版パラレル":
        return beta_matches(text, card)
    target = compact(card.get("targetSet", ""))
    return not target or target in compact(text)


def is_excluded(text: str) -> bool:
    t = compact(text)
    return any(compact(x) in t for x in EXCLUDED_MARKERS)


def candidate_is_excluded(text: str, shop: dict) -> bool:
    """Reject special/damaged inventory without false-dropping a normal CBトレコロ block.

    CBトレコロ often shows a secondary "キズあり在庫" count inside the same normal
    product card.  That label alone must not make an otherwise "中古良品" product
    disappear from normal-stock monitoring.
    """
    adapter = shop.get("adapter", "")
    if adapter == "torecolo_exact":
        t = compact(text)
        # Explicit damaged-product labels are rejected.  A mere secondary
        # "キズあり在庫" field on a 中古良品 card is not enough.
        damaged_markers = (
            "★キズあり★", "商品状態・中古キズあり", "商品状態中古キズあり",
            "中古キズあり", "傷あり商品", "訳あり", "状態b", "b品",
        )
        if any(compact(x) in t for x in damaged_markers):
            return True
        # Keep the prior policy that explicitly special-price product variants
        # are not counted as ordinary inventory.
        if "数量限定特価" in norm(text) or "特価品" in norm(text):
            return True
        return False
    return is_excluded(text)


def candidate_scope_is_coherent(text: str, card: dict, shop: dict) -> bool:
    """Fail closed when an ancestor clearly contains more than one card product.

    The old parser could climb from one product row to a catalog/page container, then
    borrow rarity/set/stock/price text from sibling products.  That is especially
    dangerous for Ver.β cards because the normal and β variants share the same card
    name/code.  A candidate scope may therefore contain only the requested card code
    (or no visible full code for the two shop-specific adapters).
    """
    t = norm(text)
    codes = {m.group(0).upper() for m in CARD_CODE_RE.finditer(t)}
    target = str(card.get("code", "")).upper()
    if codes and (codes - {target}):
        return False
    # Extremely large scopes are page/catalog containers, not product blocks.
    if len(t) > 2200:
        return False
    return True


def _torecolo_good_condition_text(text: str) -> str:
    """Remove the secondary damaged-stock counter from a Torecolo good-condition row."""
    t = norm(text)
    # Torecolo may place `キズあり在庫：3点` beside `中古良品 在庫 0点`.
    # The damaged counter must never make the good-condition listing in stock.
    t = re.sub(
        r"キズあり在庫\s*[:：]?\s*(?:\d+\s*(?:点|枚|個)?|有|あり|○|×)?",
        " ", t, flags=re.I,
    )
    return norm(t)


def stock_text_for_shop(text: str, shop: dict) -> str:
    if shop.get("adapter") == "torecolo_exact" and "中古良品" in norm(text):
        return _torecolo_good_condition_text(text)
    return text


def stock_signal_for_shop(text: str, shop: dict) -> str:
    return stock_signal(stock_text_for_shop(text, shop), bool(shop.get("negativeWins")))


def parse_stock_for_shop(text: str, shop: dict) -> int:
    return parse_stock(stock_text_for_shop(text, shop))


def exact_card_matches(text: str, card: dict) -> bool:
    t = compact(text)
    if compact(card.get("code", "")) not in t:
        return False
    if not rarity_matches(text, card):
        return False
    if not set_matches(text, card):
        return False
    return True




def shop_specific_matches(text: str, card: dict, shop: dict) -> bool:
    """Strict alternatives for shops whose catalog omits the full card code in visible text."""
    adapter = shop.get("adapter", "")
    t = compact(text)
    name = compact(card.get("name", ""))
    if adapter == "cardland_exact":
        if not name or name not in t:
            return False
        if card.get("group") == "LR++":
            # Cardland uses [GD05/LR++] style; require target set and LR++ in the same product block.
            return compact(card.get("targetSet", "")) in t and "lr++" in t
        # Limited Box top parallel is labeled [Ver.β/パラレル], distinct from [Ver.β/LR].
        return any(compact(x) in t for x in ("ver.β", "verβ", "β版")) and "パラレル" in t
    if adapter == "torecolo_exact":
        if not name or name not in t:
            return False
        # Torecolo category blocks show 品番2 (series), rarity and product name, but
        # may omit the full card number.  For Ver.β cards the original series prefix
        # (ST02/GD01 etc.) is essential; name+rarity+Ver.β alone can select a different
        # card with the same display name.
        if card.get("group") == "LR++":
            return compact(card.get("targetSet", "")) in t and ("lr++" in t or "lr＋＋" in norm(text).lower())
        series_prefix = compact(str(card.get("code", "")).split("-", 1)[0])
        return bool(series_prefix and series_prefix in t) and beta_matches(text, card) and rarity_matches(text, card)
    return exact_card_matches(text, card)


def stock_signal(text: str, negative_wins: bool = False) -> str:
    t = compact(text)
    neg = any(compact(x) in t for x in NEGATIVE_MARKERS)
    pos = any(compact(x) in t for x in POSITIVE_MARKERS) or bool(re.search(r"在庫(?:数)?\s*[:：]?\s*[1-9]\d*", norm(text)))
    # Explicit numeric stock is strongest evidence.
    numeric = re.findall(r"在庫(?:数)?\s*[:：]?\s*(\d+)", norm(text))
    if numeric:
        try:
            n = max(int(x) for x in numeric)
            return "in_stock" if n > 0 else "out_of_stock"
        except ValueError:
            pass
    if "在庫数×" in norm(text):
        return "out_of_stock"
    # Some shops render SOLDOUT and a generic cart element in the same product block.
    # Contradictory evidence is held as unknown instead of producing a false in-stock alert.
    if neg and pos:
        return "out_of_stock" if negative_wins else "unknown"
    if neg:
        return "out_of_stock"
    if pos:
        return "in_stock"
    return "unknown"


def _safe_product_link(href: str | None, page_url: str) -> str | None:
    """Normalize a candidate href and reject links that should never be a buy/product link."""
    if not href:
        return None
    raw = href.strip()
    low = raw.lower()
    if not raw or raw.startswith("#") or low.startswith(("javascript:", "mailto:", "tel:")):
        return None
    absolute = urljoin(page_url, raw)
    parsed = urlparse(absolute)
    base = urlparse(page_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Product links should remain on the shop host (www/non-www differences are tolerated).
    host = parsed.netloc.lower().removeprefix("www.")
    base_host = base.netloc.lower().removeprefix("www.")
    if host != base_host:
        return None
    pathq = (parsed.path + ("?" + parsed.query if parsed.query else "")).lower()
    blocked = (
        "/cart", "/checkout", "/login", "/account", "/member", "/mypage",
        "/search", "javascript:", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg",
        "/buy/", "/damage", "-s/"
    )
    if any(x in pathq for x in blocked):
        return None
    return absolute


def _product_link_score(url: str, label: str, code: str, card_name: str = "") -> int:
    """Score detail links above category/list/navigation links."""
    p = urlparse(url)
    pathq = (p.path + ("?" + p.query if p.query else "")).lower()
    text = norm(label).lower()
    score = 0
    detail_markers = (
        "/products/detail/", "/product/detail/", "/products/detail?",
        "/product/", "/products/", "/view/item/", "/shop/g/g",
        "/cardviewer/", "/products/detail", "/item/", "product_id=", "pid="
    )
    list_markers = (
        "/product-list", "/products/list", "/collections/", "/view/category/",
        "/product-group/", "/sell/", "/category", "sort=", "page="
    )
    if any(x in pathq for x in detail_markers):
        score += 100
    if any(x in pathq for x in list_markers):
        score -= 70
    if code.lower() in pathq:
        score += 35
    if code.lower() in text:
        score += 25
    if card_name and compact(card_name) in compact(text):
        score += 20
    # Links with meaningful anchor text are safer than image-only/navigation links.
    if text:
        score += 5
    return score


def product_url_for(el, page_url: str, code: str, card_name: str = "") -> str:
    """Choose the most likely product-detail URL.

    v1.10 could return the first anchor in a matching DOM block, which occasionally
    produced category/image/navigation links. v1.11 ranks only safe same-shop links
    and strongly prefers product-detail URL shapes. If no trustworthy detail link
    exists, the known-good listing page is used instead of an unrelated anchor.
    """
    links = []
    try:
        links.extend(el.find_all("a", href=True))
        # If the matched element itself is an anchor, include it too.
        if getattr(el, "name", None) == "a" and el.get("href"):
            links.insert(0, el)
    except Exception:
        pass

    candidates = []
    seen = set()
    for a in links:
        url = _safe_product_link(a.get("href"), page_url)
        if not url or url in seen:
            continue
        seen.add(url)
        label = norm(a.get_text(" ", strip=True))
        score = _product_link_score(url, label, code, card_name)
        candidates.append((score, url))

    if candidates:
        candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
        best_score, best_url = candidates[0]
        # Require positive detail evidence. Otherwise keep the verified page URL.
        if best_score >= 30:
            return best_url
    return page_url


def parse_page_for_card(html: str, page_url: str, card: dict, shop: dict | None = None) -> Hit:
    shop = shop or {}
    soup = BeautifulSoup(html, "html.parser")
    code = card["code"]
    candidates = []
    special_adapter = shop.get("adapter") in ("cardland_exact", "torecolo_exact")
    needle = card.get("name", "") if special_adapter else code
    for node in soup.find_all(string=lambda s: isinstance(s, str) and needle in s):
        el = node.parent
        for depth in range(10):
            if el is None:
                break
            text = norm(el.get_text(" ", strip=True))
            if len(text) > 5000:
                break

            identity_present = (compact(card.get("name", "")) in compact(text)) if special_adapter else (compact(code) in compact(text))
            sig = stock_signal_for_shop(text, shop) if identity_present else "unknown"
            exact = shop_specific_matches(text, card, shop) if identity_present else False
            coherent = candidate_scope_is_coherent(text, card, shop) if identity_present else False

            if exact and coherent and sig != "unknown":
                # Only exclude the candidate itself; don't use special/damaged stock as normal inventory.
                excluded = candidate_is_excluded(text, shop)
                candidates.append((excluded, len(text), el, text, sig))
                break

            # Important fail-closed boundary: once the nearest card/product block has an
            # explicit stock signal but fails variant identity (e.g. normal GD01 card
            # instead of Limited BOX Ver.β), do not climb to a catalog ancestor and
            # borrow `Ver.β`, price or stock from siblings/page headings.
            if identity_present and sig != "unknown" and coherent and not exact:
                break

            el = el.parent
    if not candidates:
        # Fail closed by default.  Flattening an entire catalog into text was the
        # source of cross-product contamination (normal/β variants, sibling prices
        # and sibling stock counts).  A shop must explicitly opt in to the legacy
        # text fallback after its markup has been audited.
        if shop.get("allowTextFallback"):
            page_text = norm(soup.get_text(" ", strip=True))
            if shop_specific_matches(page_text, card, shop) and candidate_scope_is_coherent(page_text, card, shop):
                anchor = card.get("name", "") if special_adapter else code
                idx = page_text.find(anchor)
                if idx < 0:
                    idx = 0
                window = page_text[max(0, idx - 350): idx + 900]
                if (shop_specific_matches(window, card, shop)
                        and candidate_scope_is_coherent(window, card, shop)
                        and not candidate_is_excluded(window, shop)):
                    sig = stock_signal_for_shop(window, shop)
                    if sig != "unknown":
                        return Hit(True, sig, parse_stock_for_shop(window, shop) if sig == "in_stock" else 0,
                                   parse_price(window), page_url, window[:650])
        return Hit(False, reason="exact variant product block not found")

    # Normal-condition candidates always beat special/damaged candidates; smallest DOM block is safest.
    candidates.sort(key=lambda x: (x[0], x[1]))
    excluded, _, el, text, sig = candidates[0]
    if excluded:
        return Hit(False, reason="only special/damaged candidate found")
    url = product_url_for(el, page_url, code, card.get("name", ""))
    return Hit(True, sig, parse_stock_for_shop(text, shop) if sig == "in_stock" else 0, parse_price(text), url, text[:650])


def best_hit(current: Hit | None, hit: Hit) -> Hit:
    if not hit.found:
        return current or hit
    if current is None or not current.found:
        return hit
    # If the same normal card appears more than once, an explicit in-stock listing takes priority.
    if hit.availability == "in_stock" and current.availability != "in_stock":
        return hit
    if current.availability == "in_stock" and hit.availability != "in_stock":
        return current
    if hit.availability == current.availability == "in_stock":
        a = hit.price if hit.price is not None else 10**12
        b = current.price if current.price is not None else 10**12
        return hit if a < b else current
    return current


def robots_allowed(session: requests.Session, shop: dict) -> tuple[bool, str | None]:
    urls = shop.get("urls", [])
    if not urls:
        return False, "no catalog URL"
    first = urls[0]
    parsed = urlparse(first)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = session.get(robots_url, timeout=TIMEOUT, headers={"User-Agent": UA})
        if r.status_code == 404:
            return True, None
        if r.status_code in (403, 429):
            return False, f"robots HTTP {r.status_code}"
        if r.status_code >= 400:
            return False, f"robots HTTP {r.status_code}; safety stop"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(r.text.splitlines())
        blocked = [u for u in urls if not rp.can_fetch(UA, u)]
        if blocked:
            return False, "robots.txt blocks configured catalog URL"
        return True, None
    except Exception as e:
        return False, f"robots check failed: {type(e).__name__}"


def fetch_shop(session: requests.Session, shop: dict, cards: list[dict], min_delay: float) -> tuple[dict[str, Hit], dict]:
    supported = set(shop.get("supportedGroups") or [])
    eligible_cards = [c for c in cards if not supported or c.get("group") in supported]
    results = {c["id"]: Hit(False, reason=("not checked" if c in eligible_cards else "group not supported by verified adapter")) for c in cards}
    health = {
        "id": shop["id"], "name": shop["name"], "homeUrl": shop["homeUrl"],
        "enabled": bool(shop.get("enabled", True)), "robotsAllowed": None,
        "pagesRequested": 0, "pagesOk": 0, "exactCards": 0, "error": None,
    }
    if not shop.get("enabled", True):
        health["error"] = "disabled in config"
        return results, health

    allowed, reason = robots_allowed(session, shop)
    health["robotsAllowed"] = allowed
    if not allowed:
        health["error"] = reason
        return results, health

    last_req = 0.0
    for page_url in shop.get("urls", []):
        forbidden = [x for x in (shop.get("forbidUrlContains") or []) if x and x in page_url]
        if forbidden:
            health["error"] = f"unsafe/non-sales URL excluded: {forbidden[0]}"
            continue
        wait = min_delay - (time.monotonic() - last_req)
        if wait > 0:
            time.sleep(wait)
        try:
            health["pagesRequested"] += 1
            resp = session.get(page_url, timeout=TIMEOUT, allow_redirects=True)
            last_req = time.monotonic()
            if resp.status_code in (403, 429):
                health["error"] = f"HTTP {resp.status_code}; circuit breaker stopped this shop"
                break
            if resp.status_code >= 400:
                health["error"] = f"HTTP {resp.status_code} on one catalog page"
                continue
            html = resp.text
            if len(html) < 500:
                health["error"] = "HTML too short; held as unknown"
                continue
            health["pagesOk"] += 1
            # Only parse cards whose code appears in the document; avoids needless DOM work.
            raw = html
            for card in eligible_cards:
                if shop.get("adapter") not in ("cardland_exact", "torecolo_exact") and card["code"] not in raw:
                    continue
                hit = parse_page_for_card(html, resp.url or page_url, card, shop)
                results[card["id"]] = best_hit(results.get(card["id"]), hit)
        except Exception as e:
            last_req = time.monotonic()
            health["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            continue

    health["exactCards"] = sum(1 for h in results.values() if h.found and h.availability in ("in_stock", "out_of_stock"))
    return results, health


def transition_status(availability: str, transition_from: str | None, changed_at: str | None, now: datetime) -> str:
    age = age_hours(changed_at, now)
    if availability == "in_stock":
        return "blue" if transition_from != "in_stock" and age < TRANSITION_HOURS else "green"
    if availability == "out_of_stock":
        return "orange" if transition_from == "in_stock" and age < TRANSITION_HOURS else "gray"
    return "unknown"


def canonical_for_compare(state: dict) -> dict:
    # checked timestamps are intentionally not part of publication decisions; avoid 48 no-op commits/day.
    c = copy.deepcopy(state)
    c.pop("generatedAt", None)
    scan = c.get("scan") or {}
    scan.pop("lastAttemptAt", None)
    for h in scan.get("shops", []) or []:
        h.pop("checkedAt", None)
    for card in c.get("cards", []) or []:
        card.pop("checkedAt", None)
    return c


def main() -> int:
    now = datetime.now(timezone.utc)
    cards = read_json(CARDS_PATH, [])
    shops = read_json(SHOPS_PATH, [])
    monitor = read_json(MONITOR_PATH, {})
    prev_source = read_json(SOURCE_STATE_PATH, {"schema": 2, "cards": {}})
    prev_public = read_json(PUBLIC_STATE_PATH, {"schema": 2, "cards": []})
    # Matching rules changed materially in v1.12.  Legacy confirmations may point to
    # the normal/main-set variant while the requested card is Ver.β.  Keeping those
    # confirmations would preserve wrong price/stock/link data for hours, so a matcher
    # version change intentionally invalidates the old per-shop cache once.
    if prev_source.get("matcherVersion") != MATCHER_VERSION:
        prev_source = {"schema": 2, "matcherVersion": MATCHER_VERSION, "cards": {}}
    prev_source.setdefault("cards", {})

    # Keep public metadata synchronized.
    write_json(PUBLIC_CARDS_PATH, cards)
    write_json(PUBLIC_SHOPS_PATH, shops)

    interval = int(monitor.get("intervalMinutes", 30))
    min_delay = float(monitor.get("minDelaySeconds", DEFAULT_MIN_DELAY))
    fresh_hours = float(monitor.get("freshHours", DEFAULT_FRESH_HOURS))
    min_oos = int(monitor.get("minOutOfStockConfirmations", 2))

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.6",
        "Accept": "text/html,application/xhtml+xml",
        "Cache-Control": "no-cache",
    })

    all_hits: dict[str, dict[str, Hit]] = {}
    shop_health = []
    if monitor.get("enabled", True):
        for shop in shops:
            hits, health = fetch_shop(session, shop, cards, min_delay)
            health["checkedAt"] = iso(now)
            all_hits[shop["id"]] = hits
            shop_health.append(health)
    else:
        for shop in shops:
            all_hits[shop["id"]] = {c["id"]: Hit(False, reason="monitor disabled") for c in cards}
            shop_health.append({
                "id": shop["id"], "name": shop["name"], "homeUrl": shop["homeUrl"],
                "enabled": False, "robotsAllowed": None, "pagesRequested": 0,
                "pagesOk": 0, "exactCards": 0, "error": "monitor disabled", "checkedAt": iso(now),
            })

    shop_by_id = {s["id"]: s for s in shops}
    new_source = copy.deepcopy(prev_source)
    new_source["schema"] = 2
    new_source["matcherVersion"] = MATCHER_VERSION
    new_source.setdefault("cards", {})

    # Update per-shop confirmed states only on exact evidence. No evidence never becomes sold out.
    for card in cards:
        cid = card["id"]
        cs = new_source["cards"].setdefault(cid, {"shops": {}, "aggregate": {"availability": "unknown"}})
        cs.setdefault("shops", {})
        for sid, shop_hits in all_hits.items():
            hit = shop_hits.get(cid) or Hit(False)
            old = cs["shops"].get(sid, {"availability": "unknown"})
            if hit.found and hit.availability in ("in_stock", "out_of_stock"):
                nxt = copy.deepcopy(old)
                old_av = old.get("availability", "unknown")
                if old_av != hit.availability:
                    nxt["transition_from"] = old_av
                    nxt["changed_at"] = iso(now)
                elif not nxt.get("changed_at"):
                    nxt["transition_from"] = "unknown"
                    nxt["changed_at"] = iso(now)
                nxt.update({
                    "availability": hit.availability,
                    "stock": int(hit.stock or 0),
                    "price": hit.price,
                    "url": hit.url or shop_by_id[sid].get("homeUrl"),
                    "confirmed_at": iso(now),
                    "evidence": (hit.evidence or "")[:500],
                })
                cs["shops"][sid] = nxt

    public_cards = []
    for card in cards:
        cid = card["id"]
        cs = new_source["cards"].setdefault(cid, {"shops": {}, "aggregate": {"availability": "unknown"}})
        shop_states = cs.get("shops", {})

        inventories = []
        explicit_oos = []
        stale_instock = []
        for sid, ss in shop_states.items():
            shop = shop_by_id.get(sid)
            if not shop:
                continue
            fresh = age_hours(ss.get("confirmed_at"), now) <= fresh_hours
            if ss.get("availability") == "in_stock":
                if fresh:
                    inventories.append({
                        "id": sid,
                        "name": shop["name"],
                        "url": ss.get("url") or shop["homeUrl"],
                        "price": ss.get("price"),
                        "stock": max(1, int(ss.get("stock") or 1)),
                    })
                else:
                    stale_instock.append(sid)
            elif ss.get("availability") == "out_of_stock" and fresh:
                explicit_oos.append((sid, ss))

        inventories.sort(key=lambda x: (x["price"] is None, x["price"] or 10**12, x["name"]))
        old_agg = cs.get("aggregate", {"availability": "unknown"})
        old_av = old_agg.get("availability", "unknown")

        if inventories:
            new_av = "in_stock"
        elif len(explicit_oos) >= min_oos:
            new_av = "out_of_stock"
        else:
            new_av = "unknown"

        agg = copy.deepcopy(old_agg)
        if new_av != old_av:
            agg["transition_from"] = old_av
            agg["availability"] = new_av
            agg["changed_at"] = iso(now)
        elif not agg.get("changed_at"):
            agg["transition_from"] = "unknown"
            agg["availability"] = new_av
            agg["changed_at"] = iso(now)

        # Last sold-out page: prefer a shop that just explicitly became sold out.
        last_sold = agg.get("last_sold_url")
        if new_av == "out_of_stock":
            candidates = []
            for sid, ss in explicit_oos:
                candidates.append((age_hours(ss.get("changed_at"), now), ss.get("url")))
            candidates = [x for x in candidates if x[1]]
            if candidates:
                candidates.sort(key=lambda x: x[0])
                last_sold = candidates[0][1]
                agg["last_sold_url"] = last_sold

        cs["aggregate"] = agg
        status = transition_status(new_av, agg.get("transition_from"), agg.get("changed_at"), now)
        best = inventories[0] if inventories else None
        confidence = "exact" if new_av in ("in_stock", "out_of_stock") else ("stale" if stale_instock else "unknown")
        errors = []
        if new_av == "unknown":
            if stale_instock:
                errors.append("以前の在庫確認が古くなったため店舗数から除外しました")
            current_errors = [h for h in shop_health if h.get("error")]
            if current_errors:
                errors.append(f"{len(current_errors)}店舗で取得またはrobots確認を保留")
            errors.append("取得不能・商品未掲載を在庫なし扱いにはしていません")

        public_cards.append({
            "id": cid,
            "status": status,
            "availability": new_av,
            "price": best.get("price") if best else None,
            "shop": best.get("name") if best else "",
            "shopCount": len(inventories),
            "productUrl": best.get("url") if best else None,
            "lastProductUrl": last_sold,
            "inventories": inventories,
            "outOfStockConfirmations": len(explicit_oos),
            "scanConfidence": confidence,
            "scanError": " / ".join(errors) if errors else None,
            "checkedAt": iso(now) if confidence == "exact" else None,
        })

    new_source["updated_at"] = iso(now)

    public_state = {
        "schema": 2,
        "matcherVersion": MATCHER_VERSION,
        "generatedAt": prev_public.get("generatedAt"),
        "scan": {
            "configuredShops": len([s for s in shops if s.get("enabled", True)]),
            "shopsReached": sum(1 for h in shop_health if h.get("pagesOk", 0) > 0),
            "shopsWithExactHits": sum(1 for h in shop_health if h.get("exactCards", 0) > 0),
            "pagesRequested": sum(int(h.get("pagesRequested", 0)) for h in shop_health),
            "intervalMinutes": interval,
            "freshHours": fresh_hours,
            "lastAttemptAt": iso(now),
            "policy": f"sequential; >= {min_delay:.1f}s between requests per shop; robots check; 403/429 circuit breaker; no visitor-triggered scraping; no purchase automation",
            "shops": shop_health,
        },
        "cards": public_cards,
    }

    # Publish/commit only when semantically meaningful data changed. This avoids no-op Git commits.
    old_cmp = canonical_for_compare(prev_public)
    new_cmp = canonical_for_compare(public_state)
    if old_cmp != new_cmp:
        public_state["generatedAt"] = iso(now)
        write_json(PUBLIC_STATE_PATH, public_state)
        write_json(SOURCE_STATE_PATH, new_source)
        print("STATE_CHANGED=1")
    else:
        print("STATE_CHANGED=0")
    print(json.dumps({k: v for k, v in public_state["scan"].items() if k != "shops"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
