"use strict";

const HandlerCheck = (() => {
  const MAX_CODE = 2500;
  const fields = new Set(["From", "To", "Cc", "Bcc", "Subject", "TextBody"]);
  const whole = /^\s*function\s+sendEmail\s*\(\s*message\s*\)\s*\{\s*const\s+outgoing\s*=\s*\{\s*\.\.\.message\s*\}\s*;\s*([\s\S]*?)return\s+outgoing\s*;\s*\}\s*module\.exports\s*=\s*\{\s*sendEmail\s*\}\s*;?\s*$/;
  const header = /^\s*function\s+sendEmail\s*\(\s*message\s*\)\s*\{\s*/;
  const copy = /^const\s+outgoing\s*=\s*\{\s*\.\.\.message\s*\}\s*;\s*/;
  const assignment = /^\s*outgoing\.([A-Za-z]+)\s*=\s*("(?:\\.|[^"\\])*")\s*;\s*/;
  const lineAt = (source, position) => source.slice(0, position).split("\n").length;

  function check(source) {
    if (typeof source !== "string" || Array.from(source).length < 1 || Array.from(source).length > MAX_CODE) {
      return {ok: false, error: "index.js must contain 1 to 2500 characters.", line: 1};
    }
    const match = whole.exec(source);
    if (!match) {
      const start = header.exec(source);
      const statement = start && copy.exec(source.slice(start[0].length));
      const position = (start?.[0].length || 0) + (statement?.[0].length || 0);
      return {ok: false, error: "index.js must export sendEmail(message), copy message, and return outgoing.",
        line: lineAt(source, position)};
    }
    const body = match[1];
    const prefix = header.exec(source)[0];
    const bodyStart = prefix.length + copy.exec(source.slice(prefix.length))[0].length;
    let position = 0;
    const changes = new Set();
    while (position < body.length) {
      const current = assignment.exec(body.slice(position));
      const line = lineAt(source, bodyStart + position + (body.slice(position).match(/^\s*/)?.[0].length || 0));
      if (!current || changes.size >= 6 || !fields.has(current[1])) {
        return {ok: false, error: 'Use outgoing.Field = "value" assignments only.', line};
      }
      let value;
      try {
        value = JSON.parse(current[2]);
      } catch {
        return {ok: false, error: "Use a quoted string value for mail fields.", line};
      }
      if (typeof value !== "string" || Array.from(value).length > 8192) {
        return {ok: false, error: "Mail fields must be bounded strings.", line};
      }
      position += current[0].length;
      changes.add(current[1]);
    }
    return {ok: true};
  }
  return {check};
})();
if (typeof module !== "undefined") module.exports = HandlerCheck;
