//! Strip non-essential files from LLVM/Clang toolchain distributions.
//!
//! This crate removes unnecessary files from downloaded LLVM distributions
//! and optionally strips debug symbols from binaries to minimize package size.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result};
use ctcb_core::{Platform, Target};
use walkdir::WalkDir;

/// Essential binaries to keep (base names without extensions).
pub const ESSENTIAL_BINARIES: &[&str] = &[
    // Core compilation
    "clang",
    "clang++",
    "clang-cl",
    "clang-cpp",
    // Linkers
    "lld",
    "lld-link",
    "ld.lld",
    "ld64.lld",
    "wasm-ld",
    // Binary utilities
    "llvm-ar",
    "llvm-nm",
    "llvm-objdump",
    "llvm-objcopy",
    "llvm-ranlib",
    "llvm-strip",
    "llvm-readelf",
    "llvm-readobj",
    // Additional utilities
    "llvm-as",
    "llvm-dis",
    "clang-format",
    "clang-tidy",
    "llvm-symbolizer",
    "llvm-config",
];

/// Directories to remove completely (used as reference; the copy-based
/// approach below only copies what is needed, so these are implicitly skipped).
#[allow(dead_code)]
const REMOVE_DIRS: &[&str] = &[
    "share/doc",
    "share/man",
    "docs",
    "share/clang",
    "share/opt-viewer",
    "share/scan-build",
    "share/scan-view",
    "python_packages",
    "libexec",
];

/// File extensions to remove from lib directories.
const REMOVE_LIB_EXTENSIONS: &[&str] = &[".a", ".lib", ".cmake"];

/// Configuration for stripping.
pub struct StripConfig {
    pub target: Target,
    pub keep_headers: bool,
    pub strip_debug: bool,
    pub verbose: bool,
}

/// Result statistics from stripping.
pub struct StripStats {
    pub original_size: u64,
    pub final_size: u64,
    pub files_kept: u64,
    pub files_removed: u64,
}

/// Check if a binary name (with or without extension) is in the essential list.
pub fn is_essential_binary(name: &str) -> bool {
    let base = name
        .strip_suffix(".exe")
        .or_else(|| name.strip_suffix(".dll"))
        .or_else(|| name.strip_suffix(".so"))
        .or_else(|| name.strip_suffix(".dylib"))
        .unwrap_or(name);
    // Also handle versioned clang like "clang-19"
    let base = if base.starts_with("clang-")
        && base[6..].chars().all(|c| c.is_ascii_digit() || c == '.')
        && !base[6..].is_empty()
    {
        "clang"
    } else {
        base
    };
    ESSENTIAL_BINARIES.contains(&base)
}

/// Find the LLVM root directory (the one containing a `bin/` subdirectory).
pub fn find_llvm_root(source_dir: &Path) -> Option<PathBuf> {
    if source_dir.join("bin").exists() {
        return Some(source_dir.to_path_buf());
    }
    // Check one level of subdirectories
    if let Ok(entries) = fs::read_dir(source_dir) {
        for entry in entries.flatten() {
            if entry.path().join("bin").exists() {
                return Some(entry.path());
            }
        }
    }
    None
}

/// Calculate total size of a directory recursively.
fn dir_size(path: &Path) -> u64 {
    WalkDir::new(path)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
        .map(|e| e.metadata().map(|m| m.len()).unwrap_or(0))
        .sum()
}

