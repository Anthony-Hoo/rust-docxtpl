//! `DocxTemplate.patch_xml` of docxtpl 0.20.1, pass by pass.
//!
//! Each pass quotes the original Python regex and then states the matching
//! rule the scanner implements. Two regex shapes recur:
//!
//! * **tempered greedy** `START(?:(?!START).)*TAG.*?CLOSER` — the greedy
//!   tempered dot runs up to the next `START`, then backtracks, so the match
//!   uses the *last* `TAG` of that segment for which a `CLOSER` exists later.
//! * **tempered lazy** `START(?:(?!START).)*?TAG.*?CLOSER` — same, but the
//!   *first* `TAG` of the segment.
//!
//! `re.sub` never rescans its own output and resumes after each match, which
//! is why every pass tracks a single forward `pos`.

use crate::scan::{
    find, find_byte, find_in, rfind_in, skip_py_space, starts_with_at, ElemStart, Rewriter,
};
use crate::Unsupported;

type PassResult = Result<Option<String>, Unsupported>;

/// Byte-exact equivalent of `DocxTemplate.patch_xml(src)`.
pub fn patch_xml(src: &str) -> Result<String, Unsupported> {
    const PASSES: &[fn(&str) -> PassResult] = &[
        strip_tags_inside_delimiters,
        merge_split_tags,
        |s| rewrite_cell_tag(s, &COLSPAN),
        |s| rewrite_cell_tag(s, &CELLBG),
        preserve_space,
        isolate_run_tags,
        merge_with_previous_text,
        merge_with_next_text,
        |s| unwrap_structural(s, &TR, StructKind::Tag),
        |s| unwrap_structural(s, &TC, StructKind::Tag),
        |s| unwrap_structural(s, &P, StructKind::Tag),
        |s| unwrap_structural(s, &R, StructKind::Tag),
        |s| unwrap_structural(s, &TR, StructKind::Comment),
        |s| unwrap_structural(s, &TC, StructKind::Comment),
        |s| unwrap_structural(s, &P, StructKind::Comment),
        |s| merge_cells(s, Merge::Vertical),
        |s| merge_cells(s, Merge::Horizontal),
        clean_tags,
    ];
    let mut current: Option<String> = None;
    for pass in PASSES {
        if let Some(next) = pass(current.as_deref().unwrap_or(src))? {
            current = Some(next);
        }
    }
    Ok(current.unwrap_or_else(|| src.to_owned()))
}

// ---------------------------------------------------------------------------
// Generic tempered-greedy substitution
// ---------------------------------------------------------------------------

/// A template tag located by a pass-specific finder; `g0..g1` is its payload.
#[derive(Clone, Copy)]
struct Tag {
    start: usize,
    end: usize,
    g0: usize,
    g1: usize,
}

/// Writes the replacement for one match: `(out, match_start, tag, match_end)`.
type Emit<'a> = dyn FnMut(&mut String, usize, &Tag, usize) -> Result<(), Unsupported> + 'a;

/// `re.sub` for `START(?:(?!START).)*TAG.*?CLOSER` (`CLOSER` may be empty).
///
/// `next_tag(from)` returns the first `TAG` beginning at or after `from`.
/// `emit(out, start, tag, match_end)` writes the replacement of
/// `src[start..match_end]`.
fn tempered_greedy_sub(
    src: &str,
    start: &ElemStart,
    next_tag: &dyn Fn(usize) -> Option<Tag>,
    closer: &[u8],
    emit: &mut Emit,
) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    // Once a closer search fails at offset x it fails for every offset >= x.
    let mut closer_absent_from = usize::MAX;
    let mut candidates: Vec<Tag> = Vec::new();
    while let Some(first) = next_tag(pos) {
        // Earlier starts have tag-free segments and fail; the last start
        // before the tag owns the segment the tag lives in.
        let Some(s) = start.rfind(h, pos, first.start) else {
            pos = first.start + 1;
            continue;
        };
        let limit = start.find(h, first.start + 1).unwrap_or(h.len());
        candidates.clear();
        candidates.push(first);
        let mut from = first.start + 1;
        while let Some(tag) = next_tag(from) {
            if tag.start >= limit {
                break;
            }
            from = tag.start + 1;
            candidates.push(tag);
        }
        let mut matched = false;
        for tag in candidates.iter().rev() {
            let match_end = if closer.is_empty() {
                tag.end
            } else if tag.end >= closer_absent_from {
                continue;
            } else {
                match find(h, closer, tag.end) {
                    Some(c) => c + closer.len(),
                    None => {
                        closer_absent_from = tag.end;
                        continue;
                    }
                }
            };
            emit(rw.rewrite(s, match_end), s, tag, match_end)?;
            pos = match_end;
            matched = true;
            break;
        }
        if !matched {
            pos = limit;
        }
    }
    Ok(rw.finish())
}

