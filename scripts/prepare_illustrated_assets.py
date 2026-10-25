"""Slice the generated atlas into registered animation components; no repainting.
Run from the repository root after regenerating the source atlases.
"""
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]/'studio/assets/illustrated'

def main():
    atlas=Image.open(ROOT/'character-atlas.png').convert('RGBA')
    boxes={'head':(0,0,724,620),'torso':(724,0,1448,620),
           'hand-rest':(0,620,724,1086),'hand-open':(724,620,1448,1086)}
    for name,box in boxes.items():
        part=atlas.crop(box);bounds=part.getchannel('A').point(lambda a:255 if a>24 else 0).getbbox()
        part.crop(bounds).save(ROOT/f'{name}.png')
    atlas=Image.open(ROOT/'background-atlas.png').convert('RGB')
    # Atlas seams are recorded from the generated source, not guessed equal cells.
    xs=[0,358,736,1151];ys=[0,679,1366]
    for index,name in enumerate(['cafe','park','street','home','office','stage']):
        col,row=index%3,index//3
        atlas.crop((xs[col]+2,ys[row]+2,xs[col+1]-2,ys[row+1]-2)).save(ROOT/f'background-{name}.jpg',quality=95)

if __name__=='__main__':main()
