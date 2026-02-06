// Service Worker for Livestream Schedule PWA
const CACHE_NAME = 'livestream-schedule-v2';
const urlsToCache = [
    '/',
    '/static/css/style.css?v=2',
    '/static/js/script.js?v=2',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css'
];

// Install event
self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(function (cache) {
                console.log('Opened cache');
                return cache.addAll(urlsToCache);
            })
    );
});

// Fetch event - network first, then cache
self.addEventListener('fetch', function (event) {
    event.respondWith(
        fetch(event.request)
            .then(function (response) {
                // Clone the response
                const responseClone = response.clone();

                caches.open(CACHE_NAME)
                    .then(function (cache) {
                        // Only cache GET requests
                        if (event.request.method === 'GET') {
                            cache.put(event.request, responseClone);
                        }
                    });

                return response;
            })
            .catch(function () {
                // If network fails, try cache
                return caches.match(event.request);
            })
    );
});

// Activate event - clean up old caches
self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (cacheNames) {
            return Promise.all(
                cacheNames.filter(function (cacheName) {
                    return cacheName !== CACHE_NAME;
                }).map(function (cacheName) {
                    return caches.delete(cacheName);
                })
            );
        })
    );
});
