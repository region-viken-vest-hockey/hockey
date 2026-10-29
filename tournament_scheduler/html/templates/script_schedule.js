// Schedule-only helpers and rendering
const MONTHS = ["jan","feb","mar","apr","mai","jun","jul","aug","sep","okt","nov","des"];
const WEEKDAYS = ["son","man","tir","ons","tor","fre","lor"];

// The exporter serializes tournaments in canonical chronological order, but
// sort defensively so the timeline never depends on the JSON array order.
// ISO YYYY-MM-DD strings compare correctly across year boundaries.
function compareTournaments(a, b) {
  return (a.d || '').localeCompare(b.d || '') ||
    (a.ts || '').localeCompare(b.ts || '') ||
    (a.g || '').localeCompare(b.g || '') ||
    (a.a || '').localeCompare(b.a || '') ||
    (a.h || '').localeCompare(b.h || '') ||
    (a.id || '').localeCompare(b.id || '');
}
TOURNAMENTS.sort(compareTournaments);
// Keep the complete season stable while the upcoming-only view mutates the
// shared array consumed by the existing renderer and filter helpers.
const ALL_TOURNAMENTS = TOURNAMENTS.slice();

function localIsoDate() {
  var now = new Date();
  var y = String(now.getFullYear());
  var m = String(now.getMonth() + 1).padStart(2, '0');
  var d = String(now.getDate()).padStart(2, '0');
  return y + '-' + m + '-' + d;
}

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

function tournamentTeams(t) {
  if (Array.isArray(t.p) && t.p.length) return t.p;
  const byLabel = {};
  (t.m || []).forEach(function(match) {
    [match[0], match[1]].forEach(function(label) {
      if (!byLabel[label]) byLabel[label] = { c: getClubFromTeam(label), l: label, g: t.g };
    });
  });
  return Object.values(byLabel);
}

function tournamentHasClub(t, club) {
  if (!club) return true;
  if (t.h === club) return true;
  return tournamentTeams(t).some(function(team) { return team.c === club; });
}