// ---------------------------------------------------------------------------
// Pass 1
// ---------------------------------------------------------------------------

/// `(?<={)(<[^>]*>)+(?=[\{%\#])|(?<=[%\}\#])(<[^>]*>)+(?=\})` -> `""`
///
/// `<[^>]*>` is deterministic (up to the next `>`), and inside a run of such
/// tags every tag but the last is followed by `<`, so only the maximal run
/// can satisfy the lookahead.
fn strip_tags_inside_delimiters(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 1;
    while let Some(i) = find_byte(h, b'<', pos) {
        let allowed_next: &[u8] = match h[i - 1] {
            b'{' => b"{%#",
            b'%' | b'}' | b'#' => b"}",
            _ => {
                pos = i + 1;
                continue;
            }
        };
        let mut end = i;
        while h.get(end) == Some(&b'<') {
            match find_byte(h, b'>', end + 1) {
                Some(gt) => end = gt + 1,
                None => break,
            }
        }
        if end > i && h.get(end).is_some_and(|c| allowed_next.contains(c)) {
            rw.rewrite(i, end);
            pos = end;
        } else {
            pos = i + 1;
        }
    }
    Ok(rw.finish())
}

// ---------------------------------------------------------------------------
// Pass 2
// ---------------------------------------------------------------------------

/// `{%(?:(?!%}).)*|{#(?:(?!#}).)*|{{(?:(?!}}).)*` with, inside each match,
/// `</w:t>.*?(<w:t>|<w:t [^>]*>)` -> `""`.
///
/// A match spans from the opener to just before its closer (or to the end of
/// the input when unclosed); the inner substitution never looks past it.
fn merge_split_tags(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    while let Some(i) = find_byte(h, b'{', pos) {
        let closer: &[u8] = match h.get(i + 1) {
            Some(b'%') => b"%}",
            Some(b'#') => b"#}",
            Some(b'{') => b"}}",
            _ => {
                pos = i + 1;
                continue;
            }
        };
        let end = find(h, closer, i + 2).unwrap_or(h.len());
        let mut p = i;
        'inner: while let Some(a) = find_in(h, b"</w:t>", p, end) {
            let mut q_from = a + 6;
            let cut_end = loop {
                let Some(q) = find_in(h, b"<w:t", q_from, end) else {
                    break 'inner;
                };
                match h.get(q + 4) {
                    Some(b'>') if q + 5 <= end => break q + 5,
                    Some(b' ') => match find_byte(h, b'>', q + 5) {
                        Some(gt) if gt < end => break gt + 1,
                        // No `>` left in the match: no later attempt can succeed.
                        _ => break 'inner,
                    },
                    _ => q_from = q + 1,
                }
            };
            rw.rewrite(a, cut_end);
            p = cut_end;
        }
        pos = end;
    }
    Ok(rw.finish())
}

// ---------------------------------------------------------------------------
// Passes 3 and 4: {% colspan x %} / {% cellbg x %}
// ---------------------------------------------------------------------------

struct CellTag {
    name: &'static [u8],
    /// Element removed once (`<w:gridSpan[^/]*/>`).
    drop_prefix: &'static [u8],
    before: &'static str,
    after: &'static str,
}

const COLSPAN: CellTag = CellTag {
    name: b"colspan",
    drop_prefix: b"<w:gridSpan",
    before: "<w:gridSpan w:val=\"{{",
    after: "}}\"/>",
};

