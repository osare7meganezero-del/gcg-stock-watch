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
LAYOUT = json.loads((ROOT/'site'/'data'/'layout.json').read_text(encoding='utf-8'))

EXPECTED = [
    ('NTC25M1-GD01-067', 'GD01-067', 'ガンダム・エアリアル（改修型）', 'GD01-067_p3', '2025 MISSION1'),
    ('NTC25M2-GD02-069', 'GD02-069', 'Ζガンダム', 'GD02-069_p2', '2025 MISSION2'),
    ('NTC25M3-GD02-036', 'GD02-036', 'キュベレイ', 'GD02-036_p2', '2025 MISSION3'),
    ('NTC26M1-GD01-065', 'GD01-065', 'フリーダムガンダム', 'GD01-065_p2', '2026 MISSION1'),
    ('NTC26M2-ST07-005', 'ST07-005', 'ガンダムデュナメス', 'ST07-005_p2', '2026 MISSION2'),
    ('NTC26M3-GD03-050', 'GD03-050', 'ガンダム・バルバトスルプス', 'GD03-050_p3', '2026 MISSION3'),
    ('NTC26M4-GD04-066', 'GD04-066', 'ユニコーンガンダム（覚醒）', 'GD04-066_p2', '2026 MISSION4'),
]

class NtcWinnerTests(unittest.TestCase):
    def test_all_seven_winners_exist_with_official_variants(self):
        cards = [c for c in CARDS if c.get('group') == 'NTC優勝']
        self.assertEqual(len(cards), 7)
        by_id = {c['id']: c for c in cards}
        for cid, code, name, detail, mission in EXPECTED:
            self.assertIn(cid, by_id)
            c = by_id[cid]
            self.assertEqual(c['code'], code)
            self.assertEqual(c['name'], name)
            self.assertEqual(c['officialDetailSearch'], detail)
            self.assertEqual(c['eventMission'], mission)
            self.assertEqual(c['eventRole'], '優勝記念品')
            self.assertIn(detail, c['officialUrl'])

    def test_default_layout_places_ntc_between_lrpp_and_beta(self):
        ids = [x for x in LAYOUT if x]
        self.assertEqual(ids[:13], [c['id'] for c in CARDS if c.get('group') == 'LR++'])
        self.assertEqual(ids[13:20], [x[0] for x in EXPECTED])
        self.assertEqual(ids[20:32], [c['id'] for c in CARDS if c.get('group') == 'β版パラレル'])
        self.assertEqual(len(ids), 32)

    def test_ntc_winner_accepts_event_listing_and_rejects_normal_parallel(self):
        card = {
            'id':'NTC26M1-GD01-065', 'code':'GD01-065', 'name':'フリーダムガンダム',
            'group':'NTC優勝', 'rarity':'LR', 'targetSet':'NTC',
            'eventMission':'2026 MISSION1', 'eventRole':'優勝記念品'
        }
        winner = '''<article><a href="/view/item/winner">〖LR〗フリーダムガンダム（ニュータイプチャレンジ 2026 MISSION1優勝記念品）《GD01-065》</a><b>598,000円</b><span>在庫数:2</span><button>カートに入れる</button></article>'''
        hit = mod.parse_page_for_card(winner, 'https://shop.example/list', card, {'adapter':'catalog_exact'})
        self.assertTrue(hit.found)
        self.assertEqual(hit.availability, 'in_stock')
        self.assertEqual(hit.stock, 2)
        self.assertEqual(hit.price, 598000)

        normal = '''<article><a href="/view/item/normal">〖LR+〗フリーダムガンダム（パラレル）《GD01-065》</a><b>3,980円</b><span>在庫数:8</span><button>カートに入れる</button></article>'''
        hit = mod.parse_page_for_card(normal, 'https://shop.example/list', card, {'adapter':'catalog_exact'})
        self.assertFalse(hit.found)

    def test_ntc_scoped_category_can_identify_cardland_pr_row(self):
        card = {
            'id':'NTC25M3-GD02-036', 'code':'GD02-036', 'name':'キュベレイ',
            'group':'NTC優勝', 'rarity':'LR', 'targetSet':'NTC',
            'eventMission':'2025 MISSION3', 'eventRole':'優勝記念品'
        }
        shop = {'adapter':'cardland_exact', 'ntcScopedUrlContains':['/product-list/817']}
        html = '''<div class="item"><a href="/product/ntc-q">キュベレイ [PR]</a><b>49,980円(税込)</b><span>在庫数1枚</span><button>カートに入れる</button></div>'''
        hit = mod.parse_page_for_card(html, 'https://www.cardland-kamata.com/product-list/817', card, shop)
        self.assertTrue(hit.found)
        self.assertEqual(hit.stock, 1)
        self.assertEqual(hit.price, 49980)

    def test_zeta_official_name_can_match_ascii_z_shop_spelling(self):
        card = {
            'id':'NTC25M2-GD02-069', 'code':'GD02-069', 'name':'Ζガンダム', 'aliases':['Zガンダム'],
            'group':'NTC優勝', 'rarity':'LR', 'targetSet':'NTC',
            'eventMission':'2025 MISSION2', 'eventRole':'優勝記念品'
        }
        shop = {'adapter':'cardland_exact', 'ntcScopedUrlContains':['/product-list/817']}
        html = '<div><a href="/product/zeta">Zガンダム [PR]</a><b>34,980円</b><span>在庫数1枚</span></div>'
        hit = mod.parse_page_for_card(html, 'https://www.cardland-kamata.com/product-list/817', card, shop)
        self.assertTrue(hit.found)

    def test_audited_shops_include_ntc_scope(self):
        by_id = {s['id']: s for s in SHOPS}
        for sid in ('bigweb','hobbystation','mercard','tierone','cardland','gunhappy'):
            self.assertIn('NTC優勝', by_id[sid].get('supportedGroups', []), sid)
        self.assertIn('/view/category/gcgpnc', ' '.join(by_id['tierone']['urls']))
        self.assertIn('/product-group/37', ' '.join(by_id['mercard']['urls']))
        self.assertIn('/product-list/817', ' '.join(by_id['cardland']['urls']))
        self.assertIn('/view/category/promo', ' '.join(by_id['gunhappy']['urls']))

    def test_site_has_ntc_tab_and_footer_count(self):
        html = (ROOT/'site'/'index.html').read_text(encoding='utf-8')
        self.assertIn("['NTC優勝','NTC優勝 7種']", html)
        self.assertIn("NTC優勝：<b>${ntc}</b>", html)

if __name__ == '__main__':
    unittest.main()
