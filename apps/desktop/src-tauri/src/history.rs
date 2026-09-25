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
    pub original_asr: Option<&'a [String]>,
    pub processing: Option<&'a ProcessingMetadata>,
}

/// Frozen with the session. Empty groups/rules mean none were applied.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProcessingMetadata {
    pub model_id: String,
    pub model_revision: String,
    pub model_provenance: String,
    pub decoder_version: String,
    pub processor_version: String,
    pub format: String,
    pub cleanup: bool,
    pub groups: Vec<String>,
    pub dictionary_version: Option<String>,
    pub cleanup_version: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum CorrectionCategory {
    Recognition,
    Cleanup,
    Style,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Revision {
    pub id: i64,
    pub created_at: i64,
    pub before_text: String,
    pub text: String,
    pub reverted: bool,
    pub category: Option<CorrectionCategory>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryDetail {
    pub entry: HistoryEntry,
    pub original_asr: Option<Vec<String>>,
    pub produced_text: String,
    pub processing: Option<ProcessingMetadata>,
    pub edit_version: i64,
    pub current_revision: Option<i64>,
    pub revisions: Vec<Revision>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EditRequest {
    id: i64,
    expected_version: i64,
    request_key: String,
    text: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UndoRequest {
    id: i64,
    expected_version: i64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RememberRequest {
    id: i64,
    revision_id: i64,
    category: CorrectionCategory,
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

    pub(crate) fn access<T>(
        &self,
        operation: impl FnOnce(&mut Connection) -> Result<T>,
    ) -> Result<T> {
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
        connection.execute_batch("PRAGMA foreign_keys = ON; PRAGMA secure_delete = ON;")?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let version: i64 = tx.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(
            (0..=2).contains(&version),
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
        if version < 2 {
            tx.execute_batch(
                "ALTER TABLE transcriptions ADD COLUMN original_asr_json TEXT;
                 ALTER TABLE transcriptions ADD COLUMN produced_text TEXT NOT NULL DEFAULT '';
                 ALTER TABLE transcriptions ADD COLUMN processing_json TEXT;
                 ALTER TABLE transcriptions ADD COLUMN current_revision INTEGER;
                 ALTER TABLE transcriptions ADD COLUMN edit_version INTEGER NOT NULL DEFAULT 0;
                 UPDATE transcriptions SET produced_text = text;
                 CREATE TABLE history_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transcription_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,
                    request_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    parent_id INTEGER REFERENCES history_revisions(id),
                    before_text TEXT NOT NULL,
                    text TEXT NOT NULL,
                    reverted INTEGER NOT NULL DEFAULT 0 CHECK(reverted IN (0, 1)),
                    UNIQUE(transcription_id, request_key)
                 );
                 CREATE TABLE confirmed_corrections (
                    revision_id INTEGER PRIMARY KEY REFERENCES history_revisions(id) ON DELETE CASCADE,
                    category TEXT NOT NULL CHECK(category IN ('recognition', 'cleanup', 'style')),
                    confirmed_at INTEGER NOT NULL
                 );
                 CREATE TRIGGER immutable_dictation BEFORE UPDATE OF
                    session_key, created_at, original_asr_json, produced_text, processing_json,
                    word_count, duration_seconds, language ON transcriptions
                 BEGIN SELECT RAISE(ABORT, 'Original dictation is immutable'); END;
                 PRAGMA user_version = 2;",
            )?;
        }
        crate::suggestions::migrate(&tx)?;
        crate::analysis::migrate(&tx)?;
        tx.commit()?;
        operation(&mut connection)
    }

    pub fn record(&self, entry: &CompletedDictation<'_>) -> Result<()> {
        if entry.text.trim().is_empty()
            && entry
                .original_asr
                .is_none_or(|parts| parts.iter().all(|part| part.trim().is_empty()))
        {
            return Ok(());
        }
        ensure!(
            entry.duration_seconds.is_finite() && entry.duration_seconds >= 0.0,
            "Durata della dettatura non valida."
        );
        self.access(|connection| {
            connection.execute(
                "INSERT INTO transcriptions(session_key, created_at, text, search_text, word_count, duration_seconds, language, original_asr_json, produced_text, processing_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?3, ?9) ON CONFLICT(session_key) DO NOTHING",
                params![entry.session_key, entry.created_at, entry.text, entry.text.to_lowercase(), word_count(entry.text), entry.duration_seconds, entry.language, entry.original_asr.map(serde_json::to_string).transpose()?, entry.processing.map(serde_json::to_string).transpose()?],
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

    pub fn detail(&self, id: i64) -> Result<HistoryDetail> {
        valid_id(id)?;
        self.access(|connection| {
            let tx = connection.transaction()?;
            let result = read_detail(&tx, id)?;
            tx.commit()?;
            Ok(result)
        })
    }

    pub fn edit(&self, request: EditRequest) -> Result<HistoryDetail> {
        valid_id(request.id)?;
        ensure!(request.expected_version >= 0, "Versione non valida.");
        ensure!(
            !request.text.trim().is_empty() && request.text.len() <= 1_000_000,
            "Inserisci un testo tra 1 e 1.000.000 byte."
        );
        ensure!(
            !request.request_key.is_empty() && request.request_key.len() <= 100,
            "Chiave di salvataggio non valida."
        );
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let previous: Option<String> = tx.query_row(
                "SELECT text FROM history_revisions WHERE transcription_id = ?1 AND request_key = ?2",
                params![request.id, request.request_key], |row| row.get(0),
            ).optional()?;
            if let Some(previous) = previous {
                ensure!(previous == request.text, "Chiave di salvataggio già usata per un altro testo.");
            } else {
                let detail = read_detail(&tx, request.id)?;
                ensure!(detail.edit_version == request.expected_version, "Il testo è cambiato. Chiudi e riapri la trascrizione.");
                ensure!(detail.entry.text != request.text, "Il testo non è cambiato.");
                tx.execute(
                    "INSERT INTO history_revisions(transcription_id, request_key, created_at, parent_id, before_text, text) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![request.id, request.request_key, timestamp()?, detail.current_revision, detail.entry.text, request.text],
                )?;
                tx.execute("UPDATE transcriptions SET text = ?1, search_text = ?2, current_revision = ?3, edit_version = edit_version + 1 WHERE id = ?4",
                    params![request.text, request.text.to_lowercase(), tx.last_insert_rowid(), request.id])?;
            }
            let result = read_detail(&tx, request.id)?;
            tx.commit()?;
            Ok(result)
        })
    }

    pub fn undo(&self, request: UndoRequest) -> Result<HistoryDetail> {
        valid_id(request.id)?;
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let detail = read_detail(&tx, request.id)?;
            ensure!(detail.edit_version == request.expected_version, "Il testo è cambiato. Chiudi e riapri la trascrizione.");
            let revision = detail.current_revision.context("Nessuna correzione da annullare.")?;
            let (text, parent): (String, Option<i64>) = tx.query_row(
                "SELECT before_text, parent_id FROM history_revisions WHERE id = ?1", [revision], |row| Ok((row.get(0)?, row.get(1)?)))?;
            tx.execute("DELETE FROM confirmed_corrections WHERE revision_id = ?1", [revision])?;
            tx.execute("UPDATE history_revisions SET reverted = 1 WHERE id = ?1", [revision])?;
            tx.execute("UPDATE transcriptions SET text = ?1, search_text = ?2, current_revision = ?3, edit_version = edit_version + 1 WHERE id = ?4",
                params![text, text.to_lowercase(), parent, request.id])?;
            let result = read_detail(&tx, request.id)?;
            tx.commit()?;
            Ok(result)
        })
    }

    pub fn remember(&self, request: RememberRequest) -> Result<HistoryDetail> {
        valid_id(request.id)?;
        valid_id(request.revision_id)?;
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let active: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM history_revisions WHERE id = ?1 AND transcription_id = ?2 AND reverted = 0)",
                params![request.revision_id, request.id], |row| row.get(0))?;
            ensure!(active, "Correzione assente o annullata.");
            let category = match request.category {
                CorrectionCategory::Recognition => "recognition",
                CorrectionCategory::Cleanup => "cleanup",
                CorrectionCategory::Style => "style",
            };
            tx.execute("INSERT INTO confirmed_corrections(revision_id, category, confirmed_at) VALUES (?1, ?2, ?3) ON CONFLICT(revision_id) DO UPDATE SET category = excluded.category",
                params![request.revision_id, category, timestamp()?])?;
            let result = read_detail(&tx, request.id)?;
            tx.commit()?;
            Ok(result)
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

fn timestamp() -> Result<i64> {
    Ok(std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as i64)
}

fn read_detail(connection: &Connection, id: i64) -> Result<HistoryDetail> {
    let (entry, raw, produced_text, processing, edit_version, current_revision) = connection.query_row(
        "SELECT id, created_at, text, word_count, duration_seconds, language, original_asr_json, produced_text, processing_json, edit_version, current_revision FROM transcriptions WHERE id = ?1",
        [id], |row| Ok((HistoryEntry {
            id: row.get(0)?, created_at: row.get(1)?, text: row.get(2)?, word_count: row.get(3)?, duration_seconds: row.get(4)?, language: row.get(5)?,
        }, row.get::<_, Option<String>>(6)?, row.get::<_, String>(7)?, row.get::<_, Option<String>>(8)?, row.get::<_, i64>(9)?, row.get::<_, Option<i64>>(10)?)),
    ).optional()?.context("Trascrizione non più presente nello storico.")?;
    let revisions = connection.prepare(
        "SELECT r.id, r.created_at, r.before_text, r.text, r.reverted, c.category FROM history_revisions r LEFT JOIN confirmed_corrections c ON c.revision_id = r.id WHERE r.transcription_id = ?1 ORDER BY r.id DESC",
    )?.query_map([id], |row| {
        let category: Option<String> = row.get(5)?;
        let category = category.map(|category| match category.as_str() {
            "recognition" => Ok(CorrectionCategory::Recognition),
            "cleanup" => Ok(CorrectionCategory::Cleanup),
            "style" => Ok(CorrectionCategory::Style),
            _ => Err(rusqlite::Error::InvalidQuery),
        }).transpose()?;
        Ok(Revision { id: row.get(0)?, created_at: row.get(1)?, before_text: row.get(2)?, text: row.get(3)?, reverted: row.get(4)?, category })
    })?.collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(HistoryDetail {
        entry,
        original_asr: raw.map(|raw| serde_json::from_str(&raw)).transpose()?,
        produced_text,
        processing: processing
            .map(|raw| serde_json::from_str(&raw))
            .transpose()?,
        edit_version,
        current_revision,
        revisions,
    })
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

#[tauri::command]
pub async fn history_detail(
    store: tauri::State<'_, HistoryStore>,
    id: i64,
) -> Result<HistoryDetail, String> {
    run(store.inner().clone(), move |store| store.detail(id)).await
}
#[tauri::command]
pub async fn history_edit(
    store: tauri::State<'_, HistoryStore>,
    request: EditRequest,
) -> Result<HistoryDetail, String> {
    run(store.inner().clone(), move |store| store.edit(request)).await
}
#[tauri::command]
pub async fn history_undo(
    store: tauri::State<'_, HistoryStore>,
    request: UndoRequest,
) -> Result<HistoryDetail, String> {
    run(store.inner().clone(), move |store| store.undo(request)).await
}
#[tauri::command]
pub async fn history_remember(
    store: tauri::State<'_, HistoryStore>,
    request: RememberRequest,
) -> Result<HistoryDetail, String> {
    run(store.inner().clone(), move |store| store.remember(request)).await
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
                    original_asr: None,
                    processing: None,
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

    fn edit(id: i64, version: i64, key: &str, text: &str) -> EditRequest {
        EditRequest {
            id,
            expected_version: version,
            request_key: key.into(),
            text: text.into(),
        }
    }

    fn legacy(f: &Fixture) -> Connection {
        fs::create_dir_all(&f.root).unwrap();
        let connection = Connection::open(f.root.join("history.sqlite3")).unwrap();
        connection.execute_batch("CREATE TABLE transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL, text TEXT NOT NULL, search_text TEXT NOT NULL,
            word_count INTEGER NOT NULL CHECK(word_count >= 0),
            duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0), language TEXT NOT NULL);
            INSERT INTO transcriptions VALUES (1, 'legacy', 1700000000000, 'Vecchio <unk> testo', 'vecchio <unk> testo', 3, 12.5, 'auto');
            PRAGMA user_version = 1;").unwrap();
        connection
    }

    #[test]
    fn v1_migration_preserves_legacy_text_and_explicitly_absent_raw() {
        let f = Fixture::new();
        drop(legacy(&f));
        for _ in 0..2 {
            let detail = HistoryStore::new(f.root.join("history.sqlite3"))
                .detail(1)
                .unwrap();
            assert_eq!(detail.produced_text, "Vecchio <unk> testo");
            assert_eq!(detail.entry.text, detail.produced_text);
            assert_eq!(detail.original_asr, None);
            assert_eq!(detail.processing, None);
            assert_eq!(detail.entry.word_count, 3);
            assert_eq!(detail.entry.duration_seconds, 12.5);
        }
        f.store
            .edit(edit(1, 0, "legacy-edit", "Testo corretto"))
            .unwrap();
        let restored = f
            .store
            .undo(UndoRequest {
                id: 1,
                expected_version: 1,
            })
            .unwrap();
        assert_eq!(restored.entry.text, "Vecchio <unk> testo");
        assert_eq!(restored.original_asr, None);
    }

    #[test]
    fn failed_migration_rolls_back_schema_and_data_then_retries() {
        let f = Fixture::new();
        let connection = legacy(&f);
        connection
            .execute_batch("CREATE TABLE history_revisions(block_migration TEXT)")
            .unwrap();
        assert!(f.store.detail(1).is_err());
        assert_eq!(
            connection
                .pragma_query_value(None, "user_version", |row| row.get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert!(
            connection
                .prepare("SELECT original_asr_json FROM transcriptions")
                .is_err()
        );
        assert_eq!(
            connection
                .query_row("SELECT text FROM transcriptions", [], |row| row
                    .get::<_, String>(0))
                .unwrap(),
            "Vecchio <unk> testo"
        );
        connection
            .execute_batch("DROP TABLE history_revisions")
            .unwrap();
        assert!(f.store.detail(1).is_ok());
    }

    #[test]
    fn edits_undo_confirmation_and_reopen_preserve_original_and_stats() {
        let f = Fixture::new();
        let raw = vec!["<unk> voce flo a capo".into(), "non 42 molto molto".into()];
        let metadata =
            crate::service::processing_metadata(&crate::settings::Settings::default()).unwrap();
        let completed = CompletedDictation {
            session_key: "new",
            created_at: 1700000000000,
            text: "voce flo\nnon 42 molto molto",
            duration_seconds: 18.0,
            language: "it-IT",
            original_asr: Some(&raw),
            processing: Some(&metadata),
        };
        f.store.record(&completed).unwrap();
        let stats = f.store.page(HistoryQuery::default()).unwrap().stats;
        let first = f
            .store
            .edit(edit(1, 0, "edit1", "VoiceFlow\nnon 42 molto molto"))
            .unwrap();
        assert!(first.revisions[0].category.is_none());
        f.store
            .remember(RememberRequest {
                id: 1,
                revision_id: first.current_revision.unwrap(),
                category: CorrectionCategory::Recognition,
            })
            .unwrap();
        f.store
            .remember(RememberRequest {
                id: 1,
                revision_id: first.current_revision.unwrap(),
                category: CorrectionCategory::Recognition,
            })
            .unwrap();
        assert_eq!(
            f.store
                .edit(edit(1, 0, "edit1", "VoiceFlow\nnon 42 molto molto"))
                .unwrap()
                .revisions
                .len(),
            1
        );
        assert!(f.store.edit(edit(1, 0, "other", "conflitto")).is_err());
        assert!(f.store.edit(edit(1, 1, "edit1", "chiave riusata")).is_err());
        let second = f
            .store
            .edit(edit(1, 1, "edit2", "Testo riscritto liberamente"))
            .unwrap();
        assert!(second.revisions[0].category.is_none());
        f.store
            .remember(RememberRequest {
                id: 1,
                revision_id: second.current_revision.unwrap(),
                category: CorrectionCategory::Style,
            })
            .unwrap();
        let undone = f
            .store
            .undo(UndoRequest {
                id: 1,
                expected_version: 2,
            })
            .unwrap();
        assert_eq!(undone.entry.text, first.entry.text);
        assert!(undone.revisions[0].reverted);
        assert!(undone.revisions[0].category.is_none());
        assert_eq!(
            undone.revisions[1].category,
            Some(CorrectionCategory::Recognition)
        );
        assert!(
            f.store
                .remember(RememberRequest {
                    id: 1,
                    revision_id: second.current_revision.unwrap(),
                    category: CorrectionCategory::Cleanup
                })
                .is_err()
        );
        assert!(
            f.store
                .undo(UndoRequest {
                    id: 1,
                    expected_version: 2
                })
                .is_err()
        );
        f.store
            .undo(UndoRequest {
                id: 1,
                expected_version: 3,
            })
            .unwrap();
        let branch = f
            .store
            .edit(edit(1, 4, "edit3", "Altra correzione"))
            .unwrap();
        assert_eq!(branch.revisions.len(), 3);
        f.store
            .record(&CompletedDictation {
                text: "Tentativo di sovrascrittura",
                ..completed
            })
            .unwrap();
        let reopened = HistoryStore::new(f.root.join("history.sqlite3"))
            .detail(1)
            .unwrap();
        assert_eq!(reopened.entry.text, "Altra correzione");
        assert_eq!(reopened.original_asr, Some(raw));
        assert_eq!(reopened.produced_text, "voce flo\nnon 42 molto molto");
        assert_eq!(reopened.processing, Some(metadata));
        assert_eq!(f.store.page(HistoryQuery::default()).unwrap().stats, stats);
        assert_eq!(f.store.text(1).unwrap(), reopened.entry.text);
        assert_eq!(
            f.store
                .page(HistoryQuery {
                    search: "altra".into(),
                    before_id: None
                })
                .unwrap()
                .entries
                .len(),
            1
        );
    }

    #[test]
    fn edit_failure_is_atomic_and_immutable_columns_are_guarded() {
        let f = Fixture::new();
        f.record("one", "Originale", 2.0);
        let connection = Connection::open(f.root.join("history.sqlite3")).unwrap();
        assert!(
            connection
                .execute("UPDATE transcriptions SET produced_text = 'changed'", [])
                .is_err()
        );
        assert!(
            connection
                .execute("UPDATE transcriptions SET word_count = 99", [])
                .is_err()
        );
        connection.execute_batch("CREATE TRIGGER fail_edit BEFORE UPDATE OF text ON transcriptions BEGIN SELECT RAISE(ABORT, 'injected write failure'); END;").unwrap();
        assert!(f.store.edit(edit(1, 0, "retry", "Correzione")).is_err());
        let detail = f.store.detail(1).unwrap();
        assert_eq!(detail.entry.text, "Originale");
        assert_eq!(detail.edit_version, 0);
        assert!(detail.revisions.is_empty());
        connection.execute_batch("DROP TRIGGER fail_edit").unwrap();
        assert!(f.store.edit(edit(1, 0, "retry", "Correzione")).is_ok());
    }

    #[test]
    fn deletion_removes_all_derived_text_and_corrections_without_orphans() {
        let f = Fixture::new();
        f.record("one", "Originale", 2.0);
        for (version, text) in ["Prima", "Seconda"].iter().enumerate() {
            let detail = f.store.edit(edit(1, version as i64, text, text)).unwrap();
            f.store
                .remember(RememberRequest {
                    id: 1,
                    revision_id: detail.current_revision.unwrap(),
                    category: CorrectionCategory::Cleanup,
                })
                .unwrap();
        }
        f.store.delete(1).unwrap();
        f.store.delete(1).unwrap();
        assert!(f.store.detail(1).is_err());
        let connection = Connection::open(f.root.join("history.sqlite3")).unwrap();
        for table in [
            "transcriptions",
            "history_revisions",
            "confirmed_corrections",
        ] {
            assert_eq!(
                connection
                    .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| row
                        .get::<_, i64>(0))
                    .unwrap(),
                0
            );
        }
        assert_eq!(
            f.store
                .page(HistoryQuery::default())
                .unwrap()
                .stats
                .total_sessions,
            0
        );
    }
}