const CELLBG: CellTag = CellTag {
    name: b"cellbg",
    drop_prefix: b"<w:shd",
    before: "<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"{{",
    after: "}}\"/>",
};

const TC_START: ElemStart = ElemStart::open("<w:tc");
const RUN_START: ElemStart = ElemStart::open("<w:r");

/// `{%\s*NAME\s+([^%]*)\s*%}`: `[^%]*` stops at the first `%`, which must
/// open `%}`; the trailing `\s*` therefore always matches empty and the group
/// keeps its trailing whitespace.
fn next_cell_tag(src: &str, mut from: usize, name: &[u8]) -> Option<Tag> {
    let h = src.as_bytes();
    while let Some(i) = find(h, b"{%", from) {
        from = i + 1;
        let j = skip_py_space(src, i + 2);
        if !starts_with_at(h, j, name) {
            continue;
        }
        let g0 = skip_py_space(src, j + name.len());
        if g0 == j + name.len() {
            continue;
        }
        if let Some(pc) = find_byte(h, b'%', g0) {
            if h.get(pc + 1) == Some(&b'}') {
                return Some(Tag {
                    start: i,
                    end: pc + 2,
                    g0,
                    g1: pc,
                });
            }
        }
    }
    None
}

/// `(<w:tc[ >](?:(?!<w:tc[ >]).)*){%\s*NAME\s+([^%]*)\s*%}(.*?</w:tc>)`
fn rewrite_cell_tag(src: &str, spec: &CellTag) -> PassResult {
    if find(src.as_bytes(), spec.name, 0).is_none() {
        return Ok(None);
    }
    tempered_greedy_sub(
        src,
        &TC_START,
        &|from| next_cell_tag(src, from, spec.name),
        b"</w:tc>",
        &mut |out, s, tag, match_end| {
            let expr = &src[tag.g0..tag.g1];
            if expr.contains('\\') {
                // The original interpolates the expression into a regex
                // replacement template, where backslashes are escapes.
                return Err(Unsupported("backslash in colspan/cellbg expression"));
            }
            let mut cell = String::with_capacity(match_end - s);
            cell.push_str(&src[s..tag.start]);
            cell.push_str(&src[tag.end..match_end]);
            let cell = drop_empty_text_runs(&cell)?.unwrap_or(cell);
            let cell = drop_first_element(&cell, spec.drop_prefix).unwrap_or(cell);
            // `(<w:tcPr[^>]*>)` -> `\1` + new element
            let ch = cell.as_bytes();
            let mut copied = 0;
            let mut p = 0;
            while let Some(x) = find(ch, b"<w:tcPr", p) {
                let Some(gt) = find_byte(ch, b'>', x + 7) else {
                    break;
                };
                out.push_str(&cell[copied..gt + 1]);
                out.push_str(spec.before);
                out.push_str(expr);
                out.push_str(spec.after);
                copied = gt + 1;
                p = gt + 1;
            }
            out.push_str(&cell[copied..]);
            Ok(())
        },
    )
}

/// `<w:r[ >](?:(?!<w:r[ >]).)*<w:t></w:t>.*?</w:r>` -> `""`
fn drop_empty_text_runs(cell: &str) -> PassResult {
    const EMPTY: &[u8] = b"<w:t></w:t>";
    let h = cell.as_bytes();
    tempered_greedy_sub(
        cell,
        &RUN_START,
        &|from| {
            find(h, EMPTY, from).map(|i| Tag {
                start: i,
                end: i + EMPTY.len(),
                g0: i,
                g1: i,
            })
        },
        b"</w:r>",
        &mut |_, _, _, _| Ok(()),
    )
}

/// `re.sub(PREFIX + r"[^/]*/>", "", cell, count=1)`
fn drop_first_element(cell: &str, prefix: &[u8]) -> Option<String> {
    let h = cell.as_bytes();
    let mut from = 0;
    while let Some(x) = find(h, prefix, from) {
        let slash = find_byte(h, b'/', x + prefix.len())?;
        if h.get(slash + 1) == Some(&b'>') {
            let mut out = String::with_capacity(cell.len());
            out.push_str(&cell[..x]);
            out.push_str(&cell[slash + 2..]);
            return Some(out);
        }
        from = x + 1;
    }
    None
}

// ---------------------------------------------------------------------------
// Pass 5
// ---------------------------------------------------------------------------

/// `<w:t>((?:(?!<w:t>).)*)({{.*?}}|{%.*?%})` -> `<w:t xml:space="preserve">\1\2`
///
/// The closing delimiter is searched without any bound (DOTALL `.*?`).
fn preserve_space(src: &str) -> PassResult {
    const TEXT_START: ElemStart = ElemStart::literal("<w:t>");
    let h = src.as_bytes();
    let last_var_close = rfind_in(h, b"}}", 0, h.len());
    let last_block_close = rfind_in(h, b"%}", 0, h.len());
    let next_tag = |mut from: usize| -> Option<Tag> {
        while let Some(i) = find_byte(h, b'{', from) {
            from = i + 1;
            let (closer, last): (&[u8], _) = match h.get(i + 1) {
                Some(b'{') => (b"}}", last_var_close),
                Some(b'%') => (b"%}", last_block_close),
                _ => continue,
            };
            if last.is_some_and(|l| l >= i + 2) {
                let c = find(h, closer, i + 2).expect("closer exists");
                return Some(Tag {
                    start: i,
                    end: c + 2,
                    g0: i,
                    g1: i,
                });
            }
        }
        None
    };
    tempered_greedy_sub(
        src,
        &TEXT_START,
        &next_tag,
        b"",
        &mut |out, s, _, match_end| {
            out.push_str("<w:t xml:space=\"preserve\">");
            out.push_str(&src[s + 5..match_end]);
            Ok(())
        },
    )
}

// ---------------------------------------------------------------------------
// Pass 6
// ---------------------------------------------------------------------------

/// `({{r\s.*?}}|{%r\s.*?%})` -> own run with preserved space.
fn isolate_run_tags(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    while let Some(i) = find_byte(h, b'{', pos) {
        pos = i + 1;
        let closer: &[u8] = match h.get(i + 1) {
            Some(b'{') => b"}}",
            Some(b'%') => b"%}",
            _ => continue,
        };
        if h.get(i + 2) != Some(&b'r') {
            continue;
        }
        let after_space = skip_one_py_space(src, i + 3);
        if after_space == i + 3 {
            continue;
        }
        let Some(c) = find(h, closer, after_space) else {
            continue;
        };
        let out = rw.rewrite(i, c + 2);
        out.push_str("</w:t></w:r><w:r><w:t xml:space=\"preserve\">");
        out.push_str(&src[i..c + 2]);
        out.push_str("</w:t></w:r><w:r><w:t xml:space=\"preserve\">");
        pos = c + 2;
    }
    Ok(rw.finish())
}

/// A single `\s`.
fn skip_one_py_space(s: &str, i: usize) -> usize {
    match s[i..].chars().next() {
        Some(c) if crate::scan::is_py_space(c) => i + c.len_utf8(),
        _ => i,
    }
}

// ---------------------------------------------------------------------------
// Passes 7 and 8: whitespace control across paragraphs
// ---------------------------------------------------------------------------

/// `</w:t>(?:(?!</w:t>).)*?{%-` -> `{%`
///
/// Only the last `</w:t>` before a `{%-` has a `</w:t>`-free path to it.
fn merge_with_previous_text(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    let mut from = 0;
    while let Some(p) = find(h, b"{%-", from) {
        from = p + 1;
        if let Some(a) = rfind_in(h, b"</w:t>", pos, p) {
            rw.rewrite(a, p + 3).push_str("{%");
            pos = p + 3;
            from = pos;
        }
    }
    Ok(rw.finish())
}

/// `-%}(?:(?!<w:t[ >]|{%|{{).)*?<w:t[^>]*?>` -> `%}`
///
/// Note that `<w:t[^>]*?>` accepts any tag whose name starts with `w:t`.
fn merge_with_next_text(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut from = 0;
    'starts: while let Some(a) = find(h, b"-%}", from) {
        from = a + 1;
        let mut q = a + 3;
        while let Some(i) = memchr::memchr2(b'<', b'{', &h[q..]).map(|i| i + q) {
            q = i + 1;
            if h[i] == b'{' {
                if matches!(h.get(i + 1), Some(b'%') | Some(b'{')) {
                    continue 'starts;
                }
            } else if starts_with_at(h, i, b"<w:t") {
                match find_byte(h, b'>', i + 4) {
                    Some(gt) => {
                        rw.rewrite(a, gt + 1).push_str("%}");
                        from = gt + 1;
                    }
                    // Unterminated tag: nothing after it can match either.
                    None => break 'starts,
                }
                continue 'starts;
            }
        }
        break;
    }
    Ok(rw.finish())
}

// ---------------------------------------------------------------------------
// Passes 9 and 10: {%tr %} {%tc %} {%p %} {%r %} and {#tr #} {#tc #} {#p #}
// ---------------------------------------------------------------------------

struct Structural {
    name: &'static [u8],
    start: ElemStart,
    closer: &'static [u8],
}

const TR: Structural = Structural {
    name: b"tr",
    start: ElemStart::open("<w:tr"),
    closer: b"</w:tr>",
};
const TC: Structural = Structural {
    name: b"tc",
    start: ElemStart::open("<w:tc"),
    closer: b"</w:tc>",
};
const P: Structural = Structural {
    name: b"p",
    start: ElemStart::open("<w:p"),
    closer: b"</w:p>",
};
const R: Structural = Structural {
    name: b"r",
    start: ElemStart::open("<w:r"),
    closer: b"</w:r>",
};

#[derive(Clone, Copy, PartialEq)]
enum StructKind {
    /// `({%|{{)y ([^}%]*(?:%}|}}))`
    Tag,
    /// `({#)y ([^}#]*(?:#}))`
    Comment,
}

/// The negated class stops at its first excluded character, which must then
/// begin one of the accepted closers.
fn next_structural_tag(h: &[u8], mut from: usize, name: &[u8], kind: StructKind) -> Option<Tag> {
    while let Some(i) = find_byte(h, b'{', from) {
        from = i + 1;
        let opener_ok = matches!(
            (kind, h.get(i + 1)),
            (StructKind::Tag, Some(b'%') | Some(b'{')) | (StructKind::Comment, Some(b'#'))
        );
        let g0 = i + 2 + name.len() + 1;
        if !opener_ok || !starts_with_at(h, i + 2, name) || h.get(g0 - 1) != Some(&b' ') {
            continue;
        }
        let other = if kind == StructKind::Tag { b'%' } else { b'#' };
        let Some(stop) = memchr::memchr2(b'}', other, &h[g0..]).map(|x| x + g0) else {
            continue;
        };
        let closes = h.get(stop + 1) == Some(&b'}')
            && (h[stop] == other || (kind == StructKind::Tag && h[stop] == b'}'));
        if closes {
            return Some(Tag {
                start: i,
                end: stop + 2,
                g0,
                g1: stop + 2,
            });
        }
    }
    None
}

/// `<w:y[ >](?:(?!<w:y[ >]).)*(OPEN)y (BODY).*?</w:y>` -> `\1 \2`
fn unwrap_structural(src: &str, spec: &Structural, kind: StructKind) -> PassResult {
    let h = src.as_bytes();
    tempered_greedy_sub(
        src,
        &spec.start,
        &|from| next_structural_tag(h, from, spec.name, kind),
        spec.closer,
        &mut |out, _, tag, _| {
            out.push_str(&src[tag.start..tag.start + 2]);
            out.push(' ');
            out.push_str(&src[tag.g0..tag.g1]);
            Ok(())
        },
    )
}

// ---------------------------------------------------------------------------
// Passes 11 and 12: {% vm %} / {% hm %}
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
enum Merge {
    Vertical,
    Horizontal,
}

impl Merge {
    fn name(self) -> &'static [u8] {
        if self == Merge::Vertical {
            b"vm"
        } else {
            b"hm"
        }
    }
}

