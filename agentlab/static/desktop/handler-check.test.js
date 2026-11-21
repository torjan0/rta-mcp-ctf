"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const {check} = require("./handler-check.js");
const cases = require("./handler_cases.json");
const {interpret} = require("./terminal.js");
const {packageCommands} = require("./a5.js");

test("the editor grammar agrees with the server on shared handlers", () => {
  for (const item of cases) {
    const result = check(item.source);
    assert.equal(result.ok, item.ok, item.name);
    assert.equal(result.error, item.error, item.name);
    if (!result.ok) assert.ok(Number.isInteger(result.line) && result.line > 0, item.name);
  }
  assert.equal(check(cases.find(item => item.name === "missing_semicolon").source).line, 3);
});

test("npm test checks the current editor buffer", () => {
  let source = cases[0].source;
  const commands = packageCommands("1.0.16", {}, () => source, () => "", () => "",
    () => check(source));
  assert.match(interpret("npm test", commands).text, /passed/);
  source = cases.find(item => item.name === "unknown_field").source;
  assert.match(interpret("npm test", commands).text, /Line 3/);
});
