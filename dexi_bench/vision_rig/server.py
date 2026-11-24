"""Vision test rig: rogbeast serves test images to an iPad held in front of a DEXI camera.

    python -m uvicorn dexi_bench.vision_rig.server:app --host 0.0.0.0 --port 8000

iPad: Safari -> http://<rogbeast LAN IP>:8000 , Add to Home Screen for full screen.
The page polls /api/state and shows whatever image the server says, so a script
(score.py) or a person controls the sequence. Taps: right half = next, left half =
previous, long-press = toggle the info overlay (hidden by default so text never
reaches the camera).

It loops on its own (every DEFAULT_INTERVAL s). Any show/next/prev command holds
the screen for ?hold= seconds (default 30); when no command arrives for that long,
looping resumes, so a stopped or crashed script can never leave the screen frozen.
A script that drives the sequence (score.py) just keeps sending commands.

API
  GET  /api/state                          current item, interval, hold
  POST /api/show/{id}?hold=30              show item id and hold it
  POST /api/next?hold=30 | /api/prev?hold=30
  POST /api/auto?interval=10               loop interval in s (0 = stop looping)
  GET  /api/manifest                       all items with expectations
  GET  /control                            control page (thumbnails, prev/next, loop)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

HERE = Path(__file__).parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())
DEFAULT_INTERVAL = 10.0
DEFAULT_HOLD = 30.0
STATE = {"index": 0, "interval": DEFAULT_INTERVAL, "changed_at": time.time(), "seq": 0, "hold_until": 0.0}
LOCK = threading.Lock()
app = FastAPI(title="DEXI vision rig")


def _set(i: int, hold: float = 0.0) -> dict:
    with LOCK:
        if hold > 0:
            STATE["hold_until"] = time.time() + hold
        STATE["index"] = i % len(MANIFEST)
        STATE["changed_at"] = time.time()
        STATE["seq"] += 1
        return _state()


def _state() -> dict:
    it = MANIFEST[STATE["index"]]
    return {"index": STATE["index"], "count": len(MANIFEST), "item": it,
            "interval": STATE["interval"], "changed_at": STATE["changed_at"], "seq": STATE["seq"],
            "held_for": max(0.0, round(STATE["hold_until"] - time.time(), 1))}


def _auto_loop() -> None:
    while True:
        time.sleep(0.2)
        iv = STATE["interval"]
        now = time.time()
        if iv > 0 and now >= STATE["hold_until"] and now - STATE["changed_at"] >= iv:
            _set(STATE["index"] + 1)


threading.Thread(target=_auto_loop, daemon=True).start()


@app.get("/api/state")
def state():
    with LOCK:
        return _state()


@app.get("/api/manifest")
def manifest():
    return MANIFEST


@app.post("/api/show/{i}")
def show(i: int, hold: float = DEFAULT_HOLD):
    if not 0 <= i < len(MANIFEST):
        raise HTTPException(404, "no such item")
    return _set(i, hold)


@app.post("/api/next")
def nxt(hold: float = DEFAULT_HOLD):
    return _set(STATE["index"] + 1, hold)


@app.post("/api/prev")
def prev(hold: float = DEFAULT_HOLD):
    return _set(STATE["index"] - 1, hold)


@app.post("/api/auto")
def auto(interval: float = DEFAULT_INTERVAL):
    with LOCK:
        STATE["interval"] = max(0.0, interval)
        STATE["changed_at"] = time.time()
        STATE["hold_until"] = 0.0
        return _state()


@app.get("/img/{name}")
def img(name: str):
    p = (HERE / "images" / name).resolve()
    if p.parent != (HERE / "images").resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "max-age=3600"})


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>DEXI vision rig</title>
<style>
html,body{margin:0;height:100%;background:#fff;overflow:hidden;-webkit-user-select:none;user-select:none;touch-action:manipulation}
#img{position:fixed;inset:0;width:100%;height:100%;object-fit:contain}
#hud{position:fixed;left:8px;bottom:8px;font:14px -apple-system,sans-serif;background:rgba(0,0,0,.7);color:#fff;
padding:6px 10px;border-radius:6px;display:none}
</style></head><body>
<img id="img" alt="">
<div id="hud"></div>
<script>
let seq=-1, hud=false, pressTimer=null;
const img=document.getElementById('img'), h=document.getElementById('hud');
async function poll(){
  try{
    const s=await (await fetch('/api/state',{cache:'no-store'})).json();
    if(s.seq!==seq){ seq=s.seq; img.src='/img/'+encodeURIComponent(s.item.file); }
    h.textContent=(s.index+1)+'/'+s.count+'  '+s.item.file+(s.interval>0?'  auto '+s.interval+'s':'');
  }catch(e){}
  setTimeout(poll,300);
}
document.addEventListener('touchstart',e=>{pressTimer=setTimeout(()=>{hud=!hud;h.style.display=hud?'block':'none';pressTimer=null;},700);},{passive:true});
document.addEventListener('touchend',e=>{
  if(pressTimer){clearTimeout(pressTimer);pressTimer=null;
    const x=e.changedTouches[0].clientX; fetch(x>innerWidth/2?'/api/next':'/api/prev',{method:'POST'});}
});
document.addEventListener('click',e=>{ if(!('ontouchstart' in window)) fetch(e.clientX>innerWidth/2?'/api/next':'/api/prev',{method:'POST'}); });
poll();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


CONTROL = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vision rig control</title>
<style>
body{font:15px -apple-system,Segoe UI,sans-serif;margin:12px;background:#f6f6f4;color:#111}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
button{font-size:15px;padding:8px 14px;border-radius:8px;border:1px solid #bbb;background:#fff}
#now{font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px}
.cell{background:#fff;border:2px solid #ddd;border-radius:8px;padding:4px;text-align:center;font-size:11px;cursor:pointer}
.cell.on{border-color:#2a78d6}
.cell img{width:100%;aspect-ratio:1;object-fit:contain}
</style></head><body>
<div class="bar"><button onclick="go('/api/prev?hold=600')">&larr; Prev</button>
<button onclick="go('/api/next?hold=600')">Next &rarr;</button>
<span>Loop every <input id="iv" type="number" value="10" min="0" style="width:4em"> s</span>
<button onclick="go('/api/auto?interval='+document.getElementById('iv').value)">Loop</button>
<button onclick="go('/api/auto?interval=0')">Stop loop</button></div>
<div id="now"></div><div class="grid" id="grid"></div>
<script>
async function go(u){await fetch(u,{method:'POST'});tick();}
async function load(){
  const m=await (await fetch('/api/manifest')).json();
  document.getElementById('grid').innerHTML=m.map(it=>
    `<div class="cell" id="c${it.id}" onclick="go('/api/show/${it.id}?hold=600')"><img src="/img/${encodeURIComponent(it.file)}" loading="lazy"><div>${it.id}: ${it.file.replace('.png','')}</div></div>`).join('');
  tick();
}
async function tick(){
  const s=await (await fetch('/api/state',{cache:'no-store'})).json();
  document.getElementById('now').textContent=`Showing ${s.index}: ${s.item.file}  |  `+
    (s.held_for>0?`held ${Math.round(s.held_for)} s more`:(s.interval>0?`looping every ${s.interval} s`:'loop stopped'));
  document.querySelectorAll('.cell').forEach(c=>c.classList.toggle('on',c.id==='c'+s.index));
}
load(); setInterval(tick,1000);
</script></body></html>"""


@app.get("/control", response_class=HTMLResponse)
def control():
    return CONTROL
