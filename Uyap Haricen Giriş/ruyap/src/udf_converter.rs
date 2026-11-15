//! `udf_converter.rs` — `.docx`, `.doc` ve `.pdf` dosyalarını UYAP UDF `content.xml` baytlarına dönüştürür.

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::Path;

use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use flate2::read::ZlibDecoder;
use roxmltree::{Document, Node};
use zip::ZipArchive;

const W_NS: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const WP_NS: &str = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
const A_NS: &str = "http://schemas.openxmlformats.org/drawingml/2006/main";
const R_NS: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

fn attr_w<'a>(node: Node<'a, 'a>, local: &str) -> Option<&'a str> {
    node.attribute((W_NS, local)).or_else(|| node.attribute(local))
}

fn find_descendant<'a>(node: Node<'a, 'a>, ns: &str, local: &str) -> Option<Node<'a, 'a>> {
    node.descendants()
        .find(|n| n.tag_name().name() == local && (n.tag_name().namespace() == Some(ns) || n.tag_name().namespace().is_none()))
}

fn find_descendants<'a>(node: Node<'a, 'a>, ns: &str, local: &str) -> Vec<Node<'a, 'a>> {
    node.descendants()
        .filter(|n| n.tag_name().name() == local && (n.tag_name().namespace() == Some(ns) || n.tag_name().namespace().is_none()))
        .collect()
}

pub fn doc_to_docx(doc_path: &str) -> Result<String, String> {
    #[cfg(windows)]
    {
        let abs = std::fs::canonicalize(doc_path)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| doc_path.to_string());
        // canonicalize adds \\?\ prefix on Windows which Word COM may dislike
        let abs = abs.strip_prefix(r"\\?\").unwrap_or(&abs).to_string();
        let fname = Path::new(doc_path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("doc.doc");
        let docx_path = std::env::temp_dir()
            .join(format!("{}x", fname))
            .to_string_lossy()
            .into_owned();

        let ps_script = format!(
            "$word = $null; try {{ $word = New-Object -ComObject Word.Application; $word.Visible = $false; $doc = $word.Documents.Open('{}'); $doc.SaveAs2('{}', 16); $doc.Close(); }} finally {{ if ($word -ne $null) {{ try {{ $word.Quit() }} catch {{}} }} }}",
            abs.replace('\'', "''"),
            docx_path.replace('\'', "''")
        );
        let out = std::process::Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", &ps_script])
            .output()
            .map_err(|e| {
                format!(
                    "Legacy .doc dosyası dönüştürülemedi. Bu özellik sisteminizde Microsoft Word kurulu olmasını gerektirir.\nHata detayı: {}",
                    e
                )
            })?;
        if !out.status.success() || !Path::new(&docx_path).exists() {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(format!(
                "Legacy .doc dosyası dönüştürülemedi. Bu özellik sisteminizde Microsoft Word kurulu olmasını gerektirir.\nHata detayı: {}",
                err.trim()
            ));
        }
        Ok(docx_path)
    }
    #[cfg(not(windows))]
    {
        let _ = doc_path;
        Err("Legacy .doc dosyası dönüştürülemedi. Bu özellik sisteminizde Microsoft Word kurulu olmasını gerektirir.".to_string())
    }
}

pub fn get_alignment(paragraph: Node) -> &'static str {
    if let Some(jc) = find_descendant(paragraph, W_NS, "jc") {
        match attr_w(jc, "val") {
            Some("center") => return "1",
            Some("right") => return "2",
            Some("both") => return "3",
            _ => {}
        }
    }
    "0"
}

pub fn get_indent_attrs(paragraph: Node) -> String {
    let ind = find_descendant(paragraph, W_NS, "ind");
    let left = ind.and_then(|n| attr_w(n, "left")).and_then(|s| s.parse::<f64>().ok());
    let right = ind.and_then(|n| attr_w(n, "right")).and_then(|s| s.parse::<f64>().ok());
    let first = ind.and_then(|n| attr_w(n, "firstLine")).and_then(|s| s.parse::<f64>().ok());

    let mut s = format!(
        "LeftIndent=\"{}\" RightIndent=\"{}\"",
        left.map(|v| v / 20.0).unwrap_or(0.0),
        right.map(|v| v / 20.0).unwrap_or(0.0)
    );
    if let Some(fl) = first {
        s.push_str(&format!(" FirstLineIndent=\"{}\"", fl / 20.0));
    }
    s
}

