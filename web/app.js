
'use strict';

const $ = (id) => document.getElementById(id);

const state = {
  token: localStorage.getItem('jaynak.token') || null,
  phone: localStorage.getItem('jaynak.phone') || null,
  places: [],
  pt: { pickup: null, dropoff: null },
  center: { lat: 23.59, lon: 58.40 },
  kind: 'errand',
  payment: 'cash',
  dispatch: 'nearby',
  quote: null,
  driver: null,
  isOwner: false,
  challenge: null,
  resendLeft: 0,
  resendTimer: null,
};


async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (body) headers['Content-Type'] = 'application/json';
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;

  let res;
  try {
    res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {


    throw new Error('لا يوجد اتصال — تحقّق من الشبكة وأعد المحاولة');
  }
  let data = {};
  try { data = await res.json(); } catch {  }

  if (res.status === 401) {
    signOut(false);
    throw new Error(data.message || 'انتهت جلستك، سجّل الدخول من جديد');
  }
  if (!res.ok) throw new Error(data.message || 'تعذّر إكمال الطلب');
  return data;
}


const toLatinDigits = (s) => String(s ?? '').replace(/[٠-٩۰-۹]/g,
  (d) => String(d.charCodeAt(0) & 0xf));
const digitsOnly = (s) => toLatinDigits(s).replace(/\D/g, '');

const money = (v) => `${Number(v).toFixed(3)} ر.ع.`;

function toast(text, isError = false) {
  const el = $('toast');
  el.textContent = text;
  el.className = 'toast' + (isError ? ' toast--error' : '');
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3400);
}

function showError(id, message) {
  const el = $(id);
  if (!message) { el.hidden = true; return; }
  el.textContent = message;
  el.hidden = false;
}


async function boot() {
  wireTabs();
  wireAuth();
  wirePayment();
  wireSubmit();
  wireDriver();
  wireTrackAndReg();
  wirePick();

  try {
    const { places, kinds, zones, center } = await api('/api/places');
    state.places = places;
    state.zones = zones;
    state.center = center || { lat: 23.59, lon: 58.40 };
    renderKinds(kinds);
    renderPlaces();
  } catch (e) {
    showError('order-error', e.message);
    return;
  }

  if (state.token) await refreshMe();
  else setAccountLabel(null);
}


const KIND_ICON = { errand: '🛒', parcel: '📦', documents: '📄' };

function renderKinds(kinds) {
  const wrap = $('kinds');
  wrap.replaceChildren(...kinds.map((k) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'kind' + (k.id === state.kind ? ' is-on' : '');
    b.dataset.kind = k.id;
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(k.id === state.kind));

    const ico = document.createElement('span');
    ico.className = 'kind__ico';
    ico.textContent = KIND_ICON[k.id] || '•';
    const label = document.createElement('span');
    label.textContent = k.title;

    b.append(ico, label);
    b.addEventListener('click', () => {
      state.kind = k.id;
      wrap.querySelectorAll('.kind').forEach((n) => {
        const on = n.dataset.kind === k.id;
        n.classList.toggle('is-on', on);
        n.setAttribute('aria-checked', String(on));
      });
      refreshQuote();
    });
    return b;
  }));
}


const OMAN = { lat: [16.0, 27.0], lon: [51.0, 60.5] };
const inOman = (lat, lon) =>
  lat >= OMAN.lat[0] && lat <= OMAN.lat[1] && lon >= OMAN.lon[0] && lon <= OMAN.lon[1];


function kmBetween(lat1, lon1, lat2, lon2) {
  const r = 6371, rad = Math.PI / 180;
  const p1 = lat1 * rad, p2 = lat2 * rad;
  const dp = p2 - p1, dl = (lon2 - lon1) * rad;
  const a = Math.sin(dp / 2) ** 2 +
            Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(a)));
}


function describePoint(lat, lon) {
  let best = null, bestKm = Infinity;
  for (const pl of state.places) {
    const d = kmBetween(lat, lon, pl.lat, pl.lon);
    if (d < bestKm) { bestKm = d; best = pl; }
  }
  if (!best) return { name: 'موقع على الخريطة', sub: '', zone: undefined };
  if (bestKm <= 0.25) return { name: best.name, sub: best.zone, zone: best.zone };
  const far = bestKm < 1 ? `${Math.round(bestKm * 1000)} م` : `${bestKm.toFixed(1)} كم`;
  return { name: `قرب ${best.name}`, sub: `${best.zone} · ${far}`, zone: best.zone };
}

const PT_CAP = { pickup: 'نقطة الاستلام', dropoff: 'نقطة التسليم' };

function renderPlaces() {


  const saved = loadPoints();
  for (const [which, idx] of [['pickup', 0], ['dropoff', 2]]) {
    const pl = state.places[idx];
    state.pt[which] = saved[which] ||
      (pl && { lat: pl.lat, lon: pl.lon, zone: pl.zone, name: pl.name });
    renderPointButton(which);
  }
  refreshQuote();
}

function renderPointButton(which) {
  const p = state.pt[which];
  const name = $(`${which}-name`), sub = $(`${which}-sub`);
  if (!p) { name.textContent = 'اختر على الخريطة'; sub.textContent = ''; return; }
  name.textContent = p.name || 'موقع على الخريطة';
  sub.textContent = p.label || p.sub || p.zone || '';
}

function setPoint(which, p) {
  state.pt[which] = p;
  renderPointButton(which);
  savePoints();
  refreshQuote();
}

const pointFor = (which) => {
  const p = state.pt[which];
  if (!p) return null;

  const name = p.label ? `${p.name} — ${p.label}` : p.name;
  return { lat: p.lat, lon: p.lon, zone: p.zone, name: name.slice(0, 120) };
};


function savePoints() {
  try { localStorage.setItem('jaynak.pts', JSON.stringify(state.pt)); }
  catch {  }
}
function loadPoints() {
  try {
    const raw = JSON.parse(localStorage.getItem('jaynak.pts') || '{}');
    const ok = (p) => p && typeof p.lat === 'number' && typeof p.lon === 'number'
                        && inOman(p.lat, p.lon);
    return { pickup: ok(raw.pickup) ? raw.pickup : null,
             dropoff: ok(raw.dropoff) ? raw.dropoff : null };
  } catch { return { pickup: null, dropoff: null }; }
}


let leafletReady = null;

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  if (leafletReady) return leafletReady;

  leafletReady = new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/web/vendor/leaflet/leaflet.css';
    document.head.append(css);

    const js = document.createElement('script');
    js.src = '/web/vendor/leaflet/leaflet.min.js';
    js.onload = () => resolve();
    js.onerror = () => reject(new Error('تعذّر تحميل ملف الخريطة'));
    document.head.append(js);
  }).catch((e) => { leafletReady = null; throw e; });

  return leafletReady;
}

const pick = { which: 'pickup', map: null, center: null, mode: 'map' };

