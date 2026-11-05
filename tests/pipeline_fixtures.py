"""Shared fixtures for the v2 pipeline tests: a valid draft and a fake Anthropic client."""
import json
from types import SimpleNamespace as NS


def beat(role, narration, visual, motion, *, text='', cast=('lead',), framing='medium', poses=(), sfx=(),
         sources=(), example=False, transition='cut'):
    return dict(role=role, narration=narration, on_screen_text=text, visual_intent=visual,
                shot=dict(framing=framing, cast=list(cast), image_prompt=f'Painted frame: {visual}',
                          pose_variants=list(poses), motion=[dict(recipe=r, anchor=a, detail=d) for r, a, d in motion],
                          transition=transition),
                sfx=[dict(name=n, anchor=a) for n, a in sfx], source_ids=list(sources), illustrative_numbers=example)


def salary_draft():
    return dict(
        title='Where Your Salary Goes',
        hook_promise='Why the money runs out before the month does',
        beats=[
            beat('hook', "Your salary didn't shrink. It leaked.", 'The lead holds a glass jar with coins trickling out of a crack.',
                 [('punch_in', 'leaked', '')], text='GONE BY THE 20TH', sfx=[('coin', 'leaked')]),
            beat('setup', 'Payday feels huge, so the first week feels like permission.',
                 'The lead at the café table beams at a full jar of coins.', [('push_in', '', ''), ('pose_swap', 'permission', '')],
                 poses=['the lead throws both arms up, grinning']),
            beat('tension', "Small buys don't feel like spending. A coffee here, a delivery there.",
                 'Coins hop out of the jar one by one as cups and parcels appear on the table.',
                 [('icon_pop', 'coffee', 'coffee cup')], sfx=[('pop', 'coffee')], framing='top_down', cast=()),
            beat('tension', 'Subscriptions renew quietly while you sleep.',
                 'The lead sleeps at a desk while three small envelopes slide under the door.',
                 [('checklist_tick', 'renew', 'music | streaming | cloud')], framing='wide'),
            beat('reveal', 'Spending tends to rise with income. That pattern is called lifestyle creep.',
                 'The jar grows taller, but a second, bigger cup drains it just as fast.',
                 [('text_slam', 'lifestyle creep', '')], text='LIFESTYLE CREEP', sources=['s1']),
            beat('reveal', 'Say you take home 3,000. Eighty a day is gone by the 20th.',
                 'A wall calendar: each day the lead tears a page, the jar beside it lowers.',
                 [('number_counter', 'gone', '3,000 -> 1,400')], text='3,000 → 1,400', example=True,
                 sfx=[('paper', 'gone')], framing='close'),
            beat('payoff', 'So pay yourself first: move savings out the day salary lands.',
                 'The lead pours coins from the first jar into a second jar before anything else.',
                 [('pose_swap', 'first', '')], poses=['the lead nods, holding the full second jar'],
                 sfx=[('ding', 'lands')], cast=('lead', 'friend')),
            beat('ending', "Check the jar on the 20th next month. It won't be empty.",
                 'Back at the café table, the lead taps a half-full jar and smiles.',
                 [('push_in', '', ''), ('punch_in', 'empty', '')]),
        ])


SALARY_FACTS_TEXT = [
    ('Household spending tends to rise as income rises, a pattern often called lifestyle inflation.',
     'https://www.example.org/lifestyle-inflation', 'Lifestyle inflation explained'),
    ('Paying yourself first means moving savings out before discretionary spending.',
     'https://www.example.gov/pay-yourself-first', 'Saving basics'),
]


def usage(input=1000, output=500, searches=0):
    return NS(input_tokens=input, output_tokens=output, cache_read_input_tokens=0, cache_creation_input_tokens=0,
              cache_creation=None, server_tool_use=NS(web_search_requests=searches, web_fetch_requests=0))


def research_response(stop='end_turn', facts=SALARY_FACTS_TEXT):
    content = [NS(type='text', text="I'll look this up.", citations=None),
               NS(type='server_tool_use', id='srv_1', name='web_search', input={'query': 'salary'}),
               NS(type='web_search_tool_result', tool_use_id='srv_1', content=[])]
    for i, (claim, url, title) in enumerate(facts):
        if i:
            content.append(NS(type='text', text='\n\n', citations=None))
        content.append(NS(type='text', text=claim, citations=[
            NS(type='web_search_result_location', url=url, title=title, cited_text=claim[:60], encrypted_index='x')]))
    return NS(content=content, usage=usage(9000, 400, 2), stop_reason=stop, stop_details=None)


def script_response(draft=None, stop='end_turn', text=None):
    body = text if text is not None else json.dumps(draft or salary_draft())
    return NS(content=[NS(type='thinking', thinking=''), NS(type='text', text=body)],
              usage=usage(4200, 3100), stop_reason=stop, stop_details=None)


class FakeMessages:
    def __init__(self, responses, count=1200):
        self.responses = list(responses)
        self.count = count
        self.created = []
        self.counted = []

    def count_tokens(self, **params):
        self.counted.append(params)
        return NS(input_tokens=self.count)

    def create(self, **params):
        self.created.append(params)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses, count=1200):
        self.messages = FakeMessages(responses, count)