pub fn get_tab_stops_xml(paragraph: Node) -> String {
    let mut tab_stops = Vec::new();
    if let Some(ppr) = find_descendant(paragraph, W_NS, "pPr") {
        if let Some(tabs) = find_descendant(ppr, W_NS, "tabs") {
            for tab in find_descendants(tabs, W_NS, "tab") {
                if let Some(pos_str) = attr_w(tab, "pos") {
                    if let Ok(pos_twips) = pos_str.parse::<f64>() {
                        let pos_pt = pos_twips / 20.0;
                        let align_str = match attr_w(tab, "val") {
                            Some("right") => "RIGHT",
                            Some("center") => "CENTER",
                            Some("decimal") => "DECIMAL",
                            _ => "LEFT",
                        };
                        tab_stops.push(format!(
                            "<tabStop position=\"{}\" alignment=\"{}\" />",
                            pos_pt, align_str
                        ));
                    }
                }
            }
        }
    }
    if tab_stops.is_empty() {
        String::new()
    } else {
        format!("<tabStops>{}</tabStops>", tab_stops.join(""))
    }
}

pub fn get_bullet_type(num_id: &str) -> &'static str {
    match num_id {
        "1" => "BULLET_TYPE_ELLIPSE",
        "2" => "BULLET_TYPE_RECTANGLE",
        "3" => "BULLET_TYPE_RECTANGLE_D",
        "4" => "BULLET_TYPE_ARROW",
        "5" => "BULLET_TYPE_DIAMOND",
        "6" => "BULLET_TYPE_TRIANGLE",
        _ => "BULLET_TYPE_ELLIPSE",
    }
}

pub fn get_number_type(list_id: &str) -> &'static str {
    match list_id {
        "1" => "NUMBER_TYPE_CHAR_SMALL_DOT",
        "2" => "BULLET_TYPE_ARROW",
        "3" => "NUMBER_TYPE_ROMAN_BIG_DOT",
        "4" => "NUMBER_TYPE_CHAR_BIG_DOT",
        "5" => "NUMBER_TYPE_CHAR_SMALL_PARANTHESE",
        "6" => "NUMBER_TYPE_NUMBER_TRE",
        "7" => "NUMBER_TYPE_ROMAN_SMALL_DOT",
        "8" => "BULLET_TYPE_ELLIPSE",
        "9" => "BULLET_TYPE_RECTANGLE",
        "10" => "BULLET_TYPE_RECTANGLE_D",
        "11" => "NUMBER_TYPE_NUMBER_PARANTHESE",
        "12" => "BULLET_TYPE_DIAMOND",
        "13" => "BULLET_TYPE_TRIANGLE",
        _ => "NUMBER_TYPE_NUMBER_TRE",
    }
}

fn process_image(
    drawing: Node,
    rels: &HashMap<String, String>,
    zip: &mut ZipArchive<Cursor<Vec<u8>>>,
) -> Option<(String, i64, i64)> {
    let container = find_descendant(drawing, WP_NS, "inline")
        .or_else(|| find_descendant(drawing, WP_NS, "anchor"));
    let extent = container.and_then(|c| find_descendant(c, WP_NS, "extent"));
    let (width, height) = if let Some(ext) = extent {
        let cx = ext.attribute("cx").and_then(|s| s.parse::<i64>().ok()).unwrap_or(1270000);
        let cy = ext.attribute("cy").and_then(|s| s.parse::<i64>().ok()).unwrap_or(1270000);
        (cx / 12700, cy / 12700)
    } else {
        (100, 100)
    };

    let blip = find_descendant(drawing, A_NS, "blip")?;
    let rid = blip.attribute((R_NS, "embed")).or_else(|| blip.attribute("embed"))?;
    let target = rels.get(rid)?;
    let full_target = if target.starts_with('/') {
        target.trim_start_matches('/').to_string()
    } else if target.starts_with("word/") {
        target.clone()
    } else {
        format!("word/{}", target)
    };

    let mut f = zip.by_name(&full_target).ok()?;
    let mut img_bytes = Vec::new();
    f.read_to_end(&mut img_bytes).ok()?;
    Some((B64.encode(&img_bytes), width, height))
}

