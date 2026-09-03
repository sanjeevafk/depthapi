use anydoc::Format;
use std::path::Path;

pub struct ParseResult {
    pub markdown: String,
    pub format: String,
    pub confidence: f64,
    pub warnings: Vec<String>,
}

/// Detect format from filename or content.
fn detect_format(bytes: &[u8], filename_or_ext: Option<&str>) -> (Option<Format>, String) {
    if let Some(name) = filename_or_ext {
        let path = Path::new(name);
        if let Some(ext) = path.extension().and_then(|e| e.to_str()).map(|s| s.to_lowercase()) {
            match ext.as_str() {
                "md" | "markdown" => return (None, "markdown".to_string()),
                "txt" | "text" => return (None, "text".to_string()),
                "html" | "htm" => return (None, "html".to_string()),
                _ => {
                    if let Some(fmt) = Format::from_extension(&ext) {
                        return (Some(fmt), ext);
                    }
                }
            }
        }
    }

    if let Some(fmt) = Format::from_bytes(bytes) {
        let name = match fmt {
            Format::Doc => "doc",
            Format::Docx => "docx",
            Format::Excel => "xlsx",
            Format::Ppt => "ppt",
            Format::Pptx => "pptx",
            Format::Pdf => "pdf",
            Format::Odt => "odt",
            Format::Ods => "ods",
            Format::Odp => "odp",
            Format::Rtf => "rtf",
            Format::Epub => "epub",
            Format::Csv => "csv",
        };

        return (Some(fmt), name.to_string());
    }

    // Default to markdown/text if valid UTF-8
    if std::str::from_utf8(bytes).is_ok() {
        (None, "text".to_string())
    } else {
        (None, "binary_unknown".to_string())
    }
}

/// Convert HTML content to clean Markdown/text.
fn html_to_markdown(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut in_tag = false;
    let mut current_tag = String::new();
    let mut in_skip = false;
    let mut skip_tag = String::new();

    let chars: Vec<char> = html.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '<' {
            in_tag = true;
            current_tag.clear();
        } else if c == '>' && in_tag {
            in_tag = false;
            let tag_lower = current_tag.to_lowercase();
            let tag_name = tag_lower.split_whitespace().next().unwrap_or("");

            if in_skip {
                if tag_name == format!("/{}", skip_tag) {
                    in_skip = false;
                    skip_tag.clear();
                }
            } else if tag_name == "script" || tag_name == "style" {
                in_skip = true;
                skip_tag = tag_name.to_string();
            } else if tag_name.starts_with('h') && tag_name.len() == 2 {
                if let Ok(level) = tag_name[1..].parse::<usize>() {
                    out.push('\n');
                    for _ in 0..level {
                        out.push('#');
                    }
                    out.push(' ');
                }
            } else if tag_name == "/p" || tag_name == "br" || tag_name == "br/" || tag_name == "/div" || tag_name == "/li" {
                out.push('\n');
            } else if tag_name == "li" {
                out.push_str("- ");
            }
        } else if in_tag {
            current_tag.push(c);
        } else if !in_skip {
            out.push(c);
        }
        i += 1;
    }

    // Replace common entities
    out.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
}

/// Extract printable ASCII / UTF-8 strings from binary as fallback.
fn extract_printable_strings(bytes: &[u8]) -> String {
    let mut result = String::new();
    let mut current = String::new();

    for &b in bytes {
        if b.is_ascii_graphic() || b == b' ' || b == b'\t' || b == b'\n' {
            current.push(b as char);
        } else {
            if current.len() >= 6 {
                result.push_str(&current);
                result.push('\n');
            }
            current.clear();
        }
    }
    if current.len() >= 6 {
        result.push_str(&current);
    }
    result
}

/// Parse arbitrary document bytes to Markdown.
pub fn parse_to_markdown(
    bytes: &[u8],
    filename_or_ext: Option<&str>,
    _mime_type: Option<&str>,
) -> Result<ParseResult, String> {
    if bytes.is_empty() {
        return Err("Cannot parse empty document (0 bytes)".to_string());
    }

    let (format, fmt_name) = detect_format(bytes, filename_or_ext);

    // 1. Direct Markdown or Text
    if fmt_name == "markdown" || fmt_name == "text" {
        let content = String::from_utf8_lossy(bytes).to_string();
        return Ok(ParseResult {
            markdown: content,
            format: fmt_name,
            confidence: 1.0,
            warnings: Vec::new(),
        });
    }

    // 2. HTML
    if fmt_name == "html" {
        let raw_str = String::from_utf8_lossy(bytes);
        let md = html_to_markdown(&raw_str);
        return Ok(ParseResult {
            markdown: md,
            format: "html".to_string(),
            confidence: 0.95,
            warnings: Vec::new(),
        });
    }

    // 3. Office & PDF via anydoc
    if let Some(anydoc_fmt) = format {
        match anydoc::to_markdown_bytes(bytes, anydoc_fmt) {
            Ok(md) => {
                return Ok(ParseResult {
                    markdown: md,
                    format: fmt_name,
                    confidence: 0.98,
                    warnings: Vec::new(),
                });
            }
            Err(err) => {
                // Soft-fail: attempt string salvage
                let fallback = extract_printable_strings(bytes);
                if fallback.trim().len() > 30 {
                    return Ok(ParseResult {
                        markdown: fallback,
                        format: fmt_name,
                        confidence: 0.4,
                        warnings: vec![format!("AnyDoc conversion failed ({err}); extracted raw text strings as fallback.")],
                    });
                } else {
                    return Err(format!("AnyDoc conversion failed for {fmt_name}: {err}"));
                }
            }
        }
    }

    // 4. Binary unknown: attempt string extraction or fail
    let fallback = extract_printable_strings(bytes);
    if fallback.trim().len() > 30 {
        Ok(ParseResult {
            markdown: fallback,
            format: "unknown_salvaged".to_string(),
            confidence: 0.3,
            warnings: vec!["Unrecognized document binary; salvaged printable strings.".to_string()],
        })
    } else {
        Err(format!("Unsupported or unrecognized binary format: {fmt_name}"))
    }
}
