use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

/// GitHub LFS media URL base
pub const LFS_MEDIA_BASE: &str =
    "https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins";

/// Root manifest (assets/{tool}/manifest.json)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RootManifest {
    pub platforms: Vec<PlatformEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlatformEntry {
    pub platform: String,
    pub architectures: Vec<ArchEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArchEntry {
    pub arch: String,
    pub manifest_path: String,
}

/// Version info in a platform manifest
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionInfo {
    pub href: String,
    pub sha256: String,
    /// Optional split parts (for archives > 100MB)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parts: Option<Vec<PartRef>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartRef {
    pub href: String,
    pub sha256: String,
}

/// Platform manifest — custom serialization because it mixes "latest" key with version keys
/// at the same JSON object level.
#[derive(Debug, Clone)]
pub struct PlatformManifest {
    pub latest: String,
    pub versions: BTreeMap<String, VersionInfo>,
}

// Custom Serialize: write {"latest": "...", "ver1": {...}, "ver2": {...}}
impl Serialize for PlatformManifest {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(Some(1 + self.versions.len()))?;
        map.serialize_entry("latest", &self.latest)?;
        for (ver, info) in &self.versions {
            map.serialize_entry(ver, info)?;
        }
        map.end()
    }
}

// Custom Deserialize: read "latest" from the object and treat all other keys as version entries
impl<'de> Deserialize<'de> for PlatformManifest {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw: serde_json::Value = serde::Deserialize::deserialize(deserializer)?;
        let obj = raw
            .as_object()
            .ok_or_else(|| serde::de::Error::custom("expected object"))?;

        let latest = obj
            .get("latest")
            .and_then(|v| v.as_str())
            .ok_or_else(|| serde::de::Error::custom("missing 'latest' key"))?
            .to_string();

        let mut versions = BTreeMap::new();
        for (key, value) in obj {
            if key == "latest" {
                continue;
            }
            let info: VersionInfo = serde_json::from_value(value.clone()).map_err(|e| {
                serde::de::Error::custom(format!("bad version entry '{}': {}", key, e))
            })?;
            versions.insert(key.clone(), info);
        }

        Ok(PlatformManifest { latest, versions })
    }
}

/// Read a root manifest
pub fn read_root_manifest(path: &Path) -> Result<RootManifest> {
    let json =
        fs::read_to_string(path).with_context(|| format!("Failed to read {}", path.display()))?;
    let manifest: RootManifest = serde_json::from_str(&json)?;
    Ok(manifest)
}

/// Write a root manifest
pub fn write_root_manifest(path: &Path, manifest: &RootManifest) -> Result<()> {
    let json = serde_json::to_string_pretty(manifest)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, json)?;
    Ok(())
}

/// Read a platform manifest
pub fn read_platform_manifest(path: &Path) -> Result<PlatformManifest> {
    let json =
        fs::read_to_string(path).with_context(|| format!("Failed to read {}", path.display()))?;
    let manifest: PlatformManifest = serde_json::from_str(&json)?;
    Ok(manifest)
}

/// Write a platform manifest
pub fn write_platform_manifest(path: &Path, manifest: &PlatformManifest) -> Result<()> {
    let json = serde_json::to_string_pretty(manifest)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, json)?;
    Ok(())
}

/// Add or update a version in a platform manifest.
/// Creates the manifest file if it doesn't exist.
pub fn update_platform_manifest(
    path: &Path,
    version: &str,
    href: &str,
    sha256: &str,
    parts: Option<Vec<PartRef>>,
) -> Result<()> {
    let mut manifest = if path.exists() {
        read_platform_manifest(path)?
    } else {
        PlatformManifest {
            latest: String::new(),
            versions: BTreeMap::new(),
        }
    };

    manifest.latest = version.to_string();
    manifest.versions.insert(
        version.to_string(),
        VersionInfo {
            href: href.to_string(),
            sha256: sha256.to_string(),
            parts,
        },
    );

    write_platform_manifest(path, &manifest)
}

/// Ensure a platform/arch entry exists in the root manifest.
pub fn ensure_root_entry(
    path: &Path,
    platform: &str,
    arch: &str,
    manifest_path: &str,
) -> Result<()> {
    let mut root = if path.exists() {
        read_root_manifest(path)?
    } else {
        RootManifest {
            platforms: Vec::new(),
        }
    };

    // Find or create platform entry
    let platform_entry = root.platforms.iter_mut().find(|p| p.platform == platform);

    if let Some(entry) = platform_entry {
        // Check if arch already exists
        if !entry.architectures.iter().any(|a| a.arch == arch) {
            entry.architectures.push(ArchEntry {
                arch: arch.to_string(),
                manifest_path: manifest_path.to_string(),
            });
        }
    } else {
        root.platforms.push(PlatformEntry {
            platform: platform.to_string(),
            architectures: vec![ArchEntry {
                arch: arch.to_string(),
                manifest_path: manifest_path.to_string(),
            }],
        });
    }

    write_root_manifest(path, &root)
}

