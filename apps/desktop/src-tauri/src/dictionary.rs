//! Local vocabulary storage. Deliberately independent of the dictation worker.
use anyhow::{Context, Result, bail, ensure};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

#[derive(Clone)]
pub struct DictionaryStore(Arc<Mutex<PathBuf>>);

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Term {
    id: i64,
    text: String,
    group_ids: Vec<i64>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Group {
    id: i64,
    name: String,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct DictionarySnapshot {
    terms: Vec<Term>,
    groups: Vec<Group>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveTerm {
    id: Option<i64>,
    text: String,
    group_ids: Vec<i64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveGroup {
    id: Option<i64>,
    name: String,
}

fn label(value: &str, limit: usize) -> Result<(String, String)> {
    let value = value.trim();
    ensure!(!value.is_empty(), "Inserisci un nome o una parola.");
    ensure!(
        value.chars().count() <= limit,
        "Testo troppo lungo, massimo {limit} caratteri."
    );
    ensure!(
        !value.chars().any(char::is_control),
        "Il testo non può contenere caratteri di controllo."
    );
    Ok((value.to_owned(), value.to_lowercase()))
}

fn require_id(id: i64) -> Result<()> {
    ensure!(
        id > 0 && id <= 9_007_199_254_740_991,
        "Identificativo non valido."
    );
    Ok(())
}

impl DictionaryStore {
    // Opening is lazy: an inaccessible or corrupt dictionary cannot block app startup.
    pub fn new(path: PathBuf) -> Self {
        Self(Arc::new(Mutex::new(path)))
    }

    fn access<T>(&self, operation: impl FnOnce(&mut Connection) -> Result<T>) -> Result<T> {
        let path = self
            .0
            .lock()
            .map_err(|_| anyhow::anyhow!("Dizionario occupato. Riapri VoiceFlow."))?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).context("Impossibile aprire la cartella del dizionario")?;
        }
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&*path)
        {
            Ok(_) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error).context("Impossibile creare il dizionario"),
        }
        fs::set_permissions(&*path, fs::Permissions::from_mode(0o600))?;
        let mut connection =
            Connection::open(&*path).context("Impossibile aprire il dizionario")?;
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.pragma_update(None, "foreign_keys", true)?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(
            version <= 1,
            "Questo dizionario richiede una versione più recente di VoiceFlow."
        );
        if version == 0 {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            tx.execute_batch(
                "CREATE TABLE IF NOT EXISTS terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    text_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    PRIMARY KEY (term_id, group_id)
                );
                CREATE INDEX IF NOT EXISTS memberships_group ON memberships(group_id);
                PRAGMA user_version = 1;",
            )?;
            tx.commit()?;
        }
        operation(&mut connection)
    }

    pub fn snapshot(&self) -> Result<DictionarySnapshot> {
        self.access(|connection| {
            let tx = connection.transaction()?;
            let snapshot = read_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }

    pub fn save_term(&self, input: SaveTerm) -> Result<DictionarySnapshot> {
        let (text, key) = label(&input.text, 200)?;
        if let Some(id) = input.id {
            require_id(id)?;
        }
        ensure!(
            input.group_ids.len() <= 1000,
            "Troppi gruppi per una parola."
        );
        for &id in &input.group_ids {
            require_id(id)?;
        }
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let duplicate: Option<i64> = tx
                .query_row("SELECT id FROM terms WHERE text_key = ?1", [&key], |row| {
                    row.get(0)
                })
                .optional()?;
            ensure!(
                duplicate.is_none() || duplicate == input.id,
                "Questa parola è già nel dizionario."
            );
            let id = if let Some(id) = input.id {
                ensure!(
                    tx.execute(
                        "UPDATE terms SET text = ?1, text_key = ?2 WHERE id = ?3",
                        params![text, key, id]
                    )? == 1,
                    "La parola non esiste più. Aggiorna il dizionario."
                );
                id
            } else {
                tx.execute(
                    "INSERT INTO terms(text, text_key) VALUES (?1, ?2)",
                    params![text, key],
                )?;
                tx.last_insert_rowid()
            };
            tx.execute("DELETE FROM memberships WHERE term_id = ?1", [id])?;
            for group_id in input.group_ids {
                let exists: bool = tx.query_row(
                    "SELECT EXISTS(SELECT 1 FROM groups WHERE id = ?1)",
                    [group_id],
                    |row| row.get(0),
                )?;
                ensure!(exists, "Un gruppo non esiste più. Aggiorna il dizionario.");
                tx.execute(
                    "INSERT OR IGNORE INTO memberships(term_id, group_id) VALUES (?1, ?2)",
                    params![id, group_id],
                )?;
            }
            let snapshot = read_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }

    pub fn save_group(&self, input: SaveGroup) -> Result<DictionarySnapshot> {
        let (name, key) = label(&input.name, 80)?;
        if let Some(id) = input.id {
            require_id(id)?;
        }
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let duplicate: Option<i64> = tx
                .query_row("SELECT id FROM groups WHERE name_key = ?1", [&key], |row| {
                    row.get(0)
                })
                .optional()?;
            ensure!(
                duplicate.is_none() || duplicate == input.id,
                "Esiste già un gruppo con questo nome."
            );
            if let Some(id) = input.id {
                ensure!(
                    tx.execute(
                        "UPDATE groups SET name = ?1, name_key = ?2 WHERE id = ?3",
                        params![name, key, id]
                    )? == 1,
                    "Il gruppo non esiste più. Aggiorna il dizionario."
                );
            } else {
                tx.execute(
                    "INSERT INTO groups(name, name_key) VALUES (?1, ?2)",
                    params![name, key],
                )?;
            }
            let snapshot = read_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }

    pub fn delete_term(&self, id: i64) -> Result<DictionarySnapshot> {
        self.delete(id, "DELETE FROM terms WHERE id = ?1")
    }
    pub fn delete_group(&self, id: i64) -> Result<DictionarySnapshot> {
        self.delete(id, "DELETE FROM groups WHERE id = ?1")
    }
    fn delete(&self, id: i64, statement: &str) -> Result<DictionarySnapshot> {
        require_id(id)?;
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            if tx.execute(statement, [id])? != 1 {
                bail!("Elemento non più presente. Aggiorna il dizionario.");
            }
            let snapshot = read_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }
}

fn read_snapshot(connection: &Connection) -> Result<DictionarySnapshot> {
    let groups = connection
        .prepare("SELECT id, name FROM groups ORDER BY name_key, id")?
        .query_map([], |row| {
            Ok(Group {
                id: row.get(0)?,
                name: row.get(1)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut terms = connection
        .prepare("SELECT id, text FROM terms ORDER BY text_key, id")?
        .query_map([], |row| {
            Ok(Term {
                id: row.get(0)?,
                text: row.get(1)?,
                group_ids: vec![],
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let positions: std::collections::HashMap<_, _> = terms
        .iter()
        .enumerate()
        .map(|(index, term)| (term.id, index))
        .collect();
    let mut statement = connection
        .prepare("SELECT term_id, group_id FROM memberships ORDER BY term_id, group_id")?;
    for membership in
        statement.query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))?
    {
        let (term, group) = membership?;
        if let Some(&position) = positions.get(&term) {
            terms[position].group_ids.push(group);
        }
    }
    Ok(DictionarySnapshot { terms, groups })
}

async fn run(
    store: DictionaryStore,
    operation: impl FnOnce(DictionaryStore) -> Result<DictionarySnapshot> + Send + 'static,
) -> Result<DictionarySnapshot, String> {
    tauri::async_runtime::spawn_blocking(move || operation(store))
        .await
        .map_err(|error| error.to_string())?
        .map_err(|error| format!("{error:#}"))
}

#[tauri::command]
pub async fn dictionary_snapshot(
    store: tauri::State<'_, DictionaryStore>,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), |store| store.snapshot()).await
}
#[tauri::command]
pub async fn dictionary_save_term(
    store: tauri::State<'_, DictionaryStore>,
    input: SaveTerm,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), move |store| store.save_term(input)).await
}
#[tauri::command]
pub async fn dictionary_save_group(
    store: tauri::State<'_, DictionaryStore>,
    input: SaveGroup,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), move |store| store.save_group(input)).await
}
#[tauri::command]
pub async fn dictionary_delete_term(
    store: tauri::State<'_, DictionaryStore>,
    id: i64,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), move |store| store.delete_term(id)).await
}
#[tauri::command]
pub async fn dictionary_delete_group(
    store: tauri::State<'_, DictionaryStore>,
    id: i64,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), move |store| store.delete_group(id)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture {
        root: PathBuf,
        store: DictionaryStore,
    }
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "voiceflow-dictionary-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let store = DictionaryStore::new(root.join("dictionary.sqlite3"));
            Self { root, store }
        }
        fn group(&self, name: &str) -> i64 {
            self.store
                .save_group(SaveGroup {
                    id: None,
                    name: name.into(),
                })
                .unwrap()
                .groups
                .into_iter()
                .find(|group| group.name == name)
                .unwrap()
                .id
        }
        fn term(&self, text: &str, group_ids: Vec<i64>) -> i64 {
            self.store
                .save_term(SaveTerm {
                    id: None,
                    text: text.into(),
                    group_ids,
                })
                .unwrap()
                .terms
                .into_iter()
                .find(|term| term.text == text.trim())
                .unwrap()
                .id
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn persistence_reopens_private_database_and_preserves_multiple_memberships() {
        let fixture = Fixture::new();
        assert!(!fixture.root.exists()); // construction never touches disk
        assert!(fixture.store.snapshot().unwrap().terms.is_empty());
        let z = fixture.group("Zeta");
        let a = fixture.group("Alfa");
        fixture.term("  Èquipe  ", vec![z, a, z]);
        fixture.term("Alpha", vec![]);
        let before = fixture.store.snapshot().unwrap();
        let reopened = DictionaryStore::new(fixture.root.join("dictionary.sqlite3"));
        assert_eq!(reopened.snapshot().unwrap(), before);
        assert_eq!(before.groups[0].name, "Alfa");
        assert_eq!(before.terms[0].text, "Alpha");
        assert_eq!(before.terms[1].text, "Èquipe");
        assert_eq!(before.terms[1].group_ids, vec![z, a]);
        assert_eq!(
            fs::metadata(fixture.root.join("dictionary.sqlite3"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        let json = serde_json::to_value(before).unwrap();
        assert_eq!(json["terms"][1]["groupIds"], serde_json::json!([z, a]));
    }

    #[test]
    fn unicode_duplicate_inserts_and_edits_leave_all_data_unchanged() {
        let fixture = Fixture::new();
        let group = fixture.group("Èquipe");
        let other = fixture.group("Other");
        fixture.term("ÉCOLE", vec![group]);
        let word = fixture.term("Altro", vec![other]);
        let before = fixture.store.snapshot().unwrap();
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: None,
                    text: " école ".into(),
                    group_ids: vec![]
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: Some(word),
                    text: "école".into(),
                    group_ids: vec![group]
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_group(SaveGroup {
                    id: None,
                    name: " èquipe ".into()
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_group(SaveGroup {
                    id: Some(other),
                    name: "ÈQUIPE".into()
                })
                .is_err()
        );
        assert_eq!(before, fixture.store.snapshot().unwrap());
        // A case-only edit is valid for the same ID.
        fixture
            .store
            .save_group(SaveGroup {
                id: Some(group),
                name: "ÈQUIPE".into(),
            })
            .unwrap();
    }

    #[test]
    fn unknown_membership_rolls_back_text_edit_and_old_memberships() {
        let fixture = Fixture::new();
        let group = fixture.group("Project");
        let word = fixture.term("Original", vec![group]);
        let before = fixture.store.snapshot().unwrap();
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: Some(word),
                    text: "Changed".into(),
                    group_ids: vec![group, 999]
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: None,
                    text: "New".into(),
                    group_ids: vec![999]
                })
                .is_err()
        );
        assert_eq!(before, fixture.store.snapshot().unwrap());
    }

    #[test]
    fn group_deletion_retains_words_and_other_groups_then_term_deletion_cascades() {
        let fixture = Fixture::new();
        let first = fixture.group("First");
        let second = fixture.group("Second");
        let word = fixture.term("Shared", vec![first, second]);
        fixture.term("Only first", vec![first]);
        let snapshot = fixture.store.delete_group(first).unwrap();
        assert_eq!(snapshot.terms.len(), 2);
        assert!(snapshot.terms[0].group_ids.is_empty());
        assert_eq!(snapshot.terms[1].group_ids, vec![second]);
        let snapshot = fixture.store.delete_term(word).unwrap();
        assert_eq!(snapshot.terms.len(), 1);
        assert_eq!(snapshot.groups.len(), 1);
        fixture
            .store
            .access(|connection| {
                let count: i64 =
                    connection
                        .query_row("SELECT COUNT(*) FROM memberships", [], |row| row.get(0))?;
                assert_eq!(count, 0);
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn edits_replace_only_target_term_and_its_memberships() {
        let fixture = Fixture::new();
        let first = fixture.group("First");
        let second = fixture.group("Second");
        let word = fixture.term("Before", vec![first]);
        fixture.term("Untouched", vec![first, second]);
        let snapshot = fixture
            .store
            .save_term(SaveTerm {
                id: Some(word),
                text: "After".into(),
                group_ids: vec![second],
            })
            .unwrap();
        assert_eq!(snapshot.terms[0].text, "After");
        assert_eq!(snapshot.terms[0].group_ids, vec![second]);
        assert_eq!(snapshot.terms[1].text, "Untouched");
        assert_eq!(snapshot.terms[1].group_ids, vec![first, second]);
        let snapshot = fixture
            .store
            .save_term(SaveTerm {
                id: Some(word),
                text: "After".into(),
                group_ids: vec![],
            })
            .unwrap();
        assert!(snapshot.terms[0].group_ids.is_empty());
    }

    #[test]
    fn invalid_values_and_unknown_ids_do_not_change_database() {
        let fixture = Fixture::new();
        fixture.term("Valid", vec![]);
        let before = fixture.store.snapshot().unwrap();
        for text in [
            " ".to_string(),
            "x".repeat(201),
            "a\nb".into(),
            "a\0b".into(),
        ] {
            assert!(
                fixture
                    .store
                    .save_term(SaveTerm {
                        id: None,
                        text,
                        group_ids: vec![]
                    })
                    .is_err()
            );
        }
        for name in ["".to_string(), "x".repeat(81)] {
            assert!(
                fixture
                    .store
                    .save_group(SaveGroup { id: None, name })
                    .is_err()
            );
        }
        for id in [-1, 0, 999, i64::MAX] {
            assert!(
                fixture
                    .store
                    .save_term(SaveTerm {
                        id: Some(id),
                        text: "Valid".into(),
                        group_ids: vec![]
                    })
                    .is_err()
            );
            assert!(
                fixture
                    .store
                    .save_group(SaveGroup {
                        id: Some(id),
                        name: "Valid".into()
                    })
                    .is_err()
            );
            assert!(fixture.store.delete_term(id).is_err());
            assert!(fixture.store.delete_group(id).is_err());
        }
        assert_eq!(before, fixture.store.snapshot().unwrap());
        assert!(
            serde_json::from_str::<SaveTerm>(r#"{"id":null,"text":"test","groupIds":["1"]}"#)
                .is_err()
        );
    }

    #[test]
    fn corrupt_database_is_not_replaced_and_failures_do_not_poison_store() {
        let fixture = Fixture::new();
        fs::create_dir_all(&fixture.root).unwrap();
        let path = fixture.root.join("dictionary.sqlite3");
        fs::write(&path, b"not a SQLite database").unwrap();
        assert!(fixture.store.snapshot().is_err());
        assert!(
            fixture
                .store
                .save_group(SaveGroup {
                    id: None,
                    name: "New".into()
                })
                .is_err()
        );
        assert_eq!(fs::read(&path).unwrap(), b"not a SQLite database");
        fs::remove_file(&path).unwrap();
        assert!(fixture.store.snapshot().unwrap().terms.is_empty());
    }

    #[test]
    fn concurrent_commands_keep_every_independent_insert() {
        let fixture = Fixture::new();
        std::thread::scope(|scope| {
            for index in 0..8 {
                let store = fixture.store.clone();
                scope.spawn(move || {
                    store
                        .save_term(SaveTerm {
                            id: None,
                            text: format!("Term {index}"),
                            group_ids: vec![],
                        })
                        .unwrap();
                });
            }
        });
        assert_eq!(fixture.store.snapshot().unwrap().terms.len(), 8);
    }
}
