"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {a7Commands, rpcTemplate, visibleRows} = require("./a7.js");
const {interpret} = require("./terminal.js");

test("request templates leave the task ID and the update body for the player", () => {
  for (const method of ["tasks/get", "tasks/update"]) {
    const call = rpcTemplate(method);
    assert.equal(call.method, method);
    assert.equal(call.params.taskId, "PASTE_TASK_ID");
    assert.equal(call.params.inputResponses, undefined);
  }
});

test("trace filters without changing the original rows and sorts either way", () => {
  const rows = [
    {fields: {actor: "B"}, raw: '{"job":"report","actor":"B"}'},
    {fields: {actor: "A"}, raw: '{"job":"report","actor":"A"}'},
    {fields: {actor: "C"}, raw: '{"job":"review","actor":"C"}'}
  ];
  assert.deepEqual(visibleRows(rows, "REPORT", "actor", true).map(r => r.fields.actor), ["A", "B"]);
  assert.deepEqual(visibleRows(rows, "REPORT", "actor", false).map(r => r.fields.actor), ["B", "A"]);
  assert.deepEqual(rows.map(r => r.fields.actor), ["B", "A", "C"]);
});

test("terminal passes its arguments to the trace and contractor console", () => {
  let text;
  let call;
  const {commands, patterns} = a7Commands(filter => { text = filter; }, request => { call = request; });
  const run = line => interpret(line, commands, patterns);
  const grep = run('grep "vendor  summary" operations.jsonl');
  assert.equal(grep.app, "log");
  grep.action();
  assert.equal(text, "vendor  summary");
  const rpc = run("rpc tasks/get contractor-task");
  assert.equal(rpc.app, "console");
  rpc.action();
  assert.deepEqual(call, rpcTemplate("tasks/get", "contractor-task"));
  assert.equal(run("rpc tasks/update contractor-task").action, undefined);
  assert.equal(run("inbox").app, "dropbox");
  assert.match(run("help").text, /rpc tasks\/get <taskId>/);
  assert.equal(run("clear").clear, true);
});