function openPick(which) {
  pick.which = which;
  $('pick-title').textContent = PT_CAP[which];
  showError('pick-error', '');
  $('pick').hidden = false;

  const cur = state.pt[which];
  $('pick-label').value = (cur && cur.label) || '';
  pick.center = cur ? { lat: cur.lat, lon: cur.lon }
                    : { lat: state.center.lat, lon: state.center.lon };

  renderPickList();
  setPickMode(pick.mode);
  paintPickOut();
}

function closePick() {
  $('pick').hidden = true;
}

function setPickMode(mode) {
  pick.mode = mode;
  $('pick-map-pane').hidden = mode !== 'map';
  $('pick-list').hidden = mode !== 'list';
  $('pick-ok').hidden = mode !== 'map';
  document.querySelectorAll('[data-pick]').forEach((b) => {
    const on = b.dataset.pick === mode;
    b.classList.toggle('is-on', on);
    b.setAttribute('aria-checked', String(on));
  });
  if (mode === 'map') showMap();
}

async function showMap() {
  const hint = $('pick-hint');
  try {
    hint.textContent = 'جاري تحميل الخريطة…';
    await loadLeaflet();
    hint.textContent = 'اسحب الخريطة حتى يستقرّ الدبّوس على الموقع بالضبط.';
  } catch (e) {

    hint.textContent = '';
    showError('pick-error', `${e.message} — استخدم «من الأماكن».`);
    setPickMode('list');
    return;
  }
  buildMap();
}

function buildMap() {
  const { lat, lon } = pick.center;

  if (!pick.map) {
    pick.map = L.map('pick-map', {
      center: [lat, lon], zoom: 15, zoomControl: true,
      attributionControl: true,
    });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, minZoom: 7,

      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(pick.map);

    const wrap = document.querySelector('.mapwrap');
    pick.map.on('movestart zoomstart', () => wrap.classList.add('is-moving'));
    pick.map.on('moveend zoomend', () => {
      wrap.classList.remove('is-moving');
      const c = pick.map.getCenter();
      pick.center = { lat: c.lat, lon: c.lng };
      paintPickOut();
    });
  } else {
    pick.map.setView([lat, lon], Math.max(pick.map.getZoom(), 15));
  }


  requestAnimationFrame(() => pick.map.invalidateSize());
}

function paintPickOut() {
  const { lat, lon } = pick.center;
  const d = describePoint(lat, lon);
  $('pick-addr').textContent = d.name;
  const coord = $('pick-coord');
  coord.replaceChildren();
  if (d.sub) coord.append(`${d.sub} · `);
  const nums = document.createElement('bdi');
  nums.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  coord.append(nums);
  $('pick-ok').disabled = !inOman(lat, lon);
  if (!inOman(lat, lon)) {
    showError('pick-error', 'هذا الموقع خارج سلطنة عمان — لا نخدمه.');
  } else {
    showError('pick-error', '');
  }
}

function confirmPick() {
  const { lat, lon } = pick.center;
  if (!inOman(lat, lon)) return;
  const d = describePoint(lat, lon);
  setPoint(pick.which, {
    lat: Number(lat.toFixed(6)), lon: Number(lon.toFixed(6)),
    zone: d.zone, name: d.name, sub: d.sub,
    label: $('pick-label').value.trim() || '',
  });
  closePick();
}

function renderPickList() {
  $('pick-list').replaceChildren(...state.places.map((pl) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'placebtn';

    const txt = document.createElement('span');
    const name = document.createElement('b');
    name.textContent = pl.name;
    const zone = document.createElement('small');
    zone.textContent = pl.details ? `${pl.zone} · ${pl.details}` : pl.zone;
    txt.append(name, document.createElement('br'), zone);

    const go = document.createElement('small');
    go.textContent = 'اختر';
    go.style.color = 'var(--green)';

    b.append(txt, go);
    b.addEventListener('click', () => {
      setPoint(pick.which, {
        lat: pl.lat, lon: pl.lon, zone: pl.zone, name: pl.name, sub: pl.zone,
        label: $('pick-label').value.trim() || '',
      });
      closePick();
    });
    return b;
  }));
}


function useMyLocation() {
  const btn = $('pick-here');
  if (!navigator.geolocation) {
    return showError('pick-error', 'متصفّحك لا يدعم تحديد الموقع — اسحب الخريطة يدوياً.');
  }
  if (!window.isSecureContext) {
    return showError('pick-error',
      'تحديد الموقع يحتاج اتصالاً مشفَّراً (https). اسحب الخريطة يدوياً، '
      + 'أو افتح الموقع على https ليعمل الزرّ.');
  }

  btn.disabled = true;
  btn.textContent = '◎ جاري التحديد…';
  const done = () => { btn.disabled = false; btn.textContent = '◎ موقعي الحالي'; };

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      done();
      const { latitude: lat, longitude: lon } = pos.coords;
      if (!inOman(lat, lon)) {
        return showError('pick-error', 'موقعك الحالي خارج سلطنة عمان.');
      }
      showError('pick-error', '');
      pick.center = { lat, lon };
      if (pick.map) pick.map.setView([lat, lon], 17);
      paintPickOut();
    },
    (err) => {
      done();
      const why = {
        1: 'رفضت الإذن بالموقع. اسمح به من إعدادات المتصفّح، أو اسحب الخريطة يدوياً.',
        2: 'تعذّر تحديد موقعك — لا إشارة كافية. اسحب الخريطة يدوياً.',
        3: 'انتهت مدّة المحاولة. أعد المحاولة أو اسحب الخريطة يدوياً.',
      };
      showError('pick-error', why[err.code] || 'تعذّر تحديد موقعك.');
    },
    { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
  );
}

function wirePick() {
  $('pickup-btn').addEventListener('click', () => openPick('pickup'));
  $('dropoff-btn').addEventListener('click', () => openPick('dropoff'));
  $('pick-close').addEventListener('click', closePick);
  $('pick-ok').addEventListener('click', confirmPick);
  $('pick-here').addEventListener('click', useMyLocation);

  document.querySelectorAll('[data-pick]').forEach((b) => {
    b.addEventListener('click', () => setPickMode(b.dataset.pick));
  });


  $('pick').addEventListener('click', (e) => { if (e.target === $('pick')) closePick(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('pick').hidden) closePick();
  });

  $('swap').addEventListener('click', () => {
    const { pickup, dropoff } = state.pt;
    state.pt.pickup = dropoff;
    state.pt.dropoff = pickup;
    renderPointButton('pickup');
    renderPointButton('dropoff');
    savePoints();
    refreshQuote();
  });
}


let quoteSeq = 0;

