/* ============================================================================
   app.js — Qobuz Studio
   Front-end vanilla (sem build step) ligado diretamente às rotas de
   qobuz_dl/webapp.py. Nenhum dado é inventado: tudo que aparece na tela
   vem de uma resposta real da API (ou do modo de demonstração do próprio
   backend, quando /api/status informa demo=true).
   ============================================================================ */
(function () {
  "use strict";

  /* ---------------------------------------------------------------------
     Utilidades
     --------------------------------------------------------------------- */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $all = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  function fmtTime(totalSeconds) {
    const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function fmtRelative(unixSeconds) {
    if (!unixSeconds) return "—";
    const diffMs = Date.now() - Number(unixSeconds) * 1000;
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 1) return "agora";
    if (diffMin < 60) return `há ${diffMin} min`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `há ${diffH} h`;
    const diffD = Math.round(diffH / 24);
    return `há ${diffD} d`;
  }

  function debounce(fn, wait) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  async function api(path, options) {
    const opts = options || {};
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = null;
      }
    }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || `Erro ${res.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  /* ---------------------------------------------------------------------
     Toasts
     --------------------------------------------------------------------- */
  function toast(message, type) {
    const region = $("#toast-region");
    if (!region) return;
    const node = document.createElement("div");
    node.className = `toast toast-${type || "info"}`;
    node.innerHTML = `<span>${escapeHtml(message)}</span>`;
    region.appendChild(node);
    const remove = () => {
      node.classList.add("is-leaving");
      setTimeout(() => node.remove(), 200);
    };
    setTimeout(remove, 4500);
    node.addEventListener("click", remove);
  }

  /* ---------------------------------------------------------------------
     Estado
     --------------------------------------------------------------------- */
  const state = {
    view: "library",
    libraryTab: "favorites",
    searchTab: "all",
    searchQuery: "",
    status: null,
    settings: null,
    favorites: [],
    favoritesLoaded: false,
    localFiles: [],
    localLoaded: false,
    searchData: { tracks: [], albums: [] },
    searchLoaded: false,
    queueItems: [],
    queueBusy: false,
    playback: {
      list: [],
      order: null,
      index: -1,
      shuffle: false,
      repeat: "off",
    },
    toolJobs: [],
    activeToolJobId: null,
  };

  /* ---------------------------------------------------------------------
     Tema
     --------------------------------------------------------------------- */
  function resolvedTheme() {
    const explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "dark" || explicit === "light") return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }

  function syncThemeMeta() {
    const meta = $("#theme-color-meta");
    if (meta) meta.setAttribute("content", resolvedTheme() === "light" ? "#f2f1ec" : "#0a0a0a");
    const toggle = $("#theme-toggle");
    if (toggle) toggle.textContent = resolvedTheme() === "light" ? "◑" : "◐";
  }

  function initTheme() {
    syncThemeMeta();
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
        if (!document.documentElement.getAttribute("data-theme")) syncThemeMeta();
      });
    }
    const toggle = $("#theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", () => {
        const next = resolvedTheme() === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("qs-theme", next); } catch (err) { /* sem storage disponível */ }
        syncThemeMeta();
      });
    }
  }

  /* ---------------------------------------------------------------------
     Navegação entre views
     --------------------------------------------------------------------- */
  const VIEW_IDS = ["library", "explore", "downloads", "tools"];

  function setView(view, opts) {
    if (!VIEW_IDS.includes(view)) view = "library";
    state.view = view;
    VIEW_IDS.forEach((id) => {
      const section = $(`#${id}-view`);
      if (section) section.hidden = id !== view;
    });
    $all(".nav-link[data-view]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === view);
    });
    $all(".tab-bar button[data-view]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === view);
    });
    closeSidebar();
    if (view === "library") {
      const tab = (opts && opts.libraryTab) || state.libraryTab;
      setLibraryTab(tab);
    } else if (view === "explore") {
      $("#global-search").focus();
    } else if (view === "downloads") {
      loadQueue();
    } else if (view === "tools") {
      loadToolJobs();
    }
    $("#main-content").scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  }

  function wireNav() {
    $all("[data-view]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const opts = btn.dataset.libraryTab ? { libraryTab: btn.dataset.libraryTab } : undefined;
        setView(btn.dataset.view, opts);
      });
    });
  }

  /* ---------------------------------------------------------------------
     Sidebar mobile
     --------------------------------------------------------------------- */
  function openSidebar() {
    $("#sidebar").classList.add("is-open");
    $("#sidebar-scrim").classList.add("is-visible");
  }
  function closeSidebar() {
    $("#sidebar").classList.remove("is-open");
    $("#sidebar-scrim").classList.remove("is-visible");
  }
  function wireSidebar() {
    $("#mobile-menu").addEventListener("click", openSidebar);
    $("#sidebar-scrim").addEventListener("click", closeSidebar);
  }

  /* ---------------------------------------------------------------------
     Status / conexão
     --------------------------------------------------------------------- */
  async function loadStatus() {
    try {
      state.status = await api("/api/status");
      renderStatus();
    } catch (err) {
      toast("Não foi possível consultar o status da conexão.", "error");
    }
  }

  function setBusy(isBusy) {
    const dot = $("#brand-logo .brand-live-dot");
    if (dot) dot.classList.toggle("is-busy", !!isBusy);
  }

  function renderStatus() {
    const status = state.status;
    if (!status) return;

    $("#profile-name").textContent = status.connected ? "Conta Qobuz" : "Sua sala";
    $("#profile-subtitle").textContent = status.connected
      ? (status.directory || "Conectado")
      : (status.demo ? "Prévia demonstrativa" : "Biblioteca pessoal");
    $("#profile-avatar").textContent = status.connected ? "♫" : "Q";
    const pill = $("#connection-pill");
    const label = $("#connection-pill-label");
    const connectBtn = $("#connect-button");
    const demoBanner = $("#demo-banner");
    const accountStatus = $("#settings-account-status");
    const accountNote = $("#settings-account-status-note");

    pill.classList.toggle("is-connected", !!status.connected);
    pill.classList.toggle("is-demo", !!status.demo);
    demoBanner.hidden = !status.demo;

    if (status.demo) {
      label.textContent = "Prévia demonstrativa";
      connectBtn.hidden = true;
    } else if (status.connected) {
      label.textContent = "Conectado";
      connectBtn.hidden = true;
    } else {
      label.textContent = status.configured ? "Pronto para conectar" : "Modo local";
      connectBtn.hidden = false;
    }

    if (accountStatus) {
      accountStatus.classList.toggle("is-connected", !!status.connected);
      if (accountNote) {
        accountNote.textContent = status.demo
          ? "Prévia demonstrativa: nenhuma conta é acessada."
          : status.connected
            ? "Conta conectada e pronta para uso."
            : status.configured
              ? "Configurada, mas ainda não conectada nesta sessão."
              : "Ainda não configurada. Use o formulário abaixo.";
      }
    }

    const footnote = $("#config-footnote");
    if (footnote) footnote.textContent = status.configDir ? `Configuração local em: ${status.configDir}` : "";
  }

  async function connectAccount() {
    const btn = $("#connect-button");
    btn.disabled = true;
    try {
      const result = await api("/api/connect", { method: "POST" });
      if (result.connected) {
        toast("Conta Qobuz conectada.", "success");
      } else if (result.message) {
        toast(result.message, "info");
      }
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------------------------------------------------------------------
     Preferências (modal de configurações)
     --------------------------------------------------------------------- */
  const SETTINGS_BOOL_FIELDS = {
    "setting-embed-art": "embed_art",
    "setting-lyrics": "fetch_lyrics",
    "setting-lrc": "lrc_files",
    "setting-credits": "credits",
    "setting-m3u": "m3u",
    "setting-fallback": "quality_fallback",
    "setting-playlist-albums": "playlist_as_albums",
    "setting-verify": "verify_after_download",
    "setting-no-cover": "no_cover",
    "setting-smart-discography": "smart_discography",
    "setting-multi-tags": "multi_value_tags",
  };

  async function loadSettings() {
    try {
      state.settings = await api("/api/settings");
      populateSettingsForm();
    } catch (err) {
      toast("Não foi possível carregar as preferências.", "error");
    }
  }

  function populateSettingsForm() {
    const s = state.settings;
    if (!s) return;
    $("#settings-directory").value = s.directory || "";
    $("#settings-quality").value = String(s.quality || 6);
    Object.entries(SETTINGS_BOOL_FIELDS).forEach(([elId, key]) => {
      const input = $(`#${elId}`);
      if (input) input.checked = !!s[key];
    });
    $("#settings-max-workers").value = s.max_workers || 1;
    $("#settings-segment-workers").value = s.segment_workers || 4;
    $("#settings-embedded-size").value = s.embedded_art_size || "org";
    $("#settings-saved-size").value = s.saved_art_size || "org";
    $("#settings-folder-format").value = s.folder_format || "";
    $("#settings-track-format").value = s.track_format || "";
  }

  function collectSettingsPayload() {
    const payload = {
      directory: $("#settings-directory").value.trim(),
      quality: Number($("#settings-quality").value),
      max_workers: Number($("#settings-max-workers").value) || 1,
      segment_workers: Number($("#settings-segment-workers").value) || 4,
      embedded_art_size: $("#settings-embedded-size").value,
      saved_art_size: $("#settings-saved-size").value,
      folder_format: $("#settings-folder-format").value.trim() || undefined,
      track_format: $("#settings-track-format").value.trim() || undefined,
    };
    Object.entries(SETTINGS_BOOL_FIELDS).forEach(([elId, key]) => {
      const input = $(`#${elId}`);
      if (input) payload[key] = input.checked;
    });
    if (!payload.folder_format) delete payload.folder_format;
    if (!payload.track_format) delete payload.track_format;
    return payload;
  }

  async function saveSettings() {
    const btn = $("#save-settings");
    const payload = collectSettingsPayload();
    if (!payload.directory) {
      toast("Informe a pasta da biblioteca antes de salvar.", "error");
      return;
    }
    btn.disabled = true;
    try {
      state.settings = await api("/api/settings", { method: "POST", body: payload });
      populateSettingsForm();
      toast("Preferências salvas.", "success");
      closeModal($("#modal-backdrop"));
      await loadStatus();
      if (state.view === "library" && state.libraryTab === "local") {
        state.localLoaded = false;
        loadLocalFiles();
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  async function saveAccount() {
    const email = $("#account-email").value.trim();
    const token = $("#account-token").value.trim();
    if (!email || !token) {
      toast("Informe e-mail e token para conectar a conta.", "error");
      return;
    }
    const btn = $("#save-account");
    btn.disabled = true;
    try {
      await api("/api/account/configure", {
        method: "POST",
        body: { email, token, store_in_keyring: $("#account-keyring").checked },
      });
      $("#account-token").value = "";
      toast("Conta validada e salva.", "success");
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------------------------------------------------------------------
     Modais genéricos
     --------------------------------------------------------------------- */
  function openModal(backdrop) {
    backdrop.hidden = false;
  }
  function closeModal(backdrop) {
    backdrop.hidden = true;
  }
  function wireModals() {
    $all("[data-close-modal]").forEach((btn) => btn.addEventListener("click", () => closeModal($("#modal-backdrop"))));
    $all("[data-close-album]").forEach((btn) => btn.addEventListener("click", () => closeModal($("#album-backdrop"))));
    [$("#modal-backdrop"), $("#album-backdrop")].forEach((backdrop) => {
      backdrop.addEventListener("click", (ev) => {
        if (ev.target === backdrop) closeModal(backdrop);
      });
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      if (!$("#album-backdrop").hidden) closeModal($("#album-backdrop"));
      else if (!$("#modal-backdrop").hidden) closeModal($("#modal-backdrop"));
    });
    $("#open-settings").addEventListener("click", () => openModal($("#modal-backdrop")));
    $("#open-settings-profile").addEventListener("click", () => openModal($("#modal-backdrop")));
    $("#avatar-top").addEventListener("click", () => openModal($("#modal-backdrop")));
    $("#empty-settings").addEventListener("click", () => openModal($("#modal-backdrop")));
    $("#connect-button").addEventListener("click", connectAccount);
    $("#settings-connect").addEventListener("click", connectAccount);
    $("#save-settings").addEventListener("click", saveSettings);
    $("#save-account").addEventListener("click", saveAccount);
    $("#browse-directory").addEventListener("click", () => {
      const input = $("#settings-directory");
      input.focus();
      input.select();
    });
  }

  /* ---------------------------------------------------------------------
     Cartão de álbum (reutilizado em Biblioteca e Explorar)
     --------------------------------------------------------------------- */
  function albumCoverHtml(album) {
    if (album.cover) {
      return `<img src="${escapeAttr(album.cover)}" alt="" loading="lazy">`;
    }
    return `<span style="display:grid;place-items:center;height:100%;color:var(--text-faint);font-size:22px;">♫</span>`;
  }

  function renderAlbumCard(album, index) {
    const payload = escapeAttr(JSON.stringify(album));
    return `
      <article class="album-card" style="--i:${index}" data-album-card="${payload}" tabindex="0" role="button" aria-label="Abrir álbum ${escapeAttr(album.title)}">
        <div class="album-card-art">
          ${albumCoverHtml(album)}
          <button class="album-card-play" data-play-album="${payload}" title="Reproduzir" aria-label="Reproduzir álbum">▶</button>
        </div>
        <div class="album-card-copy">
          <p class="album-card-title">${escapeHtml(album.title)}</p>
          <p class="album-card-meta">${escapeHtml(album.artist)}${album.year ? " · " + escapeHtml(album.year) : ""}</p>
        </div>
      </article>`;
  }

  function renderAlbumGrid(container, albums) {
    if (!albums.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = albums.map(renderAlbumCard).join("");
  }

  function wireAlbumGridDelegation(container) {
    container.addEventListener("click", (ev) => {
      const playBtn = ev.target.closest("[data-play-album]");
      if (playBtn) {
        ev.stopPropagation();
        const album = JSON.parse(playBtn.dataset.playAlbum);
        openAlbumModal(album.id, { autoplay: true });
        return;
      }
      const card = ev.target.closest("[data-album-card]");
      if (card) {
        const album = JSON.parse(card.dataset.albumCard);
        openAlbumModal(album.id);
      }
    });
    container.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const card = ev.target.closest("[data-album-card]");
      if (card) {
        ev.preventDefault();
        const album = JSON.parse(card.dataset.albumCard);
        openAlbumModal(album.id);
      }
    });
  }

  /* ---------------------------------------------------------------------
     Linhas de faixa (tabelas)
     --------------------------------------------------------------------- */
  function renderSearchTrackRow(track) {
    const payload = escapeAttr(JSON.stringify({ ...track, source: "qobuz" }));
    return `
      <tr data-track-row="${payload}">
        <td class="track-index"><button class="icon-button" data-play-track="${payload}" aria-label="Reproduzir">▶</button></td>
        <td class="track-title-cell"><strong>${escapeHtml(track.title)}</strong><span>${escapeHtml(track.artist)}</span></td>
        <td>${escapeHtml(track.album || "—")}</td>
        <td><span class="quality-chip">${escapeHtml(track.quality || "—")}</span></td>
        <td class="mono">${fmtTime(track.duration)}</td>
        <td class="track-actions-cell"><button class="icon-button" data-download-track="${payload}" title="Baixar" aria-label="Baixar">↓</button></td>
      </tr>`;
  }

  function renderLocalFileRow(file) {
    const track = {
      id: file.key,
      key: file.key,
      title: file.title,
      artist: file.artist,
      album: file.album,
      duration: file.duration,
      cover: file.cover,
      source: "local",
    };
    const payload = escapeAttr(JSON.stringify(track));
    return `
      <tr data-track-row="${payload}">
        <td class="track-index"><button class="icon-button" data-play-track="${payload}" aria-label="Reproduzir">▶</button></td>
        <td class="track-title-cell"><strong>${escapeHtml(file.title)}</strong><span>${escapeHtml(file.artist)}</span></td>
        <td>${escapeHtml(file.album || "—")}</td>
        <td><span class="status-badge">${escapeHtml((file.format || "").toUpperCase())}</span></td>
        <td class="mono">${fmtTime(file.duration)}</td>
        <td></td>
      </tr>`;
  }

  function wireTrackTableDelegation(tbody, opts) {
    tbody.addEventListener("click", (ev) => {
      const playBtn = ev.target.closest("[data-play-track]");
      if (playBtn) {
        const track = JSON.parse(playBtn.dataset.playTrack);
        const list = (opts && opts.list && opts.list()) || [track];
        const index = list.findIndex((t) => t.id === track.id && t.source === track.source);
        playFromList(list, index >= 0 ? index : 0);
        return;
      }
      const dlBtn = ev.target.closest("[data-download-track]");
      if (dlBtn) {
        const track = JSON.parse(dlBtn.dataset.downloadTrack);
        enqueueDownload({ id: track.id, kind: "track", title: track.title, artist: track.artist });
      }
    });
  }

  /* ---------------------------------------------------------------------
     Biblioteca
     --------------------------------------------------------------------- */
  function setLibraryTab(tab) {
    state.libraryTab = tab;
    $all("#library-view .tab-button[data-library-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.libraryTab === tab);
    });
    const albumsGrid = $("#library-albums");
    const filesWrap = $("#library-files-wrap");
    if (tab === "favorites") {
      albumsGrid.hidden = false;
      filesWrap.hidden = true;
      if (!state.favoritesLoaded) loadFavorites();
      else renderAlbumGrid(albumsGrid, state.favorites);
      $("#library-favorites-empty").hidden = !state.favoritesLoaded || state.favorites.length > 0;
    } else {
      albumsGrid.hidden = true;
      filesWrap.hidden = false;
      if (!state.localLoaded) loadLocalFiles();
      else renderLocalFiles();
    }
  }

  async function loadFavorites() {
    try {
      const data = await api("/api/favorites?kind=albums&limit=60");
      state.favorites = data.items || [];
      state.favoritesLoaded = true;
      if (state.libraryTab === "favorites") {
        renderAlbumGrid($("#library-albums"), state.favorites);
        $("#library-favorites-empty").hidden = state.favorites.length > 0;
      }
    } catch (err) {
      toast("Não foi possível carregar os favoritos.", "error");
    }
  }

  async function loadLocalFiles() {
    try {
      const data = await api("/api/library");
      state.localFiles = data.items || [];
      state.localLoaded = true;
      $("#library-location").textContent = data.directory
        ? `Pasta: ${data.directory}`
        : "Os seus álbuns favoritos e arquivos já baixados.";
      if (state.libraryTab === "local") renderLocalFiles();
    } catch (err) {
      toast("Não foi possível ler a biblioteca local.", "error");
    }
  }

  function renderLocalFiles() {
    const tbody = $("#library-files");
    const empty = $("#library-empty");
    tbody.innerHTML = state.localFiles.map(renderLocalFileRow).join("");
    empty.hidden = state.localFiles.length > 0;
  }

  /* ---------------------------------------------------------------------
     Explorar / busca
     --------------------------------------------------------------------- */
  function setSearchTab(tab) {
    state.searchTab = tab;
    $all("#explore-view .tab-button[data-search-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.searchTab === tab);
    });
    renderSearchResults();
  }

  function renderSearchResults() {
    const albumsGrid = $("#search-albums");
    const trackWrap = $("#search-track-wrap");
    const idle = $("#search-idle");
    const empty = $("#search-empty");

    if (!state.searchLoaded) {
      idle.hidden = false;
      albumsGrid.hidden = true;
      trackWrap.hidden = true;
      empty.hidden = true;
      return;
    }
    idle.hidden = true;

    const showAlbums = state.searchTab === "all" || state.searchTab === "albums";
    const showTracks = state.searchTab === "all" || state.searchTab === "tracks";
    const albums = showAlbums ? state.searchData.albums : [];
    const tracks = showTracks ? state.searchData.tracks : [];

    albumsGrid.hidden = !showAlbums || albums.length === 0;
    renderAlbumGrid(albumsGrid, albums);

    trackWrap.hidden = !showTracks || tracks.length === 0;
    $("#search-tracks").innerHTML = tracks.map(renderSearchTrackRow).join("");

    empty.hidden = !(albums.length === 0 && tracks.length === 0);
  }

  async function runSearch(query) {
    state.searchQuery = query;
    $("#search-query-title").textContent = query
      ? `Resultados para “${query}”`
      : "Encontre sua próxima faixa favorita por artista, álbum ou gênero.";
    if (query.trim().length < 2) {
      state.searchLoaded = false;
      state.searchData = { tracks: [], albums: [] };
      renderSearchResults();
      return;
    }
    setBusy(true);
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query.trim())}&kind=all&limit=24`);
      state.searchData = { tracks: data.tracks || [], albums: data.albums || [] };
      state.searchLoaded = true;
      renderSearchResults();
    } catch (err) {
      toast("A busca falhou. Tente novamente.", "error");
    } finally {
      setBusy(false);
    }
  }

  function wireSearch() {
    const input = $("#global-search");
    const debounced = debounce((value) => {
      if (state.view !== "explore") setView("explore");
      runSearch(value);
    }, 350);
    input.addEventListener("input", () => debounced(input.value));
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        clearTimeout();
        if (state.view !== "explore") setView("explore");
        runSearch(input.value);
      }
    });
    document.addEventListener("keydown", (ev) => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        input.focus();
        input.select();
      }
    });
    $all(".tab-button[data-search-tab]").forEach((btn) => {
      btn.addEventListener("click", () => setSearchTab(btn.dataset.searchTab));
    });
    $all(".tab-button[data-library-tab]").forEach((btn) => {
      btn.addEventListener("click", () => setLibraryTab(btn.dataset.libraryTab));
    });
    $("#queue-search").addEventListener("click", () => setView("explore"));
  }

  /* ---------------------------------------------------------------------
     Modal de álbum
     --------------------------------------------------------------------- */
  function renderAlbumTrackRow(track, index, album) {
    const payload = escapeAttr(JSON.stringify({ ...track, album: track.album || album.title, cover: track.cover || album.cover, source: "qobuz" }));
    return `
      <div class="album-track-row" data-track-row="${payload}">
        <span class="track-index mono">${index + 1}</span>
        <div class="track-title-cell"><strong>${escapeHtml(track.title)}</strong><span>${escapeHtml(track.artist)}</span></div>
        <span class="mono">${fmtTime(track.duration)}</span>
        <button class="icon-button" data-play-track="${payload}" aria-label="Reproduzir">▶</button>
        <button class="icon-button" data-download-track="${payload}" title="Baixar" aria-label="Baixar">↓</button>
      </div>`;
  }

  async function openAlbumModal(albumId, opts) {
    const backdrop = $("#album-backdrop");
    openModal(backdrop);
    $("#album-modal-title").textContent = "Carregando…";
    $("#album-modal-artist").textContent = "";
    $("#album-modal-meta").innerHTML = "";
    $("#album-track-list").innerHTML = "";
    $("#album-modal-cover").src = "";
    setBusy(true);
    try {
      const data = await api(`/api/album/${encodeURIComponent(albumId)}`);
      const album = data.album;
      const tracks = data.tracks || [];
      $("#album-modal-title").textContent = album.title;
      $("#album-modal-artist").textContent = album.artist;
      $("#album-modal-cover").src = album.cover || "";
      $("#album-modal-cover").alt = `Capa de ${album.title}`;
      $("#album-modal-meta").innerHTML = [
        album.quality ? `<span class="quality-chip">✦ ${escapeHtml(album.quality)}</span>` : "",
        album.tracks_count ? `<span>${album.tracks_count} FAIXAS</span>` : "",
        album.year ? `<span>${escapeHtml(album.year)}</span>` : "",
        album.genre ? `<span>${escapeHtml(album.genre)}</span>` : "",
      ].filter(Boolean).join("");
      $("#album-track-list").innerHTML = tracks.map((t, i) => renderAlbumTrackRow(t, i, album)).join("");

      $("#album-play-all").onclick = () => {
        const list = tracks.map((t) => ({ ...t, album: t.album || album.title, cover: t.cover || album.cover, source: "qobuz" }));
        if (list.length) playFromList(list, 0);
      };
      $("#album-download-all").onclick = () => {
        enqueueDownload({ id: album.id, kind: "album", title: album.title, artist: album.artist });
      };

      if (opts && opts.autoplay && tracks.length) {
        const list = tracks.map((t) => ({ ...t, album: t.album || album.title, cover: t.cover || album.cover, source: "qobuz" }));
        playFromList(list, 0);
      }
    } catch (err) {
      $("#album-modal-title").textContent = "Não foi possível carregar este álbum.";
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function wireAlbumModalDelegation() {
    const list = $("#album-track-list");
    list.addEventListener("click", (ev) => {
      const playBtn = ev.target.closest("[data-play-track]");
      if (playBtn) {
        const track = JSON.parse(playBtn.dataset.playTrack);
        const rows = $all("[data-track-row]", list).map((row) => JSON.parse(row.dataset.trackRow));
        const index = rows.findIndex((t) => t.id === track.id);
        playFromList(rows, index >= 0 ? index : 0);
        return;
      }
      const dlBtn = ev.target.closest("[data-download-track]");
      if (dlBtn) {
        const track = JSON.parse(dlBtn.dataset.downloadTrack);
        enqueueDownload({ id: track.id, kind: "track", title: track.title, artist: track.artist });
      }
    });
  }

  /* ---------------------------------------------------------------------
     Downloads (fila do servidor)
     --------------------------------------------------------------------- */
  async function enqueueDownload(item) {
    try {
      await api("/api/download", { method: "POST", body: item });
      toast(`“${item.title}” foi adicionado à fila de downloads.`, "success");
      loadQueue();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function statusBadge(status) {
    const map = {
      aguardando: ["status-warn", "NA FILA"],
      baixando: ["status-busy", "BAIXANDO"],
      "concluído": ["status-ok", "CONCLUÍDO"],
      falhou: ["status-danger", "FALHOU"],
      interrompido: ["status-danger", "INTERROMPIDO"],
    };
    const [cls, label] = map[status] || ["", (status || "").toUpperCase()];
    return `<span class="status-badge ${cls}">${label}</span>`;
  }

  function renderQueueRow(item) {
    const canRemove = item.status === "aguardando";
    return `
      <tr>
        <td class="track-index">${item.cover ? `<img src="${escapeAttr(item.cover)}" alt="" style="width:28px;height:28px;border-radius:6px;object-fit:cover;">` : (item.kind === "album" ? "◫" : "♪")}</td>
        <td class="track-title-cell"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.artist || "")}</span></td>
        <td>${statusBadge(item.status)}</td>
        <td class="mono">${fmtRelative(item.createdAt)}</td>
        <td class="track-actions-cell">${canRemove ? `<button class="icon-button" data-remove-queue="${escapeAttr(item.id)}" aria-label="Remover">×</button>` : ""}</td>
      </tr>`;
  }

  async function loadQueue() {
    try {
      const data = await api("/api/queue");
      state.queueItems = data.items || [];
      state.queueBusy = !!data.busy;
      renderQueue();
    } catch (err) {
      /* silencioso: a fila é atualizada em polling contínuo */
    }
  }

  function renderQueue() {
    const items = state.queueItems;
    const pending = items.filter((i) => i.status === "aguardando" || i.status === "baixando").length;
    const done = items.filter((i) => i.status === "concluído").length;
    $("#pending-total").textContent = String(pending);
    $("#done-total").textContent = String(done);
    $("#download-path-summary").textContent = (state.settings && state.settings.directory) || "—";
    $("#queue-count").textContent = String(pending);
    $("#queue-count-mobile").textContent = String(pending);
    $("#queue-count").parentElement.classList.toggle("active", state.view === "downloads");

    const tbody = $("#download-queue");
    const empty = $("#queue-empty");
    const sorted = [...items].reverse();
    tbody.innerHTML = sorted.map(renderQueueRow).join("");
    empty.hidden = items.length > 0;
  }

  async function removeQueueItem(id) {
    try {
      await api(`/api/queue/${encodeURIComponent(id)}`, { method: "DELETE" });
      loadQueue();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function wireQueueDelegation() {
    $("#download-queue").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-remove-queue]");
      if (btn) removeQueueItem(btn.dataset.removeQueue);
    });
    $("#open-download-folder").addEventListener("click", () => openModal($("#modal-backdrop")));
  }

  function startQueuePolling() {
    setInterval(() => {
      if (document.hidden) return;
      loadQueue();
    }, 3500);
  }

  /* ---------------------------------------------------------------------
     Player
     --------------------------------------------------------------------- */
  const audio = $("#audio-element");

  function streamUrlFor(track) {
    if (track.source === "local") {
      return `/api/library/play/${encodeURIComponent(track.key || track.id)}`;
    }
    const quality = state.settings && Number(state.settings.quality) === 5 ? 5 : 6;
    return `/api/stream/${encodeURIComponent(track.id)}?quality=${quality}`;
  }

  function playbackOrder() {
    const pb = state.playback;
    if (!pb.shuffle) return pb.list.map((_, i) => i);
    if (!pb.order || pb.order.length !== pb.list.length) {
      const order = pb.list.map((_, i) => i);
      for (let i = order.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
      }
      pb.order = order;
    }
    return pb.order;
  }

  function playFromList(list, index) {
    state.playback.list = list;
    state.playback.order = null;
    state.playback.index = index;
    loadCurrentTrack();
  }

  function loadCurrentTrack() {
    const pb = state.playback;
    const track = pb.list[pb.index];
    if (!track) return;
    $("#now-playing").hidden = false;
    $("#player-title").textContent = track.title;
    $("#player-artist").textContent = track.artist || "";
    const cover = $("#player-cover");
    cover.innerHTML = track.cover
      ? `<img src="${escapeAttr(track.cover)}" alt="">`
      : `<span>♫</span>`;
    $("#player-quality-label").textContent = track.source === "local" ? "LOCAL" : (track.quality || "QOBUZ");
    audio.src = streamUrlFor(track);
    audio.play().catch(() => {
      /* reprodução automática pode ser bloqueada pelo navegador até o primeiro gesto do usuário */
    });
    updatePlayButton();
  }

  function updatePlayButton() {
    $("#play-button").textContent = audio.paused ? "▶" : "⏸";
    $("#player-eq").parentElement.classList.toggle("is-active", !audio.paused);
  }

  function togglePlay() {
    if (!state.playback.list.length) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }

  function stepTrack(direction) {
    const pb = state.playback;
    if (!pb.list.length) return;
    const order = playbackOrder();
    const posInOrder = order.indexOf(pb.index);
    let nextPos = posInOrder + direction;
    if (nextPos < 0) nextPos = pb.repeat === "all" ? order.length - 1 : 0;
    if (nextPos >= order.length) {
      if (pb.repeat === "all") nextPos = 0;
      else return;
    }
    pb.index = order[nextPos];
    loadCurrentTrack();
  }

  function handleTrackEnded() {
    const pb = state.playback;
    if (pb.repeat === "one") {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    stepTrack(1);
  }

  function wirePlayer() {
    $("#play-button").addEventListener("click", togglePlay);
    $("#next-button").addEventListener("click", () => stepTrack(1));
    $("#previous-button").addEventListener("click", () => {
      if (audio.currentTime > 3) {
        audio.currentTime = 0;
        return;
      }
      stepTrack(-1);
    });
    $("#shuffle-button").addEventListener("click", () => {
      state.playback.shuffle = !state.playback.shuffle;
      state.playback.order = null;
      $("#shuffle-button").classList.toggle("is-active", state.playback.shuffle);
    });
    $("#repeat-button").addEventListener("click", () => {
      const seq = ["off", "all", "one"];
      const next = seq[(seq.indexOf(state.playback.repeat) + 1) % seq.length];
      state.playback.repeat = next;
      const btn = $("#repeat-button");
      btn.classList.toggle("is-active", next !== "off");
      btn.textContent = next === "one" ? "↻¹" : "↻";
    });

    audio.addEventListener("play", updatePlayButton);
    audio.addEventListener("pause", updatePlayButton);
    audio.addEventListener("ended", handleTrackEnded);
    audio.addEventListener("loadedmetadata", () => {
      $("#total-time").textContent = fmtTime(audio.duration);
    });
    audio.addEventListener("timeupdate", () => {
      if (!audio.duration) return;
      $("#current-time").textContent = fmtTime(audio.currentTime);
      const pct = (audio.currentTime / audio.duration) * 1000;
      const slider = $("#progress-slider");
      if (!slider.matches(":active")) slider.value = String(pct);
      slider.style.setProperty("--pct", `${(pct / 10).toFixed(2)}%`);
    });
    audio.addEventListener("error", () => {
      if (state.playback.list.length) toast("Não foi possível reproduzir esta faixa.", "error");
    });

    const slider = $("#progress-slider");
    slider.addEventListener("input", () => {
      slider.style.setProperty("--pct", `${(Number(slider.value) / 10).toFixed(2)}%`);
    });
    slider.addEventListener("change", () => {
      if (audio.duration) audio.currentTime = (Number(slider.value) / 1000) * audio.duration;
    });

    const volume = $("#volume-slider");
    audio.volume = Number(volume.value) / 100;
    volume.addEventListener("input", () => {
      audio.volume = Number(volume.value) / 100;
      volume.style.setProperty("--pct", `${volume.value}%`);
      $("#volume-button").textContent = Number(volume.value) === 0 ? "◯" : "◖";
    });
    volume.style.setProperty("--pct", `${volume.value}%`);
    $("#volume-button").addEventListener("click", () => {
      audio.muted = !audio.muted;
      $("#volume-button").textContent = audio.muted ? "◯" : "◖";
    });
  }

  /* ---------------------------------------------------------------------
     Ferramentas
     --------------------------------------------------------------------- */
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

  function currentToolValues() {
    const values = {};
    $all("#tool-dynamic-fields [data-field]").forEach((el) => {
      const key = el.dataset.field;
      values[key] = el.type === "checkbox" ? el.checked : el.value;
    });
    return values;
  }

  function renderToolFields() {
    const action = $("#tool-select").value;
    const config = TOOL_CONFIG[action] || { desc: "", fields: [] };
    $("#tool-description").textContent = config.desc;
    const container = $("#tool-dynamic-fields");

    const draw = () => {
      const values = currentToolValues();
      container.innerHTML = config.fields.map((field) => {
        if (field.showWhen && !field.showWhen(values)) return "";
        const id = `tool-field-${field.key}`;
        const value = Object.prototype.hasOwnProperty.call(values, field.key) ? values[field.key] : field.default;
        if (field.type === "checkbox") {
          return `<label class="toggle-row"><span><strong>${escapeHtml(field.label)}</strong></span><input type="checkbox" id="${id}" data-field="${field.key}" ${value ? "checked" : ""}><i></i></label>`;
        }
        if (field.type === "select") {
          const opts = field.options.map(([v, l]) => `<option value="${escapeAttr(v)}" ${v === value ? "selected" : ""}>${escapeHtml(l)}</option>`).join("");
          return `<label class="form-label" for="${id}">${escapeHtml(field.label)}</label><select id="${id}" data-field="${field.key}">${opts}</select>`;
        }
        if (field.type === "number") {
          return `<label class="form-label" for="${id}">${escapeHtml(field.label)}</label><input class="settings-text-input" type="number" id="${id}" data-field="${field.key}" min="${field.min}" max="${field.max}" value="${value != null ? value : field.default}">`;
        }
        return `<label class="form-label" for="${id}">${escapeHtml(field.label)}${field.required ? " *" : ""}</label><input class="settings-text-input" type="text" id="${id}" data-field="${field.key}" placeholder="${escapeAttr(field.placeholder || "")}" value="${escapeAttr(value || "")}">`;
      }).join("");
      $all("#tool-dynamic-fields [data-field]").forEach((el) => {
        el.addEventListener("input", draw);
        el.addEventListener("change", draw);
      });
    };
    draw();
  }

  function toolPayload() {
    const action = $("#tool-select").value;
    const values = currentToolValues();
    return {
      action,
      target: values.target || "",
      subaction: values.subaction || "",
      dry_run: values.dry_run !== undefined ? !!values.dry_run : true,
      limit: values.limit ? Number(values.limit) : 20,
      max_depth: values.max_depth ? Number(values.max_depth) : 4,
      every: values.every ? Number(values.every) : 0,
      download_new: !!values.download_new,
      download_missing: !!values.download_missing,
      confirm_downloads: !!values.confirm_downloads,
      confirm_file_changes: !!values.confirm_file_changes,
      confirm_delete: !!values.confirm_delete,
      fix: !!values.fix,
      artists: !!values.artists,
    };
  }

  function renderTerminal(job) {
    const term = $("#tool-terminal");
    if (!job) {
      term.innerHTML = `<div class="terminal-empty"><span class="terminal-prompt">›</span><span>Selecione uma ferramenta e execute para ver a saída aqui.</span></div>`;
      return;
    }
    term.textContent = job.output || "(sem saída até agora)";
    term.scrollTop = term.scrollHeight;
    const statusEl = $("#tool-console-status");
    statusEl.textContent = (job.status || "").toUpperCase();
    statusEl.classList.remove("is-running", "is-done", "is-error");
    if (job.status === "running" || job.status === "parando") statusEl.classList.add("is-running");
    else if (job.status === "concluído") statusEl.classList.add("is-done");
    else if (job.status && job.status !== "concluído") statusEl.classList.add("is-error");
    $("#stop-tool").hidden = !(job.status === "running");
  }

  async function runTool() {
    const payload = toolPayload();
    const runBtn = $("#run-tool");
    const warning = $("#tool-warning");
    warning.hidden = true;
    runBtn.disabled = true;
    try {
      const job = await api("/api/tools/run", { method: "POST", body: payload });
      state.activeToolJobId = job.id;
      renderTerminal({ ...job, output: "" });
      pollActiveJob();
      loadToolJobs();
    } catch (err) {
      warning.hidden = false;
      warning.textContent = err.message;
    } finally {
      runBtn.disabled = false;
    }
  }

  async function stopActiveJob() {
    if (!state.activeToolJobId) return;
    try {
      await api(`/api/tools/jobs/${encodeURIComponent(state.activeToolJobId)}/stop`, { method: "POST" });
    } catch (err) {
      toast(err.message, "error");
    }
  }

  let jobPollTimer = null;
  function pollActiveJob() {
    if (jobPollTimer) clearInterval(jobPollTimer);
    jobPollTimer = setInterval(async () => {
      if (!state.activeToolJobId) {
        clearInterval(jobPollTimer);
        return;
      }
      try {
        const data = await api("/api/tools/jobs");
        const job = (data.items || []).find((j) => j.id === state.activeToolJobId);
        if (job) {
          renderTerminal(job);
          state.toolJobs = data.items || [];
          renderToolJobList();
          if (job.status !== "running" && job.status !== "parando") {
            clearInterval(jobPollTimer);
          }
        }
      } catch (err) {
        clearInterval(jobPollTimer);
      }
    }, 1200);
  }

  function renderToolJobList() {
    const list = $("#tool-job-list");
    const jobs = [...state.toolJobs].reverse();
    if (!jobs.length) {
      list.innerHTML = `<div class="empty-state"><span>◉</span><h3>Nenhuma atividade ainda.</h3><p>Execute uma ferramenta para ver o histórico desta sessão.</p></div>`;
      return;
    }
    list.innerHTML = jobs.map((job) => `
      <div class="tool-job-row" data-open-job="${escapeAttr(job.id)}">
        <div>
          <strong>${escapeHtml(job.label || job.action)}</strong>
          <div class="job-meta">${fmtRelative(job.startedAt)} ${statusBadge2(job.status)}</div>
        </div>
        <div class="job-actions"><button class="button-ghost" data-open-job-btn="${escapeAttr(job.id)}">Ver saída</button></div>
      </div>`).join("");
  }

  function statusBadge2(status) {
    const map = {
      running: "EXECUTANDO", "parando": "PARANDO", "concluído": "CONCLUÍDO",
      falhou: "FALHOU", interrompido: "INTERROMPIDO", "tempo esgotado": "TEMPO ESGOTADO",
    };
    return `· ${map[status] || (status || "").toUpperCase()}`;
  }

  async function loadToolJobs() {
    try {
      const data = await api("/api/tools/jobs");
      state.toolJobs = data.items || [];
      renderToolJobList();
    } catch (err) {
      /* silencioso */
    }
  }

  function wireTools() {
    $("#tool-select").addEventListener("change", renderToolFields);
    $("#run-tool").addEventListener("click", runTool);
    $("#stop-tool").addEventListener("click", stopActiveJob);
    $("#refresh-tool-jobs").addEventListener("click", loadToolJobs);
    $("#tool-job-list").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-open-job-btn]");
      const row = ev.target.closest("[data-open-job]");
      const id = (btn && btn.dataset.openJobBtn) || (row && row.dataset.openJob);
      if (!id) return;
      const job = state.toolJobs.find((j) => j.id === id);
      if (job) {
        state.activeToolJobId = job.id;
        renderTerminal(job);
        if (job.status === "running" || job.status === "parando") pollActiveJob();
      }
    });
    renderToolFields();
  }

  /* ---------------------------------------------------------------------
     Inicialização
     --------------------------------------------------------------------- */
  function wireStaticButtons() {
    $("#library-albums").addEventListener("click", () => {});
    wireAlbumGridDelegation($("#library-albums"));
    wireAlbumGridDelegation($("#search-albums"));
    wireTrackTableDelegation($("#search-tracks"), { list: () => state.searchData.tracks.map((t) => ({ ...t, source: "qobuz" })) });
    wireTrackTableDelegation($("#library-files"), { list: () => state.localFiles.map((f) => ({ id: f.key, key: f.key, title: f.title, artist: f.artist, album: f.album, duration: f.duration, cover: f.cover, source: "local" })) });
    wireAlbumModalDelegation();
    wireQueueDelegation();
  }

  async function init() {
    initTheme();
    wireNav();
    wireSidebar();
    wireModals();
    wireSearch();
    wirePlayer();
    wireTools();
    wireStaticButtons();

    setView("library");

    setBusy(true);
    await loadStatus();
    await loadSettings();
    setBusy(false);
    renderQueue();
    loadQueue();
    startQueuePolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
