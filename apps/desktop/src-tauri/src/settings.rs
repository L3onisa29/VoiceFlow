use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use voiceflow_core::{formatting::FormatMode, streaming::Language};

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub language: Language,
    pub format: FormatMode,
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