/// `{%\s*NAME\s*%}` -> `(start, end)`
fn next_merge_tag(s: &str, mut from: usize, name: &[u8]) -> Option<(usize, usize)> {
    let h = s.as_bytes();
    while let Some(i) = find(h, b"{%", from) {
        from = i + 1;
        let j = skip_py_space(s, i + 2);
        if starts_with_at(h, j, name) {
            let k = skip_py_space(s, j + name.len());
            if starts_with_at(h, k, b"%}") {
                return Some((i, k + 2));
            }
        }
    }
    None
}

/// `</w:tc[ >]`
fn find_tc_close(h: &[u8], mut from: usize) -> Option<usize> {
    while let Some(c) = find(h, b"</w:tc", from) {
        if matches!(h.get(c + 6), Some(b' ') | Some(b'>')) {
            return Some(c + 7);
        }
        from = c + 1;
    }
    None
}

/// `<w:tc[ >](?:(?!<w:tc[ >]).)*?{%\s*NAME\s*%}.*?</w:tc[ >]`
fn merge_cells(src: &str, merge: Merge) -> PassResult {
    let h = src.as_bytes();
    let name = merge.name();
    if find(h, name, 0).is_none() {
        return Ok(None);
    }
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    let mut from = 0;
    while let Some((tag_start, tag_end)) = next_merge_tag(src, from, name) {
        from = tag_start + 1;
        let Some(s) = TC_START.rfind(h, pos, tag_start) else {
            continue;
        };
        // No closer after this tag means none after any later tag.
        let Some(match_end) = find_tc_close(h, tag_end) else {
            break;
        };
        let cell = &src[s..match_end];
        let out = rw.rewrite(s, match_end);
        match merge {
            Merge::Vertical => v_merge(cell, out),
            Merge::Horizontal => h_merge(cell, out)?,
        }
        pos = match_end;
        from = match_end;
    }
    Ok(rw.finish())
}

