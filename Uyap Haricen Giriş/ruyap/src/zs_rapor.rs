//! /zs İş Takip — raporlar (zs_rapor.py 1:1 port)
//! ----------------------------------------------
//! Kişi bazında ve zaman bazında rapor. Ham veriyi ZsStore.report_source() verir; hesap
//! burada saf Rust ile yapılır (veritabanından bağımsız, test edilebilir).
//!
//! Kapsam: yalnız ANA işler. Özel işlerden yalnız raporu alan kişinin kendi açtıkları girer (özel iş
//! yalnız açanına görünür). Silinmiş işler veritabanında olmadığı için sayılmaz.
//! Yönetici: kişi / iş / zaman raporu, herkes için. Kullanıcı: iş / zaman raporu, yalnız kendi işleri.
//!
//! Tanımlar
//! - Biten iş: şu an onaylandı/tamamlandı durumundaki iş; bitiş anı son onay/tamamlama olayıdır.
//! - Süresinde / geç: onaya gönderilme (özel işte tamamlanma) anı son tarihe göre. Son tarihsiz
//!   iş "tarihsiz" sayılır.
//! - Kişinin işi: asıl atanan + ek atananlar (bir iş birden çok kişide sayılabilir).
//! - Onay kararı: onay/ret olayı; bekleme = karar anı - son onaya gönderilme anı. "Onay gecikti":
//!   iş süresinde gönderilmiş, karar son tarihten sonra verilmiş.
//!
//! Gün sınırları Türkiye saatine (UTC+3, yaz saati yok) göre.

use std::collections::BTreeMap;
use std::fmt;

use chrono::{Datelike, Duration, FixedOffset, NaiveDate, TimeZone};
use serde_json::{json, Map, Value};

pub const GROUPS: &[&str] = &["gun", "hafta", "ay"];
pub const MAX_DAYS: i64 = 3 * 366;
pub const _AY: &[&str] = &[
    "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara",
];

