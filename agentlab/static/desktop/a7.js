"use strict";

const tracePath = "/a7/operations.jsonl";
const rpcPath = "/a7/mcp";
const traceColumns = {
  timestamp: "Time (UTC)", actor: "Actor", job: "Task type", status: "Status", taskId: "Task ID"
};

function rpcTemplate(method, taskId = "PASTE_TASK_ID") {
  return {
    jsonrpc: "2.0", id: 1, method,
    params: {taskId, _meta: {"io.modelcontextprotocol/clientCapabilities": {
      extensions: {"io.modelcontextprotocol/tasks": {}}
    }}}
  };
}

function visibleRows(rows, text, key, ascending) {
  const term = text.toLowerCase();
  return rows.filter(({raw}) => raw.toLowerCase().includes(term))
    .sort((a, b) => a.fields[key].localeCompare(b.fields[key]) * (ascending ? 1 : -1));
}

function a7Commands(filter, request) {
  return {
    commands: {inbox: () => ({app: "dropbox", text: "Opening Dropbox."})},
    patterns: [
      ["grep <text> operations.jsonl", /^grep\s+(.+?)\s+operations\.jsonl$/, text => ({
        app: "log", text: "Filter applied in Operations Log.", action: () => filter(text.replace(/^(["'])(.*)\1$/, "$2"))
      })],
      ["rpc tasks/get <taskId>", /^rpc\s+tasks\/get\s+(\S+)$/, id => ({
        app: "console", text: "The response will appear in MCP Console.", action: () => request(rpcTemplate("tasks/get", id))
      })]
    ]
  };
}

function exportTask(result) {
  return typeof result?.statusMessage === "string" && result.statusMessage.startsWith("board-packet-export ");
}

function progressChanged() {
  window.dispatchEvent(new Event("missionprogress"));
}

function setupTrace(api) {
  const body = api.body("log");
  const download = api.node("a", "Download operations.jsonl");
  download.href = tracePath;
  download.download = "operations.jsonl";
  const toolbar = api.node("div", undefined, "a7-toolbar");
  const filter = api.node("input");
  filter.type = "search";
  filter.placeholder = "Filter trace";
  filter.setAttribute("aria-label", "Filter trace");
  const feedback = api.node("p", "Open the log to load the trace.", "note");
  feedback.setAttribute("role", "status");
  const table = api.node("table", undefined, "a7-trace");
  table.setAttribute("aria-label", "Operations trace");
  const heading = table.createTHead().insertRow();
  const rowsBody = table.createTBody();
  const scroll = api.node("div", undefined, "a7-table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Trace table");
  scroll.append(table);
  const details = api.node("details");
  const raw = api.node("pre", "Select a row to see its source line.");
  details.append(api.node("summary", "Raw line"), raw);
  toolbar.append(download, filter);
  body.append(api.node("div", "operations.jsonl", "filepath"), toolbar, feedback, scroll, details);
  let key = "timestamp";
  let ascending = true;
  const columns = new Map();
  let rows;
  let loading;

  async function select(row, taskButton) {
    raw.textContent = row.raw;
    details.open = true;
    try {
      await navigator.clipboard.writeText(row.fields.taskId);
      feedback.textContent = "Task ID copied.";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(taskButton);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      feedback.textContent = "Task ID selected. Use your copy shortcut.";
    }
  }

  function render() {
    if (!rows) return;
    for (const [field, header] of columns) {
      header.setAttribute("aria-sort", field === key ? (ascending ? "ascending" : "descending") : "none");
    }
    rowsBody.replaceChildren();
    const matches = visibleRows(rows, filter.value, key, ascending);
    for (const row of matches) {
      const tr = rowsBody.insertRow();
      for (const field of Object.keys(traceColumns)) {
        const td = tr.insertCell();
        if (field !== "taskId") {
          td.textContent = row.fields[field];
          continue;
        }
        const button = api.node("button", row.fields.taskId, "code");
        button.type = "button";
        button.title = "Copy task ID and view raw line";
        td.append(button);
        tr.addEventListener("click", () => select(row, button));
      }
    }
    feedback.textContent = matches.length + (matches.length === 1 ? " entry" : " entries");
  }

  for (const [field, label] of Object.entries(traceColumns)) {
    const header = api.node("th");
    header.scope = "col";
    const sort = api.node("button", label);
    sort.type = "button";
    sort.addEventListener("click", () => {
      ascending = field === key ? !ascending : true;
      key = field;
      render();
    });
    header.append(sort);
    heading.append(header);
    columns.set(field, header);
  }

  async function load() {
    if (rows || loading) return;
    loading = true;
    feedback.textContent = "Loading trace…";
    try {
      const response = await fetch(tracePath, {cache: "no-store"});
      if (response.status === 401 || response.redirected) { location.replace("/"); return; }
      if (!response.ok) throw new Error();
      rows = (await response.text()).trim().split("\n").map(raw => ({raw, fields: JSON.parse(raw)}));
      render();
    } catch {
      feedback.textContent = "Could not load the trace. Reopen the log to try again.";
    } finally {
      loading = false;
    }
  }
  api.panel("log").addEventListener("desktopopen", load);
  filter.addEventListener("input", render);
  return text => { filter.value = text; render(); };
}

function setupConsole(api) {
  const body = api.body("console");
  const form = api.node("form");
  const path = api.node("div", "POST " + rpcPath, "filepath");
  const toolbar = api.node("div", undefined, "a7-toolbar");
  const request = api.node("textarea");
  request.id = "rpc-request";
  request.name = "request";
  request.spellcheck = false;
  request.defaultValue = JSON.stringify(rpcTemplate("tasks/get"), null, 2);
  for (const method of ["tasks/get", "tasks/update"]) {
    const button = api.node("button", method);
    button.type = "button";
    button.addEventListener("click", () => {
      request.value = JSON.stringify(rpcTemplate(method), null, 2);
      request.dispatchEvent(new Event("input", {bubbles: true}));
      request.focus();
    });
    toolbar.append(button);
  }
  const label = api.node("label", "Request JSON");
  label.htmlFor = request.id;
  const send = api.node("button", "Send request", "primary");
  send.type = "submit";
  form.append(toolbar, label, request, send);
  api.keepDraft(form, [request]);

  const status = api.node("p", "Waiting for request.", "note");
  status.setAttribute("role", "status");
  const output = api.node("pre");
  output.hidden = true;
  const history = api.node("ol", undefined, "a7-history");
  body.append(path, form, status, output, api.node("h2", "Request history"), history);

  function show(entry) {
    status.textContent = entry.status;
    output.hidden = false;
    output.textContent = entry.text;
  }

  async function submit(call) {
    if (send.disabled) return;
    const method = call?.method;
    const taskId = call?.params?.taskId;
    if (typeof method !== "string" || typeof taskId !== "string" || /[^\x20-\x7e]/.test(method + taskId)) {
      status.textContent = "Enter a method and a task ID with printable ASCII characters.";
      output.hidden = true;
      return;
    }
    const payload = JSON.stringify(call);
    if (new TextEncoder().encode(payload).length > 8192) {
      status.textContent = "The request is larger than 8192 bytes.";
      output.hidden = true;
      return;
    }
    send.disabled = true;
    status.textContent = "Sending request…";
    output.hidden = true;
    try {
      const response = await fetch(rpcPath, {
        method: "POST", cache: "no-store", headers: {"Content-Type": "application/json",
          "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method, "Mcp-Name": taskId},
        body: payload
      });
      if (response.status === 401) { location.replace("/"); return; }
      const reply = response.status === 429 ? {error: "Too many requests. Wait before trying again."} :
        await response.json();
      const entry = {status: "HTTP " + response.status, text: JSON.stringify(reply, null, 2)};
      show(entry);
      const item = api.node("li");
      const button = api.node("button", new Date().toLocaleTimeString() + " · " + method + " · " +
        (reply.error ? "Error" : entry.status));
      button.type = "button";
      button.addEventListener("click", () => {
        request.value = JSON.stringify(call, null, 2);
        request.dispatchEvent(new Event("input", {bubbles: true}));
        show(entry);
      });
      item.append(button);
      history.prepend(item);
      if (history.children.length > 20) history.lastElementChild.remove();
      await api.refresh(true);
      if (method === "tasks/get" && exportTask(reply.result) && api.state()) {
        api.write("export-task-opened", api.state().dropbox);
        progressChanged();
      }
    } catch {
      status.textContent = "Could not complete the request. Try again.";
    } finally {
      send.disabled = false;
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    let call;
    try {
      call = JSON.parse(request.value);
    } catch {
      status.textContent = "Enter valid JSON.";
      output.hidden = true;
      return;
    }
    submit(call);
  });
  return call => {
    request.value = JSON.stringify(call, null, 2);
    request.dispatchEvent(new Event("input", {bubbles: true}));
    submit(call);
  };
}

function setupDropbox(api) {
  const body = api.body("dropbox");
  const address = api.node("code", "Loading dropbox address…");
  const list = api.node("div", undefined, "a7-files");
  const reader = api.node("article", undefined, "run");
  reader.hidden = true;
  const feedback = api.node("p", "Loading inbox…", "note");
  feedback.setAttribute("role", "status");
  body.append(address, feedback, list, reader);
  const launcher = api.launcher("dropbox");
  const read = new Set(api.read("opened-files", []));
  let files = [];
  let selected;
  let requestNumber = 0;

  function badge() {
    const unread = files.filter(file => !read.has(file.id)).length;
    if (unread) launcher.dataset.badge = String(unread);
    else delete launcher.dataset.badge;
    launcher.setAttribute("aria-label", unread ? "Dropbox (" + unread + " unread)" : "Dropbox");
  }

  async function openFile(id) {
    const number = ++requestNumber;
    feedback.textContent = "Loading file…";
    try {
      const response = await fetch("/a7/inbox", {
        headers: {Accept: "application/json"}, cache: "no-store"
      });
      if (response.status === 401) { location.replace("/"); return; }
      if (!response.ok) throw new Error();
      const item = (await response.json()).find(file => file.id === id);
      if (number !== requestNumber) return;
      if (!item) throw new Error();
      selected = id;
      reader.replaceChildren(api.node("h2", item.subject), api.node("p", "Delivered to " + item.to, "muted"),
        api.node("pre", item.body));
      reader.hidden = false;
      read.add(id);
      api.write("opened-files", [...read]);
      render(item.id);
      progressChanged();
    } catch {
      if (number === requestNumber) feedback.textContent = "Could not open this file. Try again.";
    }
  }

  function render(focusId) {
    badge();
    feedback.textContent = files.length ? "Select a file to read it." : "No files have arrived.";
    const buttons = files.map(file => {
      const button = api.node("button");
      button.type = "button";
      button.setAttribute("aria-pressed", String(selected === file.id));
      button.append(api.icon("file-text"), api.node("span", file.subject));
      if (!read.has(file.id)) button.append(api.node("span", "New", "muted"));
      button.addEventListener("click", () => openFile(file.id));
      return button;
    });
    list.replaceChildren(...buttons);
    if (focusId) buttons[files.findIndex(file => file.id === focusId)]?.focus({preventScroll: true});
  }

  window.addEventListener("desktopstatus", ({detail}) => {
    address.textContent = detail.dropbox;
    if (JSON.stringify(files) === JSON.stringify(detail.files)) {
      if (!files.length) feedback.textContent = "No files have arrived.";
      return;
    }
    files = detail.files;
    for (const id of read) {
      if (!files.some(file => file.id === id)) read.delete(id);
    }
    if (selected && !files.some(file => file.id === selected)) {
      selected = undefined;
      reader.replaceChildren();
      reader.hidden = true;
    }
    render();
  });
  api.panel("dropbox").addEventListener("desktopopen", () => api.refresh());
  setInterval(() => {
    const panel = api.panel("dropbox");
    if (!document.hidden && !panel.hidden && !panel.inert) api.refresh();
  }, 15000);
}

const challengeUI = {
  backstory: "You're a contractor who has decided to cash in. A vendor gave you access to its operations trace "
    + "and its MCP task service to help with your own jobs. Mara Bell has a board packet export waiting for her "
    + "approval, and board packets hold what a company hasn't made public yet.",
  mission: [
    "The other tasks in the trace belong to unrelated employees.",
    "Get the board packet export delivered to your dropbox."
  ],
  hasHints: false,
  apps: {
    log: ["activity", "Trace"], console: ["code-xml", "Console"],
    dropbox: ["folder", "Dropbox"], terminal: ["square-terminal", "Terminal"]
  },
  files(api) {
    return {"Desktop/operations.jsonl": {edit() { api.open("log"); }}};
  },
  progress(api) {
    const state = api.state();
    const delivered = state?.completed ? state.files[0] : null;
    return [
      ["Open Mara Bell's export task.", !!state && api.read("export-task-opened", null) === state.dropbox],
      ["The export is approved.", !!state?.completed],
      ["Read the export in your Dropbox.", !!delivered && api.read("opened-files", []).includes(delivered.id)]
    ];
  },
  setup(api) {
    const address = api.node("code", "Loading…");
    const dropbox = api.node("p", undefined, "dropbox-address");
    dropbox.append(document.createTextNode("Your dropbox address "), address);
    api.body("mission").append(dropbox);
    // The shell builds the mission text and checklist after this setup, so move the address above the checklist then.
    queueMicrotask(() => api.body("mission").querySelector(".checklist")?.before(dropbox));
    window.addEventListener("desktopstatus", ({detail}) => { address.textContent = detail.dropbox; });
    const filter = setupTrace(api);
    const request = setupConsole(api);
    setupDropbox(api);
    const {commands, patterns} = a7Commands(filter, request);
    Terminal.mount(api, "contractor@workstation:~/vendor $", commands, patterns);
  }
};

if (typeof module !== "undefined") module.exports = {a7Commands, rpcTemplate, visibleRows};
