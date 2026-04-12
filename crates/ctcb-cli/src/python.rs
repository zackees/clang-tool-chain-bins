use pyo3::prelude::*;
use pyo3::exceptions::{PyFileNotFoundError, PyRuntimeError};
use std::path::PathBuf;

/// Expand a .tar.zst archive to the given output directory.
///
/// If `verify` is provided, the archive's SHA-256 is checked against the
/// expected hex digest before extraction.
#[pyfunction]
#[pyo3(signature = (archive, output_dir, verify=None))]
fn expand_archive(archive: &str, output_dir: &str, verify: Option<&str>) -> PyResult<()> {
    let archive = PathBuf::from(archive);
    let output_dir = PathBuf::from(output_dir);

    if !archive.exists() {
        return Err(PyFileNotFoundError::new_err(format!(
            "Archive not found: {}",
            archive.display()
        )));
    }

    if let Some(expected) = verify {
        let valid = ctcb_checksum::sha256_verify(&archive, expected)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        if !valid {
            return Err(PyRuntimeError::new_err("SHA256 verification failed"));
        }
    }

    std::fs::create_dir_all(&output_dir)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;

    ctcb_archive::extract_tar_zst(&archive, &output_dir)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Create a .tar.zst archive from a directory.
///
/// `zstd_level` controls compression effort (1 = fast, 22 = ultra).
#[pyfunction]
#[pyo3(signature = (source_dir, output, zstd_level=22))]
fn create_tar_zst(source_dir: &str, output: &str, zstd_level: i32) -> PyResult<()> {
    ctcb_archive::create_tar_zst(
        &PathBuf::from(source_dir),
        &PathBuf::from(output),
        zstd_level,
    )
    .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Compute the SHA-256 hex digest of a file.
#[pyfunction]
fn sha256_file(path: &str) -> PyResult<String> {
    ctcb_checksum::sha256_file(&PathBuf::from(path))
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Compute the MD5 hex digest of a file.
#[pyfunction]
fn md5_file(path: &str) -> PyResult<String> {
    ctcb_checksum::md5_file(&PathBuf::from(path))
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Verify the SHA-256 digest of a file against an expected hex string.
///
/// Returns `True` if the digest matches, `False` otherwise.
#[pyfunction]
fn sha256_verify(path: &str, expected: &str) -> PyResult<bool> {
    ctcb_checksum::sha256_verify(&PathBuf::from(path), expected)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Generate `.sha256` and `.md5` sidecar checksum files next to the archive.
#[pyfunction]
fn generate_checksum_files(archive_path: &str) -> PyResult<()> {
    ctcb_checksum::generate_checksum_files(&PathBuf::from(archive_path))
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Read a platform manifest and return its contents as a JSON string.
#[pyfunction]
fn read_platform_manifest(path: &str) -> PyResult<String> {
    let manifest = ctcb_manifest::read_platform_manifest(&PathBuf::from(path))
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    serde_json::to_string_pretty(&manifest)
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Update (or create) a platform manifest with a new version entry.
///
/// `parts` is an optional list of `(href, sha256)` tuples for split archives.
#[pyfunction]
#[pyo3(signature = (path, version, href, sha256, parts=None))]
fn update_platform_manifest(
    path: &str,
    version: &str,
    href: &str,
    sha256: &str,
    parts: Option<Vec<(String, String)>>,
) -> PyResult<()> {
    let part_refs = parts.map(|p| {
        p.into_iter()
            .map(|(href, sha256)| ctcb_manifest::PartRef { href, sha256 })
            .collect()
    });

    ctcb_manifest::update_platform_manifest(
        &PathBuf::from(path),
        version,
        href,
        sha256,
        part_refs,
    )
    .map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Generate a GitHub LFS media URL for an asset path.
///
/// `branch` defaults to `"main"`.
#[pyfunction]
#[pyo3(signature = (asset_path, branch="main"))]
fn lfs_media_url(asset_path: &str, branch: &str) -> String {
    ctcb_manifest::lfs_media_url(asset_path, branch)
}

#[pymodule]
fn _native(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(expand_archive, m)?)?;
    m.add_function(wrap_pyfunction!(create_tar_zst, m)?)?;
    m.add_function(wrap_pyfunction!(sha256_file, m)?)?;
    m.add_function(wrap_pyfunction!(md5_file, m)?)?;
    m.add_function(wrap_pyfunction!(sha256_verify, m)?)?;
    m.add_function(wrap_pyfunction!(generate_checksum_files, m)?)?;
    m.add_function(wrap_pyfunction!(read_platform_manifest, m)?)?;
    m.add_function(wrap_pyfunction!(update_platform_manifest, m)?)?;
    m.add_function(wrap_pyfunction!(lfs_media_url, m)?)?;
    Ok(())
}
