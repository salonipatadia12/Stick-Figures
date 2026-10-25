"""Registered painted cels used by the reusable actor rig.

Raster components are prepared once, then positioned as rigid images. The wrist
anchor is transformed with the hand, so rotating a greeting never detaches it.
"""
from functools import lru_cache
from pathlib import Path
import math
from PIL import Image, ImageOps, ImageChops

ART_VERSION='cafe-cels-1'
ROOT=Path(__file__).resolve().parent/'assets/illustrated'

@lru_cache(maxsize=24)
def backdrop(setting,light,size=(720,1280)):
    with Image.open(ROOT/f'background-{setting}.jpg') as im:
        result=ImageOps.fit(im.convert('RGB'),size,Image.Resampling.LANCZOS)
    if light=='sunset':result=Image.blend(result,Image.new('RGB',size,'#DF9E65'),.10)
    elif light=='night':result=Image.blend(result,Image.new('RGB',size,'#15253F'),.65)
    return result

@lru_cache(maxsize=64)
def cel(name,width,height,mirror=False,color=None):
    with Image.open(ROOT/f'{name}.png') as source:
        im=source.convert('RGBA').resize((width,height),Image.Resampling.LANCZOS)
    if color and color.upper()!='#F8F3E8':
        alpha=im.getchannel('A')
        im=ImageChops.multiply(im.convert('RGB'),Image.new('RGB',im.size,color)).convert('RGBA');im.putalpha(alpha)
    return ImageOps.mirror(im) if mirror else im

def place(canvas,sprite,anchor,target,angle=0):
    """Angle is counterclockwise; anchor and target are in actual render pixels."""
    if abs(angle)<.01:
        rotated=sprite;point=anchor
    else:
        rotated=sprite.rotate(angle,Image.Resampling.BICUBIC,expand=True)
        theta=math.radians(angle);c,s=math.cos(theta),math.sin(theta)
        dx,dy=anchor[0]-sprite.width/2,anchor[1]-sprite.height/2
        point=(rotated.width/2+c*dx+s*dy,rotated.height/2-s*dx+c*dy)
    canvas.paste(rotated,(round(target[0]-point[0]),round(target[1]-point[1])),rotated)
