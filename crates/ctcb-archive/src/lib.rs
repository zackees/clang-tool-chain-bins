//! Archive creation and extraction for `.tar.zst` toolchain packages.
//!
//! Provides streaming tar+zstd compression/decompression with hard-link
//! preservation, as well as standalone zstd compress/decompress helpers.

use std::fs;
use std::io::{self, BufReader, BufWriter};
use std::path::Path;

/// Compress `input` to `output` using zstd at the given compression `level` (1..=22).
pub fn compress_zstd(input: &Path, output: &Path, level: i32) -> anyhow::Result<()> {
    let reader = BufReader::new(fs::File::open(input)?);
    let writer = BufWriter::new(fs::File::create(output)?);
    let mut encoder = zstd::Encoder::new(writer, level)?;
    io::copy(&mut BufReader::new(reader), &mut encoder)?;
    encoder.finish()?;
    Ok(())
}

/// Decompress a zstd-compressed file from `input` to `output`.
pub fn decompress_zstd(input: &Path, output: &Path) -> anyhow::Result<()> {
    let reader = BufReader::new(fs::File::open(input)?);
    let mut decoder = zstd::Decoder::new(reader)?;
    let mut writer = BufWriter::new(fs::File::create(output)?);
    io::copy(&mut decoder, &mut writer)?;
    Ok(())
}

/// Create a tar archive from `source_dir` and write it to `output`.
///
/// Hard links within the directory are automatically detected and stored as
/// link entries by the `tar` crate (files sharing the same inode are recorded
/// once and linked thereafter).
pub fn create_tar(source_dir: &Path, output: &Path) -> anyhow::Result<()> {
    let file = BufWriter::new(fs::File::create(output)?);
    let mut builder = tar::Builder::new(file);
    builder.follow_symlinks(false);
    builder.append_dir_all(".", source_dir)?;
    builder.finish()?;
    Ok(())
}

/// Extract a tar archive to `output_dir`.
pub fn extract_tar(tar_path: &Path, output_dir: &Path) -> anyhow::Result<()> {
    fs::create_dir_all(output_dir)?;
    let file = BufReader::new(fs::File::open(tar_path)?);
    let mut archive = tar::Archive::new(file);
    archive.set_preserve_permissions(true);
    archive.unpack(output_dir)?;
    Ok(())
}

/// Create a `.tar.zst` archive from `source_dir` using streaming (no temp files).
///
/// The directory tree is tarred directly into a zstd encoder writing to `output`.
/// `zstd_level` controls compression effort (1 = fast, 22 = ultra).
pub fn create_tar_zst(source_dir: &Path, output: &Path, zstd_level: i32) -> anyhow::Result<()> {
    let file = BufWriter::new(fs::File::create(output)?);
    let encoder = zstd::Encoder::new(file, zstd_level)?;
    let mut builder = tar::Builder::new(encoder);
    builder.follow_symlinks(false);
    builder.append_dir_all(".", source_dir)?;
    let encoder = builder.into_inner()?;
    encoder.finish()?;
    Ok(())
}

/// Extract a `.tar.zst` archive to `output_dir` using streaming (no temp files).
///
/// The file is decompressed through a zstd decoder and piped directly into the
/// tar unpacker.
pub fn extract_tar_zst(archive: &Path, output_dir: &Path) -> anyhow::Result<()> {
    fs::create_dir_all(output_dir)?;
    let file = BufReader::new(fs::File::open(archive)?);
    let decoder = zstd::Decoder::new(file)?;
    let mut tar = tar::Archive::new(decoder);
    tar.set_preserve_permissions(true);
    tar.unpack(output_dir)?;
    Ok(())
}

/// Compression benchmarking utilities
pub mod bench {
    use anyhow::Result;
    use std::fs;
    use std::io::{self, BufReader, BufWriter};
    use std::path::Path;
    use std::time::Instant;

    /// Result of a single compression benchmark
    #[derive(Debug)]
    pub struct BenchResult {
        pub method: String,
        pub level: i32,
        pub compressed_size: u64,
        pub ratio: f64,
        pub duration: std::time::Duration,
    }

