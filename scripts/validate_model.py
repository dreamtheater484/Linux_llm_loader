"""Run reproducible text, vision, or occupied-context checks against Lumen."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:7860'


def api(path, body=None):
    request = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', 'X-Lumen-Local': '1'})
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.load(response)


def generate(messages, tokens=128):
    req = urllib.request.Request(URL + '/api/chat', data=json.dumps({'messages': messages, 'max_output': tokens, 'temperature': 0}).encode(),
        headers={'Content-Type': 'application/json', 'X-Lumen-Local': '1'})
    text, reasoning, complete = '', '', None
    with urllib.request.urlopen(req, timeout=1800) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            event = json.loads(line[6:])
            if event['type'] == 'token':
                text += event['text']; reasoning += event['reasoning']
                print(event['text'], end='', flush=True)
            elif event['type'] == 'complete':
                complete = event
            elif event['type'] == 'error':
                raise RuntimeError(event['message'])
    if not complete:
        raise RuntimeError('Generation did not complete')
    return dict(text=text, reasoning=reasoning, metrics=complete)


def image_fixture():
    from PIL import Image, ImageDraw, ImageFont
    path = ROOT / 'validation/vision-fixture.png'
    path.parent.mkdir(exist_ok=True)
    img = Image.new('RGB', (640, 360), '#fffdf6')
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 42)
    draw.text((165, 35), 'ORBIT 572', fill='#193927', font=font)
    draw.ellipse((65, 155, 185, 275), fill='#df3434')
    draw.rectangle((260, 155, 380, 275), fill='#245ddb')
    draw.polygon([(520, 150), (455, 275), (585, 275)], fill='#f4cc25')
    img.save(path)
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()


def main():
    p=argparse.ArgumentParser();p.add_argument('kind', choices=['text','vision','long']);p.add_argument('--tokens',type=int,default=258000)
    p.add_argument('--image',action='store_true',help='Include an image and heading retrieval in the long-context request')
    args=p.parse_args()
    status=api('/api/status')['session']
    if status['state'] != 'ready':
        raise RuntimeError('Load the model first')
    result={'kind':args.kind,'model':status['model'],'settings':status['settings'],'effective':status['effective'],
            'created':datetime.now(timezone.utc).isoformat()}
    expected=[]
    if args.kind=='text':
        content='What is 17 multiplied by 23? Give the answer, then one sentence explaining your calculation.'
        expected=['391']
    elif args.kind=='vision':
        content=[{'type':'text','text':'Answer both parts: 1. Transcribe every character in the heading at the top of this image. 2. List the three shapes from left to right, including their colors. Include the heading in your answer.'},
                 {'type':'image_url','image_url':{'url':image_fixture()}}]
        expected=['ORBIT','572','red','circle','blue','square','yellow','triangle']
    else:
        codes=[secrets.token_hex(4).upper() for _ in range(3)]
        expected=codes+(['ORBIT','572'] if args.image else [])
        fixture=image_fixture() if args.image else None
        line='The archive contains ordinary reference material about local paths, tables, measurements, and descriptions. This paragraph has no access code.\n'
        repeats=max(1,args.tokens//30//3)
        def assemble(n):
            text=f'EARLY access code: {codes[0]}. Keep this code for the final question.\n'+line*n+f'\nMIDDLE access code: {codes[1]}.\n'+line*n+f'\nLATE access code: {codes[2]}.\n'+line*n+'\nFinal question: return the EARLY, MIDDLE, and LATE access codes in that order.'
            if fixture:
                return [{'type':'text','text':text+' Also transcribe the heading in the attached image. Return the three codes and the heading.'},
                        {'type':'image_url','image_url':{'url':fixture}}]
            return text+' Return only the three codes.'
        for _ in range(4):
            content=assemble(repeats)
            count=api('/api/token-count', {'messages':[{'role':'user','content':content}]})['input_tokens']
            print(f'Occupied context: {count:,} tokens', flush=True)
            if abs(count-args.tokens)<100:
                break
            repeats=max(1,round(repeats*args.tokens/count))
        result['counted_input_tokens']=count
        result['requested_input_tokens']=args.tokens
        result['includes_image']=args.image
    result.update(generate([{'role':'user','content':content}],256 if args.kind=='vision' else 128))
    result['expected']=expected
    result['passed']=all(value.lower() in result['text'].lower() for value in expected)
    if args.kind=='long' and result['passed']:
        positions=[result['text'].lower().find(value.lower()) for value in expected[:3]]
        result['passed']=positions==sorted(positions)
    destination=ROOT/'validation'/f"{status['model']['id']}-{args.kind}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('\n'+json.dumps({'passed':result['passed'],'metrics':result['metrics'],'saved':str(destination)},indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
