"""Small 2D puppet actions with connected grips and a stationary café.

Original artwork supplies movable bag/chair/hand pixels. Generated clean plates
are revealed only inside the old silhouettes; faces are never repainted.
"""
import math
from PIL import Image, ImageDraw, ImageChops, ImageFilter
from render import Scene, HERE, SIZE, ease, pulse
from lipsync import LipSync, paint_mouth, paint_laugh
from writing import WritingRig

SOURCE=(941,1672)
NAVY='#171A32'


def canvas():return Image.new('L',SOURCE)


def bezier(points,steps=60):
    a,b,c=points
    return [((1-q)**2*a[0]+2*(1-q)*q*b[0]+q*q*c[0],
             (1-q)**2*a[1]+2*(1-q)*q*b[1]+q*q*c[1]) for q in [i/steps for i in range(steps+1)]]


def dark_hand(im,box):
    mask=canvas();d=ImageDraw.Draw(mask);d.rectangle(box,fill=255)
    # Navy hand against orange wood/strap; never sample a face color.
    rgb=im.convert('RGB');threshold=canvas()
    threshold.putdata([255 if b>r*.9 and b>g*1.08 and max(r,g,b)<145 else 0 for r,g,b in rgb.get_flattened_data()])
    return ImageChops.multiply(mask,threshold)


def sprite(im,mask):
    layer=im.convert('RGBA');layer.putalpha(mask);return layer


def translated(layer,dx,dy):
    return layer.transform(SOURCE,Image.Transform.AFFINE,(1,0,-dx,0,1,-dy),Image.Resampling.BICUBIC)


def elbow(a,b,upper=255,lower=255):
    dx,dy=b[0]-a[0],b[1]-a[1];distance=math.hypot(dx,dy)
    along=(upper*upper-lower*lower+distance*distance)/(2*distance)
    side=math.sqrt(max(0,upper*upper-along*along))
    return a[0]+along*dx/distance-side*dy/distance,a[1]+along*dy/distance+side*dx/distance


