const CACHE_NAME="naeilro-v7-4";
self.addEventListener("install",event=>{
  event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(["/","/manifest.webmanifest"])));
});
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(url.pathname.startsWith("/api/")){
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached=>cached||fetch(event.request)));
});
