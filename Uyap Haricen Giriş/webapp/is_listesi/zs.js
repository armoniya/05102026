// İş Listesi (/is-listesi) — düz JS tek sayfa. Tüm DOM h() ile kurulur (innerHTML yok → XSS yok).
"use strict";

const S = { me: null, people: {}, peopleList: [], uploads: false, maxMb: 25,
  view: "takvim", tasks: [], mine: [], onayList: null, selId: null, detail: null, expanded: new Set(),
  q: "", f: "aktif", sort: "tarih", composer: false, tab: "zaman", rejectFor: null, order: [],
  // Süzgeçler (fd: dosya türü, fg: süreç / grup etiketi, fa: aşama, ft: hepsi bulunacak etiketler; pa: Süreçler
  // görünümünde "şu an bu aşamada bekleyenler") ve tanımlar.
  fd: "", fg: "", fa: "", ft: [], pa: "", surecler: [], sablonlar: [],
  tagDefs: { hazir: [], buro: [], kisisel: [], renkler: [] }, tagMap: {} };

// "Onay bekliyor" her yerde aynı ad: etiket, süzgeç, menü.
const STATUS = { acik: "Açık", devam: "Devam ediyor", onayda: "Onay bekliyor", onaylandi: "Onaylandı", tamamlandi: "Tamamlandı" };
const PRIO = { 2: "Acil", 1: "Normal", 0: "Düşük" };
const EVENTS = { created: "oluşturdu", assigned: "atadı", edited: "düzenledi", subtask: "alt işi işaretledi",
  subtask_deleted: "alt işi sildi", submitted: "onaya gönderdi", approved: "onayladı", rejected: "reddetti",
  reopened: "yeniden açtı", status: "işi başlattı", note: "not ekledi", file: "belge ekledi",
  file_deleted: "belge sildi", deleted: "sildi", transferred: "işi devretti", published: "işi görünür yaptı",
  completed: "işi tamamladı", tags: "etiketleri değiştirdi" };
// [uzun ad, kısa ad (telefon), ikon, sayfa başlığı, alt başlık]
const VIEWS = {
  takvim: ["Takvim", "Takvim", "cal", "Takvim", ""],
  hatirlatici: ["Hatırlatıcılar", "Hatırlat", "bell", "Hatırlatıcılar", "Size kurulan ve sizin kurduğunuz hatırlatmalar — işe bağlı ya da bağımsız"],
  benim: ["Benim işlerim", "İşlerim", "inbox", "Benim işlerim", "Size atanan işler ve alt işler"],
  ozel: ["Özel işlerim", "Özel", "lock", "Özel işlerim", "Yalnız sizin görebildiğiniz işler — onaya gönderilmez, bitince “Tamamla”"],
  onay: ["Onay bekleyenler", "Onay", "shield", "Onay bekleyenler", "Kontrol edip onaylamanız gereken işler"],
  tumu: ["Tüm işler", "Tümü", "layers", "Tüm işler", "Bürodaki bütün işler"],
  surec: ["Süreçler", "Süreç", "list", "Süreçler", "Dosya ve taraf bazında aşamalar — hangi dosyanın nerede beklediği tek bakışta"],
  rapor: ["Raporlar", "Rapor", "chart", "Raporlar", ""],
  sablonlar: ["Şablonlar", "Şablon", "copy", "İş şablonları ve etiketler", "UYAP'ta “İş Ata” penceresinde aranıp seçilen hazır işler ve işleri gruplayan etiketler — buradan ekleyin, düzenleyin, silin"],
  kullanicilar: ["Kullanıcılar", "Kişiler", "users", "Kullanıcılar", "Hesapları ekleyin, düzenleyin, kapatın, parolaları sıfırlayın"],
};
const FILTERS = {
  aktif: ["Aktif", (t) => !isDone(t)],
  acik: ["Açık", (t) => t.status === "acik"],
  devam: ["Devam ediyor", (t) => t.status === "devam"],
  onayda: ["Onay bekliyor", (t) => t.status === "onayda"],
  bitti: ["Biten", (t) => isDone(t)],
  tumu: ["Tümü", () => true],
  isgec: ["İş gecikti", (t) => lateKind(t) === "is"],
  onaygec: ["Onay gecikti", (t) => lateKind(t) === "onay"],
};

// ── ikonlar (Lucide tarzı çizgi ikonlar, yerel) ───────────────────────────────────
const ICONS = {
  check: "M20 6 9 17l-5-5",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2|M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z|m9 12 2 2 4-4",
  layers: "m12 2 10 5-10 5L2 7l10-5z|m2 17 10 5 10-5|m2 12 10 5 10-5",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2|M9 7a4 4 0 1 0 0 .01|M22 21v-2a4 4 0 0 0-3-3.87|M16 3.13a4 4 0 0 1 0 7.75",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 6v6l4 2",
  cal: "M8 2v4|M16 2v4|M3 10h18|M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  msg: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  clip: "m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48",
  copy: "M8 8h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2z|M4 16V4a2 2 0 0 1 2-2h12",
  list: "M9 6h11|M9 12h11|M9 18h11|M4 6h.01|M4 12h.01|M4 18h.01",
  plus: "M12 5v14|M5 12h14",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z|m21 21-4.3-4.3",
  x: "M18 6 6 18|m6 6 12 12",
  up: "m18 15-6-6-6 6", down: "m6 9 6 6 6-6", left: "m15 18-6-6 6-6", right: "m9 18 6-6-6-6",
  edit: "M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z",
  trash: "M3 6h18|M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6|M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|m17 8-5-5-5 5|M12 3v12",
  lock: "M19 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2z|M7 11V7a5 5 0 0 1 10 0v4",
  alert: "M12 9v4|M12 17h.01|M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
  play: "m6 3 14 9-14 9V3z", send: "m22 2-7 20-4-9-9-4Z|M22 2 11 13", undo: "M3 7v6h6|M21 17a9 9 0 0 0-15-6.7L3 13",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z|M12 1v2|M12 21v2|m4.22 4.22 1.42 1.42|m18.36 18.36 1.42 1.42|M1 12h2|M21 12h2|m4.22 19.78 1.42-1.42|m18.36 5.64 1.42-1.42",
  moon: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z",
  hist: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8|M3 3v5h5|M12 7v5l4 2",
  transfer: "M16 3h5v5|M21 3l-7 7|M8 21H3v-5|M3 21l7-7",
  eye: "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  eyeoff: "M9.88 9.88a3 3 0 1 0 4.24 4.24|M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68|M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61|M2 2l20 20",
  flag: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z|M4 22v-7",
  arrowdown: "M12 5v14|m19 12-7 7-7-7",
  chart: "M3 3v18h18|M8 17V11|M13 17V7|M18 17v-4",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|m7 10 5 5 5-5|M12 15V3",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9|M10.3 21a1.94 1.94 0 0 0 3.4 0",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|m16 17 5-5-5-5|M21 12H9",
  key: "M2 18v3c0 .6.4 1 1 1h4v-3h3v-3h2l1.4-1.4a6.5 6.5 0 1 0-4-4Z|M16.5 7.5h.01",
  printer: "M6 9V2h12v7|M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2|M6 14h12v8H6z",
};
function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name].split("|")) {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", d); svg.append(p);
  }
  return svg;
}

// ── yardımcılar ───────────────────────────────────────────────────────────────────
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false)
    el.append(c instanceof Node ? c : String(c));
  return el;
}
const $ = (id) => document.getElementById(id);
const isAdmin = () => S.me && S.me.role === "admin";
const name = (id) => (id && S.people[id]) ? S.people[id].display_name : "Atanmamış";
const ownTask = (t) => t.created_by === S.me.id && t.assignee_id === S.me.id;
const locked = (t) => t.status === "onayda" || t.status === "onaylandi" || t.status === "tamamlandi";
const stop = (e) => e.stopPropagation();
const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* yok */ } } };

const AV_COLORS = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#8b5cf6", "#ef4444", "#22c55e", "#f97316", "#06b6d4"];
function initials(n) {
  const p = (n || "?").trim().split(/\s+/);
  return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toLocaleUpperCase("tr-TR");
}
function avatar(id, size) {
  if (!id || !S.people[id]) return h("span", { class: "av " + (size || ""), style: "background:var(--border-2);color:var(--muted)" }, "?");
  return h("span", { class: "av " + (size || ""), style: `background:${AV_COLORS[id % AV_COLORS.length]}`,
    title: S.people[id].display_name }, initials(S.people[id].display_name));
}
const who = (id, size) => h("span", { class: "who" }, avatar(id, size || "xs"), name(id));

const FMT = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "2-digit", month: "2-digit",
  year: "numeric", hour: "2-digit", minute: "2-digit" });
const FMT_SHORT = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "numeric", month: "short",
  hour: "2-digit", minute: "2-digit" });