/// Offsets of `(</w:tcPr[ >].*?<w:t(?:.*?)>)(.*?)(?:{%\s*NAME\s*%})(.*?)(</w:t>)`
/// at or after `from`. Every piece is lazy and the constraints only tighten
/// for later choices, so the first candidate of each piece decides success.
struct MergeParts {
    tcpr_close: usize,
    text_open_end: usize,
    tag_start: usize,
    tag_end: usize,
    text_close: usize,
}

fn find_merge_parts(cell: &str, from: usize, name: &[u8]) -> Option<MergeParts> {
    let h = cell.as_bytes();
    let mut a_from = from;
    let tcpr_close = loop {
        let a = find(h, b"</w:tcPr", a_from)?;
        if matches!(h.get(a + 8), Some(b' ') | Some(b'>')) {
            break a;
        }
        a_from = a + 1;
    };
    let text_open = find(h, b"<w:t", tcpr_close + 9)?;
    let text_open_end = find_byte(h, b'>', text_open + 4)? + 1;
    let (tag_start, tag_end) = next_merge_tag(cell, text_open_end, name)?;
    let text_close = find(h, b"</w:t>", tag_end)?;
    Some(MergeParts {
        tcpr_close,
        text_open_end,
        tag_start,
        tag_end,
        text_close,
    })
}

