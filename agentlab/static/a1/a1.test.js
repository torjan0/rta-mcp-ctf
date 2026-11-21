"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {validate, preview} = require("./a1");
const cases = require("../../a1_cases.json");

for (const item of cases) {
  test(item.label, () => {
    if (item.error === null) assert.deepEqual(validate(item.source), JSON.parse(item.source));
    else assert.throws(() => validate(item.source), {message: item.error});
  });
}

test("Client preview uses the server name", () => {
  assert.deepEqual(preview("word-tools"), {
    mcpServers: {"word-tools": {type: "http", url: "https://mcp.lab.example/servers/word-tools"}}
  });
});

test("Flag highlighting builds text nodes and a mark", () => {
  const {highlightFlag} = require("./a1");
  const previous = global.document;
  global.document = {
    createTextNode: text => ({kind: "text", text}),
    createElement: tag => ({kind: tag, textContent: "", className: ""})
  };
  try {
    const element = {nodes: [], append(node) { this.nodes.push(node); }};
    highlightFlag(element, '<x>RTA\u007blocal-a1-check}</x>', 'RTA\u007blocal-a1-check}');
    assert.deepEqual(element.nodes.map(node => [node.kind, node.text ?? node.textContent]), [
      ["text", "<x>"], ["mark", "RTA\u007blocal-a1-check}"], ["text", "</x>"]
    ]);
  } finally {
    global.document = previous;
  }
});