pub const ACT_CATS: &[&str] = &[
    "acti",
    "not",
    "belge",
    "alt_is",
    "onaya",
    "tamamladi",
    "onayladi",
    "reddetti",
    "ek_sure",
    "diger",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RaporError(pub String);

impl fmt::Display for RaporError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RaporError {}

pub fn tr_tz() -> FixedOffset {
    FixedOffset::east_opt(3 * 3600).unwrap()
}

/// 'YYYY-MM-DD' iki tarih → (başlangıç epoch, bitiş epoch [bitiş gününün sonu, hariç]).
pub fn parse_range(bas: Option<&str>, bit: Option<&str>) -> Result<(i64, i64), RaporError> {
    let bas_s = bas.ok_or_else(|| RaporError("Tarih aralığı geçersiz.".to_string()))?;
    let bit_s = bit.ok_or_else(|| RaporError("Tarih aralığı geçersiz.".to_string()))?;
    let d1 = NaiveDate::parse_from_str(bas_s, "%Y-%m-%d")
        .map_err(|_| RaporError("Tarih aralığı geçersiz.".to_string()))?;
    let d2 = NaiveDate::parse_from_str(bit_s, "%Y-%m-%d")
        .map_err(|_| RaporError("Tarih aralığı geçersiz.".to_string()))?;
    if d2 < d1 {
        return Err(RaporError(
            "Bitiş tarihi başlangıçtan önce olamaz.".to_string(),
        ));
    }
    if (d2 - d1).num_days() > MAX_DAYS {
        return Err(RaporError(
            "Rapor aralığı en fazla 3 yıl olabilir.".to_string(),
        ));
    }
    let next_day = d2 + Duration::days(1);
    Ok((_epoch(d1), _epoch(next_day)))
}

pub fn _epoch(d: NaiveDate) -> i64 {
    let naive_dt = d.and_hms_opt(0, 0, 0).unwrap();
    tr_tz()
        .from_local_datetime(&naive_dt)
        .single()
        .unwrap()
        .timestamp()
}

pub fn _date(ts: i64) -> NaiveDate {
    tr_tz().timestamp_opt(ts, 0).single().unwrap().date_naive()
}

/// `[(etiket, başlangıç, bitiş)]` — ilk/son dönem aralık sınırına kırpılır.
pub fn _buckets(start: i64, end: i64, group: &str) -> Vec<(String, i64, i64)> {
    let mut d = _date(start);
    let last = _date(end - 1);
    if group == "hafta" {
        d -= Duration::days(d.weekday().num_days_from_monday() as i64);
    } else if group == "ay" {
        d = d.with_day(1).unwrap();
    }
    let mut out = Vec::new();
    while d <= last {
        let (nxt, label) = if group == "gun" {
            let nxt = d + Duration::days(1);
            let label = format!("{} {}", d.day(), _AY[(d.month() - 1) as usize]);
            (nxt, label)
        } else if group == "hafta" {
            let nxt = d + Duration::days(7);
            let e = nxt - Duration::days(1);
            let label = format!(
                "{} {} – {} {}",
                d.day(),
                _AY[(d.month() - 1) as usize],
                e.day(),
                _AY[(e.month() - 1) as usize]
            );
            (nxt, label)
        } else {
            let nxt = (d.with_day(28).unwrap() + Duration::days(4))
                .with_day(1)
                .unwrap();
            let label = format!("{} {}", _AY[(d.month() - 1) as usize], d.year());
            (nxt, label)
        };
        out.push((label, _epoch(d).max(start), _epoch(nxt).min(end)));
        d = nxt;
    }
    out
}

#[derive(Debug, Clone)]
struct PreparedTask {
    id: i64,
    title: String,
    status: String,
    created_at: i64,
    due_at: Option<i64>,
    submitted_at: Option<i64>,
    finished_at: Option<i64>,
    ontime: Option<bool>,
    assignees: Vec<i64>,
    is_private: Value,
    events: Vec<Value>,
}

fn _prepare(src: &Value) -> BTreeMap<i64, PreparedTask> {
    let mut tasks = BTreeMap::new();
    if let Some(arr) = src.get("tasks").and_then(|v| v.as_array()) {
        for t in arr {
            let id = t.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
            let assignee_id = t.get("assignee_id").and_then(|v| v.as_i64());
            let assignees = match assignee_id {
                Some(uid) if uid != 0 => vec![uid],
                _ => Vec::new(),
            };
            tasks.insert(
                id,
                PreparedTask {
                    id,
                    title: t
                        .get("title")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    status: t
                        .get("status")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    created_at: t.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0),
                    due_at: t.get("due_at").and_then(|v| v.as_i64()),
                    submitted_at: t.get("submitted_at").and_then(|v| v.as_i64()),
                    finished_at: None,
                    ontime: None,
                    assignees,
                    is_private: t.get("is_private").cloned().unwrap_or(json!(0)),
                    events: Vec::new(),
                },
            );
        }
    }
    if let Some(members) = src.get("members").and_then(|v| v.as_array()) {
        for m in members {
            let (tid, uid) = if let Some(arr) = m.as_array() {
                (
                    arr.first().and_then(|v| v.as_i64()).unwrap_or(0),
                    arr.get(1).and_then(|v| v.as_i64()).unwrap_or(0),
                )
            } else {
                (
                    m.get("task_id").and_then(|v| v.as_i64()).unwrap_or(0),
                    m.get("user_id").and_then(|v| v.as_i64()).unwrap_or(0),
                )
            };
            if let Some(t) = tasks.get_mut(&tid) {
                if !t.assignees.contains(&uid) {
                    t.assignees.push(uid);
                }
            }
        }
    }
    if let Some(events) = src.get("events").and_then(|v| v.as_array()) {
        for e in events {
            let tid = e.get("task_id").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(t) = tasks.get_mut(&tid) {
                t.events.push(e.clone());
            }
        }
    }
    for t in tasks.values_mut() {
        let done = t.status == "onaylandi" || t.status == "tamamlandi";
        let fin: Vec<i64> = t
            .events
            .iter()
            .filter_map(|e| {
                let k = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                if k == "approved" || k == "completed" {
                    e.get("created_at").and_then(|v| v.as_i64())
                } else {
                    None
                }
            })
            .collect();
        t.finished_at = if done && !fin.is_empty() {
            fin.last().copied()
        } else {
            None
        };
        t.ontime = match (t.due_at, t.submitted_at) {
            (Some(due), Some(sent)) if due != 0 && sent != 0 => Some(sent <= due),
            _ => None,
        };
    }
    tasks
}

#[derive(Debug, Clone)]
struct Decision {
    actor: Option<i64>,
    at: i64,
    kind: String,
    wait: i64,
    late: bool,
}

