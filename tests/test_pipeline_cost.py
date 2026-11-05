import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from pipeline.cost import BudgetExceeded, Ledger, PriceUnknown, token_cost, usage_dict
from pipeline_fixtures import usage


class Prices(unittest.TestCase):
    def test_sonnet_rates_and_search_fee(self):
        self.assertEqual(token_cost('claude-sonnet-5-5', input=1_000_000), Decimal('2'))
        self.assertEqual(token_cost('claude-sonnet-5-5', output=10_000), Decimal('0.1'))
        self.assertEqual(token_cost('claude-sonnet-5-5', cache_read=1_000_000), Decimal('0.1'))
        self.assertEqual(token_cost('claude-sonnet-5-5', web_searches=4), Decimal('0.04'))

    def test_unknown_model_fails_closed(self):
        with self.assertRaises(PriceUnknown):
            token_cost('claude-unknown', input=1)

    def test_usage_normalisation(self):
        self.assertEqual(usage_dict(usage(10, 20, 3)), dict(input=10, output=20, cache_read=0, cache_write_5m=0,
                                                            cache_write_1h=0, web_searches=3))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'ledger.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_reserve_refuses_before_spending_past_the_cap(self):
        ledger = Ledger.open(self.path, 0.50)
        first = ledger.reserve('script', 'anthropic', 'm', 0.30)
        ledger.settle(first, 0.12)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve('images', 'openai', 'm', 0.39)
        self.assertEqual(len(ledger.entries), 1)  # nothing recorded for the refused step
        ledger.reserve('images', 'openai', 'm', 0.38)

    def test_failed_calls_stay_counted_unless_not_sent(self):
        ledger = Ledger.open(self.path, 2.0)
        a = ledger.reserve('research', 'anthropic', 'm', 0.25)
        ledger.fail(a, 'HTTP 529', billed=True)
        b = ledger.reserve('script', 'anthropic', 'm', 0.15)
        ledger.fail(b, 'HTTP 400', billed=False)
        self.assertEqual(ledger.spent(), Decimal('0.25'))

    def test_persists_and_budget_can_be_raised_explicitly(self):
        ledger = Ledger.open(self.path, 1.0)
        ledger.settle(ledger.reserve('script', 'anthropic', 'm', 0.2), 0.05)
        again = Ledger.open(self.path)
        self.assertEqual(again.spent(), Decimal('0.05'))
        self.assertEqual(again.budget, Decimal('1.0'))
        raised = Ledger.open(self.path, 3.0)
        self.assertEqual(json.loads(self.path.read_text())['budget_usd'], 3.0)
        self.assertEqual(raised.remaining(), Decimal('2.95'))


if __name__ == '__main__':
    unittest.main()
