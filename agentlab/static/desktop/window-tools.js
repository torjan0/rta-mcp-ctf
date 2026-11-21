"use strict";

const WindowTools = (() => {
  const clamp = (value, min, max) => Math.max(min, Math.min(value, max));

  function read(area, key, fallback) {
    try {
      return JSON.parse(window[area].getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  }

  function write(area, key, value) {
    try {
      if (value == null) window[area].removeItem(key);
      else window[area].setItem(key, JSON.stringify(value));
    } catch {
      // The desktop is usable with storage disabled.
    }
  }

  function snap(x, y, width, height) {
    if (y <= 12) return {x: 0, y: 0, width, height};
    const half = Math.floor(width / 2);
    if (x <= 12) return {x: 0, y: 0, width: half, height};
    if (x >= width - 12) return {x: half, y: 0, width: width - half, height};
    return null;
  }

  function fit(rect, width, height) {
    const w = clamp(rect.width, Math.min(220, width), width);
    const h = clamp(rect.height, Math.min(160, height), height);
    return {x: clamp(rect.x, 0, width - w), y: clamp(rect.y, 0, height - h), width: w, height: h};
  }

  function resize(rect, edge, dx, dy, width, height) {
    let {x, y} = rect;
    let right = x + rect.width;
    let bottom = y + rect.height;
    if (edge.includes("w")) x = clamp(x + dx, 0, right - Math.min(220, width));
    if (edge.includes("e")) right = clamp(right + dx, x + Math.min(220, width), width);
    if (edge.includes("n")) y = clamp(y + dy, 0, bottom - Math.min(160, height));
    if (edge.includes("s")) bottom = clamp(bottom + dy, y + Math.min(160, height), height);
    return {x, y, width: right - x, height: bottom - y};
  }

  function hintCount(saved, length) {
    return Number.isInteger(saved) ? clamp(saved, 0, length) : 0;
  }

  return {read, write, snap, fit, resize, hintCount};
})();
if (typeof module !== "undefined") module.exports = WindowTools;
