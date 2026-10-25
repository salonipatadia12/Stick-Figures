"""API boundaries. No credentials or model responses are logged to the browser."""
import base64
from contextlib import ExitStack
import os
from pathlib import Path
from .models import Story, Landmarks


class GenerationError(RuntimeError):
    pass


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI
        if not os.getenv('OPENAI_API_KEY'):
            raise GenerationError('AI generation needs OPENAI_API_KEY on the server. You can use the manual workflow now.')
        self.client = OpenAI(timeout=240, max_retries=0)
        self.text_model = os.getenv('STORY_MODEL', 'gpt-4.1-mini')
        self.image_model = os.getenv('IMAGE_MODEL', 'gpt-image-2')

    def draft(self, brief):
        instructions = f'''Write an ORIGINAL complete illustrated stick-figure story, about {brief.seconds} seconds.
Mood: {brief.mood}. Use 3–8 visual scenes and roughly {round(brief.seconds*2.1)} spoken words.
Give the protagonist a concrete goal, a believable obstacle, an action they choose,
and a satisfying causal ending. Avoid random twists, coincidences, generic slogans,
and copying an existing story. Narration must sound conversational. Use one consistent
third-person narrator, short sentences, contractions, and occasional quoted dialogue.
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
Define characters with IDs hero and friend, colors as hex, accessories none/scarf/glasses.
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
        response = self.client.responses.parse(model=self.text_model,
            input=[{'role':'system','content':instructions}, {'role':'user','content':brief.prompt}],
            text_format=Story)
        if response.output_parsed is None:
            raise GenerationError('The story service did not return a usable story. Try a different brief.')
        if response.output_parsed.render_style!=brief.render_style:
            raise GenerationError('The story service returned a different animation style. Please retry the draft.')
        return response.output_parsed.reviewable()

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
