// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * <pp-deck> — the presentable half of a deck deliverable.
 *
 * The slides are the same 1280x720 documents slidify converts. This adds the
 * things a human needs to stand in front of them: scaling to the screen,
 * navigation, speaker notes, and print.
 *
 * The contract that makes it safe:
 *
 *   Every slide stays in the light DOM as a `.slide` child. All chrome and
 *   all scaling live in the shadow root. slidify parses HTML without running
 *   scripts, so it never sees the shadow root — it sees N untouched
 *   `.slide` elements at authored size and splits on them. That is why one
 *   file can be both the thing you present and the thing you convert, with no
 *   export-time surgery and no second copy to keep in sync.
 *
 * Speaker notes come from each slide's `data-pptx-notes`, the same attribute
 * slidify reads into `slide.notes_slide`. One attribute, two consumers.
 *
 *   <pp-deck>
 *     <section class="slide" data-pptx-notes="Open on the number.">…</section>
 *     <section class="slide">…</section>
 *   </pp-deck>
 *   <script src="deck-stage.js"></script>
 *
 * Keys: arrows / space / PgUp / PgDn / Home / End to move, N for notes,
 * digits to jump. The `noscale` attribute renders 1:1 with no transform,
 * which is what a DOM probe needs so its geometry is the authored geometry.
 */

const DESIGN_W = 1280;
const DESIGN_H = 720;

const CHROME = `
:host { display: block; position: fixed; inset: 0; overflow: hidden;
        background: #111; --pp-scale: 1; }
:host([noscale]) { position: static; background: none; }

#canvas { position: absolute; top: 50%; left: 50%; width: var(--pp-w);
          height: var(--pp-h); transform: translate(-50%, -50%) scale(var(--pp-scale));
          transform-origin: center center; background: #fff; }
:host([noscale]) #canvas { position: static; transform: none; }

/* Slides are light-DOM children; only their placement is ours. */
::slotted(.slide) { position: absolute !important; inset: 0 !important;
                    visibility: hidden; }
::slotted(.slide[data-deck-active]) { visibility: visible; }
:host([noscale]) ::slotted(.slide) { position: relative !important; }

#hud { position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%);
       font: 500 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
       color: #fff; background: rgba(0,0,0,.55); padding: 7px 12px;
       border-radius: 999px; letter-spacing: .04em; opacity: 0;
       transition: opacity .25s; pointer-events: none; }
#hud[data-show] { opacity: 1; }

#notes { position: absolute; right: 0; bottom: 0; top: 0; width: 340px;
         background: #16161a; color: #e8e6e1; padding: 24px 26px;
         font: 400 15px/1.55 ui-sans-serif, system-ui, sans-serif;
         overflow-y: auto; white-space: pre-wrap; display: none; }
:host([notes]) #notes { display: block; }
#notes b { display: block; margin-bottom: 12px; font: 600 11px/1 ui-monospace,
           Menlo, monospace; letter-spacing: .12em; text-transform: uppercase;
           color: #8b8b85; }

/* Print puts every slide on its own page at authored size. The stage drops
   its scale first, because a transform would otherwise shrink each page. */
@media print {
  :host { position: static; background: none; }
  #canvas { position: static; transform: none; width: auto; height: auto; }
  #hud, #notes { display: none; }
  ::slotted(.slide) { position: relative !important; visibility: visible !important;
                      break-after: page; }
  ::slotted(.slide:last-of-type) { break-after: auto; }
}
`;

class PixelpitchDeck extends HTMLElement {
  #slides = [];
  #index = 0;
  #hudTimer = 0;

  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML =
      `<style>${CHROME}</style><div id="canvas"><slot></slot></div>` +
      `<div id="hud"></div><aside id="notes"><b>Speaker notes</b><div id="note"></div></aside>`;

    this.#collect();
    root.querySelector("slot").addEventListener("slotchange", () => this.#collect());

    this.#fit();
    new ResizeObserver(() => this.#fit()).observe(document.documentElement);
    addEventListener("keydown", (e) => this.#key(e));

    // Print has to see unscaled, all-visible slides. The media query in the
    // shadow sheet covers layout; this covers the transform, which is set
    // from script and so is not reachable from a stylesheet.
    addEventListener("beforeprint", () => this.style.setProperty("--pp-scale", 1));
    addEventListener("afterprint", () => this.#fit());

    this.#go(Number(location.hash.slice(1)) - 1 || 0, false);
  }

  #collect() {
    this.#slides = [...this.children].filter((el) => el.classList.contains("slide"));
    this.#slides.forEach((slide, i) => {
      slide.setAttribute("data-screen-label", String(i + 1).padStart(2, "0"));
    });
    this.#go(Math.min(this.#index, Math.max(this.#slides.length - 1, 0)), false);
  }

  #fit() {
    const w = Number(this.getAttribute("width")) || DESIGN_W;
    const h = Number(this.getAttribute("height")) || DESIGN_H;
    this.style.setProperty("--pp-w", `${w}px`);
    this.style.setProperty("--pp-h", `${h}px`);
    if (this.hasAttribute("noscale")) return this.style.setProperty("--pp-scale", 1);
    const room = this.hasAttribute("notes") ? innerWidth - 340 : innerWidth;
    this.style.setProperty("--pp-scale", Math.min(room / w, innerHeight / h));
  }

  #go(next, announce = true) {
    if (!this.#slides.length) return;
    this.#index = Math.max(0, Math.min(next, this.#slides.length - 1));
    this.#slides.forEach((slide, i) => {
      slide.toggleAttribute("data-deck-active", i === this.#index);
    });
    const notes = this.#slides[this.#index].getAttribute("data-pptx-notes") || "";
    this.shadowRoot.getElementById("note").textContent =
      notes || "No notes on this slide.";
    location.hash = String(this.#index + 1);
    if (announce) this.#flash();
    this.dispatchEvent(
      new CustomEvent("slidechange", {
        bubbles: true,
        composed: true,
        detail: { index: this.#index, total: this.#slides.length },
      })
    );
  }

  #flash() {
    const hud = this.shadowRoot.getElementById("hud");
    hud.textContent = `${this.#index + 1} / ${this.#slides.length}`;
    hud.setAttribute("data-show", "");
    clearTimeout(this.#hudTimer);
    this.#hudTimer = setTimeout(() => hud.removeAttribute("data-show"), 1600);
  }

  #key(event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key;
    if (key === "ArrowRight" || key === "PageDown" || key === " ") this.#go(this.#index + 1);
    else if (key === "ArrowLeft" || key === "PageUp") this.#go(this.#index - 1);
    else if (key === "Home") this.#go(0);
    else if (key === "End") this.#go(this.#slides.length - 1);
    else if (key === "n" || key === "N") {
      this.toggleAttribute("notes");
      this.#fit();
    } else if (/^[1-9]$/.test(key)) this.#go(Number(key) - 1);
    else return;
    event.preventDefault();
  }
}

customElements.define("pp-deck", PixelpitchDeck);
