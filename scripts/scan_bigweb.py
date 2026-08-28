#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, sys, time, urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / 'config' / 'cards.json'
SOURCE_STATE_PATH = ROOT / 'config' / 'source_state.json'
MONITOR_PATH = ROOT / 'config' / 'monitor.json'
PUBLIC_STATE_PATH = ROOT / 'site' / 'data' / 'state.json'

SHOP = 'BIGWEB'
BASE = 'https://www.bigweb.co.jp'
MIN_DELAY = 4.0  # one request at a time; intentionally gentle
TIMEOUT = 18
TRANSITION_HOURS = 24

# Six list pages + one extra page for GD03. Visitors never request these pages.
SOURCE_PAGES = {
    'GD01': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?cardsets=8489&is_box=0',
    'GD02': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?recommend_id=1135',
    'GD03-017': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?is_box=0&is_purchase=0&is_supply=0&name=GD03-017',
    'GD03-035': 'https://www.bigweb.co.jp/ja/products/gundamgcg/cardViewer/3518663',
    'GD04': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?cardsets=8789&is_box=0',
    'GD05': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?cardsets=8906&is_box=0',
    'Ver.β': 'https://www.bigweb.co.jp/ja/products/gundamgcg/list?recommend_id=877',
}

repo = os.environ.get('GITHUB_REPOSITORY', 'your-account/gcg-stock-watch')
UA = f'GCG-Stock-Watch/1.4 (+https://github.com/{repo}; low-rate stock availability monitor; no purchase automation)'

@dataclass
class ScanResult:
    ok: bool
    in_stock: bool | None = None
    stock: int = 0
    price: int | None = None
    product_url: str | None = None
    evidence: str | None = None
    error: str | None = None


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def compact(text: str) -> str:
    return re.sub(r'\s+', '', text or '')


def yen(text: str) -> int | None:
    vals = []
    for m in re.finditer(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{3,7})\s*円', text or ''):
        try:
            vals.append(int(m.group(1).replace(',', '')))
        except ValueError:
            pass
    return vals[-1] if vals else None


def stock_count(text: str) -> int:
    # BIGWEB commonly renders quantity as 0 / 1, 0 / 4, etc.
    nums = [int(x) for x in re.findall(r'/\s*(\d{1,2})(?:\D|$)', text or '')]
    if nums:
        return max(nums)
    # If a cart button exists but quantity could not be parsed, count at least one.
    return 1 if 'カートに追加' in text else 0


def target_marker(card: dict) -> str:
    return 'Ver.β' if card.get('group') == 'β版パラレル' else card.get('targetSet', '')


def card_text_matches(text: str, card: dict) -> bool:
    c = compact(text)
    if compact(card['code']) not in c:
        return False
    if compact(card['rarity']) not in c:
        return False
    marker = compact(target_marker(card))
    if marker and marker not in c:
        return False
    if card.get('group') == 'β版パラレル' and 'ベータ版' not in c:
        return False
    return True


def find_smallest_candidate(soup: BeautifulSoup, card: dict):
    candidates = []
    code = card['code']
    for node in soup.find_all(string=lambda s: isinstance(s, str) and code in s):
        el = node.parent
        depth = 0
        while el is not None and depth < 9:
            text = norm(el.get_text(' ', strip=True))
            # Smallest useful card block: exact identifiers plus a stock signal.
            if card_text_matches(text, card) and ('売り切れ' in text or 'カートに追加' in text):
                if '特価' not in text and '傷在' not in text and '傷あり' not in text and '傷有' not in text:
                    candidates.append((len(text), el, text))
                    break
            if len(text) > 1800:
                break
            el = el.parent
            depth += 1
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1], candidates[0][2]


