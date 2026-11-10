// Polling every 3 s and the script gate's hook radios. No build step, no libraries.
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

  function pollEpisode(slug, state) {
    var cost = document.querySelector('[data-cost]');
    function tick() {
      fetch('/api/episodes/' + encodeURIComponent(slug) + '?view=status', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
          return r.json();
        })
        .then(function (d) {
          if (d.state !== state) { location.reload(); return; }
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
    if (slug) pollEpisode(slug, body.getAttribute('data-state'));
    var sig = body.getAttribute('data-history');
    if (sig !== null) pollHistory(sig);
    hookGate();
    confirmButtons();
  });
})();