fn process_paragraph(
    paragraph: Node,
    rels: &HashMap<String, String>,
    zip: &mut ZipArchive<Cursor<Vec<u8>>>,
    mut current_offset: usize,
) -> (String, String, usize) {
    let mut para_text = String::new();
    let mut para_elements: Vec<String> = Vec::new();

    let mut numbered = false;
    let mut list_id = String::new();
    let mut list_level = String::new();
    let mut number_type = "";

    if let Some(num_pr) = find_descendant(paragraph, W_NS, "numPr") {
        let ilvl = find_descendant(num_pr, W_NS, "ilvl");
        let num_id = find_descendant(num_pr, W_NS, "numId");
        if let (Some(il), Some(ni)) = (ilvl, num_id) {
            if let (Some(il_v), Some(ni_v)) = (attr_w(il, "val"), attr_w(ni, "val")) {
                numbered = true;
                list_id = ni_v.to_string();
                let lvl = il_v.parse::<i64>().unwrap_or(0) + 1;
                list_level = lvl.to_string();
                number_type = get_number_type(&list_id);
            }
        }
    }

    let mut font_family = "Times New Roman".to_string();
    let mut font_size = "12".to_string();

    for run in find_descendants(paragraph, W_NS, "r") {
        for drawing in find_descendants(run, W_NS, "drawing") {
            if let Some((image_data, width, height)) = process_image(drawing, rels, zip) {
                para_text.push('\u{FFFC}');
                para_elements.push(format!(
                    "<image imageData=\"{}\" startOffset=\"{}\" length=\"1\" width=\"{}\" height=\"{}\" />",
                    image_data, current_offset, width, height
                ));
                current_offset += 1;
            }
        }

        let has_t = find_descendants(run, W_NS, "t")
            .iter()
            .any(|t| !t.text().unwrap_or("").is_empty());
        let has_tab = find_descendant(run, W_NS, "tab").is_some();

        if has_t || has_tab {
            if let Some(rf) = find_descendant(run, W_NS, "rFonts") {
                if let Some(asc) = attr_w(rf, "ascii") {
                    if !asc.is_empty() {
                        font_family = asc.to_string();
                    }
                }
            }
            if let Some(sz) = find_descendant(run, W_NS, "sz") {
                if let Some(v) = attr_w(sz, "val").and_then(|s| s.parse::<i64>().ok()) {
                    font_size = (v / 2).to_string();
                }
            }

            let mut style_attrs = vec![
                format!("family=\"{}\"", font_family),
                format!("size=\"{}\"", font_size),
            ];
            let is_bold = find_descendant(run, W_NS, "b").is_some();
            let is_italic = find_descendant(run, W_NS, "i").is_some();
            style_attrs.push(format!("bold=\"{}\"", if is_bold { "true" } else { "false" }));
            style_attrs.push(format!("italic=\"{}\"", if is_italic { "true" } else { "false" }));

            if let Some(color_el) = find_descendant(run, W_NS, "color") {
                if let Some(color_val) = attr_w(color_el, "val") {
                    if color_val != "auto" && color_val.len() >= 6 {
                        if let (Ok(r), Ok(g), Ok(b)) = (
                            u32::from_str_radix(&color_val[0..2], 16),
                            u32::from_str_radix(&color_val[2..4], 16),
                            u32::from_str_radix(&color_val[4..6], 16),
                        ) {
                            let argb: u32 = (255 << 24) | (r << 16) | (g << 8) | b;
                            let signed_color = argb as i32;
                            style_attrs.push(format!("foreground=\"{}\"", signed_color));
                        }
                    }
                }
            }

            let style_attr_str = style_attrs.join(" ");
            for child in run.children() {
                let name = child.tag_name().name();
                if name == "t" {
                    let txt = child.text().unwrap_or("");
                    let len = txt.chars().count();
                    para_elements.push(format!(
                        "<content startOffset=\"{}\" length=\"{}\" {} />",
                        current_offset, len, style_attr_str
                    ));
                    para_text.push_str(txt);
                    current_offset += len;
                } else if name == "tab" {
                    para_elements.push(format!(
                        "<tab {} startOffset=\"{}\" length=\"1\" />",
                        style_attr_str, current_offset
                    ));
                    para_text.push('\t');
                    current_offset += 1;
                }
            }
        }
    }

    let spacing_el = find_descendant(paragraph, W_NS, "spacing");
    let mut space_before: Option<f64> = None;
    let mut space_after: Option<f64> = None;
    let mut line_spacing: Option<f64> = None;
    if let Some(sp) = spacing_el {
        if let Some(b) = attr_w(sp, "before").and_then(|s| s.parse::<f64>().ok()) {
            space_before = Some(b / 20.0);
        }
        if let Some(a) = attr_w(sp, "after").and_then(|s| s.parse::<f64>().ok()) {
            space_after = Some(a / 20.0);
        }
        if let (Some(l), Some(_lr)) = (
            attr_w(sp, "line").and_then(|s| s.parse::<f64>().ok()),
            attr_w(sp, "lineRule"),
        ) {
            line_spacing = Some(((l / 240.0) * 100.0).round() / 100.0);
        }
    }
    let line_spacing = line_spacing.unwrap_or(1.15);

    let mut alignment = get_alignment(paragraph);
    if para_text.contains('\t') && alignment == "3" {
        alignment = "0";
    }

    let mut paragraph_attrs = format!("Alignment=\"{}\" {}", alignment, get_indent_attrs(paragraph));
    if let Some(sb) = space_before {
        paragraph_attrs.push_str(&format!(" SpaceBefore=\"{}\"", sb));
    }
    if let Some(sa) = space_after {
        paragraph_attrs.push_str(&format!(" SpaceAfter=\"{}\"", sa));
    }
    paragraph_attrs.push_str(&format!(" LineSpacing=\"{}\"", line_spacing));

    if numbered {
        if number_type.starts_with("NUMBER_TYPE_") {
            paragraph_attrs.push_str(&format!(
                " Numbered=\"true\" ListId=\"{}\" ListLevel=\"{}\" NumberType=\"{}\"",
                list_id, list_level, number_type
            ));
        } else {
            paragraph_attrs.push_str(&format!(
                " Bulleted=\"true\" ListId=\"{}\" ListLevel=\"{}\" BulletType=\"{}\"",
                list_id, list_level, number_type
            ));
        }
    }

    let tab_stops_xml = get_tab_stops_xml(paragraph);
    para_text.push('\n');
    para_elements.push(format!(
        "<content startOffset=\"{}\" length=\"1\" family=\"{}\" size=\"{}\" bold=\"false\" italic=\"false\" />",
        current_offset, font_family, font_size
    ));
    current_offset += 1;

    let paragraph_element_xml = format!(
        "<paragraph {}>{}{}</paragraph>",
        paragraph_attrs,
        tab_stops_xml,
        para_elements.join("")
    );
    (para_text, paragraph_element_xml, current_offset)
}

