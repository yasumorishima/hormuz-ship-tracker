import { asyncBufferFromUrl, parquetReadObjects }
  from 'https://cdn.jsdelivr.net/npm/hyparquet@1.31.0/+esm';
import { compressors }
  from 'https://cdn.jsdelivr.net/npm/hyparquet-compressors@1.1.1/+esm';

const REPO = 'yasumorishima/hormuz-ais';
const TREE = `https://huggingface.co/api/datasets/${REPO}/tree/main?recursive=true`;
const FILE = p => `https://huggingface.co/datasets/${REPO}/resolve/main/${p}`;

// Matches the collector's bounding box (src/ais_parse.py).
const BBOX = [[22.0, 48.0], [30.5, 60.0]];

// The AIS "not available" sentinel, and the receiver glitches above it. Same
// filter the rendered snapshots use, so the map and the PNGs agree.
const isAnomalous = v => v.speed === null || v.speed === undefined || v.speed >= 40;

const TYPES = [
  { name: 'Tanker',    colour: '#ff6b6b', test: t => t >= 80 && t <= 89 },
  { name: 'Cargo',     colour: '#4aa8ff', test: t => t >= 70 && t <= 79 },
  { name: 'Tug / pilot', colour: '#9b7bff', test: t => t >= 50 && t <= 59 },
  { name: 'Passenger', colour: '#4ade80', test: t => t >= 60 && t <= 69 },
  { name: 'Fishing',   colour: '#ffd166', test: t => t === 30 },
  { name: 'Other / unknown', colour: '#8fa3b6', test: () => true },
];
const typeOf = t => TYPES.find(x => x.test(Number(t)));

const status = document.getElementById('status');
const say = html => { status.innerHTML = html; };

// Anything that came from the dataset listing, an error, or a vessel record
// came from outside this page. `say` and the popups build markup, so every
// such value goes through here first.
const esc = v => String(v).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const map = L.map('map', { preferCanvas: true, attributionControl: false })
  .fitBounds(BBOX);
map.createPane('land');
map.getPane('land').style.zIndex = 250;

// No tile provider. CARTO's keyless dark tiles now stamp "API KEY REQUIRED"
// across the map, every alternative worth using wants a key too, and a key is
// a paid dependency waiting to happen. The coastline is what a vessel map
// actually needs, and this repository already ships it: the same Natural
// Earth polygons the rendered snapshots are drawn on. Public domain, 72 kB,
// served from this site, nothing to expire.
fetch('land_mask.geojson')
  .then(r => r.json())
  .then(gj => L.geoJSON(gj, {
    pane: 'land',
    style: { color: '#5b7a99', weight: 1, fillColor: '#222c38', fillOpacity: 1 },
  }).addTo(map))
  .catch(e => console.error('land mask did not load', e));

L.rectangle(BBOX, { color: '#4aa8ff', weight: 1, dashArray: '5 5', fill: false })
  .addTo(map).bindTooltip('Collection area');

/** Newest file worth drawing, and an honest label for it. */
function chooseSource(entries) {
  const files = entries.filter(e => e.type === 'file' && e.path.endsWith('.parquet'));
  const newest = prefix => files
    .filter(f => f.path.startsWith(prefix))
    .sort((a, b) => a.path < b.path ? 1 : -1)[0];

  const shard = newest('raw/');
  if (shard) return { path: shard.path, kind: 'window' };
  const day = newest('daily/');
  if (day) return { path: day.path, kind: 'day' };
  if (files.some(f => f.path === 'positions.parquet')) {
    return { path: 'positions.parquet', kind: 'archive' };
  }
  return null;
}

/** Latest row per vessel, anomalies dropped. */
function latestPerVessel(rows) {
  const seen = new Map();
  for (const r of rows) {
    if (r.latitude === null || r.longitude === null) continue;
    if (isAnomalous(r)) continue;
    const prev = seen.get(r.mmsi);
    if (!prev || String(r.timestamp) > String(prev.timestamp)) seen.set(r.mmsi, r);
  }
  return [...seen.values()];
}

