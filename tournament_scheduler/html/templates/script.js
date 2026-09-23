// Restore saved theme before any rendering so theme-dependent colors
// (e.g. heatmap club palette) pick the correct variant on first paint.
(function() {
  var saved = localStorage.getItem('rvv-theme');
  document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';
})();

// Data embedded as JSON
const TOURNAMENTS = $TOURNAMENTS_JSON$;
const TEAM_GAME_COUNTS = $TEAM_GAME_COUNTS_JSON$;
const TEAM_TRAVEL = $TEAM_TRAVEL_JSON$;
const HEATMAP = $HEATMAP_JSON$;
const HEATMAP_WEEKS = $HEATMAP_WEEKS_JSON$;
const HEATMAP_CLUBS = $HEATMAP_CLUBS_JSON$;
const HEATMAP_CLUB_COLORS_BY_THEME = $HEATMAP_CLUB_COLORS_JSON$;
const CLUB_STATS = $CLUB_STATS_JSON$;
const ALL_CLUBS = $ALL_CLUBS_JSON$;

// Helpers
const MONTHS = ["jan","feb","mar","apr","mai","jun","jul","aug","sep","okt","nov","des"];
const WEEKDAYS = ["son","man","tir","ons","tor","fre","lor"];

function parseDate(s) {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function formatDateInfo(dateStr) {
  const d = parseDate(dateStr);
  return { day: d.getDate(), month: MONTHS[d.getMonth()], weekday: WEEKDAYS[d.getDay()] };
}

function slotLabel(n) {
  const labels = ["Bane 1", "Bane 2", "Bane 3", "Bane 4"];
  return n >= 0 && n < labels.length ? labels[n] : 'Bane ' + (n + 1);
}

function getClubFromTeam(team) {
  const clubs = ["Jar","Frisk Asker","Sandefjord","Jutul","Holmen","Skien","Ringerike","Kongsberg","Tonsberg","Tønsberg"];
  for (const c of clubs) {
    if (team === c || team.startsWith(c + ' ')) return c;
  }
  return team.split(' ')[0];
}

// Populate filter selects
(function() {
  const arenaSel = document.getElementById('filterArena');
  const clubSel = document.getElementById('filterClub');
  if (!arenaSel || !clubSel) return;

  const arenas = [...new Set(TOURNAMENTS.map(t => t.a))].sort();
  const clubs = new Set();
  TOURNAMENTS.forEach(t => {
    clubs.add(t.h);
    t.m.forEach(([h, a]) => { clubs.add(getClubFromTeam(h)); clubs.add(getClubFromTeam(a)); });
  });
  arenas.forEach(a => { const o = document.createElement('option'); o.value = a; o.textContent = a; arenaSel.appendChild(o); });
  [...clubs].sort().forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; clubSel.appendChild(o); });
})();

// Render team game counts table
(function() {
  const body = document.getElementById('teamGameCountsBody');
  if (!body) return;
  const sorted = Object.entries(TEAM_GAME_COUNTS).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  sorted.forEach(([label, count]) => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--border-dim)';
    const tdLabel = document.createElement('td');
    tdLabel.style.padding = '6px 12px';
    tdLabel.textContent = label;
    const tdCount = document.createElement('td');
    tdCount.style.padding = '6px 12px';
    tdCount.style.textAlign = 'right';
    tdCount.textContent = count;
    const tdLast = document.createElement('td');
    tdLast.style.padding = '6px 12px';
    tdLast.style.color = 'var(--text-muted)';
    let lastDate = '';
    for (const t of TOURNAMENTS) {
      if (t.m.some(([h, a]) => h === label || a === label)) {
        lastDate = t.d;
      }
    }
    tdLast.textContent = lastDate || '-';
    tr.appendChild(tdLabel);
    tr.appendChild(tdCount);
    tr.appendChild(tdLast);
    body.appendChild(tr);
  });
})();

