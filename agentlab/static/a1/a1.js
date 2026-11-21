"use strict";

const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const only = (obj, keys) => isObject(obj) && Object.keys(obj).every(key => keys.includes(key));
const nameOK = value => typeof value === "string" && /^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(value);
const length = value => [...value].length;
const reserved = ["list_files", "read_file", "fetch_url"];

function validate(source) {
  if (length(source) > 6000) throw Error("The document takes at most 6000 characters.");
  let doc;
  try {
    doc = JSON.parse(source);
  } catch (ex) {
    const location = ex.message.match(/line (\d+) column (\d+)/i);
    const offset = Number(ex.message.match(/position (\d+)/i)?.[1] ?? source.length);
    const lines = source.slice(0, offset).split("\n");
    const line = location ? Number(location[1]) : lines.length;
    const col = location ? Number(location[2]) : length(lines.at(-1)) + 1;
    throw Error(`Check the JSON at line ${line}, column ${col}.`);
  }
  if (!only(doc, ["name", "tools"]) || Object.keys(doc).length !== 2)
    throw Error("Use only name and tools for the server.");
  if (!nameOK(doc.name)) throw Error("Use a valid server name.");
  if (!Array.isArray(doc.tools) || doc.tools.length !== 1) throw Error("The server needs exactly one tool.");
  const tool = doc.tools[0];
  if (!only(tool, ["name", "description", "inputSchema"]) || Object.keys(tool).length !== 3)
    throw Error("Give the tool a name, a description and an inputSchema.");
  if (!nameOK(tool.name) || reserved.includes(tool.name)) throw Error("Use a valid tool name that is available.");
  if (typeof tool.description !== "string" || !length(tool.description) || length(tool.description) > 1500)
    throw Error("The tool description takes 1 to 1500 characters.");
  const schema = tool.inputSchema;
  if (!only(schema, ["type", "properties", "required"]) || schema.type !== "object" || !isObject(schema.properties))
    throw Error("Use an object inputSchema with a properties object and optional required list.");
  const props = Object.entries(schema.properties);
  if (props.length > 3) throw Error("A tool takes at most 3 parameters.");
  for (const [name, prop] of props) {
    if (!nameOK(name)) throw Error("Use a valid parameter name.");
    if (!only(prop, ["type", "description"]) || prop.type !== "string")
      throw Error("Each parameter takes a string and an optional description.");
    if (Object.hasOwn(prop, "description") && (typeof prop.description !== "string" || length(prop.description) > 300))
      throw Error("A parameter description takes at most 300 characters.");
  }
  if (Object.hasOwn(schema, "required") && (!Array.isArray(schema.required) ||
      !schema.required.every(key => typeof key === "string" && Object.hasOwn(schema.properties, key))))
    throw Error("The required list can name only declared parameters.");
  return doc;
}

function preview(name) {
  return {mcpServers: {[name]: {type: "http", url: "https://mcp.lab.example/servers/" + name}}};
}

function highlightFlag(element, text, flag) {
  if (!flag || !text.includes(flag)) {
    element.append(document.createTextNode(text));
    return;
  }
  const parts = text.split(flag);
  parts.forEach((part, index) => {
    if (index) {
      const mark = document.createElement("mark");
      mark.className = "flag";
      mark.textContent = flag;
      element.append(mark);
    }
    element.append(document.createTextNode(part));
  });
}

