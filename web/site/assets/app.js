/* Stratum Race leaderboard — fetches data/leaderboard.json and renders. */
(function () {
  "use strict";

  var DATA_URL = "data/leaderboard.json";
  var REFRESH_MS = 60000;
  var currentFilter = "all";
  var lastData = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function fmt(v, digits) {
    if (v === null || v === undefined) return "—";
    return Number(v).toFixed(digits === undefined ? 1 : digits);
  }
  function prettyName(name) {
    return String(name || "");
  }

  var STATUS = {
    ranked: { dot: "dot-ok", label: "live" },
    collecting: { dot: "dot-collecting", label: "collecting" },
    no_races_yet: { dot: "dot-collecting", label: "connected, no races yet" },
    unreachable: { dot: "dot-down", label: "unreachable" }
  };

  function poolCell(p) {
    var d = el("div", "pool-cell");
    d.appendChild(el("span", "pool-name", prettyName(p.name)));
    d.appendChild(el("span", "pool-host", p.host ? p.host + ":" + p.port : ""));
    return d;
  }

  function renderLeaderboard(data) {
    var tbody = $("leaderboard").tBodies[0];
    tbody.textContent = "";
    var ranked = data.pools.filter(function (p) { return p.status === "ranked"; });
    var maxMedian = 0;
    ranked.forEach(function (p) {
      if (p.median_ms !== null && p.median_ms > maxMedian) maxMedian = p.median_ms;
    });
    var shown = ranked.filter(function (p) {
      return currentFilter === "all" || (p.tier || "small") === currentFilter;
    });

    shown.forEach(function (p) {
      var tr = el("tr", p.rank === 1 ? "rank-1" : null);

      tr.appendChild(el("td", "num rank", String(p.rank)));
      var poolTd = el("td"); poolTd.appendChild(poolCell(p)); tr.appendChild(poolTd);

      // Median value with a log-scaled bar underneath: keeps 150 ms and 4 s
      // pools both visible in one column.
      var medTd = el("td", "num median-cell");
      medTd.appendChild(el("div", "median-strong", fmt(p.median_ms)));
      var track = el("div", "median-bar");
      var bar = el("div", "bar");
      var w = maxMedian > 0
        ? Math.max(2, 100 * Math.log10(1 + (p.median_ms || 0)) / Math.log10(1 + maxMedian))
        : 2;
      bar.style.width = w + "%";
      track.appendChild(bar);
      medTd.appendChild(track);
      medTd.title = "median " + fmt(p.median_ms) + " ms behind the fastest full template (bar is log-scaled)";
      tr.appendChild(medTd);

      tr.appendChild(el("td", "num", fmt(p.avg_ms)));
      tr.appendChild(el("td", "num", fmt(p.p95_ms)));
      tr.appendChild(el("td", "num", fmt(p.best_ms)));
      var winsTxt = String(p.wins) + (p.win_pct === null ? "" : " · " + fmt(p.win_pct, 0) + "%");
      tr.appendChild(el("td", "num", winsTxt));
      var ef = el("td", "num", !p.empty_first_pct ? "—" : fmt(p.empty_first_pct, 0) + "%");
      if (p.empty_first_pct) ef.title = "First notify was an empty (coinbase-only) template in " + fmt(p.empty_first_pct, 0) + "% of its races";
      tr.appendChild(ef);
      tr.appendChild(el("td", "num", p.seen + "/" + data.races));
      tbody.appendChild(tr);
    });

    if (shown.length === 0 && ranked.length > 0) {
      var emptyTr = el("tr");
      var td = el("td", "muted-cell", "No " + currentFilter + " pools ranked yet.");
      td.colSpan = 9;
      emptyTr.appendChild(td);
      tbody.appendChild(emptyTr);
    }

    $("leaderboard-panel").classList.toggle("hidden", ranked.length === 0);
  }

  function renderRaces(data) {
    var tbody = $("races").tBodies[0];
    tbody.textContent = "";
    (data.recent_races || []).forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", "num", r.height ? String(r.height) : "—"));
      tr.appendChild(el("td", null, r.utc ? String(r.utc).replace(" UTC", "") : "—"));
      tr.appendChild(el("td", null, r.miner || "—"));
      var winTd = el("td");
      winTd.appendChild(el("span", "winner-chip", prettyName(r.winner)));
      tr.appendChild(winTd);
      var js = el("td", r.empty_jumpstart ? "muted-cell" : null, r.empty_jumpstart ? prettyName(r.empty_jumpstart) : "—");
      if (r.empty_jumpstart) js.title = "Sent an empty-template notify before any pool delivered a full template";
      tr.appendChild(js);
      tr.appendChild(el("td", null, r.second ? prettyName(r.second) : "—"));
      tr.appendChild(el("td", "num", fmt(r.second_delay_ms)));
      tr.appendChild(el("td", "num", fmt(r.spread_ms)));
      tr.appendChild(el("td", "num", String(r.pools_seen)));
      tbody.appendChild(tr);
    });
    $("races-panel").classList.toggle("hidden", !data.recent_races || data.recent_races.length === 0);
  }

  function renderWatchlist(data) {
    var list = $("watchlist");
    list.textContent = "";
    var rest = data.pools.filter(function (p) { return p.status !== "ranked"; });
    rest.forEach(function (p) {
      var li = el("li");
      var s = STATUS[p.status] || STATUS.collecting;
      li.appendChild(el("span", "dot " + s.dot));
      li.appendChild(el("span", null, prettyName(p.name)));
      var why = p.status === "unreachable"
        ? "unreachable"
        : (p.seen > 0 ? p.seen + " race" + (p.seen === 1 ? "" : "s") + " so far" : "awaiting first race");
      li.appendChild(el("span", "why", why));
      if (p.last_error) li.title = p.last_error;
      list.appendChild(li);
    });
    $("watch-panel").classList.toggle("hidden", rest.length === 0);
  }

  function renderTiles(data) {
    var ranked = data.pools.filter(function (p) { return p.status === "ranked"; });
    var leader = ranked.length ? ranked[0] : null;
    $("tile-leader").textContent = leader ? prettyName(leader.name) : "—";
    $("tile-leader-sub").textContent = leader
      ? "median +" + fmt(leader.median_ms) + " ms · " + fmt(leader.win_pct, 0) + "% wins"
      : "awaiting races";
    $("tile-races").textContent = String(data.races || 0);
    var hrs = (data.observation_seconds || 0) / 3600;
    $("tile-window").textContent = hrs >= 1 ? "over " + hrs.toFixed(1) + " h of observation" : "";
    $("tile-pools").textContent = String((data.pools || []).length);
    $("tile-ranked").textContent = ranked.length + " ranked";
    var last = (data.recent_races || [])[0];
    $("tile-last").textContent = last
      ? (last.height ? "#" + last.height : (last.prevhash_short || "seen"))
      : "—";
    $("tile-updated").textContent = data.generated_utc ? "updated " + data.generated_utc : "";
    $("foot-generated").textContent = data.generated_utc
      ? "last aggregation " + data.generated_utc + " · " + (data.sessions || 0) + " measurement sessions"
      : "";
    $("min-races").textContent = String(data.min_races_for_rank || 3);
    if (data.vantage) {
      $("vantage").textContent = data.vantage;
      $("vantage-line").classList.remove("hidden");
      $("vantage-inline").textContent = data.vantage;
      $("vantage-note").classList.remove("hidden");
    }
  }

  function render(data) {
    lastData = data;
    var hasRaces = (data.races || 0) > 0;
    $("empty-state").classList.toggle("hidden", hasRaces);
    renderTiles(data);
    renderLeaderboard(data);
    renderRaces(data);
    renderWatchlist(data);
  }

  Array.prototype.forEach.call(document.querySelectorAll(".filter-btn"), function (btn) {
    btn.addEventListener("click", function () {
      currentFilter = btn.getAttribute("data-filter");
      Array.prototype.forEach.call(document.querySelectorAll(".filter-btn"), function (b) {
        b.classList.toggle("active", b === btn);
      });
      if (lastData) renderLeaderboard(lastData);
    });
  });

  function load() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(render)
      .catch(function (err) {
        var empty = $("empty-state");
        empty.classList.remove("hidden");
        empty.querySelector("p").innerHTML =
          "<strong>Warming up.</strong> Leaderboard data isn't available yet (" +
          String(err.message || err) + "). The first measurement session may still be running — check back shortly.";
      });
  }

  load();
  setInterval(load, REFRESH_MS);
})();