// Render team travel distances table
(function() {
  const body = document.getElementById('teamTravelBody');
  if (!body) return;
  const sorted = Object.entries(TEAM_TRAVEL).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const maxKm = sorted.length > 0 ? sorted[0][1] : 0;
  let awayCounts = {};
  TOURNAMENTS.forEach(t => {
    if (t.cx) return;
    const hostClub = t.h;
    if (!hostClub) return;
    t.m.forEach(([h, a]) => {
      const hClub = getClubFromTeam(h);
      if (hClub !== hostClub) awayCounts[h] = (awayCounts[h] || 0) + 1;
      const aClub = getClubFromTeam(a);
      if (aClub !== hostClub) awayCounts[a] = (awayCounts[a] || 0) + 1;
    });
  });
  sorted.forEach(([label, km]) => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--border-dim)';
    const isMost = km === maxKm && km > 0;
    if (isMost) {
      tr.style.background = 'rgba(251,191,36,.08)';
    }
    const tdLabel = document.createElement('td');
    tdLabel.style.padding = '6px 12px';
    if (isMost) {
      tdLabel.innerHTML = '<span class="travel-icon">$ICON_TRAVEL$</span> <strong>' + label + '</strong> <span style="font-size:10px;color:var(--amber);font-weight:600">(størst anslått reise)</span>';
    } else {
      tdLabel.textContent = label;
    }
    const tdKm = document.createElement('td');
    tdKm.style.padding = '6px 12px';
    tdKm.style.textAlign = 'right';
    tdKm.textContent = km.toLocaleString();
    if (isMost) {
      tdKm.style.color = 'var(--amber)';
      tdKm.style.fontWeight = '600';
    }
    const tdAway = document.createElement('td');
    tdAway.style.padding = '6px 12px';
    tdAway.style.textAlign = 'right';
    tdAway.textContent = awayCounts[label] || 0;
    if (isMost) tdAway.style.color = 'var(--amber)';
    tr.appendChild(tdLabel);
    tr.appendChild(tdKm);
    tr.appendChild(tdAway);
    body.appendChild(tr);
  });
})();

