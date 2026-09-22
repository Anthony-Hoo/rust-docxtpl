//! `docxtpl._native`: thin PyO3 bindings over `docxtpl-core`.
//!
//! Whole documents cross the FFI boundary as one `str`; the kernels run with
//! the GIL released. Functions return `None` when the Python caller should
//! keep its input (nothing changed) or fall back to the reference
//! implementation (`patch_xml` on unsupported input).

use pyo3::prelude::*;

/// Native `DocxTemplate.patch_xml`; `None` means "use the Python reference".
#[pyfunction]
fn patch_xml(py: Python<'_>, src: &str) -> Option<String> {
    py.detach(|| docxtpl_core::patch_xml(src).ok())
}

/// Native prefix of `render_xml_part`; `None` means unchanged.
#[pyfunction]
fn pre_render(py: Python<'_>, src: &str) -> Option<String> {
    py.detach(|| docxtpl_core::pre_render(src))
}

/// Native suffix of `render_xml_part` incl. `resolve_listing`; `None` means unchanged.
#[pyfunction]
fn post_render(py: Python<'_>, dst: &str) -> Option<String> {
    py.detach(|| docxtpl_core::post_render(dst))
}

#[pymodule(gil_used = false)]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(patch_xml, m)?)?;
    m.add_function(wrap_pyfunction!(pre_render, m)?)?;
    m.add_function(wrap_pyfunction!(post_render, m)?)?;
    Ok(())
}
