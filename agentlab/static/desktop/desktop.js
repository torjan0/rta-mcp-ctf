"use strict";

(() => {
  const root = document.documentElement;
  const dock = document.querySelector("#dock");
  const workspace = document.querySelector("#workspace");
  const activities = document.querySelector("#activities");
  const notice = document.querySelector("#notice");
  const compact = matchMedia("(max-width: 799px)");
  const key = root.dataset.challenge;
  const windows = new Map();
  let top = 1;
  let noticeTimer;
  let status;
  let lastCheck = 0;
  let pending;
  let ready = false;
  let recordNotification;
  const prefix = "rta.desktop." + root.dataset.owner + "." + key + ".";
  const memory = new Map();
  const read = (name, fallback) => WindowTools.read("localStorage", prefix + name, memory.get(name) ?? fallback);
  function write(name, value) {
    memory.set(name, value);
    WindowTools.write("localStorage", prefix + name, value);
  }

  function saveLayout() {
    if (!ready || compact.matches) return;
    const layout = {};
    for (const [id, {panel, launch}] of windows) {
      const style = panel.style;
      layout[id] = {open: !panel.hidden, running: launch.classList.contains("running"),
        max: panel.classList.contains("maximized"), z: Number(style.zIndex),
        rect: [style.left, style.top, style.width, style.height].map(v => v ? parseFloat(v) : null)};
    }
    write("layout", layout);
  }

  function rect(panel) {
    return {x: panel.offsetLeft, y: panel.offsetTop, width: panel.offsetWidth, height: panel.offsetHeight};
  }

  function applyRect(panel, box) {
    for (const [prop, value] of Object.entries({left: box.x, top: box.y, width: box.width, height: box.height})) {
      panel.style[prop] = value + "px";
    }
  }

  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function icon(name) {
    const image = node("img");
    image.alt = "";
    image.draggable = false;
    image.src = "/static/desktop/icons/" + name + ".svg";
    return image;
  }

  function control(label, image, action) {
    const button = node("button", undefined, "control");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.append(icon(image));
    button.addEventListener("click", action);
    return button;
  }

  function notify(message) {
    recordNotification?.(message);
    notice.textContent = message;
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 6500);
  }

  function focus(app) {
    const {panel, launch} = windows.get(app);
    if (panel.classList.contains("focused")) return;
    for (const win of windows.values()) {
      win.panel.classList.remove("focused");
      win.launch.removeAttribute("aria-current");
      if (compact.matches) win.panel.inert = win.panel !== panel;
    }
    panel.classList.add("focused");
    panel.style.zIndex = String(++top);
    launch.setAttribute("aria-current", "true");
    saveLayout();
  }

  function open(app) {
    const win = windows.get(app);
    if (!win) return;
    activities.open = false;
    win.panel.hidden = false;
    win.panel.inert = false;
    win.launch.classList.add("running");
    win.launch.setAttribute("aria-expanded", "true");
    focus(app);
    win.panel.focus({preventScroll: true});
    win.panel.dispatchEvent(new Event("desktopopen"));
    saveLayout();
  }

  function hide(app, close) {
    const {panel, launch} = windows.get(app);
    panel.classList.remove("focused");
    panel.hidden = true;
    launch.removeAttribute("aria-current");
    if (close) launch.classList.remove("running");
    if (close && app === "mission") write("mission-closed", true);
    launch.setAttribute("aria-expanded", "false");
    panel.dispatchEvent(new Event("desktophide"));
    const next = [...windows].filter(([, win]) => !win.panel.hidden)
      .sort((a, b) => Number(b[1].panel.style.zIndex) - Number(a[1].panel.style.zIndex))[0];
    if (next) {
      focus(next[0]);
      next[1].panel.focus();
    } else {
      launch.focus();
    }
    saveLayout();
  }

  function place(panel, x, y) {
    const width = workspace.clientWidth;
    const height = workspace.clientHeight;
    panel.style.left = Math.max(0, Math.min(x, width - panel.offsetWidth)) + "px";
    panel.style.top = Math.max(0, Math.min(y, height - panel.offsetHeight)) + "px";
  }

  function draggable(bar, panel) {
    let start;
    bar.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.target.closest("button") || compact.matches ||
          panel.classList.contains("maximized")) return;
      start = {x: event.clientX - panel.offsetLeft, y: event.clientY - panel.offsetTop, moved: false};
      bar.setPointerCapture(event.pointerId);
    });
    bar.addEventListener("pointermove", event => {
      if (start) {
        start.moved = true;
        place(panel, event.clientX - start.x, event.clientY - start.y);
      }
    });
    bar.addEventListener("pointerup", event => {
      if (!start?.moved) return;
      const bounds = workspace.getBoundingClientRect();
      const snap = WindowTools.snap(event.clientX - bounds.left, event.clientY - bounds.top, bounds.width, bounds.height);
      if (snap?.width === bounds.width) windows.get(panel.dataset.app).maximize(true);
      else if (snap) applyRect(panel, snap);
      saveLayout();
    });
    bar.addEventListener("lostpointercapture", () => { start = null; });
  }

  function resizable(panel) {
    for (const edge of ["n", "s", "e", "w", "nw", "ne", "sw", "se"]) {
      const handle = node("div", undefined, "resize " + edge);
      let start;
      handle.addEventListener("pointerdown", event => {
        if (event.button !== 0 || compact.matches || panel.classList.contains("maximized")) return;
        start = {rect: rect(panel), x: event.clientX, y: event.clientY};
        handle.setPointerCapture(event.pointerId);
        event.preventDefault();
      });
      handle.addEventListener("pointermove", event => {
        if (start) applyRect(panel, WindowTools.resize(start.rect, edge,
          event.clientX - start.x, event.clientY - start.y, workspace.clientWidth, workspace.clientHeight));
      });
      handle.addEventListener("lostpointercapture", () => { start = null; saveLayout(); });
      panel.append(handle);
    }
  }

  ShellApps.createPanels(node, workspace);
  for (const panel of workspace.querySelectorAll("[data-app]")) {
    const app = panel.dataset.app;
    const [image, caption] = (challengeUI.apps[app] || ShellApps.apps[app]);
    const title = panel.getAttribute("aria-label");
    const launch = node("button", undefined, "launcher app-" + app);
    launch.type = "button";
    launch.title = title;
    launch.setAttribute("aria-label", title);
    launch.setAttribute("aria-controls", "window-" + app);
    launch.setAttribute("aria-expanded", "false");
    const tile = node("span", undefined, "tile");
    tile.append(icon(image));
    launch.append(tile, node("span", caption));
    dock.append(launch);

    panel.id = "window-" + app;
    panel.tabIndex = -1;
    panel.setAttribute("role", "region");
    const titlebar = node("header", undefined, "titlebar");
    titlebar.append(icon(image), node("span", title, "window-title"));
    const maximize = control("Maximize", "maximize-2", toggleMax);
    function toggleMax() { setMax(!panel.classList.contains("maximized")); }
    function setMax(large) {
      panel.classList.toggle("maximized", large);
      const label = large ? "Restore size" : "Maximize";
      maximize.setAttribute("aria-label", label);
      maximize.title = label;
      saveLayout();
    }
    titlebar.append(control("Minimize", "minus", () => hide(app, false)), maximize,
                    control("Close", "x", () => hide(app, true)));
    titlebar.addEventListener("dblclick", event => { if (!event.target.closest("button")) toggleMax(); });
    draggable(titlebar, panel);
    panel.prepend(titlebar);
    resizable(panel);
    windows.set(app, {panel, launch, maximize: setMax});
    launch.addEventListener("click", () => open(app));
    panel.addEventListener("pointerdown", () => focus(app), true);
    panel.addEventListener("focusin", () => focus(app));
    // Stagger secondary windows so the previous title bar stays reachable.
    panel.style.setProperty("--offset", ((windows.size - 1) % 4) * 16 + "px");
  }

  async function refresh(force = false) {
    if (pending) return force ? pending.then(() => refresh(true)) : pending;
    if (!force && (Date.now() - lastCheck < 2500 || document.hidden)) return;
    lastCheck = Date.now();
    pending = (async () => {
      try {
        const response = await fetch("/" + key + "/state", {cache: "no-store"});
        if (response.status === 401) { location.replace("/"); return; }
        if (!response.ok) throw new Error("Could not refresh workspace status.");
        const next = await response.json();
        if (key === "a5" && Number(root.dataset.expires) !== Math.trunc((next.workspace?.expires || 0) * 1000)) {
          location.reload();
          return;
        }
        status = next;
        window.dispatchEvent(new CustomEvent("desktopstatus", {detail: next}));
      } catch (error) {
        notify(error.message);
      } finally {
        pending = null;
      }
    })();
    return pending;
  }

  const clock = document.querySelector("#clock");
  const timer = document.querySelector("#remaining");
  const expires = Number(root.dataset.expires);
  const clockFormat = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit"});
  function tick() {
    clock.textContent = clockFormat.format(Date.now());
    if (!expires) return;
    const seconds = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
    timer.hidden = false;
    timer.textContent = "Workspace  " + Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    if (seconds === 0 && !document.hidden && Date.now() - lastCheck >= 15000) refresh();
  }

  function fit() {
    for (const {panel} of windows.values()) {
      panel.inert = compact.matches && !panel.classList.contains("focused");
      if (!compact.matches && !panel.hidden && panel.style.left && !panel.classList.contains("maximized")) {
        applyRect(panel, WindowTools.fit(rect(panel), workspace.clientWidth, workspace.clientHeight));
      }
    }
  }

  function keepDraft(form, fields) {
    const name = "draft." + root.dataset.expires;
    const saved = read(name, {});
    const values = () => Object.fromEntries(fields.map(field => [field.name, field.value]));
    const persist = () => write(name, fields.some(field => field.value !== field.defaultValue) ? values() : null);
    for (const field of fields) {
      if (typeof saved?.[field.name] === "string") field.value = saved[field.name];
      if (!form.contains(field)) field.addEventListener("input", persist);
    }
    form.addEventListener("input", persist);
    persist();
    return () => {
      write(name, null);
      for (const field of fields) field.defaultValue = field.value;
    };
  }

  async function action(form, progress, onSuccess) {
    const button = form.querySelector("button[type=submit], button:not([type])");
    let message = form.previousElementSibling;
    if (!message?.classList.contains("action-status")) {
      message = node("p", "", "action-status");
      message.setAttribute("role", "status");
      form.before(message);
    }
    if (form.dataset.running === "true") return false;
    form.dataset.running = "true";
    button.disabled = true;
    message.textContent = progress;
    try {
      const response = await fetch(form.action, {
        method: "POST", headers: {Accept: "application/json"},
        body: new URLSearchParams(new FormData(form)), cache: "no-store"
      });
      if (response.status === 401) { location.replace("/"); return false; }
      const result = await response.json();
      if (!response.ok || !result.ok) {
        message.textContent = result.error || "The action could not finish.";
        return false;
      }
      message.textContent = "";
      onSuccess?.();
      await refresh(true);
      return true;
    } catch {
      message.textContent = "The action could not finish. Try again.";
      return false;
    } finally {
      delete form.dataset.running;
      button.disabled = false;
      form.dispatchEvent(new Event("actioncomplete"));
    }
  }

  function workspaceAction(form) {
    const message = node("p", "", "note");
    message.setAttribute("role", "status");
    form.after(message);
    const button = form.querySelector("button");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (button.disabled) return;
      button.disabled = true;
      message.textContent = "Starting workspace…";
      try {
        const response = await fetch(form.action, {method: "POST", headers: {Accept: "application/json"},
          body: new URLSearchParams(new FormData(form))});
        if (response.status === 401) { location.replace("/"); return; }
        const result = await response.json();
        if (!response.ok || !result.ok) {
          message.textContent = result.error || "Could not start the workspace.";
          return;
        }
        write("draft." + root.dataset.expires, null);
        location.reload();
      } catch {
        message.textContent = "Could not start the workspace. Try again.";
      } finally {
        button.disabled = false;
      }
    });
  }

  const api = {node, icon, read, write, keepDraft, action, control, open, focus, hide, notify, refresh,
    panel: app => windows.get(app)?.panel, launcher: app => windows.get(app)?.launch,
    state: () => status, body: app => windows.get(app)?.panel.querySelector(".window-body")};
  for (const form of workspace.querySelectorAll('form[action="/a5/new"]')) workspaceAction(form);
  challengeUI.setup(api);
  const openTrash = ShellApps.setup(api, challengeUI);
  function tidy() {
    if (compact.matches) return;
    let index = 0;
    for (const win of windows.values()) {
      if (win.panel.hidden) continue;
      win.maximize(false);
      const offset = 16 + (index++ % 8) * 25;
      place(win.panel, offset, offset);
    }
    saveLayout();
  }
  recordNotification = mountDesktopChrome(api, tidy, openTrash);
  const layout = compact.matches ? null : read("layout", null);
  if (layout && typeof layout === "object") {
    for (const [id, entry] of Object.entries(layout).filter(([id, entry]) => windows.has(id) && entry)
      .sort((a, b) => a[1].z - b[1].z)) {
      const win = windows.get(id);
      if (Array.isArray(entry.rect)) {
        ["left", "top", "width", "height"].forEach((prop, i) => {
          if (Number.isFinite(entry.rect[i])) win.panel.style[prop] = entry.rect[i] + "px";
        });
      }
      win.maximize(entry.max === true);
      if (entry.open) open(id);
      else if (entry.running) win.launch.classList.add("running");
    }
  }
  const requested = location.hash.slice(1);
  if (notice.textContent && (api.panel(requested) || api.panel(root.dataset.initial))) {
    open(api.panel(requested) ? requested : root.dataset.initial);
  } else if (!read("mission-closed", false) && challengeUI.mission) open("mission");
  else if (windows.has(requested)) open(requested);
  else if (!layout || !Object.keys(layout).some(id => windows.has(id)) ||
           (root.dataset.initial === "mission" && !api.panel("editor"))) open(root.dataset.initial);
  fit();
  ready = true;
  saveLayout();
  if (notice.textContent) notify(notice.textContent);
  tick();
  setInterval(tick, 1000);
  window.addEventListener("resize", fit);
  // Reauthenticate on history restoration before showing a previous player's desktop.
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  window.addEventListener("focus", refresh);
  if (key === "a4") setInterval(() => {
    if (!document.hidden && ["browser", "registry", "listener"].some(app =>
      windows.has(app) && !windows.get(app).panel.hidden)) refresh();
  }, 15000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  document.addEventListener("pointerdown", event => { if (!activities.contains(event.target)) activities.open = false; });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && activities.open) {
      activities.open = false;
      activities.querySelector("summary").focus();
    }
  });
  refresh();
})();