// Render calendar heatmap
(function() {
  const head = document.getElementById('heatmapHead');
  const body = document.getElementById('heatmapBody');
  const legend = document.getElementById('heatmapLegend');
  if (!head || !body || !legend) return;
  if (!HEATMAP_WEEKS.length || !HEATMAP_CLUBS.length) {
    body.innerHTML = '<tr><td colspan="' + (HEATMAP_WEEKS.length + 1) + '" style="padding:16px;text-align:center;color:var(--text-muted)">Ingen turneringsdata for varmekart</td></tr>';
    return;
  }

  const currentTheme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  const HEATMAP_CLUB_COLORS = HEATMAP_CLUB_COLORS_BY_THEME[currentTheme] || HEATMAP_CLUB_COLORS_BY_THEME.dark || {};
  const DEFAULT_CLUB_COLOR = currentTheme === 'light'
    ? {bg: '#e4e4e7', text: '#52525b'}
    : {bg: '#2a2a2a', text: '#999'};

  // Build legend
  HEATMAP_CLUBS.forEach(club => {
    const c = HEATMAP_CLUB_COLORS[club] || DEFAULT_CLUB_COLOR;
    const span = document.createElement('span');
    span.style.cssText = 'display:inline-flex;align-items:center;gap:4px;font-size:11px;color:' + c.text;
    span.innerHTML = '<span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:' + c.bg + ';border:1px solid ' + c.text + '"></span>' + club;
    legend.appendChild(span);
  });
  [
    ['confirmed_booked', 'booket bekreftet'],
    ['confirmed_not_booked', 'ikke booket'],
    ['unknown', 'ikke kontrollert'],
    ['ambiguous', 'uklar/utdatert']
  ].forEach(function(item) {
    const span = document.createElement('span');
    span.className = 'heatmap-booking-legend heatmap-booking-' + item[0];
    span.textContent = item[1];
    legend.appendChild(span);
  });

  // Build header row: week labels with month grouping
  let headerRow = '<tr><th style="position:sticky;left:0;z-index:1;background:var(--bg);padding:6px 10px;text-align:left;color:var(--text-muted);font-weight:600;min-width:110px">Klubb</th>';
  const MONTHS_NO = ["","jan","feb","mar","apr","mai","jun","jul","aug","sep","okt","nov","des"];
  let lastMonth = '';
  HEATMAP_WEEKS.forEach(wk => {
    const parts = wk.split('-W');
    const year = parseInt(parts[0]);
    const week = parseInt(parts[1]);
    const jan4 = new Date(Date.UTC(year, 0, 4));
    const jan4Day = jan4.getUTCDay() || 7;
    const firstThursday = new Date(Date.UTC(year, 0, 4 - jan4Day + 4));
    const monday = new Date(firstThursday.getTime());
    monday.setUTCDate(monday.getUTCDate() + (week - 1) * 7);
    const month = MONTHS_NO[monday.getUTCMonth() + 1];
    const monthLabel = month !== lastMonth ? month : '';
    if (month !== lastMonth && month) lastMonth = month;
    headerRow += '<th style="padding:4px 2px;text-align:center;font-weight:600;font-size:10px;color:var(--text-muted)">' + monthLabel + '<br><span style="font-size:9px;color:var(--text-muted)">' + wk.slice(-2) + '</span></th>';
  });
  headerRow += '</tr>';
  head.innerHTML = headerRow;

  // Build body: one row per club
  let bodyHtml = '';
  HEATMAP_CLUBS.forEach(club => {
    const c = HEATMAP_CLUB_COLORS[club] || DEFAULT_CLUB_COLOR;
    bodyHtml += '<tr style="border-bottom:1px solid var(--border-dim)">';
    bodyHtml += '<td style="position:sticky;left:0;z-index:0;background:var(--bg);padding:6px 10px;font-size:12px;color:' + c.text + ';font-weight:600">' + club + '</td>';
    HEATMAP_WEEKS.forEach(wk => {
      const weekData = HEATMAP[wk] || {};
      const clubData = weekData[club];
      if (clubData && clubData.length) {
        const items = clubData.map(function(item) {
          if (typeof item === 'string') return {age_group: item, booking_status: 'unknown'};
          return item || {age_group: '', booking_status: 'unknown'};
        });
        const label = items.map(function(item) {
          const status = item.booking_status || 'unknown';
          const titleMap = {
            confirmed_booked: 'booket bekreftet',
            confirmed_not_booked: 'ikke booket',
            unknown: 'ikke kontrollert',
            not_checkable: 'ikke kontrollerbar',
            ambiguous: 'uklar booking',
            stale: 'bookinggrunnlag utdatert'
          };
          return '<span class="heatmap-booking-item heatmap-booking-' + status + '" title="' + (item.age_group || '') + ' – ' + (titleMap[status] || status) + '">' + (item.age_group || '') + '</span>';
        }).join('');
        bodyHtml += '<td style="background:' + c.bg + ';border:1px solid ' + c.text + ';padding:3px 4px;text-align:center;font-size:10px;color:' + c.text + ';font-weight:600;white-space:nowrap">' + label + '</td>';
      } else {
        bodyHtml += '<td style="background:var(--heatmap-empty-bg);border:1px solid var(--border-dim);padding:3px 4px;text-align:center"></td>';
      }
    });
    bodyHtml += '</tr>';
  });
  body.innerHTML = bodyHtml;
})();

