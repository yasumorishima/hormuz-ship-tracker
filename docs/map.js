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

async function load(listing) {
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

// ------------------------------------------------------------------ radar --

// Sentinel-1 detections, drawn from the newest pass on the Hub. Kept apart
// from the AIS layer in every way: its own status line, its own legend entry,
// its own failure. A radar detection is a bright object of the right size,
// not a ship, and nothing here joins it to an AIS identity.
//
// What is shown by default, measured over the 17 scenes on the Hub on
// 2026-09-28 (src/sar_survey.py; docs/PIPELINE.md "What the map shows, and
// why"; raw output docs/sar_survey.json):
// - SAR_MIN_SHORE_KM: from 3 km out the rate of vessel-sized detections is the
//   open-sea rate (4-5 per 100 km^2); at 0.2-1 km it is 94.5.
// - SAR_MIN_SNR: beyond 3 km, 89% of detections above SNR 20 also stand out
//   in the VH channel, against under 1% at random water; at or below 20, 6%.
//   The weak ones may be small craft VH misses or sea clutter; the table
//   cannot say which, so they are drawn only on request.
// Both are held equal to sar_columns by tests/test_site.py.
const SAR_MIN_SHORE_KM = 3;
const SAR_MIN_SNR = 20;
const SAR_DET = 'sar/det/v1/';
const SAR_SCENES = 'sar/scenes/v1/';
const SAR_SCENE_COLUMNS = ['scene_id', 'acq_time', 'status', 'aoi_covered_frac'];
const SAR_COLUMNS = ['acq_time', 'platform', 'orbit_state', 'latitude', 'longitude',
                     'dist_to_land_km', 'length_m', 'snr', 'vessel_sized'];
const sarStatus = document.getElementById('sar-status');
const saySar = html => { sarStatus.innerHTML = html; };

/** Passes on the Hub, newest first: every slice of one platform's datatake
 * on one date, as scene ids.
 *
 * A pass arrives as two or three slices, each its own file; drawing only the
 * newest slice would show a third of what the satellite saw. The id's
 * platform and start date name the pass (see sar_survey.pass_of). The scene
 * table is the ledger, so the passes are read from it, not from the
 * detections. */
function passesNewestFirst(entries) {
  const ids = entries
    .filter(e => e.type === 'file' && e.path.startsWith(SAR_SCENES) && e.path.endsWith('.parquet'))
    .map(e => e.path.slice(SAR_SCENES.length, -'.parquet'.length));
  const passOf = id => id.slice(0, 3) + id.slice(17, 25);
  const start = id => id.slice(17, 32);
  const byPass = new Map();
  for (const id of ids) {
    const k = passOf(id);
    byPass.set(k, [...(byPass.get(k) || []), id]);
  }
  const newestStart = slices => slices.map(start).sort().pop();
  return [...byPass.values()].sort((x, y) => (newestStart(x) < newestStart(y) ? 1 : -1));
}

async function readRows(path, columns) {
  const file = await asyncBufferFromUrl({ url: FILE(path) });
  return parquetReadObjects({ file, compressors, columns });
}

/** The newest pass that actually looked at the AOI.
 *
 * A pass that only clips a corner gets scene rows with status `no_overlap`
 * and empty detection files; drawing it would print "0 detections" for a
 * pass that saw nothing. So passes are tried newest first until one has a
 * slice with status `ok`, and only those slices are drawn. */
async function newestSeenPass(entries, tries = 6) {
  for (const slices of passesNewestFirst(entries).slice(0, tries)) {
    const scenes = [];
    for (const id of slices) {
      scenes.push(...await readRows(`${SAR_SCENES}${id}.parquet`, SAR_SCENE_COLUMNS));
    }
    const ok = scenes.filter(r => r.status === 'ok');
    if (ok.length) return ok;
  }
  return null;
}

const SAR_BANDS = [
  { name: `Radar, ${SAR_MIN_SHORE_KM} km or more from shore, SNR over ${SAR_MIN_SNR}`, on: true,
    test: (d, snr) => d >= SAR_MIN_SHORE_KM && snr > SAR_MIN_SNR, colour: '#f2f5f8' },
  { name: `Radar, ${SAR_MIN_SHORE_KM} km or more from shore, weak (mostly unconfirmed)`, on: false,
    test: (d, snr) => d >= SAR_MIN_SHORE_KM && !(snr > SAR_MIN_SNR), colour: '#7f93a8' },
  { name: `Radar, 1–${SAR_MIN_SHORE_KM} km from shore`, on: false,
    test: d => d >= 1 && d < SAR_MIN_SHORE_KM, colour: '#c9b27c' },
  { name: 'Radar, within 1 km of shore (mixed with terrain)', on: false,
    test: d => d < 1, colour: '#9a7b52' },
];

function drawSar(rows) {
  const layers = SAR_BANDS.map(() => L.layerGroup());
  const counts = SAR_BANDS.map(() => 0);
  for (const r of rows) {
    if (!r.vessel_sized || r.latitude === null || r.longitude === null) continue;
    const d = Number(r.dist_to_land_km);
    const i = SAR_BANDS.findIndex(b => b.test(d, Number(r.snr)));
    if (i < 0) continue;
    counts[i] += 1;
    const dl = [
      ['Seen', `${r.acq_time} (${r.platform}, ${r.orbit_state})`],
      ['Length', `about ${Math.round(Number(r.length_m))} m`],
      ['SNR', Number(r.snr).toFixed(1)],
      ['From shore', `${d.toFixed(1)} km`],
    ].map(([k, val]) => `<dt>${k}</dt><dd>${esc(val)}</dd>`).join('');
    L.circleMarker([r.latitude, r.longitude], {
      radius: 3, color: SAR_BANDS[i].colour, weight: 1.5, fill: false,
    }).bindPopup('<strong>Radar detection</strong> — a bright object of vessel size, '
      + `not an identified ship<dl>${dl}</dl>`).addTo(layers[i]);
  }
  const overlays = {};
  SAR_BANDS.forEach((b, i) => {
    overlays[`<span style="color:${b.colour}">&#9675;</span> ${b.name} (${counts[i]})`] = layers[i];
    if (b.on) layers[i].addTo(map);
  });
  L.control.layers(null, overlays, { collapsed: false, position: 'topright' }).addTo(map);
  return counts;
}

async function loadSar(listing) {
  const scenes = await newestSeenPass(listing);
  if (!scenes) { saySar('Radar: no processed Sentinel-1 pass over the strait on the Hub yet.'); return; }
  const rows = [];
  for (const sc of scenes) rows.push(...await readRows(`${SAR_DET}${sc.scene_id}.parquet`, SAR_COLUMNS));
  const counts = drawSar(rows);
  const when = scenes.map(sc => String(sc.acq_time)).sort()[0];
  const age = ago(when);
  // Slices of one datatake barely overlap, so the sum is close to the union.
  const seen = Math.min(100, Math.round(
    scenes.reduce((a, sc) => a + Number(sc.aoi_covered_frac), 0) * 100));
  saySar(`Radar: <b>${counts[0]}</b> strong vessel-sized detections ${SAR_MIN_SHORE_KM} km or more `
    + `from shore in the Sentinel-1 pass of <b>${esc(when)}</b>`
    + (age ? ` (${esc(age)})` : '')
    + `, which saw about <b>${esc(seen)}%</b> of the radar box — the rest is unobserved, not empty.`
    + ` One instant, not a track. ${counts[1]} weak ones and ${counts[2] + counts[3]} `
    + 'nearer the shore are off by default.');
}

async function main() {
  const listing = await listAll(TREE);
  // Neither layer waits for, or fails with, the other.
  await Promise.all([
    load(listing).catch(e => {
      say(`Could not draw the map: ${esc(e.message)}. The data is still there — `
        + `<a href="https://huggingface.co/datasets/${REPO}">browse the dataset</a>.`);
      console.error(e);
    }),
    loadSar(listing).catch(e => {
      saySar(`Radar layer did not load: ${esc(e.message)}.`);
      console.error(e);
    }),
  ]);
}

main().catch(e => {
  say(`Could not list the dataset: ${esc(e.message)}. `
    + `<a href="https://huggingface.co/datasets/${REPO}">Browse it directly</a>.`);
  saySar('Radar layer did not load: the dataset could not be listed.');
  console.error(e);
});
