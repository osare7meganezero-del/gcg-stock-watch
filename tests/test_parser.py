import importlib.util
import json
import pathlib
import unittest
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scan_bigweb', ROOT/'scripts'/'scan_bigweb.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['scan_bigweb'] = mod
spec.loader.exec_module(mod)
CARDS = json.loads((ROOT/'config'/'cards.json').read_text(encoding='utf-8'))

class ParserTests(unittest.TestCase):
    def card(self, code):
        return next(c for c in CARDS if c['code']==code and (c['group']=='LR++' if code=='GD01-001' else True))

    def test_exact_lrpp_sold_out_ignores_regular_stock(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD01-001')
        html='''<html><body>
        <div class="item"><a href="/ja/products/gundamgcg/cardViewer/3420150">ガンダム</a><span>[GD01]</span><span>[LR++]</span><b>GD01-001 パラレル</b><span>138,000円</span><strong>売り切れ</strong></div>
        <div class="item"><span>ガンダム [GD01] [LR] GD01-001</span><span>180円</span><button>カートに追加</button><span>0 / 4</span></div>
        </body></html>'''
        r=mod.parse_card(html,'https://www.bigweb.co.jp/list',card)
        self.assertTrue(r.ok)
        self.assertFalse(r.in_stock)
        self.assertEqual(r.price,138000)

    def test_beta_exact_in_stock(self):
        card = next(c for c in CARDS if c['id']=='βbeta-GD01-070')
        html='''<html><body><section><a href="/ja/products/gundamgcg/cardViewer/999">ガンダム・エアリアル</a><span>[Ver.β]</span><span>パラレル [R+]</span><span>GD01-070 ベータ版</span><span>348,000円</span><span>0 / 1</span><button>カートに追加</button></section>
        <section><span>[GD01] [R+] GD01-070</span><span>2,980円</span><strong>売り切れ</strong></section></body></html>'''
        r=mod.parse_card(html,'https://www.bigweb.co.jp/list',card)
        self.assertTrue(r.ok)
        self.assertTrue(r.in_stock)
        self.assertEqual(r.stock,1)
        self.assertEqual(r.price,348000)

    def test_special_price_is_not_used(self):
        card = next(c for c in CARDS if c['id']=='LRpp-GD04-050')
        html='''<html><body>
        <div><span>デスティニーガンダム 特価 傷在り [GD04] [LR++] GD04-050</span><span>108,000円</span><button>カートに追加</button></div>
        <div><span>デスティニーガンダム [GD04] [LR++] GD04-050</span><span>148,000円</span><span>0 / 2</span><button>カートに追加</button></div>
        </body></html>'''
        r=mod.parse_card(html,'https://www.bigweb.co.jp/list',card)
        self.assertTrue(r.ok)
        self.assertTrue(r.in_stock)
        self.assertEqual(r.price,148000)
        self.assertEqual(r.stock,2)

if __name__=='__main__':
    unittest.main()
