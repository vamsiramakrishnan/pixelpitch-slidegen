# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Companion layer: perceived-latency engagement for the A2UI surface.

Long generations feel short when something alive is talking to you. This
module turns real pipeline events into a tamagotchi-style companion voice
(one line per milestone, never fabricated progress) and serves a tiny
dependency-free dino game as an HTML fragment the user can play while the
deck builds. Every line corresponds to a true event; nothing is faked.
"""

from __future__ import annotations

# Companion lines per event kind. Rotated deterministically so runs are
# reproducible and tests are stable. {slide}/{tool}/{n} are filled from real
# context; lines never claim progress that did not happen.
_COMPANION: dict[str, list[str]] = {
    "start": [
        "Egg cracked. Your deck-pet hatched and is reading the brief.",
        "Your designer-pet stretched, sniffed the template, and started.",
        "Hatched! First move: sniffing the template's theme colours.",
    ],
    "thinking": [
        "Your pet is thinking hard about slide {slide} (ears wiggling).",
        "Deep thought bubbles over slide {slide}. Good ones take a moment.",
        "Your pet is pacing around slide {slide}, deciding the layout.",
    ],
    "tool": [
        "Nom nom — your pet just used {tool}.",
        "{tool} crunched. Your deck-pet is chewing on the next bite.",
        "Your pet buried {tool} in the sandbox and kept digging.",
    ],
    "slide_ready": [
        "Slide {slide} hopped out of the shell, fully drafted.",
        "Slide {slide} took its first steps. Adorable. And on-template.",
        "New feather for slide {slide}. It grows fast when fed real data.",
    ],
    "gates": [
        "Your pet is checking slide {slide} against the brand rules. Strict!",
        "Tick-tock: lint, contrast, template fidelity. Your pet is fussy.",
        "Gate-checking slide {slide}. Your pet rejects anything off-brand.",
    ],
    "revise": [
        "Slide {slide} got a gentle scolding. It is being redrawn.",
        "Your pet found a flaw on slide {slide} and is fixing it properly.",
        "Back to the drawing board for slide {slide}. Standards!",
    ],
    "polish": [
        "Final grooming: titles sharpened, whitespace fluffed.",
        "Your pet is polishing every accent so exactly one thing shines.",
        "Feather preening complete on the whole deck.",
    ],
    "done": [
        "Your deck-pet grew up into a full deck. So proud.",
        "Metamorphosis complete: brief in, argument out. Enjoy!",
        "All {n} slides groomed, gated, and ready to present.",
    ],
}


def companion_line(kind: str, *, slide: int | None = None, tool: str | None = None, n: int | None = None, tick: int = 0) -> str:
    """One companion line for a real event. ``tick`` rotates the voice."""
    lines = _COMPANION.get(kind) or _COMPANION["thinking"]
    line = lines[tick % len(lines)]
    return line.format(
        slide=slide if slide is not None else "the next",
        tool=tool or "a tool",
        n=n if n is not None else "",
    ).rstrip()


class CompanionThrottle:
    """Rate-limit companion lines so the feed stays alive, not noisy."""

    def __init__(self, min_interval_seconds: float = 2.5):
        self._min = min_interval_seconds
        self._last = 0.0
        self._tick = 0

    def allow(self, now: float | None = None) -> int | None:
        """Return the tick to use, or None if suppressed by the interval."""
        import time

        now = now if now is not None else time.monotonic()
        if now - self._last < self._min:
            return None
        self._last = now
        self._tick += 1
        return self._tick - 1


def dino_fragment(status_line: str = "Your deck is being raised…") -> str:
    """Self-contained dino-runner fragment with a companion status bubble.

    A pixel dino jumps cacti (small singles, tall barrels, double clumps),
    speed ramps with score, best score persists, night falls every 700
    points. No dependencies, no network: canvas + keyboard/tap. Served
    through the same fragment mechanism as slide previews.
    """
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{margin:0;background:#f7f7f5;font:13px system-ui,sans-serif;"
        "color:#2d2a26;display:flex;flex-direction:column;align-items:center;"
        "padding:10px;gap:8px}"
        ".bubble{background:#fff;border:1px solid #e3e0da;border-radius:14px;"
        "padding:8px 14px;max-width:420px;text-align:center}"
        "canvas{background:#fff;border:1px solid #e3e0da;border-radius:10px;"
        "image-rendering:pixelated;max-width:100%;outline:none}"
        ".hint{color:#8a857c;font-size:11px}"
        "</style></head><body>"
        f"<div class='bubble'>{status_line}</div>"
        "<canvas id='c' width='440' height='150' tabindex='0'></canvas>"
        "<div class='hint'>space / tap to jump the cacti — your deck builds "
        "while you play</div>"
        "<script>"
        "const c=document.getElementById('c'),x=c.getContext('2d');"
        "const G=0.62,J=-9.6,GY=126;"
        "let y=GY,vy=0,duck=0,obs=[],sc=0,dead=false,t=0,spd=4,gap=0;"
        "let best=0;try{best=+localStorage.dinoBest||0}catch(e){}"
        "function reset(){y=GY;vy=0;duck=0;obs=[];sc=0;dead=false;t=0;"
        "spd=4;gap=60;c.focus()}"
        "function jump(){if(dead){reset();return}if(y>=GY){vy=J;duck=0}}"
        "addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();jump()}});"
        "c.addEventListener('pointerdown',jump);"
        "function cactus(o){"
        "x.fillStyle=o.n?'#0B7A45':'#0E0D26';"
        "const b=GY-o.h;"
        "x.fillRect(o.x+o.w/2-3,b,6,o.h);"
        "if(o.k>0){x.fillRect(o.x,b+o.h*0.35,4,4);x.fillRect(o.x,b+o.h*0.35,3,o.h*0.3)}"
        "if(o.k>1){x.fillRect(o.x+o.w-4,b+o.h*0.25,4,4);"
        "x.fillRect(o.x+o.w-1,b+o.h*0.25,3,o.h*0.35)}"
        "if(o.k>2){x.fillRect(o.x+1,b+o.h*0.55,3,3);"
        "x.fillRect(o.x-1,b+o.h*0.55,3,o.h*0.25)}}"
        "function dino(){"
        "const run=(t>>3)%2;const h=duck?12:18;const w=duck?22:16;"
        "x.fillStyle='#0E0D26';"
        "x.fillRect(18,y-h,w,h);"
        "x.fillRect(18+w-3,y-h+2,5,4);"
        "x.fillRect(18+w+1,y-h+3,2,2);"
        "x.fillStyle='#f7f7f5';x.fillRect(18+w-1,y-h+3,1,1);"
        "x.fillStyle='#1971ED';x.fillRect(18+w-2,y-h+8,6,2);"
        "x.fillStyle='#0E0D26';"
        "if(duck){x.fillRect(20+run*3,y-2,4,2)}"
        "else{x.fillRect(20,y-2,3,2);x.fillRect(25+run*2,y-2,3,2)}}"
        "function loop(){t++;"
        "if(!dead){"
        "vy+=G;y+=vy;if(y>GY){y=GY;vy=0}"
        "spd=4+Math.min(4,sc/300);"
        "gap-=spd;"
        "if(gap<=0){"
        "const k=1+Math.floor(Math.random()*3);"
        "const h=(k===1?26:k===2?20:30)+Math.random()*8;"
        "obs.push({x:450,w:10+k*7,h,k,n:Math.random()<0.25});"
        "gap=55+Math.random()*60-Math.min(25,sc/60)} "
        "obs.forEach(o=>o.x-=spd);obs=obs.filter(o=>o.x>-24);"
        "sc++;"
        "for(const o of obs){"
        "const dh=duck?12:18;"
        "if(o.x<34&&o.x+o.w>18&&y-dh<GY-o.h+3)die()}}}"
        "function die(){dead=true;if(sc>best){best=sc;"
        "try{localStorage.dinoBest=best}catch(e){}}}"
        "const night=Math.floor(sc/700)%2===1;"
        "x.fillStyle=night?'#171627':'#ffffff';x.fillRect(0,0,440,150);"
        "x.strokeStyle=night?'#3a3856':'#c9c4ba';"
        "x.beginPath();x.moveTo(0,GY+0.5);x.lineTo(440,GY+0.5);x.stroke();"
        "if(night){x.fillStyle='#f3ea5d';x.fillRect(400,14,10,10);"
        "x.fillStyle='#171627';x.fillRect(403,12,8,8)}"
        "else{x.fillStyle='#f3ea5d';x.beginPath();x.arc(410,20,9,0,7);x.fill()}"
        "for(let i=0;i<5;i++){x.fillStyle=night?'#2b2947':'#e9e6df';"
        "const cx=(i*97-(t*0.3)%97+440)%440;"
        "x.fillRect(cx,26+(i*23)%40,18,2)}"
        "obs.forEach(cactus);"
        "if(dead){x.fillStyle='#B3261E';x.font='bold 15px monospace';"
        "x.fillText('OUCH',190,58);x.fillStyle=night?'#a9a7c4':'#5f5b52';"
        "x.font='12px monospace';x.fillText('space to run again',170,76)}"
        "else dino();"
        "x.fillStyle=night?'#a9a7c4':'#5f5b52';x.font='12px monospace';"
        "x.fillText('HI '+String(best).padStart(5,'0')+'  '"
        "+String(sc).padStart(5,'0'),330,18);"
        "requestAnimationFrame(loop);}"
        "c.focus();loop();"
        "</script></body></html>"
    )
