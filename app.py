import os, sys, re, glob, time, tempfile, threading, subprocess, traceback
import numpy as np
from flask import Flask, request, jsonify, send_file

BASE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
FF = os.path.join(BASE, 'ffmpeg.exe') if os.path.exists(os.path.join(BASE, 'ffmpeg.exe')) else 'ffmpeg'
CF = 0x08000000 if os.name == 'nt' else 0
for b in {BASE, *sys.path}:  # CUDA-DLLs (pip nvidia-*) finden
    for d in glob.glob(os.path.join(b or '.', 'nvidia', '*', 'bin')):
        os.environ['PATH'] = d + os.pathsep + os.environ['PATH']
        try: os.add_dll_directory(d)
        except Exception: pass

app = Flask(__name__)
SRC = None
JOB = {'msg': '', 'done': True, 'result': None, 'error': None}
KW = ['aura', 'krass', 'alter', 'digga', 'wtf', 'omg', 'bruder', 'nein', 'geil', 'heftig', 'wahnsinn', 'bro', 'junge', 'what']


def job(fn, *a):
    JOB.update(msg='Start…', done=False, result=None, error=None)
    def run():
        try: JOB['result'] = fn(*a)
        except Exception as e:
            traceback.print_exc(); JOB['error'] = str(e)[-400:]
        JOB['done'] = True
    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=1)


def z(x):
    x = np.asarray(x, float); s = x.std()
    return np.zeros_like(x) if s == 0 else np.maximum(0, (x - x.mean()) / s)


def chunk(c):
    return {'s': round(c[0].start, 2), 'e': round(c[-1].end, 2), 't': ' '.join(x.word.strip() for x in c)}


def analyze(kf):
    JOB['msg'] = 'Audio lesen…'
    raw = subprocess.run([FF, '-v', 'error', '-i', SRC, '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'],
                         capture_output=True, creationflags=CF).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = max(1, len(a) // 16000)
    rms = np.sqrt((a[:n * 16000].reshape(n, 16000) ** 2).mean(1)) if len(a) >= 16000 else np.zeros(n)
    words = []
    if len(a):
        JOB['msg'] = 'Whisper transkribiert…'
        from faster_whisper import WhisperModel
        def tr(dev, ct, size):
            m = WhisperModel(size, device=dev, compute_type=ct)
            segs, _ = m.transcribe(a, language='de', word_timestamps=True, vad_filter=True)
            return [w for s in segs for w in (s.words or [])]
        try: words = tr('cuda', 'float16', 'medium')
        except Exception:
            JOB['msg'] = 'GPU nicht nutzbar, CPU…'; words = tr('cpu', 'int8', 'small')
    subs, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w.start - cur[-1].end > .5): subs.append(chunk(cur)); cur = []
        cur.append(w)
        if re.search(r'[.?!,]$', w.word.strip()): subs.append(chunk(cur)); cur = []
    if cur: subs.append(chunk(cur))
    kw = np.zeros(n)
    for w in words:
        if any(k in w.word.lower() for k in KW) and int(w.start) < n: kw[int(w.start)] += 1
    chg = np.zeros(n)
    if kf:
        import cv2
        JOB['msg'] = 'Killfeed analysieren…'
        cap = cv2.VideoCapture(SRC); fps = cap.get(5) or 30; step = max(1, round(fps / 4)); i = 0; prev = None
        while cap.grab():
            if i % step == 0:
                ok, f = cap.retrieve()
                if ok:
                    H, W = f.shape[:2]
                    g = cv2.resize(cv2.cvtColor(f[int(kf['y'] * H):int((kf['y'] + kf['h']) * H), int(kf['x'] * W):int((kf['x'] + kf['w']) * W)], cv2.COLOR_BGR2GRAY), (48, 48))
                    if prev is not None and int(i / fps) < n:
                        s = int(i / fps); chg[s] = max(chg[s], cv2.absdiff(g, prev).mean())
                    prev = g
            i += 1
    sc = np.convolve(z(rms) + 1.5 * z(chg) + kw, np.ones(3) / 3, 'same')
    hl = []
    for i in np.argsort(-sc):
        if sc[i] <= 0 or len(hl) >= 8: break
        if all(abs(i - h['t']) > 20 for h in hl):
            lab = next((c['t'] for c in subs if c['s'] >= i - 2), '')
            hl.append({'t': int(i), 'score': round(float(sc[i]), 1), 'label': lab[:60]})
    return {'subs': subs, 'highlights': hl}


def font(sz):
    from PIL import ImageFont
    for f in ('arialbd.ttf', 'DejaVuSans-Bold.ttf'):
        try: return ImageFont.truetype(f, sz)
        except Exception: pass
    return ImageFont.load_default()


def parse(line):
    out, red = [], False
    for i, t in enumerate(line.split('*')):
        if i: red = not red
        if t: out.append((t, red))
    return out


def hook_png(h, path):
    from PIL import Image, ImageDraw
    sz = int(h['size']); f = font(sz); L = [parse(l) for l in h['text'].split('\n')]; lh = int(sz * 1.15)
    ws = [sum(f.getlength(t) for t, _ in l) for l in L]
    W = int(max(ws + [0]) + 56); H = len(L) * lh + 56
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=36, fill='white')
    for i, l in enumerate(L):
        x = (W - ws[i]) / 2
        for t, r in l:
            d.text((x, 28 + i * lh), t, font=f, fill='#ff1a1a' if r else 'black'); x += f.getlength(t)
    im.save(path); return W, H


