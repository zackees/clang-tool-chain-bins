use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

/// Manifest for deduplicated directory structure
#[derive(Debug, Serialize, Deserialize)]
pub struct DeduplicationManifest {
    /// Map of relative file path -> canonical file path (relative to canonical/)
    pub manifest: BTreeMap<String, String>,
    /// Map of hash -> canonical filename
    pub canonical_files: BTreeMap<String, String>,
    /// Statistics
    pub stats: DeduplicationStats,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DeduplicationStats {
    pub total_size: u64,
    pub deduped_size: u64,
    pub savings: u64,
    pub savings_percent: f64,
    pub duplicate_count: u64,
}

/// Analysis result: hash -> list of relative file paths, hash -> file size
pub struct AnalysisResult {
    pub hash_to_files: BTreeMap<String, Vec<String>>,
    pub hash_to_size: BTreeMap<String, u64>,
}

/// Analyze a directory for duplicate files using MD5 hash.
/// Scans all files recursively.
pub fn analyze_directory(directory: &Path) -> Result<AnalysisResult> {
    let mut hash_to_files: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut hash_to_size: BTreeMap<String, u64> = BTreeMap::new();

    for entry in WalkDir::new(directory).into_iter().filter_map(|e| e.ok()) {
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        let relative = path
            .strip_prefix(directory)
            .context("Failed to strip prefix")?
            .to_string_lossy()
            .replace('\\', "/"); // Normalize to forward slashes

        let hash = ctcb_checksum::md5_file(path)?;
        let size = path.metadata()?.len();

        hash_to_files
            .entry(hash.clone())
            .or_default()
            .push(relative);
        hash_to_size.entry(hash).or_insert(size);
    }

    Ok(AnalysisResult {
        hash_to_files,
        hash_to_size,
    })
}

/// Calculate space savings from deduplication
pub fn calculate_savings(analysis: &AnalysisResult) -> DeduplicationStats {
    let mut total_size = 0u64;
    let mut deduped_size = 0u64;
    let mut duplicate_count = 0u64;

    for (hash, files) in &analysis.hash_to_files {
        let size = analysis.hash_to_size[hash];
        total_size += size * files.len() as u64;
        deduped_size += size;
        if files.len() > 1 {
            duplicate_count += (files.len() - 1) as u64;
        }
    }

    let savings = total_size - deduped_size;
    let savings_percent = if total_size > 0 {
        savings as f64 / total_size as f64 * 100.0
    } else {
        0.0
    };

    DeduplicationStats {
        total_size,
        deduped_size,
        savings,
        savings_percent,
        duplicate_count,
    }
}

/// Create a deduplicated directory structure with manifest.
///
/// Creates:
///   dest_dir/canonical/  - one copy of each unique file
///   dest_dir/dedup_manifest.json - mapping of all files to their canonical source
pub fn create_deduped_structure(
    source_dir: &Path,
    dest_dir: &Path,
) -> Result<DeduplicationManifest> {
    let analysis = analyze_directory(source_dir)?;
    let stats = calculate_savings(&analysis);

    let canonical_dir = dest_dir.join("canonical");
    fs::create_dir_all(&canonical_dir)?;

    let mut manifest_map: BTreeMap<String, String> = BTreeMap::new();
    let mut canonical_files: BTreeMap<String, String> = BTreeMap::new();

    for (hash, files) in &analysis.hash_to_files {
        let mut sorted_files = files.clone();
        sorted_files.sort();
        let canonical_name = &sorted_files[0];

        // Copy canonical file, preserving directory structure within canonical/
        let src = source_dir.join(canonical_name);
        let dst = canonical_dir.join(canonical_name);
        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(&src, &dst)?;

        canonical_files.insert(hash.clone(), canonical_name.clone());

        for filename in &sorted_files {
            manifest_map.insert(filename.clone(), canonical_name.clone());
        }
    }

    let manifest = DeduplicationManifest {
        manifest: manifest_map,
        canonical_files,
        stats,
    };

    let manifest_path = dest_dir.join("dedup_manifest.json");
    let json = serde_json::to_string_pretty(&manifest)?;
    fs::write(&manifest_path, json)?;

    Ok(manifest)
}

/// Expand a deduplicated structure back to full directory.
pub fn expand_deduped_structure(deduped_dir: &Path, output_dir: &Path) -> Result<()> {
    let manifest_path = deduped_dir.join("dedup_manifest.json");
    let json = fs::read_to_string(&manifest_path)?;
    let manifest: DeduplicationManifest = serde_json::from_str(&json)?;

    let canonical_dir = deduped_dir.join("canonical");

    for (filename, canonical_name) in &manifest.manifest {
        let src = canonical_dir.join(canonical_name);
        let dst = output_dir.join(filename);
        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(&src, &dst)?;
    }

    println!(
        "Expanded {} files from {} canonical files",
        manifest.manifest.len(),
        manifest.canonical_files.len()
    );

    Ok(())
}

/// Create a directory structure with hard links from a dedup manifest.
///
/// Reads `dedup_manifest.json` from `deduped_dir`, then for each entry in the
/// manifest the first occurrence of a canonical file is copied into `output_dir`
/// and subsequent files sharing the same canonical source are hard-linked to
/// that first copy.  If hard-linking fails (e.g. cross-device), falls back to a
/// regular copy.
pub fn create_hardlink_structure(deduped_dir: &Path, output_dir: &Path) -> Result<PathBuf> {
    let manifest_path = deduped_dir.join("dedup_manifest.json");
    let json = fs::read_to_string(&manifest_path).context("Failed to read dedup_manifest.json")?;
    let manifest: DeduplicationManifest = serde_json::from_str(&json)?;

    let canonical_dir = deduped_dir.join("canonical");
    fs::create_dir_all(output_dir)?;

    // Track which canonical files have been copied already
    let mut canonical_copied: BTreeMap<String, PathBuf> = BTreeMap::new();

    for (filename, canonical_name) in &manifest.manifest {
        let src = canonical_dir.join(canonical_name);
        let dst = output_dir.join(filename);

        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)?;
        }

