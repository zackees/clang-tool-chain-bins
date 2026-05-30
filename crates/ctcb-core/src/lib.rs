//! Core types and utilities for the clang-tool-chain-bins workspace.
//!
//! Provides platform/architecture detection, formatting helpers, and shared types
//! used across all ctcb crates.

use std::fmt;
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// Supported operating system platforms.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Platform {
    Win,
    Linux,
    Darwin,
}

/// Supported CPU architectures.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Arch {
    X86_64,
    Arm64,
}

/// A (platform, architecture) pair identifying a build target.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Target {
    pub platform: Platform,
    pub arch: Arch,
}

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

impl Platform {
    /// Parse a platform string, accepting common aliases.
    ///
    /// Recognised inputs (case-insensitive): `win`, `windows`, `linux`,
    /// `darwin`, `macos`, `mac`.
    pub fn from_str_loose(s: &str) -> anyhow::Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "win" | "windows" => Ok(Self::Win),
            "linux" => Ok(Self::Linux),
            "darwin" | "macos" | "mac" => Ok(Self::Darwin),
            other => anyhow::bail!("unknown platform: {other:?}"),
        }
    }
}

impl fmt::Display for Platform {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Win => write!(f, "win"),
            Self::Linux => write!(f, "linux"),
            Self::Darwin => write!(f, "darwin"),
        }
    }
}

// ---------------------------------------------------------------------------
// Arch
// ---------------------------------------------------------------------------

impl Arch {
    /// Parse an architecture string, accepting common aliases.
    ///
    /// Recognised inputs (case-insensitive): `x86_64`, `x64`, `amd64`,
    /// `arm64`, `aarch64`.
    pub fn from_str_loose(s: &str) -> anyhow::Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "x86_64" | "x64" | "amd64" => Ok(Self::X86_64),
            "arm64" | "aarch64" => Ok(Self::Arm64),
            other => anyhow::bail!("unknown architecture: {other:?}"),
        }
    }
}

impl fmt::Display for Arch {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::X86_64 => write!(f, "x86_64"),
            Self::Arm64 => write!(f, "arm64"),
        }
    }
}

// ---------------------------------------------------------------------------
// Target
// ---------------------------------------------------------------------------

impl Target {
    /// Create a new target from explicit platform and architecture.
    pub fn new(platform: Platform, arch: Arch) -> Self {
        Self { platform, arch }
    }

    /// Detect the current host platform and architecture at runtime.
    pub fn current() -> anyhow::Result<Self> {
        let platform = if cfg!(target_os = "windows") {
            Platform::Win
        } else if cfg!(target_os = "linux") {
            Platform::Linux
        } else if cfg!(target_os = "macos") {
            Platform::Darwin
        } else {
            anyhow::bail!("unsupported operating system");
        };

        let arch = match std::env::consts::ARCH {
            "x86_64" => Arch::X86_64,
            "aarch64" => Arch::Arm64,
            other => anyhow::bail!("unsupported architecture: {other}"),
        };

        Ok(Self { platform, arch })
    }
}

impl fmt::Display for Target {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}-{}", self.platform, self.arch)
    }
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/// Format a byte count as a human-readable string (e.g. `"52.3 MB"`).
pub fn format_size(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;
    const TB: f64 = GB * 1024.0;

    let b = bytes as f64;
    if b < KB {
        format!("{bytes} B")
    } else if b < MB {
        format!("{:.1} KB", b / KB)
    } else if b < GB {
        format!("{:.1} MB", b / MB)
    } else if b < TB {
        format!("{:.1} GB", b / GB)
    } else {
        format!("{:.1} TB", b / TB)
    }
}

/// Format a [`Duration`] as a human-readable string.
///
/// Examples: `"3m 24s"`, `"1.5s"`, `"250ms"`.
pub fn format_duration(duration: Duration) -> String {
    let total_secs = duration.as_secs();
    let millis = duration.subsec_millis();

    if total_secs == 0 {
        if millis == 0 {
            return "0ms".to_string();
        }
        return format!("{millis}ms");
    }

    let minutes = total_secs / 60;
    let secs = total_secs % 60;

    if minutes > 0 {
        if secs > 0 {
            format!("{minutes}m {secs}s")
        } else {
            format!("{minutes}m")
        }
    } else if millis > 0 {
        // Show fractional seconds when under one minute and there are millis
        let frac = total_secs as f64 + millis as f64 / 1000.0;
        format!("{frac:.1}s")
    } else {
        format!("{secs}s")
    }
}

