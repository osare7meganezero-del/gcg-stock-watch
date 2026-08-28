import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class PackageTests(unittest.TestCase):
    def test_card_counts(self):
        cards=json.loads((ROOT/'config/cards.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cards),25)
        self.assertEqual(sum(c['group']=='LR++' for c in cards),13)
        self.assertEqual(sum(c['group']=='β版パラレル' for c in cards),12)

    def test_shop_counts(self):
        shops=json.loads((ROOT/'config/shops.json').read_text(encoding='utf-8'))
        self.assertEqual(len(shops),26)
        self.assertEqual(sum(bool(s.get('enabled')) for s in shops),15)
        names={s['name'] for s in shops}
        self.assertIn('カードランド', names)
        self.assertIn('カードボックス（CBトレコロ）', names)
        self.assertIn('ドラゴンスター', names)

    def test_public_site_is_static_json_client(self):
        html=(ROOT/'site/index.html').read_text(encoding='utf-8')
        self.assertNotIn('/api/cards', html)
        self.assertNotIn('/api/refresh', html)
        self.assertIn("fetch('./data/state.json?", html)
        self.assertIn('gcg-xserver-layout-v19', html)
        self.assertIn('shopCount', html)
        self.assertIn('inventories', html)

    def test_xserver_branch_workflow(self):
        wf=(ROOT/'.github/workflows/update-stock.yml').read_text(encoding='utf-8')
        self.assertIn('xserver-public', wf)
        self.assertIn("cp -a site/. /tmp/gcg-xserver-public/", wf)
        self.assertIn("cron: '7,22,37,52 * * * *'", wf)

if __name__=='__main__': unittest.main()