/// Copy essential files from LLVM root to output directory.
fn copy_essential_files(
    llvm_root: &Path,
    output_dir: &Path,
    config: &StripConfig,
) -> Result<(u64, u64)> {
    let mut kept = 0u64;
    let mut removed = 0u64;

    fs::create_dir_all(output_dir)?;

    // 1. Copy bin/ directory (filtered to essential binaries only)
    let src_bin = llvm_root.join("bin");
    if src_bin.exists() {
        let dst_bin = output_dir.join("bin");
        fs::create_dir_all(&dst_bin)?;

        for entry in fs::read_dir(&src_bin)? {
            let entry = entry?;
            if entry.file_type()?.is_file() {
                if is_essential_binary(&entry.file_name().to_string_lossy()) {
                    fs::copy(entry.path(), dst_bin.join(entry.file_name()))?;
                    kept += 1;
                    if config.verbose {
                        println!("  Keeping: {}", entry.file_name().to_string_lossy());
                    }
                } else {
                    removed += 1;
                    if config.verbose {
                        println!("  Removing: {}", entry.file_name().to_string_lossy());
                    }
                }
            }
        }
    }

    // 2. Copy lib/ directory (keep clang runtime, dynamic libs; remove static libs and cmake)
    for lib_dir_name in &["lib", "lib64"] {
        let src_lib = llvm_root.join(lib_dir_name);
        if !src_lib.exists() {
            continue;
        }
        let dst_lib = output_dir.join(lib_dir_name);
        fs::create_dir_all(&dst_lib)?;

        for entry in fs::read_dir(&src_lib)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().to_string();

            if entry.file_type()?.is_dir() {
                // Keep the clang runtime directory
                if name == "clang" {
                    copy_dir_recursive(&entry.path(), &dst_lib.join(&name))?;
                    kept += 1;
                }
                // Skip other directories
            } else if entry.file_type()?.is_file() {
                let is_dynamic = name.ends_with(".so")
                    || name.contains(".so.")
                    || name.ends_with(".dll")
                    || name.ends_with(".dylib");
                let is_removable = REMOVE_LIB_EXTENSIONS.iter().any(|ext| name.ends_with(ext));

                if is_dynamic {
                    fs::copy(entry.path(), dst_lib.join(&name))?;
                    kept += 1;
                } else if is_removable {
                    removed += 1;
                } else {
                    // Keep other files
                    fs::copy(entry.path(), dst_lib.join(&name))?;
                    kept += 1;
                }
            }
        }
    }

    // 3. Copy include/ only if keep_headers is true
    if config.keep_headers {
        let src_include = llvm_root.join("include");
        if src_include.exists() {
            copy_dir_recursive(&src_include, &output_dir.join("include"))?;
        }
    }

    // 4. Copy LICENSE/README/NOTICE files
    for entry in fs::read_dir(llvm_root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if entry.file_type()?.is_file()
            && (name.starts_with("LICENSE")
                || name.starts_with("README")
                || name.starts_with("NOTICE"))
        {
            fs::copy(entry.path(), output_dir.join(&name))?;
        }
    }

    Ok((kept, removed))
}

/// Recursively copy a directory.
fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    fs::create_dir_all(dst)?;
    for entry in WalkDir::new(src) {
        let entry = entry?;
        let relative = entry
            .path()
            .strip_prefix(src)
            .context("failed to strip prefix during recursive copy")?;
        let target = dst.join(relative);
        if entry.file_type().is_dir() {
            fs::create_dir_all(&target)?;
        } else {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(entry.path(), &target)?;
        }
    }
    Ok(())
}

/// Strip debug symbols from binaries in the output directory.
///
/// On Linux: uses `strip --strip-all` or `llvm-strip --strip-all`
/// On Windows: uses `llvm-strip.exe --strip-all`
/// On macOS: uses `strip -x` or `llvm-strip --strip-all`
fn strip_debug_symbols(output_dir: &Path, config: &StripConfig) -> Result<u64> {
    if !config.strip_debug {
        return Ok(0);
    }

    let bin_dir = output_dir.join("bin");
    if !bin_dir.exists() {
        return Ok(0);
    }

    let mut stripped = 0u64;

    // Try to find llvm-strip in the output
    let llvm_strip = if config.target.platform == Platform::Win {
        bin_dir.join("llvm-strip.exe")
    } else {
        bin_dir.join("llvm-strip")
    };

    for entry in fs::read_dir(&bin_dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_file() {
            continue;
        }
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();

        // Determine if this file should be stripped
        let should_strip = match config.target.platform {
            Platform::Win => name.ends_with(".exe") || name.ends_with(".dll"),
            _ => {
                // On Unix: known extensions or extensionless files (executables)
                name.ends_with(".so") || name.ends_with(".dylib") || !name.contains('.')
            }
        };

        if !should_strip {
            continue;
        }

        // Don't strip llvm-strip itself while using it
        if path == llvm_strip {
            continue;
        }

        let cmd_result = if llvm_strip.exists() {
            Command::new(&llvm_strip)
                .args(["--strip-all", &path.to_string_lossy()])
                .output()
        } else if config.target.platform != Platform::Win {
            Command::new("strip")
                .args(["--strip-all", &path.to_string_lossy()])
                .output()
        } else {
            continue; // No strip tool available on Windows without llvm-strip
        };

        match cmd_result {
            Ok(output) if output.status.success() => {
                stripped += 1;
                if config.verbose {
                    println!("  Stripped: {name}");
                }
            }
            Ok(output) => {
                if config.verbose {
                    println!(
                        "  Failed to strip {name}: {}",
                        String::from_utf8_lossy(&output.stderr)
                    );
                }
            }
            Err(e) => {
                if config.verbose {
                    println!("  Error stripping {name}: {e}");
                }
            }
        }
    }

    Ok(stripped)
}

