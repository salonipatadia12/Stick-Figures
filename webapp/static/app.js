// Polling every 3 s, the script gate's hook radios, the Auto toggle and notifications (Web Push). No build step, no
// libraries.
(function () {
  'use strict';
  var POLL_MS = 3000;

  function text(el, value) { if (el) el.textContent = value == null ? '' : String(value); }

  function workerLine(w) {
    if (!w) return '';
    if (!w.last_seen) return 'Mac worker: never seen';
    var ago = w.seconds_ago == null ? '' : (w.seconds_ago < 60 ? w.seconds_ago + ' s ago' : Math.round(w.seconds_ago / 60) + ' min ago');
    return 'Mac worker: ' + (w.online ? (w.busy ? 'online, busy' : 'online, idle') : 'offline') + ' (last seen ' + ago + ')';
  }

  function updateWorker(w) {
    document.querySelectorAll('[data-worker]').forEach(function (el) {
      text(el.querySelector('[data-worker-text]'), workerLine(w));
      var dot = el.querySelector('.dot');
      if (dot) dot.className = 'dot' + (w && w.online ? (w.busy ? ' busy' : ' on') : '');
    });
  }

  function updateJob(job) {
    var card = document.querySelector('[data-progress]');
    if (!card || !job) return;
    var p = job.progress || {};
    text(card.querySelector('[data-job-kind]'), job.kind + ' · ' + job.status);
    text(card.querySelector('[data-job-stage]'), p.stage || '');
    text(card.querySelector('[data-job-message]'), p.message || '');
    text(card.querySelector('[data-job-pct]'), (p.pct || 0) + '%');
    var bar = card.querySelector('[data-job-bar]');
    if (bar) bar.style.width = Math.max(0, Math.min(100, p.pct || 0)) + '%';
  }

  function pollEpisode(slug, state, autoNote) {
    var cost = document.querySelector('[data-cost]');
    function tick() {
      fetch('/api/episodes/' + encodeURIComponent(slug) + '?view=status', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
          return r.json();
        })
        .then(function (d) {
          if (d.state !== state || (d.auto_note ? '1' : '0') !== autoNote) { location.reload(); return; }
          updateJob(d.job);
          updateWorker(d.worker);
          if (cost) text(cost, '$' + Number(d.cost_usd || 0).toFixed(4));
        })
        .catch(function () {})
        .then(function () { setTimeout(tick, POLL_MS); });
    }
    setTimeout(tick, POLL_MS);
  }

  function pollHistory(signature) {
    function tick() {
      fetch('/api/episodes', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (list) {
          if (!list) return;
          var sig = list.map(function (e) { return e.slug + ':' + e.state; }).join(',');
          if (sig !== signature) location.reload();
        })
        .catch(function () {})
        .then(function () { setTimeout(tick, POLL_MS); });
    }
    setTimeout(tick, POLL_MS);
  }

  function hookGate() {
    var form = document.querySelector('[data-hook-form]');
    if (!form) return;
    var errors = {};
    try { errors = JSON.parse(form.getAttribute('data-hook-errors') || '{}'); } catch (e) { errors = {}; }
    var button = form.querySelector('[data-approve]');
    var reason = form.querySelector('[data-approve-reason]');
    function refresh() {
      var picked = form.querySelector('input[name=hook]:checked');
      var hasHooks = !!form.querySelector('input[name=hook]');
      var errs = picked ? (errors[picked.value] || []) : [];
      var blocked = (hasHooks && !picked) || errs.length > 0;
      button.disabled = blocked;
      text(reason, hasHooks && !picked ? 'Pick a hook first.' : (errs.length ? 'Approve is off until these are fixed: ' + errs.join('; ') : ''));
    }
    form.addEventListener('change', refresh);
    refresh();
  }

  // ---- Auto toggle (New page): which explanation shows; the last choice is remembered on this device
  function autoToggle() {
    var box = document.querySelector('[data-auto-toggle]');
    if (!box) return;
    var on = document.querySelector('[data-auto-on]'), off = document.querySelector('[data-auto-off]');
    try { if (!box.checked && localStorage.getItem('stick-auto') === '1') box.checked = true; } catch (e) {}
    function refresh() {
      if (on) on.hidden = !box.checked;
      if (off) off.hidden = box.checked;
      try { localStorage.setItem('stick-auto', box.checked ? '1' : '0'); } catch (e) {}
    }
    box.addEventListener('change', refresh);
    refresh();
  }

  // ---- notifications (Web Push via /sw.js; the server's public key from /api/push/key)
  function b64ToBytes(s) {
    var pad = '='.repeat((4 - s.length % 4) % 4);
    var raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body || {}) });
  }

  function pushControl() {
    var box = document.querySelector('[data-push]');
    if (!box) return;
    var stateEl = box.querySelector('[data-push-state]');
    var onBtn = box.querySelector('[data-push-on]'), offBtn = box.querySelector('[data-push-off]');
    var testBtn = box.querySelector('[data-push-test]'), ios = box.querySelector('[data-push-ios]');
    var standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    if (!supported) {
      if (isIOS && !standalone) { box.hidden = false; text(stateEl, 'off'); ios.hidden = false; }
      return;
    }
    var key = null, reg = null;
    function show(sub) {
      box.hidden = false;
      var on = !!sub && Notification.permission === 'granted';
      text(stateEl, on ? 'on for this device' : (Notification.permission === 'denied' ? 'blocked in this browser\'s settings' : 'off'));
      onBtn.hidden = on || Notification.permission === 'denied';
      offBtn.hidden = !on; testBtn.hidden = !on;
    }
    fetch('/api/push/key', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.enabled) return null;
        key = d.public_key;
        return navigator.serviceWorker.register('/sw.js').then(function (r) { reg = r; return r.pushManager.getSubscription(); })
          .then(show);
      }).catch(function () {});
    onBtn.addEventListener('click', function () {
      onBtn.disabled = true;
      Notification.requestPermission().then(function (perm) {
        if (perm !== 'granted') { show(null); return null; }
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) })
          .then(function (sub) { return post('/api/push/subscribe', { subscription: sub.toJSON() }).then(function () { show(sub); }); });
      }).catch(function () { text(stateEl, 'could not turn on'); })
        .then(function () { onBtn.disabled = false; });
    });
    offBtn.addEventListener('click', function () {
      reg.pushManager.getSubscription().then(function (sub) {
        if (!sub) return show(null);
        return post('/api/push/unsubscribe', { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); })
          .then(function () { show(null); });
      }).catch(function () {});
    });
    testBtn.addEventListener('click', function () {
      testBtn.disabled = true;
      post('/api/push/test').then(function () { text(stateEl, 'test sent'); })
        .catch(function () {}).then(function () { testBtn.disabled = false; });
    });
  }

  function confirmButtons() {
    document.querySelectorAll('form[data-confirm]').forEach(function (f) {
      f.addEventListener('submit', function (ev) {
        if (!window.confirm(f.getAttribute('data-confirm'))) ev.preventDefault();
      });
    });
    // one tap = one action: disable submit buttons after the first press
    document.querySelectorAll('form[method=post]').forEach(function (f) {
      f.addEventListener('submit', function (ev) {
        if (ev.defaultPrevented) return;
        setTimeout(function () { f.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = true; }); }, 0);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var body = document.body;
    var slug = body.getAttribute('data-slug');
    if (slug) pollEpisode(slug, body.getAttribute('data-state'), body.getAttribute('data-auto-note') || '0');
    var sig = body.getAttribute('data-history');
    if (sig !== null) pollHistory(sig);
    hookGate();
    autoToggle();
    pushControl();
    confirmButtons();
  });
})();