function buildMatchHTML(matches, byes) {
  const rounds = {};

  matches.forEach(([home, away, slot, roundNumber]) => {
    const roundKey = Number.isFinite(roundNumber) && roundNumber > 0 ? String(roundNumber) : '0';
    if (!rounds[roundKey]) rounds[roundKey] = { matches: [], byes: [] };
    rounds[roundKey].matches.push([home, away, slot]);
  });

  if (byes && Object.keys(byes).length) {
    for (const [roundNum, labels] of Object.entries(byes)) {
      const roundKey = String(parseInt(roundNum, 10) || 0);
      if (!rounds[roundKey]) rounds[roundKey] = { matches: [], byes: [] };
      for (const label of labels) {
        rounds[roundKey].byes.push(label);
      }
    }
  }

  const roundKeys = Object.keys(rounds).sort((a, b) => Number(a) - Number(b));
  if (!roundKeys.length) {
    return '<div class="match-grid"><div class="match-row"><span class="bye-label">Ingen kamper</span></div></div>';
  }

  return roundKeys.map((roundKey) => {
    const round = rounds[roundKey];
    const roundLabel = roundKey === '0' ? 'Uten runde' : 'Runde ' + roundKey;
    let html = '<section class="round-group">' +
      '<div class="round-group-header"><span>' + roundLabel + '</span><span class="round-group-count">' + round.matches.length + ' kamper</span></div>' +
      '<div class="match-grid">' +
      round.matches.map(([home, away, slot]) =>
        '<div class="match-row"><span>' + home + '</span><span class="vs">vs</span><span>' + away +
        '</span><span class="slot">' + slotLabel(slot) + '</span></div>'
      ).join('') +
      '</div>';

    if (round.byes.length) {
      html += '<div class="round-byes">' + round.byes.map((label) =>
        '<div class="match-row bye-row"><span class="bye-label">Pause</span><span class="vs">&middot;</span><span>' + label + '</span><span class="slot"></span></div>'
      ).join('') + '</div>';
    }

    html += '</section>';
    return html;
  }).join('');
}

function render() {
  var ageSel = document.getElementById('filterAge');
  var arenaSel = document.getElementById('filterArena');
  var clubSel = document.getElementById('filterClub');
  var searchSel = document.getElementById('filterSearch');
  var timeline = document.getElementById('timeline');
  var totalTournaments = document.getElementById('totalTournaments');
  var visibleCount = document.getElementById('visibleCount');
  var totalCount = document.getElementById('totalCount');
  var monthRange = document.getElementById('monthRange');

  var age = ageSel ? ageSel.value : '';
  var arena = arenaSel ? arenaSel.value : '';
  var club = clubSel ? clubSel.value : '';
  var search = searchSel ? searchSel.value.toLowerCase().trim() : '';

  var html = '';
  var visible = 0;

  for (var i = 0; i < TOURNAMENTS.length; i++) {
    var t = TOURNAMENTS[i];
    if (age && t.g !== age) continue;
    if (arena && t.a !== arena) continue;
    if (club && t.h !== club) {
      var hasClub = t.m.some(function(m) { return getClubFromTeam(m[0]) === club || getClubFromTeam(m[1]) === club; });
      if (!hasClub) continue;
    }
    if (search) {
      var haystack = (t.a + ' ' + t.h + ' ' + t.g + ' ' + t.m.map(function(m) { return m[0] + ' ' + m[1]; }).join(' ')).toLowerCase();
      if (haystack.indexOf(search) === -1) continue;
    }

    visible++;
    if (!timeline) continue;

    var di = formatDateInfo(t.d);
    var timeRangeHtml = '';
    if (t.ts) {
      var timeLabel = t.te ? (t.ts + '–' + t.te) : t.ts;
      timeRangeHtml = '<div class="time-range">' + timeLabel + '</div>';
    }
    var cancelledClass = t.cx ? ' cancelled' : '';
    var cancelledBadge = t.cx
      ? '<div class="cancelled-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>AVLYST' + (t.cr ? ': ' + t.cr : '') + '</div>'
      : '';
    html += '<div class="tournament-card' + cancelledClass + '" onclick="this.classList.toggle(\'expanded\')">' +
      cancelledBadge +
      '<div class="tournament-card-header">' +
        '<div class="tournament-date"><div class="day">' + di.day + '</div><div class="month">' + di.month + '</div><div class="weekday">' + di.weekday + '</div>' + timeRangeHtml + '</div>' +
        '<div class="tournament-info"><h3>' + t.h + ' <span>&middot;</span> ' + t.a + '</h3>' +
          '<div class="tournament-meta">' +
            '<span class="tag tag--age"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>' + t.g + '</span>' +
            '<span class="tag tag--arena"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>' + t.a + '</span>' +
            '<span class="tag tag--teams"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>' + t.m.length + ' kamper</span>' +
            (t.tr ? '<span class="tag tag--travel">' + t.tr + '</span>' : '') +
          '</div></div>' +
        '<div class="tournament-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></div>' +
      '</div>' +
      '<div class="matches"><div class="matches-inner">' +
        '<div class="matches-header"><h4>Kamper per runde</h4><span class="count">' + t.m.length + ' stk</span></div>' +
        buildMatchHTML(t.m, t.b) +
      '</div></div></div>';
  }

  if (totalTournaments) totalTournaments.textContent = TOURNAMENTS.length;
  if (visibleCount) visibleCount.textContent = visible;
  if (totalCount) totalCount.textContent = TOURNAMENTS.length;

  if (TOURNAMENTS.length && monthRange) {
    var first = parseDate(TOURNAMENTS[0].d);
    var last = parseDate(TOURNAMENTS[TOURNAMENTS.length - 1].d);
    monthRange.textContent = MONTHS[first.getMonth()] + ' ' + first.getFullYear() + ' \u2013 ' + MONTHS[last.getMonth()] + ' ' + last.getFullYear();
  }

  if (!timeline) return;
  timeline.innerHTML = html ||
    '<div class="no-results"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><p>Ingen turneringer matcher filteret</p></div>';
}