fn process_cell(
    cell: Node,
    rels: &HashMap<String, String>,
    zip: &mut ZipArchive<Cursor<Vec<u8>>>,
    mut current_offset: usize,
) -> (String, Vec<String>, usize) {
    let mut cell_text = String::new();
    let mut cell_elements = Vec::new();
    for p in find_descendants(cell, W_NS, "p") {
        let (pt, px, next_off) = process_paragraph(p, rels, zip, current_offset);
        cell_text.push_str(&pt);
        cell_elements.push(px);
        current_offset = next_off;
    }
    if cell_text.is_empty() {
        cell_text = "\n".to_string();
        cell_elements.push(format!(
            "<paragraph Alignment=\"0\"><content startOffset=\"{}\" length=\"1\" family=\"Times New Roman\" size=\"12\" bold=\"false\" italic=\"false\" /></paragraph>",
            current_offset
        ));
        current_offset += 1;
    }
    (cell_text, cell_elements, current_offset)
}

fn process_table(
    table: Node,
    rels: &HashMap<String, String>,
    zip: &mut ZipArchive<Cursor<Vec<u8>>>,
    mut current_offset: usize,
) -> (String, String, usize) {
    let mut table_text = String::new();
    let mut rows = Vec::new();
    let grid_cols = find_descendants(table, W_NS, "gridCol");
    let column_count = grid_cols.len();

    let column_widths: Vec<i64> = grid_cols
        .iter()
        .map(|col| {
            attr_w(*col, "w")
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(0)
        })
        .collect();
    let mut total_width: i64 = column_widths.iter().sum();
    if total_width == 0 {
        total_width = 1;
    }
    let column_spans: Vec<String> = column_widths
        .iter()
        .map(|&w| {
            let v = (((w as f64) / (total_width as f64)) * 300.0) as i64;
            v.max(1).to_string()
        })
        .collect();
    let column_spans = column_spans.join(",");

    let mut border_type = "borderCell";
    if let Some(tbl_borders) = find_descendant(table, W_NS, "tblBorders") {
        let names = ["top", "left", "bottom", "right", "insideH", "insideV"];
        let all_none = names.iter().all(|bname| {
            match find_descendant(tbl_borders, W_NS, bname) {
                None => true,
                Some(b) => matches!(attr_w(b, "val"), Some("none" | "nil" | "0")),
            }
        });
        if all_none {
            border_type = "borderNone";
        }
    }

    for (row_idx, row) in find_descendants(table, W_NS, "tr").into_iter().enumerate() {
        let mut cells = Vec::new();
        for cell in find_descendants(row, W_NS, "tc") {
            let (ct, celems, next_off) = process_cell(cell, rels, zip, current_offset);
            current_offset = next_off;
            cells.push(format!("<cell>{}</cell>", celems.join("")));
            table_text.push_str(&ct);
        }
        rows.push(format!(
            "<row rowName=\"row{}\" rowType=\"dataRow\">{}</row>",
            row_idx + 1,
            cells.join("")
        ));
    }

    let table_xml = format!(
        "<table tableName=\"Sabit\" columnCount=\"{}\" columnSpans=\"{}\" border=\"{}\">{}</table>",
        column_count,
        column_spans,
        border_type,
        rows.join("")
    );
    (table_text, table_xml, current_offset)
}