        if !src.exists() {
            eprintln!("Warning: Canonical file not found: {}", src.display());
            continue;
        }

        if let Some(first_copy) = canonical_copied.get(canonical_name) {
            // Create hard link to the first copy
            if dst.exists() {
                fs::remove_file(&dst)?;
            }
            match fs::hard_link(first_copy, &dst) {
                Ok(()) => {}
                Err(e) => {
                    eprintln!("Warning: Hard link failed ({}), falling back to copy", e);
                    fs::copy(&src, &dst)?;
                }
            }
        } else {
            // First occurrence: copy the file
            fs::copy(&src, &dst)?;
            canonical_copied.insert(canonical_name.clone(), dst);
        }
    }

    Ok(output_dir.to_path_buf())
}

/// Get the unique file identifier (inode on Unix, file index on Windows).
///
/// On Windows this opens the file and calls `GetFileInformationByHandle` to
/// obtain a 64-bit file index.  On Unix it reads `ino()` from metadata.
fn get_file_id(path: &Path) -> Result<u64> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;

        #[repr(C)]
        #[allow(non_snake_case)]
        struct BY_HANDLE_FILE_INFORMATION {
            dwFileAttributes: u32,
            ftCreationTime: [u32; 2],
            ftLastAccessTime: [u32; 2],
            ftLastWriteTime: [u32; 2],
            dwVolumeSerialNumber: u32,
            nFileSizeHigh: u32,
            nFileSizeLow: u32,
            nNumberOfLinks: u32,
            nFileIndexHigh: u32,
            nFileIndexLow: u32,
        }

        unsafe extern "system" {
            fn GetFileInformationByHandle(
                hFile: *mut std::ffi::c_void,
                lpFileInformation: *mut BY_HANDLE_FILE_INFORMATION,
            ) -> i32;
        }

        let file = fs::File::open(path)?;
        let handle = file.as_raw_handle();
        let mut info = std::mem::MaybeUninit::<BY_HANDLE_FILE_INFORMATION>::zeroed();
        let ret = unsafe { GetFileInformationByHandle(handle as *mut _, info.as_mut_ptr()) };
        if ret == 0 {
            anyhow::bail!("GetFileInformationByHandle failed for {}", path.display());
        }
        let info = unsafe { info.assume_init() };
        Ok(((info.nFileIndexHigh as u64) << 32) | info.nFileIndexLow as u64)
    }
    #[cfg(not(windows))]
    {
        use std::os::unix::fs::MetadataExt;
        let meta = fs::metadata(path)?;
        Ok(meta.ino())
    }
}

