"""Rebuild the before/after image and original character pose sheet."""
from pathlib import Path
import sys
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'outputs/the-message-source'))
from builder.visuals import draw
from builder.story import validate
from builder.art import font

def scene(action='idle',expression='happy',background='standing'):
    return validate({'scenes':[dict(action=action,expression=expression,background=background)]})['scenes'][0]

def build(output):
    output.mkdir(parents=True,exist_ok=True)
    sheet=Image.new('RGB',(1200,940),'#FFFEFA');d=ImageDraw.Draw(sheet)
    for i,(style,label) in enumerate([('classic','PREVIOUS'),('expressive','NEW CHARACTER')]):
        shot=draw(scene(),1.8,style).crop((200,630,880,1490)).resize((540,683),Image.Resampling.LANCZOS)
        sheet.paste(shot,(30+i*600,155))
        d.text((300+i*600,80),label,font=font(27),fill='#20292D',anchor='mm')
    d.line((600,150,600,850),fill='#D4DDDA',width=2)
    d.text((300,890),'Large eyes / thin limbs',font=font(21,False),fill='#657570',anchor='mm')
    d.text((900,890),'Simple face / clearer silhouette',font=font(21,False),fill='#397E78',anchor='mm')
    sheet.save(output/'character-redesign.png')
    poses=[('THINK','think','neutral'),('LISTEN','nod','happy'),('TIRED','idle','tired'),('CELEBRATE','celebrate','happy')]
    sheet=Image.new('RGB',(1600,920),'#FFFEFA');d=ImageDraw.Draw(sheet)
    d.text((800,70),'ONE CHARACTER. FOUR READABLE POSES.',font=font(32),fill='#20292D',anchor='mm')
    for i,(label,action,exp) in enumerate(poses):
        shot=draw(scene(action,exp),1.8).crop((270,640,810,1480)).resize((380,591),Image.Resampling.LANCZOS)
        sheet.paste(shot,(10+i*400,180))
        d.text((200+i*400,830),label,font=font(23),fill='#397E78',anchor='mm')
    sheet.save(output/'character-pose-sheet.png')

if __name__=='__main__':build(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'outputs')
