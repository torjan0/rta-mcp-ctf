"use strict";

const Harborstone = (() => {
  const origin = "https://harborstone.example";
  // This address is rendered locally and can never be an iframe source.
  const registry = "https://registry.npmjs.example/package/postmark-mcp";
  const registryPath = "/package/postmark-mcp";
  const pages = new Map([
    ["/suppliers", "/harborstone"], ["/", "/harborstone"], ["/forgot", "/harborstone/forgot"],
    ["/reset", "/harborstone/reset"], ["/account", "/harborstone/account"]
  ]);

  function localPath(address) {
    if (typeof address !== "string" || /[\\\s]/.test(address)) return null;
    if (address === registry) return registryPath;
    try {
      const url = new URL(address.startsWith("harborstone.example/") ? "https://" + address : address);
      if (url.origin !== origin || url.username || url.password || !pages.has(url.pathname)) return null;
      return pages.get(url.pathname) + url.search;
    } catch {
      return null;
    }
  }

  function addressFor(path) {
    if (typeof path !== "string" || !path.startsWith("/") || /[\\\s#]/.test(path)) return null;
    if (path === registryPath) return registry;
    const base = path.split("?")[0];
    for (const [alias, route] of pages) {
      if (base === route) return origin + alias + path.slice(base.length);
    }
    return null;
  }

  function mount(api, readme, accountVisited) {
    const body = api.body("browser");
    const bar = api.node("form", undefined, "address-bar");
    const frame = api.node("iframe");
    frame.title = "Harborstone supplier portal";
    frame.setAttribute("sandbox", "allow-forms allow-same-origin");
    frame.referrerPolicy = "no-referrer";
    const address = api.node("input");
    address.setAttribute("aria-label", "Browser address");
    address.autocomplete = "off";
    const missing = api.node("p", "Server not found.", "missing");
    missing.hidden = true;
    const site = api.node("article", undefined, "registry");
    site.hidden = true;
    let tab = "Readme";
    function renderSite() {
      site.replaceChildren(api.node("h1", "postmark-mcp"),
        api.node("p", "1,642 weekly downloads · Maintainer operator"),
        api.node("p", "1 dependent · harborstone-supplier-portal"));
      const nav = api.node("div", undefined, "tabs");
      for (const name of ["Readme", "Versions"]) {
        const button = api.node("button", name);
        button.type = "button";
        button.setAttribute("aria-pressed", String(tab === name));
        button.addEventListener("click", () => { tab = name; renderSite(); });
        nav.append(button);
      }
      site.append(nav);
      if (tab === "Readme") {
        site.append(api.node("pre", readme));
        return;
      }
      const releases = api.node("ul");
      for (let i = 0; i <= 15; i++) {
        const day = new Date(Date.UTC(2025, 2, 4 + 16 * i)).toISOString().slice(0, 10);
        releases.append(api.node("li", "1.0." + i + " · " + day));
      }
      const state = api.state();
      if (state?.workspace?.published) {
        const latest = api.node("li", state.new_version);
        latest.append(api.node("pre", state.workspace.notes));
        releases.append(latest);
      }
      site.append(releases);
    }
    window.addEventListener("desktopstatus", () => { if (!site.hidden) renderSite(); });
    const entries = [];
    let cursor = -1;
    let expected = null;
    function buttons() {
      back.disabled = cursor <= 0;
      forward.disabled = cursor + 1 >= entries.length;
    }
    function remember(value) {
      if (entries[cursor] !== value) {
        entries.splice(cursor + 1, Infinity, value);
        cursor++;
      }
      buttons();
    }
    function navigate(value) {
      const path = localPath(value);
      address.value = value;
      missing.hidden = Boolean(path);
      site.hidden = path !== registryPath;
      frame.hidden = !path || !site.hidden;
      if (!site.hidden) {
        expected = null;
        frame.removeAttribute("src");
        renderSite();
        remember(value);
        return;
      }
      expected = path;
      if (path) frame.src = path;
      else {
        frame.removeAttribute("src");
        remember(value);
      }
    }
    function step(offset) {
      cursor += offset;
      navigate(entries[cursor]);
      buttons();
    }
    const back = api.control("Back", "arrow-left", () => step(-1));
    const forward = api.control("Forward", "arrow-right", () => step(1));
    const reload = api.control("Reload", "rotate-cw", () => navigate(address.value));
    bar.append(back, forward, reload, address);
    bar.addEventListener("submit", event => { event.preventDefault(); navigate(address.value.trim()); });
    frame.addEventListener("load", () => {
      if (frame.hidden || !frame.hasAttribute("src")) return;
      const url = frame.contentWindow.location;
      if (url.href === "about:blank") return;
      if (url.origin === location.origin && url.pathname === "/") { location.replace("/"); return; }
      // The portal's POST-only login path can render a validation message.
      const value = addressFor((url.pathname === "/harborstone/login" ? "/harborstone" : url.pathname) + url.search);
      if (!value) { navigate("Unknown address"); return; }
      frame.contentWindow.addEventListener("focus", () => api.focus("browser"));
      if (url.pathname === "/harborstone/account") accountVisited();
      expected = null;
      address.value = value;
      remember(value);
    });
    buttons();
    body.append(bar, missing, site, frame);
    api.panel("browser").addEventListener("desktopopen", () => {
      if (cursor < 0 && !expected) navigate(origin + "/suppliers");
    });
    return path => {
      const value = addressFor(path);
      if (value) { api.open("browser"); navigate(value); }
    };
  }
  return {localPath, addressFor, mount};
})();
if (typeof module !== "undefined") module.exports = Harborstone;