fn v_merge(cell: &str, out: &mut String) {
    let mut copied = 0;
    while let Some(m) = find_merge_parts(cell, copied, b"vm") {
        out.push_str(&cell[copied..m.tcpr_close]);
        out.push_str(
            "<w:vMerge w:val=\"{% if loop.first %}restart{% else %}continue{% endif %}\"/>",
        );
        out.push_str(&cell[m.tcpr_close..m.text_open_end]);
        out.push_str("{% if loop.first %}");
        out.push_str(&cell[m.text_open_end..m.tag_start]);
        out.push_str(&cell[m.tag_end..m.text_close]);
        out.push_str("{% endif %}</w:t>");
        copied = m.text_close + 6;
    }
    out.push_str(&cell[copied..]);
}

fn h_merge(cell: &str, out: &mut String) -> Result<(), Unsupported> {
    const SPAN: &[u8] = b"w:gridSpan w:val=\"";
    let h = cell.as_bytes();
    out.push_str("{% if loop.first %}");
    if find(h, b"w:gridSpan", 0).is_some() {
        // `(w:gridSpan w:val=")(\d+)(")` -> `\1{{ \2 * loop.length }}\3`
        let mut scaled = String::with_capacity(cell.len() + 32);
        let mut copied = 0;
        let mut from = 0;
        while let Some(x) = find(h, SPAN, from) {
            from = x + 1;
            let d0 = x + SPAN.len();
            let d1 = d0 + h[d0..].iter().take_while(|b| b.is_ascii_digit()).count();
            match h.get(d1) {
                Some(b'"') if d1 > d0 => {
                    scaled.push_str(&cell[copied..d0]);
                    scaled.push_str("{{ ");
                    scaled.push_str(&cell[d0..d1]);
                    scaled.push_str(" * loop.length }}");
                    copied = d1;
                    from = d1 + 1;
                }
                // Python's `\d` also accepts non-ASCII decimal digits.
                Some(b) if !b.is_ascii() => return Err(Unsupported("non-ASCII gridSpan value")),
                _ => {}
            }
        }
        scaled.push_str(&cell[copied..]);
        // `{%\s*hm\s*%}` -> `""`
        let mut copied = 0;
        while let Some((tag_start, tag_end)) = next_merge_tag(&scaled, copied, b"hm") {
            out.push_str(&scaled[copied..tag_start]);
            copied = tag_end;
        }
        out.push_str(&scaled[copied..]);
    } else {
        let mut copied = 0;
        while let Some(m) = find_merge_parts(cell, copied, b"hm") {
            out.push_str(&cell[copied..m.tcpr_close]);
            out.push_str("<w:gridSpan w:val=\"{{ loop.length }}\"/>");
            out.push_str(&cell[m.tcpr_close..m.tag_start]);
            out.push_str(&cell[m.tag_end..m.text_close + 6]);
            copied = m.text_close + 6;
        }
        out.push_str(&cell[copied..]);
    }
    out.push_str("{% endif %}");
    Ok(())
}

