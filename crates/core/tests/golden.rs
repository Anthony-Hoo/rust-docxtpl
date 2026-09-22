//! Replays `golden.json` (generated from the Python reference implementation
//! by `tests/make_golden.py`) so the kernels can be verified without Python.

use docxtpl_core::{patch_xml, post_render, pre_render};
use serde_json::Value;

fn cases(name: &str) -> Vec<(String, Option<String>)> {
    let golden: Value =
        serde_json::from_str(include_str!("golden.json")).expect("valid golden.json");
    golden[name]
        .as_array()
        .expect("case list")
        .iter()
        .map(|case| {
            (
                case[0].as_str().expect("input").to_owned(),
                case[1].as_str().map(str::to_owned),
            )
        })
        .collect()
}

#[test]
fn patch_xml_matches_reference() {
    let cases = cases("patch_xml");
    assert!(cases.len() > 100);
    for (input, expected) in cases {
        match (patch_xml(&input), expected) {
            (Ok(actual), Some(expected)) => assert_eq!(actual, expected, "input: {input:?}"),
            // The reference raised: the native path must have declined too.
            (Ok(actual), None) => {
                panic!("reference failed but native returned {actual:?} for {input:?}")
            }
            // Declining is always allowed; the caller falls back to the reference.
            (Err(_), _) => {}
        }
    }
}

#[test]
fn declines_are_rare_and_intentional() {
    let declined: Vec<_> = cases("patch_xml")
        .into_iter()
        .filter(|(input, _)| patch_xml(input).is_err())
        .collect();
    assert!(declined.len() <= 5, "unexpected declines: {declined:?}");
    assert!(declined
        .iter()
        .all(|(input, _)| input.contains('\\') || !input.is_ascii()));
}

#[test]
fn pre_render_matches_reference() {
    for (input, expected) in cases("pre_render") {
        let actual = pre_render(&input).unwrap_or_else(|| input.clone());
        assert_eq!(Some(actual), expected, "input: {input:?}");
    }
}

#[test]
fn post_render_matches_reference() {
    for (input, expected) in cases("post_render") {
        let actual = post_render(&input).unwrap_or_else(|| input.clone());
        assert_eq!(Some(actual), expected, "input: {input:?}");
    }
}

#[test]
fn unchanged_input_is_reported_without_allocation() {
    assert_eq!(pre_render("<w:body><w:sectPr/></w:body>"), None);
    assert_eq!(post_render("<w:body><w:sectPr/></w:body>"), None);
}
