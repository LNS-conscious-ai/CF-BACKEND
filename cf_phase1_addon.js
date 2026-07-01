/**
 * cf_phase1_addon.js — LNS Phase 1 · CF Computer handoff (REWIRED to the real widget)
 * ----------------------------------------------------------------------------------
 * Adds the "Build my validation report with CF Computer" experience to the EXISTING
 * lns.life chat widget. Written to match the real DOM (#cf-msgs, .cf-msg.cf, .cf-bubble)
 * and the real backend endpoint (POST /cf-computer/report). Purely additive — it does
 * NOT replace or duplicate the site's existing consent flow or chat code.
 *
 * CF COMPUTER IDENTITY (locked 2026-06-30): CF Computer is a TEAL glowing orb that
 * echoes the hero CF orb (glowing core + breathing halo rings), distinct from CF's
 * GOLD. Two entry points, both INSIDE the chat — no second floating orb on the page:
 *   (A) a teal orb in the chat header (#cf-header-btns), tap to open CF Computer;
 *   (B) the inline teal handoff button that appears in-thread once CF confirms a problem.
 *
 * Config (set BEFORE this script in index.html):
 *   window.LNS_PHASE1 = { backend: 'https://cf-backend-pr-1.onrender.com', debug: true };
 *   // The header orb ALWAYS opens an intake gate. It pre-fills only with a real problem the
 *   // user already described to CF; otherwise the box is blank. No report is ever sent — and
 *   // no tokens spent — until the user taps Build on a real problem statement.
 *
 * Backend contract (verified against cf_computer_router.py):
 *   POST {backend}/cf-computer/report
 *     body: { problem_statement (10..2000), session_id, user_id }
 *     200 : { status, report_id, steps_completed[], conscious_review, ... }
 *     429 : daily limit reached
 *   PDF is served at: {backend}/reports/{report_id}.pdf
 */