fn parse_rels(zip: &mut ZipArchive<Cursor<Vec<u8>>>) -> HashMap<String, String> {
    let mut rels = HashMap::new();
    let Ok(mut f) = zip.by_name("word/_rels/document.xml.rels") else {
        return rels;
    };
    let mut xml = String::new();
    if f.read_to_string(&mut xml).is_err() {
        return rels;
    }
    if let Ok(doc) = Document::parse(&xml) {
        for n in doc.descendants() {
            if n.tag_name().name() == "Relationship" {
                if let (Some(id), Some(target)) = (n.attribute("Id"), n.attribute("Target")) {
                    rels.insert(id.to_string(), target.to_string());
                }
            }
        }
    }
    rels
}

pub fn docx_to_udf_xml(docx_path: &str) -> Result<Vec<u8>, String> {
    let raw = std::fs::read(docx_path).map_err(|e| format!("DOCX okunamadı: {}", e))?;
    let mut zip =
        ZipArchive::new(Cursor::new(raw)).map_err(|e| format!("DOCX ZIP açılamadı: {}", e))?;

    let rels = parse_rels(&mut zip);

    let doc_xml = {
        let mut f = zip
            .by_name("word/document.xml")
            .map_err(|e| format!("word/document.xml bulunamadı: {}", e))?;
        let mut s = String::new();
        f.read_to_string(&mut s)
            .map_err(|e| format!("document.xml okunamadı: {}", e))?;
        s
    };

    let doc = Document::parse(&doc_xml).map_err(|e| format!("XML ayrıştırma hatası: {}", e))?;
    let body = doc
        .descendants()
        .find(|n| n.tag_name().name() == "body")
        .ok_or_else(|| "word/document.xml içinde body bulunamadı".to_string())?;

    let mut content_list = Vec::new();
    let mut elements_list = Vec::new();
    let mut current_offset = 0usize;

    for element in body.children() {
        let name = element.tag_name().name();
        if name == "p" {
            let (pt, px, next_off) = process_paragraph(element, &rels, &mut zip, current_offset);
            current_offset = next_off;
            elements_list.push(px);
            content_list.push(pt);
        } else if name == "tbl" {
            let (tt, tx, next_off) = process_table(element, &rels, &mut zip, current_offset);
            current_offset = next_off;
            elements_list.push(tx);
            content_list.push(tt);
        }
    }

    if content_list.is_empty() {
        content_list.push("\n".to_string());
        elements_list.push(
            "<paragraph Alignment=\"0\"><content startOffset=\"0\" length=\"1\" family=\"Times New Roman\" size=\"12\" bold=\"false\" italic=\"false\" /></paragraph>".to_string(),
        );
    }

    let full_text = content_list.join("");
    let elements_xml = elements_list.join("\n");

    let mut left_margin = 42.525;
    let mut right_margin = 42.525;
    let mut top_margin = 42.525;
    let mut bottom_margin = 42.525;
    if let Some(pg_mar) = find_descendant(body, W_NS, "pgMar") {
        if let Some(v) = attr_w(pg_mar, "left").and_then(|s| s.parse::<f64>().ok()) {
            left_margin = v / 20.0;
        }
        if let Some(v) = attr_w(pg_mar, "right").and_then(|s| s.parse::<f64>().ok()) {
            right_margin = v / 20.0;
        }
        if let Some(v) = attr_w(pg_mar, "top").and_then(|s| s.parse::<f64>().ok()) {
            top_margin = v / 20.0;
        }
        if let Some(v) = attr_w(pg_mar, "bottom").and_then(|s| s.parse::<f64>().ok()) {
            bottom_margin = v / 20.0;
        }
    }

    let xml_template = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" ?> \n\n\
<template format_id=\"1.8\" >\n\
<content><![CDATA[{full_text}]]></content><properties><pageFormat mediaSizeName=\"1\" leftMargin=\"{left_margin}\" rightMargin=\"{right_margin}\" topMargin=\"{top_margin}\" bottomMargin=\"{bottom_margin}\" paperOrientation=\"1\" headerFOffset=\"20.0\" footerFOffset=\"20.0\" /></properties>\n\
<elements resolver=\"hvl-default\" >\n\
{elements_xml}\n\
</elements>\n\
<styles><style name=\"default\" description=\"Geçerli\" family=\"Dialog\" size=\"12\" bold=\"false\" italic=\"false\" FONT_ATTRIBUTE_KEY=\"javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]\" foreground=\"-13421773\" /><style name=\"hvl-default\" family=\"Times New Roman\" size=\"12\" description=\"Gövde\" /></styles>\n\
</template>\n"
    );

    Ok(xml_template.into_bytes())
}

