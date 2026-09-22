//! Text transforms around the Jinja2 evaluation in
//! `DocxTemplate.render_xml_part` and `DocxTemplate.resolve_listing`.

use crate::scan::{find, find_byte, find_in, replace_all, Rewriter};

/// `re.sub(r"<w:p([ >])", r"\n<w:p\1", src)`; `None` when nothing matched.
pub fn pre_render(src: &str) -> Option<String> {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    while let Some(i) = find(h, b"<w:p", pos) {
        pos = i + 4;
        if matches!(h.get(pos), Some(b' ') | Some(b'>')) {
            rw.insert(i).push('\n');
        }
    }
    rw.finish()
}

type Pass<'a> = dyn Fn(&str) -> Option<String> + 'a;

/// Everything `render_xml_part` does after `template.render()`:
/// undo [`pre_render`], unescape `{_{`-style literals, resolve listings.
pub fn post_render(dst: &str) -> Option<String> {
    let mut current: Option<String> = None;
    let passes: [&Pass; 6] = [
        &strip_paragraph_newlines,
        &|s| replace_all(s, "{_{", "{{"),
        &|s| replace_all(s, "}_}", "}}"),
        &|s| replace_all(s, "{_%", "{%"),
        &|s| replace_all(s, "%_}", "%}"),
        &resolve_listing,
    ];
    for pass in passes {
        if let Some(next) = pass(current.as_deref().unwrap_or(dst)) {
            current = Some(next);
        }
    }
    current
}

/// `re.sub(r"\n<w:p([ >])", r"<w:p\1", dst)`
fn strip_paragraph_newlines(dst: &str) -> Option<String> {
    let h = dst.as_bytes();
    let mut rw = Rewriter::new(dst);
    let mut pos = 0;
    while let Some(i) = find(h, b"\n<w:p", pos) {
        pos = i + 5;
        if matches!(h.get(pos), Some(b' ') | Some(b'>')) {
            rw.rewrite(i, i + 1);
        }
    }
    rw.finish()
}

#[inline]
fn is_listing_char(b: u8) -> bool {
    matches!(b, b'\t' | b'\n' | 0x07 | 0x0C)
}

/// `<NAME(?: [^>]*)?>.*?</NAME>` at or after `from`, inside `h[..to]`:
/// returns `(start, end)`. `None` also means no later match exists.
fn next_element(
    h: &[u8],
    mut from: usize,
    to: usize,
    open: &[u8],
    close: &[u8],
) -> Option<(usize, usize)> {
    loop {
        let start = find_in(h, open, from, to)?;
        from = start + 1;
        let tag_end = match h.get(start + open.len()) {
            Some(b'>') if start + open.len() < to => start + open.len() + 1,
            Some(b' ') => match find_byte(h, b'>', start + open.len() + 1) {
                Some(gt) if gt < to => gt + 1,
                _ => return None,
            },
            _ => continue,
        };
        let end = find_in(h, close, tag_end, to)?;
        return Some((start, end + close.len()));
    }
}

/// `re.search(r"<w:xPr>.*?</w:xPr>", text)` or `""`. This search has no
/// DOTALL flag: a candidate whose content spans a newline is skipped.
fn properties<'a>(text: &'a str, open: &[u8], close: &[u8]) -> &'a str {
    let h = text.as_bytes();
    let mut from = 0;
    while let Some(s) = find(h, open, from) {
        from = s + 1;
        let body = s + open.len();
        let Some(e) = find(h, close, body) else { break };
        if !h[body..e].contains(&b'\n') {
            return &text[s..e + close.len()];
        }
    }
    ""
}

/// `DocxTemplate.resolve_listing`: tab, bell (new paragraph), newline and
/// form feed inside `<w:t>` become WordprocessingML, scoped by the same
/// nested lazy paragraph / run / text matches as the original.
fn resolve_listing(xml: &str) -> Option<String> {
    let h = xml.as_bytes();
    if !h.iter().any(|&b| is_listing_char(b)) {
        return None;
    }
    let mut rw = Rewriter::new(xml);
    let mut p_from = 0;
    while let Some((p_start, p_end)) = next_element(h, p_from, h.len(), b"<w:p", b"</w:p>") {
        p_from = p_end;
        if !h[p_start..p_end].iter().any(|&b| is_listing_char(b)) {
            continue;
        }
        let mut p_props: Option<&str> = None;
        let mut r_from = p_start;
        while let Some((r_start, r_end)) = next_element(h, r_from, p_end, b"<w:r", b"</w:r>") {
            r_from = r_end;
            let mut r_props: Option<&str> = None;
            let mut t_from = r_start;
            while let Some((t_start, t_end)) = next_element(h, t_from, r_end, b"<w:t", b"</w:t>") {
                t_from = t_end;
                if !h[t_start..t_end].iter().any(|&b| is_listing_char(b)) {
                    continue;
                }
                let pp = *p_props.get_or_insert_with(|| {
                    properties(&xml[p_start..p_end], b"<w:pPr>", b"</w:pPr>")
                });
                let rp = *r_props.get_or_insert_with(|| {
                    properties(&xml[r_start..r_end], b"<w:rPr>", b"</w:rPr>")
                });
                rw.rewrite(t_start, t_end)
                    .push_str(&resolve_text(&xml[t_start..t_end], rp, pp));
            }
        }
    }
    rw.finish()
}

/// The four chained `str.replace` calls, in the original order (a later
/// replacement also sees text inserted by an earlier one).
fn resolve_text(text: &str, rp: &str, pp: &str) -> String {
    let tab = format!("</w:t></w:r><w:r>{rp}<w:tab/></w:r><w:r>{rp}<w:t xml:space=\"preserve\">");
    let bell = format!("</w:t></w:r></w:p><w:p>{pp}<w:r>{rp}<w:t xml:space=\"preserve\">");
    let feed = format!(
        "</w:t></w:r></w:p><w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>\
         <w:p>{pp}<w:r>{rp}<w:t xml:space=\"preserve\">"
    );
    let steps: [(&str, &str); 4] = [
        ("\t", &tab),
        ("\u{07}", &bell),
        ("\n", "</w:t><w:br/><w:t xml:space=\"preserve\">"),
        ("\u{0C}", &feed),
    ];
    let mut current = text.to_owned();
    for (needle, with) in steps {
        if let Some(next) = replace_all(&current, needle, with) {
            current = next;
        }
    }
    current
}
