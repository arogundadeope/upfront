/* Live page logic: conversation -> /api/answer -> tile and panels. */
(function () {
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function usd(n) { return n == null ? '—' : '$' + Math.round(n).toLocaleString('en-US'); }
  function title(s) { return String(s || '').toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  var state = {
    text: '', answers: {}, benefits: null,
    location: { lat: 37.7599, lon: -122.4148, label: 'the Mission, San Francisco' },
    radius: 5, episode: null, pricing: null, plans: null
  };

  var thread = $('thread'), input = $('ask'), send = $('send');

  // location: browser first, default otherwise
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(function (pos) {
      state.location = { lat: pos.coords.latitude, lon: pos.coords.longitude, label: 'your location' };
      $('loc').textContent = 'Near your location';
    }, function () { }, { timeout: 4000 });
  }

  function addUser(text) {
    var m = el('div', 'msg user', esc(text)); thread.appendChild(m);
    requestAnimationFrame(function () { m.classList.add('in'); });
    return m;
  }
  function addSys(inner) {
    var m = el('div', 'msg sys'); m.appendChild(inner); thread.appendChild(m);
    requestAnimationFrame(function () { m.classList.add('in'); });
    m.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
    return m;
  }
  function thinking() {
    var p = el('p', null, '<span class="dots"><i></i><i></i><i></i></span>');
    return addSys(p);
  }

  function workingCard(steps) {
    var box = el('div', 'msg work');
    var ul = el('ul');
    steps.forEach(function (s) { ul.appendChild(el('li', null, '<i></i>' + esc(s))); });
    box.appendChild(ul);
    var bar = el('div', 'bar'); var fill = el('i'); bar.appendChild(fill); box.appendChild(bar);
    thread.appendChild(box);
    requestAnimationFrame(function () { box.classList.add('in'); });
    var lis = ul.children, i = 0;
    var t = setInterval(function () {
      if (i >= lis.length) { clearInterval(t); return; }
      lis[i].classList.add('in');
      (function (li, k) { setTimeout(function () { li.classList.add('done'); fill.style.width = ((k + 1) / lis.length * 100) + '%'; }, 350); })(lis[i], i);
      i++;
    }, 420);
    return { box: box, finish: function () { clearInterval(t); Array.prototype.forEach.call(lis, function (li) { li.classList.add('in', 'done'); }); fill.style.width = '100%'; } };
  }

  function ask(text) {
    if (text) { state.text = text; state.answers = {}; state.episode = null; addUser(text); }
    var think = thinking();
    var body = JSON.stringify({ text: state.text, location: state.location, answers: state.answers, benefits: state.benefits, radius: state.radius, episode: state.episode });
    fetch('/api/jobs/answer', { method: 'POST', headers: { 'content-type': 'application/json' }, body: body })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var work = null, shown = 0, misses = 0;
        function poll() {
          fetch('/api/jobs/' + j.job).then(function (r) { if (r.status === 404) { throw new Error('gone'); } return r.json(); }).then(function (job) {
            misses = 0;
            if (job.events.length && !work) { think.remove(); work = liveWorkCard(j.job); }
            while (work && shown < job.events.length) { work.add(job.events[shown].text); shown++; }
            // (agent screen frames are shown in hospital rows during fetches, not here)
            if (job.status === 'running') { setTimeout(poll, 700); return; }
            if (job.status === 'failed') { if (work) work.done(); else think.remove(); addSys(el('p', null, 'Something went wrong: ' + esc(job.error))); return; }
            var d = job.result;
            if (!work) think.remove(); else work.done();
            handle(d);
          }).catch(function (e) {
            if (e.message !== 'gone' && misses++ < 12) { setTimeout(poll, 1500); return; }   // ride out a brief server hiccup
            if (work) work.done(); else think.remove();
            addSys(el('p', null, e.message === 'gone' ? 'The server restarted mid-run. Send it again.' : 'Lost the connection to the server. Send it again.'));
          });
        }
        poll();
      })
      .catch(function (e) { think.remove(); addSys(el('p', null, 'Something went wrong: ' + esc(e.message))); });
  }

  function handle(d) {
    state.episode = d.episode;
    if (!d.episode.components || !d.episode.components.length) {
      var note = d.episode.notes && d.episode.notes[0];
      addSys(el('p', null, esc(note || "I couldn't work out what procedure that is yet. Try describing it another way.")));
      return;
    }
    if (d.episode.questions && d.episode.questions.length) { renderQuestions(d.episode); return; }
    if (d.episode.insurer === null) { state.benefits = { cash: true }; }
    if (!state.benefits) { renderBenefitsQuestion(d.episode); return; }
    state.pricing = d.pricing; state.plans = d.plans; render();
  }

  // a working card that fills with the agent's own progress lines
  function liveWorkCard(jobId) {
    var box = el('div', 'msg work'); var ul = el('ul'); box.appendChild(ul);
    var screen = el('div', 'screen'); var cap = el('span', 'cap', "Agent's screen"); var img = el('img'); img.alt = "The agent's screen"; screen.appendChild(img); screen.appendChild(cap); screen.hidden = true; box.appendChild(screen);
    var bar = el('div', 'bar'); var fill = el('i'); bar.appendChild(fill); box.appendChild(bar);
    var steer = el('div', 'steer'); var si = el('input'); si.placeholder = 'Tell the agent something while it works'; var sb = el('button', null, 'Steer'); sb.type = 'button';
    steer.appendChild(si); steer.appendChild(sb); box.appendChild(steer);
    function sendSteer() { var t = si.value.trim(); if (!t || !jobId) return; si.value = ''; fetch('/api/jobs/' + jobId + '/steer', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t }) }); }
    sb.addEventListener('click', sendSteer); si.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.stopPropagation(); sendSteer(); } });
    thread.appendChild(box); requestAnimationFrame(function () { box.classList.add('in'); });
    var n = 0;
    return {
      frame: function (dataUrl) { if (dataUrl) { img.src = dataUrl; screen.hidden = false; } },
      add: function (text) {
        var prev = ul.lastElementChild; if (prev) prev.classList.add('done');
        var li = el('li', null, '<i></i>' + esc(text)); ul.appendChild(li);
        requestAnimationFrame(function () { li.classList.add('in'); });
        n++; fill.style.width = Math.min(95, 15 + n * 8) + '%';
        if (ul.children.length > 7) ul.removeChild(ul.firstElementChild);
        box.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
      },
      done: function () { var prev = ul.lastElementChild; if (prev) prev.classList.add('done'); fill.style.width = '100%'; steer.remove(); }
    };
  }

  function readback(ep) {
    var parts = [ep.procedure || 'That procedure'];
    if (ep.insurer && ep.insurer !== 'unknown') parts.push('with ' + ep.insurer.replace(' of California', '')); else if (ep.insurer === null) parts.push('paying cash');
    var p = el('p', null, esc(parts.join(', ')) + '.');
    var loc = el('p', 'loc', 'Near ' + esc(state.location.label) + '<u id="chg">Change</u>');
    var box = el('div'); box.appendChild(p); box.appendChild(loc);
    return box;
  }

  function renderQuestions(ep) {
    var box = readback(ep);
    var qs = el('div', 'qs');
    ep.questions.forEach(function (q) {
      var wrap = el('div'); wrap.setAttribute('data-q', q.id); wrap.appendChild(el('div', 'q', esc(q.text)));
      var seg = el('span', 'seg');
      q.options.forEach(function (o) {
        var b = el('b', null, esc(o));
        b.addEventListener('click', function () {
          Array.prototype.forEach.call(seg.children, function (x) { x.classList.remove('on'); }); b.classList.add('on');
          state.answers[q.id] = o === 'Blue Shield' ? 'Blue Shield of California' : (o === 'Paying cash' ? null : o);
          if (q.id === 'insurer') { state.text = state.text + ' (insurance: ' + o + ')'; if (o === 'Paying cash') { state.answers.product = null; var pq = box.querySelector('[data-q="product"]'); if (pq) pq.remove(); } }
          if (ep.questions.every(function (qq) { return qq.id in state.answers; })) {
            setTimeout(function () { addUser(ep.questions.map(function (qq) { return qq.id === 'product' && state.answers.product === null ? null : (state.answers[qq.id] || 'Paying cash'); }).filter(Boolean).join('. ') + '.'); ask(); }, 350);
          }
        });
        seg.appendChild(b);
      });
      wrap.appendChild(seg); qs.appendChild(wrap);
    });
    box.appendChild(qs);
    addSys(box);
  }

  function renderBenefitsQuestion(ep) {
    var box = el('div');
    box.appendChild(el('p', null, 'How much is left on your deductible this year?'));
    box.appendChild(el('p', 'loc', "It's in your insurer's app, under Benefits."));
    var row = el('div', 'qs');
    var field = el('span', 'field'); var inp = el('input'); inp.type = 'text'; inp.placeholder = '$'; inp.className = 'fieldin'; field.appendChild(inp);
    var seg = el('span', 'seg'); var ns = el('b', null, 'Not sure'); seg.appendChild(ns);
    var go = el('b', null, 'Continue'); seg.appendChild(go);
    row.appendChild(field); row.appendChild(seg); box.appendChild(row);
    function done(ded, assumed) {
      state.benefits = { deductible_left: ded, coinsurance: 0.2, oop_left: 4000, assumed: !!assumed };
      addUser(assumed ? 'Not sure.' : 'About ' + usd(ded) + ' left.'); ask();
    }
    ns.addEventListener('click', function () { done(1500, true); });
    go.addEventListener('click', function () { var v = parseFloat(inp.value.replace(/[^0-9.]/g, '')); if (!isNaN(v)) done(v, false); });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') go.click(); });
    addSys(box); inp.focus();
  }

  // ------------------------------------------------------------ render answer
  function render() {
    var P = state.pricing, ep = state.episode, rec = P.recommended;
    var tile = $('tile'), story = $('story');
    if (!rec) {
      var withFile = P.hospitals.filter(function (h) { return h.file_id; });
      var allUnlisted = withFile.length && withFile.every(function (h) { return h.why_missing === 'not_listed'; });
      var msg = allUnlisted ? 'None of the ' + withFile.length + ' hospitals within ' + state.radius + ' miles lists ' + esc(ep.procedure).toLowerCase() + ' in its price file. Try a wider radius, or ask the hospital directly.'
                            : 'Hospitals near you list this, but none publishes a ' + esc(ep.insurer ? ep.insurer.split(' ')[0] : 'cash') + ' rate for it. Try a wider radius.';
      tile.innerHTML = '<p class="k">No priced option nearby</p><h1>We could not price this within <u class="radius" id="radius0">' + state.radius + ' miles</u>.</h1><p class="line">' + msg + '</p>';
      tile.classList.add('in'); story.hidden = true;
      $('radius0').addEventListener('click', function () { state.radius = state.radius >= 25 ? 5 : (state.radius >= 10 ? 25 : 10); ask(); });
      return;
    }
    var priced = P.hospitals.filter(function (h) { return h.total; }).sort(function (a, b) { return a.total - b.total; });
    var worst = priced[priced.length - 1];
    var reason = [rec.distance + ' miles away'];
    if (rec.rating) reason.push('rated ' + rec.rating + ' of 5');
    if (rec.confidence === 'verified') reason.push('and the lowest published price near you');
    else if (rec.confidence === 'unconfirmed') reason.push('and the lowest published price near you, though its file shows no recent cases');
    else if (rec.confidence === 'estimate') reason.push('and the lowest estimate near you (no ' + (ep.insurer ? ep.insurer.split(' ')[0] : 'cash') + ' rate is published for part of this)');
    else reason.push('and the lowest price near you, though part of the episode is not published there');
    var more = worst && worst.ccn !== rec.ccn && worst.total - rec.total > 500 ? ' ' + worst.name + ' would cost about ' + usd(worst.total - rec.total) + ' more for the same procedure' + (rec.you_pay != null && worst.you_pay != null ? ', ' + usd(worst.you_pay - rec.you_pay) + ' of it yours.' : '.') : '';
    tile.innerHTML =
      '<p class="k">Our recommendation</p><h1>' + esc(rec.name) + '</h1>' +
      '<p class="line">' + esc(reason.join(', ')) + '.' + esc(more) + '</p>' +
      '<div class="kpis">' +
      (state.benefits.cash
        ? '<div class="kpi you"><span>You pay</span><b class="tab">' + usd(rec.total) + '</b><small>cash, no insurance</small></div><div class="kpi"><span>Hospital side</span><b class="tab">' + usd(rec.lines.filter(function (l) { return l.role === 'facility' && !l.optional; }).reduce(function (a, l) { return a + l.amount; }, 0)) + '</b><small>published cash prices</small></div>'
        : '<div class="kpi you"><span>You pay</span><b class="tab">' + usd(rec.you_pay) + '</b><small>' + (state.benefits.assumed ? 'typical plan assumed' : usd(state.benefits.deductible_left) + ' deductible, then 20%') + ' <u id="editb">edit</u></small></div>' +
          '<div class="kpi"><span>Total cost</span><b class="tab">' + usd(rec.total) + '</b><small>' + esc(ep.insurer ? ep.insurer.split(' ')[0] + ' pays ' + usd(rec.total - rec.you_pay) : 'cash price') + '</small></div>') +
      '<div class="kpi"><span>Rating</span><b>' + (rec.rating ? rec.rating + ' / 5' : '—') + '</b><small>Medicare hospital rating</small></div>' +
      '<div class="kpi"><span>Distance</span><b class="tab">' + rec.distance + ' mi</b><small>' + esc(title(rec.city)) + '</small></div></div>' +
      '<div class="cta"><button class="btn" type="button">Book an appointment</button></div>';
    requestAnimationFrame(function () { tile.classList.add('in'); });
    setTimeout(function () { tile.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); }, 300);

    // panel 1: breakdown
    var lines = rec.lines.filter(function (l) { return !l.optional; });
    var opt = rec.lines.filter(function (l) { return l.optional; });
    var colors = ['var(--fg)', 'var(--bar-3)', 'var(--bar-2)', 'var(--bar)'];
    var tot = lines.reduce(function (s, l) { return s + l.amount; }, 0);
    $('p-break-h').textContent = 'What the ' + usd(rec.total) + ' covers';
    $('p-break-line').textContent = 'The ' + lines.map(function (l) { return l.label.toLowerCase(); }).join(', the ').replace(/, the ([^,]*)$/, ' and the $1') + ' bill separately.' + (opt.length ? ' ' + opt.map(function (o) { return o.label.replace(/,.*$/, '') + ' adds about ' + usd(o.amount) + (o.label.indexOf(',') > -1 ? o.label.slice(o.label.indexOf(',')) : ''); }).join('. ') + '.' : '');
    $('p-break-stack').innerHTML = lines.map(function (l, i) { return '<i style="--w:' + (l.amount / tot * 100).toFixed(1) + '%;background:' + colors[i % 4] + '"></i>'; }).join('') +
      opt.map(function (o) { return '<i style="--w:' + (o.amount / tot * 100).toFixed(1) + '%;background:repeating-linear-gradient(135deg,var(--bar) 0 5px,transparent 5px 9px)"></i>'; }).join('');
    $('p-break-leg').innerHTML = lines.concat(opt).map(function (l, i) { return '<div style="--c:' + (l.optional ? 'var(--bar)' : colors[i % 4]) + '"><b class="tab">' + usd(l.amount) + '</b>' + esc(l.label) + (l.estimate ? ' <span class="est">est.</span>' : '') + (l.note ? '<span class="est" style="display:block">' + esc(l.note) + '</span>' : '') + '</div>'; }).join('');
    $('p-break-fine').textContent = (ep.notes && ep.notes.join(' ')) || (lines.some(function (l) { return l.estimate; }) ? 'Doctor and anesthesia fees are estimates from Medicare rates. The hospital fee is the published contract rate.' : '');

    // panel 2: your share (not shown for cash)
    $('p-share-h').closest('.panel').hidden = !!state.benefits.cash;
    $('p-share-h').textContent = 'Your plan turns ' + usd(rec.total) + ' into ' + usd(rec.you_pay);
    $('p-share-line').textContent = state.benefits.assumed ? 'Assuming a typical plan: $1,500 deductible left, then 20%. Change it above if you know yours.' : 'You pay the ' + usd(state.benefits.deductible_left) + ' left on your deductible, then 20% of the rest.';
    var yp = rec.you_pay / rec.total * 100;
    $('p-share-split').innerHTML = '<i style="--w:' + yp.toFixed(1) + '%;background:var(--teal)"></i><i style="--w:' + (100 - yp).toFixed(1) + '%;background:var(--bar)"></i>';
    $('p-share-leg').innerHTML = '<div style="--c:var(--teal)"><b class="tab">' + usd(rec.you_pay) + '</b>You</div><div style="--c:var(--bar)"><b class="tab">' + usd(rec.total - rec.you_pay) + '</b>' + esc(ep.insurer ? ep.insurer.split(' ')[0] : 'Plan') + '</div>';

    // panel 3: nearby
    var unpriced = P.hospitals.filter(function (h) { return !h.total && h.file_id; });
    $('p-where-h').textContent = priced.length + ' place' + (priced.length === 1 ? '' : 's') + ' near you';
    $('p-where-line').innerHTML = 'Within <u class="radius" id="radius">' + state.radius + ' miles</u>' + esc(ep.insurer ? ', with ' + ep.insurer.replace(' of California', '') + (ep.product ? ' ' + ep.product : '') : ', paying cash') + '. Total for the same procedure.';
    $('radius').addEventListener('click', function () { state.radius = state.radius >= 25 ? 5 : (state.radius >= 10 ? 25 : 10); ask(); });
    var max = worst.total;
    $('list').innerHTML = priced.map(function (h) {
      var cls = 'r' + (h.ccn === rec.ccn ? ' me' : '') + (h.confidence !== 'verified' ? ' unv' : '') + (h.total > rec.total * 2 ? ' out' : '');
      var idattr = ' data-ccn="' + esc(h.ccn) + '"';
      var notes = h.lines.filter(function (l) { return l.note; }).map(function (l) { return l.note; });
      var miss = h.missing && h.missing.length ? 'Excludes ' + h.missing.join(', ').toLowerCase() : '';
      var tag = (h.ccn === rec.ccn ? '<em class="teal">Recommended</em>' : (h.confidence !== 'verified' ? '<em class="warn">' + (h.confidence === 'partial' ? 'Partial' : (notes.length ? 'Estimate' : 'Price not confirmed')) + '</em>' : '')) + (notes.length || miss ? '<em class="note">' + esc([miss].concat(notes).filter(Boolean).join('. ')) + '</em>' : '');
      return '<div class="' + cls + '"' + idattr + '><div class="n">' + esc(h.name) + '<small>' + esc(h.kind) + ' · ' + h.distance + ' mi · ' + (h.rating ? h.rating + '/5' : 'not rated') + '</small>' + tag + '</div><div class="t"><i style="--w:' + (h.total / max * 100).toFixed(1) + '%"></i></div><div class="v tab">' + usd(h.total) + '<small>you ' + usd(h.you_pay) + '</small></div></div>';
    }).join('') + unpriced.map(function (h) {
      return '<div class="r" data-ccn="' + esc(h.ccn) + '"><div class="n">' + esc(h.name) + '<small>' + esc(h.kind) + ' · ' + h.distance + ' mi · ' + (h.rating ? h.rating + '/5' : 'not rated') + '</small><em>' + (h.why_missing === 'not_listed' ? "Doesn't list this procedure" : 'No ' + esc(ep.insurer ? ep.insurer.split(' ')[0] : 'cash') + ' rate for it') + '</em></div><div class="t"><i style="--w:0"></i></div><div class="v tab">—</div></div>';
    }).join('') + P.hospitals.filter(function (h) { return !h.file_id; }).map(function (h) {
      return '<div class="r nofile" data-ccn="' + esc(h.ccn) + '"><div class="n">' + esc(h.name) + '<small>' + esc(h.kind) + ' · ' + h.distance + ' mi · ' + (h.rating ? h.rating + '/5' : 'not rated') + '</small><em>No price list in our index</em></div><div class="act"><button class="mini" type="button">Find its prices</button></div><div class="v tab">—</div></div>';
    }).join('');
    $('list').querySelectorAll('.nofile .mini').forEach(function (b) {
      b.addEventListener('click', function () {
        var row = b.closest('.r'), ccn = row.getAttribute('data-ccn');
        fetch('/api/hospital-domains').then(function (r) { return r.json(); }).then(function (map) {
          var domain = map[ccn]; if (!domain) { b.textContent = 'No website on file'; return; }
          b.disabled = true; b.textContent = 'Pine is looking…';
          var log = el('div', 'minilog'); row.appendChild(log);
          fetch('/api/jobs/index', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ domain: domain, ccn: ccn }) })
            .then(function (r) { return r.json(); }).then(function (j) {
              var shown = 0;
              (function poll() {
                fetch('/api/jobs/' + j.job).then(function (r) { return r.json(); }).then(function (job) {
                  while (shown < job.events.length) { log.textContent = job.events[shown].text; shown++; }
                  if (job.frame) { var im = row.querySelector('img.rowshot'); if (!im) { im = el('img', 'rowshot'); row.appendChild(im); } im.src = job.frame; }
                  if (job.status === 'running') { setTimeout(poll, 800); return; }
                  if (job.status === 'failed' || !(job.result && job.result.loaded && job.result.loaded.length)) { b.textContent = 'Not published anywhere we could find'; log.remove(); return; }
                  b.textContent = 'Added. Re-pricing…'; setTimeout(function () { ask(); }, 600);
                });
              })();
            });
        });
      });
    });
    var unv = priced.filter(function (h) { return !h.verified; });
    var zero = priced.filter(function (h) { return !h.verified && h.lines.some(function (l) { return l.role === 'facility' && l.cases === 0; }); });
    $('p-where-fine').textContent = zero.length ? zero.map(function (h) { return h.name; }).join(' and ') + ' list' + (zero.length === 1 ? 's' : '') + ' a rate, but the file shows no paid cases behind it in the past year.' : '';
    drawMap(priced, rec);

    // panel 4: other plans
    var plans = state.plans || [];
    var mine = plans.find(function (p) { return p.insurer === ep.insurer; });
    $('p-plans-h').textContent = 'The same procedure under other plans';
    $('p-plans-line').textContent = 'What ' + rec.name + ' charges for the hospital side of this, by plan' + (plans.some(function (p) { return p.lob === 'cash'; }) ? ', and if you paid cash.' : '.');
    var pmax = Math.max.apply(null, plans.map(function (p) { return p.price; }));
    $('plans').innerHTML = plans.map(function (p) {
      var me = p.insurer === ep.insurer;
      return '<div class="p' + (me ? ' me' : '') + (p.lob === 'cash' ? ' cash' : '') + '"><div class="n">' + esc(p.insurer === ep.insurer ? p.insurer.replace(' of California', '') + ', your plan' : (p.lob === 'cash' ? 'Cash, no insurance' : p.insurer.replace(' of California', ''))) + '</div><div class="t"><i style="--w:' + (p.price / pmax * 100).toFixed(1) + '%"></i></div><div class="v tab">' + usd(p.price) + (p.estimate ? ' <span class="est">est.</span>' : '') + '</div></div>';
    }).join('');
    var cash = plans.find(function (p) { return p.lob === 'cash'; });
    $('p-plans-fine').textContent = cash && mine ? (cash.price > mine.price ? 'Cash would cost more than your rate, and the doctors would bill you on top.' : 'The hospital cash price is below your plan rate. Ask about self-pay before you book.') : '';

    story.hidden = false;
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.remove('in'); });
    observe();
    var eb = $('editb'); if (eb) eb.addEventListener('click', function () { state.benefits = null; ask(); });
  }

  function observe() {
    var panels = document.querySelectorAll('.panel');
    if ('IntersectionObserver' in window && !reduce && !new URLSearchParams(location.search).get('all')) {
      var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }); }, { threshold: 0.15 });
      panels.forEach(function (p) { io.observe(p); });
    } else { panels.forEach(function (p) { p.classList.add('in'); }); }
  }

  // ------------------------------------------------------------ map
  var svgNS = 'http://www.w3.org/2000/svg';
  function drawMap(priced, rec) {
    var inSF = state.location.lat > 37.70 && state.location.lat < 37.82 && state.location.lon > -122.52 && state.location.lon < -122.35;
    $('tg-map').hidden = !inSF;
    if (!inSF) return;
    var svg = $('svgmap'), base = $('base'), pts = $('pts');
    base.innerHTML = ''; pts.innerHTML = '';
    var px = 80, C = { lat: 37.7650, lon: -122.4250 }, cx = 320, cy = 240;
    function P(lat, lon) { return [cx + (lon - C.lon) * 54.6 * px, cy - (lat - C.lat) * 69 * px]; }
    function path(a, close) { return a.map(function (p, i) { var q = P(p[0], p[1]); return (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1); }).join(' ') + (close ? ' Z' : ''); }
    function mk(tag, attrs, parent) { var e = document.createElementNS(svgNS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); (parent || base).appendChild(e); return e; }
    var land = [[37.787, -122.508], [37.790, -122.495], [37.7935, -122.483], [37.808, -122.477], [37.806, -122.465], [37.807, -122.445], [37.8075, -122.425], [37.810, -122.412], [37.806, -122.402], [37.799, -122.396], [37.795, -122.3935], [37.788, -122.388], [37.780, -122.387], [37.772, -122.386], [37.766, -122.383], [37.758, -122.383], [37.748, -122.387], [37.740, -122.376], [37.730, -122.358], [37.720, -122.370], [37.710, -122.385], [37.700, -122.390], [37.700, -122.520], [37.720, -122.510], [37.750, -122.512], [37.775, -122.514], [37.787, -122.508]];
    mk('path', { d: path(land, true), 'class': 'land' });
    [[[37.7735, -122.511], [37.7735, -122.4545], [37.7665, -122.4545], [37.7665, -122.511]],
     [[37.810, -122.478], [37.8065, -122.447], [37.7885, -122.447], [37.7885, -122.4835], [37.7935, -122.4835]],
     [[37.7585, -122.4535], [37.7575, -122.4440], [37.7480, -122.4450], [37.7485, -122.4560]],
     [[37.7615, -122.4285], [37.7615, -122.4255], [37.7580, -122.4255], [37.7580, -122.4285]]].forEach(function (p) { mk('path', { d: path(p, true), 'class': 'park' }); });
    var roads = [[[37.7946, -122.3949], [37.7795, -122.4140], [37.7625, -122.4350], [37.7560, -122.4460]], [[37.788, -122.400], [37.7755, -122.4145], [37.765, -122.4195], [37.752, -122.4185], [37.740, -122.4225], [37.728, -122.4315]], [[37.806, -122.4245], [37.7745, -122.4195], [37.752, -122.4165]], [[37.788, -122.405], [37.7805, -122.470], [37.780, -122.512]], [[37.788, -122.4760], [37.720, -122.4750]], [[37.800, -122.4420], [37.760, -122.4350]], [[37.7485, -122.3885], [37.7485, -122.4255]], [[37.806, -122.405], [37.7955, -122.3935], [37.787, -122.388]]];
    var fwys = [[[37.790, -122.392], [37.7745, -122.4045], [37.765, -122.405], [37.750, -122.4055], [37.735, -122.4035], [37.720, -122.401]], [[37.770, -122.392], [37.757, -122.393], [37.746, -122.396], [37.737, -122.405], [37.730, -122.420], [37.722, -122.440]]];
    fwys.forEach(function (r) { mk('path', { d: path(r), 'class': 'fwy' }); });
    roads.forEach(function (r) { mk('path', { d: path(r), 'class': 'road-e' }); });
    roads.forEach(function (r) { mk('path', { d: path(r), 'class': 'road' }); });
    [['Presidio', 37.800, -122.465], ['Marina', 37.803, -122.437], ['Richmond', 37.780, -122.490], ['Golden Gate Park', 37.770, -122.483], ['Sunset', 37.750, -122.490], ['Haight', 37.7705, -122.449], ['Pacific Heights', 37.792, -122.436], ['SoMa', 37.779, -122.401], ['Mission', 37.7665, -122.4165], ['Castro', 37.7605, -122.4365], ['Noe Valley', 37.7465, -122.4305], ['Potrero Hill', 37.7625, -122.396], ['Bernal Heights', 37.741, -122.414]].forEach(function (h) { var q = P(h[1], h[2]); mk('text', { x: q[0], y: q[1], 'class': 'hood' }).textContent = h[0]; });
    mk('path', { d: 'M24 456 H' + (24 + px), 'class': 'scale' }); mk('path', { d: 'M24 452 V460 M' + (24 + px) + ' 452 V460', 'class': 'scale' });
    mk('text', { x: 24 + px / 2, y: 448, 'class': 'scalet', 'text-anchor': 'middle' }).textContent = '1 mile';
    var yq = P(state.location.lat, state.location.lon);
    mk('circle', { cx: yq[0], cy: yq[1], r: 14, 'class': 'youring' }, pts); mk('circle', { cx: yq[0], cy: yq[1], r: 6, 'class': 'you' }, pts);
    mk('text', { x: yq[0] + 12, y: yq[1] + 4, 'class': 'lab' }, pts).textContent = 'You';
    var tip = $('tip');
    priced.forEach(function (h) {
      var q = P(h.lat, h.lon), x = q[0], y = q[1];
      var cls = !h.verified ? 'unv' : (h.total <= rec.total * 1.15 ? 'lo' : (h.total > rec.total * 2 ? 'hi' : 'mid'));
      var c = mk('circle', { cx: x, cy: y, r: 8, 'class': 'pt ' + cls }, pts);
      var side = x > 320 ? 'r' : 'l';
      mk('text', { x: side === 'r' ? x + 13 : x - 13, y: y + 4, 'class': 'lab', 'text-anchor': side === 'r' ? 'start' : 'end' }, pts).textContent = h.name;
      function show() { var r = svg.getBoundingClientRect(), s = r.width / 640; tip.innerHTML = '<b>' + esc(h.name) + '</b>' + usd(h.total) + ' total · <strong>you pay ' + usd(h.you_pay) + '</strong><br><span>' + h.distance + ' mi away · ' + (h.rating ? 'rated ' + h.rating + '/5' : 'not rated') + '</span>'; tip.style.left = (x * s) + 'px'; tip.style.top = (y * s) + 'px'; tip.classList.add('on'); }
      c.addEventListener('mouseenter', show); c.addEventListener('click', show); c.addEventListener('mouseleave', function () { tip.classList.remove('on'); });
    });
    $('mkey').innerHTML = '<span><i style="background:var(--teal)"></i>About ' + usd(rec.total) + '</span><span><i style="background:var(--bar-2)"></i>Up to 2x</span><span><i style="background:var(--out)"></i>Over 2x</span><span><i style="border:2px dashed var(--fg-3)"></i>Not confirmed</span>';
  }

  $('tg-list').addEventListener('click', function () { $('list').hidden = false; $('map').hidden = true; this.classList.add('on'); $('tg-map').classList.remove('on'); });
  $('tg-map').addEventListener('click', function () { $('list').hidden = true; $('map').hidden = false; this.classList.add('on'); $('tg-list').classList.remove('on'); });

  // ------------------------------------------------------------ input
  function submit() { var t = input.value.trim(); if (!t) return; input.value = ''; ask(t); }
  send.addEventListener('click', submit);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  document.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'chg') {
      var v = prompt('Where are you? (lat, lon)', state.location.lat + ', ' + state.location.lon);
      if (v) { var a = v.split(',').map(parseFloat); if (a.length === 2 && !isNaN(a[0])) { state.location = { lat: a[0], lon: a[1], label: 'your location' }; } }
    }
  });
  $('replay').addEventListener('click', function () { location.reload(); });

  // demo mode: ?q=<request>&a=<answer>&a=<answer>&ded=1000 auto-runs the flow (used for the recorded run and for tests)
  var params = new URLSearchParams(location.search);
  if (params.get('q')) {
    var autoAnswers = params.getAll('a'), autoDed = params.get('ded');
    var origAddSys = addSys;
    addSys = function (inner) {
      var m = origAddSys(inner);
      setTimeout(function () {
        var segs = m.querySelectorAll('.seg b');
        var clicked = false;
        segs.forEach(function (b) { if (autoAnswers.indexOf(b.textContent) > -1 && !b.classList.contains('on')) { b.click(); clicked = true; } });
        var fi = m.querySelector('.fieldin');
        if (fi && !clicked) {
          if (autoDed === 'unsure') { segs.forEach(function (b) { if (b.textContent === 'Not sure') b.click(); }); }
          else { fi.value = autoDed || '1000'; segs.forEach(function (b) { if (b.textContent === 'Continue') b.click(); }); }
        }
      }, 900);
      return m;
    };
    setTimeout(function () { input.value = params.get('q'); submit(); }, 400);
  }
})();
