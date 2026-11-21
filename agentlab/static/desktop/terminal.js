"use strict";

const Terminal = (() => {
  function interpret(line, commands, patterns = []) {
    const key = line.trim().replace(/\s+/g, " ");
    if (!key) return {};
    if (key === "clear") return {clear: true};
    if (key === "help") {
      return {text: ["help", "clear", ...Object.keys(commands), ...patterns.map(([label]) => label)].join("\n")};
    }
    if (Object.hasOwn(commands, key)) return commands[key]();
    for (const [, pattern, run] of patterns) {
      const match = pattern.exec(line.trim());
      if (match) return run(...match.slice(1));
    }
    return {text: "Unknown command. Type help for available commands."};
  }

  function mount(api, prompt, commands, patterns) {
    const body = api.body("terminal");
    const output = api.node("pre", "Type help for available commands.", "console-output");
    output.setAttribute("role", "log");
    const form = api.node("form", undefined, "command-line");
    const label = api.node("label", prompt);
    label.htmlFor = "command";
    const input = api.node("input");
    input.id = "command";
    input.autocomplete = "off";
    input.spellcheck = false;
    form.append(label, input);
    body.append(output, form);
    let cursor = 0;
    let draft = "";
    const history = [];
    form.addEventListener("submit", event => {
      event.preventDefault();
      const line = input.value;
      const result = interpret(line, commands, patterns);
      if (line.trim()) history.push(line);
      cursor = history.length;
      draft = "";
      output.textContent = result.clear ? "" : [output.textContent, prompt + " " + line, result.text]
        .filter(Boolean).join("\n");
      input.value = "";
      input.scrollIntoView({block: "nearest"});
      if (result.app) api.open(result.app);
      if (result.action) result.action();
    });
    input.addEventListener("keydown", event => {
      if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      if (cursor === history.length) draft = input.value;
      cursor = Math.min(history.length, Math.max(0, cursor + (event.key === "ArrowUp" ? -1 : 1)));
      input.value = cursor === history.length ? draft : history[cursor];
    });
    api.panel("terminal").addEventListener("desktopopen", () => input.focus({preventScroll: true}));
  }
  return {interpret, mount};
})();
if (typeof module !== "undefined") module.exports = Terminal;
