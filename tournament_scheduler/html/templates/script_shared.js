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

// Shared helpers
function getClubFromTeam(team) {
  const clubs = ["Jar","Frisk Asker","Sandefjord","Jutul","Holmen","Skien","Ringerike","Kongsberg","Tonsberg","Tønsberg"];
  for (const c of clubs) {
    if (team === c || team.startsWith(c + ' ')) return c;
  }
  return team.split(' ')[0];
}

// Render static club summary table
(function() {
  const body = document.getElementById('clubSummaryBody');
  if (!body) return;
  const clubs = (ALL_CLUBS || []).slice().sort((a, b) => a.localeCompare(b));
  if (!clubs.length) return;
  body.innerHTML = '';
  clubs.forEach((club) => {
    const stats = CLUB_STATS[club] || {};
    const tr = document.createElement('tr');

    const tdClub = document.createElement('td');
    tdClub.textContent = club;
    tdClub.className = 'club-summary-name';
    tr.appendChild(tdClub);

    ['teams', 'hosted', 'away', 'travel_km'].forEach((key) => {
      const td = document.createElement('td');
      const value = stats[key] || 0;
      td.textContent = key === 'travel_km' ? value.toLocaleString() : value;
      td.className = 'numeric-cell';
      tr.appendChild(td);
    });

    body.appendChild(tr);
  });
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