pub fn estimate_alignment(bbox: (f64, f64, f64, f64), page_width: f64) -> i32 {
    let (x0, _y0, x1, _y1) = bbox;
    let mid_block = (x0 + x1) / 2.0;
    let mid_page = page_width / 2.0;
    if (mid_block - mid_page).abs() < 25.0 && (x1 - x0) < page_width * 0.75 {
        return 1;
    }
    if x0 > page_width * 0.4 && (page_width - x1) < 70.0 {
        return 2;
    }
    0
}

fn extract_pdf_text_lines(pdf_bytes: &[u8]) -> Vec<String> {
    let mut lines = Vec::new();
    let mut pos = 0usize;
    while let Some(rel_start) = pdf_bytes[pos..].windows(6).position(|w| w == b"stream") {
        let mut s = pos + rel_start + 6;
        if s < pdf_bytes.len() && pdf_bytes[s] == b'\r' {
            s += 1;
        }
        if s < pdf_bytes.len() && pdf_bytes[s] == b'\n' {
            s += 1;
        }
        let Some(rel_end) = pdf_bytes[s..].windows(9).position(|w| w == b"endstream") else {
            break;
        };
        let raw_stream = &pdf_bytes[s..s + rel_end];
        let trimmed = raw_stream.strip_suffix(b"\r\n").or_else(|| raw_stream.strip_suffix(b"\n")).unwrap_or(raw_stream);
        let decoded = {
            let mut dec = ZlibDecoder::new(trimmed);
            let mut out = Vec::new();
            if dec.read_to_end(&mut out).is_ok() && !out.is_empty() {
                out
            } else {
                trimmed.to_vec()
            }
        };
        let text = String::from_utf8_lossy(&decoded);
        // Extract parenthesized PDF string literals before Tj / TJ
        let bytes = text.as_bytes();
        let mut i = 0;
        let mut cur_line = String::new();
        while i < bytes.len() {
            if bytes[i] == b'(' {
                i += 1;
                let mut lit = Vec::new();
                let mut depth = 1usize;
                while i < bytes.len() && depth > 0 {
                    if bytes[i] == b'\\' && i + 1 < bytes.len() {
                        i += 1;
                        match bytes[i] {
                            b'n' => lit.push(b'\n'),
                            b'r' => lit.push(b'\r'),
                            b't' => lit.push(b'\t'),
                            other => lit.push(other),
                        }
                    } else if bytes[i] == b'(' {
                        depth += 1;
                        lit.push(b'(');
                    } else if bytes[i] == b')' {
                        depth -= 1;
                        if depth > 0 {
                            lit.push(b')');
                        }
                    } else {
                        lit.push(bytes[i]);
                    }
                    i += 1;
                }
                let s_part = String::from_utf8_lossy(&lit);
                cur_line.push_str(&s_part);
            } else {
                if bytes[i..].starts_with(b"Td") || bytes[i..].starts_with(b"TD") || bytes[i..].starts_with(b"T*") {
                    let t = cur_line.trim();
                    if !t.is_empty() {
                        lines.push(t.to_string());
                    }
                    cur_line.clear();
                }
                i += 1;
            }
        }
        let t = cur_line.trim();
        if !t.is_empty() {
            lines.push(t.to_string());
        }
        pos = s + rel_end + 9;
    }
    lines
}

