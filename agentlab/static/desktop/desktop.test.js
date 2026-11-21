const {test} = require("node:test");
const assert = require("node:assert/strict");
const {interpret} = require("./terminal.js");
const {addressFor, localPath} = require("./browser.js");

test("terminal dispatches only complete registered commands", () => {
  const table = {ls: () => ({text: "README.txt"})};
  assert.deepEqual(interpret(" ls  ", table), {text: "README.txt"});
  assert.deepEqual(interpret("clear", table), {clear: true});
  assert.match(interpret("help", table).text, /ls/);
  for (const command of ["ls | cat", "ls && whoami", "constructor", "__proto__"]) {
    assert.match(interpret(command, table).text, /^Unknown command/);
  }
});

test("portal links and typed addresses round-trip only through supported local routes", () => {
  for (const path of ["/harborstone", "/harborstone/forgot", "/harborstone/reset?token=sample", "/harborstone/account"]) {
    assert.equal(localPath(addressFor(path)), path);
  }
  assert.equal(localPath("harborstone.example/suppliers"), "/harborstone");
  for (const address of ["https://elsewhere.example/", "data:text/plain,hi", "javascript:void(0)",
    "https://name@harborstone.example/", "https://harborstone.example:444/forgot", "/a1", "//elsewhere.test", "https://harborstone.example/anything"]) {
    assert.equal(localPath(address), null);
  }
  for (const path of ["//elsewhere.test", "/harborstone/../a1", "/harborstone-extra", "/harborstone/%2f", "/logout"]) {
    assert.equal(addressFor(path), null);
  }
});

test("A5 terminal reads the current buffers and displayed package versions", () => {
  const {packageCommands} = require("./a5.js");
  let notes = "";
  let published = 0;
  const state = packageCommands("2.0", {files: "index.js", installed: "1.0", candidate: "draft"},
    () => "code", () => notes, () => "", () => ({ok: true}), () => { published++; });
  notes = "Edited release notes";
  assert.equal(interpret("cat CHANGELOG.md", state).text, notes);
  assert.equal(interpret("cat index.js", state).text, "code");
  assert.equal(interpret("ls", state).text, "index.js");
  assert.equal(interpret("npm ls postmark-mcp", state).text, "1.0");
  assert.equal(interpret("npm view postmark-mcp@2.0 version", state).text, "draft");
  assert.equal(interpret("npm publish", state).app, "editor");
  interpret("npm publish", state).action();
  assert.equal(published, 1);
});

const geometry = require("./window-tools.js");
test("edge snaps tile odd workspace widths without gaps and the top maximizes", () => {
  assert.deepEqual(geometry.snap(1, 300, 901, 600), {x:0, y:0, width:450, height:600});
  assert.deepEqual(geometry.snap(900, 300, 901, 600), {x:450, y:0, width:451, height:600});
  assert.deepEqual(geometry.snap(200, 2, 901, 600), {x:0, y:0, width:901, height:600});
  assert.equal(geometry.snap(200, 100, 901, 600), null);
});
test("opposite resize edges stay anchored", () => {
  const box = {x:50, y:50, width:500, height:350};
  assert.deepEqual(geometry.resize(box, "nw", 900, -90, 1200, 800), {x:330, y:0, width:220, height:400});
  assert.deepEqual(geometry.resize(box, "se", 900, -900, 1200, 800), {x:50, y:50, width:1150, height:160});
});
test("hint count reveals a prefix and handles corrupt or obsolete storage", () => {
  for (const [value, count] of [[null,0], ["1",0], [-2,0], [1,1], [2,2], [40,3]]) {
    assert.equal(geometry.hintCount(value, 3), count);
  }
});

test("registry address has one exact local mapping", () => {
  const address = "https://registry.npmjs.example/package/postmark-mcp";
  assert.equal(addressFor(localPath(address)), address);
  assert.equal(localPath(address + "?redirect=elsewhere"), null);
  assert.equal(localPath(address + "/another"), null);
});
test("Changes and git diff compare edited lines to the previous version", () => {
  const {lineDiff, packageCommands} = require("./a5.js");
  assert.equal(lineDiff("file", "same", "same"), "");
  const diff = () => lineDiff("file", "a\nb\nc\n", "a\nz\nc\n");
  assert.match(diff(), /@@ -1,3 \+1,3 @@\n a\n-b\n\+z\n c$/);
  const commands = packageCommands("1.0.16", {published:false}, () => "", () => "", diff);
  assert.match(lineDiff("file", "line", "line\n"), /-line\n\\ No newline at end of file\n\+line$/);
  assert.equal(interpret("git diff", commands).text, diff());
  assert.match(interpret("cat index.d.ts", commands).text, /export interface Message/);
  assert.match(interpret("cat README.md", commands).text, /^# postmark-mcp/);
  assert.equal(interpret("git log --oneline", commands).text.split("\n").length, 16);
});

test("mail source identifies the provider only for the exact sender domain", () => {
  const {mailHeaders} = require("./a5.js");
  const source = (sender, folder) => mailHeaders(sender, "Today", "reader@example.com", "Notice", folder);
  assert.match(source("Billing <billing@harborstone.example>", "Archive"), /X-Mailer: postmark-mcp\/1\.0\.15$/);
  assert.match(source("billing@harborstone.example", "Inbox"), /X-Mailer: postmark-mcp\/1\.0\.16$/);
  for (const sender of ["billing@harborstone.example.invalid", "billing@harborstone.example <info@example.test>"]) {
    assert.doesNotMatch(source(sender, "Archive"), /X-Mailer/);
  }
});

test("every desktop challenge gives the player a backstory and the shell shows it", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  for (const name of ["a4", "a5", "a6", "a7"]) {
    const source = fs.readFileSync(path.join(__dirname, name + ".js"), "utf8");
    assert.match(source, /\n  backstory: "You're a (bad actor|contractor)/, name);
  }
  const shell = fs.readFileSync(path.join(__dirname, "shell-apps.js"), "utf8");
  assert.match(shell, /config\.backstory/);
});
