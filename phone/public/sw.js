// Fix-It Lens service worker: caches the app shell, shows due reminders as notifications, and opens the job on a tap.
// It never caches /api or /ws, and it does not count the open: the app posts reminder_opened once, from ?reminder=.
// A PDF shared to the app (Android's share sheet, the manifest's share_target) is held here until the app takes it.
const SHELL = "fixit-shell-v1";
const SHARE = "fixit-share";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "./manifest.webmanifest", "./icon.svg", "./icon-192.png"])));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL && k !== SHARE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method === "POST" && u.origin === location.origin && u.pathname.endsWith("/share-pdf")) {
    e.respondWith(takeShare(e.request));
    return;
  }
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname === "/ws") return;
  // Network first, so a new build shows at once; the cache only covers a dropped connection.
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok && (u.pathname.startsWith("/assets/") || u.pathname === "/" )) {
          const copy = r.clone();
          caches.open(SHELL).then((c) => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("./"))),
  );
});

// The shared file goes into the cache under one fixed key (a new share replaces the last), and the app opens at
// ?share=pdf to ask which appliance it is for. Nothing is sent anywhere until the person picks one.
async function takeShare(req) {
  try {
    const f = (await req.formData()).get("pdf");
    if (f && typeof f !== "string") {
      const c = await caches.open(SHARE);
      await c.put("shared.pdf", new Response(f, { headers: { "content-type": "application/pdf", "x-name": encodeURIComponent(f.name || "manual.pdf") } }));
    }
  } catch {
    // A share that can't be read opens the app as usual.
  }
  return Response.redirect(new URL("./?share=pdf", self.registration.scope).href, 303);
}

// Push from the server: {key, title, body, footnote, page, url, appliance_id, job_id}; body carries the footnote line.
self.addEventListener("push", (e) => {
  let p = {};
  try {
    p = e.data ? e.data.json() : {};
  } catch {
    p = { title: "Fix-It Lens", body: e.data ? e.data.text() : "" };
  }
  e.waitUntil(
    self.registration.showNotification(p.title || "Fix-It Lens", {
      body: p.body || "",
      tag: p.key,
      icon: "icon-192.png",
      data: { url: p.url || "./", key: p.key, title: p.title },
    }),
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const d = e.notification.data || {};
  const url = new URL(d.url || "./", self.registration.scope);
  if (d.title && !url.searchParams.has("title")) url.searchParams.set("title", d.title);
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
      for (const c of cs) if ("navigate" in c) return c.navigate(url.href).then((w) => w && w.focus());
      return self.clients.openWindow(url.href);
    }),
  );
});