const FMT_TIME = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", hour: "2-digit", minute: "2-digit" });
const fmt = (t) => t ? FMT.format(new Date(t * 1000)) : "";
const fmtShort = (t) => t ? FMT_SHORT.format(new Date(t * 1000)) : "";
const fromLocalInput = (v) => v ? Math.floor(new Date(v).getTime() / 1000) : null;
const sizeTxt = (n) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`;
// Son tarih: tarih + (isteğe bağlı) saat. Saat boşsa gün sonu mesaisi (18:00) varsayılır.
const DEFAULT_DUE_TIME = "18:00";
function dueField(ts) {
  const p = (n) => String(n).padStart(2, "0"), d = ts ? new Date(ts * 1000) : null;
  const date = h("input", { type: "date", value: d ? `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` : "" });
  const time = h("input", { type: "time", value: d ? `${p(d.getHours())}:${p(d.getMinutes())}` : "",
    title: `Saat boş kalırsa ${DEFAULT_DUE_TIME}` });
  date.addEventListener("change", () => { if (date.value && !time.value) time.value = DEFAULT_DUE_TIME; });
  return { el: h("span", { class: "due-in" }, date, time), date,
    value: () => date.value ? fromLocalInput(`${date.value}T${time.value || DEFAULT_DUE_TIME}`) : null };
}
const isDone = (t) => t.status === "onaylandi" || t.status === "tamamlandi" || t.status === "tamam";
const isLate = (t) => !!t.due_at && t.due_at * 1000 < Date.now() && !isDone(t);
function dayStart(offset) { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + (offset || 0)); return d.getTime() / 1000; }
const isToday = (t) => !!t.due_at && t.due_at >= dayStart(0) && t.due_at < dayStart(1);
// Gecikmenin kime ait olduğu: süresinde onaya gönderilip onaylanmamışsa "onay", aksi halde "iş".
function lateKind(t) {
  if (!isLate(t)) return null;
  if (t.status === "onayda" && t.submitted_at && t.submitted_at <= t.due_at) return "onay";
  return "is";
}
const assigneesOf = (t) => (t.assignees && t.assignees.length) ? t.assignees : (t.assignee_id ? [t.assignee_id] : []);
const isMine = (t) => assigneesOf(t).includes(S.me.id);
const canApprove = (t) => isAdmin() || (t.approvers || []).includes(S.me.id);

let toastTimer = null;
function toast(msg, ok) {
  const t = $("toast");
  t.replaceChildren(icon(ok ? "check" : "alert"), msg);
  t.className = "toast" + (ok ? " ok" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

async function api(method, url, body, isForm) {
  const opt = { method, headers: { "X-ZS": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  if (r.status === 401) { location.reload(); throw new Error("Oturum kapandı."); }
  let data = null;
  try { data = await r.json(); } catch (_) { /* boş/metin yanıt */ }
  if (!r.ok || !data || data.ok === false) throw new Error((data && data.error) || `Hata (HTTP ${r.status})`);
  return data;
}
function act(fn) {
  return async (ev) => {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    const b = ev && ev.currentTarget; if (b && b.disabled) return;
    if (b && b.tagName === "BUTTON") b.disabled = true;
    try { await fn(ev); } catch (e) { toast(e.message); } finally { if (b && b.tagName === "BUTTON") b.disabled = false; }
  };
}
// Tehlikeli işlemde iki tık onayı (tarayıcı confirm() diyaloğu yerine).
function confirmBtn(label, fn, cls = "btn-danger-soft sm", ic = "trash") {
  const b = h("button", { type: "button", class: cls });
  const set = (txt) => b.replaceChildren(ic ? icon(ic) : "", txt);
  let armed = false, t = null;
  set(label);
  b.addEventListener("click", act(async () => {
    if (!armed) { armed = true; set("Emin misiniz?"); t = setTimeout(() => { armed = false; set(label); }, 3000); return; }
    clearTimeout(t); await fn();
  }));
  return b;
}
function peopleSelect(selected, withEmpty = true) {
  const s = h("select", {}, withEmpty ? h("option", { value: "" }, "Atanmamış") : null);
  for (const p of S.peopleList) if (p.active || p.id === selected)
    s.append(h("option", { value: p.id, selected: p.id === selected }, p.display_name));
  return s;
}
const prioSelect = (v) => h("select", {}, [2, 1, 0].map((p) => h("option", { value: p, selected: p === (v ?? 1) }, PRIO[p])));
const pill = (st) => h("span", { class: `pill s-${st}` }, STATUS[st] || st);
function latePill(t) {
  const k = lateKind(t);
  return k ? h("span", { class: "pill s-late", title: k === "onay" ? "İş süresinde onaya gönderildi; onay son tarihi geçti"
    : "Son tarih geçti, iş tamamlanmadı" }, k === "onay" ? "Onay gecikti" : "İş gecikti") : null;
}
function whoMany(ids) {
  if (ids.length <= 1) return who(ids[0]);
  return h("span", { class: "who" }, h("span", { class: "avs" }, ids.slice(0, 3).map((i) => avatar(i, "xs"))),
    `${name(ids[0])} +${ids.length - 1}`);
}
const privTag = (t) => t.is_private ? h("span", { class: "priv", title: "Yalnız siz görüyorsunuz" }, icon("lock"), "Özel") : null;
const isOwnPrivate = (t) => !!t.is_private && t.created_by === S.me.id;
const prioTag = (t) => t.priority === 2 ? h("span", { class: "prio p2" }, icon("flag"), "Acil")
  : t.priority === 0 ? h("span", { class: "prio p0" }, icon("arrowdown"), "Düşük") : null;
function dueChip(t) {
  if (!t.due_at) return null;
  const late = isLate(t), today = !late && isToday(t) && !isDone(t);
  return h("span", { class: "due" + (late ? " late" : today ? " today" : "") }, icon("clock"),
    today ? `Bugün ${FMT_TIME.format(new Date(t.due_at * 1000))}` : fmtShort(t.due_at), late ? " · gecikti" : "");
}

// ── süreç / aşama / etiket (hazırlar sunucuda kodda; özel etiketler kişisel ya da büroya açık) ──
async function loadMeta() {
  try {
    const [p, g, s] = await Promise.all([api("GET", "/is-listesi/api/surecler"), api("GET", "/is-listesi/api/etiketler"),
      api("GET", "/is-listesi/api/sablonlar")]);
    S.surecler = p.surecler; S.tagDefs = g; S.sablonlar = s.hazir.concat(s.buro);
    S.tagMap = Object.fromEntries([...g.hazir, ...g.buro, ...g.kisisel].map((x) => [x.key, x]));
  } catch (_) { /* eski sunucu: süzgeçler boş kalır */ }
  // Hatırlanan süzgeçte artık olmayan grup / etiket düşer.
  if (S.fg && !(S.tagMap[S.fg] && S.tagMap[S.fg].parent === `e-${S.fd}`)) { S.fg = ""; S.fa = ""; }
  if (S.fa && !(surecOf(fsOf()) || { asamalar: [] }).asamalar.some((a) => a.key === S.fa)) S.fa = "";
  S.ft = S.ft.filter((k) => S.tagMap[k]);
}
const surecOf = (k) => S.surecler.find((s) => s.key === k) || null;
// İşin (ya da şablonun "surec/asama" değerinin) aşaması: {s, a, i} ya da null.
function asamaOf(t) {
  const [sk, ak] = typeof t === "string" ? t.split("/") : [t.surec, t.asama];
  const s = sk && surecOf(sk); if (!s) return null;
  const i = s.asamalar.findIndex((a) => a.key === ak);
  return i < 0 ? null : { s, a: s.asamalar[i], i };
}
function asamaTag(t) {
  const x = asamaOf(t); if (!x) return null;
  return h("span", { class: "asama" + (x.a.dal ? " iade" : ""),
    title: `${x.s.title} · aşama ${x.i + 1}/${x.s.asamalar.length}${x.a.dal ? " · iade dalı (yeniden tebliğ)" : ""}` }, `${x.i + 1}. ${x.a.title}`);
}
function asamaSelect(cur) {
  return h("select", {}, h("option", { value: "" }, "Süreç yok"), S.surecler.map((s) => h("optgroup", { label: s.title },
    s.asamalar.map((a, i) => h("option", { value: `${s.key}/${a.key}`, selected: cur === `${s.key}/${a.key}` },
      `${i + 1}. ${a.title}${a.dal ? " (iade dalı)" : ""}`)))));
}
// İşin şablonu (kontrol sorusu ve sonuç dalları için): kayıtlı bağ ("h-mazbata" | "b-12").
const tplOf = (t) => t.sablon ? (S.sablonlar || []).find((x) => String(x.id) === t.sablon || `b-${x.id}` === t.sablon) || null : null;
const adKey = (s) => String(s || "").toLocaleLowerCase("tr-TR").replace(/ı/g, "i").replace(/\s+/g, " ").trim();
const tplByTitle = (ad) => { const k = adKey(ad); return (S.sablonlar || []).find((x) => adKey(x.title) === k)
  || (S.sablonlar || []).find((x) => (x.eski || []).some((e) => adKey(e) === k)) || null; };
// Etiket ağacı: dosya türü (ana etiket) → süreç / grup → alt etiket (en çok 3 katman).
const allTags = () => [...S.tagDefs.hazir, ...S.tagDefs.buro, ...S.tagDefs.kisisel];
function tagChain(k) {   // [etiket, üstü, …, kök]
  const out = [];
  while (k && S.tagMap[k] && !out.includes(k) && out.length < 4) { out.push(k); k = S.tagMap[k].parent; }
  return out;
}
const tagPath = (k) => tagChain(k).reverse().map((x) => S.tagMap[x].name).join(" › ");
// Üst etiketle süzünce altındaki etiketlerden biri takılı işler de gelir.
const hasTag = (t, k) => (t.tags || []).some((x) => tagChain(x).includes(k));
// Ağaç sırası: [{g, d}] — kökler (önce dosya türleri) ve altları; d = derinlik (0 = kök).
function tagTree(list) {
  const keys = new Set(list.map((g) => g.key)), kids = {};
  for (const g of list) { const p = g.parent && keys.has(g.parent) ? g.parent : ""; (kids[p] = kids[p] || []).push(g); }
  const out = [], walk = (p, d) => (kids[p] || []).forEach((g) => { out.push({ g, d }); if (d < 3) walk(g.key, d + 1); });
  walk("", 0);
  return out;
}
const indent = (d) => d ? "   ".repeat(d - 1) + " └ " : "";
// Etiket bu türdeki işe takılabilir mi (dosyasız iş ya da genel etiket: her zaman).
const tagFits = (g, tur) => !tur || !g.tur || g.tur === tur;
function tagChip(k, extra) {
  const g = S.tagMap[k]; if (!g) return null;
  return h("span", { class: "etk" + (g.owner_id ? " kisisel" : "") + (g.parent ? "" : " kok"), style: `--c:${g.color}`,
    title: tagPath(k) + (g.owner_id ? " — kişisel etiket, yalnız siz görürsünüz" : "") }, g.name, extra || null);
}
// İşte en derin etiketler gösterilir; üstleri (ör. İcra) yalnız başka alt etiketi yoksa görünür. Aşama
// etiketi gösterilmez: aşama rozeti (asamaTag) onu zaten gösterir.
function tagChips(t) {
  const ks = (t.tags || []).filter((k) => !(S.tagMap[k] || {}).asama), parents = new Set(ks.flatMap((k) => tagChain(k).slice(1)));
  return ks.filter((k) => !parents.has(k)).map((k) => tagChip(k));
}
const tagText = (t) => (t.tags || []).flatMap((k) => tagChain(k)).map((k) => (S.tagMap[k] || {}).name || "").join(" ");

// Süzgeç katmanları: dosya türü (fd) → süreç / grup (fg: türün altındaki etiket) → aşama (fa); ft: ayrıca
// hepsi bulunacak etiketler. Dosyasız iş "yok", türü bilinmeyen dosya "diger" sayılır.
const TUR_SIRA = [["icra", "İcra"], ["hukuk", "Hukuk davası"], ["arabuluculuk", "Arabuluculuk"], ["cbs", "CBS"],
  ["ceza", "Ceza davası"], ["idare", "İdari dava"], ["diger", "Diğer dosya"], ["yok", "Dosyasız"]];
const TUR_HEP = new Set(["icra", "hukuk", "arabuluculuk", "cbs"]);   // iş olmasa da görünen türler
const turOf = (t) => t.tur || (t.dosya_id ? "diger" : "yok");
const fsOf = () => (S.tagMap[S.fg] || {}).surec || "";
// upto: kaç katman süzülür (çip sayıları için üst katmanlar: 0 = yalnız ft, 1 = +tür, 2 = +grup, 3 = +aşama).
function facetFilter(T, upto = 3) {
  if (S.ft.length) T = T.filter((t) => S.ft.every((k) => hasTag(t, k)));
  if (upto > 0 && S.fd) T = T.filter((t) => turOf(t) === S.fd);
  const sk = fsOf();
  if (upto > 1 && S.fg) T = T.filter((t) => sk ? t.surec === sk : hasTag(t, S.fg));
  if (upto > 2 && S.fa && sk) T = T.filter((t) => t.asama === S.fa);
  return T;
}
const facetsOn = () => !!(S.fd || S.fg || S.ft.length);
function saveFacets() { store.set("zs-facet", JSON.stringify({ fd: S.fd, fg: S.fg, fa: S.fa, ft: S.ft })); }
function clearFacets() { S.fd = ""; S.fg = ""; S.fa = ""; S.ft = []; saveFacets(); }

// Süzgeç: Dosya türü çipleri → (tür seçilince) süreç / grup çipleri → (süreç seçilince) sıralı aşama çipleri.
// Her çipte o seçimle kaç iş kaldığı yazar; aynı çipe yeniden basmak seçimi kaldırır.
function facetBar() {
  const T = baseTasks().filter(FILTERS[S.f][1]);
  const redraw = () => { saveFacets(); renderHead(); renderList(); };
  const chip = (on, label, n, onclick, cls) => h("button", { type: "button", class: "chip" + (on ? " on" : "") + (cls || ""),
    "aria-pressed": on ? "true" : "false", onclick }, label, h("span", { class: "n" }, ` ${n}`));
  const row = (label, kids) => h("div", { class: "facet-row" }, h("span", { class: "facet-l" }, label), h("div", { class: "chips" }, kids));
  const T0 = facetFilter(T, 0);
  const turN = (k) => T0.filter((t) => turOf(t) === k).length;
  const rows = [row("Dosya türü", [chip(!S.fd, "Tümü", T0.length, () => { S.fd = ""; S.fg = ""; S.fa = ""; redraw(); }),
    ...TUR_SIRA.filter(([k]) => TUR_HEP.has(k) || S.fd === k || turN(k)).map(([k, l]) =>
      chip(S.fd === k, l, turN(k), () => { S.fd = S.fd === k ? "" : k; S.fg = ""; S.fa = ""; redraw(); }))])];
  const groups = S.fd ? allTags().filter((g) => g.parent === `e-${S.fd}`) : [];
  if (groups.length) {
    const T1 = facetFilter(T, 1);
    const gN = (g) => T1.filter((t) => g.surec ? t.surec === g.surec : hasTag(t, g.key)).length;
    rows.push(row("Süreç", [chip(!S.fg, "Tümü", T1.length, () => { S.fg = ""; S.fa = ""; redraw(); }),
      ...groups.map((g) => chip(S.fg === g.key, g.name, gN(g), () => { S.fg = S.fg === g.key ? "" : g.key; S.fa = ""; redraw(); },
        g.surec ? " surec" : ""))]));
  }
  const s = surecOf(fsOf());
  if (s) {
    const T2 = facetFilter(T, 2), kids = [chip(!S.fa, "Tüm aşamalar", T2.length, () => { S.fa = ""; redraw(); })];
    s.asamalar.forEach((a, i) => {
      if (a.dal && !(s.asamalar[i - 1] || {}).dal) kids.push(h("span", { class: "facet-dal" }, "İade dalı:"));
      if (!a.dal && (s.asamalar[i - 1] || {}).dal) kids.push(h("span", { class: "facet-dal" }, "Tebliğ edildiyse:"));
      kids.push(chip(S.fa === a.key, `${i + 1}. ${a.title}`, T2.filter((t) => t.asama === a.key).length,
        () => { S.fa = S.fa === a.key ? "" : a.key; redraw(); }, a.dal ? " iade" : ""));
    });
    rows.push(row("Aşama", kids));
  }
  // Diğer etiketler (genel, kişisel, gruplar): dosya türü kökleri ve aşama etiketleri yukarıda seçilir.
  const shown = facetFilter(T);
  const tagSel = h("select", { "aria-label": "Etiket" }, h("option", { value: "" }, "Etiket ekle…"),
    tagTree(allTags().filter((g) => !g.asama && (g.parent || !g.tur))).filter(({ g }) => !S.ft.includes(g.key)).map(({ g, d }) =>
      h("option", { value: g.key }, `${indent(d)}${g.name}${g.owner_id ? " (kişisel)" : ""} · ${shown.filter((t) => hasTag(t, g.key)).length}`)));
  tagSel.addEventListener("change", () => { if (tagSel.value) { S.ft.push(tagSel.value); redraw(); } });
  rows.push(row("Etiket", [tagSel,
    ...S.ft.map((k) => h("button", { type: "button", class: "etk on", style: `--c:${(S.tagMap[k] || {}).color || ""}`,
      title: `${S.tagMap[k] ? tagPath(k) : k} — süzgeçten çıkar`, onclick: () => { S.ft = S.ft.filter((x) => x !== k); redraw(); } }, (S.tagMap[k] || {}).name || k, icon("x"))),
    facetsOn() ? h("button", { type: "button", class: "btn-ghost sm", onclick: () => { clearFacets(); renderHead(); renderList(); } }, "Süzgeçleri temizle") : null]));
  return h("div", { class: "facets" }, rows);
}

// İşin etiketleri: kişisel etiketi işi gören herkes takar; hazır / büro etiketini atanan, açan ya da yönetici.
function tagDialog(t) {
  const dlg = $("dlg"), shared = isAdmin() || isMine(t) || t.created_by === S.me.id;
  const have = new Set(t.tags || []), boxes = [];
  // Ağaç: işin dosya türüne uyan dal + genel etiketler. Dosya türü etiketi dosyadan, aşama etiketi işin
  // aşamasından gelir (ikisi de burada değişmez; aşama "Düzenle"den).
  const rows = tagTree(allTags().filter((g) => tagFits(g, t.tur) && !g.asama)).map(({ g, d }) => {
    const auto = !!t.tur && !g.parent && g.tur === t.tur;
    const canUse = !auto && (g.owner_id || shared);
    const cb = h("input", { type: "checkbox", checked: have.has(g.key), disabled: !canUse });
    if (canUse) boxes.push([g.key, cb]);
    return h("label", { class: "etk-opt", style: `padding-left:${d * 22}px`,
      title: auto ? "Dosya türü etiketi dosyadan gelir" : canUse ? tagPath(g.key) : "Büro etiketini yalnız işe atanan, işi açan ya da yönetici takar" },
      cb, tagChip(g.key));
  });
  const nw = h("input", { placeholder: "Yeni kişisel etiket adı", maxlength: 40, class: "grow" });
  const addNew = act(async () => {
    if (!nw.value.trim()) { nw.focus(); return; }
    const r = await api("POST", "/is-listesi/api/etiketler", { name: nw.value, kisisel: true });
    await api("POST", `/is-listesi/api/tasks/${t.id}/etiketler`, { add: [r.tag.key] });
    await loadMeta(); await refresh(); if (S.detail) tagDialog(S.detail);
  });
  nw.addEventListener("keydown", (e) => { if (e.key === "Enter") addNew(e); });
  dlg.replaceChildren(h("h3", {}, "Etiketler"), h("div", { class: "small muted" }, `“${t.title}”`),
    asamaOf(t) ? h("div", { class: "small", style: "margin-top:6px" }, "Aşama: ", asamaTag(t),
      h("span", { class: "muted" }, " — işin aşamasından gelir; değiştirmek için “Düzenle”.")) : null,
    h("div", { class: "etk-agac" }, rows),
    h("div", { class: "row" }, nw, h("button", { type: "button", class: "btn-ghost sm", onclick: addNew }, icon("plus"), "Ekle ve tak")),
    h("div", { class: "actions", style: "margin-top:18px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        const add = boxes.filter(([k, cb]) => cb.checked && !have.has(k)).map(([k]) => k);
        const remove = boxes.filter(([k, cb]) => !cb.checked && have.has(k)).map(([k]) => k);
        if (add.length || remove.length) await api("POST", `/is-listesi/api/tasks/${t.id}/etiketler`, { add, remove });
        dlg.close(); await refresh(); }) }, icon("check"), "Kaydet"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}

// ── tema ───────────────────────────────────────────────────────────────────────────
function getTheme() {
  const v = store.get("zs-theme");
  if (v === "light" || v === "dark") return v;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  $("btn-theme").replaceChildren(icon(t === "dark" ? "sun" : "moon"));
}

// ── yükleme / gezinme ──────────────────────────────────────────────────────────────
async function loadMe() {
  const d = await api("GET", "/is-listesi/api/me");
  S.me = d.me; S.uploads = d.uploads; S.maxMb = d.max_file_mb; S.office = d.office;
  const bs = document.querySelector(".brand-sub");
  if (bs) bs.textContent = d.office && d.office.label ? d.office.label : "cokluuyap";
  $("btn-pw").hidden = !!S.me.account_username;   // parola CokluUyap hesabından değişir
  S.peopleList = d.people; S.people = Object.fromEntries(d.people.map((p) => [p.id, p]));
  $("me").replaceChildren(avatar(S.me.id, "lg"), h("div", { class: "txt" },
    h("div", { class: "nm" }, S.me.display_name), h("div", { class: "rl" }, isAdmin() ? "Yönetici" : "Kullanıcı")));
}

function go(view, selId) { if (view === "rapor" && S.rep) S.rep.data = null; S.view = view; S.selId = null; S.q = ""; S.composer = false; S.rejectFor = null; S.f = "aktif"; if (selId) { S.selId = selId; S.f = "tumu"; } refresh(); }

function renderTabs() {
  const mineActive = S.mine.filter((t) => !isDone(t));
  const counts = { benim: mineActive.length, ozel: mineActive.filter((t) => t.is_private).length,
    onay: isAdmin() ? (S.onayList || []).length : S.mine.filter((t) => t.status === "onayda").length,
    takvim: mineActive.filter(isToday).length,
    hatirlatici: (S.myRems || []).filter((r) => !r.sent_at && r.user_id === S.me.id && r.remind_at < dayStart(1)).length };
  const late = mineActive.some(isLate);
  const item = (k) => {
    const [long, short, ic] = VIEWS[k];
    const n = counts[k];
    return h("button", { type: "button", class: (S.view === k ? "on" : "") + (k === "sablonlar" ? " wide-only" : ""), onclick: () => go(k), "aria-current": S.view === k ? "page" : null },
      icon(ic), h("span", { class: "long" }, long), h("span", { class: "short" }, short),
      k === "benim" && late ? h("span", { class: "dot", title: "Geciken işiniz var" }) : null,
      n ? h("span", { class: "cnt" + (k === "onay" && isAdmin() ? " warn" : "") }, n) : null);
  };
  const kids = [h("div", { class: "nav-h" }, "İşler"), item("takvim"), item("hatirlatici"), item("benim"), item("ozel"), item("onay"), item("surec")];
  if (isAdmin()) kids.push(item("tumu"), h("div", { class: "nav-h" }, "Yönetim"), item("rapor"), item("sablonlar"), item("kullanicilar"));
  else kids.push(item("rapor"), item("sablonlar"));
  $("tabs").replaceChildren(...kids);
}

function baseTasks() {
  return S.view === "ozel" ? S.tasks.filter((t) => t.is_private) : S.tasks;
}

function renderHead() {
  const v = VIEWS[S.view];
  const title = v[3];
  const sub = S.view === "takvim" ? (isAdmin() ? "Ekibin işleri gün, hafta ve ay olarak — biten silik, geciken kırmızı" : "Size düşen işler gün, hafta ve ay olarak — biten silik, geciken kırmızı")
    : S.view === "onay" && !isAdmin() ? "Gönderdiğiniz, onay yetkilisinin kararını bekleyen işler"
    : S.view === "rapor" ? (isAdmin() ? "Kişilerin yaptıkları, iş ve tarih bazında sonuçlar (başkalarının özel işleri hariç)" : "Yaptıklarınız ve işlerinizin sonuçları")
    : v[4];
  const top = h("div", { class: "top" }, h("div", {}, h("h1", {}, title), h("div", { class: "subt" }, sub)));
  if (S.view === "kullanicilar" || S.view === "rapor" || S.view === "sablonlar") { $("head").replaceChildren(top); return; }
  const q = h("input", { type: "search", id: "q", placeholder: "İşlerde ara…  ( / )", value: S.q });
  q.addEventListener("input", () => { S.q = q.value; renderList(); });
  if (S.view === "takvim") q.placeholder = "Takvimde ara…  ( / )";
  if (S.view === "hatirlatici") q.placeholder = "Hatırlatıcılarda ara…  ( / )";
  if (S.view === "surec") {
    q.placeholder = "Dosya ya da taraf ara…  ( / )";
    top.append(h("div", { class: "row" }, h("label", { class: "search" }, icon("search"), q)));
    $("head").replaceChildren(top); return;
  }
  const right = h("div", { class: "row" }, h("label", { class: "search" }, icon("search"), q));
  const remDay = S.view === "takvim" && S.cal && S.cal.mode === "gun" ? S.cal.at : null;
  right.append(h("button", { type: "button", class: S.view === "hatirlatici" ? "btn" : "btn-ghost", title: "Hatırlatıcı kur (işi açmadan)",
    onclick: () => reminderCreateDialog({ day: remDay }) }, icon("bell"), S.view === "hatirlatici" ? "Hatırlatıcı kur" : "Hatırlatıcı"));
  if (S.view === "hatirlatici") { top.append(right); $("head").replaceChildren(top); return; }
  if (S.view !== "onay") right.append(h("button", { type: "button", class: "btn", onclick: openComposer }, icon("plus"), "Yeni iş"));
  top.append(right);
  if (S.view === "takvim") { calHead(top); return; }
  if (S.view === "onay") { $("head").replaceChildren(top); return; }

  // Tek süzgeç satırı: durumlar + (varsa) gecikme süzgeçleri. Gecikme süzgeci yeniden basılınca kalkar.
  // Sayılar seçili süreç / aşama / etiketlere göre.
  const T = facetFilter(baseTasks());
  const chip = (f, extra) => {
    const n = T.filter(FILTERS[f][1]).length;
    return h("button", { type: "button", class: "chip" + (extra || "") + (S.f === f ? " on" : ""),
      "aria-pressed": S.f === f ? "true" : "false",
      onclick: () => { S.f = S.f === f && extra ? "aktif" : f; renderHead(); renderList(); } },
      extra ? icon("clock") : null, FILTERS[f][0], h("span", { class: "n" }, ` ${n}`));
  };
  const chips = ["aktif", "acik", "devam", "onayda", "bitti", "tumu"].map((f) => chip(f));
  const lateChips = ["isgec", "onaygec"].filter((f) => S.f === f || T.some(FILTERS[f][1])).map((f) => chip(f, " late"));
  if (lateChips.length) chips.push(h("span", { class: "chip-sep" }), ...lateChips);
  const sortSel = h("select", { "aria-label": "Sıralama" }, [["tarih", "Son tarihe göre"], ["oncelik", "Önceliğe göre"],
    ["asama", "Aşamaya göre"], ["eklenme", "Eklenme sırası"]]
    .map(([k, l]) => h("option", { value: k, selected: S.sort === k }, l)));
  sortSel.addEventListener("change", () => { S.sort = sortSel.value; store.set("zs-sort", S.sort); renderList(); });
  $("head").replaceChildren(top,
    S.surecler.length || S.tagMap && Object.keys(S.tagMap).length ? facetBar() : null,
    h("div", { class: "toolbar" }, h("div", { class: "chips" }, chips),
      h("label", { class: "sortsel" }, "Sırala", sortSel)));
}

async function refresh() {
  try {
    const admin = isAdmin();
    const want = { takvim: admin ? "tumu" : "benim", benim: "benim", ozel: "benim", onay: "onay", tumu: "tumu", surec: admin ? "tumu" : "benim",
      kullanicilar: null, rapor: null, hatirlatici: null, sablonlar: null }[S.view];
    const [mine, onay, cur] = await Promise.all([
      api("GET", "/is-listesi/api/tasks?filtre=benim"),
      admin ? api("GET", "/is-listesi/api/tasks?filtre=onay") : Promise.resolve(null),
      want && want !== "benim" && !(want === "onay" && admin) ? api("GET", `/is-listesi/api/tasks?filtre=${want}`) : Promise.resolve(null)]);
    S.mine = mine.tasks; S.onayList = onay ? onay.tasks : null;
    try { S.myRems = (await api("GET", "/is-listesi/api/hatirlaticilar")).reminders; } catch (_) { S.myRems = []; }
    $("layout").classList.toggle("cal", S.view === "takvim");
    S.tasks = want === "benim" ? S.mine : want === "onay" && admin ? S.onayList : cur ? cur.tasks : [];
    renderTabs(); renderHead();
    if (S.view === "kullanicilar") { closeDetail(); await renderUsers(); return; }
    if (S.view === "rapor") { closeDetail(); await renderReport(); return; }
    if (S.view === "sablonlar") { closeDetail(); await renderTemplates(); return; }
    if (S.view === "hatirlatici") { closeDetail(); renderReminders(); return; }
    renderList();
    if (S.selId) await openDetail(S.selId, true); else closeDetail();
  } catch (e) { toast(e.message); }
}

// ── liste: süzme, gruplama ─────────────────────────────────────────────────────────
function shownTasks() {
  let T = baseTasks();
  if (S.view !== "onay") T = facetFilter(T.filter(FILTERS[S.f][1]));
  const q = S.q.trim().toLocaleLowerCase("tr-TR");
  if (q) T = T.filter((t) => (t.title + " " + (t.description || "") + " " + name(t.assignee_id) + " " + dosyaText(t) + " " +
    t.subtasks.map((s) => s.title).join(" ") + " " + tagText(t) + " " + ((asamaOf(t) || {}).a || {}).title)
    .toLocaleLowerCase("tr-TR").includes(q));
  return T;
}
const byDue = (a, b) => (a.due_at || 9e12) - (b.due_at || 9e12) || a.id - b.id;
const byPrioDue = (a, b) => (b.priority ?? 1) - (a.priority ?? 1) || byDue(a, b);
function grouped(T) {
  if (S.sort === "eklenme") return [[null, T]];
  if (S.sort === "asama") {
    const out = [];
    for (const s of S.surecler) s.asamalar.forEach((a, i) => out.push([`${S.surecler.length > 1 ? s.title + " · " : ""}${i + 1}. ${a.title}${a.dal ? " (iade dalı)" : ""}`,
      T.filter((t) => t.surec === s.key && t.asama === a.key).sort(byPrioDue)]));
    return [...out, ["Süreçsiz", T.filter((t) => !asamaOf(t)).sort(byPrioDue)]];
  }
  if (S.sort === "oncelik") return [[2, "Acil"], [1, "Normal"], [0, "Düşük"]]
    .map(([p, l]) => [l, T.filter((t) => (t.priority ?? 1) === p).sort(byDue)]);
  const week = dayStart(7), out = { gec: [], bugun: [], hafta: [], sonra: [], yok: [], bitti: [] };
  for (const t of T) {
    if (isDone(t)) out.bitti.push(t);
    else if (!t.due_at) out.yok.push(t);
    else if (isLate(t)) out.gec.push(t);
    else if (isToday(t)) out.bugun.push(t);
    else if (t.due_at < week) out.hafta.push(t);
    else out.sonra.push(t);
  }
  return [["Gecikmiş", out.gec], ["Bugün", out.bugun], ["Bu hafta", out.hafta], ["Daha sonra", out.sonra],
    ["Tarihsiz", out.yok], ["Bitenler", out.bitti]].map(([l, a]) => [l, a.sort(byPrioDue)]);
}

function renderList() {
  if (S.view === "takvim") { renderCalendar(); return; }
  if (S.view === "hatirlatici") { renderReminders(); return; }
  if (S.view === "surec") { renderProcess(); return; }
  const kids = [];
  if (S.composer) kids.push(composerCard());
  const T = shownTasks();
  S.order = [];
  if (!T.length) {
    const filtered = S.q.trim() || (S.view !== "onay" && (S.f !== "tumu" || facetsOn()) && baseTasks().length);
    kids.push(h("div", { class: "card empty" }, h("div", { class: "ic" }, icon(filtered ? "search" : "check")),
      h("b", {}, filtered ? "Bu süzgeçte iş yok" : S.view === "onay" ? "Onay bekleyen iş yok" : S.view === "ozel" ? "Özel işiniz yok" : "Burada iş yok"),
      filtered ? h("button", { type: "button", class: "btn-ghost sm", style: "margin-top:10px",
        onclick: () => { S.q = ""; S.f = "tumu"; clearFacets(); renderHead(); renderList(); } }, "Süzgeçleri temizle")
        : S.view === "onay" ? "Her şey güncel." : "Sağ üstteki “Yeni iş” ile ya da N tuşuyla başlayın."));
  }
  for (const [label, arr] of grouped(T)) {
    if (!arr.length) continue;
    if (label) kids.push(h("div", { class: "group-h" + (label === "Gecikmiş" || label === "Acil" ? " late" : "") }, label, h("span", { class: "n" }, arr.length)));
    for (const t of arr) { S.order.push(t.id); kids.push(taskCard(t)); }
  }
  $("list").replaceChildren(...kids);
}

function openComposer() {
  if (["onay", "kullanicilar", "rapor", "hatirlatici", "sablonlar", "surec"].includes(S.view)) { S.view = "benim"; S.selId = null; S.f = "aktif"; refresh(); }
  S.composer = true; renderList();
  const i = document.querySelector(".composer input"); if (i) { i.focus(); i.scrollIntoView({ block: "center" }); }
}

// Yeni iş formundaki belge seçimi: dosyalar iş oluşturulana kadar tarayıcıda bekler.
function composerFiles() {
  const picked = [];
  const inp = h("input", { type: "file", multiple: true, accept: FILE_ACCEPT });
  const list = h("div", { class: "files" });
  const draw = () => list.replaceChildren(...picked.map((f, i) => h("div", { class: "file" }, fileTag(f.name),
    h("div", { class: "grow" }, h("div", { class: "fn", title: f.name }, f.name), h("div", { class: "small muted" }, sizeTxt(f.size))),
    h("button", { type: "button", class: "btn-icon", title: "Çıkar", onclick: () => { picked.splice(i, 1); draw(); } }, icon("x")))));
  const add = (fl) => {
    for (const f of fl) {
      if (f.size > S.maxMb * 1048576) { toast(`${f.name}: ${S.maxMb} MB sınırını aşıyor.`); continue; }
      if (!picked.some((p) => p.name === f.name && p.size === f.size)) picked.push(f);
    }
    inp.value = ""; draw();
  };
  inp.addEventListener("change", () => add(inp.files));
  const drop = h("div", { class: "drop" }, h("div", { class: "ic" }, icon("upload")),
    h("div", { class: "grow" }, h("div", { style: "font-weight:600" }, "Belge ekle (isteğe bağlı)"),
      h("div", { class: "small muted" }, `İş oluşturulunca yüklenir · PDF, UDF, Word, Excel, görsel, ZIP · dosya başına en fazla ${S.maxMb} MB`)),
    h("button", { type: "button", class: "btn-ghost sm", onclick: () => inp.click() }, "Dosya seç"), inp);
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); add(e.dataTransfer.files); });
  return { el: h("div", { class: "composer-files" }, drop, list), list: () => picked.slice() };
}

function composerCard() {
  const title = h("input", { placeholder: "Ne yapılacak?", maxlength: 300, style: "width:100%" });
  const sel = peopleSelect(S.me.id), due = dueField(S.composerDue || null), prio = prioSelect(1);
  const desc = h("textarea", { placeholder: "Açıklama (isteğe bağlı)", maxlength: 5000 });
  const priv = h("input", { type: "checkbox", checked: S.view === "ozel" });
  const extra = isAdmin() ? peoplePicker("Ek atanan ekle…") : null;
  const appr = isAdmin() ? peoplePicker("Onay yetkilisi ekle…") : null;
  const extraRow = isAdmin() ? h("div", { class: "form-grid" },
    h("div", { class: "lbl" }, "Ek atananlar", extra.el), h("div", { class: "lbl" }, "Onay yetkilileri", appr.el)) : null;
  const sync = () => {
    sel.disabled = priv.checked; if (priv.checked) sel.value = String(S.me.id);
    if (extraRow) extraRow.hidden = priv.checked;
  };
  priv.addEventListener("change", sync); sync();
  const files = S.uploads ? composerFiles() : null;
  // Hatırlatıcı (isteğe bağlı): iş açılınca işi açana kurulur; başkasına iş detayından kurulur.
  const rem = remWhen(null);
  const close = () => { S.composer = false; S.composerDue = null; renderList(); };
  const add = act(async () => {
    if (!title.value.trim()) { title.focus(); return; }
    const remTs = rem.value();
    if (remTs && !remCheckTs(remTs)) return;
    const body = { title: title.value, assignee_id: sel.value || null, due_at: due.value(), description: desc.value,
      private: priv.checked, priority: Number(prio.value) };
    if (extra && !priv.checked) { body.assignees = extra.ids(); body.approvers = appr.ids(); }
    const d = await api("POST", "/is-listesi/api/tasks", body);
    S.selId = d.task.id; S.composer = false; S.composerDue = null; S.f = "aktif";
    // İş açıldı; belgeler ayrı istekte yüklenir. Biri başarısız olursa iş kalır, belge işin detayından eklenebilir.
    const picked = files ? files.list() : [], failed = [];
    for (const f of picked) { try { await uploadFile(d.task.id, f); } catch (e) { failed.push(`${f.name}: ${e.message}`); } }
    if (remTs) {
      try { await api("POST", `/is-listesi/api/tasks/${d.task.id}/hatirlatici`, { remind_at: remTs, note: "" }); }
      catch (e) { failed.push(`Hatırlatıcı: ${e.message}`); }
    }
    if (failed.length) toast(`İş oluşturuldu ama tamamlanamayanlar var — ${failed.join(" · ")}`);
    else toast((picked.length ? `İş oluşturuldu, ${picked.length} belge yüklendi.` : "İş oluşturuldu.") +
      (remTs ? ` Hatırlatıcı: ${fmt(remTs)}` : ""), true);
    await refresh();
  });
  // Başlıkta Enter sonraki alana geçer (yanlışlıkla eksik iş açılmasın); Ctrl+Enter her yerden oluşturur.
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !(e.ctrlKey || e.metaKey)) { e.preventDefault(); (isAdmin() ? sel : due.date).focus(); }
  });
  const card = h("div", { class: "card composer" }, h("h3", {}, "Yeni iş"), title,
    h("div", { class: "more" }, h("div", { class: "form-grid" },
      isAdmin() ? h("label", {}, "Atanan kişi", sel) : null,
      h("div", { class: "lbl" }, "Son tarih", due.el), h("label", {}, "Öncelik", prio)), extraRow, desc,
      h("div", { class: "lbl" }, h("span", {}, icon("bell"), " Bana hatırlat (isteğe bağlı)"), rem.el), files ? files.el : null,
      h("label", { class: "check" }, priv, h("span", {}, h("b", {}, "Özel iş"),
        h("span", { class: "muted" }, isAdmin() ? " — yalnız ben göreyim; onaya gönderilmez, bitince “Tamamla” derim. Sonra görünür yapabilir ya da devredebilirim."
          : " — yöneticiler dahil yalnız ben göreyim; onaya gönderilmez, bitince “Tamamla” derim. İstediğimde görünür yapabilirim."))),
      h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: add }, icon("plus"), "Oluştur"),
        h("button", { type: "button", class: "btn-ghost", onclick: close }, "Vazgeç"),
        h("span", { class: "small muted" }, h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Enter"), " oluşturur, ", h("kbd", {}, "Esc"), " kapatır"))));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); add(e); }
    if (e.key === "Escape") close();
  });
  return card;
}

// Birden çok kişi seçici: açılır listeden seçilen kişi kaldırılabilir rozet olur.
function peoplePicker(placeholder) {
  const chosen = [];
  const box = h("div", { class: "picked" });
  const sel = h("select", {});
  const paint = () => {
    sel.replaceChildren(h("option", { value: "" }, placeholder));
    for (const p of S.peopleList) if (p.active && !chosen.includes(p.id)) sel.append(h("option", { value: p.id }, p.display_name));
    box.replaceChildren(...chosen.map((id) => h("span", { class: "pchip" }, avatar(id, "xs"), name(id),
      h("button", { type: "button", class: "btn-icon", title: "Çıkar", "aria-label": `${name(id)} çıkar`,
        onclick: () => { chosen.splice(chosen.indexOf(id), 1); paint(); } }, icon("x")))), sel);
  };
  sel.addEventListener("change", () => { if (sel.value) { chosen.push(Number(sel.value)); paint(); sel.focus(); } });
  paint();
  return { el: box, ids: () => chosen.slice() };
}

// ── iş durumu eylemleri (kart + detay ortak) ───────────────────────────────────────
async function doTransition(t, action, reason, extra) {
  const r = await api("POST", `/is-listesi/api/tasks/${t.id}/durum`, { action, reason: reason || "", ...(extra || {}) });
  const n = r.sonraki, k = r.kesinlesme;
  const oneri = (ad, x) => !x ? "" : x.hata ? ` ${ad} açılamadı: ${x.hata}` : x.var ? ` ${ad} zaten açık: ${x.title}`
    : ` ${ad} açıldı: ${x.title}${x.due_at ? ` (${fmt(x.due_at)})` : ""}`;
  toast({ basla: "İş başlatıldı.", gonder: "Onaya gönderildi.", tamamla: "İş tamamlandı.", onayla: "Onaylandı.",
    reddet: "Reddedildi.", yeniden_ac: "Yeniden açıldı." }[action] + oneri("Sonraki iş", n) + oneri("Kesinleşme işi", k),
    !(n && n.hata) && !(k && k.hata));
  S.rejectFor = null; await refresh();
}
// Kontrol şablonlu işi bitirme: sonuç seçilir (şablondaki adıyla, ör. "Tebliğ edildi" / "İade"), o dalın
// sonraki işi (süreçte sıradaki aşama) önerilir; sunucu aynı dosya / taraf / atananla açar.
function kontrolDialog(t, action) {
  const tpl = tplOf(t), dlg = $("dlg"), sonraki = !t.is_private;
  let sonuc = null;
  const ad = h("input", { maxlength: 200, class: "grow", placeholder: "Sonraki iş (boş: açılmaz)" });
  const gun = h("input", { type: "number", min: 0, max: 365, placeholder: "gün", style: "width:80px" });
  const ac = h("input", { type: "checkbox" }), hedef = h("div", { class: "small" });
  const note = h("textarea", { maxlength: 1800, rows: 3, placeholder: "Ne görüldü / ne yapıldı? (isteğe bağlı — ör. iade nedeni)" });
  // Kesinleşme günü: şablonun kesinleşme dalında (Tebliğ kontrol → Tebliğ edildi) sorulur; girilirse sunucu
  // o güne "Kesinleşme tarihi kontrol" işini de açar. Gün elle girilir.
  const kes = h("input", { type: "date" });
  const kesBox = h("div", { class: "next-box", hidden: true },
    h("label", { class: "lbl" }, "Kesinleşme günü (isteğe bağlı)", kes),
    h("div", { class: "small muted" }, "Bu güne “Kesinleşme tarihi kontrol” işi açılır; o güne kadar itiraz yoksa kesinleştirme talebi gönderilir."));
  const hedefCiz = () => {
    const n = tplByTitle(ad.value), x = n && n.asama ? asamaOf(n.asama) : null;
    hedef.replaceChildren(...(x ? ["Sonraki aşama: ", asamaTag(n.asama)] : ad.value.trim() ? [h("span", { class: "muted" }, "Süreç dışı iş")] : []));
  };
  ad.addEventListener("input", () => { ac.checked = !!ad.value.trim(); hedefCiz(); });
  const btns = Object.keys(SONUC).map((k) => {
    const dal = (tpl.sonuclar || {})[k];
    return h("button", { type: "button", class: "chip", "data-k": k,
      title: dal ? `Sonraki iş: ${dal.baslik || tpl.title}${dal.gun != null ? ` (+${dal.gun} gün)` : ""}` : "Sonraki iş önerilmez",
      onclick: () => {
        sonuc = k;
        btns.forEach((b) => b.classList.toggle("on", b.getAttribute("data-k") === k));
        const n = dal ? tplByTitle(dal.baslik || tpl.title) : null;
        ad.value = dal ? dal.baslik || tpl.title : "";
        gun.value = dal && dal.gun != null ? dal.gun : n && n.due_days != null ? n.due_days : "";
        ac.checked = !!dal; hedefCiz();
        kesBox.hidden = !(sonraki && tpl.kesinlesme === k);
        if (kesBox.hidden) kes.value = "";
      } }, (dal && dal.ad) || SONUC[k]);
  });
  const send = act(async () => {
    if (!sonuc) { toast("Kontrol sonucunu seçin."); return; }
    const dal = (tpl.sonuclar || {})[sonuc], ac2 = sonraki && ac.checked && ad.value.trim();
    const kesGun = !kesBox.hidden && /^\d{4}-\d{2}-\d{2}$/.test(kes.value) ? kes.value : "";
    const reason = [`Kontrol: ${((dal && dal.ad) || SONUC[sonuc]).toLocaleLowerCase("tr-TR")} (${tpl.kontrol})`, note.value.trim(),
      ac2 ? `Sonraki iş: ${ad.value.trim()}` : "", kesGun ? `Kesinleşme günü: ${kesGun.split("-").reverse().join(".")}` : ""]
      .filter(Boolean).join(" — ");
    await doTransition(t, action, reason, { sonuc,
      sonraki: ac2 ? { baslik: ad.value.trim(), gun: gun.value === "" ? null : Number(gun.value) } : false,
      ...(kesGun ? { kesinlesme: kesGun } : {}) });
    dlg.close();
  });
  dlg.replaceChildren(h("h3", {}, action === "tamamla" ? "İşi tamamla" : "Onaya gönder"),
    h("div", { class: "small muted" }, `“${t.title}”`, asamaOf(t) ? [" · ", asamaTag(t)] : null),
    h("div", { style: "margin:14px 0 6px;font-weight:600" }, tpl.kontrol),
    h("div", { class: "chips" }, btns),
    sonraki ? h("div", { class: "next-box" }, h("label", { class: "row small", style: "gap:6px" }, ac, "Sonraki işi aç"),
      h("div", { class: "row", style: "gap:6px" }, ad, gun), hedef,
      h("div", { class: "small muted" }, `Aynı dosya${t.taraf_adi ? `, taraf (${t.taraf_adi})` : ""} ve atananla açılır; süre bugünden gün sayısıdır (17:00).`)) : null,
    kesBox,
    h("label", { class: "lbl", style: "margin-top:12px" }, "Not", note),
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: "btn", onclick: send }, icon(action === "tamamla" ? "check" : "send"), action === "tamamla" ? "Tamamla" : "Onaya gönder"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}
// Onaya gönder / tamamla: işin şablonunda kontrol sorusu varsa önce sonuç sorulur.
const finish = (t, action) => (e) => { stop(e); const tp = tplOf(t); if (tp && tp.kontrol) kontrolDialog(t, action); else act(() => doTransition(t, action))(e); };
// Ret gerekçesi kutusu: kartta ya da (iş seçiliyse) detayda açılır.
function askReject(t) {
  return (e) => { stop(e); S.rejectFor = t.id; renderList(); if (S.selId === t.id) renderDetail(); };
}
const asking = (t) => S.rejectFor === t.id;
function flowButtons(t, small) {
  const mine = isMine(t), admin = isAdmin(), appr = canApprove(t), sm = small ? " sm" : "", b = [];
  const open = t.subtasks.filter((s) => s.status !== "tamam").length;
  const active = t.status === "acik" || t.status === "devam";
  if (t.status === "acik" && (mine || admin)) b.push(h("button", { type: "button", class: "btn-ghost" + sm, onclick: act(() => doTransition(t, "basla")) }, icon("play"), "Başla"));
  if (active && isOwnPrivate(t) && (!small || !open))
    b.push(h("button", { type: "button", class: "btn-ok" + sm, disabled: open > 0,
      onclick: finish(t, "tamamla") }, icon("check"), "Tamamla"));
  if (active && mine && !t.is_private && (!small || !open))
    b.push(h("button", { type: "button", class: "btn" + sm, disabled: open > 0,
      onclick: finish(t, "gonder") }, icon("send"), "Onaya gönder"));
  if (!small && isOwnPrivate(t)) b.push(h("button", { type: "button", class: "btn-ghost", onclick: () => transferDialog("publish"),
    title: "Özel olmaktan çıkar; yöneticiler görebilir" }, icon("eye"), "Görünür yap"));
  if (t.status === "onayda" && appr) b.push(
    h("button", { type: "button", class: "btn-ok" + sm, onclick: act(() => doTransition(t, "onayla")) }, icon("check"), "Onayla"),
    h("button", { type: "button", class: "btn-danger-soft" + sm, onclick: askReject(t) }, icon("undo"), "Reddet"));
  const canReopen = (t.status === "onaylandi" && appr) || (t.status === "tamamlandi" && (admin || t.created_by === S.me.id));
  if (!small && canReopen) b.push(h("button", { type: "button", class: "btn-ghost", onclick: act(() => doTransition(t, "yeniden_ac")) }, icon("undo"), "Yeniden aç"));
  return b;
}
function rejectBox(t, after) {
  const r = h("textarea", { placeholder: "Ret gerekçesi (zorunlu) — işe atanan kişi görecek", maxlength: 2000 });
  setTimeout(() => r.focus(), 0);
  return h("div", { class: "reject", onclick: stop }, r, h("div", { class: "row" },
    h("button", { type: "button", class: "btn-danger sm", onclick: act(async () => {
      if (!r.value.trim()) { r.focus(); toast("Ret gerekçesi yazın."); return; }
      await doTransition(t, "reddet", r.value); }) }, icon("undo"), "Reddi gönder"),
    h("button", { type: "button", class: "btn-ghost sm", onclick: (e) => { stop(e); S.rejectFor = null; after(); } }, "Vazgeç")));
}

// ── UYAP dosyası bağı ──────────────────────────────────────────────────────────────
// İş, CokluUyap'ta UYAP dosya kartından açıldıysa birim + dosya no taşır.
function dosyaText(t) { return t.dosya_id ? `${t.birim_adi || ""} ${t.dosya_no || ""}`.trim() : ""; }
// "UYAP'ta aç": bu bilgisayardaki CokluUyap (127.0.0.1:8800) UYAP'ın kendi dosya kartını açar.
// Kart verisi adresin # kısmında gider (sunucuya iletilmez); bkz. uyap_core/is_listesi.py.
const UYAP_YEREL = "http://127.0.0.1:8800";
function uyaptaAcUrl(t) {
  let kart = null;
  try { kart = t.dosya_uyap ? JSON.parse(t.dosya_uyap) : null; } catch (_) { kart = null; }
  if (!kart || !kart.dosyaId) {
    kart = { dosyaId: t.dosya_id, dosyaNo: t.dosya_no || "", birimId: t.birim_id || "", birimAdi: t.birim_adi || "",
      dosyaTurKodAciklama: t.dosya_tur || "" };
  }
  return `${UYAP_YEREL}/__uyap_kisi__/dosya-ac#${encodeURIComponent(JSON.stringify(kart))}`;
}
function dosyaTag(t) {
  if (!t.dosya_id) return null;
  return h("button", { type: "button", class: "tag dosya", title: `UYAP dosyası${t.dosya_tur ? " · " + t.dosya_tur : ""} — bu dosyanın işlerini göster`,
    onclick: (e) => { stop(e); S.q = t.dosya_no || dosyaText(t); S.f = "tumu"; renderHead(); renderList(); } },
    icon("clip"), dosyaText(t) || "UYAP dosyası");
}