// ---------------------------------------------------------------------------
// Pass 13
// ---------------------------------------------------------------------------

/// `(?<=\{[\{%])(.*?)(?=[\}%]})` (no DOTALL) with entity/quote clean-up.
///
/// Mirrors `re.sub` stepping: after an empty match the next attempt at the
/// same offset must be non-empty.
fn clean_tags(src: &str) -> PassResult {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    let mut nonempty_at = usize::MAX;
    let mut x_from = 0;
    while let Some(x) = find_byte(h, b'{', x_from) {
        x_from = x + 1;
        let i = x + 2;
        if i < pos || !matches!(h.get(x + 1), Some(b'{') | Some(b'%')) {
            continue;
        }
        let mut k = i;
        let end = loop {
            let Some(stop) = memchr::memchr3(b'\n', b'}', b'%', &h[k..]).map(|o| o + k) else {
                break None;
            };
            if h[stop] == b'\n' {
                break None;
            }
            if h.get(stop + 1) == Some(&b'}') && !(stop == i && nonempty_at == i) {
                break Some(stop);
            }
            k = stop + 1;
        };
        let Some(j) = end else { continue };
        if j == i {
            // Empty match: retry this offset once, demanding a longer match.
            nonempty_at = i;
            x_from = x;
            pos = i;
            continue;
        }
        if let Some(cleaned) = clean_tag_text(&src[i..j]) {
            rw.rewrite(i, j).push_str(&cleaned);
        }
        pos = j;
        x_from = j.saturating_sub(2).max(x + 1);
    }
    Ok(rw.finish())
}

/// The chained `str.replace` calls commute: no replacement output (`' < > "`)
/// can complete another search string.
fn clean_tag_text(text: &str) -> Option<String> {
    const TABLE: &[(&str, &str)] = &[
        ("&#8216;", "'"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("\u{201c}", "\""),
        ("\u{201d}", "\""),
        ("\u{2018}", "'"),
        ("\u{2019}", "'"),
    ];
    let h = text.as_bytes();
    if !h.iter().any(|&b| b == b'&' || b == 0xE2) {
        return None;
    }
    let mut rw = Rewriter::new(text);
    let mut i = 0;
    while i < h.len() {
        if h[i] == b'&' || h[i] == 0xE2 {
            if let Some((needle, with)) = TABLE
                .iter()
                .find(|(n, _)| starts_with_at(h, i, n.as_bytes()))
            {
                rw.rewrite(i, i + needle.len()).push_str(with);
                i += needle.len();
                continue;
            }
        }
        i += 1;
    }
    rw.finish()
}
