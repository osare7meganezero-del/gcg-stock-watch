import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scan_variant', ROOT/'scripts'/'scan_shops.py')
scan = importlib.util.module_from_spec(spec)
sys.modules['scan_variant'] = scan
spec.loader.exec_module(scan)
CARDS = json.loads((ROOT/'config'/'cards.json').read_text(encoding='utf-8'))


class VariantIntegrityTests(unittest.TestCase):
    def test_beta_normal_variant_does_not_borrow_beta_marker_from_page_ancestor(self):
        card = next(c for c in CARDS if c['id'] == 'βbeta-GD01-118')
        # This is a normal/main-set parallel product.  The page also happens to contain
        # a Ver.β heading elsewhere.  Page-level text must never turn the normal card
        # into the requested Limited BOX Ver.β variant.
        html = '''
        <main>
          <h2>リミテッドBOX Ver.β 関連商品</h2>
          <section class="normal-products">
            <article class="item">
              <a href="/products/normal-118">溢れる慈愛 [GD01] [U+] GD01-118 パラレル</a>
              <span>29,800円</span><span>在庫数1枚</span><button>カートに入れる</button>
            </article>
          </section>
        </main>
        '''
        hit = scan.parse_page_for_card(html, 'https://shop.example/gd01', card, {'adapter':'catalog_exact'})
        self.assertFalse(hit.found)

    def test_beta_parallel_uses_its_own_price_stock_and_link_not_normal_sibling(self):
        card = next(c for c in CARDS if c['id'] == 'βbeta-GD01-118')
        html = '''
        <main>
          <article class="item">
            <a href="/products/normal-118">溢れる慈愛 [GD01] [U+] GD01-118 パラレル</a>
            <span>29,800円</span><span>在庫数7枚</span><button>カートに入れる</button>
          </article>
          <article class="item">
            <a href="/products/beta-118">溢れる慈愛 リミテッドBOX Ver.β [U+] GD01-118</a>
            <span>129,800円</span><span>在庫数1枚</span><button>カートに入れる</button>
          </article>
        </main>
        '''
        hit = scan.parse_page_for_card(html, 'https://shop.example/list', card, {'adapter':'catalog_exact'})
        self.assertTrue(hit.found)
        self.assertEqual(hit.availability, 'in_stock')
        self.assertEqual(hit.stock, 1)
        self.assertEqual(hit.price, 129800)
        self.assertEqual(hit.url, 'https://shop.example/products/beta-118')


    def test_tierone_beta_category_scope_can_prove_beta_variant_without_product_level_beta_label(self):
        card = next(c for c in CARDS if c['id'] == 'βbeta-GD01-026')
        shop = {
            'id':'tierone','adapter':'catalog_exact','negativeWins':True,
            'betaScopedUrlContains':['/view/category/gcglm','/view/category/gcglmgd','/view/category/gcglmst']
        }
        html = '''
        <main>
          <h2>リミテッドBOX Ver.β</h2>
          <article class="item">
            <a href="/view/item/000000010453">〖R+〗ザクⅡ（シャア・アズナブル機）（パラレル）《GD01-026》〖GCG〗</a>
            <span>￥148,000（税込）</span>
            <span>在庫数:1</span>
            <button>カートに入れる</button>
          </article>
          <article class="item">
            <a href="/view/item/normal">〖R〗ザクⅡ（シャア・アズナブル機）《GD01-026》〖GCG〗</a>
            <span>￥80（税込）</span><span>在庫数:18</span>
          </article>
        </main>
        '''
        hit = scan.parse_page_for_card(html, 'https://tier-one.jp/view/category/gcglm', card, shop)
        self.assertTrue(hit.found)
        self.assertEqual(hit.availability, 'in_stock')
        self.assertEqual(hit.stock, 1)
        self.assertEqual(hit.price, 148000)
        self.assertEqual(hit.url, 'https://tier-one.jp/view/item/000000010453')

    def test_torecolo_beta_requires_original_series_prefix(self):
        card = {'name':'ウイングガンダム','code':'ST02-001','targetSet':'Ver.β','rarity':'LR+','group':'β版パラレル'}
        shop = {'adapter':'torecolo_exact'}
        wrong = 'ＬＲ＋ ウイングガンダム シリーズ リミテッドBOX Ver.β 品番2 ST01 在庫 1点'
        self.assertFalse(scan.shop_specific_matches(wrong, card, shop))
        right = 'ＬＲ＋ ウイングガンダム シリーズ リミテッドBOX Ver.β 品番2 ST02 在庫 1点'
        self.assertTrue(scan.shop_specific_matches(right, card, shop))

    def test_torecolo_good_stock_does_not_count_damaged_inventory(self):
        card = {
            'name':'デスティニーガンダム','code':'GD04-050','targetSet':'GD04',
            'rarity':'LR++','group':'LR++'
        }
        shop = {'adapter':'torecolo_exact', 'negativeWins': True}
        html = '''
        <div class="product">
          <a href="/shop/g/gGOOD/">ＬＲ＋＋ デスティニーガンダム 品番2 GD04</a>
          <span>商品状態・中古良品</span><span>在庫 0点</span>
          <span>キズあり在庫：3点</span><span>120,000円</span>
        </div>
        '''
        hit = scan.parse_page_for_card(html, 'https://www.torecolo.jp/shop/c/c1035/', card, shop)
        self.assertTrue(hit.found)
        self.assertEqual(hit.availability, 'out_of_stock')
        self.assertEqual(hit.stock, 0)

    def test_broad_multi_product_ancestor_is_not_used_for_price_or_stock(self):
        card = next(c for c in CARDS if c['id'] == 'LRpp-GD05-017')
        # The target row has no stock signal.  Another product does.  A catalog-wide
        # ancestor must not be accepted as the target product block.
        html = '''
        <main>
          <article><span>νガンダム [GD05/LR++] GD05-017</span><span>248,000円</span></article>
          <article><span>サザビー [GD05/LR++] GD05-049</span><span>128,000円</span><span>在庫数9枚</span></article>
        </main>
        '''
        hit = scan.parse_page_for_card(html, 'https://shop.example/list', card, {'adapter':'catalog_exact'})
        self.assertFalse(hit.found)

    def test_matcher_upgrade_invalidates_old_confirmations(self):
        import tempfile
        from unittest.mock import patch
        from datetime import datetime, timezone

        card = next(c for c in CARDS if c['id'] == 'βbeta-GD01-118')
        shop = {
            'id':'example','name':'Example','homeUrl':'https://example.jp/',
            'urls':['https://example.jp/list'],'enabled':True,
            'adapter':'catalog_exact','supportedGroups':['β版パラレル']
        }
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            paths = {
                'CARDS_PATH': td/'cards.json', 'SHOPS_PATH': td/'shops.json',
                'MONITOR_PATH': td/'monitor.json', 'SOURCE_STATE_PATH': td/'source.json',
                'PUBLIC_STATE_PATH': td/'state.json', 'PUBLIC_SHOPS_PATH': td/'public_shops.json',
                'PUBLIC_CARDS_PATH': td/'public_cards.json',
            }
            paths['CARDS_PATH'].write_text(json.dumps([card], ensure_ascii=False), encoding='utf-8')
            paths['SHOPS_PATH'].write_text(json.dumps([shop], ensure_ascii=False), encoding='utf-8')
            paths['MONITOR_PATH'].write_text(json.dumps({'enabled':True,'freshHours':6,'minOutOfStockConfirmations':1}), encoding='utf-8')
            now = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            legacy = {
                'schema':2, 'matcherVersion':'v1.11',
                'cards': {card['id']: {'shops': {'example': {
                    'availability':'in_stock','stock':9,'price':29800,
                    'url':'https://example.jp/wrong-normal','confirmed_at':now,
                    'changed_at':now,'transition_from':'unknown'
                }}, 'aggregate': {'availability':'in_stock','changed_at':now,'transition_from':'unknown'}}}
            }
            paths['SOURCE_STATE_PATH'].write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
            paths['PUBLIC_STATE_PATH'].write_text(json.dumps({'schema':2,'cards':[]}), encoding='utf-8')

            old_values = {name:getattr(scan,name) for name in paths}
            try:
                for name, value in paths.items():
                    setattr(scan, name, value)
                no_hit = {card['id']: scan.Hit(False, reason='no exact variant')}
                health = {'id':'example','name':'Example','homeUrl':'https://example.jp/','enabled':True,
                          'robotsAllowed':True,'pagesRequested':1,'pagesOk':1,'exactCards':0,'error':None}
                with patch.object(scan, 'fetch_shop', return_value=(no_hit, health)):
                    scan.main()
                published = json.loads(paths['PUBLIC_STATE_PATH'].read_text(encoding='utf-8'))
                self.assertEqual(published['cards'][0]['availability'], 'unknown')
                source = json.loads(paths['SOURCE_STATE_PATH'].read_text(encoding='utf-8'))
                self.assertNotEqual(source.get('matcherVersion'), 'v1.11')
            finally:
                for name, value in old_values.items():
                    setattr(scan, name, value)


if __name__ == '__main__':
    unittest.main()