function taskCard(t) {
  const subs = t.subtasks || [], done = subs.filter((s) => s.status === "tamam").length;
  const open = S.expanded.has(t.id);
  const quick = flowButtons(t, true);
  const card = h("div", { class: `card task s-${t.status}` + (S.selId === t.id ? " sel" : "") + (isLate(t) ? " late" : "") + (t.unseen ? " unseen" : ""),
    tabindex: 0, "data-id": t.id, onclick: () => openDetail(t.id) },
    h("div", { class: "row" }, h("span", { class: "t grow", title: t.unseen ? "Son bakışınızdan beri değişti" : null }, t.title),
      privTag(t), prioTag(t), latePill(t), pill(t.status)),
    h("div", { class: "meta" }, whoMany(assigneesOf(t)), dueChip(t), bellTag(t.reminder_at), dosyaTag(t), asamaTag(t), tagChips(t),
      t.ext_pending ? h("span", { class: "extp" }, icon("clock"), "Ek süre talebi") : null,
      subs.length ? h("span", { class: "prog" }, h("span", { class: "b" }, h("i", { style: `width:${Math.round(100 * done / subs.length)}%` })), `${done}/${subs.length}`) : null,
      t.note_count ? h("span", { title: "Not" }, icon("msg"), t.note_count) : null,
      t.file_count ? h("span", { title: "Belge" }, icon("clip"), t.file_count) : null,
      subs.length ? h("button", { type: "button", class: "expand", onclick: (e) => {
        stop(e); open ? S.expanded.delete(t.id) : S.expanded.add(t.id); renderList(); } },
        icon(open ? "up" : "down"), open ? "Gizle" : "Alt işler") : null,
      quick.length ? h("span", { class: "quick" + (S.view === "onay" ? " show" : ""), onclick: stop }, quick) : null));
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) openDetail(t.id); });
  if (asking(t) && S.selId !== t.id) card.append(rejectBox(t, renderList));
  if (open) card.append(h("div", { class: "subs-inline" }, subs.map((s) => subRow(s, t, false))));
  return card;
}

// ── alt iş satırı (liste ve detayda ortak) ─────────────────────────────────────────
function canEditSub(s, root) {
  if (isAdmin()) return true;
  if (locked(root)) return false;
  return isMine(root) || s.assignee_id === S.me.id;
}

function subRow(s, root, full) {
  const editable = canEditSub(s, root);
  const cb = h("input", { type: "checkbox", checked: s.status === "tamam", disabled: !editable,
    "aria-label": "Tamamlandı", onclick: stop,
    // Önce ekranda işaretle, sonra kaydet; hata olursa geri al. Tazeleme arka planda.
    onchange: async () => {
      const v = cb.checked; row.classList.toggle("done", v);
      try { await api("PATCH", `/is-listesi/api/tasks/${s.id}`, { done: v }); refresh(); }
      catch (e) { cb.checked = !v; row.classList.toggle("done", !v); toast(e.message); }
    } });
  const row = h("div", { class: "sub" + (s.status === "tamam" ? " done" : ""), onclick: stop },
    cb, h("span", { class: "st" }, s.title),
    s.assignee_id && s.assignee_id !== root.assignee_id ? avatar(s.assignee_id, "xs") : null,
    s.due_at ? dueChip(s) : null,
    full ? h("button", { type: "button", class: "btn-icon sub-bell", title: "Bu alt iş için hatırlatıcı kur",
      onclick: () => reminderCreateDialog({ taskId: s.id }) }, icon("bell")) : null);
  if (full && editable) {
    row.append(h("span", { class: "tools" },
      h("button", { type: "button", class: "btn-icon", title: "Yukarı", onclick: act(async () => {
        await api("PATCH", `/is-listesi/api/tasks/${s.id}`, { move: -1 }); await refresh(); }) }, icon("up")),
      h("button", { type: "button", class: "btn-icon", title: "Aşağı", onclick: act(async () => {
        await api("PATCH", `/is-listesi/api/tasks/${s.id}`, { move: 1 }); await refresh(); }) }, icon("down")),
      h("button", { type: "button", class: "btn-icon", title: "Düzenle", onclick: () => editSub(row, s) }, icon("edit")),
      confirmBtn("", async () => { await api("DELETE", `/is-listesi/api/tasks/${s.id}`); await refresh(); }, "btn-icon", "x")));
  }
  return row;
}

function editSub(row, s) {
  const title = h("input", { value: s.title, class: "grow", maxlength: 300 });
  const kids = [title];
  let sel = null, due = null;
  if (isAdmin()) {
    sel = peopleSelect(s.assignee_id); due = dueField(s.due_at);
    kids.push(sel, due.el);
  }
  const save = act(async () => {
    const body = { title: title.value };
    if (sel) { body.assignee_id = sel.value || null; body.due_at = due.value(); }
    await api("PATCH", `/is-listesi/api/tasks/${s.id}`, body); await refresh();
  });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") save(e); if (e.key === "Escape") refresh(); });
  row.replaceChildren(h("div", { class: "row grow" }, ...kids,
    h("button", { type: "button", class: "btn sm", onclick: save }, "Kaydet"),
    h("button", { type: "button", class: "btn-ghost sm", onclick: () => refresh() }, "Vazgeç")));
  title.focus();
}

// ── detay ──────────────────────────────────────────────────────────────────────────
function closeDetail() {
  S.selId = null; S.detail = null;
  $("detail").hidden = true; $("detail").replaceChildren();
  $("layout").classList.remove("split"); document.body.classList.remove("detail-open");
}

async function openDetail(id, quiet) {
  try {
    const d = await api("GET", `/is-listesi/api/tasks/${id}`);
    if (S.selId !== d.task.id) { S.tab = "zaman"; if (!asking(d.task)) S.rejectFor = null; }
    S.selId = d.task.id; S.detail = d.task;
    const row = (S.tasks || []).find((t) => t.id === id); if (row) row.unseen = false;
    renderDetail();
    if (!quiet) {
      renderList(); renderTabs();
      if (matchMedia("(max-width:900px)").matches) scrollTo({ top: 0 });
    }
  } catch (e) { closeDetail(); if (!quiet) toast(e.message); }
}

function flowHint(t) {
  const mine = isMine(t), admin = canApprove(t);
  const open = t.subtasks.filter((s) => s.status !== "tamam").length;
  if (t.status === "onaylandi") return [icon("check"), "Bu iş onaylandı."];
  if (t.status === "tamamlandi") return [icon("check"), "Bu iş tamamlandı."];
  if (t.status === "onayda") return admin ? [icon("shield"), lateKind(t) === "onay"
    ? "Onay gecikti: iş süresinde gönderildi, son tarih geçti." : "İş onayınızı bekliyor."]
    : [icon("lock"), "Onay bekleniyor; bu sırada değişiklik yapılamaz."];
  if (isOwnPrivate(t)) return [icon("lock"), open ? `Tamamlamak için ${open} alt işi bitirin. Özel iş onaya gönderilmez.`
    : "Özel iş: yalnız siz görürsünüz, onaya gönderilmez. Bitince “Tamamla”, paylaşmak için “Görünür yap”."];
  if (mine && open) return [icon("list"), `Onaya göndermek için ${open} alt işi tamamlayın.`];
  return null;
}

function renderDetail() {
  const t = S.detail, el = $("detail");
  if (!t) return;
  $("layout").classList.add("split"); document.body.classList.add("detail-open");
  el.hidden = false;
  const flow = flowButtons(t, false), hint = flowHint(t);
  const close = () => { closeDetail(); renderList(); };
  el.replaceChildren(
    h("div", { class: "d-bar" },
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn-ghost sm back", onclick: close }, icon("left"), "Liste"),
        h("div", { class: "tags grow" }, pill(t.status), latePill(t), privTag(t), prioTag(t)),
        h("button", { type: "button", class: "btn-icon close-x", title: "Kapat (Esc)", onclick: close }, icon("x"))),
      h("h2", {}, t.title),
      flow.length ? h("div", { class: "flow" }, flow) : null,
      asking(t) ? rejectBox(t, () => { renderDetail(); renderList(); }) : null,
      extBanner(t),
      hint ? h("div", { class: "hint" }, hint) : null),
    h("div", { class: "d-body" },
      h("dl", { class: "info" },
        h("dt", {}, assigneesOf(t).length > 1 ? "Atananlar" : "Atanan"), h("dd", {}, peopleList(t, "assignee")),
        t.is_private ? null : [h("dt", {}, "Onaylayan"), h("dd", {}, peopleList(t, "approver"))],
        h("dt", {}, "Son tarih"), h("dd", {}, t.due_at ? dueChip(t) : h("span", { class: "muted" }, "—")),
        t.dosya_id ? [h("dt", {}, "UYAP dosyası"), h("dd", {}, dosyaText(t) || t.dosya_id,
          t.dosya_tur ? h("span", { class: "small muted" }, ` · ${t.dosya_tur}`) : null,
          t.taraf_adi ? h("div", { class: "small" }, `Taraf: ${t.taraf_adi}`) : null,
          t.evrak_id ? h("div", { class: "small" }, `Belge: ${t.evrak_adi || t.evrak_id}`) : null,
          t.barkod ? h("div", { class: "small" }, `PTT barkodu: ${t.barkod}`,
            h("a", { class: "btn-ghost sm", href: `${UYAP_YEREL}/__uyap_kisi__/ptt#${encodeURIComponent(t.barkod)}`, target: "_blank",
              rel: "noopener noreferrer", title: "Bu bilgisayardaki CokluUyap ile ptt.gov.tr'den tebliğ durumunu sorgular",
              style: "margin-left:8px;display:inline-flex" }, "PTT sorgula")) : null,
          h("div", {}, h("a", { class: "btn-ghost sm", href: uyaptaAcUrl(t), target: "_blank", rel: "noopener noreferrer",
            title: "Bu bilgisayardaki CokluUyap'ta (127.0.0.1:8800) UYAP'ın dosya kartını açar", style: "margin-top:6px;display:inline-flex" },
            icon("clip"), "UYAP'ta aç")))] : null,
        asamaOf(t) ? [h("dt", {}, "Süreç"), h("dd", {}, asamaTag(t), h("span", { class: "small muted" }, ` ${asamaOf(t).s.title}`))] : null,
        h("dt", {}, "Etiketler"), h("dd", {}, h("span", { class: "row", style: "gap:6px;flex-wrap:wrap" }, tagChips(t),
          h("button", { type: "button", class: "btn-ghost sm", onclick: () => tagDialog(t) }, icon("edit"), (t.tags || []).length ? "Değiştir" : "Etiket ekle"))),
        nextReminder(t.id) ? [h("dt", {}, "Hatırlatma"), h("dd", {}, h("button", { type: "button", class: "linkish", onclick: () => reminderDialog() }, bellTag(nextReminder(t.id))))] : null,
        h("dt", {}, "Açan"), h("dd", {}, h("span", { class: "row", style: "gap:6px" }, who(t.created_by, "xs"),
          h("span", { class: "small muted" }, fmtShort(t.created_at))))),
      t.description ? h("div", { class: "desc" }, t.description) : null,
      detailActions(t),
      detailTabs(t)));
}

function detailActions(t) {
  const admin = isAdmin(), own = ownTask(t) && !locked(t);
  const btns = [];
  if (admin || own) btns.push(h("button", { type: "button", class: "btn-ghost sm", onclick: () => editMain() }, icon("edit"), "Düzenle"));
  if (admin) btns.push(h("button", { type: "button", class: "btn-ghost sm", onclick: () => transferDialog("transfer") }, icon("transfer"), "Devret"));
  if (admin && !t.is_private) btns.push(h("button", { type: "button", class: "btn-ghost sm", onclick: () => memberDialog() }, icon("users"), "Kişi ekle"));
  if (isMine(t) && !isDone(t) && !t.is_private) {
    const pending = (t.extensions || []).some((x) => x.status === "bekliyor");
    btns.push(h("button", { type: "button", class: "btn-ghost sm", disabled: pending, title: pending ? "Karar bekleyen talebiniz var" : null,
      onclick: () => extensionDialog() }, icon("clock"), "Ek süre iste"));
  }
  btns.push(h("button", { type: "button", class: "btn-ghost sm", onclick: () => reminderDialog() }, icon("bell"), "Hatırlatıcı"));
  if (admin || own) btns.push(confirmBtn("Sil", async () => {
    await api("DELETE", `/is-listesi/api/tasks/${t.id}`); closeDetail(); toast("İş silindi.", true); await refresh(); }));
  return btns.length ? h("div", { class: "actions" }, btns) : null;
}

function detailTabs(t) {
  const done = t.subtasks.filter((s) => s.status === "tamam").length;
  const tabs = [["zaman", "Zaman çizelgesi", t.notes.length || null],
    ["alt", "Alt işler", t.subtasks.length ? `${done}/${t.subtasks.length}` : null], ["belge", "Belgeler", t.files.length || null]];
  if (!tabs.some(([k]) => k === S.tab)) S.tab = "zaman";
  const body = { zaman: timelineContent, alt: subContent, belge: fileContent }[S.tab](t);
  return h("div", {}, h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, l, n]) => h("button", {
    type: "button", role: "tab", "aria-selected": S.tab === k ? "true" : "false", class: S.tab === k ? "on" : "",
    onclick: () => { S.tab = k; renderDetail(); } }, l, n ? h("span", { class: "n" }, n) : null))), body);
}

function editMain() {
  const t = S.detail, admin = isAdmin();
  const title = h("input", { value: t.title, maxlength: 300 });
  const sel = peopleSelect(t.assignee_id), due = dueField(t.due_at);
  const prio = prioSelect(t.priority);
  const curAsama = t.surec && t.asama ? `${t.surec}/${t.asama}` : "", asm = asamaSelect(curAsama);
  if (t.is_private) { sel.disabled = true; sel.title = "Özel işi başkasına vermek için “Devret”i kullanın."; }
  const desc = h("textarea", { maxlength: 5000 }); desc.value = t.description || "";
  $("detail").replaceChildren(h("div", { class: "d-bar" }, h("h2", { style: "margin:0" }, "İşi düzenle")),
    h("div", { class: "d-body", style: "padding-top:16px" },
      h("div", { class: "form-grid" }, h("label", { style: "grid-column:1/-1" }, "Başlık", title),
        admin ? h("label", {}, "Atanan kişi", sel) : null, h("div", { class: "lbl" }, "Son tarih", due.el), h("label", {}, "Öncelik", prio),
        S.surecler.length ? h("label", {}, "Süreç aşaması", asm) : null),
      h("label", { class: "lbl" }, "Açıklama", desc),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn", onclick: act(async () => {
          const body = { title: title.value, description: desc.value, due_at: due.value(), priority: Number(prio.value) };
          if (admin && !t.is_private) body.assignee_id = sel.value || null;
          if (asm.value !== curAsama) body.asama = asm.value;
          await api("PATCH", `/is-listesi/api/tasks/${t.id}`, body);
          toast("Kaydedildi.", true); await refresh(); }) }, icon("check"), "Kaydet"),
        h("button", { type: "button", class: "btn-ghost", onclick: () => renderDetail() }, "Vazgeç"))));
  title.focus();
}

function subContent(t) {
  const canAdd = isAdmin() || (t.assignee_id === S.me.id && !locked(t));
  const kids = t.subtasks.map((s) => subRow(s, t, true));
  if (!kids.length) kids.push(h("div", { class: "none-txt" }, "Henüz alt iş yok. İşi adımlara bölmek için ekleyin."));
  if (canAdd) {
    const title = h("input", { placeholder: "Alt iş ekle… (Enter)", class: "grow", maxlength: 300 });
    const sel = isAdmin() && !t.is_private ? peopleSelect(null) : null;
    const add = act(async () => {
      if (!title.value.trim()) { title.focus(); return; }
      await api("POST", "/is-listesi/api/tasks", { parent_id: t.id, title: title.value, assignee_id: sel ? sel.value || null : null });
      await refresh();
      const i = document.querySelector(".add-row input"); if (i) i.focus();
    });
    title.addEventListener("keydown", (e) => { if (e.key === "Enter") add(e); });
    kids.push(h("div", { class: "add-row" }, title, sel, h("button", { type: "button", class: "btn sm", onclick: add }, icon("plus"), "Ekle")));
  }
  return h("div", {}, kids);
}

const FTYPE = { pdf: ["PDF", "#dc2626"], udf: ["UDF", "#7c3aed"], doc: ["DOC", "#2563eb"], docx: ["DOC", "#2563eb"],
  xls: ["XLS", "#16a34a"], xlsx: ["XLS", "#16a34a"], jpg: ["JPG", "#0891b2"], jpeg: ["JPG", "#0891b2"],
  png: ["PNG", "#0891b2"], tif: ["TIF", "#0891b2"], tiff: ["TIF", "#0891b2"], zip: ["ZIP", "#a16207"] };

const FILE_ACCEPT = ".pdf,.udf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.tif,.tiff,.zip";
const uploadFile = (taskId, f) => { const fd = new FormData(); fd.append("file", f); return api("POST", `/is-listesi/api/tasks/${taskId}/files`, fd, true); };
function fileTag(filename) {
  const ext = (filename.split(".").pop() || "").toLowerCase(), [lab, col] = FTYPE[ext] || ["DOS", "#64748b"];
  return h("span", { class: "ftype", style: `background:${col}` }, lab);
}

