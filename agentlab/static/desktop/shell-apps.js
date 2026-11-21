"use strict";

function tabStrip(api, names, select) {
  const bar = api.node("div", undefined, "tabs");
  const buttons = names.map((name, index) => {
    const button = api.node("button", name);
    button.type = "button";
    button.addEventListener("click", () => show(index));
    bar.append(button);
    return button;
  });
  function show(index) {
    buttons.forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
    select(index);
  }
  return {bar, show, buttons};
}

const ShellApps = (() => {
  const apps = {
    mission: ["file-text", "Mission"], hints: ["activity", "Hints"],
    files: ["folder", "Files"], text: ["file-text", "Text viewer"]
  };

  function createPanels(node, workspace) {
    for (const [id, [, label]] of Object.entries(apps)) {
      if (workspace.querySelector('[data-app="' + id + '"]')) continue;
      const panel = node("section", undefined, "window");
      panel.hidden = true;
      panel.dataset.app = id;
      panel.setAttribute("aria-label", label);
      panel.append(node("div", undefined, "window-body"));
      workspace.append(panel);
    }
  }

  function setup(api, config) {
    const {node, open, body, read, write} = api;
    const button = (label, action) => {
      const b = node("button", label);
      b.type = "button";
      b.addEventListener("click", action);
      return b;
    };
    const mission = config.mission || [];
    const section = node("section");
    if (config.backstory) {
      const story = node("p", undefined, "backstory");
      story.append(node("b", "Backstory. "), document.createTextNode(config.backstory));
      section.append(story);
    }
    for (const line of mission) section.append(node("p", line));
    const checklist = node("ul", undefined, "checklist");
    section.append(checklist);
    section.append(button(config.hasHints === false ? "Open Hints" :
      "Stuck? Open Hints. Each hint is free and opens one at a time.", () => open("hints")));
    body("mission").prepend(section);
    function progress() {
      checklist.replaceChildren();
      for (const [label, done] of config.progress?.(api) || []) {
        const item = node("li", label);
        item.prepend(node("span", done ? "✓ " : "○ ", done ? "done" : ""));
        item.setAttribute("aria-label", label + (done ? " Complete" : " Pending"));
        checklist.append(item);
      }
    }
    progress();
    window.addEventListener("desktopstatus", progress);
    window.addEventListener("missionprogress", progress);

    const list = node("ol");
    const hintStatus = node("p");
    const next = button("Show next hint", () => {
      if (!hints) { loadHints(); return; }
      count = WindowTools.hintCount(count + 1, hints.length);
      write("hints", count);
      renderHints();
    });
    let hints;
    let count;
    let loading = false;
    function renderHints() {
      list.replaceChildren();
      for (const line of hints.slice(0, count)) {
        const li = node("li");
        line.split("`").forEach((part, i) =>
          li.append(i % 2 ? node("code", part) : document.createTextNode(part)));
        list.append(li);
      }
      hintStatus.textContent = hints.length ? count + " of " + hints.length + " hints open" :
        "No hints are available for this challenge.";
      next.hidden = !hints.length;
      next.disabled = count >= hints.length;
    }
    async function loadHints() {
      if (loading || hints) return;
      loading = true;
      next.disabled = true;
      try {
        const response = await fetch("/static/desktop/hints.json");
        if (!response.ok) throw new Error();
        const rows = (await response.json())[document.documentElement.dataset.challenge];
        if (!Array.isArray(rows) || rows.some(line => typeof line !== "string")) throw new Error();
        hints = rows;
        count = WindowTools.hintCount(read("hints", 0), hints.length);
        renderHints();
      } catch {
        hintStatus.textContent = "Could not load hints. Try again.";
        next.disabled = false;
      } finally {
        loading = false;
      }
    }
    body("hints").append(list, hintStatus, next);
    api.panel("hints").addEventListener("desktopopen", loadHints);

    const files = body("files");
    const tree = node("div");
    const fileTitle = node("div", "", "filepath");
    const text = node("pre");
    body("text").append(fileTitle, text);
    const entries = {"Desktop/mission.txt": () => [config.backstory, ...mission].filter(Boolean).join("\n\n"),
      ...config.files?.(api)};
    function showText(path) {
      if (!Object.hasOwn(entries, path) || typeof entries[path] !== "function") return;
      fileTitle.textContent = "~/" + path;
      text.textContent = entries[path]();
      write("viewer-file", path);
    }
    api.panel("text").addEventListener("desktopopen", () => showText(read("viewer-file", "Desktop/mission.txt")));
    function renderTree(trash = false) {
      tree.replaceChildren();
      if (trash) { tree.append(node("p", "Trash is empty.")); return; }
      const folders = new Map([["", tree]]);
      for (const [path, readFile] of Object.entries(entries)) {
        const parts = path.split("/");
        const name = parts.pop();
        let prefix = "";
        let parent = tree;
        for (const part of parts) {
          prefix += "/" + part;
          if (!folders.has(prefix)) {
            const folder = node("details");
            folder.append(node("summary", part));
            parent.append(folder);
            folders.set(prefix, folder);
          }
          parent = folders.get(prefix);
        }
        if (!name) continue;
        const file = node("button", name);
        file.type = "button";
        function view() {
          if (typeof readFile === "object") { readFile.edit(); return; }
          showText(path);
          open("text");
        }
        file.addEventListener("dblclick", view);
        file.addEventListener("click", event => { if (event.detail === 0) view(); });
        parent.append(file);
      }
    }
    files.append(button("Home", () => renderTree()), button("Trash", () => renderTree(true)), tree);
    renderTree();
    return () => { open("files"); renderTree(true); };
  }

  return {apps, createPanels, setup};
})();

