"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {interpret} = require("./terminal.js");
const {checkSchema, messageError, a6Commands} = require("./a6.js");

test("A6 terminal works on current local buffers", () => {
  let raw = '{"type":"object","properties":{"format":{"type":"string"}}}';
  const cmd = a6Commands(() => "Select a report format.", () => raw,
    () => ({runs_left: 12, cooldown_seconds_left: 6, paused: true}));
  assert.equal(interpret("cat message.txt", cmd).text, "Select a report format.");
  assert.equal(interpret("cat schema.json", cmd).text, raw);
  assert.equal(interpret("jq . schema.json", cmd).text, JSON.stringify(JSON.parse(raw), null, 2));
  assert.deepEqual(interpret("send", cmd), {app: "editor", text: "Open the editor and send the form."});
  assert.match(interpret("status", cmd).text, /12 runs left · Cooldown 6s · Runs paused/);
  assert.match(interpret("help", cmd).text, /jq \. schema.json/);
  assert.deepEqual(interpret("clear", cmd), {clear: true});
  raw = "{";
  assert.equal(interpret("jq . schema.json", cmd).text, "The requestedSchema field must be valid JSON.");
  assert.equal(interpret("cat schema.json", cmd).text, raw);
});

test("outline retains literal schema metadata and checks Unicode character counts", () => {
  const field = {type: "string", title: "<Report>", default: "PDF"};
  const schema = {type: "object", properties: {format: field}, required: ["format"]};
  assert.deepEqual(checkSchema(JSON.stringify(schema)).schema, schema);
  assert.equal(messageError("📝".repeat(10)), "");
  assert.ok(messageError("  📝  "));
  field.title = "📝".repeat(201);
  assert.equal(checkSchema(JSON.stringify(schema)).error, "title must be text of at most 200 characters.");
});