function fileContent(t) {
  const lk = !isAdmin() && locked(t);
  const kids = [];
  if (!S.uploads) kids.push(h("div", { class: "warnbox" }, icon("alert"), "Belge yükleme bu sunucuda henüz açık değil."));
  else if (lk) kids.push(h("div", { class: "lock" }, icon("lock"), "Onaydaki/onaylanmış işe belge eklenemez."));
  else {
    const inp = h("input", { type: "file", accept: FILE_ACCEPT });
    const upload = async (f) => {
      if (!f) return;
      if (f.size > S.maxMb * 1048576) { toast(`Dosya ${S.maxMb} MB sınırını aşıyor.`); return; }
      try { await uploadFile(t.id, f); toast("Belge yüklendi (şifreli saklanıyor).", true); await refresh(); }
      catch (e) { toast(e.message); }
    };
    inp.addEventListener("change", () => upload(inp.files[0]));
    const drop = h("div", { class: "drop" }, h("div", { class: "ic" }, icon("upload")),
      h("div", { class: "grow" }, h("div", { style: "font-weight:600" }, "Belge yükleyin"),
        h("div", { class: "small muted" }, `Sürükleyip bırakın ya da seçin · PDF, UDF, Word, Excel, görsel, ZIP · en fazla ${S.maxMb} MB`)),
      h("button", { type: "button", class: "btn-ghost sm", onclick: () => inp.click() }, "Dosya seç"), inp);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files[0]); });
    kids.push(drop);
  }
  if (t.files.length) kids.push(h("div", { class: "files" }, t.files.map((f) => {
    const canDel = isAdmin() || (f.uploader_id === S.me.id && !lk);
    return h("div", { class: "file" }, fileTag(f.filename),
      h("div", { class: "grow" }, h("a", { class: "fn", href: `/is-listesi/api/files/${f.id}`, title: f.filename }, f.filename),
        h("div", { class: "small muted" }, `${sizeTxt(f.size)} · ${name(f.uploader_id)} · ${fmtShort(f.created_at)}`)),
      canDel ? confirmBtn("", async () => { await api("DELETE", `/is-listesi/api/files/${f.id}`); await refresh(); }, "btn-icon", "trash") : null);
  })));
  else if (S.uploads) kids.push(h("div", { class: "none-txt" }, "Henüz belge yok."));
  return h("div", {}, kids);
}

// ── kişiler (atananlar / onay yetkilileri) ────────────────────────────────────────
function peopleList(t, role) {
  const ids = role === "assignee" ? assigneesOf(t) : (t.approvers || []);
  const rows = ids.map((id, i) => h("span", { class: "person" }, who(id, "xs"),
    isAdmin() && !(role === "assignee" && i === 0) ? confirmBtn("", async () => {
      await api("POST", `/is-listesi/api/tasks/${t.id}/kisiler/cikar`, { user_id: id, role }); toast("Kişi çıkarıldı.", true); await refresh();
    }, "btn-icon", "x") : null));
  if (role === "approver") rows.unshift(h("span", { class: "person muted small" }, "Yöneticiler"));
  return h("div", { class: "people" }, rows.length ? rows : h("span", { class: "muted" }, "—"));
}

function memberDialog() {
  const t = S.detail, dlg = $("dlg");
  const sel = h("select", {}, h("option", { value: "" }, "Kişi seçin…"));
  for (const p of S.peopleList) if (p.active) sel.append(h("option", { value: p.id }, p.display_name));
  const r1 = h("input", { type: "radio", name: "mrole", value: "assignee", checked: true });
  const r2 = h("input", { type: "radio", name: "mrole", value: "approver" });
  const note = h("textarea", { placeholder: "Neden ekleniyor? (zorunlu — zaman çizelgesine yazılır)", maxlength: 2000 });
  dlg.replaceChildren(h("h3", {}, "İşe kişi ekle"), h("div", { class: "small muted" }, `“${t.title}”`),
    h("label", { class: "lbl" }, "Kişi", sel),
    h("div", { class: "lbl", style: "margin-top:14px" }, "Rol"),
    h("label", { class: "radio" }, r1, h("span", {}, h("b", {}, "Ek atanan"),
      h("span", { class: "muted" }, " — işi birlikte yürütür, alt iş ekler, onaya gönderebilir, ek süre isteyebilir."))),
    h("label", { class: "radio" }, r2, h("span", {}, h("b", {}, "Onay yetkilisi"),
      h("span", { class: "muted" }, " — bu işi onaylayabilir/reddedebilir ve ek süre taleplerine karar verir."))),
    h("label", { class: "lbl" }, "Not", note),
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        if (!sel.value) { toast("Kişi seçin."); return; }
        if (!note.value.trim()) { note.focus(); toast("Ekleme notu yazın."); return; }
        await api("POST", `/is-listesi/api/tasks/${t.id}/kisiler`, { user_id: Number(sel.value), role: r2.checked ? "approver" : "assignee", note: note.value });
        dlg.close(); toast("Kişi eklendi.", true); S.tab = "zaman"; await refresh(); }) }, icon("plus"), "Ekle"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}

// ── hatırlatıcı ────────────────────────────────────────────────────────────────────
// Kurulan saatte Web Push + Telegram (kişinin açtığı kanallar).
// İşe bağlı: iş detayındaki "Hatırlatıcı" (atanan, kendi açtığı, özel — görebildiği her iş). Kullanıcı kendine,
// yönetici işi görebilen herkese kurar. İşten bağımsız: takvimdeki "Hatırlatıcı" düğmesi; kişiseldir.
const nextReminder = (id) => { const r = (S.tasks || []).find((x) => x.id === id) || S.mine.find((x) => x.id === id); return r ? r.reminder_at : null; };
const bellTag = (ts) => ts ? h("span", { class: "bell-tag", title: `Hatırlatma: ${fmt(ts)}` }, icon("bell"), fmtShort(ts)) : null;

// "Ne zaman?" alanı: hazır seçenekler + tarih/saat. dueAt varsa son tarihe göre seçenekler de çıkar.
function remWhen(dueAt, day) {
  const now = Date.now() / 1000, when = dueField(null);
  const setTs = (ts) => {
    const d = new Date(ts * 1000);
    when.date.value = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
    when.el.querySelector("input[type=time]").value = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
  };
  const at = (dayOffset, hh, mm = 0) => { const d = new Date(); d.setDate(d.getDate() + dayOffset); d.setHours(hh, mm, 0, 0); return d.getTime() / 1000; };
  const presets = [["1 saat sonra", Math.ceil((now + 3600) / 300) * 300], ["Bu akşam 17:00", at(0, 17)],
    ["Yarın 09:00", at(1, 9)], ["Pazartesi 09:00", at((8 - new Date().getDay()) % 7 || 7, 9)]];
  if (dueAt) presets.push(["Son tarihten 1 gün önce", dueAt - 86400], ["Son tarihten 1 saat önce", dueAt - 3600], ["Son tarihte", dueAt]);
  const chips = h("div", { class: "rem-presets" }, presets.filter(([, ts]) => ts > now + 30).map(([l, ts]) =>
    h("button", { type: "button", class: "chip", onclick: (e) => {
      setTs(ts); chips.querySelectorAll(".chip").forEach((c) => c.classList.remove("on")); e.currentTarget.classList.add("on"); } }, l)));
  // Takvimde bir güne tıklanarak açıldıysa o gün 09:00 (geçmişse boş bırakılır).
  if (day) { const d = new Date(day); d.setHours(9, 0, 0, 0); if (d.getTime() / 1000 > now) setTs(d.getTime() / 1000); }
  return { el: h("div", {}, chips, h("div", { style: "margin-top:8px" }, when.el)), value: () => when.value() };
}
const remHint = () => h("div", { class: "small muted", style: "margin-top:10px" }, icon("bell"),
  " Bildirim, “Bildirimler” menüsünden açtığınız cihazlara ve bağladıysanız Telegram'a gider.");
function remCheckTs(ts) {
  if (!ts) { toast("Tarih ve saat seçin."); return false; }
  if (ts < Date.now() / 1000 - 60) { toast("Geçmiş bir zamana hatırlatıcı kurulamaz."); return false; }
  return true;
}
function remPeopleSelect(ids) {
  return h("select", {}, S.peopleList.filter((p) => p.active && (!ids || ids.has(p.id))).map((p) =>
    h("option", { value: p.id, selected: p.id === S.me.id }, p.id === S.me.id ? `${p.display_name} (ben)` : p.display_name)));
}
function remRow(r, after) {
  const sent = !!r.sent_at;
  const canDel = r.created_by === S.me.id || r.user_id === S.me.id || (isAdmin() && r.task_id);
  return h("div", { class: "rem" + (sent ? " sent" : "") },
    h("span", { class: "ic" }, icon(sent ? "check" : "bell")),
    h("div", { class: "grow" }, h("div", { class: "w" }, fmt(r.remind_at),
      r.user_id !== S.me.id ? h("span", { class: "muted" }, ` · ${name(r.user_id)}`) : null,
      h("span", { class: "muted small" }, sent ? " · gönderildi" : " · bekliyor")),
      r.sub_title ? h("div", { class: "small" }, `↳ ${r.sub_title}`) : null,
      r.note ? h("div", { class: "small muted" }, r.note) : null),
    canDel ? h("button", { type: "button", class: "btn-icon", title: sent ? "Kaldır" : "İptal et", onclick: act(async () => {
      await api("DELETE", `/is-listesi/api/hatirlatici/${r.id}`); toast("Hatırlatıcı kaldırıldı.", true); await refresh(); after(); }) }, icon("x")) : null);
}

// İşe bağlı hatırlatıcı (iş detayından).
async function reminderDialog() {
  const t = S.detail, dlg = $("dlg");
  let list = [];
  try { list = (await api("GET", `/is-listesi/api/tasks/${t.id}/hatirlatici`)).reminders; } catch (e) { toast(e.message); return; }
  const when = remWhen(t.due_at);
  const note = h("input", { type: "text", maxlength: 500, placeholder: "Not (isteğe bağlı) — ör. “Müvekkili ara”" });
  // Yönetici: işi görebilenler (atananlar, onay yetkilileri, ben). Özel işte yalnız kendisi.
  const to = isAdmin() && !t.is_private ? remPeopleSelect(new Set([S.me.id, ...assigneesOf(t), ...(t.approvers || [])])) : null;
  dlg.replaceChildren(...[h("h3", {}, "Hatırlatıcı"), h("div", { class: "small muted" }, `“${t.title}”`),
    list.length ? h("div", { class: "rem-list" }, list.map((r) => remRow(r, reminderDialog))) : null,
    h("div", { class: "lbl" }, "Ne zaman?"), when.el,
    to ? h("label", { class: "lbl" }, "Kime", to) : null,
    h("label", { class: "lbl" }, "Not", note), remHint(),
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        const ts = when.value();
        if (!remCheckTs(ts)) return;
        await api("POST", `/is-listesi/api/tasks/${t.id}/hatirlatici`, { remind_at: ts, note: note.value, user_id: to ? Number(to.value) : null });
        dlg.close(); toast(`Hatırlatıcı kuruldu: ${fmt(ts)}`, true); await refresh(); }) }, icon("bell"), "Kur"),
      (t.subtasks || []).some((x) => x.status !== "tamam") ? h("button", { type: "button", class: "btn-ghost",
        onclick: () => reminderCreateDialog({ taskId: t.id }) }, "Alt işe kur…") : null,
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Kapat"))].filter(Boolean));
  if (!dlg.open) dlg.showModal();
}

// Genel "Hatırlatıcı kur": işi açmadan. İlgili iş/alt iş isteğe bağlı (boşsa işten bağımsız).
// o.taskId: önceden seçili iş ya da alt iş; o.day: takvimde seçili gün.
async function reminderCreateDialog(o = {}) {
  const dlg = $("dlg"), admin = isAdmin();
  let roots = [];
  try { roots = (await api("GET", `/is-listesi/api/tasks?filtre=${admin ? "tumu" : "benim"}`)).tasks.filter((t) => !isDone(t) || t.id === o.taskId); }
  catch (e) { toast(e.message); return; }
  // Seçenekler: ana işler ve altında açık alt işleri.
  const opts = [];
  for (const t of roots.sort(byDue)) {
    opts.push({ id: t.id, root: t, sub: null, label: t.title });
    for (const s of t.subtasks || []) if (s.status !== "tamam" || s.id === o.taskId) opts.push({ id: s.id, root: t, sub: s, label: `↳ ${s.title}` });
  }
  const byId = new Map(opts.map((x) => [x.id, x]));
  const filter = h("input", { type: "search", placeholder: "İş ara…" });
  const sel = h("select", {});
  const fill = () => {
    const q = filter.value.trim().toLocaleLowerCase("tr-TR"), cur = Number(sel.value) || o.taskId || 0;
    sel.replaceChildren(h("option", { value: "" }, "— İşe bağlı değil —"),
      ...opts.filter((x) => !q || `${x.root.title} ${x.sub ? x.sub.title : ""}`.toLocaleLowerCase("tr-TR").includes(q) || x.id === cur)
        .map((x) => h("option", { value: x.id, selected: x.id === cur }, x.label)));
  };
  filter.addEventListener("input", fill);
  fill();
  const title = h("input", { type: "text", maxlength: 300, placeholder: "Neyi hatırlatalım? — ör. “Bilirkişiyi ara”" });
  const titleRow = h("label", { class: "lbl" }, "Başlık", title);
  const whenBox = h("div", {}), toBox = h("div", {});
  const note = h("input", { type: "text", maxlength: 500, placeholder: "Not (isteğe bağlı) — ör. “Müvekkili ara”" });
  let when = null, to = null;
  const sync = () => {
    const x = byId.get(Number(sel.value));
    titleRow.hidden = !!x;
    when = remWhen(x ? (x.sub ? x.sub.due_at : x.root.due_at) : null, o.day);
    whenBox.replaceChildren(h("div", { class: "lbl" }, "Ne zaman?"), when.el);
    // Kime: bağımsızda yönetici herkese; ana işte yönetici işi görenlere; alt işte herkes işi görenlere.
    let ids = null;
    if (x) {
      const r = x.root;
      ids = new Set([S.me.id, r.created_by, ...assigneesOf(r), ...(r.approvers || []), ...(r.subtasks || []).map((s) => s.assignee_id)].filter(Boolean));
    }
    const can = x ? (!x.root.is_private && (x.sub || admin)) : admin;
    to = can ? remPeopleSelect(ids) : null;
    if (to && x && x.sub && x.sub.assignee_id && ids.has(x.sub.assignee_id)) to.value = String(x.sub.assignee_id);
    toBox.replaceChildren(...(to ? [h("label", { class: "lbl" }, "Kime", to)] : []));
  };
  sel.addEventListener("change", sync);
  sync();
  dlg.replaceChildren(h("h3", {}, "Hatırlatıcı kur"),
    h("div", { class: "lbl" }, "İlgili iş (isteğe bağlı)"), h("div", { class: "rem-task" }, filter, sel),
    titleRow, whenBox, toBox, h("label", { class: "lbl" }, "Not", note), remHint(),
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        const taskId = Number(sel.value) || null;
        if (!taskId && !title.value.trim()) { title.focus(); toast("Başlık yazın ya da bir iş seçin."); return; }
        const ts = when.value();
        if (!remCheckTs(ts)) return;
        await api("POST", "/is-listesi/api/hatirlatici", { task_id: taskId, title: title.value, remind_at: ts, note: note.value,
          user_id: to ? Number(to.value) : null });
        dlg.close(); toast(`Hatırlatıcı kuruldu: ${fmt(ts)}`, true); await refresh(); }) }, icon("bell"), "Kur"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  if (!dlg.open) dlg.showModal();
  (o.taskId ? note : title).focus();
}

// Takvimdeki bağımsız hatırlatıcıya tıklanınca.
function freeReminderView(r) {
  const dlg = $("dlg"), sent = !!r.sent_at;
  dlg.replaceChildren(...[h("h3", {}, r.title),
    h("div", { class: "rem-view" }, h("span", { class: "ic" }, icon(sent ? "check" : "bell")),
      h("div", {}, h("b", {}, fmt(r.remind_at)), h("div", { class: "small muted" }, sent ? "Gönderildi" : "Bekliyor",
        r.user_id !== S.me.id ? ` · Kime: ${name(r.user_id)}` : "", r.created_by !== S.me.id ? ` · Kuran: ${name(r.created_by)}` : ""))),
    r.note ? h("div", { class: "desc", style: "margin-top:12px" }, r.note) : null,
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: sent ? "btn-ghost" : "btn-danger-soft", onclick: act(async () => {
        await api("DELETE", `/is-listesi/api/hatirlatici/${r.id}`); dlg.close(); toast("Hatırlatıcı kaldırıldı.", true); await refresh(); }) },
        icon(sent ? "trash" : "x"), sent ? "Kaldır" : "İptal et"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Kapat"))].filter(Boolean));
  dlg.showModal();
}

// ── Hatırlatıcılar ekranı (sol menü) ───────────────────────────────────────────────
const remLabel = (r) => r.task_id ? (r.sub_title ? `${r.task_title} › ${r.sub_title}` : r.task_title) : r.title;
function renderReminders() {
  const me = S.me.id, q = S.q.trim().toLocaleLowerCase("tr-TR");
  const all = (S.myRems || []).filter((r) => !q || `${remLabel(r)} ${r.note || ""} ${name(r.user_id)}`.toLocaleLowerCase("tr-TR").includes(q));
  const pend = all.filter((r) => !r.sent_at), mine = pend.filter((r) => r.user_id === me), others = pend.filter((r) => r.user_id !== me);
  const sent = all.filter((r) => r.sent_at).sort((a, b) => b.remind_at - a.remind_at);
  const t1 = dayStart(1), t2 = dayStart(2), wk = +weekStart(new Date()) / 1000 + 7 * 86400;
  const groups = [["Bugün", mine.filter((r) => r.remind_at < t1)], ["Yarın", mine.filter((r) => r.remind_at >= t1 && r.remind_at < t2)],
    ["Bu hafta", mine.filter((r) => r.remind_at >= t2 && r.remind_at < wk)], ["Daha sonra", mine.filter((r) => r.remind_at >= wk)],
    ["Başkalarına kurduklarım", others], ["Gönderilenler (son 60 gün)", sent]];
  const row = (r) => {
    const canDel = r.created_by === me || r.user_id === me || (isAdmin() && r.task_id);
    const who = r.user_id !== me ? `Kime: ${name(r.user_id)}` : r.created_by !== me ? `Kuran: ${name(r.created_by)}` : "";
    return h("div", { class: "card rem-row" + (r.sent_at ? " sent" : "") },
      h("div", { class: "rem-when" }, h("b", {}, FMT_TIME.format(new Date(r.remind_at * 1000))),
        h("span", {}, new Date(r.remind_at * 1000).toLocaleDateString("tr-TR", { day: "numeric", month: "short", weekday: "short" }))),
      h("span", { class: "ic" }, icon(r.sent_at ? "check" : "bell")),
      h("div", { class: "grow" },
        r.task_id ? h("button", { type: "button", class: "rem-link", title: "İşi aç", onclick: () => openTaskFrom(r.task_id, r.task_private) },
          r.sub_title ? h("span", { class: "muted" }, `${r.task_title} › `) : null, r.sub_title || r.task_title)
          : h("b", {}, r.title),
        r.note ? h("div", { class: "small muted" }, r.note) : null,
        h("div", { class: "small muted" }, r.task_id ? (r.sub_title ? "Alt işe bağlı" : "İşe bağlı") : "İşten bağımsız", who ? ` · ${who}` : "")),
      canDel ? confirmBtn("", async () => { await api("DELETE", `/is-listesi/api/hatirlatici/${r.id}`); toast("Hatırlatıcı kaldırıldı.", true); await refresh(); }, "btn-icon", r.sent_at ? "trash" : "x") : null);
  };
  const kids = [];
  for (const [label, arr] of groups) {
    if (!arr.length) continue;
    kids.push(h("div", { class: "group-h" + (label === "Bugün" ? " late" : "") }, label, h("span", { class: "n" }, arr.length)));
    kids.push(...arr.map(row));
  }
  if (!kids.length) kids.push(h("div", { class: "card empty" }, h("div", { class: "ic" }, icon("bell")),
    h("b", {}, q ? "Bu aramada hatırlatıcı yok" : "Hatırlatıcınız yok"),
    h("button", { type: "button", class: "btn sm", style: "margin-top:10px", onclick: () => reminderCreateDialog() }, icon("bell"), "Hatırlatıcı kur")));
  $("list").replaceChildren(...kids);
}

// ── ek süre ────────────────────────────────────────────────────────────────────────
function extensionDialog() {
  const t = S.detail, dlg = $("dlg");
  const base = t.due_at && t.due_at * 1000 > Date.now() ? t.due_at : Math.floor(Date.now() / 1000);
  const due = dueField(base + 7 * 86400);
  const reason = h("textarea", { placeholder: "Neden ek süre gerekiyor? (zorunlu)", maxlength: 2000 });
  dlg.replaceChildren(h("h3", {}, "Ek süre iste"), h("div", { class: "small muted" }, `“${t.title}”`),
    h("div", { class: "small", style: "margin-top:10px" }, "Mevcut son tarih: ", t.due_at ? h("b", {}, fmt(t.due_at)) : "yok"),
    h("div", { class: "lbl" }, "Yeni son tarih", due.el), h("label", { class: "lbl" }, "Gerekçe", reason),
    h("div", { class: "small muted", style: "margin-top:8px" }, "Talep, yönetici ya da işin onay yetkilisi karar verene kadar bekler; kabul edilirse son tarih güncellenir."),
    h("div", { class: "actions", style: "margin-top:16px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        if (!due.value()) { toast("Yeni tarihi seçin."); return; }
        if (!reason.value.trim()) { reason.focus(); toast("Gerekçe yazın."); return; }
        await api("POST", `/is-listesi/api/tasks/${t.id}/ek-sure`, { new_due: due.value(), reason: reason.value });
        dlg.close(); toast("Ek süre talebi gönderildi.", true); S.tab = "zaman"; await refresh(); }) }, icon("send"), "Talep et"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}

function extBanner(t) {
  const x = (t.extensions || []).find((e) => e.status === "bekliyor");
  if (!x) return null;
  const range = `${x.old_due ? fmtShort(x.old_due) : "tarihsiz"} → ${fmtShort(x.new_due)}`;
  if (!canApprove(t)) return h("div", { class: "ext-banner" }, icon("clock"),
    h("div", { class: "grow" }, h("b", {}, "Ek süre talebi karar bekliyor"), h("div", { class: "small" }, `${range} · ${x.reason}`)));
  const note = h("textarea", { placeholder: "Karar notu (reddederken zorunlu)", maxlength: 2000 });
  const decide = (approve) => act(async () => {
    if (!approve && !note.value.trim()) { note.focus(); toast("Ret gerekçesi yazın."); return; }
    await api("POST", `/is-listesi/api/ek-sure/${x.id}/karar`, { approve, note: note.value });
    toast(approve ? "Ek süre kabul edildi; son tarih güncellendi." : "Ek süre talebi reddedildi.", true); await refresh();
  });
  return h("div", { class: "ext-banner" }, icon("clock"), h("div", { class: "grow" },
    h("b", {}, `${name(x.requester_id)} ek süre istiyor`), h("div", { class: "small" }, range),
    h("div", { class: "quote" }, x.reason), note,
    h("div", { class: "row", style: "margin-top:8px" },
      h("button", { type: "button", class: "btn-ok sm", onclick: decide(true) }, icon("check"), "Kabul et"),
      h("button", { type: "button", class: "btn-danger-soft sm", onclick: decide(false) }, icon("undo"), "Reddet"))));
}

// ── zaman çizelgesi (kronolojik: eskiden yeniye) ───────────────────────────────────
const ROLE_TXT = { assignee: "ek atanan", approver: "onay yetkilisi" };
const FIELD_TXT = { title: "başlık", description: "açıklama", due_at: "son tarih", priority: "öncelik", asama: "süreç aşaması",
  dosya: "UYAP dosyası" };
function eventView(e) {
  const d = e.detail || {};
  switch (e.kind) {
    case "created": return d.sub ? ["plus", "", `alt iş ekledi: ${d.title}`] : ["plus", "", "işi oluşturdu"];
    case "assigned": return ["users", "", d.assignee_id ? `atadı → ${name(d.assignee_id)}` : "atamayı kaldırdı"];
    case "edited": return ["edit", "", `işi düzenledi (${(d.fields || []).map((f) => FIELD_TXT[f] || f).join(", ")})`];
    case "subtask": return ["check", d.done ? "ok" : "", `${d.done ? "alt işi tamamladı" : "alt işi yeniden açtı"}: ${d.title}`];
    case "subtask_deleted": return ["x", "", `alt işi sildi: ${d.title}`];
    case "status": return ["play", "", "işi başlattı"];
    case "submitted": return ["send", "warn", "işi onaya gönderdi"];
    case "approved": return ["check", "ok", "işi onayladı"];
    case "rejected": return ["undo", "bad", "işi reddetti"];
    case "reopened": return ["undo", "", "işi yeniden açtı"];
    case "file": return ["clip", "", `belge ekledi: ${d.filename}`];
    case "file_deleted": return ["trash", "", `belge sildi: ${d.filename}`];
    case "transferred": return ["transfer", "", `devretti → ${name(d.to_id)}` +
      (d.hidden && d.hidden.length ? ` (devir öncesi gizlenen: ${d.hidden.join(", ")})` : "")];
    case "published": return ["eye", "", "işi görünür yaptı" + (d.hidden && d.hidden.length ? ` (gizlenen: ${d.hidden.join(", ")})` : "")];
    case "member_added": return ["users", d.role === "approver" ? "warn" : "", `${ROLE_TXT[d.role] || d.role} ekledi: ${name(d.user_id)}`, d.note];
    case "member_removed": return ["x", "", `${ROLE_TXT[d.role] || d.role} çıkardı: ${name(d.user_id)}`, d.note];
    case "ext_requested": return ["clock", "warn", `ek süre istedi: ${d.old_due ? fmtShort(d.old_due) : "tarihsiz"} → ${fmtShort(d.new_due)}`, d.reason];
    case "ext_approved": return ["check", "ok", `ek süreyi kabul etti — yeni son tarih ${fmtShort(d.new_due)}`, d.note];
    case "ext_rejected": return ["undo", "bad", "ek süre talebini reddetti", d.note];
    case "completed": return ["check", "ok", "işi tamamladı"];
    case "tags": return ["flag", "", [(d.added || []).length ? `etiket ekledi: ${d.added.join(", ")}` : "",
      (d.removed || []).length ? `etiket çıkardı: ${d.removed.join(", ")}` : ""].filter(Boolean).join(" · ") || EVENTS.tags];
    default: return ["hist", "", EVENTS[e.kind] || e.kind];
  }
}

function timelineContent(t) {
  // Not olayları ("not ekledi") notun kendisiyle gösterilir; onay/ret gerekçeleri de not olarak düşer.
  const items = [
    ...t.events.filter((e) => e.kind !== "note" && e.kind !== "deleted").map((e) => ({ at: e.created_at, ord: 0, id: e.id, e })),
    ...t.notes.map((n) => ({ at: n.created_at, ord: 1, id: n.id, n })),
  ].sort((a, b) => a.at - b.at || a.ord - b.ord || a.id - b.id);
  const body = h("textarea", { placeholder: "Not yazın… (Ctrl+Enter ile ekle)", maxlength: 5000 });
  const when = h("input", { type: "datetime-local", title: "İsteğe bağlı: notun ilgili olduğu tarih/saat" });
  const add = act(async () => {
    if (!body.value.trim()) { body.focus(); return; }
    await api("POST", `/is-listesi/api/tasks/${t.id}/notes`, { body: body.value, note_at: fromLocalInput(when.value) });
    await refresh();
  });
  body.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) add(e); });
  let lastDay = "";
  const DAY = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "numeric", month: "long", year: "numeric", weekday: "long" });
  const rows = [];
  for (const it of items) {
    const day = DAY.format(new Date(it.at * 1000));
    if (day !== lastDay) { rows.push(h("div", { class: "tl-day" }, day)); lastDay = day; }
    const time = FMT_TIME.format(new Date(it.at * 1000));
    if (it.n) {
      const n = it.n;
      rows.push(h("div", { class: "tl-item note-item" }, h("span", { class: "tl-dot" }, avatar(n.author_id, "xs")),
        h("div", { class: "grow" }, h("div", { class: "tl-h" }, h("b", {}, name(n.author_id)), h("span", { class: "muted" }, " not ekledi"),
          h("time", {}, time), n.note_at ? h("span", { class: "when" }, icon("cal"), fmt(n.note_at)) : null),
          h("div", { class: "nb" }, n.body))));
    } else {
      const [ic, tone, txt, quote] = eventView(it.e);
      rows.push(h("div", { class: "tl-item" + (tone ? " " + tone : "") }, h("span", { class: "tl-dot ic" }, icon(ic)),
        h("div", { class: "grow" }, h("div", { class: "tl-h" }, h("b", {}, name(it.e.actor_id)), ` ${txt}`, h("time", {}, time)),
          quote ? h("div", { class: "nb" }, quote) : null)));
    }
  }
  return h("div", {}, rows.length ? h("div", { class: "tl" }, rows) : h("div", { class: "none-txt" }, "Henüz kayıt yok."),
    h("div", { class: "tl-compose" }, body,
      h("div", { class: "note-form" }, h("span", { class: "small muted" }, "İlgili tarih/saat"), when,
        h("span", { class: "grow" }), h("button", { type: "button", class: "btn sm", onclick: add }, "Not ekle"))));
}

