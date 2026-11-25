use anyhow::{Context, Result, bail, ensure};
use rusqlite::{Connection, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::Path,
    time::Duration,
};
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
        let connection = Self::open(path)?;
        let (language, format, cleanup): (String, String, i64) = connection.query_row(
            "SELECT language, format, cleanup FROM settings WHERE id = 1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        Ok(Self {
            language: match language.as_str() {
                "auto" => Language::Auto,
                "italian" => Language::Italian,
                "english" => Language::English,
                _ => bail!("Lingua salvata non valida"),
            },
            format: match format.as_str() {
                "faithful" => FormatMode::Faithful,
                "spoken-commands" => FormatMode::SpokenCommands,
                _ => bail!("Formato salvato non valido"),
            },
            cleanup: match cleanup {
                0 => false,
                1 => true,
                _ => bail!("Impostazione di pulizia non valida"),
            },
        })
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        let connection = Self::open(path)?;
        ensure!(
            connection.execute(
                "UPDATE settings SET language = ?1, format = ?2, cleanup = ?3 WHERE id = 1",
                params![self.language_name(), self.format_name(), self.cleanup],
            )? == 1,
            "Impostazioni mancanti nel database"
        );
        Ok(())
    }

    fn language_name(&self) -> &'static str {
        match self.language {
            Language::Auto => "auto",
            Language::Italian => "italian",
            Language::English => "english",
        }
    }

    fn format_name(&self) -> &'static str {
        match self.format {
            FormatMode::Faithful => "faithful",
            FormatMode::SpokenCommands => "spoken-commands",
        }
    }

    fn open(path: &Path) -> Result<Connection> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        match fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(path)
        {
            Ok(_) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error.into()),
        }
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        let mut connection =
            Connection::open(path).context("Database impostazioni non leggibile")?;
        connection.busy_timeout(Duration::from_secs(2))?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let version: i64 =
            transaction.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(
            (0..=1).contains(&version),
            "Versione del database impostazioni non supportata"
        );
        if version == 0 {
            // Commit the imported row and schema version together. A failed import
            // can be retried; an existing database never reimports the old JSON.
            let initial = match fs::read(path.with_extension("json")) {
                Ok(data) => serde_json::from_slice::<Self>(&data)
                    .context("Impossibile importare desktop-settings.json")?,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Self::default(),
                Err(error) => return Err(error.into()),
            };
            transaction.execute_batch(
                "CREATE TABLE settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    language TEXT NOT NULL CHECK (language IN ('auto', 'italian', 'english')),
                    format TEXT NOT NULL CHECK (format IN ('faithful', 'spoken-commands')),
                    cleanup INTEGER NOT NULL CHECK (cleanup IN (0, 1))
                );",
            )?;
            transaction.execute(
                "INSERT INTO settings (id, language, format, cleanup) VALUES (1, ?1, ?2, ?3)",
                params![
                    initial.language_name(),
                    initial.format_name(),
                    initial.cleanup
                ],
            )?;
            transaction.pragma_update(None, "user_version", 1)?;
        }
        transaction.commit()?;
        Ok(connection)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
    };

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let root = std::env::temp_dir().join(format!(
                "voiceflow-settings-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }

        fn database(&self) -> PathBuf {
            self.0.join("desktop-settings.sqlite3")
        }

        fn legacy(&self, contents: &str) {
            fs::write(self.0.join("desktop-settings.json"), contents).unwrap();
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn defaults_and_changes_persist_in_sqlite_after_reopening() {
        let fixture = Fixture::new();
        let path = fixture.database();
        assert_eq!(Settings::load(&path).unwrap().language.code(), "auto");
        Settings {
            language: Language::English,
            format: FormatMode::SpokenCommands,
            cleanup: true,
        }
        .save(&path)
        .unwrap();
        let loaded = Settings::load(&path).unwrap();
        assert_eq!(loaded.language.code(), "en-US");
        assert!(matches!(loaded.format, FormatMode::SpokenCommands));
        assert!(loaded.cleanup);
        let connection = Connection::open(&path).unwrap();
        let row: (String, String, i64) = connection
            .query_row(
                "SELECT language, format, cleanup FROM settings",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(row, ("english".into(), "spoken-commands".into(), 1));
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(!fixture.0.join("desktop-settings.json").exists());
    }

    #[test]
    fn imports_legacy_once_and_keeps_original_file() {
        let fixture = Fixture::new();
        let original = r#"{"language":"italian","format":"spoken-commands","cleanup":true}"#;
        fixture.legacy(original);
        let path = fixture.database();
        let loaded = Settings::load(&path).unwrap();
        assert_eq!(loaded.language.code(), "it-IT");
        assert!(matches!(loaded.format, FormatMode::SpokenCommands));
        assert!(loaded.cleanup);
        Settings::default().save(&path).unwrap();
        assert_eq!(
            fs::read_to_string(fixture.0.join("desktop-settings.json")).unwrap(),
            original
        );
        fixture.legacy("invalid JSON now ignored");
        assert_eq!(Settings::load(&path).unwrap().language.code(), "auto");
    }

    #[test]
    fn imports_older_legacy_without_cleanup() {
        let fixture = Fixture::new();
        fixture.legacy(r#"{"language":"italian","format":"faithful"}"#);
        let loaded = Settings::load(&fixture.database()).unwrap();
        assert_eq!(loaded.language.code(), "it-IT");
        assert!(!loaded.cleanup);
    }

    #[test]
    fn failed_import_does_not_commit_and_can_be_retried() {
        let fixture = Fixture::new();
        fixture.legacy("broken JSON");
        let path = fixture.database();
        assert!(Settings::load(&path).is_err());
        assert!(Settings::default().save(&path).is_err());
        let connection = Connection::open(&path).unwrap();
        let version: i64 = connection
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .unwrap();
        assert_eq!(version, 0);
        fixture.legacy(r#"{"language":"english","format":"faithful"}"#);
        assert_eq!(Settings::load(&path).unwrap().language.code(), "en-US");
    }

    #[test]
    fn corrupt_database_is_not_overwritten_or_replaced_with_legacy() {
        let fixture = Fixture::new();
        let path = fixture.database();
        fixture.legacy(r#"{"language":"italian","format":"faithful"}"#);
        fs::write(&path, "corrupt database").unwrap();
        assert!(Settings::load(&path).is_err());
        assert!(Settings::default().save(&path).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), "corrupt database");
    }

    #[test]
    fn newer_schema_is_rejected_without_changing_settings() {
        let fixture = Fixture::new();
        let path = fixture.database();
        Settings::default().save(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        connection.pragma_update(None, "user_version", 2).unwrap();
        assert!(Settings::load(&path).is_err());
        assert!(
            Settings {
                language: Language::Italian,
                ..Settings::default()
            }
            .save(&path)
            .is_err()
        );
        let language: String = connection
            .query_row("SELECT language FROM settings", [], |row| row.get(0))
            .unwrap();
        assert_eq!(language, "auto");
        let version: i64 = connection
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .unwrap();
        assert_eq!(version, 2);
    }

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