async function refreshQuote() {
  const pickup = pointFor('pickup');
  const dropoff = pointFor('dropoff');
  if (!pickup || !dropoff) return;


  if (kmBetween(pickup.lat, pickup.lon, dropoff.lat, dropoff.lon) < 0.05) {
    state.quote = null;
    $('quote-total').textContent = '—';
    $('quote-meta').textContent = 'النقطتان في نفس المكان — اختر نقطتين مختلفتين';
    $('quote-details').hidden = true;
    $('submit').disabled = true;
    return;
  }


  const seq = ++quoteSeq;
  $('quote').classList.add('is-loading');
  $('quote-meta').textContent = 'جاري حساب السعر…';

  try {
    const q = await api('/api/quote', {
      method: 'POST',
      body: { kind: state.kind, pickup, dropoff },
    });
    if (seq !== quoteSeq) return;

    state.quote = q;
    $('quote-total').textContent = money(q.total);
    $('quote-meta').textContent =
      `${q.km} كم · ${q.minutes} دقيقة${q.isPeak ? ' · وقت ذروة' : ''}`;

    const rows = [
      ['السعر الأساسي', q.base],
      ['المسافة', q.distance],
      ['الوقت', q.time],
    ];
    if (q.zoneSurcharge > 0) rows.push(['رسم الولاية', q.zoneSurcharge]);
    if (q.kindAdjustment !== 0) rows.push(['نوع الطلب', q.kindAdjustment]);
    if (q.surge > 0) rows.push(['ذروة', q.surge]);
    if (q.discount > 0) rows.push(['خصم', -q.discount]);

    $('quote-rows').replaceChildren(...rows.map(([k, v]) => {
      const div = document.createElement('div');
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = money(v);
      div.append(dt, dd);
      return div;
    }));
    $('quote-details').hidden = false;
    $('submit').disabled = false;
    showError('order-error', '');
  } catch (e) {
    if (seq !== quoteSeq) return;
    $('quote-total').textContent = '—';
    $('quote-meta').textContent = e.message;
    $('submit').disabled = true;
  } finally {
    if (seq === quoteSeq) $('quote').classList.remove('is-loading');
  }
}


const DISPATCH_HINT = {
  nearby: 'يوصل لأقرب ٣ مندوبين في نطاق ٣ كم، وأول من يقبل يثبت عليه.',
  open: 'يُعرض على كل المندوبين فوراً — أنسب للمشاوير الطويلة بين الولايات.',
};

function wirePayment() {
  document.querySelectorAll('[data-pay]').forEach((b) => {
    b.addEventListener('click', () => {
      state.payment = b.dataset.pay;
      document.querySelectorAll('[data-pay]').forEach((n) =>
        n.classList.toggle('is-on', n === b));
    });
  });
  document.querySelectorAll('[data-dispatch]').forEach((b) => {
    b.addEventListener('click', () => {
      state.dispatch = b.dataset.dispatch;
      document.querySelectorAll('[data-dispatch]').forEach((n) =>
        n.classList.toggle('is-on', n === b));
      $('dispatch-hint').textContent = DISPATCH_HINT[state.dispatch];
    });
  });
}


function wireSubmit() {
  $('submit').addEventListener('click', async () => {
    if (!state.token) { openAuth(); return; }
    const btn = $('submit');
    btn.classList.add('is-busy');
    btn.disabled = true;
    showError('order-error', '');
    try {
      const { order, offered } = await api('/api/orders', {
        method: 'POST',
        body: {
          kind: state.kind,
          pickup: pointFor('pickup'),
          dropoff: pointFor('dropoff'),
          payment: state.payment,
          dispatchMode: state.dispatch,
          note: $('note').value,
        },
      });
      $('note').value = '';
      toast(offered > 0
        ? `تم الطلب ${order.ref} — عُرض على ${offered} مندوب`
        : `تم الطلب ${order.ref} — لا مندوب متصل الآن، سيُعرض عند توفّره`);
      switchView('orders');
      loadOrders();
    } catch (e) {
      showError('order-error', e.message);
    } finally {
      btn.classList.remove('is-busy');
      btn.disabled = false;
    }
  });
}


const STATUS = {
  searching: 'يُبحث عن مندوب', accepted: 'قَبِلَه مندوب',
  pickedUp: 'في الطريق', delivered: 'تم التسليم', cancelled: 'ملغى',
};

async function loadOrders() {
  const list = $('orders-list');
  if (!state.token) {
    list.replaceChildren(emptyState('🔐', 'سجّل الدخول', 'لتشوف طلباتك وتتابعها'));
    return;
  }
  try {
    const { orders } = await api('/api/orders');
    if (!orders.length) {
      list.replaceChildren(emptyState('📭', 'لا طلبات بعد',
        'أول طلب تسويه يظهر هنا مع حالته'));
      return;
    }
    list.replaceChildren(...orders.map(orderCard));
  } catch (e) {
    list.replaceChildren(emptyState('⚠️', 'تعذّر التحميل', e.message));
  }
}

function orderCard(o) {
  const card = document.createElement('article');
  card.className = 'order';

  const top = document.createElement('div');
  top.className = 'order__top';
  const ref = document.createElement('span');
  ref.className = 'order__ref'; ref.textContent = o.ref;
  const total = document.createElement('span');
  total.className = 'order__total'; total.textContent = money(o.price.total);
  top.append(ref, total);

  const route = document.createElement('div');
  route.className = 'order__route';
  const from = document.createElement('b'); from.textContent = o.pickup.name || '—';
  const to = document.createElement('b'); to.textContent = o.dropoff.name || '—';
  route.append(from, document.createTextNode('  ←  '), to);

  const badge = document.createElement('span');
  badge.className = `badge badge--${o.status}`;
  badge.textContent = STATUS[o.status] || o.status;

  card.append(top, route, badge);
  card.style.cursor = 'pointer';
  card.addEventListener('click', () => openTrack(o.id));
  return card;
}

function regPrompt(title, text) {
  const wrap = document.createElement('div');
  wrap.className = 'stack';
  wrap.append(emptyState('📋', title, text));
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'cta'; btn.textContent = 'ابدأ التسجيل';
  btn.addEventListener('click', openReg);
  wrap.append(btn);
  return wrap;
}

function emptyState(ico, title, text) {
  const d = document.createElement('div');
  d.className = 'empty';
  const i = document.createElement('div'); i.className = 'empty__ico'; i.textContent = ico;
  const b = document.createElement('b'); b.textContent = title;
  const p = document.createElement('span'); p.textContent = text;
  d.append(i, b, p);
  return d;
}


function wireTabs() {
  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => switchView(t.dataset.view));
  });
}

function switchView(name) {
  ['order', 'orders', 'wallet', 'driver', 'admin'].forEach((v) => {
    $(`view-${v}`).hidden = v !== name;
  });
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('is-on', t.dataset.view === name));


  clearInterval(driverPoll);
  if (name === 'orders') loadOrders();
  if (name === 'wallet') loadWallet();
  if (name === 'admin') loadAdmin();
  if (name === 'driver') {
    loadDriver();
    driverPoll = setInterval(loadDriver, 8000);
  }
  window.scrollTo({ top: 0, behavior: 'instant' });
}