// ── devret / görünür yap ──────────────────────────────────────────────────────────
function transferDialog(mode) {
  const t = S.detail, dlg = $("dlg"), pub = mode === "publish";
  const sel = h("select", {}, h("option", { value: "" }, "Kişi seçin…"));
  for (const p of S.peopleList) if (p.active && (p.id !== t.assignee_id || t.is_private))
    sel.append(h("option", { value: p.id }, p.display_name));
  const opts = [["subtasks", "Alt işler", t.subtasks.length], ["notes", "Notlar", t.notes.length],
    ["files", "Belgeler", t.files.length], ["history", "İşlem geçmişi", t.events.length]];
  const boxes = {};
  const rows = opts.map(([k, label, n]) => {
    const cb = h("input", { type: "checkbox", checked: true });
    const state = h("span", { class: "vis" });
    const paint = () => state.replaceChildren(icon(cb.checked ? "eye" : "eyeoff"), cb.checked ? "Gösterilecek" : "Gizlenecek");
    cb.addEventListener("change", paint); paint(); boxes[k] = cb;
    return h("label", { class: "vis-row" + (n ? "" : " none") }, cb, h("span", { class: "grow" }, label,
      h("span", { class: "muted small" }, ` · ${n} kayıt`)), state);
  });
  dlg.replaceChildren(h("h3", {}, pub ? "İşi görünür yap" : "İşi devret"),
    h("div", { class: "small muted" }, `“${t.title}”`),
    pub ? h("div", { class: "small", style: "margin-top:10px" },
      "İş size atanmış olarak kalır; özel olmaktan çıkar ve ",
      isAdmin() ? "diğer yöneticilere görünür olur." : "yöneticilere görünür olur.")
      : h("label", { class: "lbl" }, "Kime devredilecek?", sel),
    h("div", { class: "lbl", style: "margin-top:16px" }, pub ? "Şu ana kadarki kayıtlar" : "Devir öncesi kayıtlar yeni kişiye"),
    h("div", { class: "vis-list" }, rows),
    h("div", { class: "small muted", style: "margin-top:10px" },
      `Gizlenen kayıtları yalnız siz görmeye devam edersiniz. ${pub ? "Bundan sonra" : "Devirden sonra"} eklenenler herkese normal görünür.`,
      !pub && t.is_private ? " Özel iş, devirle birlikte devralan kişiye ve yöneticilere görünür olur." : ""),
    h("div", { class: "actions", style: "margin-top:18px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        const show = Object.fromEntries(Object.entries(boxes).map(([k, cb]) => [k, cb.checked]));
        if (pub) {
          await api("POST", `/is-listesi/api/tasks/${t.id}/gorunur`, { show });
          dlg.close(); toast("İş artık görünür.", true); await refresh(); return;
        }
        if (!sel.value) { toast("Devredilecek kişiyi seçin."); return; }
        await api("POST", `/is-listesi/api/tasks/${t.id}/devret`, { to_user_id: Number(sel.value), show });
        dlg.close(); toast(`İş devredildi: ${name(Number(sel.value))}`, true); await refresh(); }) },
        icon(pub ? "eye" : "transfer"), pub ? "Görünür yap" : "Devret"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}

// ── kullanıcılar (yönetici) ───────────────────────────────────────────────────────
async function renderUsers(shown) {
  const d = await api("GET", "/is-listesi/api/users");
  const uname = h("input", { placeholder: "kullanici.adi", maxlength: 40 });
  const dname = h("input", { placeholder: "Ad Soyad", maxlength: 80 });
  const role = h("select", {}, h("option", { value: "user" }, "Kullanıcı"), h("option", { value: "admin" }, "Yönetici"));
  const pw = h("input", { placeholder: "Boş bırakın → otomatik", type: "text", autocomplete: "new-password" });
  const add = act(async () => {
    const r = await api("POST", "/is-listesi/api/users", { username: uname.value, display_name: dname.value, role: role.value, password: pw.value });
    await loadMe(); await renderUsers({ who: r.user.username, pw: r.password });
  });
  const editRow = (tr, u) => {
    const dn = h("input", { value: u.display_name, maxlength: 80, disabled: !!u.account_username,
      title: u.account_username ? "Ad CokluUyap hesabından gelir" : null });
    const rl = h("select", { disabled: u.id === S.me.id, title: u.id === S.me.id ? "Kendi rolünüzü değiştiremezsiniz" : null },
      h("option", { value: "user", selected: u.role === "user" }, "Kullanıcı"),
      h("option", { value: "admin", selected: u.role === "admin" }, "Yönetici"));
    const save = act(async () => {
      const body = u.account_username ? {} : { display_name: dn.value };
      if (u.id !== S.me.id) body.role = rl.value;
      await api("POST", `/is-listesi/api/users/${u.id}`, body);
      toast("Kullanıcı güncellendi.", true); await loadMe(); renderTabs(); await renderUsers();
    });
    dn.addEventListener("keydown", (e) => { if (e.key === "Enter") save(e); if (e.key === "Escape") renderUsers(); });
    tr.replaceChildren(
      h("td", {}, h("div", { class: "row" }, avatar(u.id), h("div", { class: "grow" }, dn, h("div", { class: "small muted" }, u.username)))),
      h("td", {}, rl), h("td", {}),
      h("td", { style: "text-align:right" }, h("div", { class: "row", style: "justify-content:flex-end" },
        h("button", { type: "button", class: "btn sm", onclick: save }, "Kaydet"),
        h("button", { type: "button", class: "btn-ghost sm", onclick: () => renderUsers() }, "Vazgeç"))));
    dn.focus();
  };
  const rows = d.users.map((u) => { const tr = h("tr", {},
    h("td", {}, h("div", { class: "row" }, avatar(u.id), h("div", {}, h("b", {}, u.display_name), h("div", { class: "small muted" }, u.account_username || u.username)))),
    h("td", {}, h("span", { class: "tag" + (u.role === "admin" ? " admin" : "") }, u.role === "admin" ? "Yönetici" : "Kullanıcı")),
    h("td", {}, h("span", { class: "tag" + (u.active ? "" : " off") }, u.active ? "Etkin" : "Kapalı")),
    h("td", { style: "text-align:right" }, h("div", { class: "row", style: "justify-content:flex-end" },
      h("button", { type: "button", class: "btn-ghost sm", onclick: () => editRow(tr, u) }, icon("edit"), "Düzenle"),
      u.id === S.me.id ? h("span", { class: "small muted" }, "Siz") : u.account_username ? null : h("button", { type: "button", class: "btn-ghost sm", onclick: act(async () => {
        await api("POST", `/is-listesi/api/users/${u.id}/aktif`, { active: !u.active }); await loadMe(); await renderUsers(); }) },
        u.active ? "Kapat" : "Aç"),
      u.id === S.me.id || u.account_username ? null : confirmBtn("Parola sıfırla", async () => {
        const r = await api("POST", `/is-listesi/api/users/${u.id}/sifre`, {}); await renderUsers({ who: u.username, pw: r.password }); }, "btn-ghost sm", null)))); return tr; });
  $("list").replaceChildren(...[
    shown ? h("div", { class: "pwshow" }, icon("check"), h("div", {}, `${shown.who} için parola: `, h("code", {}, shown.pw),
      h("div", { class: "small muted", style: "margin-top:4px" }, "Bu parola yalnız şimdi gösteriliyor; kullanıcıya iletin."))) : null,
    S.me.account_username ? h("div", { class: "card" }, h("h3", {}, "Kişiler"),
      h("div", { class: "small muted" }, "Büronuzun CokluUyap hesapları burada kendiliğinden görünür. Kişi eklemek, ad/parola değiştirmek ",
        "ya da hesabı kapatmak için CokluUyap Ofis panelini (/ofis) kullanın. Burada yalnız İş Listesi rolü (Yönetici/Kullanıcı) değişir; ",
        "büro sahibi her zaman yöneticidir.")) :
    h("div", { class: "card" }, h("h3", {}, "Kullanıcı ekle"),
      h("div", { class: "form-grid" }, h("label", {}, "Kullanıcı adı", uname), h("label", {}, "Ad Soyad", dname),
        h("label", {}, "Rol", role), h("label", {}, "Parola", pw)),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: add }, icon("plus"), "Ekle"),
        h("span", { class: "small muted" }, "Parola: en az 8 karakter; büyük/küçük harf, rakam ve işaret."))),
    h("div", { class: "card", style: "padding:6px 8px;overflow-x:auto" },
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Kişi"), h("th", {}, "Rol"), h("th", {}, "Durum"), h("th", {}, ""))),
        h("tbody", {}, rows)))].filter(Boolean));
}

// ── iş şablonları (UYAP'ta "İş Ata" penceresinde seçilir; model: "yapılacak iş.md") ──
const TARAF = { borclu: "Borçlu", alacakli: "Alacaklı", herhangi: "Herhangi taraf" };
const canEditTpl = (t) => typeof t.id === "number" && (isAdmin() || t.created_by === S.me.id);
// Kontrol sonucu → sonraki iş (Aşama 2). baslik boş = aynı iş yeniden (ör. "bekliyor" → +3 gün).
const SONUC = { olumlu: "Olumlu", olumsuz: "Olumsuz", bekliyor: "Bekliyor" };
function sonucText(t) {
  const r = t.sonuclar || {};
  return Object.keys(SONUC).filter((k) => r[k]).map((k) =>
    `${r[k].ad || SONUC[k]} → ${r[k].baslik || "aynı iş"}${r[k].gun != null ? ` (+${r[k].gun} gün)` : ""}`).join(" · ");
}
function templateForm(t, done, titles) {
  const title = h("input", { value: t ? t.title : "", maxlength: 200, placeholder: "ör. Haciz ihbarnamesi kontrol" });
  const due = h("input", { type: "number", min: 0, max: 365, value: t && t.due_days != null ? t.due_days : "", placeholder: "Yok" });
  const prio = h("select", {}, [1, 2, 0].map((p) => h("option", { value: p, selected: (t ? t.priority : 1) === p }, PRIO[p])));
  const taraf = h("select", {}, h("option", { value: "" }, "Taraf yok"),
    Object.entries(TARAF).map(([k, l]) => h("option", { value: k, selected: !!t && t.taraf === k }, l)));
  const desc = h("textarea", { rows: 2, maxlength: 2000, placeholder: "Ne kontrol edilecek, ne yapılacak? (iş açılınca açıklamaya yazılır)" }, t ? t.description : "");
  const kontrol = h("input", { value: t && t.kontrol ? t.kontrol : "", maxlength: 300, placeholder: "ör. Tebligat tebliğ edildi mi? (boş: sonuç sorulmaz)" });
  // Süreç aşaması + varsayılan etiketler (yalnız hazır ve büro etiketleri; şablon büronundur).
  const asm = asamaSelect(t && t.asama ? t.asama : "");
  // Dosya türü (kök) etiketi işin dosyasından gelir; burada yalnız başlık olarak görünür.
  const tagBoxes = [];
  const tagPick = h("div", { class: "etk-agac" }, tagTree([...S.tagDefs.hazir, ...S.tagDefs.buro].filter((g) => !g.asama)).map(({ g, d }) => {
    if (!g.parent && g.tur) return h("div", { class: "small muted", style: "margin-top:6px" }, tagChip(g.key));
    const cb = h("input", { type: "checkbox", checked: !!t && (t.etiketler || []).includes(g.key) });
    tagBoxes.push([g.key, cb]);
    return h("label", { class: "etk-opt", style: `padding-left:${d * 22}px`, title: tagPath(g.key) }, cb, tagChip(g.key));
  }));
  const listId = `tpl-titles-${t ? t.id : "yeni"}`;
  const dal = {};
  const dallar = Object.keys(SONUC).map((k) => {
    const r = (t && t.sonuclar && t.sonuclar[k]) || {};
    dal[k] = { baslik: h("input", { value: r.baslik || "", maxlength: 200, list: listId, style: "flex:1;min-width:0",
                                     placeholder: k === "bekliyor" ? "Boş: aynı iş yeniden" : "Sonraki iş (boş: öneri yok)" }),
               gun: h("input", { type: "number", min: 0, max: 365, value: r.gun != null ? r.gun : "", placeholder: "gün", style: "width:100%" }) };
    return h("label", {}, `${SONUC[k]} →`, h("div", { class: "row", style: "gap:6px;flex-wrap:nowrap" }, dal[k].baslik,
      h("span", { style: "flex:0 0 72px;display:flex" }, dal[k].gun)));
  });
  const save = act(async () => {
    const sonuclar = {};
    for (const k of Object.keys(SONUC)) {
      const b = dal[k].baslik.value.trim(), g = dal[k].gun.value;
      const ad = ((t && t.sonuclar && t.sonuclar[k]) || {}).ad || null;   // sonucun adı (formda değişmez, korunur)
      sonuclar[k] = b || g !== "" ? { baslik: b || null, gun: g === "" ? null : Number(g), ad } : null;
    }
    await api("POST", t ? `/is-listesi/api/sablonlar/${t.id}` : "/is-listesi/api/sablonlar", {
      title: title.value, description: desc.value, due_days: due.value === "" ? null : Number(due.value),
      priority: Number(prio.value), taraf: taraf.value || null, kontrol: kontrol.value, sonuclar,
      asama: asm.value || null, etiketler: tagBoxes.filter(([, cb]) => cb.checked).map(([k]) => k) });
    toast(t ? "Şablon güncellendi." : "Şablon eklendi.", true); await done();
  });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") save(e); if (e.key === "Escape" && t) done(); });
  const el = h("div", {},
    h("div", { class: "form-grid" }, h("label", {}, "İş", title), h("label", {}, "Süre (gün)", due),
      h("label", {}, "Öncelik", prio), h("label", {}, "Taraf", taraf)),
    h("label", { class: "lbl", style: "margin-bottom:12px" }, "Açıklama", desc),
    h("label", { class: "lbl", style: "margin-bottom:8px" }, "Kontrol sorusu (işi bitirirken sorulur)", kontrol),
    h("div", { class: "form-grid" }, dallar),
    S.surecler.length ? h("div", { class: "form-grid" }, h("label", {}, "Süreç aşaması (işler bu aşamada görünür)", asm)) : null,
    tagBoxes.length ? h("details", { class: "lbl" }, h("summary", { style: "cursor:pointer" },
      `Varsayılan etiketler (iş açılınca takılır)${t && (t.etiketler || []).length ? ` · ${t.etiketler.length} seçili` : ""}`), tagPick) : null,
    h("datalist", { id: listId }, (titles || []).map((x) => h("option", { value: x }))),
    h("div", { class: "row" }, h("button", { type: "button", class: t ? "btn sm" : "btn", onclick: save }, t ? null : icon("plus"), t ? "Kaydet" : "Ekle"),
      t ? h("button", { type: "button", class: "btn-ghost sm", onclick: () => done() }, "Vazgeç")
        : h("span", { class: "small muted" }, "Süre: iş açılınca son tarih bugünden bu kadar gün sonra 17:00 olur. Taraf “Borçlu” ise dosyanın ilk borçlusu seçilir.")));
  if (t) setTimeout(() => title.focus());
  return el;
}
async function renderTemplates() {
  const d = await api("GET", "/is-listesi/api/sablonlar");
  S.sablonlar = d.hazir.concat(d.buro);   // kontrol penceresi güncel şablonla açılsın
  if (S.view !== "sablonlar") return;
  const q = h("input", { type: "search", placeholder: "Şablonlarda ara…", value: S.tq || "" });
  const titles = d.hazir.concat(d.buro).map((t) => t.title);
  const tbody = h("tbody");
  const cells = (t) => [
    h("td", {}, h("b", {}, t.title), t.description ? h("div", { class: "small muted" }, t.description) : null,
      t.kontrol ? h("div", { class: "small" }, `❓ ${t.kontrol}`) : null,
      sonucText(t) ? h("div", { class: "small muted" }, sonucText(t)) : null,
      asamaOf(t.asama || "") || (t.etiketler || []).length ? h("div", { class: "row", style: "gap:6px;flex-wrap:wrap;margin-top:4px" },
        t.asama ? asamaTag(t.asama) : null, (t.etiketler || []).map((k) => tagChip(k))) : null),
    h("td", {}, t.due_days != null ? `${t.due_days} gün` : "—"),
    h("td", {}, h("span", { class: "tag" + (t.priority === 2 ? " off" : "") }, PRIO[t.priority])),
    h("td", {}, TARAF[t.taraf] || "—"),
    h("td", { class: "small muted" }, typeof t.id === "number" ? t.created_by_name || "" : "Hazır")];
  const draw = () => {
    const k = (S.tq || "").toLocaleLowerCase("tr");
    const rows = d.buro.filter((t) => !k || t.title.toLocaleLowerCase("tr").includes(k)).map((t) => {
      const tr = h("tr", {}, cells(t), h("td", { style: "text-align:right" }, canEditTpl(t) ? h("div", { class: "row", style: "justify-content:flex-end" },
        h("button", { type: "button", class: "btn-ghost sm", onclick: () => tr.replaceChildren(h("td", { colspan: 6 }, templateForm(t, renderTemplates, titles))) },
          icon("edit"), "Düzenle"),
        confirmBtn("Sil", async () => { await api("DELETE", `/is-listesi/api/sablonlar/${t.id}`); toast("Şablon silindi.", true); await renderTemplates(); },
          "btn-ghost sm")) : null));
      return tr;
    });
    tbody.replaceChildren(...(rows.length ? rows : [h("tr", {}, h("td", { colspan: 6, class: "muted" },
      d.buro.length ? "Eşleşen şablon yok." : "Henüz büro şablonu yok. Yukarıdan ekleyin ya da UYAP'ta İş Ata penceresinde “Şablon olarak kaydet” deyin."))]));
  };
  q.addEventListener("input", () => { S.tq = q.value; draw(); });
  draw();
  const head = h("tr", {}, ["İş", "Süre", "Öncelik", "Taraf", "Ekleyen", ""].map((l) => h("th", {}, l)));
  $("list").replaceChildren(
    h("div", { class: "card" }, h("h3", {}, "Yeni şablon"), templateForm(null, renderTemplates, titles)),
    h("div", { class: "card", style: "padding:6px 8px;overflow-x:auto" },
      h("div", { class: "row", style: "justify-content:space-between;flex-wrap:wrap;padding:6px 6px 8px" },
        h("b", {}, `Büro şablonları (${d.buro.length})`), h("label", { class: "search" }, icon("search"), q)),
      h("table", {}, h("thead", {}, head), tbody)),
    h("details", { class: "card", style: "padding:6px 8px;overflow-x:auto" },
      h("summary", { style: "cursor:pointer;padding:6px;font-weight:600" }, `Hazır şablonlar (${d.hazir.length}) — her büroda var, değiştirilemez`),
      h("table", {}, h("thead", {}, head.cloneNode(true)), h("tbody", {}, d.hazir.map((t) => h("tr", {}, cells(t), h("td")))))),
    tagManager());
}

// ── etiketler: hazırlar değişmez; büro etiketini ekleyen ya da yönetici, kişiseli sahibi düzenler ──
const canEditTag = (g) => !!g.id && (!!g.owner_id || isAdmin() || g.created_by === S.me.id);
function swatches(cur) {
  const st = { v: cur || S.tagDefs.renkler[0] };
  const el = h("div", { class: "swatches", role: "radiogroup", "aria-label": "Renk" });
  const draw = () => el.replaceChildren(...S.tagDefs.renkler.map((c) => h("button", { type: "button", class: "swatch" + (st.v === c ? " on" : ""),
    style: `--c:${c}`, role: "radio", "aria-checked": st.v === c ? "true" : "false", "aria-label": c,
    onclick: () => { st.v = c; draw(); } })));
  draw();
  return { el, value: () => st.v };
}
// Üst etiket seçimi: 3. katmandakilerin altına etiket konmaz; büro etiketi kişisel etiketin altına konmaz.
function parentSelect(cur, self) {
  const maxD = (S.tagDefs.derinlik || 3) - 1;
  return h("select", { "aria-label": "Üst etiket" }, h("option", { value: "" }, "Üst etiket yok (genel)"),
    tagTree(allTags()).filter(({ g, d }) => d < maxD && g.key !== self && !(self && tagChain(g.key).includes(self)))
      .map(({ g, d }) => h("option", { value: g.key, selected: cur === g.key }, `${indent(d)}${g.name}${g.owner_id ? " (kişisel)" : ""}`)));
}
function tagManager() {
  const again = async () => { await loadMeta(); await renderTemplates(); };
  const nm = h("input", { maxlength: 40, placeholder: "ör. Banka müvekkili" }), sw = swatches();
  const par = parentSelect("e-icra");
  const kisi = h("input", { type: "checkbox", checked: !isAdmin() });
  const add = act(async () => {
    await api("POST", "/is-listesi/api/etiketler", { name: nm.value, color: sw.value(), kisisel: kisi.checked, parent: par.value || null });
    toast("Etiket eklendi.", true); await again();
  });
  nm.addEventListener("keydown", (e) => { if (e.key === "Enter") add(e); });
  const row = ({ g, d }) => {
    const tr = h("tr", {}, h("td", { style: `padding-left:${10 + d * 24}px` }, d ? h("span", { class: "muted" }, "└ ") : null, tagChip(g.key)),
      h("td", { class: "small muted" }, g.owner_id ? "Yalnız siz" : g.id ? name(g.created_by) : !g.parent && g.tur ? "Dosya türü"
        : g.asama ? "Süreç aşaması (işin aşamasından gelir)" : g.surec ? "Süreç" : "Hazır"),
      h("td", { style: "text-align:right" }, canEditTag(g) ? h("div", { class: "row", style: "justify-content:flex-end" },
        h("button", { type: "button", class: "btn-ghost sm", onclick: () => {
          const n2 = h("input", { value: g.name, maxlength: 40 }), s2 = swatches(g.color), p2 = parentSelect(g.parent || "", g.key);
          const save = act(async () => {
            await api("POST", `/is-listesi/api/etiketler/${g.id}`, { name: n2.value, color: s2.value(), parent: p2.value || null });
            toast("Etiket güncellendi.", true); await again(); });
          n2.addEventListener("keydown", (e) => { if (e.key === "Enter") save(e); if (e.key === "Escape") again(); });
          tr.replaceChildren(h("td", { colspan: 3 }, h("div", { class: "row", style: "flex-wrap:wrap" }, n2, p2, s2.el,
            h("button", { type: "button", class: "btn sm", onclick: save }, "Kaydet"),
            h("button", { type: "button", class: "btn-ghost sm", onclick: () => again() }, "Vazgeç"))));
          n2.focus(); } }, icon("edit"), "Düzenle"),
        confirmBtn("Sil", async () => { await api("DELETE", `/is-listesi/api/etiketler/${g.id}`); toast("Etiket silindi.", true); await again(); },
          "btn-ghost sm")) : null));
    return tr;
  };
  const tree = tagTree(allTags());
  return h("div", { class: "card", style: "overflow-x:auto" }, h("h3", {}, `Etiketler (${tree.length})`),
    h("div", { class: "small muted", style: "margin-bottom:10px" },
      "Ağaç en çok üç katmandır: dosya türü (İcra, Hukuk…) → süreç / grup → alt etiket. Bir dosya türünün altındaki etiket ",
      "yalnız o türdeki dosyanın işine takılır; dosya türü etiketi işe dosyadan kendiliğinden gelir. Üst etiketle süzünce altındakiler de ",
      "gelir. Kişisel etiketi yalnız siz görürsünüz."),
    h("div", { class: "row", style: "flex-wrap:wrap;margin-bottom:12px" }, nm, par, sw.el,
      h("label", { class: "row small", style: "gap:6px" }, kisi, "Yalnız bana (kişisel)"),
      h("button", { type: "button", class: "btn sm", onclick: add }, icon("plus"), "Ekle")),
    h("table", {}, h("tbody", {}, tree.map(row))));
}

