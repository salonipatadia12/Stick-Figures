"""API boundaries. No credentials or model responses are logged to the browser."""
import base64
from contextlib import ExitStack
import os
from pathlib import Path
from .models import Story, Landmarks


class GenerationError(RuntimeError):
    pass


# Standard USD / million tokens, verified 2026-10-25 against provider model pages.
# Unknown models fail closed; no model fallback or automatic paid retries.
STORY_BUDGET_USD = 0.25
STORY_RATES = {'claude-sonnet-5-5': (2, 10), 'gpt-4.1-mini': (.4, 1.6)}


def budget_output_tokens(model, input_tokens, budget_usd=STORY_BUDGET_USD):
    from decimal import Decimal, ROUND_FLOOR
    if model not in STORY_RATES:
        raise GenerationError('This model has no verified pricing for the $0.25 story limit. Use the default story model.')
    if type(input_tokens) is not int or input_tokens < 0:
        raise GenerationError('Could not verify the input token count. No story generation was started.')
    input_rate, output_rate = map(lambda v: Decimal(str(v)), STORY_RATES[model])
    # Keep 20% of the budget unused and add 1024 input tokens for counting overhead.
    budget = Decimal(str(budget_usd))
    if not budget.is_finite() or not Decimal('0') < budget <= Decimal('0.25'):
        raise GenerationError('The story budget must be positive and at most $0.25.')
    allowance = budget * Decimal('0.8') - (input_tokens + 1024) * input_rate / 1_000_000
    maximum = int((allowance * 1_000_000 / output_rate).to_integral_value(rounding=ROUND_FLOOR))
    if maximum < 2048:
        raise GenerationError('This request cannot fit the $0.25 story budget. Shorten the brief; no story generation was started.')
    return min(8192, maximum)


def story_provider_name():
    selected = os.getenv('STORY_PROVIDER', 'auto').strip().lower() or 'auto'
    if selected == 'auto':
        return 'anthropic' if os.getenv('ANTHROPIC_API_KEY', '').strip() else 'openai'
    return selected if selected in {'anthropic', 'openai'} else None


def story_available():
    name = story_provider_name()
    return bool(name and os.getenv(f'{name.upper()}_API_KEY', '').strip())


def story_model():
    if story_provider_name() == 'anthropic':
        return os.getenv('ANTHROPIC_STORY_MODEL', '').strip() or 'claude-sonnet-5-5'
    return os.getenv('STORY_MODEL', '').strip() or 'gpt-4.1-mini'


def story_provider():
    if not story_available():
        raise GenerationError('Configure ANTHROPIC_API_KEY or OPENAI_API_KEY and select a matching STORY_PROVIDER, then restart the server.')
    return AnthropicProvider() if story_provider_name() == 'anthropic' else OpenAIProvider()


def finish_story(story, brief):
    if brief.workflow=='automatic' and story.render_style=='rigged':
        from .directing import prepare_story
        return prepare_story(story)
    return story.reviewable()