function mountDesktopChrome(api, tidy, openTrash) {
  const {node, open} = api;
  const byId = id => document.getElementById(id);
  const splash = byId("splash");
  if (!WindowTools.read("sessionStorage", "rta.booted", false)) {
    WindowTools.write("sessionStorage", "rta.booted", true);
    splash.hidden = false;
    const dismiss = () => {
      splash.hidden = true;
      document.removeEventListener("keydown", skip, true);
    };
    function skip(event) {
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    }
    document.addEventListener("keydown", skip, true);
    splash.addEventListener("click", dismiss, {once: true});
    setTimeout(dismiss, 950);
  }

  const menu = byId("desktop-menu");
  const calendar = byId("calendar");
  const tray = byId("notifications");
  const bell = byId("bell");
  const clock = byId("calendar-toggle");
  const popups = [[tray, bell], [calendar, clock], [menu, null]];
  function closePopups(except) {
    for (const [panel, trigger] of popups) {
      if (panel === except) continue;
      panel.hidden = true;
      trigger?.setAttribute("aria-expanded", "false");
    }
  }
  function toggle(panel, trigger) {
    closePopups(panel);
    panel.hidden = !panel.hidden;
    trigger.setAttribute("aria-expanded", String(!panel.hidden));
  }
  document.addEventListener("pointerdown", event => {
    if (!popups.some(([panel, trigger]) => panel.contains(event.target) || trigger?.contains(event.target))) closePopups();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const active = popups.find(([panel]) => !panel.hidden);
    closePopups();
    active?.[1]?.focus();
  });

  const shortcuts = byId("shortcuts");
  for (const [label, name, action] of [
    ["Mission", "file-text", () => open("mission")], ["Hints", "activity", () => open("hints")],
    ["Files", "folder", () => open("files")], ["Trash", "folder", openTrash]
  ]) {
    const b = node("button");
    b.type = "button";
    b.append(api.icon(name), node("span", label));
    b.addEventListener("dblclick", action);
    b.addEventListener("click", event => { if (event.detail === 0) action(); });
    shortcuts.append(b);
  }
  for (const [label, action] of [
    ["Open Terminal", () => open("terminal")], ["Open Files", () => open("files")],
    ["Show hints", () => open("hints")], ["Tidy windows", tidy]
  ]) {
    const b = node("button", label);
    b.type = "button";
    if (label === "Open Terminal" && !api.panel("terminal")) b.disabled = true;
    b.addEventListener("click", () => { closePopups(); action(); });
    menu.append(b);
  }
  document.addEventListener("contextmenu", event => {
    if (!event.target.matches("body,#workspace,.wallpaper")) return;
    event.preventDefault();
    closePopups(menu);
    menu.hidden = false;
    menu.style.left = Math.max(0, Math.min(event.clientX, innerWidth - menu.offsetWidth)) + "px";
    menu.style.top = Math.max(0, Math.min(event.clientY, innerHeight - menu.offsetHeight)) + "px";
    menu.querySelector("button:enabled").focus();
  });

  clock.addEventListener("click", () => {
    const today = new Date();
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    const end = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    calendar.replaceChildren(node("strong", today.toLocaleDateString(undefined, {month: "long", year: "numeric"})));
    const grid = node("div", undefined, "month");
    for (const day of ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]) grid.append(node("b", day));
    for (let i = 0; i < start.getDay(); i++) grid.append(node("span"));
    for (let i = 1; i <= end; i++) grid.append(node("span", i, i === today.getDate() ? "today" : ""));
    calendar.append(grid);
    toggle(calendar, clock);
  });

  let sound = WindowTools.read("localStorage", "rta.sound", false) === true;
  let audio;
  const soundButton = byId("sound");
  const soundLabel = () => {
    soundButton.textContent = sound ? "Sound on" : "Sound off";
    soundButton.setAttribute("aria-pressed", String(sound));
  };
  function enableAudio() {
    if (!sound || !window.AudioContext) return;
    try {
      audio ||= new AudioContext();
      if (audio.state === "suspended") audio.resume().catch(() => {});
    } catch { /* Visual notifications are always available. */ }
  }
  document.addEventListener("pointerdown", enableAudio, {once: true});
  document.addEventListener("keydown", enableAudio, {once: true});
  soundButton.addEventListener("click", () => {
    sound = !sound;
    WindowTools.write("localStorage", "rta.sound", sound);
    soundLabel();
    enableAudio();
  });
  soundLabel();
  bell.addEventListener("click", () => {
    toggle(tray, bell);
    bell.classList.remove("unread");
    bell.setAttribute("aria-label", "Notifications");
  });
  return message => {
    tray.querySelector("ol").prepend(node("li", message));
    if (tray.hidden) {
      bell.classList.add("unread");
      bell.setAttribute("aria-label", "Notifications, unread");
    }
    if (sound && audio?.state === "running") {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      gain.gain.setValueAtTime(0.045, audio.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.12);
      osc.connect(gain).connect(audio.destination);
      osc.start();
      osc.stop(audio.currentTime + 0.13);
    }
  };
}