// ── süreç görünümü: her satır bir dosya + taraf, her sütun bir aşama; hücre o aşamadaki son iş ──
// İade dalından sonra süreç baştan başlar (yeni tebligat turu): tablo yalnız son turu gösterir.
// Üstteki rapor her dosyanın ŞU AN hangi aşamada beklediğini sayar; çipe basınca yalnız o aşamadakiler kalır.
function processRows(s) {
  const rows = new Map();
  const tasks = S.tasks.filter((t) => t.surec === s.key && s.asamalar.some((a) => a.key === t.asama))
    .sort((a, b) => a.created_at - b.created_at || a.id - b.id);
  for (const t of tasks) {
    const i = s.asamalar.findIndex((a) => a.key === t.asama);
    // Dosyası olmayan iş kendi satırında kalır (ilgisiz işler birleşmesin).
    const key = t.dosya_id ? `${t.birim_id || t.birim_adi || ""}|${t.dosya_no || ""}|${t.taraf_adi || ""}` : `#${t.id}`;
    let r = rows.get(key);
    if (!r) rows.set(key, r = { dosya: dosyaText(t) || t.title, taraf: t.taraf_adi || "", tur: 1, cells: [], last: null });
    if (i === 0 && r.cells.some((c, j) => c && j > 0)) { r.tur += 1; r.cells = []; }   // yeni tebligat turu
    (r.cells[i] = r.cells[i] || []).push(t);
    r.last = t;
  }
  for (const r of rows.values()) {
    const t = r.last, i = s.asamalar.findIndex((a) => a.key === t.asama);
    // Şu anki yer: son işin aşaması; son aşama olumlu bittiyse süreç tamam.
    r.bitti = i === s.asamalar.length - 1 && isDone(t) && t.sonuc !== "olumsuz";
    r.yer = r.bitti ? "bitti" : s.asamalar[i].key;
    r.gec = !isDone(t) && isLate(t);
    r.i = i;
  }
  return [...rows.values()];
}
function renderProcess() {
  S.order = [];
  const q = S.q.trim().toLocaleLowerCase("tr-TR"), kids = [];
  for (const s of S.surecler) {
    const all = processRows(s);
    let list = all;
    if (q) list = list.filter((r) => `${r.dosya} ${r.taraf}`.toLocaleLowerCase("tr-TR").includes(q));
    const qList = list;
    if (S.pa === "gec") list = list.filter((r) => r.gec);
    else if (S.pa) list = list.filter((r) => r.yer === S.pa);
    // Önce bekleyenler, en geride kalan önce; bitenler sonda.
    list.sort((a, b) => (a.bitti - b.bitti) || (a.i - b.i) || a.dosya.localeCompare(b.dosya, "tr"));
    const pick = (k) => () => { S.pa = S.pa === k ? "" : k; renderProcess(); };
    const chip = (k, label, n, cls) => h("button", { type: "button", class: "chip" + (S.pa === k ? " on" : "") + (cls || ""),
      "aria-pressed": S.pa === k ? "true" : "false", onclick: pick(k) }, label, h("span", { class: "n" }, ` ${n}`));
    const rapor = [chip("", "Tümü", qList.length)];
    s.asamalar.forEach((a, i) => {
      const n = qList.filter((r) => r.yer === a.key).length;
      if (n || S.pa === a.key) rapor.push(chip(a.key, `${i + 1}. ${a.title}`, n, a.dal ? " iade" : ""));
    });
    rapor.push(chip("bitti", "✓ Kesinleşti", qList.filter((r) => r.bitti).length, " ok"));
    const gec = qList.filter((r) => r.gec).length;
    if (gec || S.pa === "gec") rapor.push(chip("gec", "Gecikmiş", gec, " late"));
    const cell = (arr, a) => {
      const iade = a.dal ? " pc-iade" : "";
      if (!arr) return h("td", { class: "pc" + iade });
      const t = arr[arr.length - 1];
      const st = !isDone(t) ? (isLate(t) ? "late" : "open") : t.sonuc === "olumsuz" ? "neg" : "ok";
      const dal = ((tplOf(t) || {}).sonuclar || {})[t.sonuc];
      return h("td", { class: `pc ${st}${iade}` }, h("button", { type: "button", class: "pc-b",
        title: `${t.title} — ${STATUS[t.status] || t.status}${t.sonuc ? ` · sonuç: ${(dal && dal.ad) || SONUC[t.sonuc]}` : ""}` +
          `${t.due_at ? " · " + fmtShort(t.due_at) : ""}${arr.length > 1 ? ` (bu aşamada ${arr.length} iş)` : ""}`,
        onclick: () => openDetail(t.id) }, icon(st === "ok" ? "check" : st === "neg" ? "undo" : st === "late" ? "alert" : "clock")));
    };
    const grp = [];   // üst başlık: ardışık aşamalar dal adına göre gruplanır
    s.asamalar.forEach((a) => { const l = a.dal ? "İade dalı (yeniden tebliğ)" : "";
      if (grp.length && grp[grp.length - 1].l === l) grp[grp.length - 1].n++; else grp.push({ l, n: 1 }); });
    const head = [h("tr", { class: "pt-grp" }, h("th", {}), grp.map((g) => h("th", { colspan: g.n, class: g.l ? "pc-iade" : "" }, g.l))),
      h("tr", {}, h("th", {}, "Dosya / taraf"), s.asamalar.map((a, i) => h("th", { class: "pc" + (a.dal ? " pc-iade" : ""), title: a.title }, `${i + 1}. ${a.title}`)))];
    kids.push(h("div", { class: "card", style: "padding:6px 8px;overflow-x:auto" },
      h("div", { class: "row", style: "justify-content:space-between;flex-wrap:wrap;padding:6px 6px 4px" },
        h("b", {}, s.title), h("span", { class: "small muted" }, `${all.length} dosya / taraf · ✓ olumlu · ↺ iade / olumsuz · saat: bekliyor · ! gecikti`)),
      h("div", { class: "facet-row", style: "padding:0 6px 8px" }, h("span", { class: "facet-l" }, "Şu an"), h("div", { class: "chips" }, rapor)),
      list.length ? h("table", { class: "pt" }, h("thead", {}, head), h("tbody", {}, list.map((r) => h("tr", { class: r.bitti ? "pt-bitti" : "" },
        h("td", {}, h("b", {}, r.dosya), r.taraf ? h("div", { class: "small muted" }, r.taraf) : null,
          r.tur > 1 ? h("div", { class: "small tur-n", title: "İade sonrası tebligat baştan izleniyor; önceki turlar işlerin geçmişinde" }, `${r.tur}. tebligat turu`) : null),
        s.asamalar.map((a, i) => cell(r.cells[i], a))))))
        : h("div", { class: "none-txt", style: "padding:10px 6px" }, q || S.pa ? "Eşleşen dosya yok."
          : "Bu süreçte iş yok. Şablondan açılan işler (ör. “Tebliğ kontrol”) aşamasıyla burada görünür.")));
  }
  if (!kids.length) kids.push(h("div", { class: "card empty" }, h("b", {}, "Tanımlı süreç yok")));
  $("list").replaceChildren(...kids);
}

// ── raporlar (yönetici) ──────────────────────────────────────────────────────────
// Hesap sunucuda (zs_rapor.py); burada yalnız süzgeç, tablo, grafik ve dışa aktarma.
const REP_PRESETS = [["bu-hafta", "Bu hafta"], ["bu-ay", "Bu ay"], ["gecen-ay", "Geçen ay"], ["son-3-ay", "Son 3 ay"],
  ["bu-yil", "Bu yıl"], ["ozel", "Özel aralık"]];
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function presetRange(k) {
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const y = t.getFullYear(), m = t.getMonth();
  if (k === "bugun") return [t, t];
  if (k === "dun") { const a = new Date(t); a.setDate(t.getDate() - 1); return [a, a]; }
  if (k === "bu-hafta" || k === "gecen-hafta") {
    const a = new Date(t); a.setDate(t.getDate() - ((t.getDay() + 6) % 7) - (k === "gecen-hafta" ? 7 : 0));
    const b = new Date(a); b.setDate(a.getDate() + 6); return [a, b];
  }
  if (k === "gecen-ay") return [new Date(y, m - 1, 1), new Date(y, m, 0)];
  if (k === "son-3-ay") return [new Date(y, m - 2, 1), new Date(y, m + 1, 0)];
  if (k === "bu-yil") return [new Date(y, 0, 1), new Date(y, 11, 31)];
  return [new Date(y, m, 1), new Date(y, m + 1, 0)];
}
function repState() {
  if (!S.rep) {
    const [a, b] = presetRange("bu-ay");
    const saved = store.get("zs-rep-tab");
    const tabs = isAdmin() ? ["kisi", "etkinlik", "is", "zaman"] : ["etkinlik", "is", "zaman"];
    S.rep = { tab: tabs.includes(saved) ? saved : tabs[0], durum: "tumu", ara: "", preset: "bu-ay", bas: isoDay(a), bit: isoDay(b),
      grup: "hafta", kisi: "", sel: null, data: null };
  }
  return S.rep;
}
function durShort(sec) {
  if (sec === null || sec === undefined) return "—";
  const m = Math.round(sec / 60);
  if (m < 60) return `${Math.max(1, m)} dk`;
  const hr = Math.round(m / 60);
  if (hr < 24) return `${hr} sa`;
  const d = Math.floor(hr / 24), r = hr % 24;
  return r ? `${d} g ${r} sa` : `${d} g`;
}
const pct = (a, b) => b ? Math.round(100 * a / b) : null;
const STATUS_ONTIME = (t) => t.ontime === true ? ["Süresinde", "ok"] : t.ontime === false ? ["Geç", "bad"] : [t.due_at ? "—" : "Tarihsiz", ""];

async function renderReport() {
  const R = repState();
  const bas = h("input", { type: "date", value: R.bas, "aria-label": "Başlangıç" });
  const bit = h("input", { type: "date", value: R.bit, "aria-label": "Bitiş" });
  const preset = h("select", { "aria-label": "Dönem" }, REP_PRESETS.map(([k, l]) => h("option", { value: k, selected: R.preset === k }, l)));
  const reload = () => { R.data = null; R.sel = null; renderReport(); };
  preset.addEventListener("change", () => {
    R.preset = preset.value;
    if (R.preset !== "ozel") { const [a, b] = presetRange(R.preset); R.bas = isoDay(a); R.bit = isoDay(b); }
    reload();
  });
  for (const [inp, k] of [[bas, "bas"], [bit, "bit"]]) inp.addEventListener("change", () => {
    if (!inp.value) return; R[k] = inp.value; R.preset = "ozel"; reload(); });
  const tabBtn = (k, l, ic) => h("button", { type: "button", class: "seg" + (R.tab === k ? " on" : ""), "aria-pressed": R.tab === k ? "true" : "false",
    onclick: () => { R.tab = k; store.set("zs-rep-tab", k); renderReport(); } }, icon(ic), l);
  const ctrls = [h("div", { class: "segs" }, isAdmin() ? tabBtn("kisi", "Kişi bazında", "users") : null,
    tabBtn("etkinlik", isAdmin() ? "Etkinlik" : "Ne yaptım", "hist"),
    tabBtn("is", "İş bazında", "list"), tabBtn("zaman", "Tarih bazında", "cal"))];
  if (R.tab === "etkinlik") {
    const A = actState();
    if (isAdmin()) {
      const kisi = h("select", { "aria-label": "Kişi" }, S.peopleList.map((p) =>
        h("option", { value: p.id, selected: p.id === A.uid }, p.display_name)));
      kisi.addEventListener("change", () => { A.uid = Number(kisi.value); renderReport(); });
      ctrls.push(h("label", { class: "rf" }, "Kişi", kisi));
    }
    ctrls.push(h("span", { class: "grow" }), h("button", { type: "button", class: "btn-ghost sm", onclick: () => print() }, icon("printer"), "Yazdır"));
    const body = h("div", { class: "rep-body" });
    $("list").replaceChildren(h("div", { class: "rep-bar" }, ctrls), body);
    body.append(activityPanel(A.uid));
    return;
  }
  ctrls.push(h("label", { class: "rf" }, "Dönem", preset), h("span", { class: "rf range" }, bas, "–", bit));
  if (R.tab === "zaman") {
    const grup = h("select", { "aria-label": "Gruplama" }, [["gun", "Günlük"], ["hafta", "Haftalık"], ["ay", "Aylık"]]
      .map(([k, l]) => h("option", { value: k, selected: R.grup === k }, l)));
    grup.addEventListener("change", () => { R.grup = grup.value; reload(); });
    ctrls.push(h("label", { class: "rf" }, "Gruplama", grup));
  }
  if (R.tab !== "kisi" && isAdmin()) {
    const kisi = h("select", { "aria-label": "Kişi" }, h("option", { value: "" }, "Herkes"),
      S.peopleList.map((p) => h("option", { value: p.id, selected: String(p.id) === String(R.kisi) }, p.display_name)));
    kisi.addEventListener("change", () => { R.kisi = kisi.value; reload(); });
    ctrls.push(h("label", { class: "rf" }, "Kişi", kisi));
  }
  const exportBtn = h("button", { type: "button", class: "btn-ghost sm", title: "Tabloyu Excel'de açılabilir CSV olarak indir" }, icon("download"), "Excel'e aktar");
  ctrls.push(h("span", { class: "grow" }), exportBtn,
    h("button", { type: "button", class: "btn-ghost sm", onclick: () => print() }, icon("printer"), "Yazdır"));
  const bar = h("div", { class: "rep-bar" }, ctrls);
  const body = h("div", { class: "rep-body" });
  $("list").replaceChildren(bar, body);
  if (!R.data) {
    body.append(h("div", { class: "card none-txt" }, "Rapor hazırlanıyor…"));
    const q = new URLSearchParams({ bas: R.bas, bit: R.bit, grup: R.grup });
    if (R.kisi) q.set("kisi", R.kisi);
    try { R.data = await api("GET", `/is-listesi/api/rapor?${q}`); }
    catch (e) { body.replaceChildren(h("div", { class: "card warnbox" }, icon("alert"), e.message)); return; }
    if (S.view !== "rapor") return;
    body.replaceChildren();
  }
  const table = { kisi: personReport, is: taskReport, zaman: timeReport }[R.tab](body, R);
  const fileKey = { kisi: "kisi", is: "is", zaman: "tarih" }[R.tab];
  exportBtn.addEventListener("click", () => exportCsv(table, `is-takip-${fileKey}-raporu_${R.bas}_${R.bit}.csv`));
}

// Excel (Türkçe bölge) noktalı virgülü ayraç bekler; BOM ile UTF-8 olarak açılır.
function exportCsv(table, filename) {
  const esc = (v) => /[";\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  const lines = [...table.querySelectorAll("tr:not([data-skip])")].map((tr) =>
    [...tr.children].map((c) => esc((c.getAttribute("data-csv") ?? c.textContent).trim())).join(";"));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = h("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const num = (v, cls) => h("td", { class: "num" + (cls ? " " + cls : "") }, v === null || v === undefined ? "—" : String(v));
function personReport(body, R) {
  const rows = R.data.kisiler;
  const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
  const tot = { biten: sum("biten"), suresinde: sum("suresinde"), gec: sum("gec") };
  // Özet: dönemde biten işlerin ne kadarı süresinde (son tarihi olanlar arasında).
  const withDue = tot.suresinde + tot.gec;
  body.append(h("div", { class: "rep-sum" },
    sumTile("Dönemde biten", tot.biten), sumTile("Süresinde", withDue ? `%${pct(tot.suresinde, withDue)}` : "—", withDue ? `${tot.suresinde} / ${withDue} iş` : "son tarihli iş yok"),
    sumTile("Dönemde açılan", sum("yeni")), sumTile("Şu an açık", sum("acik"), `${sum("geciken")} gecikmiş`, sum("geciken") ? "bad" : "")));
  const ontimeCell = (r) => {
    const n = r.suresinde + r.gec, p = pct(r.suresinde, n);
    return h("td", { class: "num", "data-csv": n ? String(p) : "" }, n ? h("span", { class: "ratio" },
      h("span", { class: "rbar" }, h("i", { style: `width:${p}%` })), `%${p}`) : h("span", { class: "muted" }, "—"));
  };
  const table = h("table", { class: "rep-table" },
    h("thead", {},
      h("tr", { class: "grp" }, h("th", {}), h("th", { colspan: 9, class: "gh" }, "İşleri"), h("th", { colspan: 4, class: "gh" }, "Onay yetkilisi olarak")),
      h("tr", {}, ["Kişi", "Açılan", "Biten", "Süresinde", "Geç", "Tarihsiz", "Süresinde %", "Reddedilen", "Ort. bitirme", "Açık / gecikmiş",
        "Onayladı", "Reddetti", "Ort. karar", "Geç karar"].map((l, i) => h("th", { class: i ? "num" : "" }, l)))),
    h("tbody", {}, rows.map((r) => h("tr", { class: "click" + (R.sel === r.user_id ? " sel" : ""), tabindex: 0,
      title: "İşlerini göster", onclick: () => { R.sel = R.sel === r.user_id ? null : r.user_id; renderReport(); },
      onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } },
      h("td", { "data-csv": r.name }, h("span", { class: "row", style: "gap:8px;flex-wrap:nowrap" }, avatar(r.user_id, "xs"), r.name,
        r.active ? null : h("span", { class: "tag off" }, "Kapalı"))),
      num(r.yeni), num(r.biten), num(r.suresinde), num(r.gec, r.gec ? "bad" : ""), num(r.tarihsiz), ontimeCell(r),
      num(r.reddedilen), h("td", { class: "num", "data-csv": r.ort_sure === null ? "" : (r.ort_sure / 86400).toFixed(1) }, durShort(r.ort_sure)),
      h("td", { class: "num", "data-csv": `${r.acik}/${r.geciken}` }, `${r.acik}`, r.geciken ? h("span", { class: "bad" }, ` / ${r.geciken}`) : h("span", { class: "muted" }, " / 0")),
      num(r.onayladi), num(r.reddetti),
      h("td", { class: "num", "data-csv": r.ort_karar === null ? "" : (r.ort_karar / 3600).toFixed(1) }, durShort(r.ort_karar)),
      num(r.gec_karar, r.gec_karar ? "bad" : "")))));
  body.append(h("div", { class: "card rep-card" }, h("div", { class: "tscroll" }, table),
    h("div", { class: "small muted rep-note" }, "Süresinde/geç: onaya gönderilme anı son tarihe göre. Bir iş birden çok kişiye atanmışsa her birinde sayılır. ",
      "Ort. bitirme: açılıştan onaya kadar; Ort. karar: onaya gönderilmeden onay/ret kararına kadar. Dışa aktarmada süreler gün (bitirme) ve saat (karar) olarak yazılır.")));
  if (R.sel) {
    const panel = activityPanel(R.sel, { onClose: () => { R.sel = null; renderReport(); }, tasks: () => personTasks(R) });
    body.append(panel);
    requestAnimationFrame(() => panel.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
  return table;
}
function sumTile(label, value, sub, tone) {
  return h("div", { class: "card rep-tile" }, h("div", { class: "l" }, label), h("div", { class: "v" }, String(value)),
    sub ? h("div", { class: "s" + (tone ? " " + tone : "") }, sub) : null);
}
function personTasks(R) {
  const p = R.data.kisiler.find((r) => r.user_id === R.sel);
  const list = R.data.isler.filter((t) => t.assignees.includes(R.sel));
  const s0 = R.data.donemler.length ? R.data.donemler[0].start : 0;
  const s1 = R.data.donemler.length ? R.data.donemler[R.data.donemler.length - 1].end : 0;
  const rows = list.map((t) => {
    const [ot, tone] = t.finished_at ? STATUS_ONTIME(t) : ["", ""];
    const late = !t.finished_at && t.due_at && t.due_at < R.data.now;
    return h("tr", { class: "click", tabindex: 0, title: "İşi aç", onclick: () => go("tumu", t.id),
      onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } },
      h("td", {}, t.title), h("td", {}, pill(t.status)), h("td", {}, fmtShort(t.created_at)),
      h("td", { class: late ? "bad" : "" }, t.due_at ? fmtShort(t.due_at) : "—"),
      h("td", {}, t.finished_at ? fmtShort(t.finished_at) : "—"),
      h("td", { class: tone }, t.finished_at && t.finished_at >= s0 && t.finished_at < s1 ? ot : t.finished_at ? `${ot} (dönem dışı)` : late ? "Gecikmiş" : ""));
  });
  return h("div", {}, h("div", { class: "small muted", style: "margin:0 0 8px" },
    `Üstteki rapor döneminde (${fmtDay(R.bas)} – ${fmtDay(R.bit)}) ${p ? p.name : ""} kişisine atanmış; o dönemde açılan, biten ya da şu an açık işler. Satıra tıklayınca iş açılır.`),
    rows.length ? h("div", { class: "tscroll" }, h("table", { class: "rep-table" }, h("thead", {}, h("tr", {},
      ["İş", "Durum", "Açılış", "Son tarih", "Bitiş", "Süre"].map((l) => h("th", {}, l)))), h("tbody", {}, rows)))
      : h("div", { class: "none-txt" }, "Bu dönemde iş yok."));
}

// ── kişi etkinliği: bir günde / haftada / ayda ne yaptı ───────────────────────────────
// Dönem seçimi ana rapordan bağımsızdır (kişi tablosunun dönemi aylık, burada çoğu zaman gün/hafta bakılır).
const ACT_PRESETS = [["bugun", "Bugün"], ["dun", "Dün"], ["bu-hafta", "Bu hafta"], ["gecen-hafta", "Geçen hafta"],
  ["bu-ay", "Bu ay"], ["gecen-ay", "Geçen ay"]];
const ACT_CATS = { acti: "İş açtı", not: "Not", belge: "Belge", alt_is: "Alt iş", onaya: "Onaya gönderdi", tamamladi: "Tamamladı",
  onayladi: "Onayladı", reddetti: "Reddetti", ek_sure: "Ek süre istedi", diger: "Diğer" };
const fmtDay = (iso) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };
function actState() {
  if (!S.act) {
    const [a, b] = presetRange("bu-hafta");
    S.act = { uid: S.me.id, preset: "bu-hafta", bas: isoDay(a), bit: isoDay(b), sub: "gun", cat: "", cache: {} };
  }
  return S.act;
}
function openTaskFrom(id, isPrivate) { go(isPrivate ? "ozel" : isAdmin() ? "tumu" : "benim", id); }
function actText(it) {
  if (it.kind === "note") return ["msg", "", "not ekledi", it.body];
  if (it.kind === "deleted") return ["trash", "bad", "işi sildi"];
  const [ic, tone, txt, extra] = eventView({ kind: it.kind, detail: it.detail });
  return [ic, tone, txt, extra || it.detail.reason || null];
}
function activityPanel(uid, opts = {}) {
  const A = actState();
  const panel = h("div", { class: "card rep-card act" });
  const key = () => `${uid}|${A.bas}|${A.bit}`;
  const load = async () => {
    const k = key();
    if (A.cache[k]) return draw();
    draw();
    try { A.cache[k] = await api("GET", `/is-listesi/api/rapor/etkinlik?${new URLSearchParams({ kisi: uid, bas: A.bas, bit: A.bit })}`); }
    catch (e) { A.cache[k] = { error: e.message }; }
    if (panel.isConnected) draw();
  };
  const setRange = (preset, a, b) => { A.preset = preset; A.bas = a; A.bit = b; A.cat = ""; load(); };
  function draw() {
    const D = A.cache[key()];
    const who = S.people[uid] ? S.people[uid].display_name : "";
    const presetBtns = h("div", { class: "segs" }, ACT_PRESETS.map(([k, l]) => h("button", { type: "button",
      class: "seg" + (A.preset === k ? " on" : ""), "aria-pressed": A.preset === k ? "true" : "false",
      onclick: () => { const [a, b] = presetRange(k); setRange(k, isoDay(a), isoDay(b)); } }, l)));
    const bas = h("input", { type: "date", value: A.bas, "aria-label": "Başlangıç" });
    const bit = h("input", { type: "date", value: A.bit, "aria-label": "Bitiş" });
    for (const inp of [bas, bit]) inp.addEventListener("change", () => {
      if (bas.value && bit.value) setRange("ozel", bas.value, bit.value); });
    const exportBtn = h("button", { type: "button", class: "btn-ghost sm", title: "Tabloyu Excel'de açılabilir CSV olarak indir" },
      icon("download"), "Excel'e aktar");
    const head = h("div", { class: "act-head" }, avatar(uid, "sm"), h("h3", {}, uid === S.me.id && !opts.onClose ? "Ne yaptım" : "Ne yaptı"),
      h("span", { class: "small muted" }, `${who} · ${A.bas === A.bit ? fmtDay(A.bas) : `${fmtDay(A.bas)} – ${fmtDay(A.bit)}`}`),
      h("span", { class: "grow" }),
      opts.onClose ? h("button", { type: "button", class: "btn-icon", title: "Kapat", onclick: opts.onClose }, icon("x")) : null);
    const bar = h("div", { class: "act-bar" }, presetBtns, h("span", { class: "rf range" }, bas, "–", bit), h("span", { class: "grow" }), exportBtn);
    const kids = [head, bar];
    if (!D) kids.push(h("div", { class: "none-txt" }, "Hazırlanıyor…"));
    else if (D.error) kids.push(h("div", { class: "warnbox" }, icon("alert"), D.error));
    else {
      const o = D.ozet, nDays = D.gunler.length;
      kids.push(h("div", { class: "rep-sum" },
        sumTile("İşlem", o.toplam, nDays > 1 ? `${o.aktif_gun} / ${nDays} gün etkin` : (o.toplam ? `${fmtTimeOf(D.items[0].at)} – ${fmtTimeOf(D.items[D.items.length - 1].at)}` : "")),
        sumTile("Üzerinde çalıştığı iş", o.is_sayisi, o.acti ? `${o.acti} yeni iş açtı` : ""),
        sumTile("Not · Belge · Alt iş", `${o.not} · ${o.belge} · ${o.alt_is}`),
        sumTile("Onaya gönderdi / tamamladı", o.onaya + o.tamamladi,
          o.onayladi + o.reddetti ? `${o.onayladi} onay, ${o.reddetti} ret kararı verdi` : "")));
      if (nDays > 1 && nDays <= 62)
        kids.push(barChart(D.gunler, [["toplam", "İşlem", "var(--series-1)"]], { aria: "Günlere göre işlem sayısı", height: 160,
          tip: (r) => Object.entries(ACT_CATS).filter(([k]) => r[k]).map(([k, l]) => [String(r[k]), l]) }));
      const subBtn = (k, l) => h("button", { type: "button", class: "seg" + (A.sub === k ? " on" : ""), "aria-pressed": A.sub === k ? "true" : "false",
        onclick: () => { A.sub = k; draw(); } }, l);
      kids.push(h("div", { class: "act-bar" }, h("div", { class: "segs" }, subBtn("gun", "Gün gün"), subBtn("is", "İş bazında"),
        opts.tasks ? subBtn("atanan", "Atanan işler") : null)));
      if (A.sub === "atanan" && opts.tasks) kids.push(opts.tasks());
      else if (A.sub === "is") actTasks(kids, D);
      else actLog(kids, D, A, draw);
      exportBtn.addEventListener("click", () => {
        const t = panel.querySelector("table.rep-table");
        if (t) exportCsv(t, `is-takip-etkinlik_${who.replace(/\s+/g, "-")}_${A.bas}_${A.bit}.csv`);
        else toast("Aktarılacak satır yok.");
      });
    }
    panel.replaceChildren(...kids);
  }
  load();
  return panel;
}
const FMT_TIME_ONLY = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", hour: "2-digit", minute: "2-digit" });
const fmtTimeOf = (t) => FMT_TIME_ONLY.format(new Date(t * 1000));
function actLog(kids, D, A, redraw) {
  const counts = D.ozet;
  const cat = h("select", { "aria-label": "İşlem türü" }, h("option", { value: "" }, `Tüm işlemler (${counts.toplam})`),
    Object.entries(ACT_CATS).filter(([k]) => counts[k]).map(([k, l]) => h("option", { value: k, selected: A.cat === k }, `${l} (${counts[k]})`)));
  cat.addEventListener("change", () => { A.cat = cat.value; redraw(); });
  const items = D.items.filter((it) => !A.cat || it.cat === A.cat);
  const DAY = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "numeric", month: "long", year: "numeric", weekday: "long" });
  const FULL = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const perDay = {};
  for (const it of items) { const d = DAY.format(new Date(it.at * 1000)); perDay[d] = (perDay[d] || 0) + 1; }
  const rows = [];
  let last = "";
  for (const it of items) {
    const day = DAY.format(new Date(it.at * 1000));
    if (day !== last) {
      rows.push(h("tr", { class: "day", "data-skip": "1" }, h("td", { colspan: 3 }, day, h("span", { class: "muted" }, ` · ${perDay[day]} işlem`))));
      last = day;
    }
    const [ic, tone, txt, extra] = actText(it);
    const quote = extra ? String(extra) : "";
    rows.push(h("tr", { class: it.exists ? "click" : "", tabindex: it.exists ? 0 : null, title: it.exists ? "İşi aç" : "İş silinmiş",
      onclick: it.exists ? () => openTaskFrom(it.task_id, it.is_private) : null,
      onkeydown: (e) => { if (e.key === "Enter" && it.exists) e.currentTarget.click(); } },
      h("td", { class: "t", "data-csv": FULL.format(new Date(it.at * 1000)) }, fmtTimeOf(it.at)),
      h("td", { "data-csv": it.title }, it.title || "—", it.is_private ? h("span", { class: "priv", style: "margin-left:6px" }, icon("lock"), "Özel") : null,
        it.exists ? null : h("span", { class: "tag off", style: "margin-left:6px" }, "silindi")),
      h("td", { class: "what", "data-csv": quote ? `${txt}: ${quote}` : txt },
        h("span", { class: "act-what " + tone }, icon(ic), txt),
        quote ? h("div", { class: "quote", title: quote }, quote.length > 280 ? quote.slice(0, 280) + "…" : quote) : null)));
  }
  const table = h("table", { class: "rep-table act-log" },
    h("thead", {}, h("tr", {}, h("th", {}, "Zaman"), h("th", {}, "İş"), h("th", {}, "Ne yaptı"))), h("tbody", {}, rows));
  kids.push(h("div", { class: "row rep-filter", style: "margin:4px 0 8px" }, h("label", { class: "rf" }, "Göster", cat),
    h("span", { class: "small muted" }, `${items.length} işlem`)),
    items.length ? h("div", { class: "tscroll" }, table) : h("div", { class: "none-txt" }, "Bu dönemde işlem yok."),
    h("div", { class: "small muted rep-note" }, "Kişinin kendi yaptığı işlemler (iş açma, not, belge, alt iş, onaya gönderme, onay kararı, ek süre…) zamana göre. ",
      "Başkalarının özel işleri ve devirde gizlenen kayıtlar görünmez. Satıra tıklayınca iş açılır."));
  return table;
}
function actTasks(kids, D) {
  const done = (t) => [t.acti && "açtı", t.onaya && "onaya gönderdi", t.tamamladi && "tamamladı", t.onayladi && "onayladı",
    t.reddetti && "reddetti", t.ek_sure && "ek süre istedi"].filter(Boolean).join(", ");
  const table = h("table", { class: "rep-table" },
    h("thead", {}, h("tr", {}, ["İş", "Şu anki durum", "İşlem", "Not", "Belge", "Alt iş", "Öne çıkan", "İlk işlem", "Son işlem"]
      .map((l, i) => h("th", { class: i >= 2 && i <= 5 ? "num" : "" }, l)))),
    h("tbody", {}, D.isler.map((t) => h("tr", { class: t.exists ? "click" : "", tabindex: t.exists ? 0 : null, title: t.exists ? "İşi aç" : "İş silinmiş",
      onclick: t.exists ? () => openTaskFrom(t.task_id, t.is_private) : null,
      onkeydown: (e) => { if (e.key === "Enter" && t.exists) e.currentTarget.click(); } },
      h("td", { "data-csv": t.title }, t.title || "—", t.is_private ? h("span", { class: "priv", style: "margin-left:6px" }, icon("lock"), "Özel") : null),
      h("td", { "data-csv": t.exists ? (STATUS[t.status] || t.status) : "silindi" }, t.exists ? pill(t.status) : h("span", { class: "tag off" }, "silindi")),
      num(t.toplam), num(t.not || null), num(t.belge || null), num(t.alt_is || null),
      h("td", {}, done(t) || h("span", { class: "muted" }, "—")), h("td", {}, fmtShort(t.ilk)), h("td", {}, fmtShort(t.son))))));
  kids.push(D.isler.length ? h("div", { class: "tscroll" }, table) : h("div", { class: "none-txt" }, "Bu dönemde işlem yok."),
    h("div", { class: "small muted rep-note" }, "Kişinin bu dönemde üzerinde işlem yaptığı işler; en son dokunduğu üstte."));
  return table;
}