    /// Run compression benchmarks on a tar archive of the given directory.
    /// Tests gzip and zstd at various levels.
    pub fn run_benchmarks(source_dir: &Path, output_prefix: &str) -> Result<Vec<BenchResult>> {
        // First, create an uncompressed tar of the source
        let tar_path = std::env::temp_dir().join(format!("{}.tar", output_prefix));
        super::create_tar(source_dir, &tar_path)?;
        let tar_size = fs::metadata(&tar_path)?.len();

        println!("Uncompressed tar: {}", ctcb_core::format_size(tar_size));
        println!();
        println!(
            "{:<15} {:>6} {:>12} {:>8} {:>10}",
            "Method", "Level", "Size", "Ratio", "Time"
        );
        println!("{}", "-".repeat(55));

        let mut results = Vec::new();

        // Gzip
        for level in [1, 6, 9] {
            let output = std::env::temp_dir().join(format!("{}.tar.gz.{}", output_prefix, level));
            let start = Instant::now();
            compress_gzip(&tar_path, &output, level)?;
            let elapsed = start.elapsed();
            let size = fs::metadata(&output)?.len();
            let ratio = tar_size as f64 / size as f64;
            println!(
                "{:<15} {:>6} {:>12} {:>7.1}x {:>10}",
                "gzip",
                level,
                ctcb_core::format_size(size),
                ratio,
                ctcb_core::format_duration(elapsed)
            );
            results.push(BenchResult {
                method: "gzip".into(),
                level,
                compressed_size: size,
                ratio,
                duration: elapsed,
            });
            fs::remove_file(&output)?;
        }

        // Zstd
        for level in [1, 3, 10, 15, 19, 22] {
            let output = std::env::temp_dir().join(format!("{}.tar.zst.{}", output_prefix, level));
            let start = Instant::now();
            super::compress_zstd(&tar_path, &output, level)?;
            let elapsed = start.elapsed();
            let size = fs::metadata(&output)?.len();
            let ratio = tar_size as f64 / size as f64;
            println!(
                "{:<15} {:>6} {:>12} {:>7.1}x {:>10}",
                "zstd",
                level,
                ctcb_core::format_size(size),
                ratio,
                ctcb_core::format_duration(elapsed)
            );
            results.push(BenchResult {
                method: "zstd".into(),
                level,
                compressed_size: size,
                ratio,
                duration: elapsed,
            });
            fs::remove_file(&output)?;
        }

        // Clean up tar
        fs::remove_file(&tar_path)?;

        Ok(results)
    }

    fn compress_gzip(input: &Path, output: &Path, level: i32) -> Result<()> {
        use flate2::Compression;
        use flate2::write::GzEncoder;

        let reader = BufReader::new(fs::File::open(input)?);
        let writer = BufWriter::new(fs::File::create(output)?);
        let mut encoder = GzEncoder::new(writer, Compression::new(level as u32));
        let mut reader = BufReader::new(reader);
        io::copy(&mut reader, &mut encoder)?;
        encoder.finish()?;
        Ok(())
    }
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    /// Create a small directory tree for testing.
    fn create_test_tree(root: &Path) {
        let sub = root.join("subdir");
        fs::create_dir_all(&sub).unwrap();

        fs::write(root.join("hello.txt"), b"Hello, world!\n").unwrap();
        fs::write(root.join("binary.bin"), vec![0u8; 1024]).unwrap();
        fs::write(sub.join("nested.txt"), b"Nested content\n").unwrap();
    }

    // -- zstd round-trip -----------------------------------------------------

    #[test]
    fn zstd_compress_decompress_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let original = dir.path().join("original.txt");
        let compressed = dir.path().join("compressed.zst");
        let decompressed = dir.path().join("decompressed.txt");

        let content = b"The quick brown fox jumps over the lazy dog.\n";
        fs::write(&original, content).unwrap();

        compress_zstd(&original, &compressed, 3).unwrap();

        // Compressed file must exist and be non-empty
        let meta = fs::metadata(&compressed).unwrap();
        assert!(meta.len() > 0);

