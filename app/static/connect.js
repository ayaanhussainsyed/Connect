/* Connect: show the work while it happens.
 *
 * - A thin bar at the top while a page or an answer loads.
 * - Forms with data-steps (moving to the next day, joining): a card lists what Connect is doing, one
 *   step at a time, each with the model or rule behind it. The request is sent while the last step runs,
 *   so the last spinner keeps going until the server has really finished.
 * - Buttons and replies on the phone with data-steps: "typing…" in the thread, and the same step list
 *   under it until the answer comes back.
 * data-steps is JSON: [["What Connect does", "the model or rule behind it"], ...]
 */
(() => {
  "use strict";
  const STEP_MS = 520;

  // --- top bar ---------------------------------------------------------------
  const bar = document.createElement("div");
  bar.className = "toploader";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(bar));
  let busy = 0, grow = null, width = 0;
  function barStart() {
    busy += 1;
    if (busy > 1) return;
    width = 8; bar.style.width = width + "%"; bar.classList.add("is-on");
    clearInterval(grow);
    grow = setInterval(() => { width = Math.min(width + (90 - width) * 0.08, 90); bar.style.width = width + "%"; }, 180);
  }
  function barStop() {
    busy = Math.max(0, busy - 1);
    if (busy) return;
    clearInterval(grow);
    bar.style.width = "100%";
    setTimeout(() => { bar.classList.remove("is-on"); bar.style.width = "0"; }, 260);
  }
  window.addEventListener("beforeunload", () => { busy = 0; barStart(); });
  window.addEventListener("pageshow", (e) => {
    busy = 1; barStop();
    if (e.persisted) {     // back to a page from the browser's cache: clear the old working card
      document.querySelectorAll(".overlay").forEach((o) => o.remove());
      document.querySelectorAll("form[data-sent]").forEach((f) => { delete f.dataset.sent; });
    }
  });

  // --- step lists --------------------------------------------------------------
  function stepsOf(el) {
    if (!el || !el.dataset || !el.dataset.steps) return null;
    try { const s = JSON.parse(el.dataset.steps); return Array.isArray(s) && s.length ? s : null; } catch (e) { return null; }
  }
  function render(list, steps) {
    list.innerHTML = "";
    return steps.map(([label, tech]) => {
      const li = document.createElement("li");
      li.className = "is-wait";
      const mark = document.createElement("span"); mark.className = "work__mark";
      const body = document.createElement("span");
      const l = document.createElement("span"); l.textContent = label; body.appendChild(l);
      if (tech) { const t = document.createElement("span"); t.className = "work__tech"; t.textContent = tech; body.appendChild(t); }
      li.append(mark, body);
      list.appendChild(li);
      return li;
    });
  }
  function setState(li, state) {
    li.className = "is-" + state;
    li.firstChild.innerHTML = state === "run" ? '<span class="spin"></span>' : "";
  }
  // Runs the steps one by one; the last one keeps spinning until finish() (the server has answered).
  function run(items, onLast) {
    let i = 0, timer = null;
    const tick = () => {
      if (i > 0) setState(items[i - 1], "done");
      setState(items[i], "run");
      if (i === items.length - 1) { if (onLast) onLast(); return; }
      i += 1;
      timer = setTimeout(tick, STEP_MS);
    };
    tick();
    return { finish() { clearTimeout(timer); items.forEach((li) => setState(li, "done")); } };
  }

  // --- forms that move time (or join): a full-screen card of the day's work -----
  document.addEventListener("submit", (e) => {
    const form = e.target;
    const steps = stepsOf(form);
    if (!steps || form.dataset.sent || form.hasAttribute("hx-post") || form.hasAttribute("hx-get")) return;
    e.preventDefault();
    form.dataset.sent = "1";
    const ov = document.createElement("div");
    ov.className = "overlay";
    ov.innerHTML = '<div class="overlay__card" role="status" aria-live="polite"><p class="kicker"></p>' +
                   '<p class="overlay__title mt-1"></p><ul class="work mt-3"></ul></div>';
    ov.querySelector(".kicker").textContent = form.dataset.stepsKicker || "Connect is working";
    ov.querySelector(".overlay__title").textContent = form.dataset.stepsTitle || "";
    document.body.appendChild(ov);
    const items = render(ov.querySelector(".work"), steps);
    run(items, () => { barStart(); HTMLFormElement.prototype.submit.call(form); });
  }, true);

  // --- the phone: typing…, and the steps under the thread ----------------------
  function isPoll(el) { return el && el.hasAttribute && el.hasAttribute("data-poll"); }
  document.addEventListener("htmx:beforeRequest", (e) => {
    const el = e.detail.elt;
    if (!isPoll(el)) barStart();
    const steps = stepsOf(el);
    const box = document.getElementById("work");
    if (!steps || !box) return;
    box.hidden = false;
    clearTimeout(box._hide);
    box._run = run(render(box.querySelector(".work"), steps));
    const thread = document.getElementById("thread");
    if (thread && !thread.querySelector(".typing")) {
      const t = document.createElement("div");
      t.className = "typing"; t.setAttribute("aria-label", "Connect is typing");
      t.innerHTML = "<span></span><span></span><span></span>";
      thread.appendChild(t);
      thread.scrollTop = thread.scrollHeight;
    }
  });
  document.addEventListener("htmx:afterRequest", (e) => {
    const el = e.detail.elt;
    if (!isPoll(el)) barStop();
    const box = document.getElementById("work");
    if (!box || !box._run || !stepsOf(el)) return;
    box._run.finish(); box._run = null;
    box._hide = setTimeout(() => { box.hidden = true; }, 2600);
  });
})();