// İş bazında: dönemde açılan, dönemde biten ve şu an açık işler; durum/sonuç ve metinle süzülür.
const TASK_FILTERS = [
  ["tumu", "Tümü", () => true],
  ["acik", "Açık", (t) => !t.finished_at],
  ["gecikmis", "Gecikmiş (açık)", (t, now) => !t.finished_at && !!t.due_at && t.due_at < now],
  ["biten", "Biten", (t) => !!t.finished_at],
  ["suresinde", "Süresinde biten", (t) => !!t.finished_at && t.ontime === true],
  ["gec", "Geç biten", (t) => !!t.finished_at && t.ontime === false],
];
function taskReport(body, R) {
  const now = R.data.now, all = R.data.isler;
  const flt = TASK_FILTERS.find(([k]) => k === R.durum) || TASK_FILTERS[0];
  const q = R.ara.trim().toLocaleLowerCase("tr-TR");
  const rows = all.filter((t) => flt[2](t, now) && (!q || t.title.toLocaleLowerCase("tr-TR").includes(q)));
  const fin = all.filter((t) => t.finished_at), withDue = fin.filter((t) => t.ontime !== null);
  const onTime = withDue.filter((t) => t.ontime).length;
  const late = all.filter((t) => !t.finished_at && t.due_at && t.due_at < now).length;
  body.append(h("div", { class: "rep-sum" }, sumTile("İş", all.length, "dönemde açılan, biten ya da açık"),
    sumTile("Biten", fin.length),
    sumTile("Süresinde", withDue.length ? `%${pct(onTime, withDue.length)}` : "—",
      withDue.length ? `${onTime} / ${withDue.length} iş` : "son tarihli iş yok"),
    sumTile("Açık", all.length - fin.length, `${late} gecikmiş`, late ? "bad" : "")));
  const durum = h("select", { "aria-label": "Durum" }, TASK_FILTERS.map(([k, l, f]) =>
    h("option", { value: k, selected: R.durum === k }, `${l} (${all.filter((t) => f(t, now)).length})`)));
  durum.addEventListener("change", () => { R.durum = durum.value; renderReport(); });
  const ara = h("input", { type: "search", placeholder: "İş adında ara…", value: R.ara });
  ara.addEventListener("input", () => {
    R.ara = ara.value; const pos = ara.selectionStart;
    renderReport().then(() => { const i = document.querySelector(".rep-filter input"); if (i) { i.focus(); i.setSelectionRange(pos, pos); } });
  });
  const showWho = isAdmin() && !R.kisi;
  const head = ["İş"].concat(showWho ? ["Atanan"] : [], ["Durum", "Açılış", "Son tarih", "Onaya gönderildi", "Bitiş", "Bitirme süresi", "Sonuç"]);
  const table = h("table", { class: "rep-table" },
    h("thead", {}, h("tr", {}, head.map((l) => h("th", { class: l === "Bitirme süresi" ? "num" : "" }, l)))),
    h("tbody", {}, rows.map((t) => {
      const lateOpen = !t.finished_at && t.due_at && t.due_at < now;
      const [ot, tone] = t.finished_at ? STATUS_ONTIME(t) : lateOpen ? ["Gecikmiş", "bad"] : ["Açık", ""];
      const who_ = t.assignees.map(name).join(", ") || "—";
      return h("tr", { class: "click", tabindex: 0, title: "İşi aç",
        onclick: () => go(t.is_private ? "ozel" : isAdmin() ? "tumu" : "benim", t.id),
        onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } },
        h("td", { "data-csv": t.title }, t.title, t.is_private ? h("span", { class: "priv", style: "margin-left:6px" }, icon("lock"), "Özel") : null),
        showWho ? h("td", { "data-csv": who_ }, who_) : null,
        h("td", { "data-csv": STATUS[t.status] || t.status }, pill(t.status)),
        h("td", {}, fmtShort(t.created_at)), h("td", { class: lateOpen ? "bad" : "" }, t.due_at ? fmtShort(t.due_at) : "—"),
        h("td", {}, t.submitted_at ? fmtShort(t.submitted_at) : "—"), h("td", {}, t.finished_at ? fmtShort(t.finished_at) : "—"),
        h("td", { class: "num", "data-csv": t.finished_at ? ((t.finished_at - t.created_at) / 86400).toFixed(1) : "" },
          t.finished_at ? durShort(t.finished_at - t.created_at) : "—"),
        h("td", { class: tone }, ot));
    })));
  body.append(h("div", { class: "card rep-card" },
    h("div", { class: "row rep-filter", style: "margin-bottom:8px" }, h("label", { class: "rf" }, "Göster", durum), ara,
      h("span", { class: "small muted" }, `${rows.length} iş`)),
    rows.length ? h("div", { class: "tscroll" }, table) : h("div", { class: "none-txt" }, all.length ? "Bu süzgeçte iş yok." : "Bu dönemde iş yok."),
    h("div", { class: "small muted rep-note" }, "Dönemde açılan, dönemde biten ve şu an açık işler listelenir. Sonuç: onaya gönderilme (özel işte tamamlanma) anı son tarihe göre. ",
      "Dışa aktarmada bitirme süresi gün olarak yazılır. Satıra tıklayınca iş açılır.")));
  return table;
}

function timeReport(body, R) {
  const rows = R.data.donemler;
  const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const fin = sum("suresinde") + sum("gec");
  body.append(h("div", { class: "rep-sum" }, sumTile("Açılan", sum("acilan")), sumTile("Biten", sum("biten")),
    sumTile("Süresinde", fin ? `%${pct(sum("suresinde"), fin)}` : "—", fin ? `${sum("suresinde")} / ${fin} iş` : "son tarihli iş yok"),
    sumTile("Reddedilen", sum("reddedilen"))));
  body.append(h("div", { class: "card rep-card" }, h("h3", {}, "Açılan ve biten işler"), barChart(rows)));
  const table = h("table", { class: "rep-table" },
    h("thead", {}, h("tr", {}, ["Dönem", "Açılan", "Biten", "Süresinde", "Geç", "Reddedilen", "Dönem sonunda açık"]
      .map((l, i) => h("th", { class: i ? "num" : "" }, l)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, r.label), num(r.acilan), num(r.biten), num(r.suresinde),
      num(r.gec, r.gec ? "bad" : ""), num(r.reddedilen), num(r.acik_sonda)))),
    h("tfoot", {}, h("tr", {}, h("td", {}, "Toplam"), num(sum("acilan")), num(sum("biten")), num(sum("suresinde")),
      num(sum("gec")), num(sum("reddedilen")), h("td", { class: "num muted" }, "—"))));
  body.append(h("div", { class: "card rep-card" }, h("div", { class: "tscroll" }, table),
    h("div", { class: "small muted rep-note" }, "Biten: o dönemde onaylanan işler. Dönem sonunda açık: o güne kadar açılmış, henüz bitmemiş işler (gelecek dönemlerde boş).")));
  return table;
}

// Gruplanmış sütun grafik: Açılan (seri 1) / Biten (seri 2); tek eksen, adet.
const OPEN_DONE = [["acilan", "Açılan", "var(--series-1)"], ["biten", "Biten", "var(--series-2)"]];
function barChart(rows, series = OPEN_DONE, opts = {}) {
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  const max = Math.max(1, ...rows.flatMap((r) => series.map(([k]) => r[k] || 0)));
  const step = max <= 5 ? 1 : Math.ceil(max / 4 / (max > 20 ? 5 : 1)) * (max > 20 ? 5 : 1);
  const top = Math.ceil(max / step) * step;
  const groupW = Math.max(26, Math.min(120, 900 / Math.max(1, rows.length)));
  const padL = 34, padB = 26, padT = 10, H = opts.height || 220, W = padL + rows.length * groupW + 8;
  const barW = Math.max(4, Math.min(series.length > 1 ? 18 : 24, (groupW - 10) / series.length));
  const y = (v) => padT + (H - padT - padB) * (1 - v / top);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": opts.aria || "Dönemlere göre açılan ve biten iş sayısı" });
  for (let v = 0; v <= top; v += step) {
    svg.append(svgEl("line", { x1: padL, x2: W - 4, y1: y(v), y2: y(v), class: v ? "grid" : "axis" }));
    const t = svgEl("text", { x: padL - 6, y: y(v) + 4, "text-anchor": "end", class: "tick" }); t.textContent = v; svg.append(t);
  }
  const labelEvery = Math.ceil(rows.length / Math.max(1, Math.floor((W - padL) / 70)));
  const tip = h("div", { class: "chart-tip", hidden: true });
  rows.forEach((r, i) => {
    const gx = padL + i * groupW, cx = gx + groupW / 2;
    series.forEach(([k, , col], j) => {
      const v = r[k]; if (!v) return;
      const x = cx - (series.length * (barW + 2) - 2) / 2 + j * (barW + 2), y0 = y(v), hgt = y(0) - y0;
      // Üst köşeler yuvarlak (4px), taban düz.
      const rr = Math.min(4, barW / 2, hgt);
      svg.append(svgEl("path", { fill: col, d: `M${x},${y(0)} V${y0 + rr} Q${x},${y0} ${x + rr},${y0} H${x + barW - rr} Q${x + barW},${y0} ${x + barW},${y0 + rr} V${y(0)} Z` }));
    });
    if (i % labelEvery === 0) {
      const t = svgEl("text", { x: cx, y: H - 8, "text-anchor": "middle", class: "tick" });
      t.textContent = r.label.split(" – ")[0]; svg.append(t);
    }
    const hit = svgEl("rect", { x: gx, y: padT, width: groupW, height: H - padT - padB, class: "hit", tabindex: 0,
      "aria-label": `${r.label}: ` + series.map(([k, l]) => `${l.toLocaleLowerCase("tr-TR")} ${r[k] || 0}`).join(", ") });
    const show = () => {
      tip.replaceChildren(h("b", {}, r.label), ...series.map(([k, l, col]) => h("div", { class: "tr" },
        h("i", { style: `background:${col}` }), h("strong", {}, String(r[k] || 0)), h("span", {}, l))),
        ...(opts.tip ? opts.tip(r).map(([v, l]) => h("div", { class: "tr sub" }, h("i", {}), h("strong", {}, v), h("span", {}, l))) : []));
      tip.hidden = false;
      const box = svg.getBoundingClientRect(), wrap = tip.parentElement.getBoundingClientRect();
      const px = box.left - wrap.left + (cx / W) * box.width;
      tip.style.left = `${Math.min(px + 12, wrap.width - 150)}px`; tip.style.top = "8px";
    };
    hit.addEventListener("pointerenter", show); hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", () => { tip.hidden = true; }); hit.addEventListener("blur", () => { tip.hidden = true; });
    svg.append(hit);
  });
  const legend = series.length > 1 ? h("div", { class: "legend" }, series.map(([, l, col]) => h("span", {}, h("i", { style: `background:${col}` }), l))) : null;
  return h("div", { class: "chart" }, legend, h("div", { class: "chart-wrap" }, h("div", { class: "chart-scroll" }, svg), tip));
}

// ── parolam ────────────────────────────────────────────────────────────────────────
function passwordDialog() {
  const dlg = $("dlg");
  const old = h("input", { type: "password", autocomplete: "current-password" });
  const nw = h("input", { type: "password", autocomplete: "new-password" });
  const nw2 = h("input", { type: "password", autocomplete: "new-password" });
  const show = h("input", { type: "checkbox" });
  show.addEventListener("change", () => { for (const i of [old, nw, nw2]) i.type = show.checked ? "text" : "password"; });
  dlg.replaceChildren(h("h3", {}, "Parolamı değiştir"),
    h("div", { class: "small muted" }, "En az 8 karakter; büyük/küçük harf, rakam ve işaret."),
    h("label", { class: "lbl" }, "Mevcut parola", old), h("label", { class: "lbl" }, "Yeni parola", nw),
    h("label", { class: "lbl" }, "Yeni parola (tekrar)", nw2),
    h("label", { class: "row small", style: "margin-top:10px;cursor:pointer" }, show, "Parolaları göster"),
    h("div", { class: "actions", style: "margin-top:18px" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        if (nw.value !== nw2.value) { nw2.focus(); toast("Yeni parolalar aynı değil."); return; }
        await api("POST", "/is-listesi/api/me/password", { old: old.value, new: nw.value });
        dlg.close(); toast("Parola değişti.", true); }) }, "Kaydet"),
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Vazgeç")));
  dlg.showModal();
}

// ── takvim (açılış ekranı) ──────────────────────────────────────────────────────────
// İşler ve tarihli alt işler son tarihlerinin gününe yerleşir. Biten silik, geciken kırmızı.
// Kullanıcı yalnız kendi işlerini görür; yönetici herkesinkini, kişi süzgeciyle (bir ya da birkaç kişi).
const CAL_MODES = [["gun", "Gün"], ["hafta", "Hafta"], ["ay", "Ay"]];
const WD_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const p2 = (n) => String(n).padStart(2, "0");
const dkey = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
const dayOf = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const weekStart = (d) => { const x = dayOf(d); return addDays(x, -((x.getDay() + 6) % 7)); };
const calFmt = (d, o) => d.toLocaleDateString("tr-TR", o);

// Hafta görünümü 5 günlük bir penceredir: açılışta pazartesi–cuma; oklar pencereyi birer gün kaydırır
// (cumadan sonra cumartesi gelir). Bugün hafta sonuysa pencere bugünle biter.
const WEEK_DAYS = 5;
const weekWin = (d) => { const x = dayOf(d), wd = (x.getDay() + 6) % 7; return addDays(x, wd < WEEK_DAYS ? -wd : -(WEEK_DAYS - 1)); };

function calState() {
  if (!S.cal) {
    const mode = store.get("zs-cal-mode");
    let people = [];
    try { people = JSON.parse(store.get("zs-cal-people") || "[]").filter((id) => S.people[id]); } catch (_) { /* bozuk kayıt */ }
    S.cal = { mode: CAL_MODES.some(([k]) => k === mode) ? mode : "hafta", at: dayOf(new Date()),
      wk: weekWin(new Date()), people: new Set(people), strips: new Set() };
  }
  return S.cal;
}
function calSave() {
  store.set("zs-cal-mode", S.cal.mode);
  store.set("zs-cal-people", JSON.stringify([...S.cal.people]));
}

