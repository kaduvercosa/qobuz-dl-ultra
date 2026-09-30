/* API simulada para a prévia: mesmas rotas do webapp.py, dados fictícios. */
(function () {
  const C = __COVERS__, names = Object.keys(C);
  const cover = (i) => C[names[i % names.length]];
  const ALB = [
    ["Blue Hour", "Mira Sol", "2025", "album"], ["Quiet Geometry", "Aster Vale", "2024", "ep"], ["Night Signal", "Lumen", "2023", "single"],
    ["Live at Ridge", "The Northbound", "2022", "live"], ["Amber Tapes", "Casa Verde", "2021", "compilation"], ["Glyph", "Mira Sol", "2020", "album"],
  ].map((a, i) => ({ id: "a" + i, title: a[0], artist: a[1], year: a[2], type: a[3], genre: "Alternative", tracks_count: 8, quality: i % 2 ? "16b/44.1kHz" : "24b/96kHz", cover: cover(i), coverHi: cover(i), duration: "" }));
  const TT = ["Intro", "The Blue Between", "A Map of Quiet", "Static Bloom", "Low Tide", "Paper Moons", "Afterimage", "Signal Fade"];
  const tracksOf = (a) => TT.map((t, i) => ({ id: a.id + "t" + i, title: t, artist: a.artist, album: a.title, duration: 150 + i * 17, cover: a.cover, coverHi: a.coverHi, quality: a.quality, isrc: "" }));
  const LOCAL = ALB.slice(0, 4).flatMap((a, ai) => tracksOf(a).slice(0, 4).map((t, i) => ({ key: "loc" + ai + i, title: t.title, artist: a.artist, album: a.title, duration: t.duration, format: i % 2 ? "flac" : "mp3", size: 3e7, pathLabel: a.title, cover: a.cover })));
  const LRC = "Bem-vindo ao teste da letra sincronizada;Cada linha acende no tempo certo;A gaveta abre pelo botão Letra;E fecha sem sair do player;A fila fica no botão ao lado;Toque numa linha para pular;A capa vem em alta resolução;Qobuz e arquivos locais também;Tudo espelhando o seu config.ini;Sem recarregar a página inteira;Só o que muda é que muda;Fim da demonstração".split(";")
    .map((l, i) => "[" + String(Math.floor(i * 5 / 60)).padStart(2, "0") + ":" + String((i * 5) % 60).padStart(2, "0") + ".00] " + l).join("\n");
  let settings = { directory: "~/Music/Qobuz", quality: 6, embed_art: true, fetch_lyrics: true, lrc_files: true, credits: true, m3u: true, quality_fallback: true, playlist_as_albums: false, verify_after_download: false, no_cover: false, smart_discography: false, multi_value_tags: false, max_workers: 2, segment_workers: 4, embedded_art_size: "org", saved_art_size: "org", folder_format: "{album_artist} - {album_title}", track_format: "{track_number}. {track_title}", configDir: "~/.config/qobuz-dl" };
  const now = () => Date.now() / 1000;
  let queue = [
    { id: "q1", itemId: "a0", kind: "album", title: "Blue Hour", artist: "Mira Sol", status: "baixando", createdAt: now() - 90, message: "O downloader está processando este item", cover: cover(0) },
    { id: "q2", itemId: "a1", kind: "album", title: "Quiet Geometry", artist: "Aster Vale", status: "aguardando", createdAt: now() - 60, message: "Na fila", cover: cover(1) },
    { id: "q3", itemId: "a2t1", kind: "track", title: "Night Signal", artist: "Lumen", status: "concluído", createdAt: now() - 900, message: "Download concluído", cover: cover(2) },
  ];
  const json = (o, s) => new Response(JSON.stringify(o), { status: s || 200, headers: { "Content-Type": "application/json" } });
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opt) => {
    const u = new URL(url, location.href); if (!u.pathname.startsWith("/api/")) return realFetch(url, opt);
    const p = u.pathname, q = u.searchParams, body = opt && opt.body ? JSON.parse(opt.body) : {};
    await new Promise((r) => setTimeout(r, 120));
    if (p === "/api/status") return json({ demo: false, configured: true, connected: true, directory: settings.directory, quality: settings.quality, qualityLabel: "FLAC · CD", configDir: settings.configDir, accountLabel: "Conta Qobuz" });
    if (p === "/api/settings") { if (opt && opt.method === "POST") settings = Object.assign(settings, body); return json(settings); }
    if (p === "/api/connect") return json({ connected: true });
    if (p === "/api/search") { const s = (q.get("q") || "").toLowerCase(); const albums = ALB.filter((a) => (a.title + a.artist).toLowerCase().includes(s)); const tracks = albums.flatMap(tracksOf).slice(0, 12); return json({ albums, tracks }); }
    if (p.startsWith("/api/album/")) { const a = ALB.find((x) => x.id === decodeURIComponent(p.split("/").pop())) || ALB[0]; return json({ album: a, tracks: tracksOf(a) }); }
    if (p === "/api/favorites") return json({ items: q.get("kind") === "tracks" ? ALB.flatMap(tracksOf).slice(0, 10) : ALB });
    if (p === "/api/library") return json({ items: LOCAL, directory: settings.directory });
    if (p.startsWith("/api/lyrics/") || p.startsWith("/api/library/lyrics/")) return json({ kind: "synced", text: LRC });
    if (p === "/api/queue") return json({ items: queue, busy: queue.some((x) => x.status === "baixando") });
    if (p === "/api/download") { const it = { id: "q" + Date.now(), itemId: body.id, kind: body.kind, title: body.title, artist: body.artist, status: "baixando", createdAt: now(), message: "Processando", cover: null }; queue.push(it); setTimeout(() => { it.status = "concluído"; it.message = "Download concluído"; }, 6000); return json(it); }
    if (p.startsWith("/api/queue/")) { queue = queue.filter((x) => x.id !== decodeURIComponent(p.split("/").pop())); return json({ ok: true }); }
    if (p.startsWith("/api/tools")) return json({ items: [] });
    return json({ detail: "Rota não simulada: " + p }, 404);
  };
  /* áudio de teste: acorde sintetizado de 60 s no lugar do stream */
  let wav = null;
  function makeWav() {
    if (wav) return wav; const sr = 8000, n = sr * 60, buf = new ArrayBuffer(44 + n), v = new DataView(buf), w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF"); v.setUint32(4, 36 + n, true); w(8, "WAVEfmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true); w(36, "data"); v.setUint32(40, n, true);
    for (let i = 0; i < n; i++) { const t = i / sr, f = [220, 277.2, 329.6][Math.floor(t / 2) % 3]; v.setUint8(44 + i, 128 + 22 * Math.sin(2 * Math.PI * f * t) * (0.6 + 0.4 * Math.sin(t * 2))); }
    return (wav = URL.createObjectURL(new Blob([buf], { type: "audio/wav" })));
  }
  const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src");
  Object.defineProperty(HTMLMediaElement.prototype, "src", { get() { return d.get.call(this); }, set(v) { d.set.call(this, /\/api\/(stream|library\/play)\//.test(v) ? makeWav() : v); } });
  const bar = document.createElement("div"); bar.textContent = "PRÉVIA"; bar.style.cssText = "position:fixed;z-index:999;left:0;top:0;font:700 8px monospace;letter-spacing:.14em;padding:1px 7px;border-radius:0 0 6px 0;background:#ff3b2f;color:#fff;pointer-events:none;opacity:.9";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(bar));
})();
