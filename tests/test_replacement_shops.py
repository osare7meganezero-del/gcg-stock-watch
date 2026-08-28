import importlib.util
import pathlib
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scan',ROOT/'scripts'/'scan_shops.py')
scan=importlib.util.module_from_spec(spec); sys.modules['scan']=scan; spec.loader.exec_module(scan)

class ReplacementShopTests(unittest.TestCase):
    def test_cardland_lrpp(self):
        card={'name':'ウイングガンダムゼロ（EW版）','code':'GD05-067','targetSet':'GD05','rarity':'LR++','group':'LR++'}
        shop={'adapter':'cardland_exact'}
        self.assertTrue(scan.shop_specific_matches('ウイングガンダムゼロ（EW版） [GD05/LR++] 299,980円 在庫なし',card,shop))
        self.assertFalse(scan.shop_specific_matches('ウイングガンダムゼロ（EW版） [GD05/LR+] 在庫数2枚',card,shop))
    def test_cardland_beta_parallel_not_beta_lr(self):
        card={'name':'ウイングガンダム','code':'ST02-001','targetSet':'Ver.β','rarity':'LR+','group':'β版パラレル'}
        shop={'adapter':'cardland_exact'}
        self.assertTrue(scan.shop_specific_matches('ウイングガンダム [Ver.β/パラレル] 在庫なし',card,shop))
        self.assertFalse(scan.shop_specific_matches('ウイングガンダム [Ver.β/LR] 在庫数14枚',card,shop))
    def test_torecolo_beta(self):
        card={'name':'ウイングガンダム','code':'ST02-001','targetSet':'Ver.β','rarity':'LR+','group':'β版パラレル'}
        shop={'adapter':'torecolo_exact'}
        text='ＬＲ＋ ウイングガンダム シリーズ リミテッドBOX Ver.β 品番2 ST02 在庫 0点'
        self.assertTrue(scan.shop_specific_matches(text,card,shop))
    def test_torecolo_lrpp(self):
        card={'name':'デスティニーガンダム','code':'GD04-050','targetSet':'GD04','rarity':'LR++','group':'LR++'}
        shop={'adapter':'torecolo_exact'}
        self.assertTrue(scan.shop_specific_matches('ＬＲ＋＋ デスティニーガンダム 品番2 GD04 中古良品 在庫 1点',card,shop))

    def test_torecolo_good_condition_not_excluded_by_secondary_damaged_stock_label(self):
        shop={'adapter':'torecolo_exact'}
        text='ＬＲ＋＋ デスティニーガンダム 商品状態・中古良品 在庫 1点 キズあり在庫：有'
        self.assertFalse(scan.candidate_is_excluded(text, shop))

    def test_torecolo_explicit_damaged_product_is_excluded(self):
        shop={'adapter':'torecolo_exact'}
        text='★キズあり★ ＬＲ＋＋ デスティニーガンダム 商品状態・中古キズあり 在庫 1点'
        self.assertTrue(scan.candidate_is_excluded(text, shop))

if __name__=='__main__': unittest.main()