let driverPoll = null;

async function loadDriver() {
  if (!state.token) {
    $('drv-status').textContent = 'سجّل الدخول لتعمل كمندوب';
    $('drv-toggle').hidden = true;
    $('drv-offers').replaceChildren(
      emptyState('🔐', 'سجّل الدخول', 'ثم تظهر لك الطلبات المتاحة'));
    return;
  }
  try {
    const f = await api('/api/driver/feed');
    state.driver = f.driver;
    renderDriver(f);
  } catch (e) {
    $('drv-status').textContent = e.message;
  }
}

const APPROVAL = {
  pending: 'بانتظار موافقة الإدارة',
  approved: 'حساب معتمد',
  rejected: 'الحساب مرفوض',
  suspended: 'الحساب موقوف',
};

function renderDriver(feed) {
  const { driver, active, offers, board, floor } = feed;
  const toggle = $('drv-toggle');
  if (!driver) {
    $('drv-status').textContent = 'لا حساب مندوب بهذا الرقم';
    toggle.hidden = true;
    $('drv-offers').replaceChildren(regPrompt('سجّل كمندوب',
      'بياناتك ومركبتك ومستنداتك، ثم موافقة الإدارة'));
    return;
  }
  toggle.hidden = false;

  const online = driver.presence === 'online';
  toggle.textContent = online ? 'متصل' : 'غير متصل';
  toggle.classList.toggle('is-online', online);
  $('drv-status').textContent = APPROVAL[driver.approval] || driver.approval;

  const bal = $('drv-balance');
  bal.textContent = money(driver.earningsBalance);
  bal.classList.toggle('is-debt', driver.earningsBalance < 0);
  $('drv-done').textContent = driver.completedOrders;


  const warn = $('drv-warn');
  if (driver.earningsBalance < floor) {
    warn.textContent = `رصيدك تحت الحدّ (${money(floor)}) — الطلبات موقوفة حتى تعبّئ محفظتك`;
    warn.hidden = false;
  } else if (driver.earningsBalance < floor / 2) {
    warn.textContent = `اقترب رصيدك من الحدّ (${money(floor)}) — عبّئ محفظتك قبل التوقّف`;
    warn.hidden = false;
  } else warn.hidden = true;

  renderTier(feed.tier);
  renderDestination(feed);

  $('drv-active-wrap').hidden = !active;
  if (active) $('drv-active').replaceChildren(activeCard(active));

  if (driver.approval === 'rejected' || driver.approval === 'pending') {
    $('drv-offers').replaceChildren(regPrompt(
      driver.approval === 'rejected' ? 'أعد إرسال بياناتك' : 'أكمل تسجيلك',
      driver.approval === 'rejected'
        ? 'راجع المستندات وأرسلها من جديد'
        : 'إن لم ترسل مستنداتك بعد، أرسلها ليُراجع حسابك'));
    return;
  }

  const list = $('drv-offers');
  if (active) {
    list.replaceChildren(emptyState('🚗', 'عندك طلب جارٍ',
      'أكمله ليظهر لك المتاح من جديد'));
  } else if (!offers.length) {
    list.replaceChildren(emptyState(online ? '⏳' : '💤',
      online ? 'لا طلبات قريبة الآن' : 'أنت غير متصل',
      online ? 'يظهر الطلب هنا لحظة وصوله' : 'اضغط «غير متصل» لتبدأ الاستلام'));
  } else {
    list.replaceChildren(...offers.map(offerCard));
  }

  const boardWrap = $('drv-board-wrap');
  boardWrap.hidden = active || !board || !board.length;
  if (!boardWrap.hidden) {
    $('drv-board').replaceChildren(...board.map(offerCard));
  }
}


function renderTier(tier) {
  const card = $('drv-tier-card');
  if (!tier) { card.hidden = true; return; }
  card.hidden = false;
  $('drv-tier').textContent = `مندوب ${tier.title}`;
  $('drv-tier-badge').textContent = `${tier.points} نقطة`;

  const parts = [`عمولتك ${money(tier.commission)} للطلب`];
  if (tier.rating) parts.push(`تقييمك ${tier.rating} من ٥`);
  if (tier.nextTitle) {
    parts.push(`${tier.nextAt - tier.points} نقطة لرتبة ${tier.nextTitle}`);
  }
  $('drv-tier-sub').textContent = parts.join(' · ');
}


function renderDestination(feed) {
  const sel = $('drv-dest');
  if (!sel.options.length) {
    const none = document.createElement('option');
    none.value = ''; none.textContent = 'بلا وجهة';
    sel.replaceChildren(none, ...state.places.map((p, i) => {
      const o = document.createElement('option');
      o.value = String(i); o.textContent = `${p.name} — ${p.zone}`;
      return o;
    }));
  }
  const dest = feed.destination;
  sel.value = dest ? String(state.places.findIndex((p) => p.name === dest.name)) : '';
  $('drv-dest-state').textContent = dest
    ? `متجه إلى ${dest.name} — تستلم ما يقع على مسارك`
    : 'بلا وجهة — تستلم ما حولك فقط';
  const radius = $('drv-radius');
  if (document.activeElement !== radius) {
    radius.value = Number(feed.acceptRadiusKm ?? 3).toFixed(1);
  }
}

async function saveDestination(body) {
  try {
    await api('/api/driver/destination', { method: 'POST', body });
    loadDriver();
  } catch (e) {
    toast(e.message, true);
    loadDriver();
  }
}

function offerCard(o) {
  const card = document.createElement('article');
  card.className = 'order';

  const top = document.createElement('div');
  top.className = 'order__top';
  const km = document.createElement('span');
  km.className = 'offer__km';
  const kmNum = document.createElement('b');
  kmNum.textContent = `${o.km} كم`;
  km.append(kmNum, document.createTextNode(' للاستلام'));
  const total = document.createElement('span');
  total.className = 'order__total'; total.textContent = money(o.price.driverPayout);
  top.append(km, total);

  const route = document.createElement('div');
  route.className = 'order__route';
  const a = document.createElement('b'); a.textContent = o.pickup.name || '—';
  const b = document.createElement('b'); b.textContent = o.dropoff.name || '—';
  route.append(a, document.createTextNode('  ←  '), b);

  const row = document.createElement('div');
  row.className = 'offer__row';
  const accept = document.createElement('button');
  accept.type = 'button'; accept.className = 'cta'; accept.textContent = 'اقبل';
  accept.addEventListener('click', () => acceptOffer(o.id, accept));
  const skip = document.createElement('button');
  skip.type = 'button'; skip.className = 'ghost'; skip.textContent = 'تخطَّ';
  skip.addEventListener('click', () => declineOffer(o.id, skip));
  row.append(accept, skip);

  card.append(top, route);
  if (o.onPath) {
    const tag = document.createElement('span');
    tag.className = 'onpath'; tag.textContent = '↗ على مسارك';
    card.append(tag);
  }
  card.append(row);
  return card;
}