def story_instructions(brief):
    instructions = f'''Write an ORIGINAL complete illustrated stick-figure story, about {brief.seconds} seconds.
Mood: {brief.mood}. The user requires a {brief.ending} ending. Build the setup and character choices toward that ending; do not substitute another ending. Use {"5–8 short speaking turns" if brief.render_style=='rigged' else (3 if brief.seconds <= 45 else "4–8 visual scenes")} and roughly {round(brief.seconds*2.1)} spoken words.
Give the protagonist a concrete goal, a believable obstacle, an action they choose,
and a satisfying causal ending. Avoid random twists, coincidences, generic slogans,
and copying an existing story. Narration must sound conversational. Use short sentences and contractions. Keep third-person narration separate from direct dialogue: a character-speaker scene contains only the exact words that character says. Never assign a character speaker to third-person narration about that character.
Each scene's visual must enact its narration, specifying pose, facial expression,
place, props and framing. Vary shots. Keep at most two recurring characters.
Character design: shaded white filled round head and rounded torso, navy outlines,
slim navy limbs, small hands, expressive simple oval eyes. Warm illustrated settings,
but characters dominate. Specify continuity details in character_notes. No text inside art.
movement is breathe, nod, or still; use sparingly. This is a storyboard, not full-action
video generation: prefer intimate everyday stories over fights or complex choreography.'''
    if brief.render_style=='rigged':
        instructions += """
Use render_style=rigged. This story is rendered with articulated 2D characters.
Write the interaction as a conversation performed by the characters. Use brief narrator
lines only for setup, a necessary transition, or the closing beat. When two people converse,
include at least one direct line from each, responding to the other's actual words.
Each scene is one speaking turn, not necessarily a new location or camera shot.
Use framing=two_shot for setup and shared actions, framing=speaker for a dialogue
turn whose facial reaction matters. These are static cuts, never moving zooms.
Keep both characters and their staging consistent across consecutive dialogue turns.
For example: narrator: "Mia hesitated." hero: "Will you stay?" friend: "Of course."
Never replace this exchange with "Mia asked him to stay and he said yes."
Use action=talk for the current speaker, and a restrained appropriate action and
emotion/end_emotion reaction for the listener. Only the speaker's mouth follows speech.
The renderer casts a separate consistent voice for narrator, hero and friend.
Plan the supported physical actions first; write narration that describes only those actions.
Use idle for a person already standing. stand is ONLY rising from a seated pose.
For two actors keep hero x/end_x .20–.34 and friend x/end_x .66–.80 so heads cannot collide.
Keep lighting consistent across continuous scenes. Keep a carried prop visible in later scenes;
put it down in its own scene before picking up a different one. Do not introduce bags unless
carrying or putting down the bag is essential; opening/searching a bag is unsupported.
Each scene can enact only ONE action per actor. Split picking up, walking and speaking into
separate scenes. Dialogue must be a separate scene with speaker hero/friend for mouth motion.
Do not narrate shaking, reading, opening objects, or looking inside them: these are unsupported.
Prefer simple goals, clear gestures and a small cast. The ending must happen on screen.
Define characters with IDs hero and friend, colors as hex, accessories none/scarf/glasses.
Use only the schema's emotion names: neutral, happy, sad, worried, surprised, angry.
Use prop="" for actions that do not interact with a prop; do not use null or "none".
Use action_start=0 and action_end=1 unless a specific later timing is needed.
Keep directions concise, but fill all schema fields and respect their limits.
Every scene MUST have animation directions. Settings: cafe, park, street, home, office, stage.
Light: day/sunset/night. Speaker narrator unless the entire scene is spoken by hero or friend.
Supported actions ONLY: idle, walk, sit, stand, wave, talk, point, write, pick_up, put_down, give, laugh.
One action per character per scene. action_start/action_end are scene fractions 0 to 1, at least .15 apart; use these to place the action near the words describing it. Pose standing/seated. Actor x and end_x are .18 to .82.
Props: book, bag, cup, flower, note, ball. Name each with a stable lowercase id.
Props start on a small table (holder none) or in hero/friend's hand. No invisible props.
write needs a book on the table. pick_up needs a free prop. give/put_down need a held prop.
Prop x must be within .26 of interacting actor x. For give, actors must be within .42,
and receiver uses idle. Preserve each prop's final holder in the next scene.
Use time_jump only for an explicit change of time. Narration describes exactly the supported
visible action; no unrenderable actions such as opening doors, cooking or pulling chairs.
Keep heads rigid, dialogue separate from narration, and use movement=still.
Do not promise a complex action using a similar simple action. Adapt the story to this vocabulary."""
    return instructions


def anthropic_story_schema():
    """Strict API subset; all original bounds still validated locally."""
    def transform(node):
        if isinstance(node, list):
            return [transform(v) for v in node]
        if not isinstance(node, dict):
            return node
        result = {}
        constraints = []
        for key, value in node.items():
            if key in {'minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf',
                       'minLength','maxLength','maxItems'} or (key == 'minItems' and value > 1):
                constraints.append(f'{key}: {value}')
            else:
                result[key] = transform(value)
        if constraints:
            result['description'] = (result.get('description', '') + ' Required limits: ' + '; '.join(constraints)).strip()
        if result.get('type') == 'object':
            result['additionalProperties'] = False
            # Avoid Claude's 24-optional-field grammar limit for the full scene tree.
            result['required'] = list(result.get('properties', {}))
        return result
    return transform(Story.model_json_schema())


def normalize_story_enums(value):
    """Claude strict outputs can vary enum casing. Do not invent actions or poses."""
    schema = Story.model_json_schema()
    def visit(value, spec):
        if '$ref' in spec:
            return visit(value, schema['$defs'][spec['$ref'].rsplit('/', 1)[-1]])
        for option in spec.get('anyOf', []):
            if value is None and option.get('type') == 'null':return None
            if value is not None and option.get('type') != 'null':return visit(value, option)
        if isinstance(value, str) and 'enum' in spec:
            return next((v for v in spec['enum'] if isinstance(v, str) and v.casefold() == value.strip().casefold()), value)
        if isinstance(value, dict):
            return {k: visit(v, spec.get('properties', {}).get(k, {})) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, spec.get('items', {})) for v in value]
        return value
    return visit(value, schema)


