//! Completed dictations, stored locally without audio or network services.
use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

const PAGE_SIZE: usize = 20;

#[derive(Clone)]
pub struct HistoryStore(Arc<Mutex<PathBuf>>);

pub struct CompletedDictation<'a> {
    pub session_key: &'a str,
    pub created_at: i64,
    pub text: &'a str,
    pub duration_seconds: f64,
    pub language: &'a str,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HistoryEntry {
    pub id: i64,
    pub created_at: i64,
    pub text: String,
    pub word_count: i64,
    pub duration_seconds: f64,
    pub language: String,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HistoryStats {
    total_words: i64,
    total_sessions: i64,
    total_seconds: f64,
    words_per_minute: Option<f64>,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct HistoryPage {
    entries: Vec<HistoryEntry>,
    next_cursor: Option<i64>,
    stats: HistoryStats,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HistoryQuery {
    search: String,
    before_id: Option<i64>,
}

// Whitespace-separated tokens containing a letter or number, suitable for Italian/English.
fn word_count(text: &str) -> i64 {
    text.split_whitespace()
        .filter(|token| token.chars().any(char::is_alphanumeric))
        .count() as i64
}

fn valid_id(id: i64) -> Result<()> {
    ensure!(
        id > 0 && id <= 9_007_199_254_740_991,
        "Identificativo non valido."
    );
    Ok(())
}

impl HistoryStore {
    pub fn new(path: PathBuf) -> Self {
        Self(Arc::new(Mutex::new(path)))
    }

    fn access<T>(&self, operation: impl FnOnce(&mut Connection) -> Result<T>) -> Result<T> {
        let path = self
            .0
            .lock()
            .map_err(|_| anyhow::anyhow!("Storico occupato. Riapri VoiceFlow."))?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).context("Impossibile aprire la cartella dello storico")?;
        }
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&*path)
        {
            Ok(_) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error).context("Impossibile creare lo storico"),
        }
        fs::set_permissions(&*path, fs::Permissions::from_mode(0o600))?;
        let mut connection = Connection::open(&*path).context("Impossibile aprire lo storico")?;
        connection.busy_timeout(Duration::from_secs(2))?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let version: i64 = tx.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(
            version <= 1,
            "Questo storico richiede una versione più recente di VoiceFlow."
        );
        if version == 0 {
            tx.execute_batch(
                "CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    word_count INTEGER NOT NULL CHECK(word_count >= 0),
                    duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0),
                    language TEXT NOT NULL
                );
                PRAGMA user_version = 1;",
            )?;
        }
        tx.commit()?;
        operation(&mut connection)
    }

    pub fn record(&self, entry: &CompletedDictation<'_>) -> Result<()> {
        if entry.text.trim().is_empty() {
            return Ok(());
        }
        ensure!(
            entry.duration_seconds.is_finite() && entry.duration_seconds >= 0.0,
            "Durata della dettatura non valida."
        );
        self.access(|connection| {
            connection.execute(
                "INSERT INTO transcriptions(session_key, created_at, text, search_text, word_count, duration_seconds, language)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) ON CONFLICT(session_key) DO NOTHING",
                params![entry.session_key, entry.created_at, entry.text, entry.text.to_lowercase(), word_count(entry.text), entry.duration_seconds, entry.language],
            )?;
            Ok(())
        })
    }

    pub fn page(&self, query: HistoryQuery) -> Result<HistoryPage> {
        ensure!(
            query.search.chars().count() <= 200,
            "La ricerca può contenere al massimo 200 caratteri."
        );
        if let Some(id) = query.before_id {
            valid_id(id)?;
        }
        let search = query.search.trim().to_lowercase();
        self.access(|connection| {
            let tx = connection.transaction()?;
            let stats = tx.query_row(
                "SELECT COALESCE(SUM(word_count), 0), COUNT(*), COALESCE(SUM(duration_seconds), 0),
                    60.0 * SUM(CASE WHEN duration_seconds > 0 THEN word_count ELSE 0 END) / NULLIF(SUM(duration_seconds), 0)
                 FROM transcriptions", [], |row| Ok(HistoryStats {
                    total_words: row.get(0)?, total_sessions: row.get(1)?, total_seconds: row.get(2)?, words_per_minute: row.get(3)?,
                }),
            )?;
            let mut entries = tx.prepare(
                "SELECT id, created_at, text, word_count, duration_seconds, language FROM transcriptions
                 WHERE (?1 IS NULL OR id < ?1) AND instr(search_text, ?2) > 0
                 ORDER BY id DESC LIMIT ?3",
            )?.query_map(params![query.before_id, search, (PAGE_SIZE + 1) as i64], |row| Ok(HistoryEntry {
                id: row.get(0)?, created_at: row.get(1)?, text: row.get(2)?, word_count: row.get(3)?, duration_seconds: row.get(4)?, language: row.get(5)?,
            }))?.collect::<rusqlite::Result<Vec<_>>>()?;
            let has_more = entries.len() > PAGE_SIZE;
            entries.truncate(PAGE_SIZE);
            let next_cursor = if has_more { entries.last().map(|entry| entry.id) } else { None };
            tx.commit()?;
            Ok(HistoryPage { entries, next_cursor, stats })
        })
    }

    pub fn text(&self, id: i64) -> Result<String> {
        valid_id(id)?;
        self.access(|connection| {
            connection
                .query_row(
                    "SELECT text FROM transcriptions WHERE id = ?1",
                    [id],
                    |row| row.get(0),
                )
                .optional()?
                .context("Trascrizione non più presente nello storico.")
        })
    }

    pub fn delete(&self, id: i64) -> Result<()> {
        valid_id(id)?;
        self.access(|connection| {
            connection.execute("DELETE FROM transcriptions WHERE id = ?1", [id])?;
            Ok(())
        })
    }
}