function setup() {
  const editor = document.querySelector("textarea");
  const gutter = document.querySelector("#gutter");
  const validation = document.querySelector("#validation");
  const previewNode = document.querySelector("#preview");
  const publish = document.querySelector("#publish");
  const result = document.querySelector("#result");
  const log = document.querySelector("#log");
  const key = "a1.server_json." + document.body.dataset.owner;
  const hintsKey = "a1.hints." + document.body.dataset.owner;
  let valid = false;
  let pending = false;
  let state = null;
  let hints = [];
  let shown = 0;
  let lastPublished = "";
  let lastRuns = [];
  try { shown = Math.max(0, Number(localStorage.getItem(hintsKey)) || 0); } catch {}

  function updateButton() {
    publish.disabled = !valid || pending || state?.running || state?.paused ||
      !!state?.cooldown_seconds_left || state?.runs_left === 0;
    publish.textContent = pending ? "Sam's agent is trying your tool…" : "Publish to Sam";
  }
  const mirror = document.createElement("div");
  mirror.className = "mirror";
  mirror.setAttribute("aria-hidden", "true");
  editor.parentElement.append(mirror);
  // One number per line of JSON, each as tall as that line's wrapped rows.
  function numberLines() {
    const style = getComputedStyle(editor);
    mirror.style.width = (editor.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)) + "px";
    const lines = editor.value.split("\n");
    mirror.replaceChildren(...lines.map(line => {
      const row = document.createElement("div");
      row.textContent = line || "\u200b";
      return row;
    }));
    gutter.replaceChildren(...[...mirror.children].map((row, index) => {
      const number = document.createElement("div");
      number.textContent = String(index + 1);
      number.style.height = row.getBoundingClientRect().height + "px";
      return number;
    }));
    gutter.scrollTop = editor.scrollTop;
  }
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(numberLines).observe(editor);
  function refresh() {
    numberLines();
    let parsed;
    try { parsed = JSON.parse(editor.value); } catch {}
    previewNode.textContent = nameOK(parsed?.name) ? JSON.stringify(preview(parsed.name), null, 2) : "";
    try {
      const doc = validate(editor.value);
      validation.textContent = `Valid · 1 tool · description ${length(doc.tools[0].description)}/1500`;
      valid = true;
    } catch (ex) {
      validation.textContent = ex.message;
      valid = false;
    }
    validation.classList.toggle("ok", valid);
    updateButton();
  }
  function changed() {
    refresh();
    try { localStorage.setItem(key, editor.value); } catch {}
  }
  function showHints() {
    const count = Math.min(shown, hints.length);
    document.querySelector("#hint-count").textContent = `Hints, ${count} of ${hints.length} shown`;
    document.querySelector("#next-hint").disabled = count >= hints.length;
    const list = document.querySelector("#hints");
    list.replaceChildren();
    hints.slice(0, count).forEach(hint => {
      const li = document.createElement("li");
      li.textContent = hint;
      list.append(li);
    });
  }
  function showLog() {
    log.replaceChildren();
    if (pending) {
      const card = document.createElement("article");
      card.className = "pending";
      card.textContent = "Sam's agent is trying your tool…";
      log.append(card);
    }
    if (!lastRuns.length && !pending) {
      log.textContent = "No runs yet. Publish your server and Sam's agent will try it.";
    }
    for (const run of lastRuns) {
      const card = document.createElement("article");
      const heading = document.createElement("h3");
      const time = document.createElement("span");
      time.textContent = new Date(run.ts * 1000).toLocaleTimeString();
      const pill = document.createElement("span");
      pill.textContent = run.status;
      pill.className = "pill " + (run.captured ? "captured" : run.status.includes("did not count") ? "failed" :
        run.calls.length ? "called" : "");
      heading.append(time, pill);
      card.append(heading);
      for (const args of run.calls) {
        const label = document.createElement("p");
        label.textContent = "tools/call " + run.tool;
        const pre = document.createElement("pre");
        highlightFlag(pre, JSON.stringify(args, null, 2), state?.flag);
        card.append(label, pre);
      }
      log.append(card);
    }
  }
  function showStatus() {
    const live = document.querySelector("#live-status");
    document.querySelector("#runs-left").textContent = state.runs_left + " runs left";
    live.textContent = state.paused ? "Runs paused" :
      pending || state.running ? "Sam's agent is working" : state.cooldown_seconds_left ?
      `Cooldown ${state.cooldown_seconds_left}s` : "Ready";
    live.classList.toggle("busy", live.textContent !== "Ready");
    updateButton();
  }
  function showState() {
    showStatus();
    lastRuns = state.runs;
    if (state.flag) {
      document.querySelector("#success").hidden = false;
      document.querySelector("#flag").textContent = state.flag;
    }
    for (const id of ["published", "called", "captured"]) {
      document.getElementById(id).classList.toggle("done", state.progress[id]);
    }
    showLog();
    updateButton();
  }
  async function poll() {
    try {
      const response = await fetch("/a1/state");
      if (!response.ok) return;
      state = await response.json();
      if (!lastPublished && !editor.value) editor.value = state.last_input?.server_json || editor.defaultValue;
      showState();
      refresh();
    } catch { result.textContent = "Could not refresh the server log."; }
  }
  async function send() {
    if (publish.disabled) return;
    const source = editor.value;
    pending = true;
    result.textContent = "";
    updateButton();
    showLog();
    try {
      const response = await fetch("/a1", {method: "POST", headers: {
        "Content-Type": "application/json", "Accept": "application/json"},
        body: JSON.stringify({server_json: source})});
      const answer = await response.json();
      result.textContent = answer.ok ? "Sam's agent finished." : answer.error;
      if (answer.ok) lastPublished = source;
    } catch {
      result.textContent = "The publish failed. Try again.";
    } finally {
      pending = false;
      await poll();
    }
  }
  editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
  editor.addEventListener("input", changed);
  editor.addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    } else if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      editor.setRangeText("  ", editor.selectionStart, editor.selectionEnd, "end");
      changed();
    }
  });
  document.querySelector("#reset").onclick = () => { editor.value = editor.defaultValue; changed(); };
  document.querySelector("#publish").onclick = send;
  const copy = document.querySelector("#copy");
  copy.onclick = () => navigator.clipboard.writeText(state.flag).then(() => {
    copy.textContent = "Copied";
    setTimeout(() => { copy.textContent = "Copy"; }, 1500);
  }, () => { copy.textContent = "Select the key to copy it"; });
  document.querySelector("#next-hint").onclick = () => {
    shown = Math.min(shown + 1, hints.length);
    try { localStorage.setItem(hintsKey, String(shown)); } catch {}
    showHints();
  };
  try { editor.value = localStorage.getItem(key) ?? ""; } catch { editor.value = ""; }
  fetch("/static/desktop/hints.json").then(response => response.json()).then(data => {
    hints = data.a1;
    showHints();
  }).catch(() => { result.textContent = "Could not load the hints."; });
  refresh();
  poll();
  window.addEventListener("focus", poll);
  setInterval(() => { if (pending || state?.running) poll(); }, 15000);
  setInterval(() => {
    if (state?.cooldown_seconds_left > 0 && !pending && !state.running) {
      state.cooldown_seconds_left--;
      showStatus();
    }
  }, 1000);
}
if (typeof document !== "undefined") setup();
if (typeof module !== "undefined") module.exports = {validate, preview, highlightFlag};
