//! Toplu takip açma sonrası veritabanı kaydı (`uyap_core/dosya_kaydet.py` 1:1 portu)
//! =================================================================================
//! Bir takip UYAP'ta GERÇEKTEN açılıp gerçek esas no ("2026/894734" gibi, bkz.
//! `crate::esas_no`) doğrulandıktan SONRA çağrılır. `search_phrase_detayli.ajx` ile
//! o dosyanın TAM kapak künyesini yeniden çeker ve `models/icra_models/ingest.py`'nin
//! `dosya_kunyesi_kaydet` fonksiyonuna verir (varsa Python/Django köprüsü üzerinden).
//!
//! Hata (ağ/DB) durumunda BU FONKSİYON SESSİZCE `None` döner + varsa `log` ile
//! uyarır: takip zaten UYAP'ta AÇIK, veritabanı senkronu başarısız olsa da GERÇEK
//! dosya kaybolmaz (sonraki normal 'Dosyalarım' taramasında yakalanır).

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{json, Value};

use crate::jobs::JobContext;

pub async fn _api_json(
    ctx: &JobContext,
    path: &str,
    payload: Option<&Value>,
) -> Result<Value, String> {
    let body = payload.cloned().unwrap_or_else(|| json!({}));
    let resp = ctx
        .uyap_json("POST", path, &body)
        .await
        .map_err(|e| e.to_string())?;
    if resp.status_code >= 400 {
        return Err(format!("UYAP '{}' HTTP {} döndürdü.", path, resp.status_code));
    }
    serde_json::from_str(&resp.text).map_err(|e| format!("JSON ayrıştırma hatası ({}): {}", path, e))
}

pub fn _yil_sira(gercek_dosya_no: &str) -> (Option<i64>, Option<i64>) {
    let s = gercek_dosya_no.trim();
    if let Some((yil_str, sira_str)) = s.split_once('/') {
        if let (Ok(yil), Ok(sira)) = (yil_str.trim().parse::<i64>(), sira_str.trim().parse::<i64>()) {
            return (Some(yil), Some(sira));
        }
    }
    (None, None)
}

/// `search_phrase_detayli.ajx` yanıtı (envelope farklı şekillerde olabilir)
/// içinden `dosyaNo`'su TAM eşleşen kaydı döner.
pub fn _kayit_sec(veri: &Value, gercek_dosya_no: &str) -> Option<Value> {
    let mut kayitlar: Vec<Value> = if let Some(obj) = veri.as_object() {
        let mut found = None;
        for k in ["data", "veri", "list", "rows", "dosyalar"] {
            if let Some(arr) = obj.get(k).and_then(|v| v.as_array()) {
                found = Some(arr.clone());
                break;
            }
        }
        found.unwrap_or_else(|| vec![veri.clone()])
    } else if let Some(arr) = veri.as_array() {
        arr.clone()
    } else {
        return None;
    };

    if let Some(first_arr) = kayitlar.first().and_then(|v| v.as_array()).cloned() {
        kayitlar = first_arr;
    }

    let obj_kayitlar: Vec<Value> = kayitlar.into_iter().filter(|k| k.is_object()).collect();
    for k in &obj_kayitlar {
        let dn = k
            .get("dosyaNo")
            .and_then(|v| match v {
                Value::String(s) => Some(s.as_str()),
                _ => None,
            })
            .unwrap_or("")
            .trim();
        if dn == gercek_dosya_no {
            return Some(k.clone());
        }
    }
    if obj_kayitlar.len() == 1 {
        obj_kayitlar.into_iter().next()
    } else {
        None
    }
}

fn find_models_dir() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("models"));
        if let Some(parent) = cwd.parent() {
            candidates.push(parent.join("models"));
            if let Some(gp) = parent.parent() {
                candidates.push(gp.join("models"));
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("models"));
            if let Some(parent) = dir.parent() {
                candidates.push(parent.join("models"));
                if let Some(gp) = parent.parent() {
                    candidates.push(gp.join("models"));
                }
            }
        }
    }
    candidates
        .into_iter()
        .find(|p| p.join("icra_models").join("ingest.py").is_file())
}

