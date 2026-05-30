use std::fs;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

/// Info about a split part
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PartInfo {
    pub path: PathBuf,
    pub sha256: String,
    pub size: u64,
}

/// Split an archive file into parts of at most `max_part_size_mb` megabytes.
/// Returns info about each created part.
pub fn split_archive(
    archive_path: &Path,
    max_part_size_mb: u64,
    output_dir: &Path,
) -> Result<Vec<PartInfo>> {
    let file_size = fs::metadata(archive_path)?.len();
    let max_bytes = max_part_size_mb * 1024 * 1024;

    if file_size <= max_bytes {
        println!(
            "Archive is {} — no splitting needed (limit: {} MB)",
            ctcb_core::format_size(file_size),
            max_part_size_mb
        );
        return Ok(vec![]);
    }

    fs::create_dir_all(output_dir)?;

    let stem = archive_path
        .file_name()
        .context("No filename")?
        .to_string_lossy();

    let mut reader = BufReader::new(fs::File::open(archive_path)?);
    let mut parts = Vec::new();
    let mut part_num = 1u32;
    let mut buf = vec![0u8; 64 * 1024]; // 64KB read buffer

    loop {
        let part_name = format!("{}.part{}", stem, part_num);
        let part_path = output_dir.join(&part_name);
        let mut writer = BufWriter::new(fs::File::create(&part_path)?);
        let mut part_bytes = 0u64;

        loop {
            let to_read = std::cmp::min(buf.len() as u64, max_bytes - part_bytes) as usize;
            let n = reader.read(&mut buf[..to_read])?;
            if n == 0 {
                break;
            }
            writer.write_all(&buf[..n])?;
            part_bytes += n as u64;
            if part_bytes >= max_bytes {
                break;
            }
        }

        writer.flush()?;
        drop(writer);

        if part_bytes == 0 {
            // We read nothing — done, remove empty file
            fs::remove_file(&part_path)?;
            break;
        }

        let sha256 = ctcb_checksum::sha256_file(&part_path)?;
        println!(
            "  Part {}: {} (sha256: {}...)",
            part_num,
            ctcb_core::format_size(part_bytes),
            &sha256[..16]
        );

        parts.push(PartInfo {
            path: part_path,
            sha256,
            size: part_bytes,
        });

        part_num += 1;
        if part_bytes < max_bytes {
            break;
        } // Last chunk was smaller = EOF
    }

    println!("Split into {} parts", parts.len());
    Ok(parts)
}

/// Reassemble parts back into a single file.
pub fn join_parts(parts: &[PathBuf], output: &Path) -> Result<()> {
    let mut writer = BufWriter::new(fs::File::create(output)?);
    let mut buf = vec![0u8; 64 * 1024];

    for part in parts {
        let mut reader = BufReader::new(fs::File::open(part)?);
        loop {
            let n = reader.read(&mut buf)?;
            if n == 0 {
                break;
            }
            writer.write_all(&buf[..n])?;
        }
    }

    writer.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_no_split_needed() {
        let tmp = TempDir::new().unwrap();
        let archive = tmp.path().join("small.tar.zst");
        fs::write(&archive, vec![0u8; 1024]).unwrap(); // 1KB file
        let parts = split_archive(&archive, 95, tmp.path().join("parts").as_path()).unwrap();
        assert!(parts.is_empty()); // No split needed
    }

    #[test]
    fn test_split_and_join_roundtrip() {
        let tmp = TempDir::new().unwrap();
        let archive = tmp.path().join("big.tar.zst");
        // Create a 3MB file (will be split into 2 parts at 2MB limit)
        let data: Vec<u8> = (0..3_000_000u32).map(|i| (i % 256) as u8).collect();
        fs::write(&archive, &data).unwrap();

        let parts_dir = tmp.path().join("parts");
        let parts = split_archive(&archive, 2, &parts_dir).unwrap();
        assert_eq!(parts.len(), 2);

        // Rejoin
        let rejoined = tmp.path().join("rejoined.tar.zst");
        let part_paths: Vec<PathBuf> = parts.iter().map(|p| p.path.clone()).collect();
        join_parts(&part_paths, &rejoined).unwrap();

        // Verify identical content
        assert_eq!(fs::read(&archive).unwrap(), fs::read(&rejoined).unwrap());
    }
}