async function declineOffer(id, btn) {
  btn.disabled = true;
  try {
    await api('/api/driver/decline', { method: 'POST', body: { orderID: id } });
    loadDriver();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

const NEXT_LABEL = { accepted: 'استلمت الطلب', pickedUp: 'سلّمت الطلب' };

function activeCard(o) {
  const card = document.createElement('article');
  card.className = 'order';

  const top = document.createElement('div');
  top.className = 'order__top';
  const ref = document.createElement('span');
  ref.className = 'order__ref'; ref.textContent = o.ref;
  const total = document.createElement('span');
  total.className = 'order__total'; total.textContent = money(o.price.driverPayout);
  top.append(ref, total);

  const route = document.createElement('div');
  route.className = 'order__route';
  const a = document.createElement('b'); a.textContent = o.pickup.name || '—';
  const b = document.createElement('b'); b.textContent = o.dropoff.name || '—';
  route.append(a, document.createTextNode('  ←  '), b);

  card.append(top, route);

  if (o.note) {
    const note = document.createElement('div');
    note.className = 'order__route'; note.textContent = `ملاحظة: ${o.note}`;
    card.append(note);
  }


  if (o.customerPhone) {
    const line = document.createElement('div');
    line.className = 'phoneline';
    const label = document.createElement('span'); label.textContent = 'رقم العميل';
    const link = document.createElement('a');
    link.href = `tel:+${o.customerPhone}`;
    link.textContent = `+${o.customerPhone}`;
    line.append(label, link);
    card.append(line);
  }

  const label = NEXT_LABEL[o.status];
  if (label) {
    const row = document.createElement('div');
    row.className = 'offer__row';
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'cta'; btn.textContent = label;
    btn.addEventListener('click', () => advance(o.id, btn));
    row.append(btn);
    card.append(row);
  }
  return card;
}

async function acceptOffer(id, btn) {
  btn.classList.add('is-busy'); btn.disabled = true;
  try {
    const r = await api('/api/driver/accept', { method: 'POST', body: { orderID: id } });
    toast(`قبلت ${r.order.ref}`);
    loadDriver();
  } catch (e) {
    toast(e.message, true);
    loadDriver();
  } finally {
    btn.classList.remove('is-busy'); btn.disabled = false;
  }
}

async function advance(id, btn) {
  btn.classList.add('is-busy'); btn.disabled = true;
  try {
    const r = await api('/api/driver/advance', { method: 'POST', body: { orderID: id } });
    toast(r.status === 'delivered' ? 'تم التسليم' : 'تم الاستلام');
    if (r.note) setTimeout(() => toast(r.note, true), 1200);
    loadDriver();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.classList.remove('is-busy'); btn.disabled = false;
  }
}

function wireDriver() {
  $('drv-dest').addEventListener('change', (e) => {
    const v = e.target.value;
    saveDestination(v === '' ? { clear: true } : { placeIndex: Number(v) });
  });
  $('drv-dest-clear').addEventListener('click', () => saveDestination({ clear: true }));
  $('drv-radius').addEventListener('change', (e) => {
    const v = Number(toLatinDigits(e.target.value).replace(/[^\d.]/g, ''));
    if (!Number.isFinite(v)) { loadDriver(); return; }
    saveDestination({ acceptRadiusKm: v });
  });

  $('drv-toggle').addEventListener('click', async () => {
    const want = state.driver?.presence === 'online' ? 'offline' : 'online';
    const btn = $('drv-toggle');
    btn.disabled = true;
    try {
      await api('/api/driver/presence', { method: 'POST', body: { presence: want } });
      toast(want === 'online' ? 'أنت متصل الآن' : 'خرجت من الاتصال');
      loadDriver();
    } catch (e) {
      toast(e.message, true);
      loadDriver();
    } finally {
      btn.disabled = false;
    }
  });
}


function setAccountLabel(phone) {
  const el = $('account-label');
  el.textContent = phone ? `••${String(phone).slice(-4)}` : 'دخول';

  el.dir = phone ? 'ltr' : 'auto';
}

async function refreshMe() {
  try {
    const me = await api('/api/me');
    state.phone = me.phone;
    state.isOwner = !!me.isOwner;
    localStorage.setItem('jaynak.phone', me.phone);
    setAccountLabel(me.phone);


    document.querySelector('.tab[data-view="admin"]').hidden = !me.isOwner;
  } catch {
    setAccountLabel(null);
    document.querySelector('.tab[data-view="admin"]').hidden = true;
  }
}

function openAuth() {
  $('auth').hidden = false;
  $('auth-step-phone').hidden = false;
  $('auth-step-code').hidden = true;
  showError('auth-error', '');
  showError('code-error', '');
  $('dev-code').hidden = true;
  setTimeout(() => $('phone').focus(), 80);
}

function closeAuth() {
  $('auth').hidden = true;
  clearInterval(state.resendTimer);
}

function signOut(notify = true) {


  if (state.token) {
    fetch('/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${state.token}` },
      keepalive: true,
    }).catch(() => {  });
  }
  state.token = null;
  state.phone = null;
  localStorage.removeItem('jaynak.token');
  localStorage.removeItem('jaynak.phone');
  state.isOwner = false;
  setAccountLabel(null);
  document.querySelector('.tab[data-view="admin"]').hidden = true;
  if (notify) toast('خرجت من حسابك');
}

function wireAuth() {
  $('account-btn').addEventListener('click', () => {
    if (state.token) {
      if (confirm('تسجيل الخروج؟')) { signOut(); loadOrders(); }
    } else openAuth();
  });
  $('auth-close').addEventListener('click', closeAuth);
  $('auth').addEventListener('click', (e) => { if (e.target === $('auth')) closeAuth(); });


  const phone = $('phone');
  phone.addEventListener('input', () => {
    const d = digitsOnly(phone.value).slice(0, 8);
    phone.value = d.length > 4 ? `${d.slice(0, 4)} ${d.slice(4)}` : d;
  });
  phone.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendCode(); });

  $('send-code').addEventListener('click', sendCode);
  $('verify-code').addEventListener('click', verifyCode);
  $('resend').addEventListener('click', sendCode);

  buildOtpBoxes();
}

async function sendCode() {
  const raw = digitsOnly($('phone').value);
  if (raw.length !== 8) {
    return showError('auth-error', 'رقم الهاتف يجب أن يكون ٨ أرقام');
  }
  const btn = $('send-code');
  btn.classList.add('is-busy'); btn.disabled = true;
  showError('auth-error', '');
  try {
    const r = await api('/auth/request-otp', { method: 'POST', body: { phone: raw } });
    state.challenge = raw;
    $('auth-step-phone').hidden = true;
    $('auth-step-code').hidden = false;
    $('code-sent-to').textContent = `أُرسل إلى +968 ${raw}`;

    if (r.dev_code) {
      $('dev-code').hidden = false;
      $('dev-code').replaceChildren(
        document.createTextNode('وضع التجربة — الرمز: '),
        Object.assign(document.createElement('b'), { textContent: r.dev_code }));
    }
    startResend(r.resend_after || 45);
    otpBoxes()[0].focus();
  } catch (e) {
    showError('auth-error', e.message);
  } finally {
    btn.classList.remove('is-busy'); btn.disabled = false;
  }
}