class PropRig:
    def __init__(self,name,file):
        self.name=name;self.original=Image.open(HERE/'assets'/file).convert('RGB')
        self.base=self.original.copy()
        plate=Image.open(HERE/'assets/motion'/f'{name}-plate.png').convert('RGB').resize(SOURCE,Image.Resampling.LANCZOS)
        erase=canvas();e=ImageDraw.Draw(erase);mask=canvas();d=ImageDraw.Draw(mask)
        if name=='bag':
            # Solid canvas body and separate straps leave their holes transparent.
            d.polygon([(652,852),(694,835),(748,850),(799,874),(843,889),(853,941),(859,1028),(842,1059),(761,1049),(689,1027),(640,1002),(632,926),(642,880)],fill=255)
            d.line([(688,761),(704,739),(720,740),(741,782),(760,830),(785,869)],fill=255,width=18,joint='curve')
            d.line([(691,777),(684,811),(673,843),(665,867)],fill=255,width=13,joint='curve')
            d.line([(854,963),(883,1004),(896,1030),(885,1045),(859,1037)],fill=255,width=13,joint='curve')
            self.shoulder=(365,749);self.wrist=(675,765)
            grip=dark_hand(self.original,(647,732,724,793))
            silhouette=canvas();ImageDraw.Draw(silhouette).polygon(
                [(649,754),(660,743),(676,736),(700,736),(713,742),(721,750),
                 (721,776),(709,790),(692,791),(679,778),(650,777)],fill=255)
            self.hand=sprite(self.original,ImageChops.multiply(grip,silhouette))
            # Replace his entire occluded arm together: a narrow strip left a detached hand.
            e.rectangle((540,635,682,862),fill=255)
            e.line([(357,748),(489,775),(675,765)],fill=255,width=54,joint='curve')
            self.upper,self.lower=180,180
        else:
            # The chair's individual wooden pieces, rather than an opaque box.
            d.polygon([(788,745),(841,733),(941,738),(941,814),(790,810)],fill=255)
            for a,b,width in [((799,805),(780,1037),25),((848,808),(823,1045),24),((894,809),(866,1054),24),((940,810),(907,1068),30),
                ((607,1121),(560,1376),25),((775,1138),(775,1330),22),((797,1138),(795,1441),26),((915,1122),(941,1288),25),
                ((586,1215),(778,1190),20),((586,1228),(793,1278),20),((796,1276),(929,1221),21),((778,1193),(930,1222),17)]:
                d.line([a,b],fill=255,width=width)
            d.polygon([(585,1067),(775,1029),(941,1065),(941,1118),(790,1146),(582,1115)],fill=255)
            self.shoulder=(325,745);self.wrist=(779,792)
            self.hand=sprite(self.original,dark_hand(self.original,(759,725,861,816)))
            e.line(bezier([(325,744),(554,920),(803,773)]),fill=255,width=52,joint='curve')
            self.upper,self.lower=255,255
        self.prop=sprite(self.original,mask)
        if name=='bag':
            isolated=Image.open(HERE/'assets/motion/bag-sprite.png').convert('RGBA')
            isolated=isolated.crop(isolated.getchannel('A').getbbox()).resize((264,325),Image.Resampling.LANCZOS)
            self.prop=Image.new('RGBA',SOURCE);self.prop.alpha_composite(isolated,(635,735))
        else:
            propfile=HERE/'assets/motion/chair-sprite.png'
            if propfile.exists():
                isolated=Image.open(propfile).convert('RGBA')
                isolated=isolated.crop(isolated.getchannel('A').getbbox()).resize((540,710),Image.Resampling.LANCZOS)
                self.prop=Image.new('RGBA',SOURCE);self.prop.alpha_composite(isolated,(570,733))
            e.polygon([(780,720),(941,720),(941,1500),(540,1450),(560,1130),(580,1060),(740,1020)],fill=255)
        erase=ImageChops.lighter(erase,mask.filter(ImageFilter.MaxFilter(9)))
        erase=ImageChops.lighter(erase,self.hand.getchannel('A').filter(ImageFilter.MaxFilter(9)))
        self.base.paste(plate,(0,0),erase)

    def frame(self,t,acting):
        if self.name=='bag':
            q=ease(t/1.12);dx=-50*q;dy=28-78*q
            # Small settling arc after the lift, not a repeating oscillation.
            dy+=3*math.sin(t*6)*math.exp(-t*3)
        else:
            # Chair pull occurs at the final invitation, not throughout his return.
            q=ease((t-.2)/1.2) if acting=='pull' else 0
            dx=4*q;dy=22*q
        im=self.base.convert('RGBA')
        prop=translated(self.prop,dx,dy);im.alpha_composite(prop)
        wrist=(self.wrist[0]+dx,self.wrist[1]+dy)
        mid=elbow(self.shoulder,wrist,self.upper,self.lower)
        arm=Image.new('RGBA',SOURCE);d=ImageDraw.Draw(arm)
        # Fixed-length upper arm and forearm, rounded joint, intact original hand.
        if self.name=='chair':
            # Smooth connected arm, with effort building before the chair moves.
            control=(mid[0],mid[1]+42)
            d.line(bezier([self.shoulder,control,wrist]),fill=NAVY,width=32,joint='curve')
        else:
            d.line([self.shoulder,mid,wrist],fill=NAVY,width=29,joint='curve')
        for x,y in ([self.shoulder,wrist] if self.name=='chair' else [self.shoulder,mid,wrist]):d.ellipse((x-14,y-14,x+14,y+14),fill=NAVY)
        im.alpha_composite(arm);im.alpha_composite(translated(self.hand,dx,dy))
        return im.convert('RGB').resize(SIZE,Image.Resampling.LANCZOS)


class MovementScene(Scene):
    def __init__(self,name,spec):
        super().__init__(name,spec)
        self.lips=LipSync();self.timeline_t=0
        self.writing=WritingRig() if name=='alone' else None
        self.rig=PropRig('bag',spec['file']) if name=='bag' else PropRig('chair',spec['file']) if name=='return' else None

    def frame(self,t,acting):
        im=self.writing.frame(self.timeline_t) if self.writing else self.rig.frame(t,acting) if self.rig else self.im.copy()
        from recut import at, PAUSES, LEAD
        # Invert the edit's inserted pauses, without making a paused word repeat.
        source_t=self.timeline_t-LEAD
        for threshold,gap in PAUSES:
            if self.timeline_t>=at(threshold):source_t-=gap
            elif self.timeline_t>=at(threshold)-gap:source_t=threshold-.001;break
        width,height=self.lips.shape(self.timeline_t,source_t)
        im=paint_mouth(im,self.name,width,height)
        chuckle=pulse(t,.12,.65)
        if acting=='laugh':im=paint_laugh(im,chuckle)
        # Raster heads remain rigid. Animate only explicitly separated parts.
        return im
