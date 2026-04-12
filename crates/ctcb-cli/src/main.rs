use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{bail, Context};
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "ctcb", version, about = "Clang Toolchain Binary Builder")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Expand a .tar.zst archive
    Expand {
        /// Path to .tar.zst archive
        archive: PathBuf,
        /// Output directory
        output_dir: PathBuf,
        /// SHA256 hash to verify archive before extraction
        #[arg(long)]
        verify: Option<String>,
        /// Keep hard links instead of converting to independent files
        #[arg(long)]
        keep_hardlinks: bool,
    },
    /// Deduplicate binaries by hash
    Dedup {
        #[command(subcommand)]
        action: DedupAction,
    },
    /// Strip non-essential files from toolchain
    Strip {
        /// Directory containing extracted LLVM binaries
        source_dir: PathBuf,
        /// Directory to output stripped binaries
        output_dir: PathBuf,
        /// Platform
        #[arg(long)]
        platform: String,
        /// Architecture
        #[arg(long, default_value = "x86_64")]
        arch: String,
        /// Keep header files
        #[arg(long)]
        keep_headers: bool,
        /// Don't strip debug symbols
        #[arg(long)]
        no_strip: bool,
        /// Verbose output
        #[arg(short, long)]
        verbose: bool,
    },
    /// Create hard-link archive from deduplicated structure
    HardlinkArchive {
        /// Deduped directory (containing dedup_manifest.json and canonical/)
        deduped_dir: PathBuf,
        /// Output directory for the archive
        output_dir: PathBuf,
        /// Archive base name
        #[arg(long, default_value = "win_binaries")]
        name: String,
        /// Zstd compression level (1-22)
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
    /// Download LLVM toolchain binaries
    Download {
        /// LLVM version to download
        #[arg(long, default_value = ctcb_download::DEFAULT_LLVM_VERSION)]
        version: String,
        /// Output directory
        #[arg(long, default_value = "downloads")]
        output: PathBuf,
        /// Specific platforms to download (can repeat)
        #[arg(long)]
        platform: Vec<String>,
        /// Only download for current platform
        #[arg(long)]
        current_only: bool,
        /// Skip checksum verification
        #[arg(long)]
        no_verify: bool,
    },
    /// Benchmark compression methods
    BenchCompression {
        /// Directory to compress
        directory: PathBuf,
        /// Output file prefix
        #[arg(default_value = "compressed")]
        output_prefix: String,
    },
    /// Split archive for LFS limits
    Split {
        /// Archive to split
        archive: PathBuf,
        /// Maximum part size in MB
        #[arg(long, default_value = "95")]
        part_size_mb: u64,
        /// Output directory for parts
        #[arg(long)]
        output_dir: Option<PathBuf>,
    },
    /// Fetch and archive LLVM/Clang toolchain (master pipeline)
    Fetch {
        /// Target platform (win, linux, darwin)
        #[arg(long)]
        platform: String,
        /// Target architecture (x86_64, arm64)
        #[arg(long)]
        arch: String,
        /// LLVM version
        #[arg(long, default_value = ctcb_download::DEFAULT_LLVM_VERSION)]
        version: String,
        /// Use existing extracted binaries instead of downloading
        #[arg(long)]
        source_dir: Option<PathBuf>,
        /// Working directory for intermediate files
        #[arg(long, default_value = "work")]
        work_dir: PathBuf,
        /// Output directory (default: assets/clang/{platform}/{arch})
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Zstd compression level (1-22)
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
    /// Create Include-What-You-Use archives
    Iwyu {
        /// Root directory containing pre-extracted IWYU binaries
        #[arg(long, default_value = "downloads-bins/assets/iwyu")]
        iwyu_root: PathBuf,
        /// IWYU version string
        #[arg(long, default_value = "0.25")]
        version: String,
        /// Zstd compression level
        #[arg(long, default_value = "22")]
        zstd_level: i32,
        /// Specific platform to process
        #[arg(long)]
        platform: Option<String>,
        /// Specific architecture to process
        #[arg(long)]
        arch: Option<String>,
    },
    /// Extract MinGW sysroot from LLVM-MinGW release
    MingwSysroot {
        /// Target architecture
        #[arg(long)]
        arch: String,
        /// Working directory
        #[arg(long, default_value = "work")]
        work_dir: PathBuf,
        /// Output directory
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Skip download, use existing archive
        #[arg(long)]
        skip_download: bool,
        /// LLVM-MinGW version tag
        #[arg(long, default_value = "20251104")]
        llvm_mingw_version: String,
        /// Zstd compression level
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
    /// Package Emscripten via native emsdk
    Emscripten {
        /// Target platform
        #[arg(long)]
        platform: String,
        /// Target architecture
        #[arg(long)]
        arch: String,
        /// Working directory
        #[arg(long, default_value = "work")]
        work_dir: PathBuf,
        /// Output directory
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Zstd compression level
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
    /// Package Emscripten via Docker
    EmscriptenDocker {
        /// Target platform
        #[arg(long)]
        platform: String,
        /// Target architecture
        #[arg(long)]
        arch: String,
        /// Working directory
        #[arg(long, default_value = "work")]
        work_dir: PathBuf,
        /// Output directory
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Zstd compression level
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
    /// Package Node.js runtime
    Nodejs {
        /// Target platform
        #[arg(long)]
        platform: String,
        /// Target architecture
        #[arg(long)]
        arch: String,
        /// Node.js version
        #[arg(long, default_value = "22.11.0")]
        version: String,
        /// Working directory
        #[arg(long, default_value = "work")]
        work_dir: PathBuf,
        /// Output directory
        #[arg(long)]
        output_dir: Option<PathBuf>,
        /// Zstd compression level
        #[arg(long, default_value = "22")]
        zstd_level: i32,
    },
}

#[derive(Subcommand)]
enum DedupAction {
    /// Analyze directory for duplicate files
    Analyze {
        /// Directory to analyze
        directory: PathBuf,
    },
    /// Create deduplicated structure
    Create {
        /// Source directory with files
        source_dir: PathBuf,
        /// Destination for deduped structure
        dest_dir: PathBuf,
    },
    /// Expand deduped structure back to full
    Expand {
        /// Deduped directory (containing dedup_manifest.json)
        deduped_dir: PathBuf,
        /// Output directory
        output_dir: PathBuf,
    },
}

/// Recursively compute the total size of all files under `dir`.
fn dir_size(dir: &std::path::Path) -> anyhow::Result<u64> {
    let mut total = 0u64;
    if dir.is_dir() {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                total += dir_size(&path)?;
            } else {
                total += entry.metadata().map(|m| m.len()).unwrap_or(0);
            }
        }
    }
    Ok(total)
}

/// Discover platform/arch subdirectory combos (e.g. "win/x86_64", "linux/arm64")
fn discover_platform_arch_dirs(
    root: &Path,
    filter_platform: Option<&str>,
    filter_arch: Option<&str>,
) -> anyhow::Result<Vec<(String, String, PathBuf)>> {
    let mut combos = Vec::new();

    if !root.exists() {
        anyhow::bail!("Root directory not found: {}", root.display());
    }

    for plat_entry in std::fs::read_dir(root)? {
        let plat_entry = plat_entry?;
        if !plat_entry.file_type()?.is_dir() {
            continue;
        }
        let plat = plat_entry.file_name().to_string_lossy().to_string();

        // Skip non-platform directories
        if !["win", "linux", "darwin"].contains(&plat.as_str()) {
            continue;
        }

        if let Some(fp) = filter_platform {
            if plat != fp {
                continue;
            }
        }

        for arch_entry in std::fs::read_dir(plat_entry.path())? {
            let arch_entry = arch_entry?;
            if !arch_entry.file_type()?.is_dir() {
                continue;
            }
            let ar = arch_entry.file_name().to_string_lossy().to_string();

            if let Some(fa) = filter_arch {
                if ar != fa {
                    continue;
                }
            }

            combos.push((plat.clone(), ar, arch_entry.path()));
        }
    }

    if combos.is_empty() {
        anyhow::bail!(
            "No platform/arch directories found in {}",
            root.display()
        );
    }

    combos.sort();
    Ok(combos)
}

/// Find the sysroot directory for a target triple inside a MinGW installation
fn find_sysroot(search_dir: &Path, target_triple: &str) -> anyhow::Result<PathBuf> {
    for entry in walkdir::WalkDir::new(search_dir).max_depth(3) {
        let entry = entry?;
        if entry.file_type().is_dir() && entry.file_name().to_string_lossy() == target_triple {
            return Ok(entry.path().to_path_buf());
        }
    }
    anyhow::bail!(
        "Sysroot for {} not found in {}",
        target_triple,
        search_dir.display()
    )
}

/// Detect the installed Emscripten version from the emsdk directory
fn detect_emscripten_version(emsdk_dir: &Path) -> anyhow::Result<String> {
    // Try reading from upstream/emscripten/emscripten-version.txt
    let version_file = emsdk_dir.join("upstream/emscripten/emscripten-version.txt");
    if version_file.exists() {
        let version = std::fs::read_to_string(&version_file)?
            .trim()
            .trim_matches('"')
            .to_string();
        return Ok(version);
    }
    // Fallback: check .emscripten_version
    let alt = emsdk_dir.join(".emscripten_version");
    if alt.exists() {
        return Ok(std::fs::read_to_string(&alt)?.trim().to_string());
    }
    anyhow::bail!("Could not detect Emscripten version")
}

/// Find the Node.js root directory (the one containing bin/node or node.exe)
fn find_node_root(search_dir: &Path) -> anyhow::Result<PathBuf> {
    // Check the search_dir itself
    if search_dir.join("bin/node").exists() || search_dir.join("node.exe").exists() {
        return Ok(search_dir.to_path_buf());
    }
    // Check immediate children
    for entry in std::fs::read_dir(search_dir)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            let path = entry.path();
            if path.join("bin/node").exists() || path.join("node.exe").exists() {
                return Ok(path);
            }
        }
    }
    anyhow::bail!("Node.js root not found in {}", search_dir.display())
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Expand {
            archive,
            output_dir,
            verify,
            keep_hardlinks: _,
        } => {
            // Section header
            ctcb_core::print_section("EXPANDING ARCHIVE");

            // Print archive info
            let archive_meta = std::fs::metadata(&archive)
                .with_context(|| format!("archive not found: {}", archive.display()))?;
            println!("Archive: {}", archive.display());
            println!("Output:  {}", output_dir.display());
            println!("Size:    {}", ctcb_core::format_size(archive_meta.len()));
            println!();

            // Verify SHA256 if requested
            if let Some(expected_hash) = &verify {
                print!("Verifying SHA256... ");
                let ok = ctcb_checksum::sha256_verify(&archive, expected_hash)
                    .context("failed to compute SHA256 of archive")?;
                if ok {
                    println!("OK");
                } else {
                    let actual = ctcb_checksum::sha256_file(&archive)?;
                    println!("MISMATCH");
                    bail!(
                        "SHA256 verification failed\n  expected: {}\n  actual:   {}",
                        expected_hash.to_lowercase(),
                        actual
                    );
                }
                println!();
            }

            // Create output directory
            std::fs::create_dir_all(&output_dir)
                .with_context(|| format!("failed to create output dir: {}", output_dir.display()))?;

            // Extract
            println!("Extracting...");
            let start = Instant::now();
            ctcb_archive::extract_tar_zst(&archive, &output_dir)
                .context("extraction failed")?;
            let elapsed = start.elapsed();

            // Compute decompressed size
            let decompressed_size = dir_size(&output_dir).unwrap_or(0);

            // Completion stats
            println!();
            ctcb_core::print_section("EXTRACTION COMPLETE");
            println!("Location: {}", output_dir.display());
            println!("Time:     {}", ctcb_core::format_duration(elapsed));
            println!(
                "Size:     {} -> {}",
                ctcb_core::format_size(archive_meta.len()),
                ctcb_core::format_size(decompressed_size)
            );

            Ok(())
        }
        Commands::Dedup { action } => match action {
            DedupAction::Analyze { directory } => {
                let analysis = ctcb_dedup::analyze_directory(&directory)?;
                ctcb_dedup::print_analysis(&analysis);
                Ok(())
            }
            DedupAction::Create {
                source_dir,
                dest_dir,
            } => {
                println!("Creating deduplicated structure...");
                let manifest =
                    ctcb_dedup::create_deduped_structure(&source_dir, &dest_dir)?;
                println!("\nDeduplication complete!");
                println!(
                    "Original size:  {}",
                    ctcb_core::format_size(manifest.stats.total_size)
                );
                println!(
                    "Deduped size:   {}",
                    ctcb_core::format_size(manifest.stats.deduped_size)
                );
                println!(
                    "Saved:          {}",
                    ctcb_core::format_size(manifest.stats.savings)
                );
                println!(
                    "Manifest saved to: {}/dedup_manifest.json",
                    dest_dir.display()
                );
                Ok(())
            }
            DedupAction::Expand {
                deduped_dir,
                output_dir,
            } => {
                ctcb_dedup::expand_deduped_structure(&deduped_dir, &output_dir)?;
                Ok(())
            }
        },
        Commands::Strip {
            source_dir,
            output_dir,
            platform,
            arch,
            keep_headers,
            no_strip,
            verbose,
        } => {
            let target = ctcb_core::Target::new(
                ctcb_core::Platform::from_str_loose(&platform)?,
                ctcb_core::Arch::from_str_loose(&arch)?,
            );
            let config = ctcb_strip::StripConfig {
                target,
                keep_headers,
                strip_debug: !no_strip,
                verbose,
            };
            ctcb_strip::strip_llvm(&source_dir, &output_dir, &config)?;
            Ok(())
        }
        Commands::HardlinkArchive {
            deduped_dir,
            output_dir,
            name,
            zstd_level,
        } => {
            ctcb_core::print_section("CREATE HARDLINK ARCHIVE");

            std::fs::create_dir_all(&output_dir)?;

            // Step 1: Create hardlink structure
            println!("Step 1: Creating hardlink structure...");
            let hardlink_dir = output_dir.join("_hardlinked");
            ctcb_dedup::create_hardlink_structure(&deduped_dir, &hardlink_dir)?;

            // Step 2: Verify hardlinks
            let (total, unique) = ctcb_dedup::verify_hardlinks(&hardlink_dir)?;
            println!("Hardlinks verified: {} files, {} unique", total, unique);

            // Step 3: Create tar.zst
            let archive_path = output_dir.join(format!("{}.tar.zst", name));
            println!("\nStep 3: Creating tar.zst archive...");
            let start = Instant::now();
            ctcb_archive::create_tar_zst(&hardlink_dir, &archive_path, zstd_level)?;
            let elapsed = start.elapsed();

            // Step 4: Generate checksums
            ctcb_checksum::generate_checksum_files(&archive_path)?;

            // Step 5: Clean up
            std::fs::remove_dir_all(&hardlink_dir)?;

            let size = std::fs::metadata(&archive_path)?.len();
            ctcb_core::print_section("SUCCESS");
            println!("Archive: {}", archive_path.display());
            println!("Size:    {}", ctcb_core::format_size(size));
            println!("Time:    {}", ctcb_core::format_duration(elapsed));

            Ok(())
        }
        Commands::Download {
            version,
            output,
            platform,
            current_only,
            no_verify,
        } => {
            let platforms = if current_only {
                let current = ctcb_download::detect_current_platform()
                    .ok_or_else(|| anyhow::anyhow!("Could not detect current platform"))?;
                println!("Detected current platform: {}", current);
                Some(vec![current.to_string()])
            } else if !platform.is_empty() {
                Some(platform)
            } else {
                None
            };

            ctcb_download::download_all(
                &version,
                platforms.as_deref(),
                &output,
                !no_verify,
            )?;

            Ok(())
        }
        Commands::BenchCompression { directory, output_prefix } => {
            ctcb_core::print_section("COMPRESSION BENCHMARKS");
            ctcb_archive::bench::run_benchmarks(&directory, &output_prefix)?;
            Ok(())
        }
        Commands::Split { archive, part_size_mb, output_dir } => {
            let out = output_dir.unwrap_or_else(|| archive.parent().unwrap_or(Path::new(".")).to_path_buf());
            ctcb_core::print_section("SPLITTING ARCHIVE");
            ctcb_split::split_archive(&archive, part_size_mb, &out)?;
            Ok(())
        }
        Commands::Fetch {
            platform,
            arch,
            version,
            source_dir,
            work_dir,
            output_dir,
            zstd_level,
        } => {
            let plat = ctcb_core::Platform::from_str_loose(&platform)?;
            let ar = ctcb_core::Arch::from_str_loose(&arch)?;
            let target = ctcb_core::Target::new(plat, ar);

            let output = output_dir.unwrap_or_else(|| {
                PathBuf::from(format!("assets/clang/{}/{}", plat, ar))
            });

            let total_start = Instant::now();

            ctcb_core::print_section(&format!(
                "FETCH AND ARCHIVE LLVM {} for {}",
                version, target
            ));

            // Step 1: Get source binaries (download + extract, or use existing)
            ctcb_core::print_section("STEP 1: SOURCE BINARIES");
            let extracted_dir = if let Some(src) = source_dir {
                println!("Using existing source: {}", src.display());
                src
            } else {
                println!("Downloading LLVM {}...", version);
                // Map target to download platform key
                // The download crate uses "linux-aarch64" while Arch::Arm64 displays as "arm64"
                let dl_key = match (plat, ar) {
                    (ctcb_core::Platform::Linux, ctcb_core::Arch::Arm64) => {
                        "linux-aarch64".to_string()
                    }
                    (ctcb_core::Platform::Darwin, ctcb_core::Arch::Arm64) => {
                        "darwin-arm64".to_string()
                    }
                    _ => format!("{}-{}", plat, ar),
                };
                std::fs::create_dir_all(&work_dir).with_context(|| {
                    format!("failed to create work dir: {}", work_dir.display())
                })?;
                ctcb_download::download_platform(&version, &dl_key, &work_dir, true)?
            };

            // Step 2: Strip non-essential files
            ctcb_core::print_section("STEP 2: STRIP BINARIES");
            let stripped_dir = work_dir.join("stripped");
            let strip_config = ctcb_strip::StripConfig {
                target,
                keep_headers: false,
                strip_debug: target.platform != ctcb_core::Platform::Win,
                verbose: false,
            };
            let strip_stats =
                ctcb_strip::strip_llvm(&extracted_dir, &stripped_dir, &strip_config)?;
            println!(
                "Kept {} files, removed {} files",
                strip_stats.files_kept, strip_stats.files_removed
            );

            // Step 3: Deduplicate
            ctcb_core::print_section("STEP 3: DEDUPLICATE");
            let deduped_dir = work_dir.join("deduped");
            let manifest =
                ctcb_dedup::create_deduped_structure(&stripped_dir, &deduped_dir)?;
            println!(
                "Saved: {} ({:.1}%)",
                ctcb_core::format_size(manifest.stats.savings),
                manifest.stats.savings_percent
            );

            // Step 4: Create hardlink structure
            ctcb_core::print_section("STEP 4: CREATE HARDLINK STRUCTURE");
            let hardlink_dir = work_dir.join("hardlinked");
            ctcb_dedup::create_hardlink_structure(&deduped_dir, &hardlink_dir)?;
            let (total_files, unique) = ctcb_dedup::verify_hardlinks(&hardlink_dir)?;
            println!(
                "{} files, {} unique (saved {} via hardlinks)",
                total_files,
                unique,
                total_files - unique
            );

            // Step 5: Create tar.zst archive
            ctcb_core::print_section("STEP 5: COMPRESS");
            let archive_name =
                format!("llvm-{}-{}-{}.tar.zst", version, plat, ar);
            std::fs::create_dir_all(&output)?;
            let archive_path = output.join(&archive_name);
            let compress_start = Instant::now();
            ctcb_archive::create_tar_zst(&hardlink_dir, &archive_path, zstd_level)?;
            let compress_time = compress_start.elapsed();
            let archive_size = std::fs::metadata(&archive_path)?.len();
            println!(
                "Archive: {} ({})",
                archive_path.display(),
                ctcb_core::format_size(archive_size)
            );
            println!(
                "Compression time: {}",
                ctcb_core::format_duration(compress_time)
            );

            // Step 6: Generate checksums
            ctcb_core::print_section("STEP 6: CHECKSUMS");
            ctcb_checksum::generate_checksum_files(&archive_path)?;
            let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
            println!("SHA256: {}", sha256);

            // Step 7: Split if needed (>99 MB for Git LFS)
            let parts = if archive_size > 99 * 1024 * 1024 {
                ctcb_core::print_section("STEP 7: SPLIT ARCHIVE");
                let split_parts =
                    ctcb_split::split_archive(&archive_path, 95, &output)?;
                if split_parts.is_empty() {
                    None
                } else {
                    Some(split_parts)
                }
            } else {
                println!("Archive is under 99 MB, no splitting needed.");
                None
            };

            // Step 8: Update manifests
            ctcb_core::print_section("STEP 8: UPDATE MANIFESTS");
            let relative_path =
                format!("clang/{}/{}/{}", plat, ar, archive_name);
            let href = ctcb_manifest::lfs_media_url(&relative_path, "main");

            let part_refs: Option<Vec<ctcb_manifest::PartRef>> =
                parts.map(|p| {
                    p.iter()
                        .map(|part| {
                            let part_name = part
                                .path
                                .file_name()
                                .unwrap()
                                .to_string_lossy()
                                .to_string();
                            let part_relative =
                                format!("clang/{}/{}/{}", plat, ar, part_name);
                            ctcb_manifest::PartRef {
                                href: ctcb_manifest::lfs_media_url(
                                    &part_relative,
                                    "main",
                                ),
                                sha256: part.sha256.clone(),
                            }
                        })
                        .collect()
                });

            let platform_manifest_path = output.join("manifest.json");
            ctcb_manifest::update_platform_manifest(
                &platform_manifest_path,
                &version,
                &href,
                &sha256,
                part_refs,
            )?;
            println!("Updated: {}", platform_manifest_path.display());

            // Update root manifest
            let root_manifest_path = PathBuf::from("assets/clang/manifest.json");
            let arch_manifest_relative =
                format!("{}/{}/manifest.json", plat, ar);
            ctcb_manifest::ensure_root_entry(
                &root_manifest_path,
                &plat.to_string(),
                &ar.to_string(),
                &arch_manifest_relative,
            )?;
            println!("Updated: {}", root_manifest_path.display());

            // Summary
            let total_time = total_start.elapsed();
            ctcb_core::print_section("COMPLETE");
            println!("Archive:  {}", archive_path.display());
            println!("Size:     {}", ctcb_core::format_size(archive_size));
            println!("SHA256:   {}", sha256);
            println!("Time:     {}", ctcb_core::format_duration(total_time));

            Ok(())
        }

        // -----------------------------------------------------------------
        // IWYU — Create Include-What-You-Use archives
        // -----------------------------------------------------------------
        Commands::Iwyu {
            iwyu_root,
            version,
            zstd_level,
            platform,
            arch,
        } => {
            ctcb_core::print_section(&format!("CREATE IWYU ARCHIVES v{}", version));

            // Discover platform/arch combos
            let combos = discover_platform_arch_dirs(
                &iwyu_root,
                platform.as_deref(),
                arch.as_deref(),
            )?;

            for (plat, ar, source_dir) in &combos {
                ctcb_core::print_section(&format!("Processing IWYU {}-{}", plat, ar));

                let output_dir = PathBuf::from(format!("assets/iwyu/{}/{}", plat, ar));
                std::fs::create_dir_all(&output_dir)?;

                let archive_name = format!("iwyu-{}-{}-{}.tar.zst", version, plat, ar);
                let archive_path = output_dir.join(&archive_name);

                ctcb_archive::create_tar_zst(source_dir, &archive_path, zstd_level)?;
                ctcb_checksum::generate_checksum_files(&archive_path)?;

                let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
                let relative = format!("iwyu/{}/{}/{}", plat, ar, archive_name);
                let href = ctcb_manifest::lfs_media_url(&relative, "main");

                ctcb_manifest::update_platform_manifest(
                    &output_dir.join("manifest.json"),
                    &version,
                    &href,
                    &sha256,
                    None,
                )?;

                let size = std::fs::metadata(&archive_path)?.len();
                println!(
                    "Created: {} ({})",
                    archive_path.display(),
                    ctcb_core::format_size(size)
                );
            }

            // Update root manifest
            let root_path = PathBuf::from("assets/iwyu/manifest.json");
            for (plat, ar, _) in &combos {
                let manifest_rel = format!("{}/{}/manifest.json", plat, ar);
                ctcb_manifest::ensure_root_entry(&root_path, plat, ar, &manifest_rel)?;
            }

            ctcb_core::print_section("COMPLETE");
            Ok(())
        }

        // -----------------------------------------------------------------
        // MinGW Sysroot — Extract from LLVM-MinGW release
        // -----------------------------------------------------------------
        Commands::MingwSysroot {
            arch,
            work_dir,
            output_dir,
            skip_download,
            llvm_mingw_version,
            zstd_level,
        } => {
            let ar = ctcb_core::Arch::from_str_loose(&arch)?;
            let output = output_dir.unwrap_or_else(|| PathBuf::from("assets/mingw/win"));

            ctcb_core::print_section(&format!("EXTRACT MINGW SYSROOT ({})", ar));

            let target_triple = match ar {
                ctcb_core::Arch::X86_64 => "x86_64-w64-mingw32",
                ctcb_core::Arch::Arm64 => "aarch64-w64-mingw32",
            };

            // Step 1: Download LLVM-MinGW if needed
            let archive_dir = work_dir.join("mingw-download");
            std::fs::create_dir_all(&archive_dir)?;

            if !skip_download {
                let url = format!(
                    "https://github.com/mstorsjo/llvm-mingw/releases/download/{}/llvm-mingw-{}-ucrt-x86_64.tar.xz",
                    llvm_mingw_version, llvm_mingw_version
                );
                let archive_path = archive_dir.join(format!(
                    "llvm-mingw-{}.tar.xz",
                    llvm_mingw_version
                ));
                if !archive_path.exists() {
                    ctcb_download::download_file(&url, &archive_path)?;
                }
                ctcb_download::extract_tar_xz(&archive_path, &archive_dir)?;
            }

            // Step 2: Find and copy sysroot
            let sysroot_src = find_sysroot(&archive_dir, target_triple)?;
            println!("Found sysroot: {}", sysroot_src.display());

            // Step 3: Create tar.zst archive
            std::fs::create_dir_all(&output)?;
            let archive_name = format!(
                "mingw-sysroot-{}-{}.tar.zst",
                llvm_mingw_version, ar
            );
            let archive_path = output.join(&archive_name);

            ctcb_archive::create_tar_zst(&sysroot_src, &archive_path, zstd_level)?;
            ctcb_checksum::generate_checksum_files(&archive_path)?;

            let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
            let size = std::fs::metadata(&archive_path)?.len();

            // Update manifest
            let relative = format!("mingw/win/{}/{}", ar, archive_name);
            let href = ctcb_manifest::lfs_media_url(&relative, "main");
            ctcb_manifest::update_platform_manifest(
                &output.join("manifest.json"),
                &llvm_mingw_version,
                &href,
                &sha256,
                None,
            )?;

            ctcb_core::print_section("COMPLETE");
            println!(
                "Archive: {} ({})",
                archive_path.display(),
                ctcb_core::format_size(size)
            );

            Ok(())
        }

        // -----------------------------------------------------------------
        // Emscripten — Package via native emsdk
        // -----------------------------------------------------------------
        Commands::Emscripten {
            platform,
            arch,
            work_dir,
            output_dir,
            zstd_level,
        } => {
            let plat = ctcb_core::Platform::from_str_loose(&platform)?;
            let ar = ctcb_core::Arch::from_str_loose(&arch)?;
            let output = output_dir.unwrap_or_else(|| {
                PathBuf::from(format!("assets/emscripten/{}/{}", plat, ar))
            });

            ctcb_core::print_section(&format!(
                "PACKAGE EMSCRIPTEN for {}-{}",
                plat, ar
            ));

            // Step 1: Clone emsdk
            let emsdk_dir = work_dir.join("emsdk");
            if !emsdk_dir.exists() {
                println!("Cloning emsdk...");
                let status = std::process::Command::new("git")
                    .args([
                        "clone",
                        "https://github.com/emscripten-core/emsdk.git",
                        &emsdk_dir.to_string_lossy(),
                    ])
                    .status()?;
                if !status.success() {
                    anyhow::bail!("git clone failed");
                }
            }

            // Step 2: Install latest
            println!("Installing latest Emscripten...");
            let emsdk_bin = if plat == ctcb_core::Platform::Win {
                "emsdk.bat"
            } else {
                "./emsdk"
            };
            let status = std::process::Command::new(emsdk_bin)
                .args(["install", "latest"])
                .current_dir(&emsdk_dir)
                .status()?;
            if !status.success() {
                anyhow::bail!("emsdk install failed");
            }

            let status = std::process::Command::new(emsdk_bin)
                .args(["activate", "latest"])
                .current_dir(&emsdk_dir)
                .status()?;
            if !status.success() {
                anyhow::bail!("emsdk activate failed");
            }

            // Step 3: Detect version
            let version =
                detect_emscripten_version(&emsdk_dir).unwrap_or_else(|_| "unknown".to_string());
            println!("Emscripten version: {}", version);

            // Step 4: Package upstream directory
            let upstream = emsdk_dir.join("upstream");
            if !upstream.exists() {
                anyhow::bail!("emsdk upstream directory not found");
            }

            std::fs::create_dir_all(&output)?;
            let archive_name = format!("emscripten-{}-{}-{}.tar.zst", version, plat, ar);
            let archive_path = output.join(&archive_name);

            ctcb_archive::create_tar_zst(&upstream, &archive_path, zstd_level)?;
            ctcb_checksum::generate_checksum_files(&archive_path)?;

            let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
            let size = std::fs::metadata(&archive_path)?.len();
            let relative = format!("emscripten/{}/{}/{}", plat, ar, archive_name);
            let href = ctcb_manifest::lfs_media_url(&relative, "main");

            ctcb_manifest::update_platform_manifest(
                &output.join("manifest.json"),
                &version,
                &href,
                &sha256,
                None,
            )?;

            ctcb_core::print_section("COMPLETE");
            println!(
                "Archive: {} ({})",
                archive_path.display(),
                ctcb_core::format_size(size)
            );

            Ok(())
        }

        // -----------------------------------------------------------------
        // Emscripten Docker — Package via Docker
        // -----------------------------------------------------------------
        Commands::EmscriptenDocker {
            platform,
            arch,
            work_dir,
            output_dir,
            zstd_level,
        } => {
            let plat = ctcb_core::Platform::from_str_loose(&platform)?;
            let ar = ctcb_core::Arch::from_str_loose(&arch)?;
            let output = output_dir.unwrap_or_else(|| {
                PathBuf::from(format!("assets/emscripten/{}/{}", plat, ar))
            });

            ctcb_core::print_section(&format!(
                "PACKAGE EMSCRIPTEN (Docker) for {}-{}",
                plat, ar
            ));

            // Step 1: Pull docker image
            println!("Pulling emscripten/emsdk:latest...");
            let status = std::process::Command::new("docker")
                .args(["pull", "emscripten/emsdk:latest"])
                .status()?;
            if !status.success() {
                anyhow::bail!("docker pull failed");
            }

            // Step 2: Create container and extract /emsdk/upstream
            println!("Extracting from Docker container...");
            let container_id_output = std::process::Command::new("docker")
                .args(["create", "emscripten/emsdk:latest"])
                .output()?;
            if !container_id_output.status.success() {
                anyhow::bail!("docker create failed");
            }
            let container_id =
                String::from_utf8_lossy(&container_id_output.stdout).trim().to_string();

            let extract_dir = work_dir.join("emsdk-docker");
            std::fs::create_dir_all(&extract_dir)?;

            let status = std::process::Command::new("docker")
                .args([
                    "cp",
                    &format!("{}:/emsdk/upstream", container_id),
                    &extract_dir.to_string_lossy(),
                ])
                .status()?;

            // Cleanup container
            let _ = std::process::Command::new("docker")
                .args(["rm", &container_id])
                .status();

            if !status.success() {
                anyhow::bail!("docker cp failed");
            }

            // Step 3: Detect version and archive
            let upstream = extract_dir.join("upstream");
            let version_file = upstream.join("emscripten/emscripten-version.txt");
            let version = if version_file.exists() {
                std::fs::read_to_string(&version_file)?
                    .trim()
                    .trim_matches('"')
                    .to_string()
            } else {
                "unknown".to_string()
            };
            println!("Emscripten version: {}", version);

            std::fs::create_dir_all(&output)?;
            let archive_name = format!("emscripten-{}-{}-{}.tar.zst", version, plat, ar);
            let archive_path = output.join(&archive_name);

            ctcb_archive::create_tar_zst(&upstream, &archive_path, zstd_level)?;
            ctcb_checksum::generate_checksum_files(&archive_path)?;

            let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
            let size = std::fs::metadata(&archive_path)?.len();
            let relative = format!("emscripten/{}/{}/{}", plat, ar, archive_name);
            let href = ctcb_manifest::lfs_media_url(&relative, "main");

            ctcb_manifest::update_platform_manifest(
                &output.join("manifest.json"),
                &version,
                &href,
                &sha256,
                None,
            )?;

            ctcb_core::print_section("COMPLETE");
            println!(
                "Archive: {} ({})",
                archive_path.display(),
                ctcb_core::format_size(size)
            );

            Ok(())
        }

        // -----------------------------------------------------------------
        // Node.js — Package Node.js runtime
        // -----------------------------------------------------------------
        Commands::Nodejs {
            platform,
            arch,
            version,
            work_dir,
            output_dir,
            zstd_level,
        } => {
            let plat = ctcb_core::Platform::from_str_loose(&platform)?;
            let ar = ctcb_core::Arch::from_str_loose(&arch)?;
            let output = output_dir.unwrap_or_else(|| {
                PathBuf::from(format!("assets/nodejs/{}/{}", plat, ar))
            });

            ctcb_core::print_section(&format!(
                "PACKAGE NODE.JS {} for {}-{}",
                version, plat, ar
            ));

            // Map to Node.js download naming conventions
            let (node_os, node_arch, ext) = match (plat, ar) {
                (ctcb_core::Platform::Win, ctcb_core::Arch::X86_64) => ("win", "x64", "zip"),
                (ctcb_core::Platform::Win, ctcb_core::Arch::Arm64) => ("win", "arm64", "zip"),
                (ctcb_core::Platform::Linux, ctcb_core::Arch::X86_64) => {
                    ("linux", "x64", "tar.xz")
                }
                (ctcb_core::Platform::Linux, ctcb_core::Arch::Arm64) => {
                    ("linux", "arm64", "tar.xz")
                }
                (ctcb_core::Platform::Darwin, ctcb_core::Arch::X86_64) => {
                    ("darwin", "x64", "tar.gz")
                }
                (ctcb_core::Platform::Darwin, ctcb_core::Arch::Arm64) => {
                    ("darwin", "arm64", "tar.gz")
                }
            };

            let filename = format!("node-v{}-{}-{}.{}", version, node_os, node_arch, ext);
            let url = format!("https://nodejs.org/dist/v{}/{}", version, filename);

            // Step 1: Download
            let download_dir = work_dir.join("nodejs-download");
            std::fs::create_dir_all(&download_dir)?;
            let download_path = download_dir.join(&filename);
            if !download_path.exists() {
                ctcb_download::download_file(&url, &download_path)?;
            }

            // Step 2: Extract
            let extract_dir = work_dir.join("nodejs-extracted");
            if extract_dir.exists() {
                std::fs::remove_dir_all(&extract_dir)?;
            }
            std::fs::create_dir_all(&extract_dir)?;
            if ext == "zip" {
                // Use 7z for zip extraction on Windows
                let status = std::process::Command::new("7z")
                    .args([
                        "x",
                        &download_path.to_string_lossy(),
                        &format!("-o{}", extract_dir.display()),
                        "-y",
                    ])
                    .status()?;
                if !status.success() {
                    anyhow::bail!("7z extraction failed");
                }
            } else {
                // tar.xz and tar.gz both handled by system tar
                ctcb_download::extract_tar_xz(&download_path, &extract_dir)?;
            }

            // Step 3: Find node root and strip non-essential files
            let node_root = find_node_root(&extract_dir)?;
            println!("Node.js root: {}", node_root.display());

            // Remove non-essential dirs
            for dir_name in &["include", "share"] {
                let d = node_root.join(dir_name);
                if d.exists() {
                    std::fs::remove_dir_all(&d)?;
                }
            }
            // Remove non-essential files
            for pattern in &["README.md", "CHANGELOG.md", "LICENSE"] {
                let f = node_root.join(pattern);
                if f.exists() {
                    std::fs::remove_file(&f)?;
                }
            }

            // Step 4: Archive
            std::fs::create_dir_all(&output)?;
            let archive_name = format!("nodejs-{}-{}-{}.tar.zst", version, plat, ar);
            let archive_path = output.join(&archive_name);

            ctcb_archive::create_tar_zst(&node_root, &archive_path, zstd_level)?;
            ctcb_checksum::generate_checksum_files(&archive_path)?;

            let sha256 = ctcb_checksum::sha256_file(&archive_path)?;
            let size = std::fs::metadata(&archive_path)?.len();
            let relative = format!("nodejs/{}/{}/{}", plat, ar, archive_name);
            let href = ctcb_manifest::lfs_media_url(&relative, "main");

            ctcb_manifest::update_platform_manifest(
                &output.join("manifest.json"),
                &version,
                &href,
                &sha256,
                None,
            )?;

            ctcb_core::print_section("COMPLETE");
            println!(
                "Archive: {} ({})",
                archive_path.display(),
                ctcb_core::format_size(size)
            );

            Ok(())
        }
    }
}
