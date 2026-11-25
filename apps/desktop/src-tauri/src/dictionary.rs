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
use voiceflow_core::dictionary::{
    Context as RuleContext, Dictionary, MatchMode, Rule, validate_rule,
};

#[derive(Clone)]
pub struct DictionaryStore(Arc<Mutex<PathBuf>>);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Term {
    pub(crate) id: i64,
    pub(crate) text: String,
    pub(crate) group_ids: Vec<i64>,
    #[serde(flatten)]
    pub(crate) rule: TermRule,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Group {
    pub(crate) id: i64,
    pub(crate) name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DictionarySnapshot {
    pub(crate) terms: Vec<Term>,
    pub(crate) groups: Vec<Group>,
    pub(crate) selected_group_ids: Vec<i64>,
    pub(crate) version: i64,
    pub(crate) conflicts: Vec<i64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TermRule {
    #[serde(default)]
    variants: Vec<String>,
    #[serde(default = "all_languages")]
    language: String,
    #[serde(default)]
    matching: MatchMode,
    #[serde(default)]
    context: RuleContext,
}
fn all_languages() -> String {
    "*".into()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveTerm {
    id: Option<i64>,
    text: String,
    group_ids: Vec<i64>,
    #[serde(flatten)]
    rule: TermRule,
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

    pub(crate) fn access<T>(
        &self,
        operation: impl FnOnce(&mut Connection) -> Result<T>,
    ) -> Result<T> {
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
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let version: i64 = tx.pragma_query_value(None, "user_version", |row| row.get(0))?;
        ensure!(
            (0..=2).contains(&version),
            "Questo dizionario richiede una versione più recente di VoiceFlow."
        );
        if version < 2 {
            if version == 0 {
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
            }
            if version < 2 {
                tx.execute_batch(r#"ALTER TABLE terms ADD COLUMN rule_json TEXT NOT NULL DEFAULT '{"variants":[],"language":"*","matching":"exact","context":{"before":null,"after":null,"exceptBefore":[],"exceptAfter":[]}}';
                CREATE TABLE dictionary_state(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT INTO dictionary_state VALUES(1, 1);
                CREATE TABLE selected_groups(group_id INTEGER PRIMARY KEY REFERENCES groups(id) ON DELETE CASCADE);
                PRAGMA user_version = 2;"#)?;
            }
        }
        tx.execute_batch("CREATE TABLE IF NOT EXISTS dictionary_versions (revision INTEGER PRIMARY KEY, definition_json TEXT NOT NULL);")?;
        archive_snapshot(&tx)?;
        tx.commit()?;
        operation(&mut connection)
    }

    pub(crate) fn path(&self) -> Result<PathBuf> {
        Ok(self
            .0
            .lock()
            .map_err(|_| anyhow::anyhow!("Dizionario occupato"))?
            .clone())
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
        ensure!(
            ["*", "it", "en"].contains(&input.rule.language.as_str()),
            "Lingua non valida."
        );
        ensure!(
            input.rule.variants.len() <= 128,
            "Massimo 128 varianti per termine."
        );
        for variant in &input.rule.variants {
            validate_rule(&Rule {
                id: input.id.unwrap_or(0),
                variant: variant.clone(),
                preferred: text.clone(),
                mode: input.rule.matching,
                context: input.rule.context.clone(),
                scope: 0,
            })?;
        }
        let rule_json = serde_json::to_string(&input.rule)?;
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
            tx.execute(
                "UPDATE terms SET rule_json = ?1 WHERE id = ?2",
                params![rule_json, id],
            )?;
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
            tx.execute(
                "UPDATE dictionary_state SET revision = revision + 1 WHERE id = 1",
                [],
            )?;
            let snapshot = read_snapshot(&tx)?;
            archive_snapshot(&tx)?;
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
            tx.execute(
                "UPDATE dictionary_state SET revision = revision + 1 WHERE id = 1",
                [],
            )?;
            let snapshot = read_snapshot(&tx)?;
            archive_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }

    pub fn select_groups(&self, ids: Vec<i64>) -> Result<DictionarySnapshot> {
        ensure!(ids.len() <= 1000, "Troppi gruppi selezionati.");
        for &id in &ids {
            require_id(id)?;
        }
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            tx.execute("DELETE FROM selected_groups", [])?;
            for id in ids {
                tx.execute("INSERT OR IGNORE INTO selected_groups VALUES(?1)", [id])?;
            }
            tx.execute(
                "UPDATE dictionary_state SET revision = revision + 1 WHERE id = 1",
                [],
            )?;
            let snapshot = read_snapshot(&tx)?;
            archive_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }

    pub fn session_snapshot(&self, language: &str) -> Result<(Dictionary, Vec<String>, String)> {
        let snapshot = self.snapshot()?;
        let language = language.split('-').next().unwrap_or(language);
        let rules = snapshot.rules(Some(language));
        let dictionary = Dictionary::compile(rules)?;
        let groups = snapshot
            .groups
            .iter()
            .filter(|group| snapshot.selected_group_ids.contains(&group.id))
            .map(|group| format!("{}:{}", group.id, group.name))
            .collect();
        Ok((
            dictionary,
            groups,
            format!("dictionary-v2:{}", snapshot.version),
        ))
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
            tx.execute(
                "UPDATE dictionary_state SET revision = revision + 1 WHERE id = 1",
                [],
            )?;
            let snapshot = read_snapshot(&tx)?;
            archive_snapshot(&tx)?;
            tx.commit()?;
            Ok(snapshot)
        })
    }
}

fn archive_snapshot(connection: &Connection) -> Result<()> {
    archive_schema_snapshot(connection, "main")
}
fn archive_schema_snapshot(connection: &Connection, schema: &str) -> Result<()> {
    let exists:bool=connection.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {schema}.dictionary_versions WHERE revision=(SELECT revision FROM {schema}.dictionary_state WHERE id=1))"),[],|r|r.get(0))?;
    if exists {
        return Ok(());
    }
    let snapshot = read_schema_snapshot(connection, schema)?;
    connection.execute(
        &format!("INSERT OR IGNORE INTO {schema}.dictionary_versions VALUES(?1, ?2)"),
        params![snapshot.version, serde_json::to_string(&snapshot)?],
    )?;
    Ok(())
}
/// The shape every automatic variant uses: any language, no group, no context.
fn automatic_rule(variants: Vec<String>) -> TermRule {
    TermRule {
        variants,
        language: all_languages(),
        matching: MatchMode::UnicodeLowercase,
        context: RuleContext::default(),
    }
}
impl Term {
    #[cfg(test)]
    pub(crate) fn rule_variants(&self) -> usize {
        self.rule.variants.len()
    }
}
impl DictionarySnapshot {
    /// The term holding this variant under case-insensitive matching, if any.
    pub(crate) fn term_of_variant(&self, variant: &str) -> Option<&Term> {
        self.terms.iter().find(|term| {
            term.rule
                .variants
                .iter()
                .any(|v| v.to_lowercase() == variant)
        })
    }
    /// Whether an automatic variant may join this term: only a term that already is
    /// case-insensitive, without context, language or group restriction.
    pub(crate) fn accepts_automatic_variant(&self, term: &str) -> Option<bool> {
        let key = term.trim().to_lowercase();
        self.terms
            .iter()
            .find(|candidate| candidate.text.to_lowercase() == key)
            .map(|found| {
                found.group_ids.is_empty()
                    && found.rule.variants.len() < 128
                    && found.rule.language == "*"
                    && found.rule.matching == MatchMode::UnicodeLowercase
                    && found.rule.context == RuleContext::default()
            })
    }
}
/// Adds one case-insensitive variant to `term` in an attached dictionary, creating the
/// term when it is missing. The caller has validated the combination and owns the
/// transaction; this bumps and archives the revision like any manual save.
pub(crate) fn add_automatic_variant(
    connection: &Connection,
    schema: &str,
    term: &str,
    variant: &str,
) -> Result<i64> {
    let (text, key) = label(term, 200)?;
    let existing: Option<(i64, String)> = connection
        .query_row(
            &format!("SELECT id,rule_json FROM {schema}.terms WHERE text_key=?1"),
            [&key],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let id = if let Some((id, json)) = existing {
        let mut rule: TermRule = serde_json::from_str(&json)?;
        rule.variants.push(variant.to_owned());
        connection.execute(
            &format!("UPDATE {schema}.terms SET rule_json=?1 WHERE id=?2"),
            params![serde_json::to_string(&rule)?, id],
        )?;
        id
    } else {
        connection.execute(
            &format!("INSERT INTO {schema}.terms(text,text_key,rule_json) VALUES(?1,?2,?3)"),
            params![
                text,
                key,
                serde_json::to_string(&automatic_rule(vec![variant.to_owned()]))?
            ],
        )?;
        connection.last_insert_rowid()
    };
    bump(connection, schema)?;
    Ok(id)
}
/// Removes a variant, whichever term holds it. False when it is no longer present.
pub(crate) fn remove_variant(connection: &Connection, schema: &str, variant: &str) -> Result<bool> {
    let terms = connection
        .prepare(&format!("SELECT id,rule_json FROM {schema}.terms"))?
        .query_map([], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut removed = false;
    for (id, json) in terms {
        let mut rule: TermRule = serde_json::from_str(&json)?;
        let before = rule.variants.len();
        rule.variants.retain(|v| v.to_lowercase() != variant);
        if rule.variants.len() != before {
            connection.execute(
                &format!("UPDATE {schema}.terms SET rule_json=?1 WHERE id=?2"),
                params![serde_json::to_string(&rule)?, id],
            )?;
            removed = true;
        }
    }
    if removed {
        bump(connection, schema)?;
    }
    Ok(removed)
}
fn bump(connection: &Connection, schema: &str) -> Result<()> {
    connection.execute(
        &format!("UPDATE {schema}.dictionary_state SET revision = revision + 1 WHERE id = 1"),
        [],
    )?;
    archive_schema_snapshot(connection, schema)
}

pub(crate) fn read_snapshot(connection: &Connection) -> Result<DictionarySnapshot> {
    read_schema_snapshot(connection, "main")
}

pub(crate) fn read_schema_snapshot(
    connection: &Connection,
    schema: &str,
) -> Result<DictionarySnapshot> {
    let groups = connection
        .prepare(&format!(
            "SELECT id, name FROM {schema}.groups ORDER BY name_key, id"
        ))?
        .query_map([], |row| {
            Ok(Group {
                id: row.get(0)?,
                name: row.get(1)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut terms = connection
        .prepare(&format!(
            "SELECT id, text, rule_json FROM {schema}.terms ORDER BY text_key, id"
        ))?
        .query_map([], |row| {
            Ok(Term {
                id: row.get(0)?,
                text: row.get(1)?,
                group_ids: vec![],
                rule: serde_json::from_str(&row.get::<_, String>(2)?).map_err(|error| {
                    rusqlite::Error::FromSqlConversionFailure(
                        2,
                        rusqlite::types::Type::Text,
                        Box::new(error),
                    )
                })?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let positions: std::collections::HashMap<_, _> = terms
        .iter()
        .enumerate()
        .map(|(index, term)| (term.id, index))
        .collect();
    let mut statement = connection.prepare(&format!(
        "SELECT term_id, group_id FROM {schema}.memberships ORDER BY term_id, group_id"
    ))?;
    for membership in
        statement.query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))?
    {
        let (term, group) = membership?;
        if let Some(&position) = positions.get(&term) {
            terms[position].group_ids.push(group);
        }
    }
    let selected_group_ids = connection
        .prepare(&format!(
            "SELECT group_id FROM {schema}.selected_groups ORDER BY group_id"
        ))?
        .query_map([], |row| row.get(0))?
        .collect::<rusqlite::Result<Vec<i64>>>()?;
    let version = connection.query_row(
        &format!("SELECT revision FROM {schema}.dictionary_state WHERE id=1"),
        [],
        |row| row.get(0),
    )?;
    let mut snapshot = DictionarySnapshot {
        terms,
        groups,
        selected_group_ids,
        version,
        conflicts: Vec::new(),
    };
    // Check potential co-activation for each language. Different groups can be
    // selected together, but Italian and English scopes cannot both match.
    let mut potential = snapshot.clone();
    potential.selected_group_ids = potential.groups.iter().map(|group| group.id).collect();
    for language in ["it", "en", "auto"] {
        snapshot
            .conflicts
            .extend(Dictionary::compile(potential.rules(Some(language)))?.conflicts());
    }
    snapshot.conflicts.sort_unstable();
    snapshot.conflicts.dedup();
    Ok(snapshot)
}

impl DictionarySnapshot {
    pub(crate) fn rules(&self, language: Option<&str>) -> Vec<Rule> {
        self.terms
            .iter()
            .filter(|term| {
                language.is_none_or(|language| {
                    (language == "auto"
                        || term.rule.language == "*"
                        || term.rule.language == language)
                        && (term.group_ids.is_empty()
                            || term
                                .group_ids
                                .iter()
                                .any(|id| self.selected_group_ids.contains(id)))
                })
            })
            .flat_map(|term| {
                term.rule.variants.iter().map(|variant| Rule {
                    id: term.id,
                    variant: variant.clone(),
                    preferred: term.text.clone(),
                    mode: term.rule.matching,
                    context: term.rule.context.clone(),
                    scope: u8::from(term.rule.language != "*") * 2
                        + u8::from(!term.group_ids.is_empty()),
                })
            })
            .collect()
    }
}
#[tauri::command]
pub async fn dictionary_select_groups(
    store: tauri::State<'_, DictionaryStore>,
    ids: Vec<i64>,
) -> Result<DictionarySnapshot, String> {
    run(store.inner().clone(), move |store| store.select_groups(ids)).await
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
                    rule: TermRule {
                        language: all_languages(),
                        ..TermRule::default()
                    },
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
        assert_eq!(json["selectedGroupIds"], serde_json::json!([]));
        assert!(json["version"].is_number());
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
                    group_ids: vec![],
                    rule: TermRule {
                        language: all_languages(),
                        ..TermRule::default()
                    },
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: Some(word),
                    text: "école".into(),
                    group_ids: vec![group],
                    rule: TermRule {
                        language: all_languages(),
                        ..TermRule::default()
                    },
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
                    group_ids: vec![group, 999],
                    rule: TermRule {
                        language: all_languages(),
                        ..TermRule::default()
                    },
                })
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_term(SaveTerm {
                    id: None,
                    text: "New".into(),
                    group_ids: vec![999],
                    rule: TermRule {
                        language: all_languages(),
                        ..TermRule::default()
                    },
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
                rule: TermRule {
                    language: all_languages(),
                    ..TermRule::default()
                },
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
                rule: TermRule {
                    language: all_languages(),
                    ..TermRule::default()
                },
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
                        group_ids: vec![],
                        rule: TermRule {
                            language: all_languages(),
                            ..TermRule::default()
                        },
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
                        group_ids: vec![],
                        rule: TermRule {
                            language: all_languages(),
                            ..TermRule::default()
                        },
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
                            rule: TermRule {
                                language: all_languages(),
                                ..TermRule::default()
                            },
                        })
                        .unwrap();
                });
            }
        });
        assert_eq!(fixture.store.snapshot().unwrap().terms.len(), 8);
    }
    fn configured(text: &str, variants: &[&str], groups: Vec<i64>, language: &str) -> SaveTerm {
        serde_json::from_value(serde_json::json!({"id":null,"text":text,"groupIds":groups,"variants":variants,"language":language,"matching":"exact","context":{"before":null,"after":null,"exceptBefore":[],"exceptAfter":[]}})).unwrap()
    }
    fn output(dictionary: Dictionary, text: &str) -> String {
        let mut processor = voiceflow_core::formatting::TextProcessor::with_dictionary(
            voiceflow_core::formatting::FormatMode::Faithful,
            false,
            dictionary,
        );
        processor.update(text, true).unwrap();
        processor.parts().0.into()
    }
    #[test]
    fn confirmed_variants_group_language_reopen_and_snapshot_immutability() {
        let fixture = Fixture::new();
        let project = fixture.group("Project");
        fixture
            .store
            .save_term(configured(
                "VoiceFlow",
                &["voice flow"],
                vec![project],
                "it",
            ))
            .unwrap();
        fixture
            .store
            .save_term(configured("Memo", &[], vec![], "*"))
            .unwrap();
        let (inactive, groups, _) = fixture.store.session_snapshot("it-IT").unwrap();
        assert!(groups.is_empty());
        assert_eq!(output(inactive, "voice flow memo"), "voice flow memo");
        fixture.store.select_groups(vec![project]).unwrap();
        let (active, groups, version) = fixture.store.session_snapshot("it-IT").unwrap();
        assert_eq!(groups, [format!("{project}:Project")]);
        assert_eq!(output(active.clone(), "voice flow"), "VoiceFlow");
        assert_eq!(
            output(
                fixture.store.session_snapshot("en-US").unwrap().0,
                "voice flow"
            ),
            "voice flow"
        );
        assert_eq!(
            output(
                fixture.store.session_snapshot("auto").unwrap().0,
                "voice flow"
            ),
            "VoiceFlow"
        );
        let reopened = DictionaryStore::new(fixture.root.join("dictionary.sqlite3"));
        assert_eq!(reopened.session_snapshot("it-IT").unwrap().2, version);
        reopened.select_groups(vec![]).unwrap();
        assert_eq!(output(active, "voice flow"), "VoiceFlow");
        assert_eq!(
            output(reopened.session_snapshot("it-IT").unwrap().0, "voice flow"),
            "voice flow"
        );
        assert_ne!(reopened.session_snapshot("it-IT").unwrap().2, version);
    }
    #[test]
    fn validation_and_selection_failures_roll_back_version_and_data() {
        let fixture = Fixture::new();
        let group = fixture.group("One");
        fixture.store.select_groups(vec![group]).unwrap();
        let before = fixture.store.snapshot().unwrap();
        assert!(fixture.store.select_groups(vec![999]).is_err());
        assert!(
            fixture
                .store
                .save_term(configured(
                    "Long",
                    &["one two three four five six seven eight"],
                    vec![],
                    "*"
                ))
                .is_err()
        );
        assert!(
            fixture
                .store
                .save_term(configured("Bad", &["word"], vec![], "zz"))
                .is_err()
        );
        assert_eq!(fixture.store.snapshot().unwrap(), before);
        assert!(
            fixture
                .store
                .delete_group(group)
                .unwrap()
                .selected_group_ids
                .is_empty()
        );
        assert!(serde_json::from_value::<SaveTerm>(serde_json::json!({"id":null,"text":"Bad","groupIds":[],"variants":[],"invented":true})).is_err());
    }
    #[test]
    fn management_surfaces_cross_language_conflicts_in_automatic_mode() {
        let fixture = Fixture::new();
        let mut a = configured("First", &["variant"], vec![], "it");
        a.rule.context.before = Some("x".into());
        let mut b = configured("Second", &["variant"], vec![], "it");
        b.rule.context.after = Some("y".into());
        fixture.store.save_term(a).unwrap();
        assert_eq!(fixture.store.save_term(b).unwrap().conflicts.len(), 2);
        assert_eq!(
            output(
                fixture.store.session_snapshot("it-IT").unwrap().0,
                "x variant y"
            ),
            "x variant y"
        );
        let other = Fixture::new();
        other
            .store
            .save_term(configured("First", &["variant"], vec![], "it"))
            .unwrap();
        assert_eq!(
            other
                .store
                .save_term(configured("Second", &["variant"], vec![], "en"))
                .unwrap()
                .conflicts
                .len(),
            2
        );
        for (language, expected) in [("it", "First"), ("en", "Second"), ("auto", "variant")] {
            assert_eq!(
                output(other.store.session_snapshot(language).unwrap().0, "variant"),
                expected
            );
        }
    }
    #[test]
    fn v1_migration_preserves_terms_without_inventing_variants_and_is_idempotent() {
        let fixture = Fixture::new();
        fs::create_dir_all(&fixture.root).unwrap();
        let path = fixture.root.join("dictionary.sqlite3");
        let old = Connection::open(&path).unwrap();
        old.execute_batch("CREATE TABLE terms(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT NOT NULL,text_key TEXT NOT NULL UNIQUE);CREATE TABLE groups(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE);CREATE TABLE memberships(term_id INTEGER REFERENCES terms(id) ON DELETE CASCADE,group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,PRIMARY KEY(term_id,group_id));INSERT INTO terms VALUES(7,'VoiceFlow','voiceflow');PRAGMA user_version=1;").unwrap();
        drop(old);
        let migrated = fixture.store.snapshot().unwrap();
        assert_eq!(migrated.terms[0].id, 7);
        assert!(migrated.terms[0].rule.variants.is_empty());
        assert_eq!(migrated.version, 1);
        assert_eq!(DictionaryStore::new(path).snapshot().unwrap(), migrated);
        assert!(
            fixture
                .store
                .session_snapshot("it-IT")
                .unwrap()
                .0
                .is_empty()
        );
    }
    #[test]
    fn failed_v2_migration_rolls_back_schema_and_can_be_retried() {
        let fixture = Fixture::new();
        fs::create_dir_all(&fixture.root).unwrap();
        let path = fixture.root.join("dictionary.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection.execute_batch("CREATE TABLE terms(id INTEGER PRIMARY KEY,text TEXT,text_key TEXT); CREATE TABLE dictionary_state(existing TEXT); PRAGMA user_version=1;").unwrap();
        assert!(fixture.store.snapshot().is_err());
        assert_eq!(
            connection
                .pragma_query_value::<i64, _>(None, "user_version", |row| row.get(0))
                .unwrap(),
            1
        );
        assert!(connection.prepare("SELECT rule_json FROM terms").is_err());
        connection.execute_batch("DROP TABLE dictionary_state; CREATE TABLE groups(id INTEGER PRIMARY KEY,name TEXT,name_key TEXT);CREATE TABLE memberships(term_id INTEGER,group_id INTEGER);").unwrap();
        assert!(fixture.store.snapshot().is_ok());
    }
    #[test]
    fn independently_opened_stores_migrate_once_under_concurrent_startup() {
        let fixture = Fixture::new();
        std::thread::scope(|scope| {
            for index in 0..8 {
                let store = DictionaryStore::new(fixture.root.join("dictionary.sqlite3"));
                scope.spawn(move || {
                    store
                        .save_term(configured(&format!("Term{index}"), &[], vec![], "*"))
                        .unwrap();
                });
            }
        });
        assert_eq!(fixture.store.snapshot().unwrap().terms.len(), 8);
    }
}