def parse_card(html: str, page_url: str, card: dict) -> ScanResult:
    soup = BeautifulSoup(html, 'html.parser')
    hit = find_smallest_candidate(soup, card)
    if hit:
        el, text = hit
        sold = '売り切れ' in text and 'カートに追加' not in text
        href = None
        a = el.find('a', href=re.compile(r'/cardViewer/'))
        if a and a.get('href'):
            href = urljoin(BASE, a['href'])
        if not href:
            p = el.parent
            for _ in range(3):
                if not p:
                    break
                a = p.find('a', href=re.compile(r'/cardViewer/'))
                if a and a.get('href'):
                    href = urljoin(BASE, a['href'])
                    break
                p = p.parent
        return ScanResult(
            ok=True,
            in_stock=not sold,
            stock=0 if sold else stock_count(text),
            price=yen(text),
            product_url=href or card.get('bigwebSearchUrl') or page_url,
            evidence=text[:550],
        )

    # cardViewer pages expose an availability table with the same set/rarity identifiers.
    page_text = norm(soup.get_text(' ', strip=True))
    if card_text_matches(page_text, card):
        code_pos = page_text.find(card['code'])
        rarity_pos = page_text.find(card['rarity'])
        pos = max(0, min([p for p in (code_pos, rarity_pos) if p >= 0] or [0]))
        window = page_text[max(0, pos - 500): pos + 1100]
        if card_text_matches(window, card):
            if '特価' not in window and '傷在' not in window:
                sold = '売り切れ' in window and 'カートに追加' not in window
                return ScanResult(
                    ok=True,
                    in_stock=not sold,
                    stock=0 if sold else stock_count(window),
                    price=yen(window),
                    product_url=page_url if '/cardViewer/' in page_url else card.get('bigwebSearchUrl'),
                    evidence=window[:550],
                )
    return ScanResult(ok=False, error='対象カードの在庫欄を厳密に特定できませんでした')


def source_key(card: dict) -> str:
    if card.get('group') == 'β版パラレル':
        return 'Ver.β'
    if card.get('targetSet') == 'GD03':
        return card['code']
    return card.get('targetSet', '')


def check_robots(session: requests.Session, urls: list[str]) -> tuple[bool, str | None]:
    robots_url = BASE + '/robots.txt'
    try:
        r = session.get(robots_url, headers={'User-Agent': UA}, timeout=TIMEOUT)
        if r.status_code == 404:
            return True, None
        if r.status_code >= 400:
            return False, f'robots.txt HTTP {r.status_code}: 安全側に倒して監視を停止しました'
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(r.text.splitlines())
        blocked = [u for u in urls if not rp.can_fetch(UA, u)]
        if blocked:
            return False, 'robots.txt で対象URLへのアクセスが許可されていません'
        return True, None
    except Exception as e:
        return False, f'robots.txt を確認できませんでした。安全側に倒して監視を停止しました: {type(e).__name__}'


def derive_status(src: dict, availability: str, now: datetime) -> str:
    if availability not in ('in_stock', 'out_of_stock'):
        return 'unknown'
    changed_at = src.get('changed_at')
    previous = src.get('transition_from')
    age_h = 9999.0
    if changed_at:
        try:
            dt = datetime.fromisoformat(changed_at.replace('Z', '+00:00'))
            age_h = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600
        except Exception:
            pass
    if availability == 'in_stock':
        return 'blue' if previous == 'out_of_stock' and age_h < TRANSITION_HOURS else 'green'
    return 'orange' if previous == 'in_stock' and age_h < TRANSITION_HOURS else 'gray'


