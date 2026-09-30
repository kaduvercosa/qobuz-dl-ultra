/* Service worker do qobuz-dl-ultra.
   Guarda só a "casca" do app (HTML, CSS, JS, ícones) para abrir rápido e
   sobreviver a um servidor reiniciando. Nunca toca em /api/ (dados, áudio,
   downloads) -- isso sempre vai direto ao servidor.
   Atualização controlada: uma versão nova espera até a pessoa aceitar
   (mensagem SKIP_WAITING enviada pelo app). Service workers só funcionam em
   HTTPS ou localhost; em http://IP:porta o navegador ignora o registro. */
const VERSION = "v2-1";
const CACHE = `qobuz-shell-${VERSION}`;
const SHELL = ["/", "/app.css", "/app.js", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k.startsWith("qobuz-shell-")).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && (SHELL.includes(url.pathname) || url.pathname.startsWith("/assets/"))) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
  );
});
