"""Vision test rig: rogbeast serves test images to an iPad held in front of a DEXI camera.

    python -m uvicorn dexi_bench.vision_rig.server:app --host 0.0.0.0 --port 8000

iPad: Safari -> http://<rogbeast LAN IP>:8000 , Add to Home Screen for full screen.
The page polls /api/state and shows whatever image the server says, so a script
(score.py) or a person controls the sequence. Taps: right half = next, left half =
previous, long-press = toggle the info overlay (hidden by default so text never
reaches the camera).

API
  GET  /api/state                 current item, mode, interval
  POST /api/show/{id}             show item id
  POST /api/next | /api/prev
  POST /api/auto?interval=5       auto-advance every N s (interval=0 stops)
  GET  /api/manifest              all items with expectations
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
STATE = {"index": 0, "interval": 0.0, "changed_at": time.time(), "seq": 0}
LOCK = threading.Lock()
app = FastAPI(title="DEXI vision rig")


def _set(i: int) -> dict:
    with LOCK:
        STATE["index"] = i % len(MANIFEST)
        STATE["changed_at"] = time.time()
        STATE["seq"] += 1
        return _state()


def _state() -> dict:
    it = MANIFEST[STATE["index"]]
    return {"index": STATE["index"], "count": len(MANIFEST), "item": it,
            "interval": STATE["interval"], "changed_at": STATE["changed_at"], "seq": STATE["seq"]}


def _auto_loop() -> None:
    while True:
        time.sleep(0.2)
        iv = STATE["interval"]
        if iv > 0 and time.time() - STATE["changed_at"] >= iv:
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
def show(i: int):
    if not 0 <= i < len(MANIFEST):
        raise HTTPException(404, "no such item")
    return _set(i)


@app.post("/api/next")
def nxt():
    return _set(STATE["index"] + 1)


@app.post("/api/prev")
def prev():
    return _set(STATE["index"] - 1)


@app.post("/api/auto")
def auto(interval: float = 5.0):
    with LOCK:
        STATE["interval"] = max(0.0, interval)
        STATE["changed_at"] = time.time()
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
