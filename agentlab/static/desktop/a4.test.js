"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {AcmeSupport, packageJson, decodedRequest, a4Commands} = require("./a4.js");
const Terminal = require("./terminal.js");

test("acme support accepts only its two local pages", () => {
  assert.equal(AcmeSupport.localPath("support.acme.example/new"), "/new");
  assert.equal(AcmeSupport.localPath("https://support.acme.example/tickets"), "/tickets");
  for (const address of ["https://support.acme.example.evil/new", "https://support.acme.example/new?x=1",
    "https://support.acme.example/new#x", "https://user@support.acme.example/new",
    "https://support.acme.example\\@evil/new", "https://support.acme.example/a4"]) {
    assert.equal(AcmeSupport.localPath(address), null, address);
  }
});

test("A4 terminal reads current buffers and points publishing at the ticket", () => {
  let open = false;
  const commands = a4Commands(() => packageJson("@sample/demo", "echo hi"),
    () => "POST /h/test", () => "https://hooks.rta-lab.example/h/one", () => "19 runs left",
    () => { open = true; });
  assert.match(Terminal.interpret("cat package.json", commands).text, /"start": "echo hi"/);
  assert.equal(Terminal.interpret("tail hook.log", commands).text, "POST /h/test");
  assert.equal(Terminal.interpret("echo $HOOK", commands).text, "https://hooks.rta-lab.example/h/one");
  assert.equal(Terminal.interpret("status", commands).text, "19 runs left");
  const publish = Terminal.interpret("npm publish", commands);
  assert.equal(publish.app, "browser");
  publish.action();
  assert.equal(open, true);
});

test("listener decodes the request query for display and checklist progress", () => {
  assert.match(decodedRequest({time: "12:00", method: "POST", path: "/h/test",
    query: "v=RTA%7Bdemo%7D", body: ""}), /v=RTA\{demo\}/);
});
