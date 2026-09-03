// Schedule-only helpers and rendering
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
    var manualBadge = t.mb
      ? '<div class="manual-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>MÅ BOOKES MANUELT</div>'
      : '';
    html += '<div class="tournament-card' + cancelledClass + '" onclick="this.classList.toggle(\'expanded\')">' +
      manualBadge +
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


render();