        decompress_zstd(&compressed, &decompressed).unwrap();

        let recovered = fs::read(&decompressed).unwrap();
        assert_eq!(recovered.as_slice(), content);
    }

    #[test]
    fn zstd_roundtrip_large_content() {
        let dir = tempfile::tempdir().unwrap();
        let original = dir.path().join("large.bin");
        let compressed = dir.path().join("large.zst");
        let decompressed = dir.path().join("large_out.bin");

        // Generate a non-trivial amount of data (100 KB)
        let mut data = Vec::with_capacity(100 * 1024);
        for i in 0u32..25600 {
            data.extend_from_slice(&i.to_le_bytes());
        }
        fs::write(&original, &data).unwrap();

        compress_zstd(&original, &compressed, 1).unwrap();
        decompress_zstd(&compressed, &decompressed).unwrap();

        let recovered = fs::read(&decompressed).unwrap();
        assert_eq!(recovered, data);
    }

    // -- tar round-trip ------------------------------------------------------

    #[test]
    fn tar_create_extract_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source");
        let tar_file = dir.path().join("archive.tar");
        let dst = dir.path().join("extracted");

        create_test_tree(&src);
        create_tar(&src, &tar_file).unwrap();

        assert!(tar_file.exists());
        assert!(fs::metadata(&tar_file).unwrap().len() > 0);

        extract_tar(&tar_file, &dst).unwrap();

        // Verify file contents survived the round-trip
        assert_eq!(
            fs::read_to_string(dst.join("hello.txt")).unwrap(),
            "Hello, world!\n"
        );
        assert_eq!(fs::read(dst.join("binary.bin")).unwrap(), vec![0u8; 1024]);
        assert_eq!(
            fs::read_to_string(dst.join("subdir").join("nested.txt")).unwrap(),
            "Nested content\n"
        );
    }

    // -- tar.zst round-trip --------------------------------------------------

    #[test]
    fn tar_zst_create_extract_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source");
        let archive = dir.path().join("archive.tar.zst");
        let dst = dir.path().join("extracted");

        create_test_tree(&src);
        create_tar_zst(&src, &archive, 3).unwrap();

        assert!(archive.exists());
        let archive_size = fs::metadata(&archive).unwrap().len();
        assert!(archive_size > 0, "archive must be non-empty");

        extract_tar_zst(&archive, &dst).unwrap();

        // Verify file contents
        assert_eq!(
            fs::read_to_string(dst.join("hello.txt")).unwrap(),
            "Hello, world!\n"
        );
        assert_eq!(fs::read(dst.join("binary.bin")).unwrap(), vec![0u8; 1024]);
        assert_eq!(
            fs::read_to_string(dst.join("subdir").join("nested.txt")).unwrap(),
            "Nested content\n"
        );
    }

    #[test]
    fn tar_zst_compression_actually_reduces_size() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source");
        let archive = dir.path().join("archive.tar.zst");

        fs::create_dir_all(&src).unwrap();
        // Write highly compressible data (all zeros)
        fs::write(src.join("zeros.bin"), vec![0u8; 64 * 1024]).unwrap();

        create_tar_zst(&src, &archive, 3).unwrap();

        let archive_size = fs::metadata(&archive).unwrap().len();
        // 64 KB of zeros should compress to well under 1 KB
        assert!(
            archive_size < 1024,
            "archive ({archive_size} bytes) should be much smaller than 64 KB of zeros"
        );
    }

    #[test]
    fn extract_tar_zst_creates_output_dir() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("source");
        let archive = dir.path().join("archive.tar.zst");
        let dst = dir.path().join("nonexistent").join("nested").join("output");

        create_test_tree(&src);
        create_tar_zst(&src, &archive, 1).unwrap();

        // dst does not exist yet
        assert!(!dst.exists());

        extract_tar_zst(&archive, &dst).unwrap();

        assert!(dst.exists());
        assert_eq!(
            fs::read_to_string(dst.join("hello.txt")).unwrap(),
            "Hello, world!\n"
        );
    }
}