/// Print a section header surrounded by a 70-character separator line.
pub fn print_section(title: &str) {
    let sep = "=".repeat(70);
    println!("{sep}");
    println!("{title}");
    println!("{sep}");
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // -- Platform round-trip -------------------------------------------------

    #[test]
    fn platform_display_roundtrip() {
        for plat in [Platform::Win, Platform::Linux, Platform::Darwin] {
            let s = plat.to_string();
            let parsed = Platform::from_str_loose(&s).unwrap();
            assert_eq!(plat, parsed, "round-trip failed for {s}");
        }
    }

    #[test]
    fn platform_from_str_loose_aliases() {
        assert_eq!(Platform::from_str_loose("windows").unwrap(), Platform::Win);
        assert_eq!(Platform::from_str_loose("WIN").unwrap(), Platform::Win);
        assert_eq!(Platform::from_str_loose("macos").unwrap(), Platform::Darwin);
        assert_eq!(Platform::from_str_loose("mac").unwrap(), Platform::Darwin);
        assert_eq!(Platform::from_str_loose("LINUX").unwrap(), Platform::Linux);
    }

    #[test]
    fn platform_from_str_loose_unknown() {
        assert!(Platform::from_str_loose("freebsd").is_err());
    }

    // -- Arch round-trip -----------------------------------------------------

    #[test]
    fn arch_display_roundtrip() {
        for arch in [Arch::X86_64, Arch::Arm64] {
            let s = arch.to_string();
            let parsed = Arch::from_str_loose(&s).unwrap();
            assert_eq!(arch, parsed, "round-trip failed for {s}");
        }
    }

    #[test]
    fn arch_from_str_loose_aliases() {
        assert_eq!(Arch::from_str_loose("x64").unwrap(), Arch::X86_64);
        assert_eq!(Arch::from_str_loose("amd64").unwrap(), Arch::X86_64);
        assert_eq!(Arch::from_str_loose("AMD64").unwrap(), Arch::X86_64);
        assert_eq!(Arch::from_str_loose("aarch64").unwrap(), Arch::Arm64);
        assert_eq!(Arch::from_str_loose("ARM64").unwrap(), Arch::Arm64);
    }

    #[test]
    fn arch_from_str_loose_unknown() {
        assert!(Arch::from_str_loose("mips").is_err());
    }

    // -- Target --------------------------------------------------------------

    #[test]
    fn target_current_succeeds() {
        let t = Target::current().unwrap();
        // We're running on a known platform, so the result must be valid.
        let _ = t.platform;
        let _ = t.arch;
    }

    #[test]
    fn target_display() {
        let t = Target::new(Platform::Linux, Arch::X86_64);
        assert_eq!(t.to_string(), "linux-x86_64");

        let t2 = Target::new(Platform::Darwin, Arch::Arm64);
        assert_eq!(t2.to_string(), "darwin-arm64");
    }

    // -- format_size ---------------------------------------------------------

    #[test]
    fn format_size_bytes() {
        assert_eq!(format_size(0), "0 B");
        assert_eq!(format_size(512), "512 B");
    }

    #[test]
    fn format_size_kilobytes() {
        assert_eq!(format_size(1024), "1.0 KB");
        assert_eq!(format_size(1536), "1.5 KB");
    }

    #[test]
    fn format_size_megabytes() {
        assert_eq!(format_size(52_428_800), "50.0 MB");
        // 52.3 MB = 52.3 * 1024 * 1024 = 54_843_597 (approx)
        let mb_52_3 = (52.3 * 1024.0 * 1024.0) as u64;
        assert_eq!(format_size(mb_52_3), "52.3 MB");
    }

    #[test]
    fn format_size_gigabytes() {
        assert_eq!(format_size(1_073_741_824), "1.0 GB");
    }

    // -- format_duration -----------------------------------------------------

    #[test]
    fn format_duration_zero() {
        assert_eq!(format_duration(Duration::ZERO), "0ms");
    }

    #[test]
    fn format_duration_millis_only() {
        assert_eq!(format_duration(Duration::from_millis(250)), "250ms");
    }

    #[test]
    fn format_duration_fractional_seconds() {
        assert_eq!(format_duration(Duration::from_millis(1500)), "1.5s");
    }

    #[test]
    fn format_duration_whole_seconds() {
        assert_eq!(format_duration(Duration::from_secs(5)), "5s");
    }

    #[test]
    fn format_duration_minutes_and_seconds() {
        assert_eq!(format_duration(Duration::from_secs(204)), "3m 24s");
    }

    #[test]
    fn format_duration_exact_minutes() {
        assert_eq!(format_duration(Duration::from_secs(120)), "2m");
    }

    // -- Serde ---------------------------------------------------------------

    #[test]
    fn platform_serde_json_roundtrip() {
        let p = Platform::Darwin;
        let json = serde_json::to_string(&p).unwrap();
        assert_eq!(json, "\"darwin\"");
        let p2: Platform = serde_json::from_str(&json).unwrap();
        assert_eq!(p, p2);
    }

    #[test]
    fn arch_serde_json_roundtrip() {
        let a = Arch::X86_64;
        let json = serde_json::to_string(&a).unwrap();
        assert_eq!(json, "\"x86_64\"");
        let a2: Arch = serde_json::from_str(&json).unwrap();
        assert_eq!(a, a2);
    }
}