function startResend(seconds) {
  clearInterval(state.resendTimer);
  state.resendLeft = seconds;
  const btn = $('resend');
  const tick = () => {
    if (state.resendLeft <= 0) {
      clearInterval(state.resendTimer);
      btn.disabled = false;
      btn.textContent = 'إعادة إرسال الرمز';
      return;
    }
    btn.disabled = true;
    btn.textContent = `إعادة الإرسال بعد ${state.resendLeft} ثانية`;
    state.resendLeft -= 1;
  };
  tick();
  state.resendTimer = setInterval(tick, 1000);
}

const otpBoxes = () => Array.from($('otp').querySelectorAll('input'));

function buildOtpBoxes() {
  const wrap = $('otp');
  wrap.replaceChildren(...Array.from({ length: 4 }, (_, i) => {
    const el = document.createElement('input');
    el.type = 'text';
    el.inputMode = 'numeric';
    el.autocomplete = i === 0 ? 'one-time-code' : 'off';
    el.maxLength = 1;
    el.setAttribute('aria-label', `الرقم ${i + 1}`);

    el.addEventListener('input', () => {
      const d = digitsOnly(el.value);
      el.value = d.slice(-1);
      el.classList.toggle('is-filled', !!el.value);
      if (el.value && i < 3) otpBoxes()[i + 1].focus();
      if (otpBoxes().every((b) => b.value)) verifyCode();
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !el.value && i > 0) otpBoxes()[i - 1].focus();
      if (e.key === 'Enter') verifyCode();
    });

    el.addEventListener('paste', (e) => {
      e.preventDefault();
      const d = digitsOnly(e.clipboardData.getData('text')).slice(0, 4);
      otpBoxes().forEach((b, j) => {
        b.value = d[j] || '';
        b.classList.toggle('is-filled', !!b.value);
      });
      if (d.length === 4) verifyCode();
    });
    return el;
  }));
}

let verifying = false;

async function verifyCode() {
  if (verifying) return;
  const code = otpBoxes().map((b) => b.value).join('');
  if (code.length !== 4) return showError('code-error', 'أدخل الرمز كاملاً');

  verifying = true;
  const btn = $('verify-code');
  btn.classList.add('is-busy'); btn.disabled = true;
  showError('code-error', '');
  try {
    const r = await api('/auth/verify-otp', {
      method: 'POST',
      body: { phone: state.challenge, code },
    });
    state.token = r.token;
    localStorage.setItem('jaynak.token', r.token);
    await refreshMe();
    closeAuth();
    toast('أهلاً بك في جاينك');
    if (state.quote) $('submit').disabled = false;
  } catch (e) {
    showError('code-error', e.message);
    otpBoxes().forEach((b) => { b.value = ''; b.classList.remove('is-filled'); });
    otpBoxes()[0].focus();
  } finally {
    verifying = false;
    btn.classList.remove('is-busy'); btn.disabled = false;
  }
}


const LEVELS = [
  [200, 'عضو جديد'], [800, 'عضو فضي'], [2500, 'عضو ذهبي'], [Infinity, 'عضو مميّز'],
];

async function loadWallet() {
  if (!state.token) {
    $('w-balance').textContent = '—';
    $('w-points').textContent = '—';
    $('w-rewards').replaceChildren(
      emptyState('🔐', 'سجّل الدخول', 'لتشوف رصيدك ونقاطك'));
    return;
  }
  try {
    const w = await api('/api/wallet');
    $('w-balance').textContent = money(w.balance);
    $('w-points').textContent = String(w.points);

    const [next, title] = LEVELS.find(([n]) => w.lifetimePoints < n);
    $('w-level').textContent = Number.isFinite(next)
      ? `${title} — ${next - w.lifetimePoints} نقطة للمستوى التالي`
      : title;

    $('w-vouchers-wrap').hidden = !w.vouchers.length;
    $('w-vouchers').replaceChildren(...w.vouchers.map((v) => {
      const d = document.createElement('div');
      d.className = 'voucher';
      d.textContent = `${v.rewardTitle} — جاهزة للاستخدام`;
      return d;
    }));

    $('w-rewards').replaceChildren(...w.rewards.map((r) => rewardCard(r, w.points)));
  } catch (e) {
    $('w-rewards').replaceChildren(emptyState('⚠️', 'تعذّر التحميل', e.message));
  }
}

function rewardCard(r, points) {
  const enough = points >= r.cost;
  const card = document.createElement('div');
  card.className = 'reward' + (enough ? '' : ' is-locked');

  const text = document.createElement('div');
  text.className = 'reward__text';
  const t = document.createElement('div');
  t.className = 'reward__title'; t.textContent = r.title;
  const d = document.createElement('div');
  d.className = 'reward__detail'; d.textContent = r.detail;
  text.append(t, d);

  const cost = document.createElement('span');
  cost.className = 'reward__cost'; cost.textContent = `${r.cost} نقطة`;

  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'ghost';
  btn.textContent = enough ? 'استبدل' : 'غير كافٍ';
  btn.disabled = !enough;
  btn.addEventListener('click', () => redeem(r.id, btn));

  card.append(text, cost, btn);
  return card;
}

