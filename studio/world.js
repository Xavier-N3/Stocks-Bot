/* The Studio world: a cozy top-down office drawn entirely in code.
   Bots walk between rooms (A* pathfinding), work at their desks when their
   real agent is busy, take breaks, chat with each other, and react to real
   events (briefs handed over, trades, debates, new evolved brains, reports). */
(() => {
  "use strict";
  const T = 32, W = 60, H = 38, PW = W * T, PH = H * T, S = 2;
  const FONT = '"Nunito", system-ui, sans-serif';
  const solid = Array.from({ length: H }, () => new Uint8Array(W));
  const rnd = mulberry(7);
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  // ------------------------------------------------------------------ colors
  const C = {
    wall: "#3b2f28", wallTop: "#5a473b", wallEdge: "#2a211c", trim: "#6e5747",
    oak: ["#b98a5e", "#b2845a", "#c09166", "#ab7f55"], walnut: ["#7c5a43", "#74533e", "#83604a", "#6d4e3a"],
    honey: ["#c99a63", "#c29360", "#d1a26b", "#bd8f5c"],
    tileA: "#e9dcc5", tileB: "#d8c3a2", carpetLab: "#30474b", carpetSage: "#6f7f5f", carpetLib: "#6b3f36",
    wood: "#8b5e3c", woodTop: "#a6744d", woodDark: "#5e3f29", stone: "#cfc6b8", steel: "#9aa3a8",
    leaf: ["#5f8f4e", "#6fa35a", "#4f7d42", "#7cb064"], pot: "#c0724a", cream: "#f3e6cf",
    sofa: "#a65d4b", sofaDark: "#86483a", sofa2: "#5e7f8c", rugA: "#8e3f3a", rugB: "#d9a45b",
    screen: "#0f1a22", day: "#3987e5", swing: "#d95926", long: "#199e70", forex: "#22a6c7",
  };

  // ------------------------------------------------------------------ layout
  const rooms = [
    { name: "The Lab", x0: 1, y0: 1, x1: 18, y1: 14, floor: "carpetLab" },
    { name: "Trading Floor", x0: 20, y0: 1, x1: 42, y1: 14, floor: "oak" },
    { name: "Manager's Office", x0: 44, y0: 1, x1: 58, y1: 14, floor: "walnut" },
    { name: "Kitchen", x0: 1, y0: 16, x1: 16, y1: 36, floor: "tiles" },
    { name: "Living Room", x0: 18, y0: 16, x1: 40, y1: 36, floor: "honey" },
    { name: "Debate Room", x0: 42, y0: 16, x1: 58, y1: 25, floor: "carpetSage" },
    { name: "Library", x0: 42, y0: 27, x1: 58, y1: 36, floor: "carpetLib" },
  ];
  const walls = [];
  function wall(x0, y0, x1, y1, doors = []) {
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const k = x0 === x1 ? y : x;
      if (doors.includes(k)) continue;
      solid[y][x] = 1; walls.push([x, y]);
    }
  }
  wall(0, 0, W - 1, 0); wall(0, H - 1, W - 1, H - 1); wall(0, 0, 0, H - 1); wall(W - 1, 0, W - 1, H - 1);
  wall(1, 15, W - 2, 15, [8, 9, 10, 30, 31, 32, 50, 51, 52]);
  wall(19, 1, 19, 14, [6, 7, 8]); wall(43, 1, 43, 14, [6, 7, 8]);
  wall(17, 16, 17, H - 2, [24, 25, 26, 27]); wall(41, 16, 41, H - 2, [20, 21, 22, 31, 32, 33]);
  wall(42, 26, W - 2, 26, [49, 50, 51]);
  const doorTiles = [[8, 15], [9, 15], [10, 15], [30, 15], [31, 15], [32, 15], [50, 15], [51, 15], [52, 15], [19, 6], [19, 7], [19, 8],
    [43, 6], [43, 7], [43, 8], [17, 24], [17, 25], [17, 26], [17, 27], [41, 20], [41, 21], [41, 22], [41, 31], [41, 32], [41, 33],
    [49, 26], [50, 26], [51, 26]];
  const windows = [[3, 6, "top"], [12, 15, "top"], [23, 26, "top"], [38, 41, "top"], [47, 50, "top"], [54, 57, "top"],
    [4, 7, "bottom"], [22, 26, "bottom"], [32, 36, "bottom"], [46, 50, "bottom"], [53, 56, "bottom"]];

  const items = [];        // static furniture: {kind, x, y, w, h, ...} in tile units
  const screens = [];      // animated screens
  const lights = [];       // {x, y, r, color, kind}
  function block(x, y, w, h) { for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (solid[j]) solid[j][i] = 1; }
  function add(kind, x, y, w, h, o = {}, blocks = true) { items.push({ kind, x, y, w, h, ...o }); if (blocks) block(x, y, w, h); }

  // trading floor
  add("marketboard", 25, 1, 13, 1, {}, true);
  const pods = [{ id: "day", x: 22, label: "DAY DESK" }, { id: "swing", x: 29, label: "SWING DESK" }, { id: "long", x: 36, label: "LONG-TERM DESK" }];
  for (const p of pods) {
    add("rug", p.x - 1, 3.6, 6, 6.6, { color: C[p.id], label: p.label }, false);
    add("desk", p.x, 6, 4, 2, { pod: p.id });
    screens.push({ x: p.x + 0.35, y: 6.05, w: 1.3, h: 0.5, kind: "chart", color: C[p.id] });
    screens.push({ x: p.x + 2.35, y: 7.45, w: 1.3, h: 0.5, kind: "chart", color: C[p.id] });
    screens.push({ x: p.x + 1.8, y: 6.05, w: 0.9, h: 0.5, kind: "news", color: C[p.id] });
    lights.push({ x: p.x + 2, y: 7, r: 3.2, color: "120,170,255", kind: "screen" });
  }
  add("plant", 20, 1, 1, 1); add("plant", 42, 13, 1, 1); add("plant", 20, 13, 1, 1);
  add("cooler", 41, 3, 1, 1); add("sidebench", 38, 12, 3, 1);

  // lab
  add("rug", 12.4, 7.3, 5.8, 3.4, { color: "#e66767", label: "CRYPTO · 24/7" }, false);
  add("rug", 2.4, 7.3, 5.8, 3.4, { color: C.forex, label: "FOREX · 24/5" }, false);
  for (const [x, y] of [[3, 4], [8, 4], [13, 4], [3, 8], [13, 8]]) {
    const crypto = x === 13 && y === 8, forex = x === 3 && y === 8;   // the two 24-hour desks sit in the lab's corners
    const col = crypto ? "#e66767" : forex ? C.forex : "#c98500", trade = crypto || forex;
    add("desk", x, y, 4, 1, { lab: !trade });
    screens.push({ x: x + 0.3, y: y + 0.08, w: 1.5, h: 0.45, kind: trade ? "chart" : "code", color: col });
    screens.push({ x: x + 2.1, y: y + 0.08, w: 1.5, h: 0.45, kind: trade ? "chart" : x === 8 ? "evo" : "code", color: col });
    lights.push({ x: x + 2, y: y + 0.6, r: 2.6, color: "255,200,120", kind: "screen" });
  }
  add("rack", 1, 10, 2, 4); add("whiteboard", 7, 1, 6, 1, { text: "neural" });
  add("beanbag", 9, 11, 1, 1, { color: "#b3743c" }, false); add("rug", 6, 10, 7, 3.4, { color: "#c98500", label: "" }, false);
  add("plant", 17, 1, 1, 1); add("plant", 17, 13, 1, 1); add("plant", 1, 1, 1, 1);

  // office
  add("rug", 46, 3, 10, 7, { color: "#9085e9", label: "" }, false);
  add("desk", 48, 5, 6, 2, { office: true });
  screens.push({ x: 49.6, y: 5.1, w: 1.6, h: 0.55, kind: "chart", color: "#9085e9" });
  screens.push({ x: 51.5, y: 5.1, w: 1.2, h: 0.55, kind: "report", color: "#9085e9" });
  add("chair", 49, 8, 1, 1, { face: "n", color: "#6e4f8a" }, false); add("chair", 52, 8, 1, 1, { face: "n", color: "#6e4f8a" }, false);
  add("bookshelf", 57, 2, 1, 8, { vertical: true }); add("armchair", 45, 11, 1, 1, { color: C.sofa2, face: "e" }, false);
  add("sidetable", 46, 12, 1, 1); add("plant", 44, 1, 1, 1); add("plant", 57, 13, 1, 1); add("plant", 44, 13, 1, 1);
  add("lamp", 55, 12, 1, 1); lights.push({ x: 55.5, y: 12.5, r: 4, color: "255,190,110", kind: "lamp" });

  // kitchen
  add("counter", 1, 16, 7, 1, { parts: { 3: "stove", 4: "stove", 6: "sink", 7: "sink" } });   // gap at x 8-10 = door to the lab
  add("counter", 11, 16, 3, 1, { parts: { 12: "coffee" } });
  add("fridge", 14, 16, 2, 2); add("island", 5, 21, 6, 2);
  for (const x of [5, 7, 9, 10]) add("stool", x, 23, 1, 1, {}, false);
  add("table", 5, 29, 6, 3, { fruit: true });
  for (const [x, y, f] of [[6, 28, "s"], [9, 28, "s"], [6, 32, "n"], [9, 32, "n"], [4, 30, "e"], [11, 30, "w"]])
    add("chair", x, y, 1, 1, { face: f, color: C.woodDark }, false);
  add("plant", 1, 35, 1, 1); add("plant", 15, 35, 1, 1); add("shelf", 1, 20, 1, 5); add("clock", 8, 15.05, 1, 1, {}, false);
  lights.push({ x: 8, y: 22, r: 4.5, color: "255,200,130", kind: "pendant" });
  lights.push({ x: 8, y: 30.5, r: 5, color: "255,200,130", kind: "pendant" });

  // living room
  add("rug", 21, 21, 16, 12.5, { color: C.rugA, pattern: true, label: "" }, false);
  add("fireplace", 22, 16, 6, 1); add("bookshelf", 36, 16, 4, 1, {});
  add("sofa", 22, 31, 10, 2, { back: "s" }); add("sofa", 19, 24, 2, 8, { back: "w" });
  add("coffeetable", 24, 27, 6, 2); add("armchair", 34, 22, 1, 1, { color: C.sofa2, face: "w" }, false);
  add("armchair", 34, 25, 1, 1, { color: C.sofa2, face: "w" }, false);
  add("tv", 40, 25, 1, 5); add("lamp", 18, 17, 1, 1); add("plant", 39, 35, 1, 1); add("plant", 18, 35, 1, 1);
  add("plant", 34, 16, 1, 1); add("petbed", 29, 18, 1, 1, {}, false);
  lights.push({ x: 18.5, y: 17.5, r: 5, color: "255,190,110", kind: "lamp" });
  lights.push({ x: 25, y: 17.4, r: 7, color: "255,140,60", kind: "fire" });
  lights.push({ x: 39.5, y: 27.5, r: 3, color: "140,180,255", kind: "screen" });

  // debate room
  add("whiteboard", 43, 16, 6, 1, { text: "proscons" }); add("roundtable", 48, 19, 4, 3);
  for (const [x, y, f] of [[49, 18, "s"], [50, 18, "s"], [49, 22, "n"], [50, 22, "n"], [47, 20, "e"], [52, 20, "w"]])
    add("chair", x, y, 1, 1, { face: f, color: "#8a4d6a" }, false);
  add("plant", 42, 16, 1, 1); add("plant", 57, 24, 1, 1);
  lights.push({ x: 50, y: 20.5, r: 4.5, color: "255,200,140", kind: "pendant" });

  // library
  add("bookshelf", 43, 27, 6, 1, {}); add("bookshelf", 52, 27, 6, 1, {}); add("bookshelf", 58, 28, 1, 8, { vertical: true });
  add("rug", 44, 29, 12, 6.5, { color: "#c98f4a", pattern: true, label: "" }, false);
  add("beanbag", 45, 31, 1, 1, { color: "#d55181" }, false); add("beanbag", 48, 33, 1, 1, { color: "#3987e5" }, false);
  add("armchair", 54, 32, 1, 1, { color: C.sofa, face: "w" }, false); add("lamp", 55, 30, 1, 1); add("plant", 42, 36, 1, 1);
  lights.push({ x: 55.5, y: 30.5, r: 4.5, color: "255,190,110", kind: "lamp" });

  // ------------------------------------------------------------------ points of interest
  const P = (x, y, face, sit = true, tag = "") => ({ x, y, face, sit, tag, key: `${x},${y}` });
  const HOME = {
    day_researcher: P(23, 5, "s"), day_trader: P(24, 8, "n"),
    swing_researcher: P(30, 5, "s"), swing_trader: P(31, 8, "n"),
    long_researcher: P(37, 5, "s"), long_trader: P(38, 8, "n"),
    manager: P(50, 4, "s"), lab: P(9, 5, "n"), challenger: P(52, 20, "w"),
    crypto_researcher: P(14, 9, "n"), crypto_trader: P(15, 9, "n"),
    forex_researcher: P(4, 9, "n"), forex_trader: P(5, 9, "n"),
  };
  // the desk monitors face the sitter: researchers sit north of their pod facing south, etc.
  const HANDOFF = { day: P(22, 9, "e", false), swing: P(29, 9, "e", false), long: P(36, 9, "e", false),
    crypto: P(16, 10, "w", false), forex: P(7, 9, "w", false) };
  const POI = {
    coffee: [P(12, 17, "n", false, "coffee")], fridge: [P(14, 18, "n", false)],
    stools: [P(5, 23, "n"), P(7, 23, "n"), P(9, 23, "n"), P(10, 23, "n")],
    dining: [P(6, 28, "s"), P(9, 28, "s"), P(6, 32, "n"), P(9, 32, "n"), P(4, 30, "e"), P(11, 30, "w")],
    sofa: [P(23, 31, "n"), P(25, 31, "n"), P(27, 31, "n"), P(29, 31, "n"), P(31, 31, "n"), P(20, 26, "e"), P(20, 28, "e"), P(20, 30, "e")],
    armchair: [P(34, 22, "w"), P(34, 25, "w"), P(45, 11, "e"), P(54, 32, "w")],
    fire: [P(24, 18, "n", false), P(26, 18, "n", false)],
    window: [P(24, 35, "s", false), P(34, 35, "s", false), P(5, 35, "s", false), P(48, 35, "s", false), P(25, 2, "n", false)],
    library: [P(45, 31, "n"), P(48, 33, "n"), P(46, 28, "n", false), P(52, 28, "n", false), P(57, 31, "e", false)],
    board: [P(30, 3, "n", false), P(32, 3, "n", false), P(28, 3, "n", false), P(34, 3, "n", false)],
    cooler: [P(40, 3, "e", false)],
    rack: [P(3, 11, "w", false), P(3, 12, "w", false)],
    labboard: [P(10, 2, "n", false), P(8, 2, "n", false)],
    labdesks: [P(4, 5, "n"), P(14, 5, "n")],
    officevisit: [P(49, 8, "n"), P(52, 8, "n")], officeshelf: [P(56, 5, "e", false)],
    debatewb: [P(45, 17, "n", false), P(47, 17, "n", false)],
    debatevisit: [P(47, 20, "e"), P(49, 18, "s"), P(50, 22, "n")],
    podwalk: [P(25, 11, "n", false), P(32, 11, "n", false), P(39, 11, "n", false)],
  };
  const MEET = [[P(6, 28, "s"), P(6, 32, "n")], [P(9, 28, "s"), P(9, 32, "n")], [P(25, 31, "n"), P(27, 31, "n")],
    [P(24, 18, "n", false), P(26, 18, "n", false)], [P(7, 23, "n"), P(9, 23, "n")], [P(45, 31, "n"), P(48, 33, "n")],
    [P(36, 20, "w", false), P(35, 20, "e", false)], [P(27, 12, "e", false), P(28, 12, "w", false)]];
  const ROLE_WORK = {
    researcher: ["library", "board", "library"], trader: ["board", "cooler", "board"], lab: ["rack", "labboard", "labdesks"],
    manager: ["podwalk", "officeshelf", "board"], challenger: ["debatewb", "library", "board"],
  };
  const SMALLTALK = [
    ["Coffee's fresh ☕", "Bless you."], ["Markets feel quiet today.", "Quiet is fine by me."],
    ["Did you see that volume spike?", "Saw it. Waiting for confirmation."], ["How's your win rate?", "Ask me after 100 trades 😄"],
    ["Darwin bred new brains again.", "Only promotes the ones that pass unseen data."],
    ["Stops are set, right?", "Always. Bracket orders on everything."], ["Nice fire tonight 🔥", "Cozy. Prices can wait."],
    ["What's the S&P doing?", "Check the big board."], ["Devi argued with me again.", "Good. That's her job."],
    ["Reading any good filings?", "Margins look healthy on a few."], ["Thinking about the Fed…", "Aren't we all."],
  ];
  const CHAIR_COL = { day_researcher: "#2f5f9e", day_trader: "#2f5f9e", swing_researcher: "#9e4a26", swing_trader: "#9e4a26",
    long_researcher: "#1f6e52", long_trader: "#1f6e52", manager: "#5b4a8a", lab: "#7a5a1a",
    crypto_researcher: "#8a3a3a", crypto_trader: "#8a3a3a", forex_researcher: "#1d6f82", forex_trader: "#1d6f82" };
  for (const [id, p] of Object.entries(HOME)) {
    solid[p.y][p.x] = 0;
    if (CHAIR_COL[id]) items.push({ kind: "chair", x: p.x, y: p.y, w: 1, h: 1, face: p.face, color: CHAIR_COL[id] });
  }

  // ------------------------------------------------------------------ pathfinding (A*, 8 directions)
  function astar(sx, sy, tx, ty) {
    if (solid[ty]?.[tx]) return null;
    const key = (x, y) => y * W + x, open = new Map(), came = new Map(), g = new Map();
    const h = (x, y) => Math.max(Math.abs(x - tx), Math.abs(y - ty)) + 0.4 * Math.min(Math.abs(x - tx), Math.abs(y - ty));
    g.set(key(sx, sy), 0); open.set(key(sx, sy), h(sx, sy));
    let guard = 0;
    while (open.size && guard++ < 6000) {
      let bk, bf = Infinity;
      for (const [k, f] of open) if (f < bf) { bf = f; bk = k; }
      open.delete(bk);
      const cx = bk % W, cy = (bk / W) | 0;
      if (cx === tx && cy === ty) {
        const path = [[tx, ty]]; let k = bk;
        while (came.has(k)) { k = came.get(k); path.push([k % W, (k / W) | 0]); }
        return path.reverse();
      }
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
        const nx = cx + dx, ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= W || ny >= H || solid[ny][nx]) continue;
        if (dx && dy && (solid[cy][nx] || solid[ny][cx])) continue;
        const ng = g.get(bk) + (dx && dy ? 1.414 : 1), nk = key(nx, ny);
        if (ng < (g.get(nk) ?? Infinity)) { g.set(nk, ng); came.set(nk, bk); open.set(nk, ng + h(nx, ny)); }
      }
    }
    return null;
  }

  // ------------------------------------------------------------------ drawing helpers
  function rr(c, x, y, w, h, r) { c.beginPath(); c.roundRect(x, y, w, h, r); }
  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16); let r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    const f = (v) => Math.max(0, Math.min(255, Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt)));
    return `rgb(${f(r)},${f(g)},${f(b)})`;
  }
  function shadow(c, x, y, w, h, r = 6) { c.fillStyle = "rgba(20,10,5,.28)"; rr(c, x + 3, y + 5, w, h, r); c.fill(); }

  // ------------------------------------------------------------------ static layer
  const stat = document.createElement("canvas");
  stat.width = PW * S; stat.height = PH * S;
  function drawStatic() {
    const c = stat.getContext("2d");
    c.setTransform(S, 0, 0, S, 0, 0);
    c.fillStyle = "#1c1714"; c.fillRect(0, 0, PW, PH);
    const r2 = mulberry(11);
    const floorRect = (rm, x0, y0, x1, y1) => {
      const X = x0 * T, Y = y0 * T, w = (x1 - x0 + 1) * T, h = (y1 - y0 + 1) * T;
      if (rm.floor === "tiles") {
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
          c.fillStyle = (x + y) % 2 ? C.tileA : C.tileB; c.fillRect(x * T, y * T, T, T);
          c.strokeStyle = "rgba(120,90,60,.18)"; c.strokeRect(x * T + .5, y * T + .5, T - 1, T - 1);
        }
      } else if (rm.floor.startsWith("carpet")) {
        c.fillStyle = C[rm.floor]; c.fillRect(X, Y, w, h);
        for (let i = 0; i < w * h / 90; i++) { c.fillStyle = `rgba(255,255,255,${r2() * .035})`; c.fillRect(X + r2() * w, Y + r2() * h, 2, 2); }
      } else {
        const tones = C[rm.floor], ph = 10;
        for (let yy = Y; yy < Y + h; yy += ph) {
          let xx = X - r2() * 90;
          while (xx < X + w) {
            const len = 60 + r2() * 90;
            c.fillStyle = tones[Math.floor(r2() * tones.length)];
            c.fillRect(Math.max(xx, X), yy, Math.min(len, X + w - Math.max(xx, X)), ph);
            c.fillStyle = "rgba(60,35,15,.25)"; c.fillRect(Math.max(xx, X), yy + ph - 1, Math.min(len, X + w - Math.max(xx, X)), 1);
            if (xx + len < X + w) c.fillRect(xx + len, yy, 1, ph);
            xx += len;
          }
        }
      }
    };
    for (const rm of rooms) floorRect(rm, rm.x0, rm.y0, rm.x1, rm.y1);
    for (const [x, y] of doorTiles) {
      const rm = rooms.find((r) => x >= r.x0 - 1 && x <= r.x1 + 1 && y >= r.y0 - 1 && y <= r.y1 + 1) || rooms[1];
      floorRect(rm.floor === "tiles" ? rooms[4] : rm, x, y, x, y);
      c.fillStyle = "rgba(70,45,25,.35)"; c.fillRect(x * T, y * T, T, T);
    }
    // rugs
    for (const it of items.filter((i) => i.kind === "rug")) {
      const X = it.x * T, Y = it.y * T, w = it.w * T, h = it.h * T;
      c.globalAlpha = it.pattern ? 1 : .22; c.fillStyle = it.color; rr(c, X, Y, w, h, 10); c.fill(); c.globalAlpha = 1;
      if (it.pattern) {
        c.strokeStyle = C.rugB; c.lineWidth = 4; rr(c, X + 10, Y + 10, w - 20, h - 20, 6); c.stroke();
        c.strokeStyle = "rgba(255,230,190,.35)"; c.lineWidth = 2; rr(c, X + 20, Y + 20, w - 40, h - 40, 4); c.stroke();
        c.fillStyle = "rgba(255,220,160,.18)";
        for (let i = 0; i < 4; i++) { c.beginPath(); c.arc(X + w / 2, Y + h / 2, 18 + i * 22, 0, 7); c.lineWidth = 2; c.strokeStyle = "rgba(255,220,160,.14)"; c.stroke(); }
      } else {
        c.strokeStyle = it.color; c.globalAlpha = .55; c.lineWidth = 2; c.setLineDash([6, 5]); rr(c, X + 4, Y + 4, w - 8, h - 8, 8); c.stroke();
        c.setLineDash([]); c.globalAlpha = 1;
      }
      if (it.label) {
        c.font = `800 11px ${FONT}`; c.fillStyle = shade(it.color, .45); c.textAlign = "center";
        c.fillText(it.label, X + w / 2, Y + h - 8); c.textAlign = "left";
      }
    }
    // furniture
    for (const it of items) if (it.kind !== "rug") drawItem(c, it);
    // walls (drawn after furniture so wall-mounted things sit on top cleanly)
    for (const [x, y] of walls) {
      c.fillStyle = C.wall; c.fillRect(x * T, y * T, T, T);
      c.fillStyle = C.wallTop; c.fillRect(x * T, y * T, T, 6);
      c.fillStyle = "rgba(0,0,0,.25)"; c.fillRect(x * T, y * T + T - 3, T, 3);
    }
    // windows
    for (const [a, b, side] of windows) {
      const y = side === "top" ? 0 : (H - 1) * T;
      c.fillStyle = "#6f8fa8"; c.fillRect(a * T + 4, y + 9, (b - a + 1) * T - 8, 14);
      c.fillStyle = "rgba(255,255,255,.35)"; c.fillRect(a * T + 6, y + 11, (b - a + 1) * T - 12, 3);
      c.fillStyle = C.trim; for (let x = a; x <= b + 1; x++) c.fillRect(x * T - 1, y + 8, 3, 16);
    }
    // room plaques
    c.font = `800 12px ${FONT}`;
    for (const rm of rooms) {
      const txt = rm.name.toUpperCase(), w = c.measureText(txt).width + 18;
      const x = rm.name === "Trading Floor" ? (rm.x0 + 1) * T : rm.x0 * T + 8, y = rm.y1 * T + 6;
      c.fillStyle = "rgba(28,23,20,.55)"; rr(c, x, y, w, 20, 10); c.fill();
      c.fillStyle = "#f4ead9"; c.fillText(txt, x + 9, y + 14);
    }
  }

  function drawItem(c, it) {
    const X = it.x * T, Y = it.y * T, w = it.w * T, h = it.h * T;
    switch (it.kind) {
      case "desk": {
        shadow(c, X, Y, w, h); c.fillStyle = it.lab ? "#3d3a36" : C.woodTop; rr(c, X, Y, w, h, 5); c.fill();
        c.strokeStyle = it.lab ? "#2a2826" : C.woodDark; c.lineWidth = 2; c.stroke();
        c.fillStyle = "rgba(255,255,255,.08)"; c.fillRect(X + 4, Y + 3, w - 8, 3);
        if (it.pod) { // keyboards + mugs
          c.fillStyle = "#2b2622"; rr(c, X + 18, Y + 22, 26, 7, 2); c.fill(); rr(c, X + 84, Y + 36, 26, 7, 2); c.fill();
          c.fillStyle = "#f3e6cf"; c.beginPath(); c.arc(X + 108, Y + 16, 4, 0, 7); c.fill();
          c.fillStyle = "#6b3f1f"; c.beginPath(); c.arc(X + 108, Y + 16, 2.5, 0, 7); c.fill();
        }
        if (it.office) {
          c.fillStyle = "#e9dcc5"; c.save(); c.translate(X + 30, Y + 30); c.rotate(-.2); c.fillRect(0, 0, 18, 24); c.restore();
          c.fillStyle = "#3a6b4f"; c.beginPath(); c.arc(X + w - 20, Y + 18, 7, 0, 7); c.fill();
        }
        break;
      }
      case "chair": {
        const col = it.color || "#5b4636";
        c.fillStyle = "rgba(20,10,5,.25)"; c.beginPath(); c.arc(X + 18, Y + 20, 11, 0, 7); c.fill();
        c.fillStyle = col; rr(c, X + 6, Y + 6, 20, 20, 6); c.fill();
        c.fillStyle = shade(col, -.3);
        const f = it.face;
        if (f === "n") rr(c, X + 5, Y + 24, 22, 5, 2); else if (f === "s") rr(c, X + 5, Y + 3, 22, 5, 2);
        else if (f === "e") rr(c, X + 3, Y + 5, 5, 22, 2); else rr(c, X + 24, Y + 5, 5, 22, 2);
        c.fill(); break;
      }
      case "plant": {
        c.fillStyle = "rgba(20,10,5,.28)"; c.beginPath(); c.arc(X + 19, Y + 20, 13, 0, 7); c.fill();
        c.fillStyle = C.pot; c.beginPath(); c.arc(X + 16, Y + 16, 10, 0, 7); c.fill();
        c.fillStyle = shade(C.pot, -.25); c.beginPath(); c.arc(X + 16, Y + 16, 7, 0, 7); c.fill();
        const r = mulberry(Math.round(X * 7 + Y));
        for (let i = 0; i < 9; i++) {
          const a = r() * 6.28, d = 4 + r() * 8;
          c.fillStyle = C.leaf[Math.floor(r() * 4)];
          c.save(); c.translate(X + 16 + Math.cos(a) * d, Y + 16 + Math.sin(a) * d); c.rotate(a);
          c.beginPath(); c.ellipse(0, 0, 9, 4.5, 0, 0, 7); c.fill(); c.restore();
        }
        break;
      }
      case "marketboard": {
        c.fillStyle = "#1a1512"; rr(c, X - 4, Y - 14, w + 8, h + 16, 6); c.fill();
        c.fillStyle = C.screen; rr(c, X, Y - 10, w, h + 8, 4); c.fill(); break;
      }
      case "cooler": {
        shadow(c, X + 4, Y + 4, 24, 24, 12); c.fillStyle = "#dfe7ea"; rr(c, X + 4, Y + 4, 24, 24, 6); c.fill();
        c.fillStyle = "#7fb2d8"; c.beginPath(); c.arc(X + 16, Y + 16, 8, 0, 7); c.fill();
        c.fillStyle = "rgba(255,255,255,.6)"; c.beginPath(); c.arc(X + 13, Y + 13, 3, 0, 7); c.fill(); break;
      }
      case "sidebench": case "sidetable": {
        shadow(c, X + 2, Y + 4, w - 4, h - 8); c.fillStyle = C.wood; rr(c, X + 2, Y + 4, w - 4, h - 8, 4); c.fill();
        if (it.kind === "sidebench") { c.fillStyle = "#c9b48f"; for (let i = 0; i < 3; i++) { rr(c, X + 10 + i * 30, Y + 8, 18, 14, 3); c.fill(); } }
        else { c.fillStyle = "#f3e6cf"; c.beginPath(); c.arc(X + 16, Y + 16, 4, 0, 7); c.fill(); }
        break;
      }
      case "rack": {
        shadow(c, X, Y, w, h, 3);
        c.fillStyle = "#23272b"; rr(c, X + 2, Y, w - 4, h, 4); c.fill();
        c.strokeStyle = "#3a4046"; c.lineWidth = 1;
        for (let i = 0; i < 10; i++) { c.strokeRect(X + 6, Y + 6 + i * 12, w - 12, 9); }
        break;
      }
      case "whiteboard": {
        c.fillStyle = C.trim; rr(c, X - 3, Y - 12, w + 6, h + 10, 4); c.fill();
        c.fillStyle = "#f4f1ea"; rr(c, X, Y - 9, w, h + 4, 3); c.fill();
        c.lineWidth = 1.5;
        if (it.text === "neural") {
          const layers = [3, 4, 4, 1], xs = layers.map((_, i) => X + 24 + i * ((w - 48) / 3));
          const ys = (n) => Array.from({ length: n }, (_, j) => Y - 4 + (j + 1) * (h + 2) / (n + 1));
          c.strokeStyle = "rgba(57,135,229,.5)";
          for (let i = 0; i < 3; i++) for (const a of ys(layers[i])) for (const b of ys(layers[i + 1])) { c.beginPath(); c.moveTo(xs[i], a); c.lineTo(xs[i + 1], b); c.stroke(); }
          for (let i = 0; i < 4; i++) for (const a of ys(layers[i])) { c.fillStyle = i === 3 ? "#d95926" : "#3987e5"; c.beginPath(); c.arc(xs[i], a, 2.6, 0, 7); c.fill(); }
        } else {
          c.font = `800 10px ${FONT}`; c.textAlign = "center"; c.fillStyle = "#0c7a3c"; c.fillText("PROS", X + w / 4, Y - 1);
          c.fillStyle = "#b03535"; c.fillText("CONS", X + 3 * w / 4, Y - 1); c.textAlign = "left";
          c.strokeStyle = "#555"; c.beginPath(); c.moveTo(X + w / 2, Y - 6); c.lineTo(X + w / 2, Y + h - 6); c.stroke();
          c.strokeStyle = "rgba(60,60,60,.45)";
          for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) { c.beginPath(); c.moveTo(X + 12 + i * w / 2, Y + 5 + j * 6); c.lineTo(X + w / 2 - 14 - j * 20 + i * w / 2, Y + 5 + j * 6); c.stroke(); }
        }
        break;
      }
      case "beanbag": {
        c.fillStyle = "rgba(20,10,5,.25)"; c.beginPath(); c.ellipse(X + 19, Y + 20, 15, 13, 0, 0, 7); c.fill();
        c.fillStyle = it.color; c.beginPath(); c.ellipse(X + 16, Y + 16, 15, 13, .3, 0, 7); c.fill();
        c.fillStyle = shade(it.color, .2); c.beginPath(); c.ellipse(X + 13, Y + 12, 7, 5, .3, 0, 7); c.fill(); break;
      }
      case "bookshelf": {
        shadow(c, X, Y, w, h, 3); c.fillStyle = C.woodDark; rr(c, X, Y, w, h, 3); c.fill();
        const r = mulberry(Math.round(X + Y * 3)), cols = ["#a64b3c", "#3e6d8e", "#c99a3c", "#5e7f4e", "#7b5aa6", "#d9c6a3", "#2f4f4f"];
        if (it.vertical) { for (let yy = Y + 3; yy < Y + h - 4;) { const t = 3 + r() * 4; c.fillStyle = cols[Math.floor(r() * cols.length)]; c.fillRect(X + 4, yy, w - 8, t); yy += t + 1; } }
        else { for (let xx = X + 3; xx < X + w - 4;) { const t = 3 + r() * 4; c.fillStyle = cols[Math.floor(r() * cols.length)]; c.fillRect(xx, Y + 5, t, h - 10); xx += t + 1; } }
        break;
      }
      case "armchair": {
        const col = it.color; c.fillStyle = "rgba(20,10,5,.28)"; rr(c, X + 2, Y + 5, 32, 32, 9); c.fill();
        c.fillStyle = shade(col, -.25); rr(c, X - 2, Y - 2, 36, 36, 9); c.fill();
        c.fillStyle = col; rr(c, X + 4, Y + 4, 24, 24, 6); c.fill(); break;
      }
      case "lamp": {
        c.fillStyle = "rgba(20,10,5,.3)"; c.beginPath(); c.arc(X + 19, Y + 20, 10, 0, 7); c.fill();
        c.fillStyle = "#f2d39b"; c.beginPath(); c.arc(X + 16, Y + 16, 10, 0, 7); c.fill();
        c.strokeStyle = "#c39a5c"; c.lineWidth = 2; c.stroke();
        c.fillStyle = "#fff4d6"; c.beginPath(); c.arc(X + 16, Y + 16, 4, 0, 7); c.fill(); break;
      }
      case "counter": {
        c.fillStyle = "#b9ab98"; c.fillRect(X, Y, w, h); c.fillStyle = C.stone; c.fillRect(X, Y + 2, w, h - 6);
        c.fillStyle = "rgba(0,0,0,.18)"; c.fillRect(X, Y + h - 4, w, 4);
        for (const [k, kind] of Object.entries(it.parts || {})) {
          const px = X + (+k - it.x) * T;
          if (kind === "stove") { c.fillStyle = "#3a3a3a"; c.fillRect(px + 2, Y + 4, T - 4, T - 10); c.strokeStyle = "#777"; c.lineWidth = 2; c.beginPath(); c.arc(px + 16, Y + 15, 7, 0, 7); c.stroke(); }
          if (kind === "sink") { c.fillStyle = "#9aa7ad"; rr(c, px + 4, Y + 6, T - 8, T - 14, 4); c.fill(); c.fillStyle = "#c6d1d6"; rr(c, px + 7, Y + 9, T - 14, T - 20, 3); c.fill(); }
          if (kind === "coffee") { c.fillStyle = "#2e2b29"; rr(c, px + 6, Y + 3, 20, 20, 4); c.fill(); c.fillStyle = "#c0392b"; c.beginPath(); c.arc(px + 21, Y + 8, 2, 0, 7); c.fill(); c.fillStyle = "#f3e6cf"; c.beginPath(); c.arc(px + 14, Y + 17, 4, 0, 7); c.fill(); }
        }
        break;
      }
      case "fridge": {
        shadow(c, X + 2, Y, w - 4, h - 4, 4); c.fillStyle = "#dfe6e8"; rr(c, X + 2, Y, w - 4, h - 4, 5); c.fill();
        c.strokeStyle = "#b8c2c6"; c.lineWidth = 2; c.beginPath(); c.moveTo(X + 2, Y + h * .45); c.lineTo(X + w - 2, Y + h * .45); c.stroke();
        c.fillStyle = "#9aa3a8"; c.fillRect(X + w - 12, Y + 10, 3, 10); c.fillRect(X + w - 12, Y + h * .5, 3, 10);
        c.fillStyle = "#e8b45a"; c.beginPath(); c.arc(X + 16, Y + 16, 3, 0, 7); c.fill(); c.fillStyle = "#3987e5"; c.beginPath(); c.arc(X + 26, Y + 20, 3, 0, 7); c.fill();
        break;
      }
      case "island": case "table": case "coffeetable": case "roundtable": {
        shadow(c, X, Y, w, h, it.kind === "roundtable" ? h / 2 : 6);
        const col = it.kind === "island" ? C.stone : it.kind === "coffeetable" ? C.woodDark : C.woodTop;
        c.fillStyle = col;
        if (it.kind === "roundtable") { c.beginPath(); c.ellipse(X + w / 2, Y + h / 2, w / 2, h / 2, 0, 0, 7); c.fill(); }
        else { rr(c, X, Y, w, h, 8); c.fill(); }
        c.strokeStyle = shade(col.startsWith("#") ? col : "#8b5e3c", -.25); c.lineWidth = 2; c.stroke();
        if (it.fruit) {
          c.fillStyle = "#e9dcc5"; c.beginPath(); c.arc(X + w / 2, Y + h / 2, 13, 0, 7); c.fill();
          for (const [dx, dy, col2] of [[-5, -3, "#d0453a"], [5, -2, "#e8b45a"], [0, 5, "#7cb064"], [6, 6, "#d0453a"]]) { c.fillStyle = col2; c.beginPath(); c.arc(X + w / 2 + dx, Y + h / 2 + dy, 4.5, 0, 7); c.fill(); }
          for (const [dx, dy] of [[30, 20], [150, 60], [60, 70], [140, 18]]) { c.fillStyle = "#f3e6cf"; c.beginPath(); c.arc(X + dx, Y + dy, 9, 0, 7); c.fill(); c.strokeStyle = "#c9b48f"; c.stroke(); }
        }
        if (it.kind === "coffeetable") {
          c.fillStyle = "#3e6d8e"; rr(c, X + 20, Y + 18, 30, 22, 2); c.fill(); c.fillStyle = "#a64b3c"; rr(c, X + 24, Y + 22, 30, 22, 2); c.fill();
          c.fillStyle = "#f3e6cf"; c.beginPath(); c.arc(X + w - 40, Y + 30, 6, 0, 7); c.fill();
          c.fillStyle = "#6fa35a"; c.beginPath(); c.arc(X + w / 2 + 20, Y + 30, 8, 0, 7); c.fill();
        }
        if (it.kind === "roundtable") { c.fillStyle = "#e9dcc5"; for (const [dx, dy] of [[-30, -10], [20, 15], [-5, 18]]) { c.save(); c.translate(X + w / 2 + dx, Y + h / 2 + dy); c.rotate(dx / 50); c.fillRect(-8, -10, 16, 20); c.restore(); } }
        break;
      }
      case "stool": { c.fillStyle = "rgba(20,10,5,.25)"; c.beginPath(); c.arc(X + 18, Y + 19, 9, 0, 7); c.fill(); c.fillStyle = C.wood; c.beginPath(); c.arc(X + 16, Y + 16, 9, 0, 7); c.fill(); c.fillStyle = C.woodTop; c.beginPath(); c.arc(X + 16, Y + 16, 6, 0, 7); c.fill(); break; }
      case "shelf": { shadow(c, X, Y, w, h, 3); c.fillStyle = C.woodDark; rr(c, X + 2, Y, w - 6, h, 3); c.fill(); const jar = ["#e8b45a", "#c0724a", "#7cb064", "#f3e6cf"]; for (let i = 0; i < 6; i++) { c.fillStyle = jar[i % 4]; c.beginPath(); c.arc(X + 13, Y + 14 + i * 25, 7, 0, 7); c.fill(); } break; }
      case "sofa": {
        const col = C.sofa; shadow(c, X, Y, w, h, 12);
        c.fillStyle = C.sofaDark; rr(c, X, Y, w, h, 12); c.fill();
        c.fillStyle = col;
        if (it.back === "s") { rr(c, X + 10, Y + 4, w - 20, h - 22, 8); c.fill(); for (let i = 1; i < it.w / 2; i++) { c.strokeStyle = C.sofaDark; c.lineWidth = 2; c.beginPath(); c.moveTo(X + i * 64, Y + 6); c.lineTo(X + i * 64, Y + h - 22); c.stroke(); } }
        else { rr(c, X + 18, Y + 10, w - 22, h - 20, 8); c.fill(); for (let i = 1; i < it.h / 2; i++) { c.strokeStyle = C.sofaDark; c.lineWidth = 2; c.beginPath(); c.moveTo(X + 20, Y + i * 64); c.lineTo(X + w - 6, Y + i * 64); c.stroke(); } }
        c.fillStyle = "#e8b45a"; c.beginPath(); c.ellipse(X + (it.back === "s" ? 26 : 34), Y + (it.back === "s" ? 18 : 28), 9, 7, .4, 0, 7); c.fill();
        c.fillStyle = "#5e7f8c"; c.beginPath(); c.ellipse(X + (it.back === "s" ? w - 28 : 34), Y + (it.back === "s" ? 18 : h - 30), 9, 7, -.4, 0, 7); c.fill();
        // seats should stay walkable: only the back row blocks
        if (it.back === "s") for (let i = it.x; i < it.x + it.w; i++) { solid[it.y][i] = 0; }
        else for (let j = it.y; j < it.y + it.h; j++) { solid[j][it.x + 1] = 0; }
        break;
      }
      case "fireplace": {
        c.fillStyle = "#8d8378"; rr(c, X - 6, Y - 10, w + 12, h + 16, 4); c.fill();
        for (let i = 0; i < 9; i++) { c.fillStyle = i % 2 ? "#7a7066" : "#9b9186"; c.fillRect(X - 4 + i * (w + 8) / 9, Y - 8, (w + 8) / 9 - 2, 6); }
        c.fillStyle = "#1d1410"; rr(c, X + 30, Y - 4, w - 60, h + 2, 6); c.fill(); break;
      }
      case "tv": {
        c.fillStyle = C.woodDark; rr(c, X + 6, Y, 20, h, 4); c.fill();
        c.fillStyle = "#111"; rr(c, X + 18, Y + 10, 10, h - 20, 2); c.fill(); break;
      }
      case "petbed": { c.fillStyle = PET === "cat" ? "#8e3f3a" : "#4f6d8e"; c.beginPath(); c.arc(X + 16, Y + 16, 14, 0, 7); c.fill(); c.fillStyle = "#e9dcc5"; c.beginPath(); c.arc(X + 16, Y + 16, 10, 0, 7); c.fill(); break; }
      case "clock": break;
    }
  }

  // ------------------------------------------------------------------ animated screens + scene state
  for (const s of screens) { s.data = Array.from({ length: 24 }, (_, i) => 0.5 + Math.sin(i / 3 + rnd() * 6) * .2); s.t = 0; s.lines = Array.from({ length: 6 }, () => [rnd() * .3, .2 + rnd() * .7]); }
  const scene = { spy: null, equity: null, dayChg: null, open: false, tickers: "", darkness: 0, zoomFit: 1 };

  function drawScreens(c, t, dt) {
    for (const s of screens) {
      const X = s.x * T, Y = s.y * T, w = s.w * T, h = s.h * T;
      c.fillStyle = "#1a1512"; rr(c, X - 2, Y - 2, w + 4, h + 4, 3); c.fill();
      c.fillStyle = C.screen; c.fillRect(X, Y, w, h);
      s.t += dt;
      if (s.t > .6) { s.t = 0; s.data.push(Math.max(.1, Math.min(.9, s.data.at(-1) + (Math.random() - .48) * .18))); s.data.shift(); if (s.kind === "code") { s.lines.push([Math.random() * .3, .2 + Math.random() * .7]); s.lines.shift(); } }
      if (s.kind === "chart" || s.kind === "evo") {
        c.strokeStyle = s.kind === "evo" ? "#e8b45a" : s.color; c.lineWidth = 1.4; c.beginPath();
        s.data.forEach((v, i) => { const x = X + 2 + i * (w - 4) / 23, y = Y + h - 2 - (s.kind === "evo" ? (i / 23) * .7 + v * .25 : v) * (h - 4); i ? c.lineTo(x, y) : c.moveTo(x, y); });
        c.stroke();
      } else if (s.kind === "code") {
        s.lines.forEach(([a, b], i) => { c.fillStyle = ["#7cb064", "#e8b45a", "#86b6ef", "#e87ba4"][i % 4]; c.fillRect(X + 2 + a * w, Y + 2 + i * (h - 4) / 6, b * (w - 6) * .8, 1.6); });
      } else if (s.kind === "news") {
        for (let i = 0; i < 4; i++) { c.fillStyle = i ? "rgba(255,255,255,.35)" : s.color; c.fillRect(X + 2, Y + 2 + i * 3.6, (w - 4) * (i ? .6 + .3 * Math.sin(t + i) : .9), 1.6); }
      } else if (s.kind === "report") {
        c.fillStyle = "#f4ead9"; c.fillRect(X + 3, Y + 2, w - 6, h - 4); c.fillStyle = "#9085e9"; for (let i = 0; i < 4; i++) c.fillRect(X + 5, Y + 4 + i * 3.5, (w - 10) * (i ? .8 : .5), 1.4);
      }
    }
    // the big market board
    const mb = items.find((i) => i.kind === "marketboard"), X = mb.x * T, Y = mb.y * T - 10, w = mb.w * T, h = mb.h * T + 8;
    c.fillStyle = C.screen; c.fillRect(X, Y, w, h);
    c.save(); c.beginPath(); c.rect(X, Y, w, h); c.clip();
    c.font = `800 12px ${FONT}`; c.fillStyle = scene.open ? "#7cd07c" : "#9a8a78"; c.fillText(scene.open ? "● MARKET OPEN" : "● MARKET CLOSED", X + 10, Y + 16);
    c.fillStyle = "#f4ead9"; c.textAlign = "center"; c.fillText(scene.spy ? `SPY $${scene.spy.toFixed(2)}` : "SPY", X + w / 2, Y + 16);
    if (scene.equity) { c.fillStyle = "#e8b45a"; c.textAlign = "right"; c.fillText(`Equity $${scene.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, X + w - 10, Y + 16); }
    c.textAlign = "left";
    c.font = `700 10px ${FONT}`; c.fillStyle = "#86b6ef";
    const tick = scene.tickers || "Researchers are warming up…  ", tw = c.measureText(tick).width + 60, off = (t * 40) % tw;
    for (let k = -1; k < 3; k++) c.fillText(tick, X + w - off + k * tw - w, Y + 34);
    c.restore();
    // crypto desk "closed" sign when switched off in the Studio
    if (scene.cryptoOn === false) {
      const sx = 13 * T + 34, sy = 8 * T - 6;
      c.fillStyle = "rgba(28,23,20,.92)"; rr(c, sx, sy, 60, 20, 6); c.fill();
      c.strokeStyle = "#e66767"; c.lineWidth = 1.5; c.stroke();
      c.font = `800 10px ${FONT}`; c.fillStyle = "#e66767"; c.textAlign = "center"; c.fillText("OFF", sx + 30, sy + 14); c.textAlign = "left";
    }
    // living-room TV
    const tv = items.find((i) => i.kind === "tv"); const tx = tv.x * T + 18, ty = tv.y * T + 10, th = tv.h * T - 20;
    c.fillStyle = C.screen; c.fillRect(tx, ty, 10, th);
    c.strokeStyle = "#7cd07c"; c.lineWidth = 1.2; c.beginPath();
    for (let i = 0; i <= 20; i++) { const yy = ty + 4 + i * (th - 8) / 20, xx = tx + 5 + Math.sin(i * .9 + t * .6) * 2.5 + (i / 20 - .5) * -2; i ? c.lineTo(xx, yy) : c.moveTo(xx, yy); }
    c.stroke();
    // server LEDs
    const rack = items.find((i) => i.kind === "rack");
    for (let i = 0; i < 10; i++) for (let j = 0; j < 4; j++) {
      const on = Math.sin(t * (2 + j) + i * 1.7 + j) > .2;
      c.fillStyle = on ? (j === 0 ? "#7cd07c" : j === 3 ? "#e8b45a" : "#86b6ef") : "#2c3238";
      c.fillRect(rack.x * T + 10 + j * 9, rack.y * T + 9 + i * 12, 4, 3);
    }
    // fireplace flames
    const fp = items.find((i) => i.kind === "fireplace"), fx = fp.x * T + 30, fy = fp.y * T - 4, fw = fp.w * T - 60;
    c.fillStyle = "#4a2a18"; c.fillRect(fx + 14, fy + 18, fw - 28, 5);
    for (let i = 0; i < 7; i++) {
      const px = fx + 18 + i * (fw - 36) / 6, fl = 9 + Math.sin(t * 9 + i * 2.1) * 3 + Math.sin(t * 13 + i) * 2;
      c.fillStyle = "rgba(230,80,20,.85)"; c.beginPath(); c.ellipse(px, fy + 15 - fl / 2.5, 7, fl / 1.3, 0, 0, 7); c.fill();
      c.fillStyle = "rgba(255,150,40,.9)"; c.beginPath(); c.ellipse(px, fy + 16 - fl / 3, 4.5, fl / 1.7, 0, 0, 7); c.fill();
      c.fillStyle = "rgba(255,225,120,.95)"; c.beginPath(); c.ellipse(px, fy + 17 - fl / 4, 2.4, fl / 2.8, 0, 0, 7); c.fill();
    }
    // coffee steam
    for (let i = 0; i < 3; i++) {
      const k = ((t * .6 + i / 3) % 1), sx = 12 * T + 14 + Math.sin(t * 2 + i * 2) * 3, sy = 16 * T + 12 - k * 22;
      c.fillStyle = `rgba(255,255,255,${.35 * (1 - k)})`; c.beginPath(); c.arc(sx, sy, 3 + k * 4, 0, 7); c.fill();
    }
    // wall clock (kitchen) with the real time
    const now = new Date(), cx = 4.5 * T, cy = 15.5 * T;
    c.fillStyle = "#f4ead9"; c.beginPath(); c.arc(cx, cy, 11, 0, 7); c.fill(); c.strokeStyle = C.woodDark; c.lineWidth = 2.5; c.stroke();
    const hand = (ang, len, wdt) => { c.lineWidth = wdt; c.strokeStyle = "#3b2f28"; c.beginPath(); c.moveTo(cx, cy); c.lineTo(cx + Math.sin(ang) * len, cy - Math.cos(ang) * len); c.stroke(); };
    hand((now.getHours() % 12 + now.getMinutes() / 60) / 12 * 6.283, 6, 2.2); hand(now.getMinutes() / 60 * 6.283, 9, 1.5);
  }

  // ------------------------------------------------------------------ bots
  const bots = new Map();
  let roster = {};
  const occupied = new Map();
  function makeBot(a) {
    const home = HOME[a.id];
    const b = { id: a.id, a, name: a.name, color: a.color, badge: a.badge, kind: a.kind, desk: a.desk,
      x: (home.x + .5) * T, y: (home.y + .5) * T, face: home.face, path: [], poi: home, sitting: true,
      until: performance.now() / 1000 + rand(2, 12), queue: [], bubble: null, phase: Math.random() * 6, sleeping: false,
      blink: 0, chatting: false, moving: false };
    occupied.set(home.key, b.id);
    return b;
  }
  function claim(b, poi) { if (b.poi) occupied.delete(b.poi.key); if (poi) occupied.set(poi.key, b.id); b.poi = poi; }
  function free(list) { return list.filter((p) => !occupied.has(p.key)); }
  function goTo(b, poi, opts = {}) {
    if (!poi) return false;
    const sx = Math.floor(b.x / T), sy = Math.floor(b.y / T);
    const path = astar(sx, sy, poi.x, poi.y);
    if (!path) return false;
    claim(b, poi); b.path = path.slice(1).map(([x, y]) => [(x + .5) * T, (y + .5) * T]);
    b.sitting = false; b.sleeping = false; b.arrive = opts; b.until = Infinity; return true;
  }
  function say(b, text, secs = 6) { if (text) b.bubble = { text: String(text), until: performance.now() / 1000 + secs, born: performance.now() / 1000 }; }

  function nextActivity(b, now) {
    b.until = now + 2;            // if nothing below works out, try again shortly
    if (b.chatting) { b.until = now + 3; return; }
    if (b.queue.length) { const task = b.queue.shift(); if (!goTo(b, task.poi, task)) b.until = now + 1; return; }
    const st = roster[b.id] || {}, hour = new Date().getHours(), night = hour >= 23 || hour < 6;
    if (st.busy && Math.random() < .78) {
      if (Math.random() < .72) return void goTo(b, HOME[b.id], { dwell: rand(25, 70), sayActivity: true });
      const cat = pick(ROLE_WORK[b.kind] || ["board"]);
      return void goTo(b, pick(free(POI[cat])) || HOME[b.id], { dwell: rand(10, 22), bubble: b.kind === "researcher" ? "📖" : b.kind === "lab" ? "🧬" : "" });
    }
    const r = Math.random();
    if (night && r < .45) return void goTo(b, pick(free(POI.sofa.concat(POI.armchair))), { dwell: rand(60, 160), sleep: true });
    if (r < .14) return void goTo(b, pick(free(POI.coffee)) || pick(free(POI.stools)), { dwell: rand(8, 16), bubble: "☕" });
    if (r < .22) return void goTo(b, pick(free(POI.fridge.concat(POI.stools))), { dwell: rand(8, 18), bubble: pick(["🍪", "🍎", "🥪"]) });
    if (r < .38) return void goTo(b, pick(free(POI.sofa)), { dwell: rand(25, 70) });
    if (r < .48) return void goTo(b, pick(free(POI.dining)), { dwell: rand(20, 45) });
    if (r < .56) return void goTo(b, pick(free(POI.fire.concat(POI.window))), { dwell: rand(10, 25) });
    if (r < .66) return void goTo(b, pick(free(POI.library)), { dwell: rand(20, 50), bubble: "📖" });
    if (r < .82 && meetUp(b)) return;
    goTo(b, HOME[b.id], { dwell: rand(20, 50), sayActivity: true });
  }
  function meetUp(b) {
    const others = [...bots.values()].filter((o) => o !== b && !o.chatting && !o.queue.length && !(roster[o.id] || {}).busy && o.until !== Infinity);
    if (!others.length) return false;
    const o = pick(others), spot = pick(MEET.filter(([p, q]) => !occupied.has(p.key) && !occupied.has(q.key)));
    if (!spot) return false;
    const [l1, l2] = pick(SMALLTALK), dwell = rand(14, 24);
    if (!goTo(b, spot[0], { dwell, talk: l1, delay: 1 })) return false;
    goTo(o, spot[1], { dwell, talk: l2, delay: 4 });
    return true;
  }

  function updateBot(b, now, dt) {
    if (b.path.length) {
      b.moving = true;
      const [tx, ty] = b.path[0], dx = tx - b.x, dy = ty - b.y, d = Math.hypot(dx, dy), sp = 78 * dt;
      if (Math.abs(dx) > Math.abs(dy)) b.face = dx > 0 ? "e" : "w"; else if (d > .1) b.face = dy > 0 ? "s" : "n";
      if (d <= sp) { b.x = tx; b.y = ty; b.path.shift(); } else { b.x += dx / d * sp; b.y += dy / d * sp; }
      if (!b.path.length) {
        b.moving = false; const o = b.arrive || {}, p = b.poi;
        b.face = p.face; b.sitting = p.sit; b.sleeping = !!o.sleep;
        b.until = now + (o.dwell || rand(15, 40));
        if (o.bubble) say(b, o.bubble, 5);
        if (o.talk) setTimeout(() => say(b, o.talk, 5), (o.delay || 1) * 1000);
        if (o.sayActivity && roster[b.id]?.busy && Math.random() < .6) say(b, shorten(roster[b.id].activity, 46), 7);
        if (o.then) o.then(b);
      }
    } else if (now >= b.until) nextActivity(b, now);
    if (b.bubble && now > b.bubble.until) b.bubble = null;
    b.blink -= dt; if (b.blink < -3 - Math.random() * 3) b.blink = .15;
  }
  const shorten = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

  function drawBot(c, b, t) {
    const busy = (roster[b.id] || {}).busy;
    const bob = b.moving ? Math.abs(Math.sin(t * 11 + b.phase)) * 2.2 : b.sleeping ? Math.sin(t * 1.5) * .6 : Math.sin(t * 2 + b.phase) * .6;
    const x = b.x, y = b.y + (b.sitting ? 2 : 0);
    c.fillStyle = "rgba(20,10,5,.32)"; c.beginPath(); c.ellipse(x, y + 10, 12, 5, 0, 0, 7); c.fill();
    if (b.moving) { const s = Math.sin(t * 11 + b.phase); c.fillStyle = shade(b.color, -.45); c.beginPath(); c.ellipse(x - 5, y + 9 + s * 1.5, 3.5, 2.5, 0, 0, 7); c.ellipse(x + 5, y + 9 - s * 1.5, 3.5, 2.5, 0, 0, 7); c.fill(); }
    // body
    const by = y - 2 - bob;
    c.fillStyle = shade(b.color, -.3); c.beginPath(); c.ellipse(x, by + 1.5, 11, 10, 0, 0, 7); c.fill();
    c.fillStyle = b.color; c.beginPath(); c.ellipse(x, by, 11, 9.5, 0, 0, 7); c.fill();
    c.fillStyle = "rgba(255,255,255,.22)"; c.beginPath(); c.ellipse(x - 4, by - 4, 4, 2.5, -.5, 0, 7); c.fill();
    // arms typing when seated at home and busy
    if (b.sitting && busy && b.poi === HOME[b.id] && !b.moving) {
      const k = Math.sin(t * 18 + b.phase) > 0 ? 1 : -1, fy = b.face === "n" ? -1 : 1;
      c.fillStyle = shade(b.color, -.4); c.beginPath(); c.arc(x - 7, by + fy * (8 + k), 2.5, 0, 7); c.arc(x + 7, by + fy * (8 - k), 2.5, 0, 7); c.fill();
    }
    // head
    const hy = by - 13;
    c.fillStyle = shade(b.color, -.3); c.beginPath(); c.arc(x, hy + 1.5, 9.5, 0, 7); c.fill();
    c.fillStyle = shade(b.color, .12); c.beginPath(); c.arc(x, hy, 9.5, 0, 7); c.fill();
    // antenna light: green = working, amber = idle, off = asleep
    c.strokeStyle = shade(b.color, -.4); c.lineWidth = 1.5; c.beginPath(); c.moveTo(x, hy - 9); c.lineTo(x, hy - 14); c.stroke();
    const glow = b.sleeping ? "#5a4a3a" : busy ? (Math.sin(t * 4 + b.phase) > -.2 ? "#7cd07c" : "#3f7a3f") : "#e8b45a";
    c.fillStyle = glow; c.beginPath(); c.arc(x, hy - 15, 2.6, 0, 7); c.fill();
    // face visor
    if (b.face !== "n") {
      const ox = b.face === "e" ? 3 : b.face === "w" ? -3 : 0;
      c.fillStyle = "#1b1f24"; rr(c, x - 7 + ox, hy - 4, 14, 8, 4); c.fill();
      if (b.sleeping) { c.strokeStyle = "#86b6ef"; c.lineWidth = 1.2; c.beginPath(); c.moveTo(x - 5 + ox, hy); c.lineTo(x - 2 + ox, hy); c.moveTo(x + 2 + ox, hy); c.lineTo(x + 5 + ox, hy); c.stroke(); }
      else if (b.blink > 0) { c.fillStyle = "#86b6ef"; c.fillRect(x - 5 + ox, hy - .5, 3, 1); c.fillRect(x + 2 + ox, hy - .5, 3, 1); }
      else { c.fillStyle = b.chatting ? "#e8b45a" : "#9fd0ff"; c.beginPath(); c.arc(x - 3 + ox, hy, 1.9, 0, 7); c.arc(x + 3 + ox, hy, 1.9, 0, 7); c.fill(); }
    } else { c.fillStyle = shade(b.color, -.2); rr(c, x - 5, hy - 3, 10, 6, 3); c.fill(); }
    // badge + name tag
    c.font = `11px "Segoe UI Emoji", ${FONT}`; c.textAlign = "center"; c.fillText(b.badge, x + 11, by + 4);
    c.font = `800 9.5px ${FONT}`; const nw = c.measureText(b.name).width + 10;
    c.fillStyle = "rgba(28,23,20,.78)"; rr(c, x - nw / 2, y + 13, nw, 13, 6.5); c.fill();
    c.fillStyle = "#f4ead9"; c.fillText(b.name, x, y + 22.5); c.textAlign = "left";
    if (b.sleeping) { c.font = `800 ${9 + Math.sin(t * 2) * 1.5}px ${FONT}`; c.fillStyle = "rgba(200,220,255,.85)"; c.fillText("z", x + 10, hy - 14 - (t * 6 % 8)); c.fillText("Z", x + 15, hy - 20 - (t * 6 % 8)); }
  }
  function drawBubble(c, b, now) {
    if (!b.bubble && !b.chatting) return;
    const text = b.bubble ? b.bubble.text : "💬";
    const age = b.bubble ? now - b.bubble.born : 1, left = b.bubble ? b.bubble.until - now : 1;
    const alpha = Math.min(1, age * 4, left * 2);
    c.save(); c.globalAlpha = alpha; c.font = `700 10.5px "Segoe UI Emoji", ${FONT}`;
    const words = text.split(" "), lines = []; let line = "";
    for (const w of words) { const tst = line ? line + " " + w : w; if (c.measureText(tst).width > 160 && line) { lines.push(line); line = w; } else line = tst; }
    lines.push(line); if (lines.length > 3) { lines.length = 3; lines[2] = shorten(lines[2], 26) + (lines[2].endsWith("…") ? "" : "…"); }
    const w = Math.max(...lines.map((l) => c.measureText(l).width)) + 14, h = lines.length * 13 + 8;
    const x = b.x - w / 2, y = b.y - 44 - h;
    c.fillStyle = "rgba(255,248,235,.97)"; rr(c, x, y, w, h, 8); c.fill();
    c.beginPath(); c.moveTo(b.x - 5, y + h); c.lineTo(b.x, y + h + 6); c.lineTo(b.x + 5, y + h); c.fill();
    c.fillStyle = "#2b2420"; lines.forEach((l, i) => c.fillText(l, x + 7, y + 15 + i * 13));
    c.restore();
  }

  // ------------------------------------------------------------------ the studio pet
  // Which pet lives in the studio: "dog" (Athena the goldendoodle, click to pet) or "cat" (the original orange cat).
  const PET = "dog";

  // ------------------------------------------------------------------ the dog: Athena the goldendoodle (click to pet)
  const DOG_NAME = "Athena";
  const dog = { x: 29.5 * T, y: 18.5 * T, path: [], until: 0, sleep: true, face: 1, petUntil: 0, hearts: [], pets: 0 };
  try { dog.pets = +localStorage.getItem("dogPets") || 0; } catch (e) {}
  const DOG_SPOTS = [[29, 18], [26, 25], [30, 23], [23, 29], [35, 30], [12, 24], [46, 33], [29, 19]];
  function dogWalkTo(now, tx, ty) {
    const p = astar(Math.floor(dog.x / T), Math.floor(dog.y / T), tx, ty);
    if (p) { dog.path = p.slice(1).map(([x, y]) => [(x + .5) * T, (y + .5) * T]); dog.sleep = false; } else dog.until = now + 5;
  }
  function updateDog(now, dt) {
    dog.hearts = dog.hearts.filter((h) => now - h.t0 < 1.6);
    if (now < dog.petUntil) return;                       // sits still while being petted
    if (dog.path.length) {
      const [tx, ty] = dog.path[0], dx = tx - dog.x, dy = ty - dog.y, d = Math.hypot(dx, dy), sp = 62 * dt;
      if (Math.abs(dx) > .1) dog.face = dx >= 0 ? 1 : -1;
      if (d <= sp) { dog.x = tx; dog.y = ty; dog.path.shift(); if (!dog.path.length) { dog.until = now + rand(15, 60); dog.sleep = Math.random() < .5; } }
      else { dog.x += dx / d * sp; dog.y += dy / d * sp; }
    } else if (now > dog.until) {
      // sometimes trot over to visit one of the bots, otherwise to a favourite spot
      const vis = [...bots.values()].filter((b) => !b.sleeping);
      if (vis.length && Math.random() < .35) { const b = pick(vis); dogWalkTo(now, Math.floor(b.x / T) + (Math.random() < .5 ? 1 : -1), Math.floor(b.y / T) + 1); }
      else { const [tx, ty] = pick(DOG_SPOTS); dogWalkTo(now, tx, ty); }
    }
  }
  function petDog() {
    const now = performance.now() / 1000;
    dog.pets++; try { localStorage.setItem("dogPets", dog.pets); } catch (e) {}
    dog.sleep = false; dog.petUntil = now + 3.5; dog.until = now + rand(6, 14);
    for (let i = 0; i < 4; i++) dog.hearts.push({ x: dog.x + (i - 1.5) * 9 + rand(-2, 2), y: dog.y - 12, t0: now + i * .16 });
  }
  function dogAt(px, py) { if (PET !== "dog") return false; const [wx, wy] = toWorld(px, py); return Math.hypot(dog.x - wx, dog.y - 3 - wy) < 17; }
  // a fluffy blob: an ellipse with a scalloped, curly edge
  function fluff(c, x, y, rx, ry, col, bumps = 10, br = 2.4) {
    c.fillStyle = col; c.beginPath(); c.ellipse(x, y, rx, ry, 0, 0, 7); c.fill();
    for (let i = 0; i < bumps; i++) { const a = i / bumps * Math.PI * 2; c.beginPath(); c.arc(x + Math.cos(a) * rx, y + Math.sin(a) * ry, br, 0, 7); c.fill(); }
  }
  function curls(c, x, y, rx, ry, col, n, seed) {
    c.fillStyle = col;
    for (let i = 0; i < n; i++) { const a = (i * 2.399 + seed) % (Math.PI * 2), r = .35 + ((i * .618 + seed) % 1) * .5; c.beginPath(); c.arc(x + Math.cos(a) * rx * r, y + Math.sin(a) * ry * r, 1.3, 0, 7); c.fill(); }
  }
  function drawDog(c, t) {
    const x = dog.x, y = dog.y, f = dog.face, fur = "#dcae74", light = "#efcf9b", shade = "#c38f55", ear = "#c99559";
    c.fillStyle = "rgba(20,10,5,.3)"; c.beginPath(); c.ellipse(x, y + 7, 13, 4.5, 0, 0, 7); c.fill();
    if (dog.sleep && !dog.path.length && t > dog.petUntil) {       // curled up asleep, nose tucked into her tail
      const br = 1 + Math.sin(t * 1.6) * .03;
      fluff(c, x, y, 11 * br, 8.5 * br, fur, 12, 2.8);
      curls(c, x, y, 11, 8.5, light, 9, 1);
      fluff(c, x - 5, y + 1, 6, 5.4, light, 9, 2);                     // head resting on her paws
      c.fillStyle = ear; c.beginPath(); c.ellipse(x - 9.5, y + 1, 3, 5, .25, 0, 7); c.fill();
      c.fillStyle = "#2b2420"; c.beginPath(); c.arc(x - 1.5, y + 2.5, 1.3, 0, 7); c.fill();
      c.strokeStyle = "#5a4330"; c.lineWidth = .9; c.beginPath(); c.arc(x - 5, y, 1.2, .2, Math.PI - .2); c.stroke();
      c.font = `700 8px ${FONT}`; c.fillStyle = `rgba(255,255,255,${.5 + Math.sin(t * 2) * .3})`; c.fillText("z", x + 7, y - 11 - (t * 4 % 4));
      return;
    }
    const petting = t < dog.petUntil, walking = dog.path.length > 0;
    const wag = Math.sin(t * (petting ? 26 : walking ? 12 : 5)) * (petting ? 1 : .6);
    const bob = walking ? Math.abs(Math.sin(t * 10)) * 1.2 : 0, by = y - bob;
    // fluffy plume of a tail
    const tx = x - 13 * f + wag * 3 * f, ty = by - 9 + Math.abs(wag) * 1.5;
    c.strokeStyle = fur; c.lineWidth = 4; c.lineCap = "round";
    c.beginPath(); c.moveTo(x - 9 * f, by - 1); c.quadraticCurveTo(x - 14 * f, by - 3, tx, ty); c.stroke(); c.lineCap = "butt";
    fluff(c, tx, ty, 3, 2.6, fur, 6, 1.6);
    // legs (fluffy socks)
    const step = walking ? Math.sin(t * 14) * 1.6 : 0;
    for (const [lx, s] of [[-7, 1], [-4, -1], [4, -1], [7, 1]]) { c.fillStyle = shade; c.fillRect(x + lx * f - 1.3, by + 3, 2.8, 4 + s * step); c.fillStyle = fur; c.beginPath(); c.arc(x + lx * f, by + 7.2 + s * step, 1.8, 0, 7); c.fill(); }
    // curly body
    fluff(c, x, by, 11.5, 7, fur, 12, 2.6);
    curls(c, x, by, 11, 6.5, light, 10, 2);
    curls(c, x, by + 2, 10, 4, shade, 5, 4);
    // big teddy-bear head
    const hx = x + 10 * f, hy = by - 5 - (petting ? 1.5 : 0);
    fluff(c, hx, hy, 6.4, 6, fur, 10, 2.2);
    curls(c, hx - .5 * f, hy - 3, 4, 2.5, light, 5, 3);                    // curly topknot
    fluff(c, hx + 5 * f, hy + 2, 3.4, 2.8, light, 7, 1.5);                 // fluffy muzzle
    c.fillStyle = ear; c.beginPath(); c.ellipse(hx - 3.2 * f, hy + 2, 2.8, 5.4, f * .3, 0, 7); c.fill();   // long floppy ear
    c.fillStyle = shade; curls(c, hx - 3.2 * f, hy + 3, 2.4, 4.6, shade, 4, 5);
    c.fillStyle = "#2b2420"; c.beginPath(); c.arc(hx + 8 * f, hy + 1.5, 1.4, 0, 7); c.fill();          // nose
    if (petting) { c.strokeStyle = "#2b2420"; c.lineWidth = 1; c.beginPath(); c.arc(hx + 2 * f, hy - .8, 1.3, Math.PI * 1.1, Math.PI * 1.9); c.stroke(); }  // happy closed eye
    else { c.beginPath(); c.arc(hx + 2.2 * f, hy - .9, 1.15, 0, 7); c.fill(); c.fillStyle = "rgba(255,255,255,.8)"; c.beginPath(); c.arc(hx + 2.6 * f, hy - 1.3, .4, 0, 7); c.fill(); }
    if (petting || walking) { c.fillStyle = "#e46a7a"; c.beginPath(); c.ellipse(hx + 5.5 * f, hy + 5, 1.3, 1.9, 0, 0, 7); c.fill(); }  // tongue
    const a0 = f > 0 ? Math.PI * .5 : Math.PI * .1, a1 = f > 0 ? Math.PI * .9 : Math.PI * .5, at = f > 0 ? Math.PI * .62 : Math.PI * .38;
    c.strokeStyle = "#c0392b"; c.lineWidth = 1.8; c.beginPath(); c.arc(hx, hy, 6.6, a0, a1); c.stroke();                               // collar
    c.fillStyle = "#f2c94c"; c.beginPath(); c.arc(hx + Math.cos(at) * 7.4, hy + Math.sin(at) * 7.4, 1.1, 0, 7); c.fill();            // tag
  }
  function drawDogFx(c, now) {
    if (!dog.hearts.length) return;
    c.save(); c.font = `11px "Segoe UI Emoji", ${FONT}`; c.textAlign = "center";
    for (const h of dog.hearts) { const a = now - h.t0; if (a < 0) continue; c.globalAlpha = Math.max(0, 1 - a / 1.6); c.fillText("❤️", h.x + Math.sin(a * 5 + h.x) * 3, h.y - a * 24); }
    c.restore();
  }

  // ------------------------------------------------------------------ the cat (shown when PET = "cat")
  const cat = { x: 29.5 * T, y: 18.5 * T, path: [], until: 0, sleep: true, face: 1 };
  const CAT_SPOTS = [[29, 18], [26, 25], [30, 23], [23, 29], [35, 30], [12, 24], [46, 33], [29, 19]];
  function updateCat(now, dt) {
    if (cat.path.length) {
      const [tx, ty] = cat.path[0], dx = tx - cat.x, dy = ty - cat.y, d = Math.hypot(dx, dy), sp = 55 * dt;
      cat.face = dx >= 0 ? 1 : -1;
      if (d <= sp) { cat.x = tx; cat.y = ty; cat.path.shift(); if (!cat.path.length) { cat.until = now + rand(20, 80); cat.sleep = Math.random() < .6; } }
      else { cat.x += dx / d * sp; cat.y += dy / d * sp; }
    } else if (now > cat.until) {
      const [tx, ty] = pick(CAT_SPOTS), p = astar(Math.floor(cat.x / T), Math.floor(cat.y / T), tx, ty);
      if (p) { cat.path = p.slice(1).map(([x, y]) => [(x + .5) * T, (y + .5) * T]); cat.sleep = false; } else cat.until = now + 5;
    }
  }
  function drawCat(c, t) {
    const x = cat.x, y = cat.y, f = cat.face;
    c.fillStyle = "rgba(20,10,5,.3)"; c.beginPath(); c.ellipse(x, y + 5, 10, 4, 0, 0, 7); c.fill();
    if (cat.sleep && !cat.path.length) {
      c.fillStyle = "#e08a3c"; c.beginPath(); c.arc(x, y, 8, 0, 7); c.fill();
      c.strokeStyle = "#e08a3c"; c.lineWidth = 3; c.beginPath(); c.arc(x, y, 9, 0.2, 2.2); c.stroke();
      c.fillStyle = "#f2b06b"; c.beginPath(); c.arc(x - 3, y - 2, 4, 0, 7); c.fill();
      return;
    }
    const sw = Math.sin(t * 5) * .4;
    c.strokeStyle = "#e08a3c"; c.lineWidth = 2.5; c.beginPath(); c.moveTo(x - 8 * f, y); c.quadraticCurveTo(x - 14 * f, y - 4 + sw * 6, x - 16 * f, y - 9); c.stroke();
    c.fillStyle = "#e08a3c"; c.beginPath(); c.ellipse(x, y, 9, 5.5, 0, 0, 7); c.fill();
    c.beginPath(); c.arc(x + 8 * f, y - 2, 4.6, 0, 7); c.fill();
    c.beginPath(); c.moveTo(x + 6 * f, y - 5); c.lineTo(x + 7 * f, y - 10); c.lineTo(x + 9.5 * f, y - 5.5); c.fill();
    c.beginPath(); c.moveTo(x + 9 * f, y - 5); c.lineTo(x + 11.5 * f, y - 9.5); c.lineTo(x + 12 * f, y - 4); c.fill();
    c.fillStyle = "#2b2420"; c.beginPath(); c.arc(x + 10 * f, y - 2.5, .9, 0, 7); c.fill();
  }

  // ------------------------------------------------------------------ lighting
  function darknessNow() {
    const d = new Date(), h = d.getHours() + d.getMinutes() / 60, max = .6;
    if (h >= 8 && h < 17) return 0;
    if (h >= 17 && h < 20.5) return (h - 17) / 3.5 * max;
    if (h >= 20.5 || h < 5.5) return max;
    return (8 - h) / 2.5 * max;
  }
  const lightC = document.createElement("canvas");
  function drawLighting(ctx, t) {
    const dk = scene.darkness, z = cam.z * dpr;
    const sx = (wx) => ((wx - cam.x) * cam.z + vw / 2) * dpr, sy = (wy) => ((wy - cam.y) * cam.z + vh / 2) * dpr;
    if (dk <= .01) { // daytime: soft sunbeams from the north windows
      ctx.save(); ctx.globalCompositeOperation = "lighter";
      for (const [a, b, side] of windows) if (side === "top") {
        const g = ctx.createLinearGradient(0, sy(T), 0, sy(6 * T)); g.addColorStop(0, "rgba(255,225,160,.10)"); g.addColorStop(1, "rgba(255,225,160,0)");
        ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(sx(a * T), sy(T)); ctx.lineTo(sx((b + 1) * T), sy(T)); ctx.lineTo(sx((b + 3) * T), sy(6 * T)); ctx.lineTo(sx((a + 2) * T), sy(6 * T)); ctx.fill();
      }
      ctx.restore(); return;
    }
    if (lightC.width !== ctx.canvas.width || lightC.height !== ctx.canvas.height) { lightC.width = ctx.canvas.width; lightC.height = ctx.canvas.height; }
    const l = lightC.getContext("2d");
    l.globalCompositeOperation = "source-over"; l.clearRect(0, 0, lightC.width, lightC.height);
    l.fillStyle = `rgba(12,14,38,${dk})`; l.fillRect(0, 0, lightC.width, lightC.height);
    l.globalCompositeOperation = "destination-out";
    for (const L of lights) {
      const flick = L.kind === "fire" ? 1 + Math.sin(t * 7) * .05 + Math.sin(t * 13) * .03 : 1;
      const r = L.r * T * z * flick, x = sx(L.x * T), y = sy(L.y * T);
      const g = l.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(0,0,0,${L.kind === "screen" ? .55 : .95})`); g.addColorStop(1, "rgba(0,0,0,0)");
      l.fillStyle = g; l.beginPath(); l.arc(x, y, r, 0, 7); l.fill();
    }
    for (const b of bots.values()) { const x = sx(b.x), y = sy(b.y), r = 1.2 * T * z; const g = l.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, "rgba(0,0,0,.45)"); g.addColorStop(1, "rgba(0,0,0,0)"); l.fillStyle = g; l.beginPath(); l.arc(x, y, r, 0, 7); l.fill(); }
    ctx.drawImage(lightC, 0, 0);
    ctx.save(); ctx.globalCompositeOperation = "lighter";
    for (const L of lights) {
      if (L.kind === "screen") continue;
      const flick = L.kind === "fire" ? .8 + Math.sin(t * 8) * .12 + Math.sin(t * 15) * .06 : 1;
      const r = L.r * T * z * .8, x = sx(L.x * T), y = sy(L.y * T), g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(${L.color},${.16 * dk / .6 * flick})`); g.addColorStop(1, `rgba(${L.color},0)`);
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ camera, input, loop
  let canvas, ctx, dpr = 1, vw = 1, vh = 1;
  const cam = { x: PW / 2, y: PH / 2, z: 1 };
  let hover = null, onBot = () => {}, onBoard = () => {}, onShelf = () => {}, chattingId = null;
  const toWorld = (px, py) => [(px - vw / 2) / cam.z + cam.x, (py - vh / 2) / cam.z + cam.y];
  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    vw = canvas.clientWidth; vh = canvas.clientHeight;
    canvas.width = vw * dpr; canvas.height = vh * dpr;
    const side = vw > 900 ? 280 : 0;          // the team list sits on the left
    scene.zoomFit = Math.min(vw / PW, vh / PH);
    if (!cam.init) {
      cam.z = Math.min((vw - side - 20) / PW, (vh - 20) / PH);
      cam.x = PW / 2 - side / 2 / cam.z; cam.y = PH / 2; cam.init = true; return;
    }
    clampCam();
  }
  function clampCam() {
    cam.z = Math.max(scene.zoomFit * .7, Math.min(2.8, cam.z));
    const hw = vw / 2 / cam.z, hh = vh / 2 / cam.z, m = 320 / cam.z;
    cam.x = Math.max(Math.min(hw, PW / 2) - m, Math.min(Math.max(PW - hw, PW / 2) + m, cam.x));
    cam.y = Math.max(Math.min(hh, PH / 2) - 60, Math.min(Math.max(PH - hh, PH / 2) + 60, cam.y));
  }
  function botAt(px, py) {
    const [wx, wy] = toWorld(px, py); let best = null, bd = 22;
    for (const b of bots.values()) { const d = Math.hypot(b.x - wx, b.y - 12 - wy); if (d < bd) { bd = d; best = b; } }
    return best;
  }
  function input() {
    let drag = null, moved = 0;
    canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, cx: cam.x, cy: cam.y }; moved = 0; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; moved = Math.max(moved, Math.hypot(dx, dy)); if (moved > 4) canvas.classList.add("dragging"); cam.x = drag.cx - dx / cam.z; cam.y = drag.cy - dy / cam.z; clampCam(); }
      hover = drag && moved > 4 ? null : botAt(px, py);
      const overDog = !hover && !(drag && moved > 4) && dogAt(px, py);
      const [wx, wy] = toWorld(px, py), overBoard = wx > 25 * T && wx < 38 * T && wy < 2.2 * T;
      canvas.style.cursor = drag && moved > 4 ? "grabbing" : hover || overDog || overBoard ? "pointer" : "grab";
      World.onHover(hover ? { bot: hover, x: px, y: py } : overDog ? { dog: { name: DOG_NAME, pets: dog.pets, asleep: dog.sleep && !dog.path.length }, x: px, y: py } : null);
    });
    canvas.addEventListener("pointerup", (e) => {
      canvas.classList.remove("dragging");
      if (drag && moved <= 4) {
        const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top, b = botAt(px, py);
        const [wx, wy] = toWorld(px, py);
        if (dogAt(px, py) && (!b || Math.hypot(dog.x - wx, dog.y - wy) < Math.hypot(b.x - wx, b.y - 12 - wy))) { petDog(); World.onHover({ dog: { name: DOG_NAME, pets: dog.pets, asleep: false }, x: px, y: py }); }
        else if (b) onBot(b.id);
        else if (wx > 25 * T && wx < 38 * T && wy < 2.2 * T) onBoard();
        else if (wx > 51.5 * T && wx < 53 * T && wy > 4.8 * T && wy < 6 * T) onShelf();
      }
      drag = null;
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top, [wx, wy] = toWorld(px, py);
      cam.z *= Math.exp(-e.deltaY * .0015); clampCam();
      const [nx, ny] = toWorld(px, py); cam.x += wx - nx; cam.y += wy - ny; clampCam();
    }, { passive: false });
    window.addEventListener("resize", resize);
  }
  let last = performance.now() / 1000;
  function frame() {
    const now = performance.now() / 1000, dt = Math.min(.05, now - last); last = now;
    scene.darkness = darknessNow();
    if (cam.follow) { const b = bots.get(cam.follow); if (b) { cam.x += (b.x - cam.x) * .08; cam.y += (b.y - cam.y) * .08; } if (now > cam.followUntil) cam.follow = null; }
    for (const b of bots.values()) updateBot(b, now, dt);
    if (PET === "cat") updateCat(now, dt); else updateDog(now, dt);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = "#1c1714"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    const z = cam.z * dpr;
    ctx.setTransform(z, 0, 0, z, (vw / 2 - cam.x * cam.z) * dpr, (vh / 2 - cam.y * cam.z) * dpr);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(stat, 0, 0, PW, PH);
    drawScreens(ctx, now, dt);
    if (PET === "cat") drawCat(ctx, now); else drawDog(ctx, now);
    const sorted = [...bots.values()].sort((a, b) => a.y - b.y);
    for (const b of sorted) {
      if (b === hover) { ctx.strokeStyle = "rgba(232,180,90,.9)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.ellipse(b.x, b.y + 10, 16, 7, 0, 0, 7); ctx.stroke(); }
      drawBot(ctx, b, now);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0); drawLighting(ctx, now);
    ctx.setTransform(z, 0, 0, z, (vw / 2 - cam.x * cam.z) * dpr, (vh / 2 - cam.y * cam.z) * dpr);
    for (const b of sorted) drawBubble(ctx, b, now);
    if (PET === "dog") drawDogFx(ctx, now);
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ react to the real firm
  let prevSig = null;
  function setState(st) {
    roster = Object.fromEntries(st.agents.map((a) => [a.id, a]));
    for (const a of st.agents) if (!bots.has(a.id)) bots.set(a.id, makeBot(a)); else bots.get(a.id).a = a;
    scene.spy = st.spy; scene.equity = st.account?.equity; scene.open = st.market?.open;
    const ho = st.signals?.handoff || {};
    scene.cryptoOn = st.crypto_enabled !== false;
    scene.tickers = ["day", "swing", "long", "crypto", "forex"].map((d) => ho[d]?.symbols?.length ? `${d.toUpperCase()}: ${ho[d].symbols.join(" · ")}` : "").filter(Boolean).join("     ✦     ") + "     ✦     ";
    const s = st.signals || {};
    if (prevSig) {
      for (const d of ["day", "swing", "long", "crypto", "forex"]) {
        if (s.handoff?.[d]?.at && s.handoff[d].at !== prevSig.handoff?.[d]?.at) {
          const r = bots.get(`${d}_researcher`), tr = bots.get(`${d}_trader`);
          if (r && !r.chatting) r.queue.unshift({ poi: HANDOFF[d], dwell: 5, bubble: `📄 Briefs: ${s.handoff[d].symbols.join(", ")}`, then: () => tr && say(tr, "Thanks! Reading them now 👀", 4) });
          if (r && !r.chatting) r.until = 0;
        }
      }
      if (s.buy?.id && s.buy.id !== prevSig.buy?.id) { const b = bots.get(s.buy.agent); if (b) say(b, `🟢 ${s.buy.msg}`, 8); }
      if (s.debate?.id && s.debate.id !== prevSig.debate?.id) {
        const ch = bots.get("challenger"), tr = bots.get(`${s.debate.desk}_trader`);
        if (ch) say(ch, `⚖️ ${s.debate.symbol}: ${s.debate.verdict.toUpperCase()}`, 9);
        if (tr && !tr.chatting) { tr.queue.unshift({ poi: pick(free(POI.debatevisit)) || POI.debatevisit[0], dwell: 12, bubble: `Debating ${s.debate.symbol}…` }); tr.until = 0; }
      }
      if (s.evolution?.id && s.evolution.id !== prevSig.evolution?.id) { const b = bots.get("lab"); if (b) say(b, s.evolution.promoted ? `🧬 New ${s.evolution.desk} champion brain!` : `🧬 ${s.evolution.desk} generation done`, 8); }
      if (s.review?.id && (s.review.id !== prevSig.review?.id)) {
        const m = bots.get("manager"); if (m && !m.chatting) { m.queue.unshift({ poi: HANDOFF[s.review.desk] || POI.podwalk[0], dwell: 8, bubble: `📋 ${s.review.symbol}: grade ${s.review.grade || "?"}` }); m.until = 0; }
      }
      if (s.report && s.report !== prevSig.report) { const m = bots.get("manager"); if (m) say(m, "📝 New report is ready!", 8); }
    }
    prevSig = s;
  }

  const World = {
    async init(cv, cbs) {
      canvas = cv; ctx = canvas.getContext("2d");
      onBot = cbs.onBot; onBoard = cbs.onBoard; onShelf = cbs.onShelf || (() => {});
      try { await document.fonts.load(`800 12px Nunito`); await document.fonts.load(`700 12px Nunito`); } catch (e) {}
      drawStatic(); resize(); input(); requestAnimationFrame(frame);
    },
    setState,
    onHover: () => {},
    focus(id) { const b = bots.get(id); if (!b) return; cam.follow = id; cam.followUntil = performance.now() / 1000 + 1.6; cam.z = Math.max(cam.z, Math.min(1.4, scene.zoomFit * 2.2)); },
    setChatting(id) {
      if (chattingId && bots.get(chattingId)) { const o = bots.get(chattingId); o.chatting = false; o.until = performance.now() / 1000 + 3; }
      chattingId = id; const b = bots.get(id);
      if (b) { b.chatting = true; if (!b.path.length) { b.face = "s"; b.until = Infinity; } }
    },
    say(id, text) { const b = bots.get(id); if (b) say(b, text, 9); },
    _bots: bots, _astar: astar, _solid: solid, _dog: dog, _petDog: petDog, _cam: cam,
  };
  window.World = World;
})();
