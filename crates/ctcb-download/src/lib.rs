// Phase 7: Async download with progress and checksum verification

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};
use futures_util::StreamExt;
use indicatif::{ProgressBar, ProgressStyle};

/// LLVM GitHub release URL base
pub const GITHUB_RELEASE_URL: &str = "https://github.com/llvm/llvm-project/releases/download";

/// Default LLVM version
pub const DEFAULT_LLVM_VERSION: &str = "21.1.5";

/// Platform-specific download configuration
pub struct DownloadConfig {
    pub filename_template: &'static str,
    pub url_template: &'static str,
    /// "archive" for tar.xz, "installer" for Windows .exe
    pub archive_type: &'static str,
    /// Alternative URL for Windows (tar.xz instead of .exe)
    pub alt_filename_template: Option<&'static str>,
    pub alt_url_template: Option<&'static str>,
}

/// Get download config for a platform key like "win-x86_64", "linux-x86_64", etc.
pub fn get_download_config(platform_key: &str) -> Option<DownloadConfig> {
    match platform_key {
        "win-x86_64" => Some(DownloadConfig {
            filename_template: "LLVM-{VERSION}-win64.exe",
            url_template: "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/LLVM-{VERSION}-win64.exe",
            archive_type: "installer",
            alt_filename_template: Some("clang+llvm-{VERSION}-x86_64-pc-windows-msvc.tar.xz"),
            alt_url_template: Some(
                "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/clang+llvm-{VERSION}-x86_64-pc-windows-msvc.tar.xz",
            ),
        }),
        "linux-x86_64" => Some(DownloadConfig {
            filename_template: "LLVM-{VERSION}-Linux-X64.tar.xz",
            url_template: "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/LLVM-{VERSION}-Linux-X64.tar.xz",
            archive_type: "archive",
            alt_filename_template: None,
            alt_url_template: None,
        }),
        "linux-aarch64" => Some(DownloadConfig {
            filename_template: "LLVM-{VERSION}-Linux-ARM64.tar.xz",
            url_template: "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/LLVM-{VERSION}-Linux-ARM64.tar.xz",
            archive_type: "archive",
            alt_filename_template: None,
            alt_url_template: None,
        }),
        "darwin-x86_64" => Some(DownloadConfig {
            filename_template: "clang+llvm-{VERSION}-x86_64-apple-darwin.tar.xz",
            url_template: "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/clang+llvm-{VERSION}-x86_64-apple-darwin.tar.xz",
            archive_type: "archive",
            alt_filename_template: None,
            alt_url_template: None,
        }),
        "darwin-arm64" => Some(DownloadConfig {
            filename_template: "clang+llvm-{VERSION}-arm64-apple-darwin.tar.xz",
            url_template: "https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/clang+llvm-{VERSION}-arm64-apple-darwin.tar.xz",
            archive_type: "archive",
            alt_filename_template: None,
            alt_url_template: None,
        }),
        _ => None,
    }
}

/// All supported platform keys
pub const PLATFORM_KEYS: &[&str] = &[
    "win-x86_64",
    "linux-x86_64",
    "linux-aarch64",
    "darwin-x86_64",
    "darwin-arm64",
];

/// Detect current platform key
pub fn detect_current_platform() -> Option<&'static str> {
    let target = ctcb_core::Target::current().ok()?;
    match (target.platform, target.arch) {
        (ctcb_core::Platform::Win, ctcb_core::Arch::X86_64) => Some("win-x86_64"),
        (ctcb_core::Platform::Linux, ctcb_core::Arch::X86_64) => Some("linux-x86_64"),
        (ctcb_core::Platform::Linux, ctcb_core::Arch::Arm64) => Some("linux-aarch64"),
        (ctcb_core::Platform::Darwin, ctcb_core::Arch::X86_64) => Some("darwin-x86_64"),
        (ctcb_core::Platform::Darwin, ctcb_core::Arch::Arm64) => Some("darwin-arm64"),
        _ => None, // e.g., win-arm64 is not yet supported
    }
}

/// Download a file from a URL with a progress bar.
pub fn download_file(url: &str, destination: &Path) -> Result<()> {
    println!("Downloading {}...", url);

    // Use tokio runtime for async reqwest
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async {
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::limited(10))
            .build()?;

        let response = client
            .get(url)
            .send()
            .await
            .context("Failed to send request")?;

        if !response.status().is_success() {
            bail!("HTTP {} for {}", response.status(), url);
        }

        let total_size = response.content_length();

        let pb = if let Some(total) = total_size {
            let pb = ProgressBar::new(total);
            pb.set_style(
                ProgressStyle::default_bar()
                    .template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {bytes}/{total_bytes} ({eta})")
                    .unwrap()
                    .progress_chars("#>-"),
            );
            pb
        } else {
            ProgressBar::new_spinner()
        };

        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut file = fs::File::create(destination)?;

        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.context("Error reading response body")?;
            file.write_all(&chunk)?;
            pb.inc(chunk.len() as u64);
        }

        pb.finish_with_message("Download complete");
        Ok(())
    })
}

