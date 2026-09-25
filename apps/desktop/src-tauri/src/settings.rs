use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use voiceflow_core::{formatting::FormatMode, streaming::Language};

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub language: Language,
    pub format: FormatMode,
    #[serde(default)]
    pub cleanup: bool,
}

impl Settings {
    pub fn load(path: &Path) -> Result<Self> {
        match fs::read(path) {
            Ok(data) => Ok(serde_json::from_slice(&data)?),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error.into()),
        }
    }
    pub fn save(&self, path: &Path) -> Result<()> {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let temporary = path.with_extension("json.tmp");
        let mut file = fs::OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec_pretty(self)?)?;
        file.sync_all()?;
        fs::rename(temporary, path)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn existing_settings_default_to_cleanup_off_and_round_trip() {
        let old: Settings =
            serde_json::from_str(r#"{"language":"italian","format":"faithful"}"#).unwrap();
        assert!(!old.cleanup);
        assert!(!Settings::default().cleanup);
        let enabled = Settings {
            cleanup: true,
            ..old
        };
        let encoded = serde_json::to_string(&enabled).unwrap();
        let decoded: Settings = serde_json::from_str(&encoded).unwrap();
        assert!(decoded.cleanup);
        assert_eq!(decoded.language.code(), "it-IT");
        assert!(matches!(decoded.format, FormatMode::Faithful));
        assert!(
            serde_json::from_str::<Settings>(
                r#"{"language":"auto","format":"faithful","cleanup":"true"}"#
            )
            .is_err()
        );
    }
}