/// Verify hard links in a directory by grouping files by inode / file index.
///
/// Returns `(total_files, unique_inodes)`.  On Windows the file index from
/// `GetFileInformationByHandle` is used; on Unix the inode from
/// `MetadataExt::ino()`.
pub fn verify_hardlinks(dir: &Path) -> Result<(u64, u64)> {
    use std::collections::HashMap;

    let mut inode_to_files: HashMap<u64, Vec<String>> = HashMap::new();

    for entry in WalkDir::new(dir).into_iter().filter_map(|e| e.ok()) {
        if !entry.file_type().is_file() {
            continue;
        }

        let inode = get_file_id(entry.path())?;

        let name = entry
            .path()
            .strip_prefix(dir)
            .unwrap_or(entry.path())
            .to_string_lossy()
            .to_string();

        inode_to_files.entry(inode).or_default().push(name);
    }

    let total_files: u64 = inode_to_files.values().map(|v| v.len() as u64).sum();
    let unique_inodes = inode_to_files.len() as u64;
    let hardlink_groups = inode_to_files.values().filter(|v| v.len() > 1).count();

    ctcb_core::print_section("VERIFYING HARD LINKS");

    for files in inode_to_files.values() {
        if files.len() > 1 {
            println!("\nHard link group ({} files):", files.len());
            for f in files {
                println!("  - {}", f);
            }
        }
    }

    println!();
    println!("Total files: {}", total_files);
    println!("Unique inodes: {}", unique_inodes);
    println!("Hard link groups: {}", hardlink_groups);
    println!("Duplicate files: {}", total_files - unique_inodes);

    Ok((total_files, unique_inodes))
}