var ageFilter = document.getElementById('filterAge');
var arenaFilter = document.getElementById('filterArena');
var clubFilter = document.getElementById('filterClub');
var searchFilter = document.getElementById('filterSearch');
var clearFilter = document.getElementById('filterClear');
var clubDashboard = document.getElementById('clubDashboard');

if (ageFilter) ageFilter.addEventListener('change', render);
if (arenaFilter) arenaFilter.addEventListener('change', render);
if (clubFilter) clubFilter.addEventListener('change', function() {
  var club = this.value;
  var dashboard = clubDashboard;
  if (club && dashboard && CLUB_STATS[club]) {
    var s = CLUB_STATS[club];
    document.getElementById('clubDashName').textContent = club;
    document.getElementById('clubDashHosted').textContent = s.hosted;
    document.getElementById('clubDashAway').textContent = s.away;
    document.getElementById('clubDashTravel').textContent = (s.travel_km || 0).toLocaleString();
    document.getElementById('clubDashTeams').textContent = s.teams;
    dashboard.style.display = 'block';
  } else if (dashboard) {
    dashboard.style.display = 'none';
  }
  render();
});
if (searchFilter) searchFilter.addEventListener('input', render);
if (clearFilter) clearFilter.addEventListener('click', function() {
  if (ageFilter) ageFilter.value = '';
  if (arenaFilter) arenaFilter.value = '';
  if (clubFilter) clubFilter.value = '';
  if (searchFilter) searchFilter.value = '';
  if (clubDashboard) clubDashboard.style.display = 'none';
  render();
});

(function() {
  var THEME_KEY = 'rvv-theme';
  var toggle = document.getElementById('themeToggle');
  if (toggle) {
    toggle.addEventListener('click', function() {
      var current = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
      var next = current === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      localStorage.setItem(THEME_KEY, next);
    });
  }
})();

render();