fn _decisions(tasks: &BTreeMap<i64, PreparedTask>) -> Vec<Decision> {
    let mut out = Vec::new();
    for t in tasks.values() {
        let mut last_sub: Option<i64> = None;
        for e in &t.events {
            let kind = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
            let created_at = e.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0);
            if kind == "submitted" {
                last_sub = Some(created_at);
            } else if (kind == "approved" || kind == "rejected") && last_sub.is_some() {
                let sub_ts = last_sub.unwrap();
                let due = t.due_at;
                let late = match due {
                    Some(d) if d != 0 => sub_ts <= d && d < created_at,
                    _ => false,
                };
                out.push(Decision {
                    actor: e.get("actor_id").and_then(|v| v.as_i64()),
                    at: created_at,
                    kind: kind.to_string(),
                    wait: created_at - sub_ts,
                    late,
                });
            }
        }
    }
    out
}

fn _avg(xs: &[i64]) -> Option<i64> {
    if xs.is_empty() {
        None
    } else {
        let sum: i64 = xs.iter().sum();
        Some(sum / (xs.len() as i64))
    }
}

pub fn kisi_raporu(src: &Value, users: &[Value], start: i64, end: i64, now: i64) -> Vec<Value> {
    let tasks = _prepare(src);
    let decisions: Vec<Decision> = _decisions(&tasks)
        .into_iter()
        .filter(|d| start <= d.at && d.at < end)
        .collect();
    let mut rows = Vec::new();
    for u in users {
        let uid = u.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let name = u.get("display_name").and_then(|v| v.as_str()).unwrap_or("");
        let active = u
            .get("active")
            .and_then(|v| {
                v.as_bool()
                    .or_else(|| v.as_i64().map(|n| n != 0))
            })
            .unwrap_or(false);

        let mine: Vec<&PreparedTask> = tasks
            .values()
            .filter(|t| t.assignees.contains(&uid))
            .collect();
        let fin: Vec<&&PreparedTask> = mine
            .iter()
            .filter(|t| {
                if let Some(fa) = t.finished_at {
                    start <= fa && fa < end
                } else {
                    false
                }
            })
            .collect();
        let open_: Vec<&&PreparedTask> = mine
            .iter()
            .filter(|t| t.status != "onaylandi" && t.status != "tamamlandi")
            .collect();
        let dec: Vec<&Decision> = decisions
            .iter()
            .filter(|d| d.actor == Some(uid))
            .collect();

        let yeni = mine
            .iter()
            .filter(|t| start <= t.created_at && t.created_at < end)
            .count() as i64;
        let biten = fin.len() as i64;
        let suresinde = fin.iter().filter(|t| t.ontime == Some(true)).count() as i64;
        let gec = fin.iter().filter(|t| t.ontime == Some(false)).count() as i64;
        let tarihsiz = fin.iter().filter(|t| t.ontime.is_none()).count() as i64;
        let reddedilen: i64 = mine
            .iter()
            .map(|t| {
                t.events
                    .iter()
                    .filter(|e| {
                        let k = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                        let ca = e.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0);
                        k == "rejected" && start <= ca && ca < end
                    })
                    .count() as i64
            })
            .sum();
        let sures: Vec<i64> = fin
            .iter()
            .map(|t| t.finished_at.unwrap() - t.created_at)
            .collect();
        let ort_sure = _avg(&sures);
        let acik = open_.len() as i64;
        let geciken = open_
            .iter()
            .filter(|t| t.due_at.map(|d| d != 0 && d < now).unwrap_or(false))
            .count() as i64;
        let onayladi = dec.iter().filter(|d| d.kind == "approved").count() as i64;
        let reddetti = dec.iter().filter(|d| d.kind == "rejected").count() as i64;
        let waits: Vec<i64> = dec.iter().map(|d| d.wait).collect();
        let ort_karar = _avg(&waits);
        let gec_karar = dec.iter().filter(|d| d.late).count() as i64;

        if active
            || yeni > 0
            || biten > 0
            || reddedilen > 0
            || acik > 0
            || onayladi > 0
            || reddetti > 0
        {
            rows.push(json!({
                "user_id": uid,
                "name": name,
                "active": active,
                "yeni": yeni,
                "biten": biten,
                "suresinde": suresinde,
                "gec": gec,
                "tarihsiz": tarihsiz,
                "reddedilen": reddedilen,
                "ort_sure": ort_sure,
                "acik": acik,
                "geciken": geciken,
                "onayladi": onayladi,
                "reddetti": reddetti,
                "ort_karar": ort_karar,
                "gec_karar": gec_karar,
            }));
        }
    }
    rows
}

