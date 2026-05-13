importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyD-NLbMzqg9lLQoSIslDMHsufyOtTE_gOs",
  authDomain: "amar-veggies-a3f2a.firebaseapp.com",
  projectId: "amar-veggies-a3f2a",
  storageBucket: "amar-veggies-a3f2a.firebasestorage.app",
  messagingSenderId: "522883626327",
  appId: "1:522883626327:web:c53a7ce54701ebbadafa12",
  measurementId: "G-NZQ83J6LDF"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  self.registration.showNotification(payload.notification.title, {
    body: payload.notification.body,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png"
  });
});