pub fn pdf_to_udf_xml(pdf_path: &str) -> Result<Vec<u8>, String> {
    let raw = std::fs::read(pdf_path).map_err(|e| format!("PDF okunamadı: {}", e))?;
    let extracted_lines = extract_pdf_text_lines(&raw);

    let mut content_list = Vec::new();
    let mut elements_list = Vec::new();
    let mut current_offset = 0usize;

    for line in extracted_lines {
        let len = line.chars().count();
        let mut para_elems = Vec::new();
        para_elems.push(format!(
            "<content startOffset=\"{}\" length=\"{}\" family=\"Times New Roman\" size=\"12\" bold=\"false\" italic=\"false\" />",
            current_offset, len
        ));
        current_offset += len;
        para_elems.push(format!(
            "<content startOffset=\"{}\" length=\"1\" family=\"Times New Roman\" size=\"12\" bold=\"false\" italic=\"false\" />",
            current_offset
        ));
        current_offset += 1;

        content_list.push(format!("{}\n", line));
        elements_list.push(format!(
            "<paragraph Alignment=\"0\" LineSpacing=\"1.15\">{}</paragraph>",
            para_elems.join("")
        ));
    }

    if content_list.is_empty() {
        content_list.push("\n".to_string());
        elements_list.push(
            "<paragraph Alignment=\"0\"><content startOffset=\"0\" length=\"1\" family=\"Times New Roman\" size=\"12\" bold=\"false\" italic=\"false\" /></paragraph>".to_string(),
        );
    }

    let full_text = content_list.join("");
    let elements_xml = elements_list.join("\n");

    let xml_template = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" ?> \n\n\
<template format_id=\"1.8\" >\n\
<content><![CDATA[{full_text}]]></content><properties><pageFormat mediaSizeName=\"1\" leftMargin=\"42.525000000000006\" rightMargin=\"42.525000000000006\" topMargin=\"42.525000000000006\" bottomMargin=\"42.525000000000006\" paperOrientation=\"1\" headerFOffset=\"20.0\" footerFOffset=\"20.0\" /></properties>\n\
<elements resolver=\"hvl-default\" >\n\
{elements_xml}\n\
</elements>\n\
<styles><style name=\"default\" description=\"Geçerli\" family=\"Dialog\" size=\"12\" bold=\"false\" italic=\"false\" FONT_ATTRIBUTE_KEY=\"javax.swing.plaf.FontUIResource[family=Dialog,name=Dialog,style=plain,size=12]\" foreground=\"-13421773\" /><style name=\"hvl-default\" family=\"Times New Roman\" size=\"12\" description=\"Gövde\" /></styles>\n\
</template>\n"
    );

    Ok(xml_template.into_bytes())
}

pub fn convert_to_udf_xml(input_path: &str) -> Result<Vec<u8>, String> {
    let ext = Path::new(input_path)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| format!(".{}", s.to_lowercase()))
        .unwrap_or_default();
    match ext.as_str() {
        ".docx" => docx_to_udf_xml(input_path),
        ".doc" => {
            let docx_temp = doc_to_docx(input_path)?;
            let res = docx_to_udf_xml(&docx_temp);
            let _ = std::fs::remove_file(&docx_temp);
            res
        }
        ".pdf" => pdf_to_udf_xml(input_path),
        _ => Err("Desteklenmeyen dosya uzantısı. Yalnızca .docx, .doc ve .pdf desteklenir.".to_string()),
    }
}
