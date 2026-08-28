import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scan_shops', ROOT/'scripts'/'scan_shops.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['scan_shops'] = mod
spec.loader.exec_module(mod)
CARDS = json.loads((ROOT/'config'/'cards.json').read_text(encoding='utf-8'))
SHOPS = json.loads((ROOT/'config'/'shops.json').read_text(encoding='utf-8'))

class MultiParserTests(unittest.TestCase):
    def test_verified_shop_policy(self):
        enabled=[s for s in SHOPS if s.get('enabled')]
        self.assertEqual(len(enabled), 15)
        self.assertEqual(len({s['id'] for s in SHOPS}), len(SHOPS))
        self.assertTrue(all(s.get('supportedGroups') for s in enabled))
        # Accuracy beats a cosmetic 20-shop target: probation shops must stay disabled.
        self.assertTrue(all(not s.get('enabled') for s in SHOPS if s.get('monitorStatus')=='probation'))

    def test_lrpp_exact_stock(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD05-017')
        html='''<div class="item"><a href="/p/nu">νガンダム [GD05/LR++] GD05-017</a><b>248,000円</b><span>在庫数2枚</span><button>カートに入れる</button></div>
        <div><span>νガンダム [GD05/LR+] GD05-017</span><b>10,800円</b><span>在庫数8枚</span></div>'''
        r=mod.parse_page_for_card(html,'https://example.jp/list',card)
        self.assertTrue(r.found)
        self.assertEqual(r.availability,'in_stock')
        self.assertEqual(r.stock,2)
        self.assertEqual(r.price,248000)
        self.assertEqual(r.url,'https://example.jp/p/nu')

    def test_fullcomp_parallel_plus_plus(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD05-002')
        html='''<article><a href="/products/x">[GD05]ストライクフリーダムガンダム(パラレル++)〖LR〗GD05-002</a><span>¥448,000 ／税込</span><b>在庫切れ</b></article>'''
        r=mod.parse_page_for_card(html,'https://shop.example/list',card)
        self.assertTrue(r.found)
        self.assertEqual(r.availability,'out_of_stock')
        self.assertEqual(r.price,448000)

    def test_beta_requires_beta_marker(self):
        card = next(c for c in CARDS if c['id']=='βbeta-GD01-118')
        normal='''<div><span>溢れる慈愛 [GD01] [U+] GD01-118 パラレル</span><b>29,800円</b><span>在庫数1枚</span></div>'''
        r=mod.parse_page_for_card(normal,'https://example.jp/list',card)
        self.assertFalse(r.found)
        beta='''<div><a href="/beta118">溢れる慈愛 リミテッドBOX Ver.β [U+] GD01-118</a><b>129,800円</b><span>在庫数1枚</span></div>'''
        r=mod.parse_page_for_card(beta,'https://example.jp/list',card)
        self.assertTrue(r.found)
        self.assertEqual(r.availability,'in_stock')

    def test_special_stock_is_excluded(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD05-033')
        html='''<div><span>※プレイ用特価品※マスターガンダム〔金縁〕〖LR++〗GD05-033</span><b>98,000円</b><span>在庫数1枚</span></div>
        <div><a href="/normal">マスターガンダム〔金縁〕〖LR++〗GD05-033</a><b>138,000円</b><span>在庫なし</span></div>'''
        r=mod.parse_page_for_card(html,'https://example.jp/list',card)
        self.assertTrue(r.found)
        self.assertEqual(r.availability,'out_of_stock')
        self.assertEqual(r.price,138000)

    def test_negative_wins_for_shop_with_generic_cart_markup(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD05-067')
        html='''<article><span>ウイングガンダムゼロ（EW版） LR++ GD05-067</span><b>278,000円</b><strong>SOLD OUT</strong><button>カートに入れる</button></article>'''
        r=mod.parse_page_for_card(html,'https://shop.example/list',card,{'negativeWins':True})
        self.assertTrue(r.found)
        self.assertEqual(r.availability,'out_of_stock')

if __name__=='__main__':
    unittest.main()
