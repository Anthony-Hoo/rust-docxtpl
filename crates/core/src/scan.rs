//! Low-level search helpers shared by all passes.
//!
//! All delimiters are ASCII, so searching is done on bytes; slicing a `&str`
//! at the returned offsets is always on a UTF-8 boundary.

use memchr::memmem;

/// First occurrence of `needle` starting at or after `from`.
#[inline]
pub fn find(h: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from > h.len() {
        return None;
    }
    memmem::find(&h[from..], needle).map(|i| i + from)
}

/// First occurrence of `needle` that lies entirely inside `h[from..to]`.
#[inline]
pub fn find_in(h: &[u8], needle: &[u8], from: usize, to: usize) -> Option<usize> {
    if from > to {
        return None;
    }
    memmem::find(&h[from..to], needle).map(|i| i + from)
}

/// Last occurrence of `needle` that lies entirely inside `h[from..to]`.
#[inline]
pub fn rfind_in(h: &[u8], needle: &[u8], from: usize, to: usize) -> Option<usize> {
    if from > to {
        return None;
    }
    memmem::rfind(&h[from..to], needle).map(|i| i + from)
}

#[inline]
pub fn find_byte(h: &[u8], b: u8, from: usize) -> Option<usize> {
    if from > h.len() {
        return None;
    }
    memchr::memchr(b, &h[from..]).map(|i| i + from)
}

#[inline]
pub fn starts_with_at(h: &[u8], i: usize, needle: &[u8]) -> bool {
    h.len() >= i + needle.len() && &h[i..i + needle.len()] == needle
}

/// Python's `str.isspace()` / `\s` for `str` patterns (not Rust's White_Space).
#[inline]
pub fn is_py_space(c: char) -> bool {
    matches!(
        c,
        '\u{09}'..='\u{0D}'
            | '\u{1C}'..='\u{20}'
            | '\u{85}'
            | '\u{A0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200A}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202F}'
            | '\u{205F}'
            | '\u{3000}'
    )
}

/// Greedy `\s*` starting at byte offset `i`.
#[inline]
pub fn skip_py_space(s: &str, mut i: usize) -> usize {
    let b = s.as_bytes();
    loop {
        match b.get(i) {
            Some(&c) if c < 0x80 => {
                if is_py_space(c as char) {
                    i += 1;
                } else {
                    return i;
                }
            }
            Some(_) => match s[i..].chars().next() {
                Some(c) if is_py_space(c) => i += c.len_utf8(),
                _ => return i,
            },
            None => return i,
        }
    }
}

/// The regex fragment `<w:NAME[ >]` (or a literal when `exact`).
pub struct ElemStart {
    prefix: &'static [u8],
    exact: bool,
}

impl ElemStart {
    pub const fn open(prefix: &'static str) -> Self {
        Self {
            prefix: prefix.as_bytes(),
            exact: false,
        }
    }

    pub const fn literal(text: &'static str) -> Self {
        Self {
            prefix: text.as_bytes(),
            exact: true,
        }
    }

    #[inline]
    pub fn at(&self, h: &[u8], i: usize) -> bool {
        starts_with_at(h, i, self.prefix)
            && (self.exact || matches!(h.get(i + self.prefix.len()), Some(b' ') | Some(b'>')))
    }

    /// First start beginning in `[from, h.len())`.
    pub fn find(&self, h: &[u8], mut from: usize) -> Option<usize> {
        while let Some(i) = find(h, self.prefix, from) {
            if self.at(h, i) {
                return Some(i);
            }
            from = i + 1;
        }
        None
    }

    /// Last start beginning in `[from, to)`.
    pub fn rfind(&self, h: &[u8], from: usize, mut to: usize) -> Option<usize> {
        while let Some(i) = rfind_in(h, self.prefix, from, to) {
            if self.at(h, i) {
                return Some(i);
            }
            to = i + self.prefix.len() - 1;
        }
        None
    }
}

/// Copy-on-first-write output buffer: untouched input costs no allocation.
pub struct Rewriter<'a> {
    src: &'a str,
    out: Option<String>,
    copied: usize,
}

impl<'a> Rewriter<'a> {
    pub fn new(src: &'a str) -> Self {
        Self {
            src,
            out: None,
            copied: 0,
        }
    }

    /// Replace `src[start..end]`: returns the buffer to push the replacement to.
    pub fn rewrite(&mut self, start: usize, end: usize) -> &mut String {
        debug_assert!(self.copied <= start && start <= end);
        let src = self.src;
        let out = self
            .out
            .get_or_insert_with(|| String::with_capacity(src.len() + src.len() / 16 + 64));
        out.push_str(&src[self.copied..start]);
        self.copied = end;
        out
    }

    /// Insert at `at` without consuming input.
    pub fn insert(&mut self, at: usize) -> &mut String {
        self.rewrite(at, at)
    }

    pub fn finish(mut self) -> Option<String> {
        let src = self.src;
        if let Some(out) = self.out.as_mut() {
            out.push_str(&src[self.copied..]);
        }
        self.out
    }
}

/// `str.replace` that reports "unchanged" as `None`.
pub fn replace_all(src: &str, needle: &str, with: &str) -> Option<String> {
    let h = src.as_bytes();
    let mut rw = Rewriter::new(src);
    let mut pos = 0;
    while let Some(i) = find(h, needle.as_bytes(), pos) {
        rw.rewrite(i, i + needle.len()).push_str(with);
        pos = i + needle.len();
    }
    rw.finish()
}
