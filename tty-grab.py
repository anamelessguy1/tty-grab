#!/usr/bin/env python3
import struct, time, sys, os, re, signal, subprocess
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_tty(s):
    m = re.match(r'^(?:tty|vcsa)?(\d+)$', s, re.I)
    return f"/dev/vcsa{m.group(1)}" if m else s

HELP = """
usage: tty-grab [-r | -s] [tty N] [output FILE] [fps N]

record or screenshot a linux vt (tty) by anamelessguy
WARNING: recording uses a lot of cpu and also
SECOND WARNING: root permissions needed

  -r            record video
  -s            take screenshot
  tty N         tty number (default: tty 3)
  output FILE   output filename
  fps N         framerate for video mode (default: 14)
"""

if len(sys.argv) == 1 or "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(HELP)
    sys.exit(0)

tty_val = None
output = None
fps = 14
screenshot = False
record = False

tokens = sys.argv[1:]
i = 0
while i < len(tokens):
    tok = tokens[i]
    low = tok.lower()
    if tok in ("-s", "--screenshot"):
        screenshot = True; i += 1
    elif tok in ("-r", "--record"):
        record = True; i += 1
    elif low in ("tty", "t") and i+1 < len(tokens):
        tty_val = tokens[i+1]; i += 2
    elif low in ("output", "o", "out") and i+1 < len(tokens):
        output = tokens[i+1]; i += 2
    elif low in ("fps", "f") and i+1 < len(tokens):
        fps = int(tokens[i+1]); i += 2
    elif tty_val is None and re.match(r'^(?:tty|vcsa)?\d+$', tok, re.I):
        tty_val = tok; i += 1
    else:
        print(f"ts arguement does NOT exist")
        sys.exit(1)

if screenshot and record:
    print("you can't do that bro")
    sys.exit(1)

tty = resolve_tty(tty_val if tty_val is not None else "3")

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
if output is None:
    output = f"tty_{timestamp}.png" if screenshot else f"tty_{timestamp}.mp4"

CGA = np.array([
    (0,0,0),(0,0,170),(0,170,0),(0,170,170),
    (170,0,0),(170,0,170),(170,170,0),(170,170,170),
    (85,85,85),(85,85,255),(85,255,85),(85,255,255),
    (255,85,85),(255,85,255),(255,255,85),(255,255,255),
], dtype=np.uint8)

with open(os.path.join(SCRIPT_DIR, "ttyfont.bin"),"rb") as f:
    fontdata = f.read()

magic = struct.unpack('<I', fontdata[:4])[0]
if magic == 0x72b54a86:
    hdrsize, flags, glyphs, bpg, h, w = struct.unpack('<IIIIII', fontdata[8:32])
else:
    mode, h = fontdata[2], fontdata[3]
    hdrsize, bpg, w = 4, h, 8

CELL_W, CELL_H = w, h
glyphdata = fontdata[hdrsize:]

glyph_masks = np.zeros((256, CELL_H, CELL_W), dtype=bool)
for idx in range(256):
    glyph = glyphdata[idx*bpg:(idx+1)*bpg]
    for row in range(h):
        byte = glyph[row] if row < len(glyph) else 0
        for col in range(w):
            if (byte >> (7-col)) & 1:
                glyph_masks[idx, row, col] = True

d = open(tty,"rb").read()
rows, cols = d[0], d[1]
W, H = cols*CELL_W, rows*CELL_H

img = np.zeros((H, W, 3), dtype=np.uint8)
prev_chars = np.full(rows*cols, -1, dtype=np.int16)
prev_attrs = np.full(rows*cols, -1, dtype=np.int16)
prev_blink = False
prev_cx, prev_cy = -1, -1

def capture(tty, blink):
    global img, prev_chars, prev_attrs, prev_blink, prev_cx, prev_cy

    d = open(tty,"rb").read()
    rows, cols = d[0], d[1]
    cx, cy = d[2], d[3]
    cells = d[4:4+rows*cols*2]

    chars = np.frombuffer(cells, dtype=np.uint8)[0::2].astype(np.int16)
    attrs = np.frombuffer(cells, dtype=np.uint8)[1::2].astype(np.int16)
    changed = np.where((chars != prev_chars) | (attrs != prev_attrs))[0]

    if len(changed) > 0:
        fg = CGA[attrs[changed] & 0x0f]
        bg = CGA[(attrs[changed] >> 4) & 0x07]
        masks = glyph_masks[chars[changed]]

        cell_imgs = np.where(
            masks[..., np.newaxis],
            fg[:, np.newaxis, np.newaxis, :],
            bg[:, np.newaxis, np.newaxis, :]
        )

        xs = (changed % cols) * CELL_W
        ys = (changed // cols) * CELL_H
        for k in range(len(changed)):
            img[ys[k]:ys[k]+CELL_H, xs[k]:xs[k]+CELL_W] = cell_imgs[k]

    if prev_cx >= 0:
        old_i = prev_cy * cols + prev_cx
        if old_i < len(chars):
            c, a = chars[old_i], attrs[old_i]
            fg = CGA[a & 0x0f]
            bg = CGA[(a >> 4) & 0x07]
            mask = glyph_masks[c]
            cell = np.where(mask[..., np.newaxis], fg, bg)
            x, y = prev_cx * CELL_W, prev_cy * CELL_H
            img[y:y+CELL_H, x:x+CELL_W] = cell


    if blink and 0 <= cx < cols and 0 <= cy < rows:
        cursor_i = cy * cols + cx
        if cursor_i < len(attrs):
            fg = CGA[attrs[cursor_i] & 0x0f]
            img[cy*CELL_H+CELL_H-2:cy*CELL_H+CELL_H, cx*CELL_W:cx*CELL_W+CELL_W] = fg

    prev_chars[:] = chars
    prev_attrs[:] = attrs
    prev_blink = blink
    prev_cx, prev_cy = cx, cy

    return img.tobytes()

if screenshot:
    frame = capture(tty, blink=False)
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{W}x{H}",
        "-pix_fmt", "rgb24",
        "-i", "-",
        output
    ]
    proc = subprocess.run(cmd, input=frame, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"screenshot saved to {output}")
    sys.exit(0)

cmd = [
    "ffmpeg", "-y",
    "-f", "rawvideo",
    "-vcodec", "rawvideo",
    "-s", f"{W}x{H}",
    "-pix_fmt", "rgb24",
    "-r", str(fps),
    "-i", "-",
    "-vcodec", "libx264",
    "-pix_fmt", "yuv420p",
    "-crf", "18",
    output
]

proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

running = True
def stop(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, stop)

print(f"recording {tty} to {output} at {fps}fps, ctrl+c to stop")
frames = 0
while running:
    t = time.time()
    blink = int(time.time() * 2) % 2 == 0
    proc.stdin.write(capture(tty, blink))
    frames += 1
    elapsed = time.time() - t
    sleep = (1/fps) - elapsed
    if sleep > 0:
        time.sleep(sleep)
    else:
        print(f"haha your cpu can't keep up with {fps}fps")

proc.stdin.close()
proc.wait()
print(f"{frames} frames saved to {output} as video")