// Görünür aralık: [başlangıç, bitiş) + ızgarada çizilecek günler.
function calRange() {
  const C = calState(), a = C.at;
  if (C.mode === "gun") return { start: a, end: addDays(a, 1), days: [a] };
  if (C.mode === "hafta") return { start: C.wk, end: addDays(C.wk, WEEK_DAYS), days: [...Array(WEEK_DAYS)].map((_, i) => addDays(C.wk, i)) };
  const first = new Date(a.getFullYear(), a.getMonth(), 1), next = new Date(a.getFullYear(), a.getMonth() + 1, 1);
  const gs = weekStart(first), n = Math.round((weekStart(addDays(next, -1)) - gs) / 864e5) + 7;
  return { start: first, end: next, days: [...Array(n)].map((_, i) => addDays(gs, i)) };
}
function calLabel() {
  const C = calState(), r = calRange();
  if (C.mode === "gun") return calFmt(C.at, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  if (C.mode === "ay") return calFmt(C.at, { month: "long", year: "numeric" });
  const e = addDays(r.end, -1), same = r.start.getMonth() === e.getMonth();
  return `${calFmt(r.start, same ? { day: "numeric" } : { day: "numeric", month: "long" })} – ${calFmt(e, { day: "numeric", month: "long", year: "numeric" })}`;
}
function calMove(dir) {
  const C = calState();
  const today = dayOf(new Date());
  if (dir === 0) { C.at = today; C.wk = weekWin(today); }
  else if (C.mode === "gun") C.at = addDays(C.at, dir);
  else if (C.mode === "hafta") {
    C.wk = addDays(C.wk, dir);
    C.at = today >= C.wk && today < addDays(C.wk, WEEK_DAYS) ? today : C.wk;
  }
  else C.at = new Date(C.at.getFullYear(), C.at.getMonth() + dir, 1);
  renderHead(); renderCalendar();
}
function calSetMode(mode, day) {
  const C = calState();
  if (day) C.at = dayOf(day);
  if (mode === "hafta" && (C.mode !== "hafta" || day)) C.wk = weekWin(C.at);
  C.mode = mode; calSave(); renderHead(); renderCalendar();
}

// Takvim kalemleri: ana iş + tarihli alt işler. people = kimin işi (süzgeç ve avatar için).
function calItems() {
  const C = calState(), admin = isAdmin(), me = S.me.id, out = [];
  const q = S.q.trim().toLocaleLowerCase("tr-TR");
  for (const t of S.tasks) {
    const rootPeople = assigneesOf(t);
    if (admin || isMine(t) || isOwnPrivate(t))
      out.push({ key: `t${t.id}`, rootId: t.id, obj: t, title: t.title, parent: null, people: rootPeople, priv: !!t.is_private });
    for (const s of t.subtasks || []) {
      if (!s.due_at) continue;
      const people = s.assignee_id ? [s.assignee_id] : rootPeople;
      if (!admin && !people.includes(me)) continue;
      out.push({ key: `s${s.id}`, rootId: t.id, obj: s, title: s.title, parent: t.title, people, priv: !!t.is_private });
    }
  }
  return out.filter((it) => (!admin || !C.people.size || it.people.some((p) => C.people.has(p)))
    && (!q || `${it.title} ${it.parent || ""}`.toLocaleLowerCase("tr-TR").includes(q)));
}
const itemState = (it) => isDone(it.obj) ? "done" : isLate(it.obj) ? "late" : it.obj.status === "onayda" ? "onayda"
  : it.obj.status === "devam" ? "devam" : "acik";
const byTime = (a, b) => (a.obj.due_at || 0) - (b.obj.due_at || 0) || (b.obj.priority ?? 1) - (a.obj.priority ?? 1);
function calStats(items) {
  const s = { total: items.length, done: 0, onayda: 0, late: 0 };
  for (const it of items) { const st = itemState(it); if (st === "done") s.done++; else if (st === "late") s.late++; else if (st === "onayda") s.onayda++; }
  return s;
}
function calProg(done, total, cls) {
  const pct = total ? Math.round(100 * done / total) : 0;
  return h("span", { class: "cal-prog" + (cls ? " " + cls : "") + (total && done === total ? " full" : ""), title: `${total} işten ${done} tanesi bitti` },
    h("span", { class: "b" }, h("i", { style: `width:${pct}%` })), h("span", { class: "n" }, `${done}/${total}`));
}

// Kutucukta kişi adı: "Ayşe D." (tam ad üzerine gelince). Yönetici her zaman görür; kullanıcı yalnız
// iş birden çok kişiye atanmışsa (kendi takviminde kendi adı gereksiz).
const shortName = (id) => {
  const p = name(id).trim().split(/\s+/);
  return p.length > 1 ? `${p[0]} ${p[p.length - 1][0].toLocaleUpperCase("tr-TR")}.` : p[0];
};
function calWho(people, wide) {
  if (!people.length) return isAdmin() && !wide ? h("span", { class: "who-ns none" }, "Atanmamış") : null;
  // Gün görünümünde işler zaten kişi bölümlerinde; ad yalnız ortak işte gerekir.
  if ((wide || !isAdmin()) && people.length < 2) return null;
  const MAX = 3, more = people.length - MAX;
  return h("span", { class: "who-ns", title: people.map(name).join(", ") },
    people.slice(0, MAX).map((id) => h("span", { class: "who-n" },
      h("i", { style: `background:${AV_COLORS[id % AV_COLORS.length]}` }), shortName(id))),
    more > 0 ? h("span", { class: "who-n more" }, `+${more}`) : null);
}

function calChip(it, opts = {}) {
  const st = itemState(it), o = it.obj;
  const tip = [it.parent ? `${it.parent} › ${it.title}` : it.title, st === "done" ? "Bitti" : st === "late" ? "Gecikti" : STATUS[o.status] || "",
    it.people.map(name).join(", ")].filter(Boolean).join(" · ");
  return h("button", { type: "button", class: `cal-it st-${st}` + (S.selId === it.rootId ? " sel" : "") + (opts.wide ? " wide" : ""),
    title: tip, onclick: () => openDetail(it.rootId) },
    h("span", { class: "tm" }, opts.date ? fmtShort(o.due_at) : o.due_at ? FMT_TIME.format(new Date(o.due_at * 1000)) : "—"),
    h("span", { class: "tx" },
      it.parent ? h("span", { class: "par" }, it.parent, " ›") : null,
      h("span", { class: "tt" }, it.title)),
    o.priority === 2 && st !== "done" ? h("span", { class: "fl", title: "Acil" }, icon("flag")) : null,
    it.priv ? h("span", { class: "fl pv", title: "Özel" }, icon("lock")) : null,
    !it.parent && o.reminder_at && st !== "done" ? h("span", { class: "fl bl", title: `Hatırlatma: ${fmt(o.reminder_at)}` }, icon("bell")) : null,
    calWho(it.people, opts.wide),
    opts.wide ? (st === "done" ? h("span", { class: "pill s-onaylandi" }, "Bitti") : st === "late" ? latePill(o) || pill(o.status) : pill(o.status)) : null);
}

function calComposeOn(day) {
  const d = new Date(day); d.setHours(18, 0, 0, 0);
  S.composerDue = Math.floor(d.getTime() / 1000);
  openComposer();
}

function renderCalendar() {
  const C = calState(), r = calRange(), all = calItems(), admin = isAdmin();
  const byDay = {};
  for (const it of all) if (it.obj.due_at) (byDay[dkey(new Date(it.obj.due_at * 1000))] ||= []).push(it);
  for (const k in byDay) byDay[k].sort(byTime);
  const inRange = all.filter((it) => it.obj.due_at && it.obj.due_at * 1000 >= r.start && it.obj.due_at * 1000 < r.end);
  // İşten bağımsız hatırlatıcılar: güne yerleşir, iş sayılarına katılmaz.
  const q = S.q.trim().toLocaleLowerCase("tr-TR"), remByDay = {};
  for (const x of (S.myRems || []).filter((r) => !r.task_id)) {
    if (q && !`${x.title} ${x.note || ""}`.toLocaleLowerCase("tr-TR").includes(q)) continue;
    (remByDay[dkey(new Date(x.remind_at * 1000))] ||= []).push(x);
  }
  for (const k in remByDay) remByDay[k].sort((a, b) => a.remind_at - b.remind_at);
  const kids = [];
  if (S.composer) kids.push(composerCard());
  kids.push(calSummary(inRange, admin));
  const strips = calStrips(all, r);
  if (strips) kids.push(strips);
  const body = C.mode === "gun" ? calDayView(byDay[dkey(C.at)] || [], C.at, remByDay[dkey(C.at)] || [])
    : C.mode === "hafta" ? calWeekView(r, byDay, remByDay) : calMonthView(r, byDay, remByDay);
  kids.push(body);
  S.order = [...new Set((C.mode === "gun" ? byDay[dkey(C.at)] || [] : inRange.slice().sort(byTime)).map((it) => it.rootId))];
  $("list").replaceChildren(...kids);
}

function calSummary(items, admin) {
  const C = calState(), s = calStats(items), today = dayOf(new Date());
  const word = C.mode === "gun" ? (+C.at === +today ? "Bugün" : calFmt(C.at, { day: "numeric", month: "long" }))
    : C.mode === "hafta" ? (+C.wk === +weekWin(today) ? "Bu hafta" : "Bu aralıkta") : calFmt(C.at, { month: "long" });
  const kids = [h("div", { class: "cal-sum-main" },
    h("div", { class: "lbl" }, word),
    h("div", { class: "big" }, s.total ? `${s.done} / ${s.total}` : "İş yok", s.total ? h("span", { class: "muted" }, " iş bitti") : null),
    s.total ? calProg(s.done, s.total, "lg") : null,
    h("div", { class: "facts" },
      s.total - s.done - s.late ? h("span", {}, h("i", { class: "dot st-acik" }), `${s.total - s.done - s.late} bekliyor`) : null,
      s.onayda ? h("span", {}, h("i", { class: "dot st-onayda" }), `${s.onayda} onayda`) : null,
      s.late ? h("span", { class: "bad" }, h("i", { class: "dot st-late" }), `${s.late} gecikmiş`) : null))];
  // Yönetici: aralıktaki işler kişi kişi (birden çok atananlı iş her kişide sayılır).
  if (admin) {
    const per = new Map();
    for (const it of items) for (const p of it.people) {
      if (C.people.size && !C.people.has(p)) continue;
      const e = per.get(p) || { t: 0, d: 0, l: 0 }; e.t++;
      const st = itemState(it); if (st === "done") e.d++; else if (st === "late") e.l++;
      per.set(p, e);
    }
    if (per.size) kids.push(h("div", { class: "cal-people-sum" }, [...per.entries()]
      .sort((a, b) => name(a[0]).localeCompare(name(b[0]), "tr")).map(([p, e]) =>
        h("div", { class: "pp" + (e.l ? " has-late" : "") }, avatar(p, "xs"), h("span", { class: "nm" }, name(p)),
          calProg(e.d, e.t), e.l ? h("span", { class: "late-n", title: "Gecikmiş" }, `${e.l} gecikmiş`) : null))));
  }
  return h("div", { class: "card cal-sum" }, kids);
}

// Aralığın dışında kalan ama dikkat isteyenler: önceden kalan gecikmişler, tarihsiz açık işler.
function calStrips(all, r) {
  const C = calState();
  const late = all.filter((it) => it.obj.due_at && it.obj.due_at * 1000 < r.start && isLate(it.obj)).sort(byTime);
  const undated = all.filter((it) => !it.obj.due_at && !isDone(it.obj));
  if (!late.length && !undated.length) return null;
  const strip = (key, cls, ic, label, arr, date) => {
    const d = h("details", { class: `cal-strip ${cls}`, open: C.strips.has(key) },
      h("summary", {}, icon(ic), label, h("span", { class: "n" }, arr.length)),
      h("div", { class: "cal-strip-list" }, arr.map((it) => calChip(it, { date }))));
    d.addEventListener("toggle", () => { d.open ? C.strips.add(key) : C.strips.delete(key); });
    return d;
  };
  return h("div", { class: "cal-strips" },
    late.length ? strip("late", "late", "alert", "Önceki günlerden kalan gecikmiş işler", late, true) : null,
    undated.length ? strip("undated", "", "list", "Tarihsiz açık işler", undated, false) : null);
}

function calDayHead(day, items, opts = {}) {
  const today = +day === +dayOf(new Date()), s = calStats(items);
  return h("div", { class: "cal-dh" + (today ? " today" : "") },
    h("button", { type: "button", class: "cal-dn", title: "Gün görünümü", onclick: () => calSetMode("gun", day) },
      opts.short ? null : h("span", { class: "wd" }, WD_SHORT[(day.getDay() + 6) % 7]), h("span", { class: "num" }, day.getDate())),
    s.total ? calProg(s.done, s.total, s.late ? "has-late" : "") : h("span", { class: "grow" }),
    opts.add === false ? null : h("button", { type: "button", class: "btn-icon cal-add", title: "Bu güne iş ekle", onclick: () => calComposeOn(day) }, icon("plus")));
}

function calRemChip(x) {
  const sent = !!x.sent_at;
  return h("button", { type: "button", class: "cal-rem" + (sent ? " sent" : ""), onclick: () => freeReminderView(x),
    title: `Hatırlatıcı · ${fmt(x.remind_at)}${sent ? " · gönderildi" : ""}${x.user_id !== S.me.id ? ` · ${name(x.user_id)}` : ""}` },
    icon("bell"), h("span", { class: "tm" }, FMT_TIME.format(new Date(x.remind_at * 1000))), h("span", { class: "tt" }, x.title),
    x.user_id !== S.me.id ? h("span", { class: "who-n" }, h("i", { style: `background:${AV_COLORS[x.user_id % AV_COLORS.length]}` }), shortName(x.user_id)) : null);
}

function calWeekView(r, byDay, remByDay) {
  const today = dayOf(new Date());
  return h("div", { class: "cal-week" }, r.days.map((d) => {
    const items = byDay[dkey(d)] || [], rems = remByDay[dkey(d)] || [];
    return h("div", { class: "cal-col" + (+d === +today ? " today" : "") + (d.getDay() % 6 === 0 ? " wkend" : "") + (d < today ? " past" : "") },
      calDayHead(d, items),
      h("div", { class: "cal-col-body" }, rems.map(calRemChip), items.map((it) => calChip(it)),
        items.length || rems.length ? null : h("div", { class: "cal-none" }, "—")));
  }));
}

function calMonthView(r, byDay, remByDay) {
  const today = dayOf(new Date()), MAX = 3, m = calState().at.getMonth();
  return h("div", { class: "cal-month" },
    h("div", { class: "cal-mh" }, WD_SHORT.map((w) => h("div", {}, w))),
    h("div", { class: "cal-grid" }, r.days.map((d) => {
      const items = byDay[dkey(d)] || [], rems = remByDay[dkey(d)] || [];
      const shownRems = rems.slice(0, MAX), more = items.length + rems.length - MAX;
      return h("div", { class: "cal-cell" + (d.getMonth() !== m ? " out" : "") + (+d === +today ? " today" : "") + (d.getDay() % 6 === 0 ? " wkend" : "") },
        calDayHead(d, items, { short: true }),
        h("div", { class: "cal-cell-body" }, shownRems.map(calRemChip), items.slice(0, MAX - shownRems.length).map((it) => calChip(it)),
          more > 0 ? h("button", { type: "button", class: "cal-more", onclick: () => calSetMode("gun", d) }, `+${more} daha`) : null),
        // Telefon: ızgarada kalemler yerine durum noktaları.
        items.length || rems.length ? h("button", { type: "button", class: "cal-dots", onclick: () => calSetMode("gun", d), "aria-label": `${items.length} iş, ${rems.length} hatırlatıcı` },
          rems.slice(0, 2).map(() => h("i", { class: "dot st-rem" })), items.slice(0, 6).map((it) => h("i", { class: `dot st-${itemState(it)}` }))) : null);
    })));
}

function calDayView(items, day, rems) {
  const view = calDayTasks(items, day);
  if (!rems.length) return view;
  return h("div", {}, h("div", { class: "card cal-rems" }, h("div", { class: "cal-day-ph" }, icon("bell"), h("b", { class: "grow" }, "Hatırlatıcılar"),
    h("span", { class: "small muted" }, `${rems.length}`)), h("div", { class: "cal-day-list" }, rems.map(calRemChip))), view);
}

function calDayTasks(items, day) {
  const admin = isAdmin(), C = calState();
  const list = (arr) => arr.length ? h("div", { class: "cal-day-list" }, arr.map((it) => calChip(it, { wide: true })))
    : h("div", { class: "cal-none" }, "Bu gün için iş yok.");
  const add = h("div", { class: "row" }, h("button", { type: "button", class: "btn-ghost sm", onclick: () => calComposeOn(day) }, icon("plus"), "Bu güne iş ekle"),
    h("button", { type: "button", class: "btn-ghost sm", onclick: () => reminderCreateDialog({ day }) }, icon("bell"), "Bu güne hatırlatıcı"));
  if (!admin) return h("div", { class: "card cal-day" }, list(items), h("div", { class: "cal-day-foot" }, add));
  // Yönetici: kişi kişi bölümler — kim neyi bitirdi, kimde ne kaldı.
  const groups = new Map();
  for (const it of items) {
    const ps = it.people.length ? it.people : [0];
    for (const p of ps) { if (p && C.people.size && !C.people.has(p)) continue; (groups.get(p) || groups.set(p, []).get(p)).push(it); }
  }
  if (!groups.size) return h("div", { class: "card cal-day" }, list([]), h("div", { class: "cal-day-foot" }, add));
  const order = [...groups.keys()].sort((a, b) => (a === 0) - (b === 0) || name(a).localeCompare(name(b), "tr"));
  return h("div", { class: "cal-day-people" }, order.map((p) => {
    const arr = groups.get(p), s = calStats(arr);
    return h("div", { class: "card cal-day" },
      h("div", { class: "cal-day-ph" }, p ? avatar(p) : null, h("b", { class: "grow" }, p ? name(p) : "Atanmamış"),
        s.late ? h("span", { class: "late-n" }, `${s.late} gecikmiş`) : null, calProg(s.done, s.total)),
      list(arr));
  }), h("div", { class: "cal-day-foot" }, add));
}

function calHead(top) {
  const C = calState(), admin = isAdmin();
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Görünüm" }, CAL_MODES.map(([k, l]) =>
    h("button", { type: "button", class: C.mode === k ? "on" : "", "aria-pressed": C.mode === k ? "true" : "false", onclick: () => calSetMode(k) }, l)));
  const nav = h("div", { class: "cal-nav" },
    h("button", { type: "button", class: "btn-icon", title: "Önceki (←)", onclick: () => calMove(-1) }, icon("left")),
    h("button", { type: "button", class: "btn-ghost sm", title: "Bugüne dön (T)", onclick: () => calMove(0) }, "Bugün"),
    h("button", { type: "button", class: "btn-icon", title: "Sonraki (→)", onclick: () => calMove(1) }, icon("right")),
    h("span", { class: "cal-label" }, calLabel()));
  const rows = [top, h("div", { class: "toolbar cal-tools" }, nav, seg)];
  if (admin) {
    const people = S.peopleList.filter((p) => p.active || C.people.has(p.id));
    const toggle = (id) => { id === null ? C.people.clear() : C.people.has(id) ? C.people.delete(id) : C.people.add(id); calSave(); renderHead(); renderCalendar(); };
    rows.push(h("div", { class: "cal-people", role: "group", "aria-label": "Kişi süzgeci" },
      h("span", { class: "cal-pl" }, icon("users"), "Kişiler"),
      h("button", { type: "button", class: "chip" + (C.people.size ? "" : " on"), "aria-pressed": C.people.size ? "false" : "true", onclick: () => toggle(null) }, "Herkes"),
      people.map((p) => h("button", { type: "button", class: "chip pchip" + (C.people.has(p.id) ? " on" : ""),
        "aria-pressed": C.people.has(p.id) ? "true" : "false", onclick: () => toggle(p.id) }, avatar(p.id, "xs"), p.display_name))));
  }
  $("head").replaceChildren(...rows);
}

// ── klavye kısayolları ─────────────────────────────────────────────────────────────
function onKey(e) {
  if ($("dlg").open || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select";
  if (e.key === "Escape") {
    if (typing) { e.target.blur(); return; }
    if (S.rejectFor) { S.rejectFor = null; S.detail ? renderDetail() : renderList(); return; }
    if (S.composer) { S.composer = false; S.composerDue = null; renderList(); return; }
    if (S.selId) { closeDetail(); renderList(); }
    return;
  }
  if (typing || S.view === "kullanicilar" || S.view === "rapor") return;
  if (S.view === "hatirlatici" && e.key !== "/") return;
  if (e.key === "/") { const q = $("q"); if (q) { e.preventDefault(); q.focus(); } return; }
  if (e.key === "n" || e.key === "N") { e.preventDefault(); openComposer(); return; }
  if (S.view === "takvim") {
    const k = { ArrowLeft: () => calMove(-1), ArrowRight: () => calMove(1), t: () => calMove(0), T: () => calMove(0),
      g: () => calSetMode("gun"), h: () => calSetMode("hafta"), a: () => calSetMode("ay") }[e.key];
    if (k) { e.preventDefault(); k(); return; }
  }
  if (["ArrowDown", "ArrowUp", "j", "k"].includes(e.key) && S.order.length) {
    e.preventDefault();
    const dir = e.key === "ArrowDown" || e.key === "j" ? 1 : -1;
    const i = S.order.indexOf(S.selId);
    const next = S.order[i < 0 ? (dir > 0 ? 0 : S.order.length - 1) : Math.min(S.order.length - 1, Math.max(0, i + dir))];
    openDetail(next).then(() => { const c = document.querySelector(`.task[data-id="${next}"]`); if (c) c.scrollIntoView({ block: "nearest" }); });
  }
}

// ── bildirimler: Web Push (bu cihaz) + Telegram ──────────────────────────────────────
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
function b64uBytes(s) {
  const b = atob((s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}
async function swReg() { await navigator.serviceWorker.register("/is-listesi/sw.js", { scope: "/is-listesi" }); return navigator.serviceWorker.ready; }
async function currentSub() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/is-listesi");
  return reg ? reg.pushManager.getSubscription() : null;
}
async function pushOn(key) {
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Bildirim izni verilmedi. İzin, tarayıcının/telefonun ayarlarından da açılabilir.");
  const reg = await swReg();
  let sub = await reg.pushManager.getSubscription();
  // Sunucu anahtarı değişmişse eski abonelik çalışmaz: yenile.
  if (sub && sub.options && sub.options.applicationServerKey) {
    const a = new Uint8Array(sub.options.applicationServerKey), b = b64uBytes(key);
    if (a.length !== b.length || a.some((x, i) => x !== b[i])) { await sub.unsubscribe(); sub = null; }
  }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(key) });
  await api("POST", "/is-listesi/api/bildirim/push", sub.toJSON());
  store.set("zs-push", `${S.me.id}|${sub.endpoint}`);
}
async function pushOff() {
  const sub = await currentSub();
  if (sub) { await api("POST", "/is-listesi/api/bildirim/push/sil", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
  store.set("zs-push", "");
}
// Açılışta: izin verilmiş ve abonelik varsa sunucunun bildiğinden emin ol (başka kullanıcıyla
// açılmış cihaz, tarayıcının yenilediği abonelik).
async function pushResync() {
  try {
    if (!pushSupported()) return;
    await swReg();
    if (Notification.permission !== "granted") return;
    const sub = await currentSub();
    if (sub && store.get("zs-push") !== `${S.me.id}|${sub.endpoint}`) {
      await api("POST", "/is-listesi/api/bildirim/push", sub.toJSON());
      store.set("zs-push", `${S.me.id}|${sub.endpoint}`);
    }
  } catch (_) { /* sessiz: ayar ekranından yeniden açılabilir */ }
}

async function notifyDialog() {
  const dlg = $("dlg");
  let st;
  try { st = await api("GET", "/is-listesi/api/bildirim"); } catch (e) { toast(e.message); return; }
  const sub = await currentSub().catch(() => null);
  const busy = (fn) => act(async (e) => { await fn(e); await notifyDialog(); });
  // Bu cihaz
  let dev;
  if (!st.push.enabled) dev = [h("p", { class: "small muted" }, "Telefon bildirimi bu sunucuda henüz açılmadı.")];
  else if (!pushSupported()) dev = [h("p", { class: "small" }, isIOS() && !isStandalone()
    ? ["iPhone'da bildirim için önce siteyi ana ekrana ekleyin: Safari'de ", h("b", {}, "Paylaş"), " düğmesi → ",
      h("b", {}, "Ana Ekrana Ekle"), ". Sonra ana ekrandaki İş Listesi simgesinden açıp buraya dönün."]
    : "Bu tarayıcı bildirim desteklemiyor. Telefonda Chrome (Android) ya da ana ekrana eklenmiş Safari (iPhone) kullanın.")];
  else if (Notification.permission === "denied") dev = [h("p", { class: "small" },
    "Bildirim izni bu cihazda engellenmiş. Tarayıcının site ayarlarından (ya da telefonun Ayarlar → Bildirimler bölümünden) İş Listesi'e izin verin.")];
  else if (sub) dev = [h("div", { class: "nt-on" }, icon("check"), "Bu cihazda açık"),
    h("button", { type: "button", class: "btn-ghost sm", onclick: busy(pushOff) }, "Bu cihazda kapat")];
  else dev = [h("p", { class: "small muted" }, "İş atandığında, onay beklediğinde ya da karar verildiğinde bu cihaza bildirim gelir."),
    h("button", { type: "button", class: "btn", onclick: busy(() => pushOn(st.push.key)) }, icon("bell"), "Bu cihazda aç")];
  // Telegram
  let tg;
  if (!st.telegram.enabled) tg = [h("p", { class: "small muted" }, "Telegram botu bu sunucuda henüz ayarlanmadı.")];
  else if (st.telegram.linked) tg = [h("div", { class: "nt-on" }, icon("check"), `Bağlı${st.telegram.name ? ` (${st.telegram.name})` : ""}`),
    h("button", { type: "button", class: "btn-ghost sm", onclick: busy(() => api("POST", "/is-listesi/api/bildirim/telegram/sil", {})) }, "Bağlantıyı kaldır")];
  else tg = [h("p", { class: "small muted" }, `Bildirimler Telegram'da @${st.telegram.bot} botundan gelir. Bağla'ya basın, Telegram açılınca `,
      h("b", {}, "BAŞLAT"), "'a dokunun, sonra buraya dönüp Yenile'ye basın."),
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn", onclick: act(async () => {
        const w = window.open("about:blank", "_blank");
        const d = await api("POST", "/is-listesi/api/bildirim/telegram", {});
        if (w) w.location = d.url; else location.href = d.url;
      }) }, "Telegram'ı bağla"),
      h("button", { type: "button", class: "btn-ghost sm", onclick: () => notifyDialog() }, "Yenile"))];
  const anyOn = !!sub || st.telegram.linked;
  dlg.replaceChildren(h("h3", {}, "Bildirimler"),
    h("div", { class: "small muted" }, "Size iş atandığında, ek atanan ya da onay yetkilisi yapıldığınızda, iş onayınıza gönderildiğinde, işiniz onaylanıp reddedildiğinde, ek süre istenip karara bağlandığında ve size iş devredildiğinde. Bildirimde iş başlığı yazar."),
    h("div", { class: "nt-sec" }, h("div", { class: "nt-h" }, icon("bell"), "Bu cihaz", h("span", { class: "muted small" }, ` · kayıtlı cihazınız: ${st.push.devices}`)), ...dev),
    h("div", { class: "nt-sec" }, h("div", { class: "nt-h" }, icon("send"), "Telegram"), ...tg),
    h("div", { class: "actions", style: "margin-top:18px" },
      anyOn ? h("button", { type: "button", class: "btn-ghost", onclick: act(async () => {
        const r = await api("POST", "/is-listesi/api/bildirim/deneme", {});
        toast(`Deneme gönderildi (${r.devices} cihaz${r.telegram ? " + Telegram" : ""}).`, true); }) }, "Deneme bildirimi gönder") : null,
      h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Kapat")));
  if (!dlg.open) dlg.showModal();
}

// Telefonda üst çubuk sade: kişi simgesine dokununca hesap menüsü.
function accountSheet() {
  const dlg = $("dlg");
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const item = (ic, label, fn) => h("button", { type: "button", class: "sheet-item", onclick: fn }, icon(ic), label);
  dlg.replaceChildren(h("div", { class: "sheet-me" }, avatar(S.me.id, "lg"), h("div", {},
      h("div", { style: "font-weight:700" }, S.me.display_name), h("div", { class: "small muted" }, isAdmin() ? "Yönetici" : "Kullanıcı"))),
    item("bell", "Bildirimler", () => notifyDialog()),
    item("copy", "İş şablonları", () => { dlg.close(); go("sablonlar"); }),
    S.me.account_username ? null : item("key", "Parolamı değiştir", () => { dlg.close(); passwordDialog(); }),
    item(dark ? "sun" : "moon", dark ? "Açık tema" : "Koyu tema", () => { $("btn-theme").click(); dlg.close(); }),
    h("form", { method: "post", action: "/is-listesi/logout", style: "margin:0" },
      h("button", { type: "submit", class: "sheet-item danger" }, icon("logout"), "Çıkış")),
    h("div", { class: "actions", style: "margin-top:10px" }, h("button", { type: "button", class: "btn-ghost", onclick: () => dlg.close() }, "Kapat")));
  dlg.showModal();
}

// Bildirimden gelen bağlantı: /is-listesi#is-<id> → işi uygun görünümde aç; #sablonlar → Şablonlar
// (UYAP'taki İş Ata penceresinin "Şablonları yönet" bağlantısı).
async function openFromLink(url) {
  if (/#sablonlar$/.test(url || location.hash)) { history.replaceState(null, "", "/is-listesi"); go("sablonlar"); return; }
  const m = /#is-(\d+)$/.exec(url || location.hash);
  if (!m) return;
  history.replaceState(null, "", "/is-listesi");
  const id = Number(m[1]);
  try {
    const t = (await api("GET", `/is-listesi/api/tasks/${id}`)).task;
    const me = S.me.id;
    const view = t.is_private ? "ozel"
      : (t.assignees || []).includes(me) || t.subtasks.some((x) => x.assignee_id === me) ? "benim"
      : t.status === "onayda" ? "onay" : isAdmin() ? "tumu" : "onay";
    go(view, id);
  } catch (e) { toast(e.message); }
}

// ── başlat ─────────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  $("logo").append(icon("check"));
  applyTheme(getTheme());
  const s = store.get("zs-sort"); if (["tarih", "oncelik", "asama", "eklenme"].includes(s)) S.sort = s;
  try {
    const f = JSON.parse(store.get("zs-facet") || "{}");
    S.fd = typeof f.fd === "string" ? f.fd : ""; S.fg = typeof f.fg === "string" ? f.fg : "";
    S.fa = typeof f.fa === "string" ? f.fa : "";
    S.ft = Array.isArray(f.ft) ? f.ft.filter((k) => typeof k === "string") : [];
  } catch (_) { /* bozuk kayıt: süzgeçsiz başla */ }
  $("btn-theme").addEventListener("click", () => {
    const t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    store.set("zs-theme", t); applyTheme(t);
  });
  $("btn-pw").addEventListener("click", passwordDialog);
  $("btn-notify").addEventListener("click", () => notifyDialog());
  $("me").addEventListener("click", () => { if (matchMedia("(max-width:900px)").matches) accountSheet(); });
  document.addEventListener("keydown", onKey);
  try { await loadMe(); } catch (e) { toast(e.message); return; }
  await loadMeta();
  await refresh();
  await openFromLink();
  addEventListener("hashchange", () => openFromLink());
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (e) => { if (e.data && e.data.type === "zs-open") openFromLink(e.data.url); });
    pushResync();
  }
});