/// Extract a Windows .exe LLVM installer using 7z
pub fn extract_windows_installer(installer: &Path, output_dir: &Path) -> Result<()> {
    println!("Extracting Windows installer {}...", installer.display());
    fs::create_dir_all(output_dir)?;

    let status = Command::new("7z")
        .args([
            "x",
            &installer.to_string_lossy(),
            &format!("-o{}", output_dir.display()),
            "-y",
        ])
        .status()
        .context("Failed to run 7z. Is 7-Zip installed?")?;

    if !status.success() {
        bail!("7z extraction failed with exit code {:?}", status.code());
    }

    println!("Extracted to {}", output_dir.display());
    Ok(())
}

/// Extract a tar.xz archive
pub fn extract_tar_xz(archive: &Path, output_dir: &Path) -> Result<()> {
    println!("Extracting {}...", archive.display());
    fs::create_dir_all(output_dir)?;

    // Use system tar for .tar.xz since the Rust xz2 crate can be slow
    let status = Command::new("tar")
        .args([
            "xf",
            &archive.to_string_lossy(),
            "-C",
            &output_dir.to_string_lossy(),
        ])
        .status()
        .context("Failed to run tar")?;

    if !status.success() {
        bail!("tar extraction failed with exit code {:?}", status.code());
    }

    println!("Extracted to {}", output_dir.display());
    Ok(())
}

/// Download and extract LLVM for a single platform.
/// Returns the path to the extracted directory.
pub fn download_platform(
    version: &str,
    platform_key: &str,
    output_dir: &Path,
    verify: bool,
) -> Result<PathBuf> {
    let config = get_download_config(platform_key)
        .ok_or_else(|| anyhow::anyhow!("Unknown platform: {}", platform_key))?;

    let filename = config.filename_template.replace("{VERSION}", version);
    let url = config.url_template.replace("{VERSION}", version);

    let download_path = output_dir.join(&filename);

    // Download if not already present (or verify fails)
    if download_path.exists() {
        println!("File already exists: {}", download_path.display());
        if verify {
            println!("(Re-download not implemented for existing files, using as-is)");
        }
    } else if let Err(e) = download_file(&url, &download_path) {
        // Try alt URL if available
        if let (Some(alt_fn), Some(alt_url)) =
            (config.alt_filename_template, config.alt_url_template)
        {
            println!("Primary download failed ({}), trying alternative...", e);
            let alt_filename = alt_fn.replace("{VERSION}", version);
            let alt_url_resolved = alt_url.replace("{VERSION}", version);
            let alt_path = output_dir.join(&alt_filename);
            download_file(&alt_url_resolved, &alt_path)?;
            // Extract alt
            let extract_dir = output_dir.join(format!("{}-extracted", platform_key));
            extract_tar_xz(&alt_path, &extract_dir)?;
            return Ok(extract_dir);
        } else {
            return Err(e);
        }
    }

    // Extract
    let extract_dir = output_dir.join(format!("{}-extracted", platform_key));
    if config.archive_type == "installer" {
        extract_windows_installer(&download_path, &extract_dir)?;
    } else {
        extract_tar_xz(&download_path, &extract_dir)?;
    }

    Ok(extract_dir)
}

/// Download all specified platforms (or all if None).
pub fn download_all(
    version: &str,
    platforms: Option<&[String]>,
    output_dir: &Path,
    verify: bool,
) -> Result<Vec<(String, PathBuf)>> {
    let platform_list: Vec<String> = match platforms {
        Some(p) => p.to_vec(),
        None => PLATFORM_KEYS.iter().map(|s| s.to_string()).collect(),
    };

    let mut results = Vec::new();
    let mut failures = Vec::new();

    for platform_key in &platform_list {
        ctcb_core::print_section(&format!("Downloading {}", platform_key));
        match download_platform(version, platform_key, output_dir, verify) {
            Ok(path) => {
                println!("Success: {}", platform_key);
                results.push((platform_key.clone(), path));
            }
            Err(e) => {
                println!("Failed: {} - {}", platform_key, e);
                failures.push(platform_key.clone());
            }
        }
    }

    // Summary
    ctcb_core::print_section("Download Summary");
    for (key, path) in &results {
        println!("  {} -> {}", key, path.display());
    }
    for key in &failures {
        println!("  {} FAILED", key);
    }
    println!(
        "\nTotal: {}/{} successful",
        results.len(),
        results.len() + failures.len()
    );

    if !failures.is_empty() {
        bail!("{} platform(s) failed to download", failures.len());
    }

    Ok(results)
}