async fn run<T: Send + 'static>(
    store: HistoryStore,
    operation: impl FnOnce(HistoryStore) -> Result<T> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(move || operation(store))
        .await
        .map_err(|error| error.to_string())?
        .map_err(|error| format!("{error:#}"))
}

#[tauri::command]
pub async fn history_page(
    store: tauri::State<'_, HistoryStore>,
    query: HistoryQuery,
) -> Result<HistoryPage, String> {
    run(store.inner().clone(), move |store| store.page(query)).await
}
#[tauri::command]
pub async fn history_copy(store: tauri::State<'_, HistoryStore>, id: i64) -> Result<(), String> {
    run(store.inner().clone(), move |store| {
        crate::platform::copy_text(&store.text(id)?)
    })
    .await
}
#[tauri::command]
pub async fn history_delete(store: tauri::State<'_, HistoryStore>, id: i64) -> Result<(), String> {
    run(store.inner().clone(), move |store| store.delete(id)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture {
        root: PathBuf,
        store: HistoryStore,
    }
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "voiceflow-history-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let store = HistoryStore::new(root.join("history.sqlite3"));
            Self { root, store }
        }
        fn record(&self, key: &str, text: &str, seconds: f64) {
            self.store
                .record(&CompletedDictation {
                    session_key: key,
                    created_at: 1_700_000_000_000,
                    text,
                    duration_seconds: seconds,
                    language: "auto",
                })
                .unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
    #[test]
    fn private_storage_reopens_and_repeated_save_does_not_double_count() {
        let f = Fixture::new();
        assert!(!f.root.exists());
        f.record("one", "Ciao mondo!", 10.5);
        f.record("one", "Ciao mondo!", 10.5);
        f.record("two", "Un altro testo", 19.5);
        let page = HistoryStore::new(f.root.join("history.sqlite3"))
            .page(HistoryQuery::default())
            .unwrap();
        assert_eq!(page.entries.len(), 2);
        assert_eq!(page.stats.total_words, 5);
        assert_eq!(page.stats.total_sessions, 2);
        assert_eq!(page.stats.total_seconds, 30.0);
        assert_eq!(page.stats.words_per_minute, Some(10.0));
        assert_eq!(page.entries[0].text, "Un altro testo");
        assert_eq!(
            fs::metadata(f.root.join("history.sqlite3"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    #[test]
    fn empty_text_is_ignored_zero_duration_is_not_in_speed_and_unicode_search_is_literal() {
        let f = Fixture::new();
        f.record("empty", " \n\t", 10.0);
        f.record("zero", "L’équipe costa 100% _ oggi. —", 0.0);
        let page = f
            .store
            .page(HistoryQuery {
                search: "ÉQUIPE".into(),
                before_id: None,
            })
            .unwrap();
        assert_eq!(page.stats.total_words, 4);
        assert_eq!(page.stats.words_per_minute, None);
        assert_eq!(page.entries.len(), 1);
        assert!(
            f.store
                .page(HistoryQuery {
                    search: "% _".into(),
                    before_id: None
                })
                .unwrap()
                .entries
                .len()
                == 1
        );
        assert!(
            f.store
                .page(HistoryQuery {
                    search: "' OR 1=1 --".into(),
                    before_id: None
                })
                .unwrap()
                .entries
                .is_empty()
        );
    }
    #[test]
    fn cursor_has_no_duplicates_when_new_sessions_arrive_and_deletion_recalculates_totals() {
        let f = Fixture::new();
        for n in 0..25 {
            f.record(&n.to_string(), "Una parola", 1.0);
        }
        let first = f.store.page(HistoryQuery::default()).unwrap();
        assert_eq!(first.entries.len(), PAGE_SIZE);
        f.record("new", "Nuova", 2.0);
        let second = f
            .store
            .page(HistoryQuery {
                search: String::new(),
                before_id: first.next_cursor,
            })
            .unwrap();
        assert_eq!(second.entries.len(), 5);
        assert_eq!(second.next_cursor, None);
        assert!(
            second
                .entries
                .iter()
                .all(|entry| entry.id < first.entries.last().unwrap().id)
        );
        let id = first.entries[0].id;
        assert_eq!(f.store.text(id).unwrap(), "Una parola");
        f.store.delete(id).unwrap();
        f.store.delete(id).unwrap();
        assert!(f.store.text(id).is_err());
        let stats = f.store.page(HistoryQuery::default()).unwrap().stats;
        assert_eq!(stats.total_sessions, 25);
        assert_eq!(stats.total_words, 49);
        assert_eq!(stats.total_seconds, 26.0);
    }
    #[test]
    fn future_and_corrupt_databases_are_never_replaced() {
        for contents in [b"not a database".to_vec(), Vec::new()] {
            let f = Fixture::new();
            fs::create_dir_all(&f.root).unwrap();
            let path = f.root.join("history.sqlite3");
            fs::write(&path, &contents).unwrap();
            if contents.is_empty() {
                Connection::open(&path)
                    .unwrap()
                    .pragma_update(None, "user_version", 99)
                    .unwrap();
            }
            let before = fs::read(&path).unwrap();
            assert!(f.store.page(HistoryQuery::default()).is_err());
            assert_eq!(before, fs::read(path).unwrap());
        }
    }
    #[test]
    fn boundary_validation_rejects_invalid_queries() {
        let f = Fixture::new();
        assert!(
            f.store
                .page(HistoryQuery {
                    search: "x".repeat(201),
                    before_id: None
                })
                .is_err()
        );
        assert!(
            f.store
                .page(HistoryQuery {
                    search: String::new(),
                    before_id: Some(-1)
                })
                .is_err()
        );
        assert!(f.store.delete(0).is_err());
        assert!(!f.root.exists());
    }
}