/// Main entry point: strip an LLVM distribution.
///
/// Finds the LLVM root in `source_dir`, copies only essential files to
/// `output_dir`, and optionally strips debug symbols from binaries.
pub fn strip_llvm(
    source_dir: &Path,
    output_dir: &Path,
    config: &StripConfig,
) -> Result<StripStats> {
    let llvm_root = find_llvm_root(source_dir).ok_or_else(|| {
        anyhow::anyhow!(
            "Could not find LLVM root (directory with bin/) in {}",
            source_dir.display()
        )
    })?;

    println!("Found LLVM root: {}", llvm_root.display());

    let original_size = dir_size(&llvm_root);
    println!("Original size: {}", ctcb_core::format_size(original_size));

    println!("Copying essential files...");
    let (kept, removed) = copy_essential_files(&llvm_root, output_dir, config)?;

    if config.strip_debug {
        println!("Stripping debug symbols...");
        let stripped_count = strip_debug_symbols(output_dir, config)?;
        println!("Stripped {stripped_count} binaries");
    }

    let final_size = dir_size(output_dir);
    let savings = original_size.saturating_sub(final_size);
    let pct = if original_size > 0 {
        savings as f64 / original_size as f64 * 100.0
    } else {
        0.0
    };

    ctcb_core::print_section("Statistics");
    println!(
        "Original size:  {:>10}",
        ctcb_core::format_size(original_size)
    );
    println!("Final size:     {:>10}", ctcb_core::format_size(final_size));
    println!(
        "Saved:          {:>10} ({:.1}%)",
        ctcb_core::format_size(savings),
        pct
    );
    println!("Files kept:     {:>10}", kept);
    println!("Files removed:  {:>10}", removed);

    Ok(StripStats {
        original_size,
        final_size,
        files_kept: kept,
        files_removed: removed,
    })
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use ctcb_core::Arch;
    use tempfile::TempDir;

    fn create_fake_llvm(dir: &Path) {
        let bin = dir.join("bin");
        fs::create_dir_all(&bin).unwrap();
        // Essential
        fs::write(bin.join("clang.exe"), b"fake clang").unwrap();
        fs::write(bin.join("lld.exe"), b"fake lld").unwrap();
        fs::write(bin.join("llvm-ar.exe"), b"fake ar").unwrap();
        // Non-essential
        fs::write(bin.join("bugpoint.exe"), b"fake bugpoint").unwrap();
        fs::write(bin.join("llvm-reduce.exe"), b"fake reduce").unwrap();

        let lib = dir.join("lib");
        fs::create_dir_all(&lib).unwrap();
        fs::write(lib.join("libclang.a"), b"static lib").unwrap();
        fs::write(lib.join("libclang.dll"), b"dynamic lib").unwrap();

        fs::write(dir.join("LICENSE.TXT"), b"license").unwrap();
    }

    #[test]
    fn test_is_essential_binary() {
        assert!(is_essential_binary("clang.exe"));
        assert!(is_essential_binary("clang"));
        assert!(is_essential_binary("lld"));
        assert!(is_essential_binary("llvm-ar.exe"));
        assert!(!is_essential_binary("bugpoint.exe"));
        assert!(!is_essential_binary("llvm-reduce"));
    }

    #[test]
    fn test_is_essential_versioned_clang() {
        assert!(is_essential_binary("clang-19"));
        assert!(is_essential_binary("clang-19.1"));
    }

    #[test]
    fn test_find_llvm_root_direct() {
        let tmp = TempDir::new().unwrap();
        fs::create_dir_all(tmp.path().join("bin")).unwrap();
        assert_eq!(find_llvm_root(tmp.path()), Some(tmp.path().to_path_buf()));
    }

    #[test]
    fn test_find_llvm_root_nested() {
        let tmp = TempDir::new().unwrap();
        fs::create_dir_all(tmp.path().join("llvm-19/bin")).unwrap();
        assert!(find_llvm_root(tmp.path()).is_some());
    }

    #[test]
    fn test_strip_llvm_keeps_essentials() {
        let tmp = TempDir::new().unwrap();
        let source = tmp.path().join("source");
        let output = tmp.path().join("output");
        fs::create_dir_all(&source).unwrap();
        create_fake_llvm(&source);

        let config = StripConfig {
            target: Target::new(Platform::Win, Arch::X86_64),
            keep_headers: false,
            strip_debug: false, // Don't try to run strip in tests
            verbose: false,
        };

        let stats = strip_llvm(&source, &output, &config).unwrap();

        // Essential binaries should exist
        assert!(output.join("bin/clang.exe").exists());
        assert!(output.join("bin/lld.exe").exists());
        assert!(output.join("bin/llvm-ar.exe").exists());

        // Non-essential should NOT exist
        assert!(!output.join("bin/bugpoint.exe").exists());
        assert!(!output.join("bin/llvm-reduce.exe").exists());

        // Static libs should be removed, dynamic kept
        assert!(!output.join("lib/libclang.a").exists());
        assert!(output.join("lib/libclang.dll").exists());

        // License should be kept
        assert!(output.join("LICENSE.TXT").exists());

        assert!(stats.files_kept > 0);
        assert!(stats.files_removed > 0);
    }
}