/// Print a detailed analysis report of duplicates in a directory.
pub fn print_analysis(analysis: &AnalysisResult) {
    let stats = calculate_savings(analysis);

    ctcb_core::print_section("BINARY DEDUPLICATION ANALYSIS");
    println!();
    println!(
        "Total uncompressed size: {}",
        ctcb_core::format_size(stats.total_size)
    );
    println!(
        "Deduplicated size:       {}",
        ctcb_core::format_size(stats.deduped_size)
    );
    println!(
        "Space savings:           {} ({:.1}%)",
        ctcb_core::format_size(stats.savings),
        stats.savings_percent
    );
    println!("Duplicate files:         {}", stats.duplicate_count);
    println!();

    println!("Duplicate Groups:");
    println!("{}", "-".repeat(70));

    for (hash, files) in &analysis.hash_to_files {
        if files.len() > 1 {
            let size = analysis.hash_to_size[hash];
            let size_mb = size as f64 / (1024.0 * 1024.0);
            let waste_mb = size_mb * (files.len() - 1) as f64;
            let mut sorted = files.clone();
            sorted.sort();
            println!(
                "\n{} identical files ({:.1} MB each, {:.1} MB wasted):",
                files.len(),
                size_mb,
                waste_mb
            );
            for (i, filename) in sorted.iter().enumerate() {
                let marker = if i == 0 { " <- CANONICAL" } else { "" };
                println!("  - {}{}", filename, marker);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn create_test_files(dir: &Path) {
        // Create some files with duplicate content
        fs::write(dir.join("file_a.exe"), b"hello world").unwrap();
        fs::write(dir.join("file_b.exe"), b"hello world").unwrap(); // duplicate of a
        fs::write(dir.join("file_c.exe"), b"different content").unwrap();
        fs::write(dir.join("file_d.exe"), b"different content").unwrap(); // duplicate of c
        fs::write(dir.join("unique.exe"), b"unique data").unwrap();
    }

    #[test]
    fn test_analyze_finds_duplicates() {
        let tmp = TempDir::new().unwrap();
        create_test_files(tmp.path());
        let analysis = analyze_directory(tmp.path()).unwrap();
        // 3 unique hashes, 2 files are duplicates
        assert_eq!(analysis.hash_to_files.len(), 3);
    }

    #[test]
    fn test_calculate_savings() {
        let tmp = TempDir::new().unwrap();
        create_test_files(tmp.path());
        let analysis = analyze_directory(tmp.path()).unwrap();
        let stats = calculate_savings(&analysis);
        assert_eq!(stats.duplicate_count, 2); // file_b and file_d are duplicates
        assert!(stats.savings > 0);
    }

    #[test]
    fn test_dedup_and_expand_roundtrip() {
        let tmp = TempDir::new().unwrap();
        let source = tmp.path().join("source");
        let deduped = tmp.path().join("deduped");
        let expanded = tmp.path().join("expanded");
        fs::create_dir_all(&source).unwrap();

        create_test_files(&source);

        // Dedup
        let manifest = create_deduped_structure(&source, &deduped).unwrap();
        assert_eq!(manifest.canonical_files.len(), 3); // 3 unique files

        // Expand
        expand_deduped_structure(&deduped, &expanded).unwrap();

        // Verify all files exist with correct content
        assert_eq!(
            fs::read(expanded.join("file_a.exe")).unwrap(),
            b"hello world"
        );
        assert_eq!(
            fs::read(expanded.join("file_b.exe")).unwrap(),
            b"hello world"
        );
        assert_eq!(
            fs::read(expanded.join("file_c.exe")).unwrap(),
            b"different content"
        );
        assert_eq!(
            fs::read(expanded.join("file_d.exe")).unwrap(),
            b"different content"
        );
        assert_eq!(
            fs::read(expanded.join("unique.exe")).unwrap(),
            b"unique data"
        );
    }

    #[test]
    fn test_hardlink_structure_roundtrip() {
        let tmp = TempDir::new().unwrap();
        let source = tmp.path().join("source");
        let deduped = tmp.path().join("deduped");
        let hardlinked = tmp.path().join("hardlinked");
        fs::create_dir_all(&source).unwrap();

        // Create test files with duplicates
        fs::write(source.join("a.exe"), b"same content").unwrap();
        fs::write(source.join("b.exe"), b"same content").unwrap();
        fs::write(source.join("c.exe"), b"different").unwrap();

        // Dedup
        create_deduped_structure(&source, &deduped).unwrap();

        // Create hardlink structure
        create_hardlink_structure(&deduped, &hardlinked).unwrap();

        // Verify files exist and have correct content
        assert_eq!(fs::read(hardlinked.join("a.exe")).unwrap(), b"same content");
        assert_eq!(fs::read(hardlinked.join("b.exe")).unwrap(), b"same content");
        assert_eq!(fs::read(hardlinked.join("c.exe")).unwrap(), b"different");

        // Verify hardlinks (a.exe and b.exe should share an inode)
        let (total, unique) = verify_hardlinks(&hardlinked).unwrap();
        assert_eq!(total, 3);
        assert_eq!(unique, 2); // a.exe and b.exe are hardlinked
    }
}
