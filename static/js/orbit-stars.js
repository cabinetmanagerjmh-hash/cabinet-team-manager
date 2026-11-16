/* JMH Orbit — poussière d'azur.
   Champ de particules façon NVIDIA NIM : points fins qui dérivent lentement et
   scintillent, plus quelques nébulosités très diffuses. Canvas 2D unique,
   derrière le contenu. Figé si prefers-reduced-motion, en pause si l'onglet est
   masqué ; aucun repaint de layout (le canvas ne participe pas au flux). */
(function () {
  var canvas = document.getElementById('orbitStars');
  if (!canvas || !canvas.getContext) { return; }
  var ctx = canvas.getContext('2d');
  if (!ctx) { return; }

  var PALETTES = {
    /* Clair : poussiere quasi invisible (elle lisait comme de la salete sur le
       canevas blanc) — on garde seulement 2-3 nebuloses azur tres douces. */
    light: { dot: '27,85,112', halo: '78,155,187', min: .04, max: .12, dust: .04, haloScale: .12, density: .4, spd: .5 },
    /* Sombre : derivation et scintillement franchement perceptibles. */
    dark: { dot: '166,219,241', halo: '124,197,227', min: .16, max: .78, dust: .085, haloScale: .45, density: 1, spd: 1 }
  };

  var reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  var dpr = 1, w = 0, h = 0, stars = [], motes = [], raf = 0, last = 0, running = false;
  var dotSprite = null, haloSprite = null, dustSprite = null;

  function palette() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? PALETTES.light : PALETTES.dark;
  }

  function sprite(rgb, size, stops) {
    var c = document.createElement('canvas');
    c.width = c.height = size;
    var g = c.getContext('2d');
    var gr = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    for (var i = 0; i < stops.length; i++) { gr.addColorStop(stops[i][0], 'rgba(' + rgb + ',' + stops[i][1] + ')'); }
    g.fillStyle = gr;
    g.beginPath(); g.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2); g.fill();
    return c;
  }

  function buildSprites() {
    var p = palette();
    dotSprite = sprite(p.dot, 24, [[0, 1], [0.42, 0.95], [1, 0]]);
    haloSprite = sprite(p.halo, 128, [[0, 0.6], [0.45, 0.18], [1, 0]]);
    dustSprite = sprite(p.halo, 256, [[0, 0.30], [0.5, 0.10], [1, 0]]);
  }

  function seed() {
    var p = palette();
    var count = Math.max(26, Math.min(150, Math.round((w * h) / 16000 * p.density)));
    stars = [];
    for (var i = 0; i < count; i++) {
      stars.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 0.5 + Math.random() * 1.2,
        dx: (Math.random() - 0.5) * 0.26 * p.spd,
        dy: (-0.06 - Math.random() * 0.22) * p.spd,
        a: p.min + Math.random() * (p.max - p.min),
        tw: Math.random() * Math.PI * 2,
        ts: 0.008 + Math.random() * 0.016
      });
    }
    motes = [];
    for (var j = 0; j < 3; j++) {
      motes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 110 + Math.random() * 190,
        dx: (Math.random() - 0.5) * 0.12 * p.spd,
        dy: (Math.random() - 0.5) * 0.09 * p.spd,
        a: p.dust
      });
    }
  }

  function draw(dt) {
    var p = palette();
    var step = dt / 16.7;
    ctx.clearRect(0, 0, w, h);

    for (var m = 0; m < motes.length; m++) {
      var mo = motes[m];
      mo.x += mo.dx * step; mo.y += mo.dy * step;
      if (mo.x < -mo.r) { mo.x = w + mo.r; } else if (mo.x > w + mo.r) { mo.x = -mo.r; }
      if (mo.y < -mo.r) { mo.y = h + mo.r; } else if (mo.y > h + mo.r) { mo.y = -mo.r; }
      ctx.globalAlpha = mo.a;
      ctx.drawImage(dustSprite, mo.x - mo.r, mo.y - mo.r, mo.r * 2, mo.r * 2);
    }

    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      s.x += s.dx * step; s.y += s.dy * step;
      if (s.x < -4) { s.x = w + 4; } else if (s.x > w + 4) { s.x = -4; }
      if (s.y < -4) { s.y = h + 4; } else if (s.y > h + 4) { s.y = -4; }
      s.tw += s.ts * step;
      var a = s.a * (0.55 + 0.45 * Math.sin(s.tw));
      if (a <= 0.01) { continue; }
      var size = s.r * 2.2;
      ctx.globalAlpha = a * p.haloScale;
      ctx.drawImage(haloSprite, s.x - size * 3.5, s.y - size * 3.5, size * 7, size * 7);
      ctx.globalAlpha = a;
      ctx.drawImage(dotSprite, s.x - size, s.y - size, size * 2, size * 2);
    }
    ctx.globalAlpha = 1;
  }

  function frame(now) {
    if (!running) { return; }
    raf = window.requestAnimationFrame(frame);
    var dt = last ? Math.min(64, now - last) : 16.7;
    last = now;
    draw(dt);
  }

  function start() {
    if (running || (reduced && reduced.matches)) { return; }
    running = true; last = 0;
    raf = window.requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    if (raf) { window.cancelAnimationFrame(raf); raf = 0; }
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seed();
    draw(16.7);
  }

  buildSprites();
  resize();

  if (reduced && reduced.matches) {
    if (reduced.addEventListener) { reduced.addEventListener('change', function (e) { if (e.matches) { stop(); draw(16.7); } else { start(); } }); }
  } else {
    start();
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); } else { start(); }
  });

  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resize, 140);
  });

  window.addEventListener('orbit:theme', function () {
    buildSprites();
    seed();
    draw(16.7);
  });
})();
