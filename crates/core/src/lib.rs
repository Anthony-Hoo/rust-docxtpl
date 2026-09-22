//! Pure-Rust text kernels behind the `docxtpl` compatibility package.
//!
//! Every public function here is a byte-exact re-implementation of a chain of
//! Python `re.sub` calls in docxtpl 0.20.1. The regexes rely on lookaround and
//! backtracking order, so they are not translated to another regex engine;
//! each pass is a hand-written scanner whose matching rules are derived from
//! (and documented next to) the original pattern. Equivalence is enforced by
//! differential tests against the original Python implementation.
//!
//! Inputs the scanners cannot reproduce exactly (see [`Unsupported`]) are
//! reported to the caller, which then runs the reference Python code instead.

pub mod patch;
pub mod render;
mod scan;

pub use patch::patch_xml;
pub use render::{post_render, pre_render};

/// The input needs a regex feature that is intentionally not replicated
/// (e.g. backslash escapes in a replacement template, non-ASCII `\d`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unsupported(pub &'static str);

impl std::fmt::Display for Unsupported {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "unsupported by native path: {}", self.0)
    }
}

impl std::error::Error for Unsupported {}