async function redeem(id, btn) {
  btn.disabled = true;
  try {
    const r = await api('/api/rewards/redeem', { method: 'POST', body: { rewardID: id } });
    toast(r.kind === 'credit' ? `أُضيف ${r.title} لمحفظتك` : `حصلت على ${r.title}`);
    loadWallet();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}


const STAT_LABELS = [
  ['ordersToday', 'طلبات اليوم'], ['revenueToday', 'إيراد اليوم'],
  ['commissionToday', 'عمولة اليوم'], ['live', 'طلبات حيّة'],
  ['online', 'مندوبون متصلون'], ['pending', 'بانتظار الموافقة'],
];
const MONEY_STATS = new Set(['revenueToday', 'commissionToday']);

const RULE_FIELDS = [
  ['baseFare', 'السعر الأساسي'], ['perKm', 'سعر الكيلومتر'],
  ['perMinute', 'سعر الدقيقة'], ['minimumFare', 'الحدّ الأدنى للطلب'],
  ['fixedCommission', 'العمولة لكل طلب'], ['minEarningsBalance', 'حدّ الرصيد السالب'],
];

async function loadAdmin() {
  try {
    const a = await api('/api/admin/overview');
    $('a-stats').replaceChildren(...STAT_LABELS.map(([k, label]) => {
      const d = document.createElement('div');
      d.className = 'stat';
      const kk = document.createElement('span');
      kk.className = 'stat__k'; kk.textContent = label;
      const v = document.createElement('b');
      v.className = 'stat__v';
      v.textContent = MONEY_STATS.has(k) ? money(a.stats[k]) : String(a.stats[k]);
      d.append(kk, v);
      return d;
    }));
    $('a-payout').textContent = `أقرب يوم صرف: ${a.nextPayout}`;

    $('a-live').replaceChildren(...(a.live.length
      ? a.live.map(liveRow)
      : [emptyState('✅', 'لا طلبات حيّة', 'كل الطلبات مكتملة')]));

    $('a-drivers').replaceChildren(...(a.drivers.length
      ? a.drivers.map(driverRow)
      : [emptyState('🚗', 'لا مندوبون بعد', 'يظهرون هنا بعد التسجيل')]));

    renderRules(a.rules);
  } catch (e) {
    $('a-stats').replaceChildren();
    $('a-live').replaceChildren(emptyState('🔒', 'غير مسموح', e.message));
  }
}

function liveRow(o) {
  const row = document.createElement('div');
  row.className = 'arow';
  const text = document.createElement('div');
  text.className = 'arow__text';
  const t = document.createElement('div');
  t.className = 'arow__t'; t.textContent = `${o.ref} · ${STATUS[o.status] || o.status}`;
  const sub = document.createElement('div');
  sub.className = 'arow__s';
  sub.textContent = `${o.pickup.name || '—'} ← ${o.dropoff.name || '—'}`;
  text.append(t, sub);
  const n = document.createElement('span');
  n.className = 'arow__n'; n.textContent = money(o.price.total);
  row.append(text, n);
  return row;
}

function driverRow(d) {
  const row = document.createElement('div');
  row.className = 'arow';

  const text = document.createElement('div');
  text.className = 'arow__text';
  const t = document.createElement('div');
  t.className = 'arow__t'; t.textContent = d.name;
  const sub = document.createElement('div');
  sub.className = 'arow__s';
  sub.textContent = `${d.zone} · ${APPROVAL[d.approval] || d.approval}`
    + (d.presence === 'online' ? ' · متصل' : '')
    + ` · ${d.completedOrders} توصيلة`;
  text.append(t, sub);

  const n = document.createElement('span');
  n.className = 'arow__n' + (d.earningsBalance < 0 ? ' is-debt' : '');
  n.textContent = money(d.earningsBalance);

  row.append(text, n);


  if (d.approval !== 'approved') {
    const ok = document.createElement('button');
    ok.type = 'button'; ok.className = 'ghost'; ok.textContent = 'اعتمد';
    ok.addEventListener('click', () => setApproval(d.id, 'approved', ok));
    row.append(ok);
  } else {
    const stop = document.createElement('button');
    stop.type = 'button'; stop.className = 'ghost'; stop.textContent = 'علّق';
    stop.addEventListener('click', () => setApproval(d.id, 'suspended', stop));
    row.append(stop);
  }
  return row;
}

async function setApproval(id, approval, btn) {
  btn.disabled = true;
  try {
    const r = await api('/api/admin/approve', {
      method: 'POST', body: { driverID: id, approval },
    });
    toast(`${r.name}: ${APPROVAL[r.approval] || r.approval}`);
    loadAdmin();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

function renderRules(rules) {
  $('a-rules').replaceChildren(...RULE_FIELDS.map(([key, label]) => {
    const row = document.createElement('div');
    row.className = 'rule';
    const l = document.createElement('span');
    l.className = 'rule__label'; l.textContent = label;
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'decimal';
    input.value = Number(rules[key]).toFixed(3);
    input.setAttribute('aria-label', label);


    input.addEventListener('change', () => saveRule(key, input));
    row.append(l, input);
    return row;
  }));
}

async function saveRule(key, input) {
  const raw = toLatinDigits(input.value).replace(/[^\d.\-]/g, '');
  const value = Number(raw);
  const msg = $('a-rules-msg');
  if (!Number.isFinite(value)) {
    msg.textContent = 'قيمة غير رقمية';
    msg.hidden = false;
    return;
  }
  input.disabled = true;
  try {
    await api('/api/admin/rules', { method: 'POST', body: { [key]: value } });
    msg.hidden = true;
    input.value = value.toFixed(3);
    toast('حُفظ');
  } catch (e) {
    msg.textContent = e.message;
    msg.hidden = false;
  } finally {
    input.disabled = false;
  }
}


const STEP_LABEL = {
  searching: 'يُبحث', accepted: 'قُبل', pickedUp: 'في الطريق', delivered: 'سُلّم',
};

async function openTrack(id) {
  $('track').hidden = false;
  $('track-ref').textContent = 'جاري التحميل…';
  $('track-steps').replaceChildren();
  $('track-body').replaceChildren();
  try {
    const o = await api(`/api/orders/${encodeURIComponent(id)}`);
    renderTrack(o);
  } catch (e) {
    $('track-ref').textContent = 'تعذّر التحميل';
    $('track-body').replaceChildren(emptyState('⚠️', 'خطأ', e.message));
  }
}

function renderTrack(o) {
  $('track-ref').textContent = `${o.ref} · ${STATUS[o.status] || o.status}`;


  $('track-steps').replaceChildren(...(o.steps || []).map((st, i) => {
    const d = document.createElement('div');
    d.className = 'step'
      + (i <= o.stepIndex ? ' is-done' : '')
      + (i === o.stepIndex ? ' is-now' : '');
    const bar = document.createElement('div'); bar.className = 'step__bar';
    const lab = document.createElement('div');
    lab.className = 'step__label'; lab.textContent = STEP_LABEL[st] || st;
    d.append(bar, lab);
    return d;
  }));

  const body = [];
  const kv = (k, v, mono = false) => {
    const row = document.createElement('div');
    row.className = 'kv';
    const kk = document.createElement('span');
    kk.className = 'kv__k'; kk.textContent = k;
    const vv = document.createElement('span');
    vv.className = 'kv__v' + (mono ? ' mono' : ''); vv.textContent = v;
    row.append(kk, vv);
    return row;
  };

  const card = document.createElement('section');
  card.className = 'card';
  card.append(
    kv('من', o.pickup.name || '—'),
    kv('إلى', o.dropoff.name || '—'),
    kv('المسافة', `${o.price.km} كم · ${o.price.minutes} دقيقة`),
    kv('الإجمالي', money(o.price.total), true),
    kv('الدفع', o.payment === 'cash' ? 'نقداً عند التسليم' : 'من المحفظة'),
  );
  if (o.note) card.append(kv('ملاحظة', o.note));
  if (o.pointsEarned) card.append(kv('نقاط مكتسبة', String(o.pointsEarned), true));
  body.push(card);

  if (o.driver) {
    const d = document.createElement('section');
    d.className = 'card';
    const h = document.createElement('h3');
    h.className = 'sec'; h.style.margin = '0'; h.textContent = 'مندوبك';
    d.append(h, kv('الاسم', o.driver.name));
    if (o.driver.rating) d.append(kv('التقييم', `${o.driver.rating} من ٥`, true));
    d.append(kv('توصيلات', String(o.driver.completedOrders), true));

    const line = document.createElement('div');
    line.className = 'phoneline';
    const lbl = document.createElement('span'); lbl.textContent = 'اتصل به';
    const a = document.createElement('a');
    a.href = `tel:+${o.driver.phone}`; a.textContent = `+${o.driver.phone}`;
    line.append(lbl, a);
    d.append(line);
    body.push(d);
  }

  if (o.status === 'searching') {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'ghost wide'; btn.textContent = 'إلغاء الطلب';
    btn.addEventListener('click', () => cancelOrder(o.id, btn));
    body.push(btn);
  }

  if (o.status === 'delivered') {
    const sec = document.createElement('section');
    sec.className = 'card';
    const h = document.createElement('h3');
    h.className = 'sec'; h.style.margin = '0';
    h.textContent = o.rating ? `قيّمت هذا الطلب ${o.rating} من ٥` : 'قيّم المندوب';
    sec.append(h);
    if (!o.rating) sec.append(starRow(o.id));
    body.push(sec);
  }

  $('track-body').replaceChildren(...body);
}

function starRow(orderID) {
  const row = document.createElement('div');
  row.className = 'stars';
  const stars = [1, 2, 3, 4, 5].map((n) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'star'; b.textContent = '★';
    b.setAttribute('aria-label', `${n} من ٥`);


    const light = () => stars.forEach((s, i) => s.classList.toggle('is-on', i < n));
    b.addEventListener('mouseenter', light);
    b.addEventListener('focus', light);
    b.addEventListener('click', () => rate(orderID, n, row));
    return b;
  });
  row.addEventListener('mouseleave', () => stars.forEach((s) => s.classList.remove('is-on')));
  row.append(...stars);
  return row;
}

async function rate(orderID, stars, row) {
  row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    await api('/api/orders/rate', { method: 'POST', body: { orderID, stars } });
    toast(`شكراً — قيّمت ${stars} من ٥`);
    openTrack(orderID);
  } catch (e) {
    toast(e.message, true);
    row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

async function cancelOrder(id, btn) {
  if (!confirm('إلغاء هذا الطلب؟')) return;
  btn.disabled = true;
  try {
    await api('/api/orders/cancel', { method: 'POST', body: { orderID: id } });
    toast('أُلغي الطلب');
    $('track').hidden = true;
    loadOrders();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}


const DOC_KINDS = [
  ['civilIDFront', 'البطاقة المدنية — الوجه', 1, true],
  ['civilIDBack', 'البطاقة المدنية — الظهر', 1, false],
  ['license', 'رخصة القيادة', 1, true],
  ['ownership', 'ملكية المركبة', 1, true],
  ['vehicle', 'صور المركبة', 4, true],
];

const uploaded = {};

function openReg() {
  if (!state.token) { openAuth(); return; }
  $('reg').hidden = false;
  showError('reg-error', '');
  const zone = $('reg-zone');
  if (!zone.options.length) {
    zone.replaceChildren(...(state.zones || []).map((z) => {
      const o = document.createElement('option');
      o.value = z; o.textContent = z;
      return o;
    }));
  }
  renderDocs();
}

function renderDocs() {
  $('reg-docs').replaceChildren(...DOC_KINDS.map(([kind, label, max, required]) => {
    const got = uploaded[kind] || [];
    const row = document.createElement('div');
    row.className = 'doc' + (got.length ? ' is-done' : '');

    const text = document.createElement('div');
    text.className = 'doc__text';
    const t = document.createElement('div');
    t.className = 'doc__t';
    t.textContent = label + (required ? '' : ' (اختياري)');
    const sub = document.createElement('div');
    sub.className = 'doc__s';
    sub.textContent = got.length
      ? (max > 1 ? `${got.length} من ${max} مرفوعة` : 'مرفوعة')
      : (max > 1 ? `صورتان على الأقل` : 'اختر صورة أو PDF');
    text.append(t, sub);

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,application/pdf';


    input.setAttribute('capture', 'environment');
    if (max > 1) input.multiple = true;

    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'doc__btn';
    btn.textContent = got.length ? 'تغيير' : 'إرفاق';
    btn.addEventListener('click', () => input.click());
    input.addEventListener('change', () => uploadDocs(kind, input.files, max, btn, sub));

    row.append(text, input, btn);
    return row;
  }));
}

async function uploadDocs(kind, files, max, btn, sub) {
  const list = Array.from(files).slice(0, max);
  if (!list.length) return;
  btn.disabled = true;
  uploaded[kind] = [];
  try {
    for (const [i, file] of list.entries()) {
      if (file.size > 8 * 1024 * 1024) {
        throw new Error(`${file.name}: أكبر من ٨ م.ب`);
      }
      sub.textContent = `جاري الرفع ${i + 1} من ${list.length}…`;
      const form = new FormData();
      form.append('kind', kind);
      form.append('file', file);
      const res = await fetch('/documents/upload', {
        method: 'POST',
        headers: { Authorization: `Bearer ${state.token}` },
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || 'رفض الخادم الملف');
      uploaded[kind].push(data.file_id);
    }
    showError('reg-error', '');
    renderDocs();
  } catch (e) {
    delete uploaded[kind];
    showError('reg-error', e.message);
    renderDocs();
  }
}

async function submitReg() {
  const btn = $('reg-submit');
  btn.classList.add('is-busy'); btn.disabled = true;
  showError('reg-error', '');
  try {
    await api('/api/driver/register', {
      method: 'POST',
      body: {
        name: $('reg-name').value.trim(),
        civilID: digitsOnly($('reg-civil').value),
        licenseNumber: $('reg-license').value.trim(),
        zone: $('reg-zone').value,
        vehicle: {
          make: $('reg-make').value.trim(),
          model: $('reg-model').value.trim(),
          plateNumber: $('reg-plate').value.trim(),
          ownershipNumber: $('reg-own').value.trim(),
        },
        documents: uploaded,
      },
    });
    $('reg').hidden = true;
    toast('أُرسلت بياناتك للمراجعة');
    switchView('driver');
  } catch (e) {
    showError('reg-error', e.message);
  } finally {
    btn.classList.remove('is-busy'); btn.disabled = false;
  }
}

function wireTrackAndReg() {
  $('track-close').addEventListener('click', () => { $('track').hidden = true; });
  $('track').addEventListener('click', (e) => {
    if (e.target === $('track')) $('track').hidden = true;
  });
  $('reg-close').addEventListener('click', () => { $('reg').hidden = true; });
  $('reg-submit').addEventListener('click', submitReg);
  $('reg-civil').addEventListener('input', (e) => {
    e.target.value = digitsOnly(e.target.value).slice(0, 14);
  });
}

boot();
