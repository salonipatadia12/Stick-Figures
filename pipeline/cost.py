"""One cost ledger per episode, with a hard per-video cap.

Every paid call goes through `Ledger.reserve()` with a worst-case estimate *before* the
request is sent; if the cap would be exceeded nothing is sent. Afterwards `settle()` records
the actual cost computed from the provider's reported usage. Calls that fail after being
sent keep their estimate as "possibly billed", so the cap stays conservative. There are no
automatic paid retries anywhere in the pipeline.

Free local calls (Ollama) and keyless searches (DuckDuckGo) are recorded too, at $0.00 with their provider, through
`record_free()`: the ledger shows every model call and search a video made, and the cap logic is unchanged.
"""
from __future__ import annotations

import json
import os
import threading
import time
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

PRICES_VERIFIED = '2026-11-04'
# USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing.
TOKEN_PRICES = {
    'claude-sonnet-5-5': dict(input='2.00', output='10.00', cache_write_5m='2.50', cache_write_1h='4.00', cache_read='0.10'),
}
WEB_SEARCH_USD = Decimal('0.01')  # $10 per 1,000 searches
FREE_PROVIDERS = ('ollama', 'duckduckgo')  # local model and keyless search: logged at $0.00
MILLION = Decimal(1_000_000)


class BudgetExceeded(RuntimeError):
    pass


class PriceUnknown(RuntimeError):
    pass


def _get(obj, name, default=0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default) or default
    return getattr(obj, name, default) or default


def usage_dict(usage):
    """Normalise an SDK Usage object (or dict) into plain token counts."""
    creation = _get(usage, 'cache_creation', None)
    write_5m = _get(creation, 'ephemeral_5m_input_tokens')
    write_1h = _get(creation, 'ephemeral_1h_input_tokens')
    if not (write_5m or write_1h):
        write_5m = _get(usage, 'cache_creation_input_tokens')
    return dict(input=int(_get(usage, 'input_tokens')), output=int(_get(usage, 'output_tokens')),
                cache_read=int(_get(usage, 'cache_read_input_tokens')), cache_write_5m=int(write_5m),
                cache_write_1h=int(write_1h),
                web_searches=int(_get(_get(usage, 'server_tool_use', None), 'web_search_requests')))


def token_cost(model, input=0, output=0, cache_read=0, cache_write_5m=0, cache_write_1h=0, web_searches=0):
    if model not in TOKEN_PRICES:
        raise PriceUnknown(f'No verified price for {model}. Add it to pipeline/cost.py before using it.')
    p = {k: Decimal(v) for k, v in TOKEN_PRICES[model].items()}
    total = (input * p['input'] + output * p['output'] + cache_read * p['cache_read']
             + cache_write_5m * p['cache_write_5m'] + cache_write_1h * p['cache_write_1h']) / MILLION
    return total + web_searches * WEB_SEARCH_USD


def usd(value):
    """Round up to a millionth of a dollar (floats go through str to avoid binary noise)."""
    value = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    return float(value.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))


class Ledger:
    def __init__(self, path, budget_usd, entries=None):
        self.path = Path(path)
        self.budget = Decimal(str(budget_usd))
        self.entries = entries or []
        self._lock = threading.RLock()     # parallel model calls (per-beat details, fact extraction) share one ledger

    @classmethod
    def open(cls, path, budget_usd=None, default_budget=2.00):
        path = Path(path)
        if path.exists():
            data = json.loads(path.read_text())
            ledger = cls(path, data['budget_usd'], data['entries'])
            if budget_usd is not None and Decimal(str(budget_usd)) != ledger.budget:
                ledger.budget = Decimal(str(budget_usd))
                ledger.save()
            return ledger
        ledger = cls(path, default_budget if budget_usd is None else budget_usd)
        ledger.save()
        return ledger

    def spent(self):
        total = Decimal(0)
        for e in self.entries:
            if e['status'] == 'ok':
                total += Decimal(str(e['actual_usd']))
            elif e['status'] in ('reserved', 'possibly_billed'):
                total += Decimal(str(e['estimate_usd']))
        return total

    def remaining(self):
        return self.budget - self.spent()

    def check(self, estimate_usd):
        """Raise BudgetExceeded if spending `estimate_usd` more would pass the cap."""
        estimate = Decimal(str(estimate_usd))
        if self.spent() + estimate > self.budget:
            raise BudgetExceeded(
                f'Next step needs up to ${usd(estimate):.4f}; ${usd(self.spent()):.4f} of the '
                f'${usd(self.budget):.2f} budget is already used. Nothing was sent. '
                f'Raise the cap with --budget if you want to continue.')

    def reserve(self, step, provider, model, estimate_usd, detail=''):
        with self._lock:
            self.check(estimate_usd)
            entry = dict(id=len(self.entries) + 1, step=step, provider=provider, model=model,
                         estimate_usd=usd(estimate_usd), actual_usd=None, status='reserved', detail=detail,
                         usage=None, at=time.strftime('%Y-%m-%dT%H:%M:%S'))
            self.entries.append(entry)
            self.save()
            return entry['id']

    def settle(self, entry_id, actual_usd, usage=None, note=''):
        with self._lock:
            entry = self._entry(entry_id)
            entry.update(actual_usd=usd(actual_usd), status='ok', usage=usage)
            if note:
                entry['detail'] = (entry['detail'] + ' | ' + note).strip(' |')
            self.save()

    def fail(self, entry_id, note, billed=True):
        """Mark a failed call. If it may have been billed, keep its estimate counted."""
        with self._lock:
            entry = self._entry(entry_id)
            entry.update(status='possibly_billed' if billed else 'not_sent', detail=(entry['detail'] + ' | ' + note).strip(' |'))
            self.save()

    def record_free(self, step, provider, model, detail='', usage=None):
        """One free call (a local Ollama model, a keyless DuckDuckGo search): recorded at $0.00 with its provider."""
        if provider not in FREE_PROVIDERS:
            raise PriceUnknown(f'{provider} is not a free provider; paid calls go through reserve() and settle().')
        with self._lock:
            entry = self.reserve(step, provider, model, 0, detail)
            self.settle(entry, 0, usage=usage)
            return entry

    def _entry(self, entry_id):
        return next(e for e in self.entries if e['id'] == entry_id)

    def save(self):
        with self._lock:
            self._save()

    def _save(self):
        data = dict(budget_usd=usd(self.budget), spent_usd=usd(self.spent()), prices_verified=PRICES_VERIFIED,
                    entries=self.entries)
        tmp = self.path.with_suffix('.tmp')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2) + '\n')
        os.replace(tmp, self.path)

    def summary(self):
        rows = [f'Budget ${usd(self.budget):.2f} · spent ${usd(self.spent()):.4f} · remaining ${usd(self.remaining()):.4f}']
        for e in self.entries:
            amount = e['actual_usd'] if e['status'] == 'ok' else e['estimate_usd']
            rows.append(f"  #{e['id']:<3} {e['step']:<10} {e['model']:<20} {e['status']:<15} ${amount:.4f}  {e['detail']}")
        free = sum(1 for e in self.entries if e.get('provider') in FREE_PROVIDERS)
        if free:
            rows.append(f'  ({free} of these are free local calls or keyless searches, recorded at $0.00)')
        return '\n'.join(rows)