/// Generate a GitHub LFS media URL for an asset.
/// branch defaults to "main".
pub fn lfs_media_url(asset_path: &str, branch: &str) -> String {
    format!("{}/{}/assets/{}", LFS_MEDIA_BASE, branch, asset_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_platform_manifest_roundtrip() {
        let manifest = PlatformManifest {
            latest: "21.1.5".to_string(),
            versions: {
                let mut m = BTreeMap::new();
                m.insert(
                    "21.1.5".to_string(),
                    VersionInfo {
                        href: "https://example.com/llvm.tar.zst".to_string(),
                        sha256: "abc123".to_string(),
                        parts: None,
                    },
                );
                m
            },
        };

        let json = serde_json::to_string_pretty(&manifest).unwrap();
        let parsed: PlatformManifest = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.latest, "21.1.5");
        assert_eq!(parsed.versions.len(), 1);
        assert_eq!(parsed.versions["21.1.5"].sha256, "abc123");
    }

    #[test]
    fn test_read_existing_platform_manifest() {
        // Test parsing the actual format used in the repo
        let json = r#"{
  "latest": "21.1.5",
  "21.1.5": {
    "href": "https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst",
    "sha256": "3c21e45edeee591fe8ead5427d25b62ddb26c409575b41db03d6777c77bba44f"
  }
}"#;
        let manifest: PlatformManifest = serde_json::from_str(json).unwrap();
        assert_eq!(manifest.latest, "21.1.5");
        assert!(
            manifest.versions["21.1.5"]
                .href
                .contains("media.githubusercontent.com")
        );
    }

    #[test]
    fn test_root_manifest_roundtrip() {
        let manifest = RootManifest {
            platforms: vec![PlatformEntry {
                platform: "win".to_string(),
                architectures: vec![ArchEntry {
                    arch: "x86_64".to_string(),
                    manifest_path: "win/x86_64/manifest.json".to_string(),
                }],
            }],
        };

        let json = serde_json::to_string_pretty(&manifest).unwrap();
        let parsed: RootManifest = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.platforms.len(), 1);
        assert_eq!(parsed.platforms[0].architectures[0].arch, "x86_64");
    }

    #[test]
    fn test_update_platform_manifest_creates_new() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("manifest.json");

        update_platform_manifest(
            &path,
            "21.1.5",
            "https://example.com/a.tar.zst",
            "abc123",
            None,
        )
        .unwrap();

        let manifest = read_platform_manifest(&path).unwrap();
        assert_eq!(manifest.latest, "21.1.5");
        assert_eq!(manifest.versions["21.1.5"].sha256, "abc123");
    }

    #[test]
    fn test_update_platform_manifest_adds_version() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("manifest.json");

        update_platform_manifest(
            &path,
            "21.1.5",
            "https://example.com/a.tar.zst",
            "abc123",
            None,
        )
        .unwrap();
        update_platform_manifest(
            &path,
            "21.1.6",
            "https://example.com/b.tar.zst",
            "def456",
            None,
        )
        .unwrap();

        let manifest = read_platform_manifest(&path).unwrap();
        assert_eq!(manifest.latest, "21.1.6");
        assert_eq!(manifest.versions.len(), 2);
    }

    #[test]
    fn test_ensure_root_entry() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("manifest.json");

        ensure_root_entry(&path, "win", "x86_64", "win/x86_64/manifest.json").unwrap();
        ensure_root_entry(&path, "linux", "x86_64", "linux/x86_64/manifest.json").unwrap();
        ensure_root_entry(&path, "win", "arm64", "win/arm64/manifest.json").unwrap();
        // Adding same entry again should be idempotent
        ensure_root_entry(&path, "win", "x86_64", "win/x86_64/manifest.json").unwrap();

        let root = read_root_manifest(&path).unwrap();
        assert_eq!(root.platforms.len(), 2); // win, linux
        let win = root.platforms.iter().find(|p| p.platform == "win").unwrap();
        assert_eq!(win.architectures.len(), 2); // x86_64, arm64
    }

    #[test]
    fn test_lfs_media_url() {
        let url = lfs_media_url("clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst", "main");
        assert_eq!(
            url,
            "https://media.githubusercontent.com/media/zackees/clang-tool-chain-bins/main/assets/clang/win/x86_64/llvm-21.1.5-win-x86_64.tar.zst"
        );
    }

    #[test]
    fn test_read_actual_repo_manifests() {
        // Test reading actual manifest files from the repo if they exist
        let root_path = Path::new("assets/clang/manifest.json");
        if root_path.exists() {
            let root = read_root_manifest(root_path).unwrap();
            assert!(!root.platforms.is_empty());

            // Try to read one platform manifest
            for platform in &root.platforms {
                for arch in &platform.architectures {
                    let platform_path = Path::new("assets/clang").join(&arch.manifest_path);
                    if platform_path.exists() {
                        let pm = read_platform_manifest(&platform_path).unwrap();
                        assert!(!pm.latest.is_empty());
                        assert!(!pm.versions.is_empty());
                        // Verify URL uses LFS media format
                        for info in pm.versions.values() {
                            assert!(
                                info.href.contains("media.githubusercontent.com")
                                    || info.href.contains("localhost"),
                                "URL should use LFS media format: {}",
                                info.href
                            );
                        }
                        return; // One successful read is enough
                    }
                }
            }
        }
    }
}