def col(h): h = h.lstrip('#'); return f'&H00{h[4:6]}{h[2:4]}{h[0:2]}'
def ts(t): t = max(0, t); return f'{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}'


def make_ass(p, path):
    st = p['st']; a, b = p['start'], p['end']; c1, c2 = col(st['c1']), col(st['c2'])
    o = ['[Script Info]', 'ScriptType: v4.00+', 'PlayResX: 1080', 'PlayResY: 1920', '[V4+ Styles]',
         'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding',
         f"Style: D,Arial,{int(st['size'])},{c1},{c1},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{round(st['size'] * .09)},3,5,40,40,0,1",
         '[Events]', 'Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text']
    pos = '{\\an5\\pos(540,%d)}' % int(st['y'])
    for c in p['subs']:
        if c['e'] < a or c['s'] > b: continue
        w = c['t'].split(); s0, e0 = c['s'], min(c['e'], b)
        if not w: continue
        if st['mode'] == 'hl' and len(w) > 1:
            for i in range(len(w)):
                t0 = s0 + (e0 - s0) * i / len(w); t1 = s0 + (e0 - s0) * (i + 1) / len(w)
                txt = (('{\\c%s&}%s ' % (c2, ' '.join(w[:i]))) if i else '') + '{\\c%s&}%s' % (c1, w[i])
                o.append(f'Dialogue: 0,{ts(t0 - a)},{ts(t1 - a)},D,,0,0,0,,{pos}{txt}')
        else:
            o.append(f"Dialogue: 0,{ts(s0 - a)},{ts(e0 - a)},D,,0,0,0,,{pos}{' '.join(w)}")
    open(path, 'w', encoding='utf-8').write('\n'.join(o))


def export(p):
    JOB['msg'] = 'Rendere…'
    tmp = tempfile.mkdtemp(); dur = p['end'] - p['start']; L = p['layers']
    inp = ['-ss', str(p['start']), '-t', str(dur), '-i', SRC, '-f', 'lavfi', '-t', str(dur), '-i', 'color=c=black:s=1080x1920:r=30']
    def en(o):
        t0, t1 = o.get('t0'), o.get('t1')
        return '' if t0 is None and t1 is None else f":enable='between(t,{t0 or 0},{99999 if t1 is None else t1})'"
    fc, cur = [], '1:v'
    if L: fc.append('[0:v]split=%d%s' % (len(L), ''.join(f'[s{i}]' for i in range(len(L)))))
    for i, l in enumerate(L):
        h = round(l['w'] * l['sh'] / l['sw'])
        ch = f"[s{i}]crop={l['sw']}:{l['sh']}:{l['sx']}:{l['sy']},scale={l['w']}:{h}"
        if l.get('b'): ch += f",drawbox=x=0:y=0:w=iw:h=ih:color=white:t={l['b']}"
        fc += [ch + f'[l{i}]', f"[{cur}][l{i}]overlay={l['x']}:{l['y']}{en(l)}[b{i}]"]; cur = f'b{i}'
    if p.get('hook'):
        w, _ = hook_png(p['hook'], os.path.join(tmp, 'hook.png'))
        inp += ['-loop', '1', '-t', str(dur), '-i', 'hook.png']
        fc.append(f"[{cur}][2:v]overlay={int(p['hook']['x'] - w / 2)}:{int(p['hook']['y'])}{en(p['hook'])}[bh]"); cur = 'bh'
    if p['subs'] and p['st'].get('on', True):
        make_ass(p, os.path.join(tmp, 'subs.ass')); fc.append(f'[{cur}]ass=subs.ass[out]')
    else: fc.append(f'[{cur}]null[out]')
    out = os.path.splitext(SRC)[0] + f'_clip_{int(time.time())}.mp4'
    for vc in (['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '21', '-b:v', '0'], ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20']):
        r = subprocess.run([FF, '-v', 'error', '-y', *inp, '-filter_complex', ';'.join(fc), '-map', '[out]', '-map', '0:a?', *vc,
                            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-t', str(dur), '-movflags', '+faststart', out],
                           cwd=tmp, capture_output=True, text=True, creationflags=CF)
        if r.returncode == 0: return {'out': out}
    raise Exception(r.stderr[-400:])


@app.route('/')
def idx(): return send_file(os.path.join(BASE, 'index.html'))

@app.route('/video')
def video(): return send_file(SRC, conditional=True)

@app.route('/api/pick', methods=['POST'])
def pick():
    global SRC
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)
    f = filedialog.askopenfilename(filetypes=[('Video', '*.mp4 *.mov *.mkv *.webm')]); r.destroy()
    if f: SRC = f
    return jsonify(path=f)

@app.route('/api/analyze', methods=['POST'])
def an(): return job(analyze, (request.json or {}).get('kf'))

@app.route('/api/export', methods=['POST'])
def ex(): return job(export, request.json)

@app.route('/api/status')
def status(): return jsonify(JOB)


if __name__ == '__main__':
    threading.Timer(1, lambda: __import__('webbrowser').open('http://127.0.0.1:5173')).start()
    app.run(port=5173, threaded=True)