// Populate filter selects
(function() {
  const arenaSel = document.getElementById('filterArena');
  const clubSel = document.getElementById('filterClub');
  if (!arenaSel || !clubSel) return;

  const arenas = [...new Set(TOURNAMENTS.map(t => t.a))].sort();
  const clubs = new Set();
  TOURNAMENTS.forEach(t => {
    if (t.h) clubs.add(t.h);
    tournamentTeams(t).forEach(function(team) { if (team.c) clubs.add(team.c); });
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
  var approvalSel = document.getElementById('filterApproval');
  var timeline = document.getElementById('timeline');
  var totalTournaments = document.getElementById('totalTournaments');
  var visibleCount = document.getElementById('visibleCount');
  var totalCount = document.getElementById('totalCount');
  var monthRange = document.getElementById('monthRange');

  var age = ageSel ? ageSel.value : '';
  var arena = arenaSel ? arenaSel.value : '';
  var club = clubSel ? clubSel.value : '';
  var search = searchSel ? searchSel.value.toLowerCase().trim() : '';
  var approval = approvalSel ? approvalSel.value : '';

  var html = '';
  var visible = 0;

  for (var i = 0; i < TOURNAMENTS.length; i++) {
    var t = TOURNAMENTS[i];
    if (age && t.g !== age) continue;
    if (arena && t.a !== arena) continue;
    if (!tournamentHasClub(t, club)) continue;
    if (search) {
      var haystack = (t.a + ' ' + t.h + ' ' + t.g + ' ' + t.m.map(function(m) { return m[0] + ' ' + m[1]; }).join(' ')).toLowerCase();
      if (haystack.indexOf(search) === -1) continue;
    }
    if (approval === 'approved' && t.ap !== 'approved') continue;
    if (approval === 'stale' && t.ap !== 'stale_approval') continue;
    if (approval === 'not_approved' && t.ap === 'approved') continue;

    visible++;
    if (!timeline) continue;

    // An unverified/manual placeholder or a movable candidate is not an
    // established hall/time booking, so it must not render like one.
    var manual = !!t.mb;
    var confirmationRequired = !!t.rhc;
    var di = formatDateInfo(t.d);
    var timeRangeHtml = '';
    if (manual) {
      timeRangeHtml = '<div class="time-range time-range--unconfirmed">Tid ikke bekreftet</div>';
    } else if (t.ts) {
      var timeLabel = t.te ? (t.ts + '–' + t.te) : t.ts;
      if (confirmationRequired) timeLabel += ' · krever bekreftelse';
      timeRangeHtml = '<div class="time-range">' + timeLabel + '</div>';
    }
    var arenaTag = t.a || '';
    if (manual) {
      arenaTag = arenaTag ? arenaTag + ' (ikke bekreftet)' : 'Arena ikke bekreftet';
    } else if (confirmationRequired && arenaTag) {
      arenaTag += ' (krever bekreftelse)';
    }
    var cancelledClass = t.cx ? ' cancelled' : '';
    var manualClass = manual ? ' manual' : '';
    var cancelledBadge = t.cx
      ? '<div class="cancelled-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>AVLYST' + (t.cr ? ': ' + t.cr : '') + '</div>'
      : '';
    var guestBadge = '';
    if (t.gs && (t.gs.o || t.gs.f)) {
      var guestParts = [];
      if (t.gs.o) guestParts.push(t.gs.o === 1 ? '1 ledig gjesteplass' : t.gs.o + ' ledige gjesteplasser');
      if (t.gs.f) guestParts.push(t.gs.f === 1 ? '1 gjesteplass fylt' : t.gs.f + ' gjesteplasser fylt');
      guestBadge = '<div class="guest-badge">' + guestParts.join(' \u00b7 ') + '</div>';
    }
    // Cancelled cards keep their cancellation presentation only; an active
    // tournament always gets exactly one public booking badge.
    var publicBooking = publicBookingStateOf(t);
    var bookingBadge = t.cx ? ''
      : '<div class="booking-badge booking-badge--' + publicBooking + '">' + publicBookingStateLabel(publicBooking) + '</div>';
    html += '<div class="tournament-card' + cancelledClass + manualClass + '" onclick="this.classList.toggle(\'expanded\')">' +
      bookingBadge +
      guestBadge +
      cancelledBadge +
      '<div class="tournament-card-header">' +
        '<div class="tournament-date"><div class="day">' + di.day + '</div><div class="month">' + di.month + '</div><div class="weekday">' + di.weekday + '</div>' + timeRangeHtml + '</div>' +
        '<div class="tournament-info"><h3>' + t.h + ' <span>&middot;</span> ' + arenaTag + '</h3>' +
          '<div class="tournament-meta">' +
            (t.cx ? '<span class="tag tag--id">ID: ' + escapeHtml(t.id) + '</span>' : '') +
            '<span class="tag tag--age"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>' + t.g + '</span>' +
            '<span class="tag tag--arena"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>' + arenaTag + '</span>' +
            '<span class="tag tag--teams"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>' + t.m.length + ' kamper</span>' +
            (t.tr ? '<span class="tag tag--travel">' + t.tr + '</span>' : '') +
            (t.ph ? '<a class="tag tag--changed" href="public_history.html#t-' + escapeHtml(t.id) + '" onclick="event.stopPropagation()">Endret etter publisering</a>' : '') +
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
var upcomingFilter = document.getElementById('filterUpcoming');
var clubDashboard = document.getElementById('clubDashboard');

function applyUpcomingFilter() {
  var source = upcomingFilter && upcomingFilter.checked
    ? ALL_TOURNAMENTS.filter(function(t) { return !t.d || t.d >= localIsoDate(); })
    : ALL_TOURNAMENTS;
  TOURNAMENTS.splice.apply(TOURNAMENTS, [0, TOURNAMENTS.length].concat(source));
  render();
}

if (upcomingFilter) upcomingFilter.addEventListener('change', applyUpcomingFilter);
if (ageFilter) ageFilter.addEventListener('change', render);
if (arenaFilter) arenaFilter.addEventListener('change', render);
var approvalFilter = document.getElementById('filterApproval');
if (approvalFilter) approvalFilter.addEventListener('change', render);
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
  if (approvalFilter) approvalFilter.value = '';
  if (searchFilter) searchFilter.value = '';
  if (clubDashboard) clubDashboard.style.display = 'none';
  if (upcomingFilter) upcomingFilter.checked = true;
  applyUpcomingFilter();
});


applyUpcomingFilter();