def validation_details(exc):
    # Exclude model inputs, exception context and URLs from stored diagnostics.
    return [dict(path='.'.join(map(str, e['loc'])), message=e['msg'], type=e['type'])
            for e in exc.errors(include_input=False, include_context=False, include_url=False)]


class AnthropicProvider:
    """One Messages request; tool output is data, never executed code."""
    def __init__(self, budget_usd=STORY_BUDGET_USD):
        self.budget_usd = budget_usd
        self.last_attempt = None

    def draft(self, brief):
        from pydantic import ValidationError
        key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not key:
            raise GenerationError('Set ANTHROPIC_API_KEY on the server, then restart.')
        payload = dict(
            model=os.getenv('ANTHROPIC_STORY_MODEL', '').strip() or 'claude-sonnet-5-5',
            max_tokens=8192,
            system=story_instructions(brief) + '\nReturn the complete story by calling submit_story exactly once.',
            messages=[dict(role='user', content=brief.prompt)],
            tools=[dict(name='submit_story', description='Submit the complete story and supported animation directions for user review.', strict=True, input_schema=anthropic_story_schema())],
            tool_choice=dict(type='auto'),
            thinking=dict(type='between_tools'),
        )
        headers = {'x-api-key': key, 'anthropic-version': '2026-10-21', 'content-type': 'application/json'}
        workspace = os.getenv('ANTHROPIC_WORKSPACE_ID', '').strip()
        if workspace:
            headers['anthropic-workspace-id'] = workspace
        # Preflight is free; failure never falls through to a paid request.
        budget_output_tokens(payload['model'], 0, self.budget_usd)
        count_payload = {k: v for k, v in payload.items() if k != 'max_tokens'}
        counted = self._request('/count_tokens', count_payload, headers)
        payload['max_tokens'] = budget_output_tokens(payload['model'], counted.get('input_tokens'), self.budget_usd)
        payload['service_tier'] = 'standard_only'
        result = self._request('', payload, headers)
        rates = STORY_RATES[payload['model']]
        usage = result.get('usage') or {}
        self.last_attempt = dict(model=payload['model'], budget_usd=self.budget_usd,
            input_tokens_counted=counted['input_tokens'], max_output_tokens=payload['max_tokens'],
            reserved_usd=((counted['input_tokens']+1024)*rates[0]+payload['max_tokens']*rates[1])/1_000_000,
            usage={k: usage.get(k) for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')},
            stop_reason=result.get('stop_reason'))
        blocks = result.get('content') or []
        drafts = [block.get('input') for block in blocks if isinstance(block, dict)
                  and block.get('type') == 'tool_use' and block.get('name') == 'submit_story']
        if len(drafts) == 1:
            self.last_attempt['candidate'] = drafts[0]
        if result.get('stop_reason') == 'max_tokens':
            raise GenerationError('The story service reached its token limit before finishing. Any returned draft was saved for recovery; no automatic paid retry was made.')
        if len(drafts) != 1:
            raise GenerationError('The story service did not return one complete scene plan. No automatic paid retry was made.')
        try:
            self.last_attempt['candidate'] = drafts[0]
            story = Story.model_validate(normalize_story_enums(drafts[0]))
        except ValidationError as exc:
            details = validation_details(exc)
            self.last_attempt['validation_errors'] = details
            issue = details[0]
            raise GenerationError(f"The draft was saved, but needs a scene correction at {issue['path']}: {issue['message']}. No automatic paid retry was made.") from None
        if story.render_style != brief.render_style:
            raise GenerationError('The story service returned a different animation style. Please retry the draft.')
        try:
            return finish_story(story, brief)
        except ValueError as exc:
            self.last_attempt['validation_errors'] = [dict(path='scenes', message=str(exc))]
            raise GenerationError('The draft was saved, but an animation direction needs correction: ' + str(exc) + ' No automatic paid retry was made.') from None


    @staticmethod
    def _request(suffix, payload, headers):
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError
        request = urllib.request.Request('https://api.anthropic.com/v1/messages' + suffix,
            data=json.dumps(payload).encode(), headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.load(response)
        except HTTPError as exc:
            messages = {401: 'Anthropic rejected the API key. Check ANTHROPIC_API_KEY and restart.',
                403: 'Anthropic denied access. Check key permissions, workspace and model access.',
                429: 'Anthropic rate or spending limit reached. Check your account and retry later.',
                400: 'Anthropic rejected the request. Check model access, workspace and available API credits.',
                404: 'Anthropic model unavailable. Check ANTHROPIC_STORY_MODEL.'}
            message = messages.get(exc.code, 'Anthropic is temporarily unavailable. Retry later.')
            exc.close()
            raise GenerationError(message) from None
        except (URLError, TimeoutError, OSError):
            raise GenerationError('Could not reach Anthropic. Check the server connection and retry.') from None
        except (ValueError, UnicodeError):
            raise GenerationError('Anthropic returned an unreadable response. Please retry.') from None
        if not isinstance(result, dict):
            raise GenerationError('Anthropic returned an unusable response. Please retry.')
        return result


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI
        if not os.getenv('OPENAI_API_KEY'):
            raise GenerationError('AI generation needs OPENAI_API_KEY on the server. You can use the manual workflow now.')
        self.client = OpenAI(timeout=240, max_retries=0)
        self.text_model = os.getenv('STORY_MODEL', 'gpt-4.1-mini')
        self.image_model = os.getenv('IMAGE_MODEL', 'gpt-image-2')

    def draft(self, brief):
        instructions = story_instructions(brief)
        from openai.lib._parsing._responses import type_to_text_format_param
        inputs = [{'role':'system','content':instructions}, {'role':'user','content':brief.prompt}]
        budget_output_tokens(self.text_model, 0)
        # Use the same strict schema as responses.parse, with the pinned SDK.
        count = self.client.responses.input_tokens.count(model=self.text_model, input=inputs,
            text={'format': type_to_text_format_param(Story)})
        maximum = budget_output_tokens(self.text_model, count.input_tokens)
        response = self.client.responses.parse(model=self.text_model, input=inputs,
            text_format=Story, max_output_tokens=maximum, service_tier='default')
        if response.output_parsed is None:
            raise GenerationError('The story service did not return a usable story. Try a different brief.')
        if response.output_parsed.render_style!=brief.render_style:
            raise GenerationError('The story service returned a different animation style. Please retry the draft.')
        return finish_story(response.output_parsed, brief)

    def illustrate(self, story, index, reference, first_scene, target):
        scene = story.scenes[index]
        prompt = f'''Create one finished portrait illustration for scene {index+1} of an original story.
Story: {story.logline}
Identity and continuity: {story.character_notes}
Scene to illustrate: {scene.visual}
Narration context only (do NOT put these words in the image): {scene.narration}
Image 1 is the selected character/art-style reference. Preserve its white filled round
head, navy outlines and limbs, shaded white torso, expressive face and small hands.
If image 2 is included, preserve recurring identities, props and architecture from it.
Characters must be the primary focus. Two arms and two eyes per figure, connected hands,
consistent props, coherent eyelines. No lettering, speech balloons, watermarks or panels.
Use a vertical composition with important faces and props inside the central 80 percent,
and leave the bottom 15 percent free of key details for captions. Warm painted background.
This is one scene, not a contact sheet. Distinct purposeful pose and expression.'''
        with ExitStack() as stack:
            paths = [reference] + ([first_scene] if first_scene and first_scene != reference else [])
            images = [stack.enter_context(Path(p).open('rb')) for p in paths]
            result = self.client.images.edit(model=self.image_model, image=images,
                prompt=prompt, size='1024x1536', quality='medium', output_format='png', n=1)
        if not result.data or not result.data[0].b64_json:
            raise GenerationError('The image service returned no image.')
        target.write_bytes(base64.b64decode(result.data[0].b64_json))
        # Landmark analysis guides restrained head motion, never paints new eyes.
        # If the analysis fails, retain the artwork and use a still actor safely.
        try:
            response = self.client.responses.parse(model=self.text_model,
                input=[{'role':'user','content':[
                    {'type':'input_text','text':'Locate up to two round character heads in this illustration. Return each bounding ellipse center x,y and radii rx,ry normalized to image width/height (0 to 1). Exclude props and drawings. Return no heads for an overhead object shot. Tight ellipse around entire head including outline.'},
                    {'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(target.read_bytes()).decode()}]}],
                text_format=Landmarks)
            return response.output_parsed.model_dump()['heads'] if response.output_parsed else []
        except Exception:
            return []