/// Django ORM ile `rec` ve `taraflar`'ı yazar (`uyap_core/dosya_kaydet.py::_django_yaz` 1:1 karşılığı).
pub fn _django_yaz(
    rec: &Value,
    taraflar: Option<&[(String, Value)]>,
) -> Result<(Value, bool), String> {
    let models_dir = find_models_dir()
        .ok_or_else(|| "models/icra_models/ingest.py bulunamadı".to_string())?;

    let py_script = r#"
import os, sys, json
payload = json.loads(sys.stdin.read())
models_dir = payload["models_dir"]
if models_dir not in sys.path:
    sys.path.insert(0, models_dir)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
import django
django.setup()
from django.db import transaction
from icra_models.ingest import dosya_kunyesi_kaydet
from icra_models.models import Taraf, DosyaTaraf

def _taraf_upsert(Taraf, info):
    tckn = info.get("tckn")
    mersis_no = info.get("mersis_no")
    defaults = {
        "tur": info.get("tur") or "gercek",
        "ad": info.get("ad") or "", "soyad": info.get("soyad") or "",
        "unvan": info.get("unvan") or "", "vergi_no": info.get("vergi_no") or "",
        "mersis_no": mersis_no,
    }
    taraf = None
    if tckn:
        taraf = Taraf.objects.filter(tckn=tckn).first()
    elif mersis_no:
        taraf = Taraf.objects.filter(mersis_no=mersis_no).first()
    if not taraf:
        if defaults["tur"] == "tuzel" and defaults["unvan"]:
            taraf = Taraf.objects.filter(tur="tuzel", unvan=defaults["unvan"]).first()
        elif defaults["tur"] == "gercek" and defaults["ad"] and defaults["soyad"]:
            taraf = Taraf.objects.filter(tur="gercek", ad=defaults["ad"], soyad=defaults["soyad"]).first()
    if taraf:
        for k, v in defaults.items():
            setattr(taraf, k, v)
        taraf.tckn = tckn or taraf.tckn
        taraf.save()
        return taraf
    if tckn:
        defaults["tckn"] = tckn
    return Taraf.objects.create(**defaults)

rec = payload["rec"]
taraflar = payload.get("taraflar") or []
dosya_obj, created = dosya_kunyesi_kaydet(rec, yargi_turu=2)
with transaction.atomic():
    for i, item in enumerate(taraflar):
        rol, info = item[0], item[1]
        if not (info.get("tckn") or info.get("mersis_no") or info.get("ad") or info.get("unvan")):
            continue
        taraf_obj = _taraf_upsert(Taraf, info)
        DosyaTaraf.objects.update_or_create(
            dosya=dosya_obj, taraf=taraf_obj, rol=rol, defaults={"sira": i})
print(json.dumps({"id": getattr(dosya_obj, "id", None), "created": bool(created)}))
"#;

    let taraflar_json: Vec<Value> = taraflar
        .unwrap_or(&[])
        .iter()
        .map(|(rol, info)| json!([rol, info]))
        .collect();
    let input_json = serde_json::to_string(&json!({
        "models_dir": models_dir.to_string_lossy(),
        "rec": rec,
        "taraflar": taraflar_json,
    }))
    .map_err(|e| e.to_string())?;

    let mut child = Command::new("python")
        .arg("-c")
        .arg(py_script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("python başlatılamadı: {}", e))?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(input_json.as_bytes())
            .map_err(|e| format!("python stdin yazma hatası: {}", e))?;
    }

    let out = child
        .wait_with_output()
        .map_err(|e| format!("python bekleme hatası: {}", e))?;

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(stderr.trim().to_string());
    }

    let stdout = String::from_utf8_lossy(&out.stdout);
    let parsed: Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("python çıktı ayrıştırma hatası: {}", e))?;
    let created = parsed
        .get("created")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    Ok((parsed, created))
}

/// Az önce açılan takibi `search_phrase_detayli.ajx` ile YENİDEN sorgulayıp
/// veritabanına (`Birim+Dosya`) upsert eder, sonra `taraflar` biliniyorsa
/// alacaklı/borçlu bağlarını da (`Taraf+DosyaTaraf`) yazar.
/// Bulunamazsa/hata olursa `None` döner — çağıran taraf bunu FATAL saymamalı.
pub async fn kaydet(
    ctx: &JobContext,
    gercek_dosya_no: &str,
    taraflar: Option<&[(String, Value)]>,
    log: Option<&(dyn Fn(&str) + Send + Sync)>,
) -> Option<Value> {
    let (yil_opt, sira_opt) = _yil_sira(gercek_dosya_no);
    let (yil, sira) = match (yil_opt, sira_opt) {
        (Some(y), Some(s)) => (y, s),
        _ => return None,
    };

    let rec = match async {
        let payload = json!({
            "dosyaYil": yil,
            "dosyaSira": sira,
            "dosyaDurumKod": 0,
            "birimTuru2": "1101",
            "birimTuru3": "2",
            "pageSize": 50,
            "pageNumber": 1,
        });
        let veri = _api_json(ctx, "search_phrase_detayli.ajx", Some(&payload)).await?;
        Ok::<Option<Value>, String>(_kayit_sec(&veri, gercek_dosya_no))
    }
    .await
    {
        Ok(Some(r)) => r,
        Ok(None) => {
            if let Some(l) = log {
                l(&format!(
                    "⚠️ Veritabanı senkronu: {} search_phrase_detayli'de henüz bulunamadı — bir sonraki 'Dosyalarım' taramasında yakalanacak.",
                    gercek_dosya_no
                ));
            }
            return None;
        }
        Err(e) => {
            if let Some(l) = log {
                l(&format!(
                    "⚠️ Veritabanı senkronu: {} sorgusu başarısız ({}) — dosya UYAP'ta AÇIK, 'Dosyalarım' bir sonraki taramada yakalayacak.",
                    gercek_dosya_no, e
                ));
            }
            return None;
        }
    };

    let rec_clone = rec.clone();
    let taraflar_owned: Option<Vec<(String, Value)>> = taraflar.map(|t| t.to_vec());
    let db_res = tokio::task::spawn_blocking(move || {
        _django_yaz(&rec_clone, taraflar_owned.as_deref())
    })
    .await;

    match db_res {
        Ok(Ok((dosya_obj, created))) => {
            if let Some(l) = log {
                let mut msg = format!(
                    "✓ Veritabanına {}: {}",
                    if created { "eklendi" } else { "güncellendi" },
                    gercek_dosya_no
                );
                if let Some(t) = taraflar {
                    msg.push_str(&format!(" ({} taraf)", t.len()));
                }
                l(&msg);
            }
            Some(dosya_obj)
        }
        Ok(Err(e)) => {
            if let Some(l) = log {
                l(&format!(
                    "⚠️ Veritabanına kaydedilemedi ({}) — dosya UYAP'ta AÇIK, sonraki 'Dosyalarım' taramasında otomatik yakalanır.",
                    e
                ));
            }
            None
        }
        Err(e) => {
            if let Some(l) = log {
                l(&format!(
                    "⚠️ Veritabanına kaydedilemedi ({}) — dosya UYAP'ta AÇIK, sonraki 'Dosyalarım' taramasında otomatik yakalanır.",
                    e
                ));
            }
            None
        }
    }
}
