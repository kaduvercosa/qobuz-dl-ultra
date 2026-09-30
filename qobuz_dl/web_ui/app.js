/* qobuz-dl-ultra · GUI V2 — vanilla JS, sem build. Tudo vem da API real (webapp.py). */
(function () {
  "use strict";

  /* ------------------------------------------------------------ utilidades */
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (t) => { const s = Math.max(0, Math.floor(Number(t) || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* sem storage */ } },
    raw(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    setRaw(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sem storage */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* sem storage */ } },
  };
  function fmtRelative(ts) {
    if (!ts) return "—";
    const m = Math.round((Date.now() - Number(ts) * 1000) / 60000);
    if (m < 1) return "agora"; if (m < 60) return "há " + m + " min";
    const h = Math.round(m / 60); if (h < 24) return "há " + h + " h";
    return "há " + Math.round(h / 24) + " d";
  }
  async function api(path, opt) {
    const o = opt || {};
    const res = await fetch(path, { method: o.method || "GET", headers: o.body ? { "Content-Type": "application/json" } : undefined, body: o.body ? JSON.stringify(o.body) : undefined });
    const text = await res.text(); let data = null;
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!res.ok) { const d = (data && (data.detail || data.message)) || "Erro " + res.status; throw new Error(typeof d === "string" ? d : JSON.stringify(d)); }
    return data;
  }
  function toast(msg, type, action) {
    const region = $("#toast-region"); if (!region) return;
    const n = document.createElement("div"); n.className = "toast " + (type || "info");
    n.innerHTML = "<span>" + esc(msg) + "</span>" + (action ? '<button type="button">' + esc(action.label) + "</button>" : "");
    region.appendChild(n);
    const kill = () => n.remove();
    if (action) $("button", n).addEventListener("click", () => { action.run(); kill(); });
    setTimeout(kill, action ? 12000 : 4500);
  }
  const icon = (id, cls) => '<svg class="ic' + (cls ? " " + cls : "") + '"><use href="#i-' + id + '"/></svg>';
  const busy = (on) => { const d = $("#live-dot"); if (d) d.classList.toggle("is-busy", !!on); };

  /* ------------------------------------------------------------ estado */
  const S = {
    status: null, settings: null,
    favAlbums: null, favTracks: null, local: null, localDir: "",
    search: { q: "", tracks: [], albums: [], loaded: false, tab: "all" },
    queue: [], queueBusy: false,
    lib: { source: store.get("qs-src", "all"), sort: store.get("qs-sort", "recent") },
    pb: { list: [], order: null, index: -1, shuffle: false, repeat: "off" },
    lyrics: { key: null, kind: "none", lines: [], active: -1 },
    npTab: "queue",
    toolJobs: [], activeJob: null,
    token: 0,
  };
  const LISTS = new Map(); let listSeq = 0;
  const registerList = (arr) => { const id = "l" + (++listSeq); LISTS.set(id, arr); if (LISTS.size > 40) LISTS.delete(LISTS.keys().next().value); return id; };

  /* ------------------------------------------------------------ tema e cor */
  const ACCENTS = ["#ff3b2f", "#ff7a00", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2", "#ffffff"];
  function inkFor(hex) { const n = parseInt(hex.slice(1), 16); const l = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; return l > 0.6 ? "#000" : "#fff"; }
  function applyAccent(hex, persist) {
    document.documentElement.style.setProperty("--accent", hex);
    document.documentElement.style.setProperty("--accent-ink", inkFor(hex));
    if (persist) store.setRaw("qs-accent", hex);
    $$("#accent-swatches [data-accent]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.accent.toLowerCase() === hex.toLowerCase())));
  }
  const resolvedTheme = () => document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  function syncThemeMeta() { const m = $("#theme-color-meta"); if (m) m.setAttribute("content", resolvedTheme() === "light" ? "#f5f5f2" : "#000000"); }
  function setTheme(mode) {
    if (mode === "system") { document.documentElement.removeAttribute("data-theme"); store.del("qs-theme"); }
    else { document.documentElement.setAttribute("data-theme", mode); store.setRaw("qs-theme", mode); }
    const sel = $("#pref-theme"); if (sel) sel.value = mode; syncThemeMeta();
  }
  function initTheme() {
    syncThemeMeta();
    const sel = $("#pref-theme"); sel.value = store.raw("qs-theme") || "system";
    sel.addEventListener("change", () => setTheme(sel.value));
    $("#theme-toggle").addEventListener("click", () => setTheme(resolvedTheme() === "light" ? "dark" : "light"));
    matchMedia("(prefers-color-scheme: light)").addEventListener("change", syncThemeMeta);
    const box = $("#accent-swatches");
    box.innerHTML = ACCENTS.map((c) => '<button type="button" class="swatch" role="radio" aria-checked="false" aria-label="Cor ' + c + '" data-accent="' + c + '" style="background:' + c + '"></button>').join("") +
      '<input type="color" class="swatch-custom" id="accent-custom" aria-label="Cor personalizada" value="#ff3b2f">';
    box.addEventListener("click", (e) => { const b = e.target.closest("[data-accent]"); if (b) applyAccent(b.dataset.accent, true); });
    $("#accent-custom").addEventListener("input", (e) => applyAccent(e.target.value, true));
    applyAccent(store.raw("qs-accent") || ACCENTS[0], false);
  }

  /* ------------------------------------------------------------ componentes */
  function cover(url, cls, fallback) {
    const inner = url ? '<img src="' + esc(url) + '" alt="" loading="lazy" decoding="async">' : icon(fallback || "music");
    return '<div class="cover ' + (cls || "") + (url ? "" : " dots") + '">' + inner + "</div>";
  }
  const TYPE_LABEL = { single: "Single", ep: "EP", live: "Ao vivo", compilation: "Compilação", album: "Álbum" };
  function typeLabel(t) { return TYPE_LABEL[String(t || "").toLowerCase()] || ""; }
  const isSingle = (a) => String(a.type || "").toLowerCase() === "single" || (a.source === "local" && a.tracks.length === 1);

  function albumCard(a) {
    const href = "#/album/" + encodeURIComponent(a.id);
    const t = typeLabel(a.type);
    const meta2 = [a.year, t && t !== "Álbum" ? t : "", a.source === "local" ? "Local" : "Qobuz"].filter(Boolean).join(" · ");
    return '<a class="card" href="' + href + '">' + cover(a.cover, "") + '<div class="card-title">' + esc(a.title) + '</div><div class="card-meta"><span>' + esc(a.artist) + '</span></div><div class="card-meta"><span>' + esc(meta2) + "</span></div></a>";
  }
  function artistCard(x) {
    return '<a class="card" href="#/artist/' + encodeURIComponent(x.name) + '">' + cover(x.cover, "round", "user") + '<div class="card-title">' + esc(x.name) + '</div><div class="card-meta"><span>' + x.count + (x.count === 1 ? " álbum" : " álbuns") + "</span></div></a>";
  }
  function emptyBox(ic, title, text, action) {
    return '<div class="empty">' + icon(ic) + "<b>" + esc(title) + "</b><p>" + esc(text) + "</p>" + (action || "") + "</div>";
  }
  function trackRow(t, i, listId, opt) {
    const o = opt || {}; const key = t.source + ":" + (t.key || t.id);
    const playing = S.pb.list[S.pb.index] && trackKey(S.pb.list[S.pb.index]) === key;
    const dl = t.source === "qobuz" ? '<button class="icon-btn" data-dl="' + esc(t.id) + '" data-title="' + esc(t.title) + '" data-artist="' + esc(t.artist) + '" title="Baixar" aria-label="Baixar">' + icon("download") + "</button>" : "";
    return '<li class="trow' + (playing ? " playing" : "") + '" data-tk="' + esc(key) + '">' +
      (o.select ? '<input type="checkbox" class="t-check" data-sel="' + esc(t.id) + '" aria-label="Selecionar">' : "") +
      '<button class="t-idx" data-play="' + listId + ":" + i + '" aria-label="Reproduzir"><span class="n">' + (o.index != null ? o.index : i + 1) + '</span><span class="eq"><i></i><i></i><i></i></span></button>' +
      '<div class="t-main"><span class="t-title">' + esc(t.title) + '</span><span class="t-sub">' + esc([t.artist, o.showAlbum && t.album ? t.album : ""].filter(Boolean).join(" · ")) + "</span></div>" +
      '<span class="chip t-q">' + esc(t.source === "local" ? (t.format || "local").toUpperCase() : (t.quality || "Qobuz")) + "</span>" +
      '<span class="t-dur">' + fmtTime(t.duration) + "</span>" +
      '<div class="t-actions">' + dl + "</div></li>";
  }
  const trackKey = (t) => t.source + ":" + (t.key || t.id);
  const normQ = (t) => Object.assign({ source: "qobuz" }, t);
  const normLocal = (f) => ({ source: "local", id: f.key, key: f.key, title: f.title, artist: f.artist, album: f.album, duration: f.duration, cover: f.cover, format: f.format });

  /* ------------------------------------------------------------ dados */
  async function loadFav() {
    if (S.favAlbums) return;
    try { const d = await api("/api/favorites?kind=albums&limit=100"); S.favAlbums = (d.items || []).map((a) => Object.assign({}, a, { source: "qobuz", tracks: [] })); }
    catch (e) { S.favAlbums = []; if (S.status && S.status.connected) toast(e.message, "error"); }
  }
  async function loadFavTracks() {
    if (S.favTracks) return;
    try { const d = await api("/api/favorites?kind=tracks&limit=100"); S.favTracks = (d.items || []).map(normQ); } catch (e) { S.favTracks = []; }
  }
  async function loadLocal(force) {
    if (S.local && !force) return;
    try { const d = await api("/api/library"); S.local = (d.items || []).map(normLocal); S.localDir = d.directory || ""; } catch (e) { S.local = []; }
  }
  function localAlbums() {
    const map = new Map();
    (S.local || []).forEach((t) => {
      const title = t.album || "Sem álbum"; const id = "l:" + encodeURIComponent(t.artist + "||" + title);
      if (!map.has(id)) map.set(id, { id, title, artist: t.artist, year: "", type: "", cover: t.cover, source: "local", tracks: [] });
      map.get(id).tracks.push(t);
    });
    return Array.from(map.values());
  }
  function allAlbums() { return (S.favAlbums || []).concat(localAlbums()); }
  function artistsFrom(albums) {
    const map = new Map();
    albums.forEach((a) => { a.artist.split(/,\s*/).slice(0, 1).forEach((n) => { const k = n.toLowerCase(); if (!map.has(k)) map.set(k, { name: n, cover: a.cover, count: 0 }); map.get(k).count += 1; }); });
    return Array.from(map.values());
  }
  function sortItems(items, sort, keyTitle, keyArtist, keyYear) {
    const c = (a, b) => String(a || "").localeCompare(String(b || ""), "pt", { sensitivity: "base" });
    const arr = items.slice();
    if (sort === "az") arr.sort((a, b) => c(keyTitle(a), keyTitle(b)));
    else if (sort === "artist") arr.sort((a, b) => c(keyArtist(a), keyArtist(b)) || c(keyTitle(a), keyTitle(b)));
    else if (sort === "year") arr.sort((a, b) => Number(keyYear(b) || 0) - Number(keyYear(a) || 0));
    return arr; // "recent" mantém a ordem recebida (mais recentes primeiro)
  }

  /* ------------------------------------------------------------ roteador */
  function parseRoute() {
    const raw = location.hash.replace(/^#\/?/, ""); const [path, qs] = raw.split("?");
    const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch (e) { return p; } });
    return { name: parts[0] || "home", parts: parts.slice(1), query: new URLSearchParams(qs || "") };
  }
  function markNav(r) {
    const sub = r.name === "library" ? (r.parts[0] || "albums") : r.name === "downloads" ? (r.parts[0] || "queue") : "";
    const route = r.name === "album" || r.name === "artist" ? "library" : r.name;
    $$("[data-route]").forEach((el) => {
      const on = el.dataset.route === route && (!el.dataset.sub || el.dataset.sub === sub);
      el.classList.toggle("active", on);
      if (on) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
    });
  }
  async function render() {
    const r = parseRoute(); const token = ++S.token; markNav(r);
    const dyn = $("#view-dynamic"), tools = $("#view-tools");
    tools.hidden = r.name !== "tools"; dyn.hidden = r.name === "tools";
    if (r.name !== "search") { const si = $("#top-search"); if (si && document.activeElement !== si) si.value = ""; }
    if (r.name === "tools") { loadToolJobs(); scrollTop(); return; }
    busy(true);
    try {
      let html = "";
      if (r.name === "search") html = await viewSearch(r);
      else if (r.name === "library") html = await viewLibrary(r);
      else if (r.name === "album") html = await viewAlbum(r);
      else if (r.name === "artist") html = await viewArtist(r);
      else if (r.name === "downloads") html = viewDownloads(r);
      else html = await viewHome();
      if (token !== S.token) return;
      dyn.innerHTML = html; afterRender(r); scrollTop();
    } catch (e) {
      if (token === S.token) dyn.innerHTML = '<section class="page">' + emptyBox("x", "Não foi possível carregar", e.message) + "</section>";
    } finally { if (token === S.token) busy(false); }
  }
  const scrollTop = () => window.scrollTo(0, 0);
  function afterRender(r) {
    if (r.name === "search") { const i = $("#top-search"); if (document.activeElement !== i && !r.query.get("q")) i.focus({ preventScroll: true }); }
  }

  /* ------------------------------------------------------------ views */
  async function viewHome() {
    await Promise.all([loadFav(), loadLocal()]);
    const last = store.get("qs-last", null);
    const pending = S.queue.filter((q) => q.status === "aguardando" || q.status === "baixando").length;
    const done = S.queue.filter((q) => q.status === "concluído").length;
    const st = S.status || {};
    const resume = last
      ? '<div class="resume">' + cover(last.cover, "", "music") + '<div class="resume-copy"><span class="label">Continuar ouvindo</span><b>' + esc(last.title) + '</b><span class="muted">' + esc(last.artist || "") + '</span><div class="row"><button class="btn accent sm" data-resume>' + icon("play") + "Continuar</button></div></div></div>"
      : emptyBox("music", "Nada tocado ainda", "Escolha um álbum ou faixa para começar.");
    const recent = allAlbums().slice(0, 14);
    const local = (S.local || []).slice(0, 6); const lid = registerList(local);
    return '<section class="page"><header class="page-head"><p class="label">Início</p><h1 class="page-title">qobuz-dl</h1></header>' +
      '<div class="modules">' +
      '<div class="panel module m-resume">' + resume + "</div>" +
      '<div class="panel module m-dl"><span class="label">Downloads</span><div class="big-num">' + pending + '</div><div class="stat-line"><span><b>' + pending + '</b> na fila</span><span><b>' + done + '</b> concluídos</span></div><div class="row"><a class="btn sm" href="#/downloads/queue">Abrir fila</a></div></div>' +
      '<div class="panel module m-server"><span class="label">Servidor</span><div class="kv"><div><span class="label">Estado</span><b>' + (st.demo ? "Demonstração" : st.connected ? "Conectado ao Qobuz" : "Sem conta conectada") + '</b></div><div><span class="label">Endereço</span><b>' + esc(location.host) + '</b></div><div><span class="label">Pasta</span><b>' + esc(S.localDir || st.directory || "—") + '</b></div><div><span class="label">Qualidade</span><b>' + esc(qualityName(st.quality)) + "</b></div></div></div>" +
      "</div>" +
      (recent.length ? '<div class="section-head"><h2>Álbuns recentes</h2><a class="btn ghost sm" href="#/library/albums">Ver tudo</a></div><div class="strip">' + recent.map(albumCard).join("") + "</div>" : "") +
      (local.length ? '<div class="section-head"><h2>Faixas locais recentes</h2><a class="btn ghost sm" href="#/library/tracks">Ver tudo</a></div><ol class="list">' + local.map((t, i) => trackRow(t, i, lid, { showAlbum: true })).join("") + "</ol>" : "") +
      "</section>";
  }
  const qualityName = (q) => ({ 5: "MP3 320", 6: "FLAC 16/44.1", 7: "Hi-Res 24/96", 27: "Hi-Res 24/192" }[Number(q)] || "—");

  function libToolbar(r) {
    const sub = r.parts[0] || "albums";
    const sources = [["all", "Todos"], ["qobuz", "Qobuz"], ["local", "Local"]];
    const sorts = [["recent", "Recentes"], ["az", "A–Z"], ["artist", "Artista"], ["year", "Ano"]];
    return '<div class="toolbar"><div class="group"><span class="label">Origem</span>' + sources.map(([k, l]) => '<button type="button" class="chip' + (S.lib.source === k ? " on" : "") + '" data-src="' + k + '">' + l + "</button>").join("") + "</div>" +
      (sub === "artists" || sub === "playlists" ? "" : '<div class="group"><span class="label">Ordem</span>' + sorts.map(([k, l]) => '<button type="button" class="chip' + (S.lib.sort === k ? " on" : "") + '" data-sort="' + k + '">' + l + "</button>").join("") + "</div>") + "</div>";
  }
  async function viewLibrary(r) {
    const sub = ["albums", "artists", "singles", "playlists", "tracks"].includes(r.parts[0]) ? r.parts[0] : "albums";
    await Promise.all([loadFav(), loadLocal(), sub === "tracks" ? loadFavTracks() : null]);
    const tabs = [["albums", "Álbuns"], ["artists", "Artistas"], ["singles", "Singles"], ["playlists", "Playlists"], ["tracks", "Faixas"]];
    const src = S.lib.source; const pick = (x) => src === "all" || x.source === src;
    let body = "";
    if (sub === "albums" || sub === "singles") {
      let items = allAlbums().filter(pick).filter((a) => (sub === "singles") === isSingle(a));
      items = sortItems(items, S.lib.sort, (a) => a.title, (a) => a.artist, (a) => a.year);
      body = items.length ? '<div class="grid">' + items.map(albumCard).join("") + "</div>" :
        emptyBox("library", sub === "singles" ? "Nenhum single" : "Nenhum álbum", src === "local" ? "Escolha a pasta da biblioteca em Preferências para ver os arquivos locais." : "Favorite álbuns no Qobuz ou baixe músicas para vê-los aqui.");
    } else if (sub === "artists") {
      const items = artistsFrom(allAlbums().filter(pick)).sort((a, b) => a.name.localeCompare(b.name, "pt", { sensitivity: "base" }));
      body = items.length ? '<div class="grid">' + items.map(artistCard).join("") + "</div>" : emptyBox("user", "Nenhum artista", "Os artistas aparecem conforme sua biblioteca cresce.");
    } else if (sub === "playlists") {
      body = emptyBox("queue", "Playlists ainda não disponíveis", "Esta versão não lê playlists do Qobuz nem cria playlists locais. Para importar ou sincronizar, use Ferramentas.", '<a class="btn sm" href="#/tools">Abrir Ferramentas</a>');
    } else {
      let items = ((src !== "local" ? S.favTracks || [] : []).concat(src !== "qobuz" ? S.local || [] : []));
      items = sortItems(items, S.lib.sort, (t) => t.title, (t) => t.artist, () => 0);
      const id = registerList(items);
      body = items.length ? '<ol class="list">' + items.map((t, i) => trackRow(t, i, id, { showAlbum: true })).join("") + "</ol>" : emptyBox("music", "Nenhuma faixa", "Nada encontrado para este filtro.");
    }
    return '<section class="page"><header class="page-head"><p class="label">Biblioteca</p><h1 class="page-title">' + esc(tabs.find((t) => t[0] === sub)[1]) + "</h1></header>" +
      '<nav class="tabs" aria-label="Seções da biblioteca">' + tabs.map(([k, l]) => '<a class="tab' + (k === sub ? " active" : "") + '" href="#/library/' + k + '">' + l + "</a>").join("") + "</nav>" + libToolbar(r) + body + "</section>";
  }

  async function viewSearch(r) {
    const q = (r.query.get("q") || "").trim();
    const input = $("#top-search"); if (input && document.activeElement !== input) input.value = q;
    let content = "";
    if (q.length < 2) content = emptyBox("search", "Pesquise no catálogo", "Digite ao menos 2 letras para buscar artistas, álbuns e faixas do Qobuz.");
    else {
      if (S.search.q !== q || !S.search.loaded) {
        const d = await api("/api/search?q=" + encodeURIComponent(q) + "&kind=all&limit=24");
        S.search = { q, tracks: (d.tracks || []).map(normQ), albums: (d.albums || []).map((a) => Object.assign({}, a, { source: "qobuz", tracks: [] })), loaded: true, tab: S.search.tab };
      }
      const tab = S.search.tab; const showA = tab !== "tracks", showT = tab !== "albums";
      const lid = registerList(S.search.tracks);
      const a = showA && S.search.albums.length ? '<div class="section-head"><h2>Álbuns</h2></div><div class="grid">' + S.search.albums.map(albumCard).join("") + "</div>" : "";
      const t = showT && S.search.tracks.length ? '<div class="section-head"><h2>Faixas</h2></div><ol class="list">' + S.search.tracks.map((x, i) => trackRow(x, i, lid, { showAlbum: true })).join("") + "</ol>" : "";
      content = a + t || emptyBox("search", "Nada encontrado", "Tente outro nome de artista, álbum ou faixa.");
    }
    return '<section class="page"><header class="page-head"><p class="label">Pesquisar</p><h1 class="page-title">' + (q ? esc(q) : "Buscar") + "</h1></header>" +
      '<nav class="tabs">' + [["all", "Tudo"], ["tracks", "Faixas"], ["albums", "Álbuns"]].map(([k, l]) => '<button type="button" class="tab' + (S.search.tab === k ? " active" : "") + '" data-stab="' + k + '">' + l + "</button>").join("") + "</nav>" + content + "</section>";
  }

  async function viewAlbum(r) {
    const id = r.parts[0] || ""; let album, tracks;
    if (id.startsWith("l:")) {
      await loadLocal(); album = localAlbums().find((a) => a.id === id);
      if (!album) return '<section class="page">' + emptyBox("x", "Álbum não encontrado", "Ele pode ter sido removido da pasta local.") + "</section>";
      tracks = album.tracks;
    } else {
      const d = await api("/api/album/" + encodeURIComponent(id));
      album = Object.assign({}, d.album, { source: "qobuz" });
      tracks = (d.tracks || []).map((t) => Object.assign(normQ(t), { album: t.album || album.title, cover: t.cover || album.cover }));
    }
    const lid = registerList(tracks); const total = tracks.reduce((s, t) => s + (Number(t.duration) || 0), 0);
    const qual = album.source === "qobuz" ? [album.quality, typeLabel(album.type)] : [(tracks[0] && tracks[0].format ? tracks[0].format.toUpperCase() : "LOCAL")];
    return '<section class="page"><a class="back" href="javascript:history.back()">' + icon("back") + "Voltar</a>" +
      '<div class="hero">' + cover(album.cover, "", "music") + '<div class="hero-copy"><span class="label">' + (album.source === "local" ? "Álbum local" : "Álbum") + '</span><h1 class="hero-title">' + esc(album.title) + '</h1><a class="hero-artist" href="#/artist/' + encodeURIComponent(album.artist.split(/,\s*/)[0]) + '">' + esc(album.artist) + "</a>" +
      '<div class="meta-line">' + [album.year, tracks.length + " faixas", total ? fmtTime(total) : "", album.genre].filter(Boolean).map((x) => "<span>" + esc(x) + "</span>").join("") + "</div>" +
      '<div class="tech">' + qual.filter(Boolean).map((x) => '<span class="chip hi">' + esc(x) + "</span>").join("") + "</div>" +
      '<div class="row"><button class="btn accent" data-play="' + lid + ':0">' + icon("play") + "Reproduzir</button>" +
      (album.source === "qobuz" ? '<button class="btn" data-dl-album="' + esc(album.id) + '" data-title="' + esc(album.title) + '" data-artist="' + esc(album.artist) + '">' + icon("download") + 'Baixar álbum</button><button class="btn ghost" data-fav="' + esc(album.id) + '">' + icon("plus") + "Favoritar</button>" : "") +
      '<button class="btn ghost" data-select-toggle>Selecionar</button></div></div></div>' +
      '<div class="row" id="select-bar" hidden><span class="muted small" id="select-count">0 selecionadas</span><button class="btn sm accent" data-dl-selected>' + icon("download") + 'Baixar selecionadas</button></div>' +
      '<ol class="list" id="album-tracks">' + tracks.map((t, i) => trackRow(t, i, lid, { select: false })).join("") + "</ol></section>";
  }

  async function viewArtist(r) {
    const name = r.parts[0] || ""; await Promise.all([loadFav(), loadLocal()]);
    const albums = allAlbums().filter((a) => a.artist.toLowerCase().includes(name.toLowerCase()));
    const tracks = (S.local || []).filter((t) => t.artist.toLowerCase().includes(name.toLowerCase()));
    const lid = registerList(tracks);
    return '<section class="page"><a class="back" href="javascript:history.back()">' + icon("back") + "Voltar</a>" +
      '<header class="page-head"><p class="label">Artista</p><h1 class="page-title">' + esc(name) + "</h1></header>" +
      (albums.length ? '<div class="section-head"><h2>Álbuns</h2></div><div class="grid">' + albums.map(albumCard).join("") + "</div>" : emptyBox("user", "Nada na sua biblioteca", "Use Pesquisar para encontrar a discografia no catálogo.", '<a class="btn sm" href="#/search?q=' + encodeURIComponent(name) + '">Pesquisar “' + esc(name) + "”</a>")) +
      (tracks.length ? '<div class="section-head"><h2>Faixas locais</h2></div><ol class="list">' + tracks.map((t, i) => trackRow(t, i, lid, { showAlbum: true })).join("") + "</ol>" : "") + "</section>";
  }

  function statusChip(s) {
    const m = { aguardando: ["", "Na fila"], baixando: ["on", "Baixando"], "concluído": ["ok", "Concluído"], falhou: ["bad", "Falhou"], interrompido: ["bad", "Interrompido"] };
    const [c, l] = m[s] || ["", s || "—"]; return '<span class="chip ' + c + '">' + l + "</span>";
  }
  function dlItem(q) {
    const removable = q.status === "aguardando";
    return '<div class="panel dl-item">' + cover(q.cover, "md", q.kind === "album" ? "library" : "music") +
      '<div class="dl-copy"><b>' + esc(q.title) + "</b><small>" + esc([q.artist, q.kind === "album" ? "álbum" : "faixa", fmtRelative(q.createdAt)].filter(Boolean).join(" · ")) + (q.message ? " · " + esc(q.message) : "") + "</small></div>" +
      '<div class="dl-side">' + statusChip(q.status) + (removable ? '<button class="icon-btn" data-remove-queue="' + esc(q.id) + '" aria-label="Remover da fila">' + icon("x") + "</button>" : "") + "</div>" +
      (q.status === "baixando" ? '<div class="progress" role="progressbar" aria-label="Baixando"><i></i></div>' : "") + "</div>";
  }
  function viewDownloads(r) {
    const sub = r.parts[0] === "history" ? "history" : "queue";
    const active = S.queue.filter((q) => q.status === "aguardando" || q.status === "baixando");
    const past = S.queue.filter((q) => !(q.status === "aguardando" || q.status === "baixando")).slice().reverse();
    const list = sub === "queue" ? active : past;
    const cnt = (s) => S.queue.filter((q) => q.status === s).length;
    return '<section class="page"><header class="page-head"><p class="label">Downloads</p><h1 class="page-title">' + (sub === "queue" ? "Fila" : "Histórico") + "</h1></header>" +
      '<div class="sum"><div class="panel"><span class="label">Na fila</span><div class="big-num">' + cnt("aguardando") + '</div></div><div class="panel"><span class="label">Baixando</span><div class="big-num">' + cnt("baixando") + '</div></div><div class="panel"><span class="label">Concluídos</span><div class="big-num">' + cnt("concluído") + "</div></div></div>" +
      '<nav class="tabs"><a class="tab' + (sub === "queue" ? " active" : "") + '" href="#/downloads/queue">Fila</a><a class="tab' + (sub === "history" ? " active" : "") + '" href="#/downloads/history">Histórico</a></nav>' +
      (list.length ? list.map(dlItem).join("") : emptyBox("download", sub === "queue" ? "Fila vazia" : "Nada no histórico", "Baixe um álbum ou faixa pela Pesquisa ou pela Biblioteca.", '<a class="btn sm" href="#/search">Pesquisar</a>')) +
      '<p class="hint">O servidor informa só o estado de cada item; o progresso por bytes ainda não é reportado.</p></section>';
  }

  /* ------------------------------------------------------------ status / conta / preferências */
  async function loadStatus() {
    try { S.status = await api("/api/status"); } catch (e) { S.status = null; }
    renderStatus();
  }
  function renderStatus() {
    const s = S.status || {}; const ok = !!s.connected || !!s.demo;
    $("#status-dot").className = "status-dot " + (s.connected ? "ok" : "warn");
    $("#status-label").textContent = s.demo ? "Demonstração" : s.connected ? "Conectado" : S.status ? (s.configured ? "Pronto para conectar" : "Sem conta") : "Sem resposta";
    $("#status-detail").textContent = location.host;
    const pill = $("#connection-pill"); pill.className = "pill " + (s.connected ? "ok" : "warn"); $("#connection-pill-label").textContent = s.demo ? "Demo" : s.connected ? "Conectado" : "Offline";
    $("#demo-banner").hidden = !s.demo;
    const row = $("#settings-account-status"); row.classList.toggle("ok", !!s.connected);
    $("#settings-account-status-note").textContent = s.demo ? "Prévia demonstrativa." : s.connected ? "Conta conectada e pronta." : s.configured ? "Configurada, mas ainda não conectada." : "Ainda não configurada.";
    $("#settings-connect").hidden = ok;
    $("#config-footnote").textContent = s.configDir ? "Configuração em: " + s.configDir : "";
  }
  async function connectAccount() {
    const b = $("#settings-connect"); b.disabled = true;
    try { const r = await api("/api/connect", { method: "POST" }); toast(r.connected ? "Conta conectada." : (r.message || "Não foi possível conectar."), r.connected ? "success" : "info"); await loadStatus(); if (r.connected) { S.favAlbums = null; render(); } }
    catch (e) { toast(e.message, "error"); } finally { b.disabled = false; }
  }
  const BOOLS = { "setting-embed-art": "embed_art", "setting-lyrics": "fetch_lyrics", "setting-lrc": "lrc_files", "setting-credits": "credits", "setting-m3u": "m3u", "setting-fallback": "quality_fallback", "setting-playlist-albums": "playlist_as_albums", "setting-verify": "verify_after_download", "setting-no-cover": "no_cover", "setting-smart-discography": "smart_discography", "setting-multi-tags": "multi_value_tags" };
  async function loadSettings() { try { S.settings = await api("/api/settings"); fillSettings(); } catch (e) { toast("Não foi possível carregar as preferências.", "error"); } }
  function fillSettings() {
    const s = S.settings; if (!s) return;
    $("#settings-directory").value = s.directory || ""; $("#settings-quality").value = String(s.quality || 6);
    Object.entries(BOOLS).forEach(([id, k]) => { $("#" + id).checked = !!s[k]; });
    $("#settings-max-workers").value = s.max_workers || 1; $("#settings-segment-workers").value = s.segment_workers || 4;
    $("#settings-embedded-size").value = s.embedded_art_size || "org"; $("#settings-saved-size").value = s.saved_art_size || "org";
    $("#settings-folder-format").value = s.folder_format || ""; $("#settings-track-format").value = s.track_format || "";
  }
  async function saveSettings() {
    const p = { directory: $("#settings-directory").value.trim(), quality: Number($("#settings-quality").value), max_workers: Number($("#settings-max-workers").value) || 1,
      segment_workers: Number($("#settings-segment-workers").value) || 4, embedded_art_size: $("#settings-embedded-size").value, saved_art_size: $("#settings-saved-size").value,
      folder_format: $("#settings-folder-format").value.trim(), track_format: $("#settings-track-format").value.trim() };
    if (!p.folder_format) delete p.folder_format; if (!p.track_format) delete p.track_format;
    Object.entries(BOOLS).forEach(([id, k]) => { p[k] = $("#" + id).checked; });
    if (!p.directory) { toast("Informe a pasta da biblioteca.", "error"); return; }
    const b = $("#save-settings"); b.disabled = true;
    try { S.settings = await api("/api/settings", { method: "POST", body: p }); fillSettings(); toast("Preferências salvas.", "success"); closeModal(); await loadStatus(); S.local = null; render(); }
    catch (e) { toast(e.message, "error"); } finally { b.disabled = false; }
  }
  async function saveAccount() {
    const email = $("#account-email").value.trim(), token = $("#account-token").value.trim();
    if (!email || !token) { toast("Informe e-mail e token.", "error"); return; }
    const b = $("#save-account"); b.disabled = true;
    try { await api("/api/account/configure", { method: "POST", body: { email, token, store_in_keyring: $("#account-keyring").checked } }); $("#account-token").value = ""; toast("Conta validada e salva.", "success"); await loadStatus(); S.favAlbums = null; render(); }
    catch (e) { toast(e.message, "error"); } finally { b.disabled = false; }
  }
  function openModal() { $("#modal-backdrop").hidden = false; document.body.style.overflow = "hidden"; }
  function closeModal() { $("#modal-backdrop").hidden = true; document.body.style.overflow = ""; }

  /* ------------------------------------------------------------ downloads */
  async function enqueue(item) {
    try { await api("/api/download", { method: "POST", body: item }); toast("“" + item.title + "” entrou na fila.", "success"); loadQueue(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function loadQueue() {
    try {
      const d = await api("/api/queue"); const before = JSON.stringify(S.queue.map((q) => q.id + q.status));
      S.queue = d.items || []; S.queueBusy = !!d.busy;
      const pending = S.queue.filter((q) => q.status === "aguardando" || q.status === "baixando").length;
      [$("#queue-count"), $("#queue-count-mobile")].forEach((el) => { el.textContent = String(pending); el.classList.toggle("on", pending > 0); });
      if (before !== JSON.stringify(S.queue.map((q) => q.id + q.status)) && parseRoute().name === "downloads") render();
    } catch (e) { /* polling silencioso */ }
  }

  /* ------------------------------------------------------------ player */
  const audio = $("#audio-element");
  const srcFor = (t) => t.source === "local" ? "/api/library/play/" + encodeURIComponent(t.key || t.id) : "/api/stream/" + encodeURIComponent(t.id) + "?quality=" + (S.settings && Number(S.settings.quality) === 5 ? 5 : 6);
  function order() {
    const p = S.pb; if (!p.shuffle) return p.list.map((_, i) => i);
    if (!p.order || p.order.length !== p.list.length) { const o = p.list.map((_, i) => i); for (let i = o.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [o[i], o[j]] = [o[j], o[i]]; } p.order = o; }
    return p.order;
  }
  function playList(list, index) { S.pb.list = list.slice(); S.pb.order = null; S.pb.index = index; loadCurrent(); }
  function loadCurrent() {
    const t = S.pb.list[S.pb.index]; if (!t) return;
    document.body.classList.add("has-player"); $("#player").hidden = false;
    audio.src = srcFor(t); audio.play().catch(() => {});
    store.set("qs-last", { source: t.source, id: t.id, key: t.key, title: t.title, artist: t.artist, album: t.album, cover: t.cover, duration: t.duration, format: t.format, quality: t.quality });
    renderPlayerMeta(); mediaSessionMeta(t); loadLyricsFor(t); markPlaying();
  }
  function renderPlayerMeta() {
    const t = S.pb.list[S.pb.index]; if (!t) return;
    $("#player-title").textContent = t.title; $("#player-artist").textContent = t.artist || "";
    $("#player-cover").innerHTML = t.cover ? '<img src="' + esc(t.cover) + '" alt="">' : icon("music");
    $("#player-quality").textContent = t.source === "local" ? (t.format || "local").toUpperCase() : (t.quality || "Qobuz");
    $("#np-title").textContent = t.title; $("#np-artist").textContent = t.artist || ""; $("#np-album").textContent = t.album || "";
    $("#np-quality").textContent = [t.source === "local" ? "Local" : "Qobuz", t.source === "local" ? (t.format || "").toUpperCase() : t.quality].filter(Boolean).join(" · ");
    $("#np-cover").innerHTML = t.cover ? '<img src="' + esc(t.cover) + '" alt="">' : icon("music");
    $("#np-cover").className = "cover xl" + (t.cover ? "" : " dots");
    renderQueuePane();
  }
  function markPlaying() {
    const cur = S.pb.list[S.pb.index]; const k = cur ? trackKey(cur) : "";
    $$(".trow").forEach((r) => r.classList.toggle("playing", r.dataset.tk === k));
  }
  function renderQueuePane() {
    const el = $("#np-queue"); const ord = order();
    el.innerHTML = ord.map((idx) => { const t = S.pb.list[idx]; return '<li class="' + (idx === S.pb.index ? "playing" : "") + '" data-qi="' + idx + '"><span class="t-idx mono">' + (idx === S.pb.index ? '<span class="eq" style="display:inline-flex"><i></i><i></i><i></i></span>' : idx + 1) + '</span><div class="t-main"><span class="t-title">' + esc(t.title) + '</span><span class="t-sub">' + esc(t.artist || "") + '</span></div><span class="t-dur">' + fmtTime(t.duration) + "</span><span></span></li>"; }).join("");
  }
  function setPlayIcon() {
    const use = audio.paused ? "#i-play" : "#i-pause";
    $$("#play-button use, #np-play use").forEach((u) => u.setAttribute("href", use));
    ["#play-button", "#np-play"].forEach((s) => $(s).setAttribute("aria-label", audio.paused ? "Reproduzir" : "Pausar"));
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = audio.paused ? "paused" : "playing";
  }
  function togglePlay() { if (!S.pb.list.length) return; if (audio.paused) audio.play().catch(() => {}); else audio.pause(); }
  function step(dir) {
    const p = S.pb; if (!p.list.length) return; const o = order(); let pos = o.indexOf(p.index) + dir;
    if (pos < 0) pos = p.repeat === "all" ? o.length - 1 : 0;
    if (pos >= o.length) { if (p.repeat === "all") pos = 0; else { audio.pause(); return; } }
    p.index = o[pos]; loadCurrent();
  }
  function fill(el, pct) { el.style.setProperty("--pct", pct.toFixed(2) + "%"); }
  function wirePlayer() {
    $("#play-button").addEventListener("click", togglePlay); $("#np-play").addEventListener("click", togglePlay);
    ["#next-button", "#np-next"].forEach((s) => $(s).addEventListener("click", () => step(1)));
    ["#previous-button", "#np-prev"].forEach((s) => $(s).addEventListener("click", () => { if (audio.currentTime > 3) audio.currentTime = 0; else step(-1); }));
    $("#shuffle-button").addEventListener("click", () => { S.pb.shuffle = !S.pb.shuffle; S.pb.order = null; $("#shuffle-button").classList.toggle("is-on", S.pb.shuffle); $("#shuffle-button").setAttribute("aria-pressed", String(S.pb.shuffle)); renderQueuePane(); });
    $("#repeat-button").addEventListener("click", () => { const seq = ["off", "all", "one"]; S.pb.repeat = seq[(seq.indexOf(S.pb.repeat) + 1) % 3]; $("#repeat-button").classList.toggle("is-on", S.pb.repeat !== "off"); $("#repeat-button").setAttribute("aria-pressed", String(S.pb.repeat !== "off")); $("#repeat-one").hidden = S.pb.repeat !== "one"; });
    audio.addEventListener("play", setPlayIcon); audio.addEventListener("pause", setPlayIcon);
    audio.addEventListener("ended", () => { if (S.pb.repeat === "one") { audio.currentTime = 0; audio.play().catch(() => {}); } else step(1); });
    audio.addEventListener("loadedmetadata", () => { const d = fmtTime(audio.duration); $("#total-time").textContent = d; $("#np-total").textContent = d; });
    audio.addEventListener("timeupdate", () => {
      if (!audio.duration) return; const pct = (audio.currentTime / audio.duration) * 100; const c = fmtTime(audio.currentTime);
      $("#current-time").textContent = c; $("#np-current").textContent = c;
      [$("#progress-slider"), $("#np-seek")].forEach((s) => { if (document.activeElement !== s) s.value = String(pct * 10); fill(s, pct); });
      $("#player-bar-fill").style.width = pct.toFixed(2) + "%"; updateLyricsActive(); updatePosition();
    });
    audio.addEventListener("error", () => { if (S.pb.list.length) toast("Não foi possível reproduzir esta faixa.", "error"); });
    [$("#progress-slider"), $("#np-seek")].forEach((s) => {
      s.addEventListener("input", () => fill(s, Number(s.value) / 10));
      s.addEventListener("change", () => { if (audio.duration) audio.currentTime = (Number(s.value) / 1000) * audio.duration; });
    });
    const vol = $("#volume-slider"); const v = store.get("qs-vol", 80); vol.value = String(v); audio.volume = v / 100; fill(vol, v);
    vol.addEventListener("input", () => { audio.volume = Number(vol.value) / 100; fill(vol, Number(vol.value)); store.set("qs-vol", Number(vol.value)); audio.muted = false; syncMute(); });
    $("#volume-button").addEventListener("click", () => { audio.muted = !audio.muted; syncMute(); });
    $("#player-open").addEventListener("click", openNP); $("#player-queue-open").addEventListener("click", () => { openNP(); setNpTab("queue"); });
  }
  function syncMute() { $("#volume-button use").setAttribute("href", audio.muted || audio.volume === 0 ? "#i-mute" : "#i-volume"); }

  /* Media Session: controles do sistema (tela de bloqueio, fones, Central de Controle) */
  function mediaSessionMeta(t) {
    if (!("mediaSession" in navigator)) return;
    try {
      const art = t.cover ? [{ src: new URL(t.cover, location.href).href, sizes: "512x512" }] : [];
      navigator.mediaSession.metadata = new MediaMetadata({ title: t.title || "", artist: t.artist || "", album: t.album || "", artwork: art });
    } catch (e) { /* MediaMetadata indisponível */ }
  }
  function updatePosition() {
    if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState || !isFinite(audio.duration) || !audio.duration) return;
    try { navigator.mediaSession.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate || 1 }); } catch (e) { /* ignora */ }
  }
  function initMediaSession() {
    if (!("mediaSession" in navigator)) return;
    const set = (a, fn) => { try { navigator.mediaSession.setActionHandler(a, fn); } catch (e) { /* ação não suportada */ } };
    set("play", () => audio.play().catch(() => {})); set("pause", () => audio.pause());
    set("previoustrack", () => step(-1)); set("nexttrack", () => step(1));
    set("seekbackward", (d) => { audio.currentTime = Math.max(0, audio.currentTime - ((d && d.seekOffset) || 10)); });
    set("seekforward", (d) => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + ((d && d.seekOffset) || 10)); });
    set("seekto", (d) => { if (d && typeof d.seekTime === "number") audio.currentTime = d.seekTime; });
  }

  /* Tocando agora + letras */
  function openNP() { if (!S.pb.list.length) return; $("#now-playing").hidden = false; document.body.style.overflow = "hidden"; $("#np-close").focus(); if (S.npTab === "lyrics") scrollLyrics(); }
  function closeNP() { $("#now-playing").hidden = true; document.body.style.overflow = ""; }
  function setNpTab(tab) { S.npTab = tab; $$("[data-np-tab]").forEach((b) => b.classList.toggle("active", b.dataset.npTab === tab)); $("#np-queue").hidden = tab !== "queue"; $("#np-lyrics").hidden = tab !== "lyrics"; if (tab === "lyrics") scrollLyrics(); }
  function parseLrc(text) {
    const out = [];
    text.split(/\r?\n/).forEach((line) => {
      const tags = Array.from(line.matchAll(/\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g)); const body = line.replace(/\[[^\]]*\]/g, "").trim();
      if (!tags.length || !body) return;
      tags.forEach((m) => out.push({ t: Number(m[1]) * 60 + Number(m[2]) + (m[3] ? Number(m[3].padEnd(3, "0")) / 1000 : 0), text: body }));
    });
    return out.sort((a, b) => a.t - b.t);
  }
  async function loadLyricsFor(t) {
    const box = $("#np-lyrics"); const key = trackKey(t); S.lyrics = { key, kind: "none", lines: [], active: -1 };
    if (t.source !== "local") { box.className = "lyrics plain"; box.innerHTML = '<p class="muted">Letras ficam disponíveis para arquivos locais (.lrc ao lado do áudio ou letra embutida). Faixas transmitidas do Qobuz não trazem letra nesta versão.</p>'; return; }
    box.className = "lyrics plain"; box.innerHTML = '<p class="muted">Procurando letra…</p>';
    try {
      const d = await api("/api/library/lyrics/" + encodeURIComponent(t.key || t.id)); if (S.lyrics.key !== key) return;
      if (d.kind === "synced") { const lines = parseLrc(d.text); S.lyrics = { key, kind: "synced", lines, active: -1 }; box.className = "lyrics"; box.innerHTML = lines.map((l, i) => '<p class="jump" data-li="' + i + '">' + esc(l.text) + "</p>").join(""); }
      else if (d.kind === "plain") { S.lyrics.kind = "plain"; box.className = "lyrics plain"; box.innerHTML = d.text.split(/\r?\n/).map((l) => "<p>" + (esc(l) || "&nbsp;") + "</p>").join(""); }
      else box.innerHTML = '<p class="muted">Este arquivo não tem letra.</p>';
    } catch (e) { if (S.lyrics.key === key) box.innerHTML = '<p class="muted">Não foi possível carregar a letra.</p>'; }
  }
  function updateLyricsActive() {
    if (S.lyrics.kind !== "synced" || $("#now-playing").hidden) return;
    const ls = S.lyrics.lines; let a = -1; for (let i = 0; i < ls.length; i++) { if (ls[i].t <= audio.currentTime + 0.15) a = i; else break; }
    if (a === S.lyrics.active) return; S.lyrics.active = a;
    $$("#np-lyrics p").forEach((p, i) => p.classList.toggle("on", i === a)); scrollLyrics();
  }
  function scrollLyrics() {
    if (S.npTab !== "lyrics") return; const el = $("#np-lyrics p.on"); if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  /* ------------------------------------------------------------ ferramentas */
  const TOOL_CONFIG = {
    doctor: { desc: "Verifica ambiente, configuração, bancos locais e dependências sem revelar credenciais.", fields: [] },
    stats: {
      desc: "Mostra estatísticas gerais da biblioteca baixada.",
      fields: [{ key: "artists", type: "checkbox", label: "Detalhar por artista" }],
    },
    library: {
      desc: "Consulta ou ajusta o catálogo local de downloads.",
      fields: [
        { key: "subaction", type: "select", label: "AÇÃO", default: "status", options: [
          ["status", "Status geral"], ["missing", "Itens ausentes"], ["list", "Listar itens"],
          ["history", "Histórico"], ["reconcile", "Reconciliar com os arquivos"],
          ["reset-stuck", "Reiniciar itens travados"], ["unmark", "Desmarcar um álbum"],
        ] },
        { key: "limit", type: "number", label: "LIMITE", min: 1, max: 200, default: 20,
          showWhen: (v) => ["missing", "list", "history"].includes(v.subaction) },
        { key: "target", type: "text", label: "ID DO ÁLBUM", placeholder: "ID exibido no catálogo",
          showWhen: (v) => v.subaction === "unmark" },
        { key: "dry_run", type: "checkbox", label: "Simular (não gravar alterações)", default: true,
          showWhen: (v) => v.subaction === "reconcile" },
        { key: "fix", type: "checkbox", label: "Corrigir divergências encontradas",
          showWhen: (v) => v.subaction === "reconcile" },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo a alteração do catálogo local",
          showWhen: (v) => v.subaction === "unmark" || v.subaction === "reset-stuck" || (v.subaction === "reconcile" && !v.dry_run) },
      ],
    },
    scan: {
      desc: "Examina uma pasta de música e atualiza o catálogo local com o que encontrar.",
      fields: [
        { key: "target", type: "text", label: "PASTA (opcional)", placeholder: "Padrão: pasta da biblioteca" },
        { key: "max_depth", type: "number", label: "PROFUNDIDADE MÁXIMA", min: 1, max: 20, default: 4 },
        { key: "dry_run", type: "checkbox", label: "Simular (não gravar alterações)", default: true },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo atualizar o catálogo com o resultado",
          showWhen: (v) => !v.dry_run },
      ],
    },
    "sync-favorites": {
      desc: "Sincroniza (e opcionalmente baixa) os álbuns favoritados na sua conta Qobuz.",
      fields: [
        { key: "limit", type: "number", label: "LIMITE", min: 1, max: 500, default: 50 },
        { key: "download_new", type: "checkbox", label: "Baixar favoritos novos" },
        { key: "download_missing", type: "checkbox", label: "Baixar favoritos ausentes na biblioteca" },
        { key: "every", type: "number", label: "REPETIR A CADA (min, 0 = uma vez)", min: 0, max: 10080, default: 0 },
        { key: "dry_run", type: "checkbox", label: "Simular (não baixar nada)", default: true },
        { key: "confirm_downloads", type: "checkbox", label: "Confirmo iniciar downloads",
          showWhen: (v) => !v.dry_run && (v.download_new || v.download_missing) },
      ],
    },
    "sync-db": {
      desc: "Reconstrói o banco local de downloads a partir da pasta da biblioteca.",
      fields: [
        { key: "target", type: "text", label: "PASTA (opcional)", placeholder: "Padrão: pasta da biblioteca" },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo alterar o banco local" },
      ],
    },
    "find-duplicates": {
      desc: "Procura faixas duplicadas dentro de uma pasta.",
      fields: [{ key: "target", type: "text", label: "PASTA", placeholder: "Caminho a examinar", required: true }],
    },
    lyrics: {
      desc: "Preenche letras (e .lrc, se ativado) nos arquivos já baixados.",
      fields: [
        { key: "target", type: "text", label: "PASTA (opcional)", placeholder: "Padrão: pasta da biblioteca" },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo editar os arquivos de áudio" },
      ],
    },
    inspect: {
      desc: "Mostra tags e informações técnicas de um arquivo de áudio.",
      fields: [{ key: "target", type: "text", label: "ARQUIVO", placeholder: "Caminho do arquivo de áudio", required: true }],
    },
    "import-playlist": {
      desc: "Importa uma playlist externa, buscando e baixando as faixas correspondentes no Qobuz.",
      fields: [
        { key: "target", type: "text", label: "URL OU CAMINHO DA PLAYLIST", required: true },
        { key: "confirm_downloads", type: "checkbox", label: "Confirmo buscar e baixar as faixas encontradas" },
      ],
    },
    "sync-playlist": {
      desc: "Mantém uma pasta local sincronizada com uma playlist do Qobuz.",
      fields: [
        { key: "target", type: "text", label: "URL DA PLAYLIST QOBUZ", required: true },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo alterar arquivos locais" },
        { key: "confirm_downloads", type: "checkbox", label: "Confirmo iniciar downloads" },
      ],
    },
    dl: {
      desc: "Baixa uma faixa, álbum ou playlist a partir de uma URL do Qobuz.",
      fields: [
        { key: "target", type: "text", label: "URL DO QOBUZ (ou arquivo com URLs)", required: true },
        { key: "dry_run", type: "checkbox", label: "Simular (não baixar nada)" },
        { key: "confirm_downloads", type: "checkbox", label: "Confirmo iniciar o download", showWhen: (v) => !v.dry_run },
      ],
    },
    lucky: {
      desc: "Busca um termo no Qobuz e baixa o(s) primeiro(s) resultado(s) automaticamente.",
      fields: [
        { key: "target", type: "text", label: "TERMO DE BUSCA", required: true },
        { key: "limit", type: "number", label: "QUANTIDADE", min: 1, max: 20, default: 1 },
        { key: "dry_run", type: "checkbox", label: "Simular (não baixar nada)" },
        { key: "confirm_downloads", type: "checkbox", label: "Confirmo iniciar o download", showWhen: (v) => !v.dry_run },
      ],
    },
    watch: {
      desc: "Monitora uma pasta e processa novas faixas de áudio automaticamente.",
      fields: [
        { key: "target", type: "text", label: "PASTA A MONITORAR", required: true },
        { key: "confirm_file_changes", type: "checkbox", label: "Confirmo que arquivos de áudio poderão ser editados automaticamente" },
      ],
    },
    user: { desc: "Mostra dados do perfil e da assinatura Qobuz conectada.", fields: [] },
    "show-config": { desc: "Mostra a configuração atual do Qobuz-DL (com segredos ocultos).", fields: [] },
    purge: {
      desc: "Apaga por completo o banco local de downloads. Isso não apaga arquivos de áudio, só o histórico de registros.",
      fields: [{ key: "confirm_delete", type: "checkbox", label: "Confirmo apagar o banco de downloads" }],
    },
  };

  function toolValues() { const v = {}; $$("#tool-dynamic-fields [data-field]").forEach((el) => { v[el.dataset.field] = el.type === "checkbox" ? el.checked : el.value; }); return v; }
  function renderToolFields() {
    const action = $("#tool-select").value; const cfg = TOOL_CONFIG[action] || { desc: "", fields: [] }; $("#tool-description").textContent = cfg.desc;
    const box = $("#tool-dynamic-fields");
    const draw = () => {
      const v = toolValues();
      box.innerHTML = cfg.fields.map((f) => {
        if (f.showWhen && !f.showWhen(v)) return ""; const id = "tool-field-" + f.key; const val = Object.prototype.hasOwnProperty.call(v, f.key) ? v[f.key] : f.default;
        if (f.type === "checkbox") return '<label class="switch-row"><span><b>' + esc(f.label) + '</b></span><input type="checkbox" id="' + id + '" data-field="' + f.key + '"' + (val ? " checked" : "") + "><i></i></label>";
        if (f.type === "select") return '<label class="label" for="' + id + '">' + esc(f.label) + '</label><select id="' + id + '" data-field="' + f.key + '">' + f.options.map(([o, l]) => '<option value="' + esc(o) + '"' + (o === val ? " selected" : "") + ">" + esc(l) + "</option>").join("") + "</select>";
        if (f.type === "number") return '<label class="label" for="' + id + '">' + esc(f.label) + '</label><input type="number" id="' + id + '" data-field="' + f.key + '" min="' + f.min + '" max="' + f.max + '" value="' + (val != null ? val : f.default) + '">';
        return '<label class="label" for="' + id + '">' + esc(f.label) + (f.required ? " *" : "") + '</label><input type="text" id="' + id + '" data-field="' + f.key + '" placeholder="' + esc(f.placeholder || "") + '" value="' + esc(val || "") + '">';
      }).join("");
      $$("#tool-dynamic-fields [data-field]").forEach((el) => { el.addEventListener("change", draw); if (el.type === "text" || el.type === "number") return; });
    };
    draw();
  }
  function toolPayload() {
    const v = toolValues();
    return { action: $("#tool-select").value, target: v.target || "", subaction: v.subaction || "", dry_run: v.dry_run !== undefined ? !!v.dry_run : true, limit: v.limit ? Number(v.limit) : 20, max_depth: v.max_depth ? Number(v.max_depth) : 4, every: v.every ? Number(v.every) : 0,
      download_new: !!v.download_new, download_missing: !!v.download_missing, confirm_downloads: !!v.confirm_downloads, confirm_file_changes: !!v.confirm_file_changes, confirm_delete: !!v.confirm_delete, fix: !!v.fix, artists: !!v.artists };
  }
  function renderTerminal(job) {
    const term = $("#tool-terminal"), chip = $("#tool-console-status");
    if (!job) { term.innerHTML = '<span class="prompt">›</span> Selecione uma ferramenta e execute para ver a saída aqui.'; return; }
    term.textContent = job.output || "(sem saída até agora)"; term.scrollTop = term.scrollHeight;
    chip.textContent = (job.status || "").toUpperCase(); chip.className = "chip " + (job.status === "running" || job.status === "parando" ? "on" : job.status === "concluído" ? "ok" : "bad");
    $("#stop-tool").hidden = job.status !== "running";
  }
  let jobTimer = null;
  function pollJob() {
    clearInterval(jobTimer);
    jobTimer = setInterval(async () => {
      if (!S.activeJob) { clearInterval(jobTimer); return; }
      try { const d = await api("/api/tools/jobs"); S.toolJobs = d.items || []; const j = S.toolJobs.find((x) => x.id === S.activeJob); if (j) { renderTerminal(j); renderJobs(); if (j.status !== "running" && j.status !== "parando") clearInterval(jobTimer); } }
      catch (e) { clearInterval(jobTimer); }
    }, 1200);
  }
  function renderJobs() {
    const el = $("#tool-job-list"); const jobs = S.toolJobs.slice().reverse();
    el.innerHTML = jobs.length ? jobs.map((j) => '<div class="job"><div><b>' + esc(j.label || j.action) + "</b><small>" + fmtRelative(j.startedAt) + " · " + esc(j.status) + '</small></div><button class="btn sm" data-job="' + esc(j.id) + '">Ver saída</button></div>').join("") : emptyBox("terminal", "Nenhuma atividade", "Execute uma ferramenta para ver o histórico desta sessão.");
  }
  async function loadToolJobs() { try { const d = await api("/api/tools/jobs"); S.toolJobs = d.items || []; renderJobs(); } catch (e) { /* silencioso */ } }
  async function runTool() {
    const w = $("#tool-warning"); w.hidden = true; const b = $("#run-tool"); b.disabled = true;
    try { const job = await api("/api/tools/run", { method: "POST", body: toolPayload() }); S.activeJob = job.id; renderTerminal(Object.assign({}, job, { output: "" })); pollJob(); loadToolJobs(); }
    catch (e) { w.hidden = false; w.textContent = e.message; } finally { b.disabled = false; }
  }
  function wireTools() {
    $("#tool-select").addEventListener("change", renderToolFields); $("#run-tool").addEventListener("click", runTool);
    $("#stop-tool").addEventListener("click", async () => { if (S.activeJob) { try { await api("/api/tools/jobs/" + encodeURIComponent(S.activeJob) + "/stop", { method: "POST" }); } catch (e) { toast(e.message, "error"); } } });
    $("#refresh-tool-jobs").addEventListener("click", loadToolJobs);
    $("#tool-job-list").addEventListener("click", (e) => { const b = e.target.closest("[data-job]"); if (!b) return; const j = S.toolJobs.find((x) => x.id === b.dataset.job); if (j) { S.activeJob = j.id; renderTerminal(j); if (j.status === "running") pollJob(); } });
    renderToolFields();
  }

  /* ------------------------------------------------------------ eventos globais */
  function wireGlobal() {
    document.addEventListener("click", async (e) => {
      const t = e.target;
      let el;
      if ((el = t.closest("[data-play]"))) { const [lid, idx] = el.dataset.play.split(":"); const list = LISTS.get(lid); if (list && list[Number(idx)]) playList(list, Number(idx)); return; }
      if ((el = t.closest("[data-dl]"))) { enqueue({ id: el.dataset.dl, kind: "track", title: el.dataset.title, artist: el.dataset.artist }); return; }
      if ((el = t.closest("[data-dl-album]"))) { enqueue({ id: el.dataset.dlAlbum, kind: "album", title: el.dataset.title, artist: el.dataset.artist }); return; }
      if ((el = t.closest("[data-fav]"))) { try { await api("/api/favorites", { method: "POST", body: { id: el.dataset.fav, kind: "album" } }); toast("Adicionado aos favoritos.", "success"); S.favAlbums = null; } catch (err) { toast(err.message, "error"); } return; }
      if ((el = t.closest("[data-remove-queue]"))) { try { await api("/api/queue/" + encodeURIComponent(el.dataset.removeQueue), { method: "DELETE" }); loadQueue(); } catch (err) { toast(err.message, "error"); } return; }
      if ((el = t.closest("[data-src]"))) { S.lib.source = el.dataset.src; store.set("qs-src", S.lib.source); render(); return; }
      if ((el = t.closest("[data-sort]"))) { S.lib.sort = el.dataset.sort; store.set("qs-sort", S.lib.sort); render(); return; }
      if ((el = t.closest("[data-stab]"))) { S.search.tab = el.dataset.stab; render(); return; }
      if (t.closest("[data-resume]")) { const l = store.get("qs-last", null); if (l) playList([l], 0); return; }
      if (t.closest("[data-select-toggle]")) { toggleSelect(); return; }
      if (t.closest("[data-dl-selected]")) { downloadSelected(); return; }
      if ((el = t.closest("[data-np-tab]"))) { setNpTab(el.dataset.npTab); return; }
      if ((el = t.closest("#np-queue li"))) { S.pb.index = Number(el.dataset.qi); loadCurrent(); return; }
      if ((el = t.closest("#np-lyrics p[data-li]"))) { const l = S.lyrics.lines[Number(el.dataset.li)]; if (l) audio.currentTime = l.t; return; }
    });
    document.addEventListener("change", (e) => { if (e.target.matches("[data-sel]")) updateSelectCount(); });
    ["#open-settings", "#open-settings-phone", "#open-settings-nav"].forEach((s) => $(s).addEventListener("click", openModal));
    $$("[data-close-modal]").forEach((b) => b.addEventListener("click", closeModal));
    $("#modal-backdrop").addEventListener("click", (e) => { if (e.target.id === "modal-backdrop") closeModal(); });
    $("#np-close").addEventListener("click", closeNP);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { if (!$("#modal-backdrop").hidden) closeModal(); else if (!$("#now-playing").hidden) closeNP(); }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#top-search").focus(); }
      if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT|BUTTON|A/.test((document.activeElement || {}).tagName || "") && S.pb.list.length) { e.preventDefault(); togglePlay(); }
    });
    $("#settings-connect").addEventListener("click", connectAccount); $("#save-settings").addEventListener("click", saveSettings); $("#save-account").addEventListener("click", saveAccount);
    const go = debounce((v) => { const q = v.trim(); location.hash = q ? "#/search?q=" + encodeURIComponent(q) : "#/search"; }, 380);
    $("#top-search").addEventListener("input", (e) => go(e.target.value));
    $("#top-search").addEventListener("keydown", (e) => { if (e.key === "Enter") { const q = e.target.value.trim(); location.hash = q ? "#/search?q=" + encodeURIComponent(q) : "#/search"; } });
    window.addEventListener("hashchange", render);
  }
  function toggleSelect() {
    const list = $("#album-tracks"); if (!list) return; const on = !list.classList.contains("selecting-on");
    list.classList.toggle("selecting-on", on);
    $$("#album-tracks .trow").forEach((row) => { row.classList.toggle("selecting", on); const ex = $(".t-check", row); if (on && !ex) { const c = document.createElement("input"); c.type = "checkbox"; c.className = "t-check"; c.dataset.sel = row.dataset.tk.split(":")[1]; c.setAttribute("aria-label", "Selecionar"); row.insertBefore(c, row.firstChild); } else if (!on && ex) ex.remove(); });
    $("#select-bar").hidden = !on; $("[data-select-toggle]").textContent = on ? "Cancelar seleção" : "Selecionar"; updateSelectCount();
  }
  function updateSelectCount() { const n = $$("#album-tracks [data-sel]:checked").length; const c = $("#select-count"); if (c) c.textContent = n + (n === 1 ? " selecionada" : " selecionadas"); }
  function downloadSelected() {
    const rows = $$("#album-tracks .trow").filter((r) => $("[data-sel]", r) && $("[data-sel]", r).checked);
    if (!rows.length) { toast("Marque ao menos uma faixa.", "info"); return; }
    rows.forEach((r) => { const b = $("[data-dl]", r); if (b) enqueue({ id: b.dataset.dl, kind: "track", title: b.dataset.title, artist: b.dataset.artist }); });
  }

  /* ------------------------------------------------------------ PWA */
  function initPwa() {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return; // só HTTPS ou localhost
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      const offer = (w) => toast("Nova versão do app disponível.", "info", { label: "Atualizar", run: () => w.postMessage("SKIP_WAITING") });
      if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
      reg.addEventListener("updatefound", () => { const w = reg.installing; if (w) w.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) offer(w); }); });
    }).catch(() => {});
    let reloaded = false; navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloaded) { reloaded = true; location.reload(); } });
  }

  /* ------------------------------------------------------------ início */
  async function init() {
    initTheme(); wireGlobal(); wirePlayer(); wireTools(); initMediaSession(); setPlayIcon();
    if (!location.hash) history.replaceState(null, "", "#/home");
    await Promise.all([loadStatus(), loadSettings(), loadQueue()]);
    render(); setInterval(() => { if (!document.hidden) loadQueue(); }, 3500); initPwa();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