pub fn zaman_raporu(
    src: &Value,
    start: i64,
    end: i64,
    group: &str,
    now: i64,
    person: Option<i64>,
) -> Result<Vec<Value>, RaporError> {
    if !GROUPS.contains(&group) {
        return Err(RaporError(
            "Gruplama gün, hafta ya da ay olmalı.".to_string(),
        ));
    }
    let mut tasks = _prepare(src);
    if let Some(p) = person {
        if p != 0 {
            tasks.retain(|_, t| t.assignees.contains(&p));
        }
    }
    let mut rows = Vec::new();
    for (label, b0, b1) in _buckets(start, end, group) {
        let fin: Vec<&PreparedTask> = tasks
            .values()
            .filter(|t| {
                if let Some(fa) = t.finished_at {
                    b0 <= fa && fa < b1
                } else {
                    false
                }
            })
            .collect();
        let acilan = tasks
            .values()
            .filter(|t| b0 <= t.created_at && t.created_at < b1)
            .count() as i64;
        let biten = fin.len() as i64;
        let suresinde = fin.iter().filter(|t| t.ontime == Some(true)).count() as i64;
        let gec = fin.iter().filter(|t| t.ontime == Some(false)).count() as i64;
        let reddedilen: i64 = tasks
            .values()
            .map(|t| {
                t.events
                    .iter()
                    .filter(|e| {
                        let k = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                        let ca = e.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0);
                        k == "rejected" && b0 <= ca && ca < b1
                    })
                    .count() as i64
            })
            .sum();
        let acik_sonda: Option<i64> = if b0 > now {
            None
        } else {
            Some(
                tasks
                    .values()
                    .filter(|t| {
                        t.created_at < b1
                            && !t.finished_at.map(|fa| fa < b1).unwrap_or(false)
                    })
                    .count() as i64,
            )
        };
        rows.push(json!({
            "label": label,
            "start": b0,
            "end": b1,
            "acilan": acilan,
            "biten": biten,
            "suresinde": suresinde,
            "gec": gec,
            "reddedilen": reddedilen,
            "acik_sonda": acik_sonda,
        }));
    }
    Ok(rows)
}

/// İş bazında rapor: aralıkta açılan, aralıkta biten ya da şu an açık işler.
pub fn is_listesi(src: &Value, start: i64, end: i64, person: Option<i64>) -> Vec<Value> {
    let tasks = _prepare(src);
    let mut out: Vec<PreparedTask> = Vec::new();
    for t in tasks.into_values() {
        if let Some(p) = person {
            if p != 0 && !t.assignees.contains(&p) {
                continue;
            }
        }
        let fin = t.finished_at;
        let open_ = t.status != "onaylandi" && t.status != "tamamlandi";
        if open_
            || (start <= t.created_at && t.created_at < end)
            || fin.map(|fa| start <= fa && fa < end).unwrap_or(false)
        {
            out.push(t);
        }
    }
    out.sort_by_key(|x| (x.due_at.unwrap_or(9_000_000_000_000), x.id));
    out.into_iter()
        .map(|t| {
            json!({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "created_at": t.created_at,
                "due_at": t.due_at,
                "submitted_at": t.submitted_at,
                "finished_at": t.finished_at,
                "ontime": t.ontime,
                "assignees": t.assignees,
                "is_private": t.is_private,
            })
        })
        .collect()
}