const COLUMNS = ['mmsi', 'timestamp', 'latitude', 'longitude', 'speed',
                 'course', 'ship_name', 'ship_type', 'destination', 'flag'];

function ago(iso) {
  // `timestamp` is naive UTC; `Z` makes that explicit to Date.
  const t = Date.parse(String(iso).endsWith('Z') ? iso : iso + 'Z');
  if (Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 90) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

const KIND = {
  window: 'the most recent 180-second collection window',
  day: 'the most recent compacted day',
  archive: 'the 2026-03 to 2026-04 archive, collected continuously on a Raspberry Pi',
};

function draw(vessels) {
  const layer = L.layerGroup().addTo(map);
  for (const v of vessels) {
    const t = typeOf(v.ship_type);
    const speed = v.speed === null || v.speed === undefined
      ? 'unknown' : `${Number(v.speed).toFixed(1)} kn`;
    const dl = [
      ['Type', t.name], ['Speed', speed],
      ['Course', v.course === null || v.course === undefined
        ? 'unknown' : `${Math.round(Number(v.course))}°`],
      ['Destination', v.destination || '—'],
      ['Flag', v.flag || 'unrecognised prefix'],
      ['MMSI', v.mmsi], ['Reported', `${v.timestamp} UTC`],
    ].map(([k, val]) => `<dt>${k}</dt><dd>${esc(val)}</dd>`).join('');
    L.circleMarker([v.latitude, v.longitude], {
      radius: 4, color: t.colour, weight: 1, fillColor: t.colour, fillOpacity: 0.75,
    }).bindPopup(`<strong>${esc(v.ship_name || 'Unnamed')}</strong><dl>${dl}</dl>`)
      .addTo(layer);
  }
  return layer;
}

const legend = L.control({ position: 'bottomright' });
legend.onAdd = () => {
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = TYPES.map(t =>
    `<i style="background:${t.colour}"></i>${t.name}`).join('<br>');
  return div;
};

/** The tree endpoint pages at a thousand entries.
 *
 * `daily/` gains a file every day and nothing removes them, so one page stops
 * being the whole listing eventually — and a page that happens to omit the
 * newest file would leave the map quietly drawing stale data. The Hub exposes
 * `Link` to cross-origin readers, so the cursor is followable from here.
 */
async function listAll(url) {
  const entries = [];
  while (url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`the dataset listing returned ${r.status}`);
    entries.push(...await r.json());
    const link = r.headers.get('Link') || '';
    const next = link.match(/<([^>]+)>;\s*rel="next"/);
    url = next ? next[1] : null;
  }
  return entries;
}

async function load() {
  const listing = await listAll(TREE);
  const source = chooseSource(listing);
  if (!source) throw new Error('the dataset holds no parquet files yet');

  say(`Reading <b>${esc(source.path)}</b>…`);
  const file = await asyncBufferFromUrl({ url: FILE(source.path) });
  const rows = await parquetReadObjects({ file, compressors, columns: COLUMNS });
  const vessels = latestPerVessel(rows);

  draw(vessels);
  legend.addTo(map);

  const newest = vessels.reduce(
    (a, v) => (a === null || String(v.timestamp) > a ? String(v.timestamp) : a), null);
  const age = newest ? ago(newest) : null;
  say(`<b>${vessels.length}</b> vessels from ${KIND[source.kind]}`
    + (age ? `, reported <b>${age}</b>` : '')
    + ` — <code>${esc(source.path)}</code>, read from the dataset in your browser.`
    + (source.kind === 'archive'
      ? ' <b>Collection has not started yet</b>, so this is history, not now.' : ''));
}

load().catch(e => {
  say(`Could not draw the map: ${esc(e.message)}. The data is still there — `
    + `<a href="https://huggingface.co/datasets/${REPO}">browse the dataset</a>.`);
  console.error(e);
});
