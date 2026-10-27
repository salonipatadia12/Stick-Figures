"""Render the reusable acting test. First run uses online TTS; later runs cache it.

Run from the repository root: .venv/bin/python scripts/render_dialogue_test.py
"""
import json
from pathlib import Path
import sys
import argparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio.models import Story
from studio.animation import AnimatedScene
from studio.rendering import render_project


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--story',default='studio/examples/listening.json')
    parser.add_argument('--output',default='outputs/dialogue-acting-test')
    args=parser.parse_args()
    story=Story.model_validate_json((ROOT/args.story).read_text()).reviewable().model_dump()
    folder=ROOT/args.output;folder.mkdir(exist_ok=True,parents=True)
    assets={}
    for i,scene in enumerate(story['scenes']):
        filename=f'scene-{i}.jpg'
        AnimatedScene(scene,story['characters']).frame(2,5).save(folder/filename)
        assets[str(i)]={'file':filename,'heads':[]}
    project=dict(story=story,assets=assets,audio={})
    result=render_project(project,folder,lambda message:print(message,flush=True))
    (folder/'render-result.json').write_text(json.dumps(result,indent=2))
    print(folder/result['video'],flush=True)


if __name__=='__main__':main()