def main() -> int:
    now = datetime.now(timezone.utc)
    cards = read_json(CARDS_PATH, [])
    monitor = read_json(MONITOR_PATH, {'enabled': True, 'intervalMinutes': 15})
    source_state = read_json(SOURCE_STATE_PATH, {'cards': {}})
    source_state.setdefault('cards', {})

    session = requests.Session()
    session.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.6',
        'Accept': 'text/html,application/xhtml+xml',
        'Cache-Control': 'no-cache',
    })

    urls = list(dict.fromkeys(SOURCE_PAGES.values()))
    robots_ok, robots_note = check_robots(session, urls)

    page_html: dict[str, str] = {}
    page_errors: dict[str, str] = {}
    pages_requested = 0
    circuit_breaker = None
    last_request_at = 0.0

    if not monitor.get('enabled', True):
        robots_ok = False
        robots_note = 'config/monitor.json で監視停止中です'

    if robots_ok:
        time.sleep(MIN_DELAY)
        for key, url in SOURCE_PAGES.items():
            if circuit_breaker:
                page_errors[key] = circuit_breaker
                continue
            wait = MIN_DELAY - (time.monotonic() - last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                pages_requested += 1
                resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
                last_request_at = time.monotonic()
                if resp.status_code in (403, 429):
                    circuit_breaker = f'BIGWEB HTTP {resp.status_code}: 迷惑をかけないため今回の残り取得を中止しました'
                    page_errors[key] = circuit_breaker
                    continue
                if resp.status_code >= 400:
                    page_errors[key] = f'HTTP {resp.status_code}'
                    continue
                if len(resp.text) < 700:
                    page_errors[key] = 'HTMLが短すぎるため判定を保留しました'
                    continue
                page_html[key] = resp.text
            except Exception as e:
                last_request_at = time.monotonic()
                page_errors[key] = f'{type(e).__name__}: {str(e)[:160]}'
    else:
        circuit_breaker = robots_note
        for key in SOURCE_PAGES:
            page_errors[key] = robots_note or 'robots.txt blocked'

    public_cards = []
    state_changed = False
    exact_count = 0
    error_count = 0

    for card in cards:
        cid = card['id']
        prev = source_state['cards'].get(cid, {
            'availability': 'unknown',
            'changed_at': None,
            'transition_from': 'unknown',
            'product_url': card.get('bigwebSearchUrl'),
        })
        key = source_key(card)
        result = None
        if key in page_html:
            result = parse_card(page_html[key], SOURCE_PAGES[key], card)
        else:
            result = ScanResult(ok=False, error=page_errors.get(key, '取得ページなし'))

        if result.ok and result.in_stock is not None:
            exact_count += 1
            new_av = 'in_stock' if result.in_stock else 'out_of_stock'
            old_av = prev.get('availability', 'unknown')
            next_src = dict(prev)
            if old_av != new_av:
                next_src['transition_from'] = old_av
                next_src['availability'] = new_av
                next_src['changed_at'] = now.isoformat().replace('+00:00', 'Z')
                state_changed = True
            elif old_av == 'unknown':
                next_src['transition_from'] = 'unknown'
                next_src['availability'] = new_av
                next_src['changed_at'] = now.isoformat().replace('+00:00', 'Z')
                state_changed = True
            if result.product_url and result.product_url != next_src.get('product_url'):
                next_src['product_url'] = result.product_url
                state_changed = True
            source_state['cards'][cid] = next_src
            status = derive_status(next_src, new_av, now)
            inv = []
            if new_av == 'in_stock':
                inv.append({
                    'name': SHOP,
                    'url': result.product_url or card.get('bigwebSearchUrl'),
                    'price': result.price,
                    'stock': max(1, result.stock),
                })
            public_cards.append({
                'id': cid,
                'status': status,
                'availability': new_av,
                'price': result.price,
                'shop': SHOP if new_av == 'in_stock' else '',
                'shopCount': 1 if new_av == 'in_stock' else 0,
                'productUrl': result.product_url,
                'lastProductUrl': result.product_url or next_src.get('product_url'),
                'inventories': inv,
                'scanConfidence': 'exact',
                'scanError': None,
                'checkedAt': now.isoformat().replace('+00:00', 'Z'),
            })
        else:
            error_count += 1
            availability = prev.get('availability', 'unknown')
            status = derive_status(prev, availability, now) if availability != 'unknown' else 'unknown'
            public_cards.append({
                'id': cid,
                'status': status,
                'availability': availability,
                'price': None,
                'shop': SHOP if availability == 'in_stock' else '',
                'shopCount': 1 if availability == 'in_stock' else 0,
                'productUrl': prev.get('product_url') or card.get('bigwebSearchUrl'),
                'lastProductUrl': prev.get('product_url') or card.get('bigwebSearchUrl'),
                'inventories': [],
                'scanConfidence': 'stale' if availability != 'unknown' else 'unknown',
                'scanError': result.error or '判定保留',
                'checkedAt': None,
            })

    source_state['schema'] = 1
    if state_changed:
        source_state['updated_at'] = now.isoformat().replace('+00:00', 'Z')
    write_json(SOURCE_STATE_PATH, source_state)

    public_state = {
        'schema': 1,
        'generatedAt': now.isoformat().replace('+00:00', 'Z'),
        'scan': {
            'shop': SHOP,
            'pagesRequested': pages_requested,
            'exactCards': exact_count,
            'heldCards': error_count,
            'intervalMinutes': int(monitor.get('intervalMinutes', 15)),
            'robotsNote': robots_note,
            'circuitBreaker': circuit_breaker,
            'policy': 'single-threaded, 4s minimum delay, no visitor-triggered scraping, no purchase automation',
        },
        'cards': public_cards,
    }
    write_json(PUBLIC_STATE_PATH, public_state)
    print(json.dumps(public_state['scan'], ensure_ascii=False))
    return 0

if __name__ == '__main__':
    sys.exit(main())
