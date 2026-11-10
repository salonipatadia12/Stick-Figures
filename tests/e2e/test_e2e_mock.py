"""test_e2e's fake-mode flows against tests/e2e/mock_webapp.py (a TEST DOUBLE of lane B's web app, written from the
contract), so the e2e test's own logic is exercised before the merge. After the merge test_e2e runs the same flows
against the real webapp/; if that fails while this passes, the difference is between the contract as read here and
the real app."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e2e as e2e  # noqa: E402


class MockApp:
    __unittest_skip__ = False                  # test_e2e skips when webapp/ is missing; the double needs no webapp/
    app, app_dir, runner, app_src = 'mock_webapp:app', e2e.h.HERE, None, e2e.h.HERE / 'mock_webapp.py'


@unittest.skipIf(e2e.REAL, 'E2E_REAL=1: running real mode only')
class MockFlowTest(MockApp, e2e.FakeFlowTest):
    pass


@unittest.skipIf(e2e.REAL, 'E2E_REAL=1: running real mode only')
class MockFailuresTest(MockApp, e2e.FakeFailuresTest):
    pass


@unittest.skipIf(e2e.REAL, 'E2E_REAL=1: running real mode only')
class MockLeaseTest(MockApp, e2e.LeaseTest):
    pass


if __name__ == '__main__':
    unittest.main()
