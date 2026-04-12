//! Checksum utilities for verifying archive integrity.
//!
//! Provides SHA256 and MD5 hashing of files, verification against expected
//! digests, and generation of standard sidecar checksum files.

use std::fs;
use std::io::{BufReader, Read};
use std::path::Path;

use md5::Md5;
use sha2::{Digest, Sha256};

/// Size of the internal read buffer (8 KiB).
const BUF_SIZE: usize = 8 * 1024;

/// Compute the SHA-256 hex digest of the file at `path`.
///
/// The file is read in 8 KiB chunks so arbitrarily large files can be hashed
/// without loading them entirely into memory.
pub fn sha256_file(path: &Path) -> anyhow::Result<String> {
    let file = fs::File::open(path)?;
    let mut reader = BufReader::new(file);
    let mut hasher = Sha256::new();
    let mut buf = [0u8; BUF_SIZE];

    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

/// Compute the MD5 hex digest of the file at `path`.
///
/// The file is read in 8 KiB chunks so arbitrarily large files can be hashed
/// without loading them entirely into memory.
pub fn md5_file(path: &Path) -> anyhow::Result<String> {
    let file = fs::File::open(path)?;
    let mut reader = BufReader::new(file);
    let mut hasher = Md5::new();
    let mut buf = [0u8; BUF_SIZE];

    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

/// Verify that the SHA-256 digest of `path` matches `expected` (hex string).
///
/// The comparison is case-insensitive.
pub fn sha256_verify(path: &Path, expected: &str) -> anyhow::Result<bool> {
    let actual = sha256_file(path)?;
    Ok(actual == expected.to_lowercase())
}

/// Generate `.sha256` and `.md5` sidecar files next to `archive_path`.
///
/// Each sidecar contains a single line in the standard checksum format:
/// ```text
/// {hex_digest}  {filename}
/// ```
/// where `filename` is the file name only (no directory components).
pub fn generate_checksum_files(archive_path: &Path) -> anyhow::Result<()> {
    let file_name = archive_path
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("archive path has no file name component"))?
        .to_string_lossy();

    let sha = sha256_file(archive_path)?;
    let md5 = md5_file(archive_path)?;

    // Build sidecar paths by appending ".sha256" / ".md5" to the full archive name
    let sha_path = archive_path.with_file_name(format!("{file_name}.sha256"));
    let md5_path = archive_path.with_file_name(format!("{file_name}.md5"));

    fs::write(&sha_path, format!("{sha}  {file_name}\n"))?;
    fs::write(&md5_path, format!("{md5}  {file_name}\n"))?;

    Ok(())
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    /// Helper: create a temp file with `content` and return the `NamedTempFile`.
    fn temp_with(content: &[u8]) -> NamedTempFile {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(content).unwrap();
        f.flush().unwrap();
        f
    }

    // -- SHA-256 -------------------------------------------------------------

    #[test]
    fn sha256_known_content() {
        // SHA-256 of "hello\n" (0x68656c6c6f0a)
        // = 5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03
        let f = temp_with(b"hello\n");
        let hash = sha256_file(f.path()).unwrap();
        assert_eq!(
            hash,
            "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
        );
    }

    #[test]
    fn sha256_empty_file() {
        let f = temp_with(b"");
        let hash = sha256_file(f.path()).unwrap();
        // SHA-256 of empty input
        assert_eq!(
            hash,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    // -- MD5 -----------------------------------------------------------------

    #[test]
    fn md5_known_content() {
        // MD5 of "hello\n" = b1946ac92492d2347c6235b4d2611184
        let f = temp_with(b"hello\n");
        let hash = md5_file(f.path()).unwrap();
        assert_eq!(hash, "b1946ac92492d2347c6235b4d2611184");
    }

    #[test]
    fn md5_empty_file() {
        let f = temp_with(b"");
        let hash = md5_file(f.path()).unwrap();
        // MD5 of empty input
        assert_eq!(hash, "d41d8cd98f00b204e9800998ecf8427e");
    }

    // -- sha256_verify -------------------------------------------------------

    #[test]
    fn sha256_verify_matching() {
        let f = temp_with(b"hello\n");
        assert!(sha256_verify(
            f.path(),
            "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
        )
        .unwrap());
    }

    #[test]
    fn sha256_verify_matching_uppercase() {
        let f = temp_with(b"hello\n");
        assert!(sha256_verify(
            f.path(),
            "5891B5B522D5DF086D0FF0B110FBD9D21BB4FC7163AF34D08286A2E846F6BE03"
        )
        .unwrap());
    }

    #[test]
    fn sha256_verify_non_matching() {
        let f = temp_with(b"hello\n");
        assert!(!sha256_verify(f.path(), "0000000000000000000000000000000000000000000000000000000000000000").unwrap());
    }

    // -- generate_checksum_files ---------------------------------------------

    #[test]
    fn generate_checksum_files_creates_sidecars() {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("test-archive.tar.zst");
        fs::write(&archive, b"fake archive content").unwrap();

        generate_checksum_files(&archive).unwrap();

        let sha_path = dir.path().join("test-archive.tar.zst.sha256");
        let md5_path = dir.path().join("test-archive.tar.zst.md5");

        assert!(sha_path.exists(), ".sha256 sidecar must exist");
        assert!(md5_path.exists(), ".md5 sidecar must exist");

        let sha_content = fs::read_to_string(&sha_path).unwrap();
        let md5_content = fs::read_to_string(&md5_path).unwrap();

        // Verify format: "{hash}  {filename}\n"
        assert!(
            sha_content.ends_with("  test-archive.tar.zst\n"),
            "sha256 sidecar has wrong format: {sha_content:?}"
        );
        assert!(
            md5_content.ends_with("  test-archive.tar.zst\n"),
            "md5 sidecar has wrong format: {md5_content:?}"
        );

        // Verify the embedded hashes are correct
        let expected_sha = sha256_file(&archive).unwrap();
        assert!(sha_content.starts_with(&expected_sha));

        let expected_md5 = md5_file(&archive).unwrap();
        assert!(md5_content.starts_with(&expected_md5));
    }
}