(function () {
  'use strict';

  var CFG = window.LNS_PHASE1 || {};
  var BACKEND = (CFG.backend || 'https://cf-backend-ethf.onrender.com').replace(/\/+$/, '');
  var DEBUG = !!CFG.debug;
  function log() { if (DEBUG) try { console.log.apply(console, ['[CF-Phase1]'].concat([].slice.call(arguments))); } catch (e) {} }

  // The 6 real step names (from cf_computer.py)
  var STEPS = [
    'Frame the problem',
    'Market snapshot',
    'Competitor scan',
    'Target user + first version',
    '7-day action plan',
    'Conscious review'
  ];

  // CF phrases that indicate a problem has been confirmed (broad, case-insensitive)
  var CONFIRM_PATTERNS = [
    /i think your problem is/i,
    /the problem (you'?re|you are) (solving|facing)/i,
    /your (core )?problem (is|seems|sounds)/i,
    /does that (land|resonate|sound right|capture)/i,
    /shall we (build|create|put together) (a |your )?validation report/i,
    /ready to validate/i,
    /want me to build (a |your )?(validation )?report/i,
    /sounds like the (core )?problem/i
  ];

  // ---------- identity helpers ----------
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  function getUserId() {
    var k = 'lns_user_id', v = null;
    try { v = localStorage.getItem(k); if (!v) { v = uuid(); localStorage.setItem(k, v); } } catch (e) { v = uuid(); }
    return v;
  }
  function getSessionId() {
    var k = 'lns_session_id', v = null;
    try { v = sessionStorage.getItem(k); if (!v) { v = uuid(); sessionStorage.setItem(k, v); } } catch (e) { v = uuid(); }
    return v;
  }

  // ---------- styles ----------
  function injectStyles() {
    if (document.getElementById('cf-p1-styles')) return;
    var s = document.createElement('style');
    s.id = 'cf-p1-styles';
    s.textContent = [
      // Inline handoff button (path B) — TEAL, with a small glowing core dot.
      '.cfp1-btn{display:inline-flex;align-items:center;gap:8px;margin-top:10px;padding:11px 18px;border:none;border-radius:9999px;',
      'background:#2DD4BF;color:#04342C;font:600 13px/1 Inter,system-ui,sans-serif;cursor:pointer;',
      'box-shadow:0 4px 18px rgba(45,212,191,.35);transition:transform .2s,box-shadow .2s;-webkit-tap-highlight-color:transparent;}',
      '.cfp1-btn:hover{transform:translateY(-1px);box-shadow:0 6px 24px rgba(45,212,191,.5);}',
      '.cfp1-btn:disabled{opacity:.5;cursor:default;transform:none;}',
      '.cfp1-gdot{width:12px;height:12px;border-radius:50%;flex:0 0 auto;background:radial-gradient(circle at 30% 30%,#9FF3E2,#0fae8e);box-shadow:0 0 6px rgba(4,52,44,.4);}',
      // CF Computer header orb (path A) — TEAL glowing core + breathing halo rings (echoes hero CF orb).
      '@keyframes cfcBreathe{0%,100%{transform:scale(1);}50%{transform:scale(1.05);}}',
      '@keyframes cfcHalo{0%,100%{transform:translate(-50%,-50%) scale(.9);opacity:.2;}50%{transform:translate(-50%,-50%) scale(1.08);opacity:1;}}',
      '#cfp1HeaderOrb{position:relative;width:30px;height:30px;flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;background:transparent;border:none;padding:0;cursor:pointer;-webkit-tap-highlight-color:transparent;}',
      '#cfp1HeaderOrb .cfc-core{width:16px;height:16px;border-radius:50%;background:radial-gradient(circle at 30% 30%,#9FF3E2,#2DD4BF);box-shadow:0 0 11px rgba(45,212,191,.6);animation:cfcBreathe 4s ease-in-out infinite;z-index:2;}',
      '#cfp1HeaderOrb:hover .cfc-core{box-shadow:0 0 16px rgba(45,212,191,.85);}',
      '#cfp1HeaderOrb .cfc-ring{position:absolute;top:50%;left:50%;border-radius:50%;pointer-events:none;border:1px solid rgba(45,212,191,.5);animation:cfcHalo 5s ease-in-out infinite;}',
      '#cfp1HeaderOrb .cfc-r1{width:20px;height:20px;animation-delay:0s;border-color:rgba(45,212,191,.5);}',
      '#cfp1HeaderOrb .cfc-r2{width:26px;height:26px;animation-delay:-1.6s;border-color:rgba(45,212,191,.28);}',
      '#cfp1HeaderOrb .cfc-r3{width:31px;height:31px;animation-delay:-3.2s;border-color:rgba(45,212,191,.14);}',
      '@media (prefers-reduced-motion: reduce){#cfp1HeaderOrb .cfc-core,#cfp1HeaderOrb .cfc-ring{animation:none;}}',
      // CF Computer intake gate — collects a real problem BEFORE spending tokens (no accidental reports).
      '.cfp1-orbsm{position:relative;width:46px;height:46px;margin:0 auto 12px;display:flex;align-items:center;justify-content:center;}',
      '.cfp1-orbsm .cfc-core{width:24px;height:24px;border-radius:50%;background:radial-gradient(circle at 30% 30%,#9FF3E2,#2DD4BF);box-shadow:0 0 14px rgba(45,212,191,.6);animation:cfcBreathe 4s ease-in-out infinite;z-index:2;}',
      '.cfp1-orbsm .cfc-ring{position:absolute;top:50%;left:50%;border-radius:50%;pointer-events:none;border:1px solid rgba(45,212,191,.5);animation:cfcHalo 5s ease-in-out infinite;}',
      '.cfp1-orbsm .cfc-r1{width:30px;height:30px;animation-delay:0s;}',
      '.cfp1-orbsm .cfc-r2{width:38px;height:38px;animation-delay:-1.6s;border-color:rgba(45,212,191,.28);}',
      '.cfp1-orbsm .cfc-r3{width:46px;height:46px;animation-delay:-3.2s;border-color:rgba(45,212,191,.14);}',
      '@media (prefers-reduced-motion: reduce){.cfp1-orbsm .cfc-core,.cfp1-orbsm .cfc-ring{animation:none;}}',
      '.cfp1-ta{width:100%;box-sizing:border-box;min-height:88px;resize:vertical;margin:0 0 8px;padding:12px 14px;border-radius:12px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);color:#F0EDE8;font:400 13.5px/1.5 Inter,system-ui,sans-serif;}',
      '.cfp1-ta:focus{outline:none;border-color:rgba(45,212,191,.65);box-shadow:0 0 0 3px rgba(45,212,191,.15);}',
      '.cfp1-ta::placeholder{color:#6B6878;}',
      '.cfp1-ihint{font-size:12px;line-height:1.5;color:#9C9CAA;text-align:center;margin:0 0 16px;}',
      '.cfp1-build{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px;border-radius:9999px;border:none;background:#2DD4BF;color:#04342C;font:700 14px/1 Inter,system-ui,sans-serif;cursor:pointer;transition:opacity .2s,box-shadow .2s;box-shadow:0 4px 18px rgba(45,212,191,.3);}',
      '.cfp1-build:hover{box-shadow:0 6px 24px rgba(45,212,191,.45);}',
      '.cfp1-build:disabled{opacity:.4;cursor:not-allowed;box-shadow:none;}',
      '.cfp1-meta{font-size:11px;color:#8A8A97;text-align:center;margin:10px 0 0;}',
      // Progress modal (unchanged — CF brand surface)
      '.cfp1-overlay{position:fixed;inset:0;z-index:11000;background:rgba(6,8,15,.9);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);',
      'display:flex;align-items:center;justify-content:center;padding:20px;opacity:0;pointer-events:none;transition:opacity .3s;}',
      '.cfp1-overlay.open{opacity:1;pointer-events:auto;}',
      '.cfp1-card{width:100%;max-width:460px;background:#13131F;border:1px solid rgba(212,168,83,.18);border-radius:20px;padding:28px 24px;',
      'color:#F0EDE8;font-family:Inter,system-ui,sans-serif;box-shadow:0 24px 80px rgba(0,0,0,.5);}',
      '.cfp1-title{font-size:16px;font-weight:600;text-align:center;margin:0 0 4px;}',
      '.cfp1-sub{font-size:12.5px;color:#9C9CAA;text-align:center;margin:0 0 18px;min-height:18px;}',
      '.cfp1-steps{list-style:none;margin:0 0 18px;padding:0;display:flex;flex-direction:column;gap:2px;}',
      '.cfp1-step{display:flex;align-items:center;gap:11px;padding:9px 0;font-size:13px;color:#9C9CAA;opacity:.45;transition:opacity .3s,color .3s;}',
      '.cfp1-step.active{opacity:1;color:#F0EDE8;}.cfp1-step.done{opacity:.75;color:#6DD4A8;}',
      '.cfp1-dot{width:22px;height:22px;border-radius:50%;flex:0 0 auto;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;',
      'border:1.5px solid #6B6878;color:#6B6878;}',
      '.cfp1-step.active .cfp1-dot{border-color:#D4A853;color:#D4A853;}.cfp1-step.done .cfp1-dot{border-color:#6DD4A8;color:#6DD4A8;background:rgba(109,212,168,.1);}',
      '.cfp1-bar{height:6px;border-radius:3px;background:rgba(255,255,255,.06);overflow:hidden;margin-bottom:18px;}',
      '.cfp1-fill{height:100%;width:0;border-radius:3px;background:linear-gradient(90deg,#D4A853,#E8C87A);transition:width .5s ease;}',
      '.cfp1-actions{display:flex;flex-direction:column;gap:9px;}',
      '.cfp1-dl{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px;border-radius:9999px;border:none;',
      'background:linear-gradient(135deg,#E8C87A,#D4A853);color:#08080D;font:700 14px/1 Inter,system-ui,sans-serif;cursor:pointer;text-decoration:none;}',
      '.cfp1-close{display:inline-flex;align-items:center;justify-content:center;width:100%;padding:11px;border-radius:9999px;background:transparent;',
      'border:1px solid rgba(255,255,255,.12);color:#9C9CAA;font:600 13px/1 Inter,system-ui,sans-serif;cursor:pointer;}',
      '.cfp1-err{color:#FB923C;font-size:12.5px;text-align:center;margin:0 0 12px;}',
      '.cfp1-wait{font-size:12px;line-height:1.5;color:#E8C87A;text-align:center;margin:2px 4px 14px;padding:10px 12px;border:1px solid rgba(212,168,83,.28);border-radius:12px;background:rgba(212,168,83,.07);}',
      '.cfp1-share{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:13px;border-radius:9999px;border:1px solid rgba(212,168,83,.5);background:transparent;color:#E8C87A;font-size:14px;font-weight:600;cursor:pointer;transition:background .2s;}',
      '.cfp1-share:hover{background:rgba(212,168,83,.1);}',
      '.cfp1-hint{font-size:11px;color:#8A8A97;text-align:center;margin:6px 0 0;}'
    ].join('');
    document.head.appendChild(s);
  }

  // ---------- progress modal ----------
  var els = {};
  function buildModal() {
    if (document.getElementById('cfp1Overlay')) return;
    var ov = document.createElement('div');
    ov.id = 'cfp1Overlay'; ov.className = 'cfp1-overlay';
    ov.innerHTML =
      '<div class="cfp1-card" role="dialog" aria-modal="true" aria-label="CF Computer report progress">' +
        '<h3 class="cfp1-title">CF Computer</h3>' +
        '<p class="cfp1-sub" id="cfp1Sub">CF Computer is researching and writing your report — thinking deeply so it\'s genuinely useful to you.</p>' +
        '<div class="cfp1-bar"><div class="cfp1-fill" id="cfp1Fill"></div></div>' +
        '<ul class="cfp1-steps" id="cfp1Steps">' +
          STEPS.map(function (n, i) {
            return '<li class="cfp1-step" id="cfp1Step-' + i + '"><span class="cfp1-dot">' + (i + 1) + '</span><span>' + n + '</span></li>';
          }).join('') +
        '</ul>' +
        '<p class="cfp1-wait" id="cfp1Wait"><b>This usually takes up to 5 minutes. Please keep this tab open</b> — closing it will cancel your report.</p>' +
        '<p class="cfp1-err" id="cfp1Err" style="display:none"></p>' +
        '<div class="cfp1-actions">' +
          '<a class="cfp1-dl" id="cfp1Dl" target="_blank" rel="noopener" style="display:none">Download your report (PDF)</a>' +
          '<button class="cfp1-share" id="cfp1Share" type="button" style="display:none">Share report</button>' +
          '<p class="cfp1-hint" id="cfp1Hint" style="display:none">Can\'t find the file later? Use Share to send it to your email or WhatsApp.</p>' +
          '<button class="cfp1-close" id="cfp1Close" type="button">Close</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    els.ov = ov;
    els.sub = ov.querySelector('#cfp1Sub');
    els.fill = ov.querySelector('#cfp1Fill');
    els.err = ov.querySelector('#cfp1Err');
    els.dl = ov.querySelector('#cfp1Dl');
    els.wait = ov.querySelector('#cfp1Wait');
    ov.querySelector('#cfp1Close').addEventListener('click', closeModal);
    els.share = ov.querySelector('#cfp1Share');
    els.hint = ov.querySelector('#cfp1Hint');
    els.share.addEventListener('click', function () { var u = els.dl && els.dl.getAttribute('href'); if (u) shareReport(u); });
    ov.addEventListener('click', function (e) { if (!running && e.target === ov) closeModal(); });
  }
  function openModal() { buildModal(); resetModal(); els.ov.classList.add('open'); document.body.style.overflow = 'hidden'; }
  function closeModal() { if (els.ov) { els.ov.classList.remove('open'); document.body.style.overflow = ''; } }
  function resetModal() {
    els.err.style.display = 'none'; els.err.textContent = '';
    els.dl.style.display = 'none'; els.dl.removeAttribute('href');
    if (els.share) els.share.style.display = 'none';
    if (els.hint) els.hint.style.display = 'none';
    els.sub.textContent = 'CF Computer is researching and writing your report — thinking deeply so it\'s genuinely useful to you.';
    if (els.wait) els.wait.style.display = 'block';
    els.fill.style.width = '0';
    STEPS.forEach(function (_, i) {
      var st = document.getElementById('cfp1Step-' + i);
      if (st) { st.classList.remove('active', 'done'); }
    });
  }
  function setStep(activeIdx) {
    STEPS.forEach(function (_, i) {
      var st = document.getElementById('cfp1Step-' + i); if (!st) return;
      st.classList.remove('active', 'done');
      if (i < activeIdx) st.classList.add('done');
      else if (i === activeIdx) st.classList.add('active');
    });
    els.fill.style.width = Math.round((activeIdx / STEPS.length) * 100) + '%';
    if (running && els.sub && activeIdx >= STEPS.length - 1) {
      els.sub.textContent = 'Almost there — CF is reflecting deeply on your path. This is the part that takes the longest. 🌱';
    }
  }

  // ---------- the report call ----------
  var running = false;
  var sharing = false;
  function buildReport(problemStatement) {
    if (running) return;
    var problem = (problemStatement || '').trim();
    if (problem.length < 10) {
      problem = (lastUserMessage() || problem).trim();
    }
    if (problem.length < 10) {
      openModal(); showError('I need a bit more detail about the problem first. Tell CF more, then try again.');
      return;
    }
    if (problem.length > 2000) problem = problem.slice(0, 2000);

    running = true;
    openModal();

    // Optimistic step animation while the (non-streaming) endpoint works.
    var idx = 0; setStep(0);
    var ticker = setInterval(function () {
      if (idx < STEPS.length - 1) { idx++; setStep(idx); }
    }, 2600);

    var payload = { problem_statement: problem, session_id: getSessionId(), user_id: getUserId() };
    log('POST /cf-computer/report', payload);

    fetch(BACKEND + '/cf-computer/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    }).then(function (r) {
      clearInterval(ticker);
      if (!r.ok) {
        var msg = (r.data && (r.data.detail && (r.data.detail.message || r.data.detail)) ) || r.data.message;
        if (r.status === 429) msg = msg || "You've reached today's report limit (2 per day). Try again tomorrow.";
        throw new Error(msg || ('Report failed (HTTP ' + r.status + ')'));
      }
      finishReport(r.data);
    }).catch(function (err) {
      clearInterval(ticker);
      log('report error', err);
      showError(err && err.message ? err.message : 'Something went wrong building your report.');
      running = false;
    });
  }

  function finishReport(data) {
    setStep(STEPS.length); // all done
    els.fill.style.width = '100%';
    if (els.wait) els.wait.style.display = 'none';
    els.sub.textContent = 'Your validation report is ready.';
    var reportId = data && data.report_id;
    if (reportId) {
      // Prefer the durable Supabase Storage URL (survives backend redeploys / preview sleep,
      // so shared links don't rot). Fall back to the backend's local copy at
      // {backend}/reports/{id}.pdf if the durable URL is missing.
      var durable = data && data.download_url;
      els.dl.href = (durable && /^https?:\/\//.test(durable))
        ? durable
        : (BACKEND + '/reports/' + encodeURIComponent(reportId) + '.pdf');
      els.dl.style.display = 'inline-flex';
      if (els.share) els.share.style.display = 'inline-flex';
      if (els.hint) els.hint.style.display = 'block';
    } else {
      showError('Report generated, but no file id was returned. Please try again.');
    }
    running = false;
  }
  function showError(msg) {
    if (!els.err) return;
    if (els.wait) els.wait.style.display = 'none';
    els.sub.textContent = 'We hit a snag.';
    els.err.textContent = msg; els.err.style.display = 'block';
  }
  function flashShare(text) {
    if (!els.share) return;
    var orig = els.share.getAttribute('data-label') || els.share.textContent;
    els.share.setAttribute('data-label', orig);
    els.share.textContent = text;
    setTimeout(function () { if (els.share) els.share.textContent = els.share.getAttribute('data-label') || 'Share report'; }, 2200);
  }
  function copyLink(url) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { flashShare('✓ Link copied'); })
        .catch(function () { window.prompt('Copy this link to share your report:', url); });
    } else { window.prompt('Copy this link to share your report:', url); }
  }
  function shareReport(url) {
    if (sharing) return;            // guard against double clicks / double events
    sharing = true;
    var data = { title: 'My CF Computer validation report', url: url };
    if (navigator.share) {
      navigator.share(data).then(function () { sharing = false; }).catch(function (e) {
        sharing = false;
        if (e && e.name === 'AbortError') return;   // user cancelled -> nothing
        copyLink(url);                              // genuine failure -> copy link once
      });
      return;
    }
    copyLink(url);                                  // no web-share -> copy link once
    sharing = false;
  }

  // ---------- DOM detection / trigger button ----------
  function lastUserMessage() {
    var nodes = document.querySelectorAll('#cf-msgs .cf-msg.user .cf-bubble');
    if (!nodes.length) return '';
    return (nodes[nodes.length - 1].textContent || '').trim();
  }
  function injectTrigger(afterBubbleEl, problem) {
    if (afterBubbleEl.querySelector('.cfp1-btn')) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'cfp1-btn';
    btn.setAttribute('aria-label', 'Build my validation report with CF Computer');
    btn.innerHTML = '<span class="cfp1-gdot" aria-hidden="true"></span> Build your validation report';
    btn.addEventListener('click', function () { openIntake(problem); });
    afterBubbleEl.appendChild(btn);
  }

  var cfReplyCount = 0;
  function handleCfBubble(bubbleWrap) {
    var bubble = bubbleWrap.querySelector('.cf-bubble');
    if (!bubble) return;
    cfReplyCount++;
    var text = (bubble.textContent || '').trim();
    var matched = CONFIRM_PATTERNS.some(function (p) { return p.test(text); });
    // Extract a problem statement: confirmation sentence, else last user message.
    var problem = '';
    if (matched) {
      var m = text.match(/(?:your (?:core )?problem (?:is|seems to be)|the problem (?:is|you'?re solving)[:\s]+)([^.!?]+[.!?]?)/i);
      problem = (m && m[1] ? m[1] : lastUserMessage()).trim();
    }
    if (matched && problem) {
      injectTrigger(bubble.parentNode || bubbleWrap, problem);
    }
  }

  function observe() {
    var msgs = document.getElementById('cf-msgs');
    if (!msgs) { setTimeout(observe, 800); return; }
    log('observing #cf-msgs');
    var mo = new MutationObserver(function (muts) {
      muts.forEach(function (mu) {
        for (var i = 0; i < mu.addedNodes.length; i++) {
          var n = mu.addedNodes[i];
          if (n.nodeType === 1 && n.classList && n.classList.contains('cf-msg') && n.classList.contains('cf')) {
            // bubble text may stream in; check now and shortly after.
            handleCfBubble(n);
            (function (node) { setTimeout(function () { handleCfBubble(node); }, 1500); })(n);
          }
        }
      });
    });
    mo.observe(msgs, { childList: true });
  }

  // Public manual trigger (handy for testing): window.LNSBuildReport("my problem ...")
  window.LNSBuildReport = function (text) { buildReport(text || lastUserMessage()); };


  // ---------- CF Computer intake gate ----------
  // Both entry points (header orb + inline button) open this FIRST. Nothing is sent to the
  // backend — and no tokens are spent — until the user has a real problem statement and taps
  // Build. This prevents accidental, costly empty reports.
  var iel = {};
  function buildIntake() {
    if (document.getElementById('cfp1Intake')) return;
    var ov = document.createElement('div');
    ov.id = 'cfp1Intake'; ov.className = 'cfp1-overlay';
    ov.innerHTML =
      '<div class="cfp1-card" role="dialog" aria-modal="true" aria-label="Start a CF Computer report">' +
        '<div class="cfp1-orbsm" aria-hidden="true"><span class="cfc-ring cfc-r1"></span><span class="cfc-ring cfc-r2"></span><span class="cfc-ring cfc-r3"></span><span class="cfc-core"></span></div>' +
        '<h3 class="cfp1-title">CF Computer</h3>' +
        '<p class="cfp1-sub">Turn a problem into a branded validation report — framed, researched, and reviewed in CF\'s voice.</p>' +
        '<textarea id="cfp1Problem" class="cfp1-ta" placeholder="The problem you want to validate — a sentence or two. e.g. Final-year students feel anxious and directionless about their careers."></textarea>' +
        '<p class="cfp1-ihint" id="cfp1Ihint">Not sure yet? Keep talking with CF — it helps you find and frame your problem first, then come back here.</p>' +
        '<div class="cfp1-actions">' +
          '<button class="cfp1-build" id="cfp1Build" type="button" disabled>Build my validation report</button>' +
          '<button class="cfp1-close" id="cfp1TalkCF" type="button">Talk to CF first</button>' +
        '</div>' +
        '<p class="cfp1-meta">Takes a few minutes · up to 2 reports a day</p>' +
      '</div>';
    document.body.appendChild(ov);
    iel.ov = ov;
    iel.ta = ov.querySelector('#cfp1Problem');
    iel.build = ov.querySelector('#cfp1Build');
    iel.hint = ov.querySelector('#cfp1Ihint');
    iel.sync = function () {
      var v = (iel.ta.value || '').trim();
      iel.build.disabled = v.length < 20;
      if (iel.hint) iel.hint.textContent = v.length > 0 && v.length < 20
        ? 'A little more detail helps CF build something useful — a full sentence or two.'
        : 'Not sure yet? Keep talking with CF — it helps you find and frame your problem first, then come back here.';
    };
    iel.ta.addEventListener('input', iel.sync);
    iel.build.addEventListener('click', function () {
      var v = (iel.ta.value || '').trim();
      if (v.length < 20) return;
      closeIntake();
      buildReport(v);
    });
    ov.querySelector('#cfp1TalkCF').addEventListener('click', function () {
      closeIntake();
      var inp = document.getElementById('cf-input');
      if (inp) { try { inp.focus(); } catch (e) {} }
    });
    ov.addEventListener('click', function (e) { if (e.target === ov) closeIntake(); });
  }
  function openIntake(prefill) {
    buildIntake();
    iel.ta.value = (prefill && prefill.trim().length >= 10) ? prefill.trim() : '';
    iel.sync();
    iel.ov.classList.add('open');
    document.body.style.overflow = 'hidden';
    setTimeout(function () { try { iel.ta.focus(); } catch (e) {} }, 60);
  }
  function closeIntake() { if (iel.ov) { iel.ov.classList.remove('open'); document.body.style.overflow = ''; } }

  // ---------- CF Computer header orb (path A) ----------
  // Opens the intake gate (never fires a report directly). Pre-fills ONLY with a real
  // problem the user already described to CF; otherwise the box stays blank.
  function openCFComputer() {
    openIntake(lastUserMessage());
  }
  function injectHeaderOrb() {
    var btns = document.getElementById('cf-header-btns');
    if (!btns) {
      injectHeaderOrb._n = (injectHeaderOrb._n || 0) + 1;
      if (injectHeaderOrb._n < 60) setTimeout(injectHeaderOrb, 800);
      return;
    }
    if (document.getElementById('cfp1HeaderOrb')) return;
    var orb = document.createElement('button');
    orb.id = 'cfp1HeaderOrb'; orb.type = 'button';
    orb.setAttribute('aria-label', 'Open CF Computer');
    orb.title = 'CF Computer — build your validation report';
    orb.innerHTML =
      '<span class="cfc-ring cfc-r1" aria-hidden="true"></span>' +
      '<span class="cfc-ring cfc-r2" aria-hidden="true"></span>' +
      '<span class="cfc-ring cfc-r3" aria-hidden="true"></span>' +
      '<span class="cfc-core" aria-hidden="true"></span>';
    orb.addEventListener('click', openCFComputer);
    var min = document.getElementById('cf-min');
    if (min && min.parentNode === btns) btns.insertBefore(orb, min);
    else btns.insertBefore(orb, btns.firstChild);
    log('header orb injected');
  }

  function init() {
    injectStyles();
    observe();
    injectHeaderOrb();
    log('addon ready · backend =', BACKEND);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
