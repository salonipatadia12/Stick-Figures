from pathlib import Path
import asyncio,json,edge_tts
root=Path(__file__).resolve().parent
scenes=[
["Sam hadn't seen his friend in months.","Months without catching up.","intro"],
["He typed, ‘Want to grab a coffee?’ Then deleted it. They were probably busy.","What if they're busy?","hesitate"],
["A week later, his friend messaged. ‘It's been a rough week.’", "A week later…","receive"],
["This time, Sam didn't overthink it. ‘Coffee tomorrow? My treat.’", "Coffee tomorrow? My treat.","send"],
["The reply came straight back. ‘I'd really like that.’", "I'd really like that.","reply"],
["Two coffees. One long conversation.","Two coffees. One conversation.","coffee"],
["Turns out, his friend had been waiting to reach out, too.","They'd both been waiting.","end"]]
(root/'scenes.json').write_text(json.dumps(scenes))
async def main():
 for j,(text,_,_) in enumerate(scenes):
  print('Narration',j,flush=True)
  await edge_tts.Communicate(text,'en-US-AndrewNeural',rate='-3%').save(str(root/'audio'/f'{j}.mp3'))
asyncio.run(main())
