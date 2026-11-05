"""Small command line interface: author, validate, preview, narrate and render."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import urllib.error
from .story import load, validate, authoring_prompt, SCHEMA
from .visuals import preflight
from .pipeline import render, storyboard


def draft(prompt,model):
    key=os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('OPENAI_API_KEY is not set. Use the prompt command for the no-API workflow; paste its output into your writing assistant.')
    payload={'model':model,'store':False,'input':authoring_prompt(prompt),
             'text':{'format':{'type':'json_schema','name':'reel_story','strict':True,'schema':SCHEMA}}}
    request=urllib.request.Request('https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=90) as response:
            data=json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f'Prompt generation failed (HTTP {exc.code}); check your API model, account and key') from exc
    texts=[part.get('text','') for item in data.get('output',[]) if item.get('type')=='message'
           for part in item.get('content',[]) if part.get('type')=='output_text']
    if not texts:raise ValueError('Model returned no story text (possibly a refusal). Revise the prompt')
    story=validate(json.loads(''.join(texts)));preflight(story)
    return story


def write_new(path,text,force=False):
    path=Path(path)
    if path.exists() and not force:raise ValueError(f'{path} already exists; use --force to replace it')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Build new stick figure reels from editable stories')
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('prompt',help='Create a free, copy-paste authoring prompt for any writing assistant')
    p.add_argument('--prompt',required=True);p.add_argument('--output');p.add_argument('--force',action='store_true')
    p=commands.add_parser('draft',help='Optional paid OpenAI API: turn a prompt into story JSON')
    p.add_argument('--prompt',required=True);p.add_argument('--model',required=True)
    p.add_argument('--output',required=True);p.add_argument('--force',action='store_true')
    p=commands.add_parser('validate',help='Check story fields and text fit before narration/rendering')
    p.add_argument('story')
    p=commands.add_parser('preview',help='Render a storyboard without network or audio')
    p.add_argument('story');p.add_argument('--output',required=True);p.add_argument('--force',action='store_true')
    p=commands.add_parser('render',help='Generate/reuse narration, then export an MP4')
    p.add_argument('story');p.add_argument('--output',required=True)
    p.add_argument('--offline',action='store_true',help='Never call the speech service; use supplied/cached audio')
    p.add_argument('--silent',action='store_true',help='Preview motion without narration or sound effects')
    p.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='prompt':
            text=authoring_prompt(args.prompt)
            if args.output:write_new(args.output,text,args.force)
            else:print(text)
        elif args.command=='draft':
            # Fail before incurring an API call when the destination already exists.
            if Path(args.output).exists() and not args.force:raise ValueError('Output already exists; use --force')
            story=draft(args.prompt,args.model)
            write_new(args.output,json.dumps(story,indent=2,ensure_ascii=False),args.force)
            print(f'Story saved to {args.output}. Review it with the preview command before rendering.')
        elif args.command=='validate':
            story=load(args.story);preflight(story)
            print(f'Valid: {story["title"]} ({len(story["scenes"])} scenes)')
        elif args.command=='preview':
            if Path(args.output).exists() and not args.force:raise ValueError('Output already exists; use --force')
            print(storyboard(load(args.story),args.output))
        elif args.command=='render':
            render(args.story,args.output,args.offline,args.silent,args.force)
    except (ValueError,OSError,json.JSONDecodeError,subprocess.CalledProcessError) as exc:
        parser.exit(2,f'Error: {exc}\n')
    return 0