fn _act_cat(it: &Value) -> &'static str {
    let kind = it.get("kind").and_then(|v| v.as_str()).unwrap_or("");
    let detail = it.get("detail");
    if kind == "created"
        && detail
            .and_then(|d| d.get("sub"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    {
        return "alt_is";
    }
    if kind == "subtask"
        && !detail
            .and_then(|d| d.get("done"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    {
        return "diger";
    }
    match kind {
        "created" => "acti",
        "note" => "not",
        "file" => "belge",
        "subtask" => "alt_is",
        "submitted" => "onaya",
        "completed" => "tamamladi",
        "approved" => "onayladi",
        "rejected" => "reddetti",
        "ext_requested" => "ek_sure",
        _ => "diger",
    }
}

/// items: ZsStore.activity_source() çıktısı (zamana göre sıralı).
/// `items` içindeki her öğeye `"cat"` alanı da eklenir (Python ile birebir).
pub fn etkinlik(items: &mut [Value], start: i64, end: i64) -> Value {
    let mut ozet: Map<String, Value> = Map::new();
    for &c in ACT_CATS {
        ozet.insert(c.to_string(), json!(0));
    }

    let mut days: Vec<Map<String, Value>> = _buckets(start, end, "gun")
        .into_iter()
        .map(|(lbl, a, b)| {
            let mut m = Map::new();
            m.insert("label".to_string(), json!(lbl));
            m.insert("start".to_string(), json!(a));
            m.insert("end".to_string(), json!(b));
            m.insert("toplam".to_string(), json!(0));
            for &c in ACT_CATS {
                m.insert(c.to_string(), json!(0));
            }
            m
        })
        .collect();

    let start_date = _date(start);
    let mut tasks: BTreeMap<i64, Map<String, Value>> = BTreeMap::new();

    for it in items.iter_mut() {
        let c = _act_cat(it);
        if let Some(obj) = it.as_object_mut() {
            obj.insert("cat".to_string(), json!(c));
        }
        let cur_ozet = ozet.get(c).and_then(|v| v.as_i64()).unwrap_or(0);
        ozet.insert(c.to_string(), json!(cur_ozet + 1));

        let at = it.get("at").and_then(|v| v.as_i64()).unwrap_or(0);
        let i = (_date(at) - start_date).num_days();
        if i >= 0 && (i as usize) < days.len() {
            let day = &mut days[i as usize];
            let dc = day.get(c).and_then(|v| v.as_i64()).unwrap_or(0);
            day.insert(c.to_string(), json!(dc + 1));
            let dt = day.get("toplam").and_then(|v| v.as_i64()).unwrap_or(0);
            day.insert("toplam".to_string(), json!(dt + 1));
        }

        let task_id = it.get("task_id").and_then(|v| v.as_i64()).unwrap_or(0);
        let t = tasks.entry(task_id).or_insert_with(|| {
            let mut m = Map::new();
            m.insert("task_id".to_string(), json!(task_id));
            m.insert(
                "title".to_string(),
                it.get("title").cloned().unwrap_or(json!("")),
            );
            m.insert(
                "status".to_string(),
                it.get("status").cloned().unwrap_or(Value::Null),
            );
            m.insert(
                "exists".to_string(),
                it.get("exists").cloned().unwrap_or(json!(false)),
            );
            m.insert(
                "is_private".to_string(),
                it.get("is_private").cloned().unwrap_or(json!(false)),
            );
            m.insert("toplam".to_string(), json!(0));
            m.insert("ilk".to_string(), json!(at));
            m.insert("son".to_string(), json!(at));
            for &cat in ACT_CATS {
                m.insert(cat.to_string(), json!(0));
            }
            m
        });
        let tc = t.get(c).and_then(|v| v.as_i64()).unwrap_or(0);
        t.insert(c.to_string(), json!(tc + 1));
        let tt = t.get("toplam").and_then(|v| v.as_i64()).unwrap_or(0);
        t.insert("toplam".to_string(), json!(tt + 1));
        let prev_son = t.get("son").and_then(|v| v.as_i64()).unwrap_or(at);
        t.insert("son".to_string(), json!(prev_son.max(at)));
    }

    let toplam = items.len() as i64;
    let is_sayisi = tasks.len() as i64;
    let aktif_gun = days
        .iter()
        .filter(|d| d.get("toplam").and_then(|v| v.as_i64()).unwrap_or(0) > 0)
        .count() as i64;
    ozet.insert("toplam".to_string(), json!(toplam));
    ozet.insert("is_sayisi".to_string(), json!(is_sayisi));
    ozet.insert("aktif_gun".to_string(), json!(aktif_gun));

    let mut isler: Vec<Map<String, Value>> = tasks.into_values().collect();
    isler.sort_by_key(|t| {
        let son = t.get("son").and_then(|v| v.as_i64()).unwrap_or(0);
        let tid = t.get("task_id").and_then(|v| v.as_i64()).unwrap_or(0);
        (-son, tid)
    });

    json!({
        "ozet": Value::Object(ozet),
        "gunler": days.into_iter().map(Value::Object).collect::<Vec<_>>(),
        "isler": isler.into_iter().map(Value::Object).collect::<Vec<_>>(),
    })
}
