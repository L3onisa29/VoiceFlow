//! Review boundary for analyzer hypotheses. Local policy can approve validated analysis findings atomically.
use crate::{
    dictionary::{DictionarySnapshot, DictionaryStore},
    history::HistoryStore,
};
use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use voiceflow_core::{
    cleanup::{Catalog, CompiledCatalog, Example, Operation, Provenance, Rule as CleanupRule},
    dictionary::{Context as RuleContext, Dictionary, MatchMode, Rule as DictionaryRule},
    formatting::{FormatMode, TextProcessor},
};

pub mod replay;
pub mod spellings;

#[derive(Clone)]
pub struct SuggestionStore {
    history: HistoryStore,
    dictionary: DictionaryStore,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "category", rename_all = "kebab-case", deny_unknown_fields)]
pub enum Definition {
    Terminology {
        preferred: String,
        variants: Vec<String>,
        matching: MatchMode,
        context: RuleContext,
    },
    Cleanup {
        operation: Operation,
        context: voiceflow_core::cleanup::Context,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Candidate {
    pub definition: Definition,
    pub language: String,
    pub group_ids: Vec<i64>,
    pub reason: String,
    pub positive_examples: Vec<Example>,
    pub negative_examples: Vec<Example>,
}
/// Reference only. Text is always resolved from the current history database.
/// revisionId identifies a remembered, non-reverted human correction; originalIndex
/// identifies one raw ASR segment. Both absent means the produced text, never human confirmation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EvidenceRef {
    pub session_id: i64,
    pub revision_id: Option<i64>,
    pub original_index: Option<u32>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct IngestRequest {
    pub candidate: Candidate,
    pub evidence: Vec<EvidenceRef>,
}
/// Only explicit domain validation failures may consume an analysis source.
/// Unmarked errors, including storage/serialization faults, remain retryable.
#[derive(Debug)]
pub(crate) struct CandidateRejection(pub anyhow::Error);
impl std::fmt::Display for CandidateRejection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for CandidateRejection {}
/// Validation failure with a stable machine code. The code is ours: it never carries
/// provider or source text, so it can be stored and shown after the sources are gone.
#[derive(Debug)]
pub(crate) struct Coded {
    pub code: &'static str,
    message: String,
}
impl std::fmt::Display for Coded {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for Coded {}
pub(crate) fn coded(code: &'static str, message: impl Into<String>) -> Coded {
    debug_assert!(
        crate::analysis::rejections::hint(code).is_some(),
        "codice di rifiuto senza descrizione: {code}"
    );
    Coded {
        code,
        message: message.into(),
    }
}
fn reject(code: &'static str, message: &str) -> CandidateRejection {
    CandidateRejection(anyhow::Error::new(coded(code, message)))
}
/// Give an uncoded failure (compiler, parser) a code without changing its message.
fn recode(code: &'static str) -> impl Fn(anyhow::Error) -> anyhow::Error {
    move |error| {
        if error.is::<Coded>() || error.is::<CandidateRejection>() {
            error
        } else {
            anyhow::Error::new(coded(code, format!("{error:#}")))
        }
    }
}
pub(crate) fn rejection_code(error: &anyhow::Error) -> &'static str {
    if let Some(coded) = error.downcast_ref::<Coded>() {
        coded.code
    } else if let Some(rejection) = error.downcast_ref::<CandidateRejection>() {
        rejection_code(&rejection.0)
    } else {
        "rule-invalid"
    }
}
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum IngestFailure {
    /// Stable rejection code, see `analysis::rejections`.
    InvalidCandidate(&'static str),
    Infrastructure,
}
impl IngestFailure {
    fn classify(error: anyhow::Error) -> Self {
        if error.is::<CandidateRejection>() {
            Self::InvalidCandidate(rejection_code(&error))
        } else {
            Self::Infrastructure
        }
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Evidence {
    #[serde(flatten)]
    pub reference: EvidenceRef,
    pub before: String,
    pub after: Option<String>,
    pub human_confirmed: bool,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Proposal {
    pub id: i64,
    pub revision: i64,
    pub candidate: Candidate,
    pub state: String,
    pub rejection_reason: Option<String>,
    pub evidence: Vec<Evidence>,
    pub conflicts: Vec<String>,
    pub approved_at: Option<i64>,
    pub confidence: Option<u8>,
    pub analysis_outcome: Option<String>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Publication {
    pub version: i64,
    pub previous_version: Option<i64>,
    pub action: String,
    pub created_at: i64,
    pub proposal_ids: Vec<i64>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSnapshot {
    #[serde(skip)]
    validation_dictionary: DictionarySnapshot,
    pub proposals: Vec<Proposal>,
    pub publications: Vec<Publication>,
    pub version: i64,
    pub dictionary_version: i64,
    pub groups: Vec<crate::dictionary::Group>,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReviewRequest {
    pub id: Option<i64>,
    pub expected_revision: Option<i64>,
    pub expected_version: i64,
    pub dictionary_version: i64,
    pub request_key: String,
    pub action: ReviewAction,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub enum ReviewAction {
    Approve,
    Reject { reason: String },
    Defer,
    Disable,
    Edit { candidate: Box<Candidate> },
    Restore { version: i64 },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ApprovedRule {
    id: i64,
    candidate: Candidate,
}
#[derive(Debug)]
pub struct SessionRules {
    pub dictionary: Dictionary,
    pub cleanup: CompiledCatalog,
    pub groups: Vec<String>,
    pub dictionary_version: String,
}

pub(crate) fn migrate(connection: &Connection) -> Result<()> {
    connection.execute_batch("CREATE TABLE IF NOT EXISTS suggestions_schema(version INTEGER NOT NULL);
        INSERT INTO suggestions_schema SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM suggestions_schema);")?;
    let version: i64 =
        connection.query_row("SELECT version FROM suggestions_schema", [], |r| r.get(0))?;
    ensure!(
        version == 1,
        "Suggerimenti creati da una versione più recente di VoiceFlow."
    );
    connection.execute_batch("CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL UNIQUE,
        candidate_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL CHECK(state IN ('proposed','rejected','deferred','approved')),
        rejection_reason TEXT, approved_at INTEGER);
        CREATE TABLE IF NOT EXISTS suggestion_evidence (
        proposal_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
        session_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,
        revision_id INTEGER REFERENCES history_revisions(id) ON DELETE CASCADE,
        original_index INTEGER, evidence_key TEXT NOT NULL,
        PRIMARY KEY(proposal_id,evidence_key));
        CREATE TABLE IF NOT EXISTS suggestion_publications (
        version INTEGER PRIMARY KEY AUTOINCREMENT, previous_version INTEGER,
        action TEXT NOT NULL, created_at INTEGER NOT NULL, rules_json TEXT NOT NULL,
        dictionary_version INTEGER NOT NULL, builtin_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suggestion_catalogs(version TEXT PRIMARY KEY, definition_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suggestion_state(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
        INSERT OR IGNORE INTO suggestion_state VALUES(1,0);
        CREATE TABLE IF NOT EXISTS suggestion_actions (
        request_key TEXT PRIMARY KEY, proposal_id INTEGER REFERENCES suggestions(id) ON DELETE CASCADE,
        action TEXT NOT NULL, publication INTEGER NOT NULL, created_at INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS remove_unapproved_suggestions BEFORE DELETE ON transcriptions
        BEGIN DELETE FROM suggestions WHERE approved_at IS NULL AND id IN
        (SELECT proposal_id FROM suggestion_evidence WHERE session_id=OLD.id); END;")?;
    // Reject partial/corrupt feature schemas before committing the history migration.
    connection.prepare("SELECT s.id,s.identity,s.candidate_json,s.revision,s.state,s.rejection_reason,s.approved_at,e.session_id,e.revision_id,e.original_index,e.evidence_key FROM suggestions s LEFT JOIN suggestion_evidence e ON e.proposal_id=s.id LIMIT 0")?;
    connection.prepare("SELECT version,previous_version,action,created_at,rules_json,dictionary_version,builtin_version FROM suggestion_publications LIMIT 0")?;
    connection.execute_batch("CREATE INDEX IF NOT EXISTS suggestion_evidence_session ON suggestion_evidence(session_id);")?;
    replay::migrate(connection)?;
    let builtin = CompiledCatalog::builtin();
    connection.execute(
        "INSERT OR IGNORE INTO suggestion_catalogs VALUES(?1,?2)",
        params![
            builtin.version(),
            serde_json::to_string(builtin.definition())?
        ],
    )?;
    Ok(())
}
fn now() -> Result<i64> {
    Ok(std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as i64)
}
fn text(value: &str, max: usize) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.len() <= max && !value.chars().any(char::is_control),
        "Testo vuoto, troppo lungo o non valido."
    );
    Ok(())
}
fn canonical(candidate: &Candidate) -> Result<String> {
    Ok(serde_json::to_string(&(
        &candidate.definition,
        &candidate.language,
        &candidate.group_ids,
    ))?)
}
pub(crate) fn candidate_hash(candidate: &Candidate) -> Result<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&normalize(candidate.clone()))?)
    ))
}
fn normalize(mut candidate: Candidate) -> Candidate {
    candidate.group_ids.sort_unstable();
    candidate.group_ids.dedup();
    if let Definition::Terminology {
        variants,
        matching,
        context,
        ..
    } = &mut candidate.definition
    {
        if *matching == MatchMode::UnicodeLowercase {
            for variant in variants.iter_mut() {
                *variant = variant.to_lowercase();
            }
            context.before = context.before.take().map(|v| v.to_lowercase());
            context.after = context.after.take().map(|v| v.to_lowercase());
            for value in context
                .except_before
                .iter_mut()
                .chain(context.except_after.iter_mut())
            {
                *value = value.to_lowercase();
            }
        }
        variants.sort();
        variants.dedup();
        context.except_before.sort();
        context.except_before.dedup();
        context.except_after.sort();
        context.except_after.dedup();
    } else if let Definition::Cleanup { context, .. } = &mut candidate.definition {
        context.except_before.sort();
        context.except_before.dedup();
        context.except_after.sort();
        context.except_after.dedup();
    }
    candidate
}
fn cleanup_rule(id: i64, candidate: &Candidate) -> Option<CleanupRule> {
    if let Definition::Cleanup { operation, context } = &candidate.definition {
        Some(CleanupRule {
            id: format!("approved-{id}"),
            version: 1,
            language: candidate.language.clone(),
            operation: operation.clone(),
            context: context.clone(),
            provenance: Provenance {
                source: "explicit-user-approval".into(),
                evidence_ids: vec![],
            },
            positive_examples: candidate.positive_examples.clone(),
            negative_examples: candidate.negative_examples.clone(),
        })
    } else {
        None
    }
}

/// Evaluate the declared rule on its cited excerpt. The result is computed by
/// the same processor as live dictation, rather than inferred from an example.
pub(crate) fn render_candidate_excerpt(candidate: &Candidate, text: &str) -> Result<String> {
    let (before, after) = match &candidate.definition {
        Definition::Terminology { context, .. } => {
            (context.before.as_deref(), context.after.as_deref())
        }
        Definition::Cleanup { context, .. } => {
            (context.before.as_deref(), context.after.as_deref())
        }
    };
    let prefix = before.map(|word| format!("{word} ")).unwrap_or_default();
    let suffix = after.map(|word| format!(" {word}")).unwrap_or_default();
    let dictionary =
        Dictionary::compile(dictionary_rules(1, candidate)).map_err(recode("rule-invalid"))?;
    let catalog = CompiledCatalog::compile(
        Catalog {
            schema_version: 1,
            version: "analysis-excerpt-v1".into(),
            rules: cleanup_rule(1, candidate).into_iter().collect(),
        },
        "auto",
    )
    .map_err(recode("rule-invalid"))?;
    let mut processor = TextProcessor::with_rules(FormatMode::Faithful, true, dictionary, catalog);
    processor.update(&format!("{prefix}{text}{suffix}"), true)?;
    processor
        .parts()
        .0
        .strip_prefix(&prefix)
        .and_then(|text| text.strip_suffix(&suffix))
        .map(str::to_owned)
        .ok_or_else(|| {
            anyhow::Error::new(coded(
                "rule-changes-context",
                "La regola modifica il contesto esterno all'estratto",
            ))
        })
}
fn dictionary_rules(id: i64, candidate: &Candidate) -> Vec<DictionaryRule> {
    if let Definition::Terminology {
        preferred,
        variants,
        matching,
        context,
    } = &candidate.definition
    {
        variants
            .iter()
            .map(|variant| DictionaryRule {
                id: -id,
                variant: variant.clone(),
                preferred: preferred.clone(),
                mode: *matching,
                context: context.clone(),
                scope: u8::from(candidate.language != "*") * 2
                    + u8::from(!candidate.group_ids.is_empty()),
            })
            .collect()
    } else {
        vec![]
    }
}
fn validate(candidate: &Candidate, dictionary: &DictionarySnapshot) -> Result<()> {
    text(&candidate.reason, 2000).map_err(recode("reason-text"))?;
    ensure!(
        ["*", "it", "en"].contains(&candidate.language.as_str()),
        coded("language", "Lingua non valida.")
    );
    ensure!(
        candidate.group_ids.len() <= 32
            && candidate
                .group_ids
                .iter()
                .all(|id| dictionary.groups.iter().any(|g| g.id == *id)),
        coded("groups", "Gruppi non più disponibili.")
    );
    for examples in [&candidate.positive_examples, &candidate.negative_examples] {
        ensure!(
            !examples.is_empty() && examples.len() <= 16,
            coded(
                "examples-count",
                "Servono da 1 a 16 esempi positivi e di controllo."
            )
        );
        for e in examples {
            text(&e.input, 2048).map_err(recode("example-text"))?;
            text(&e.output, 2048).map_err(recode("example-text"))?;
            ensure!(
                !e.input.contains(['<', '>']) && !e.output.contains(['<', '>']),
                coded("example-markers", "Marcatori non ammessi negli esempi.")
            );
        }
    }
    ensure!(
        candidate
            .negative_examples
            .iter()
            .all(|e| e.input == e.output),
        coded(
            "negative-example-changed",
            "Le frasi di controllo devono restare invariate."
        )
    );
    ensure!(
        candidate
            .positive_examples
            .iter()
            .all(|e| e.input != e.output),
        coded(
            "positive-example-unchanged",
            "Gli esempi positivi devono mostrare una modifica."
        )
    );
    if let Definition::Terminology { variants, .. } = &candidate.definition {
        ensure!(
            !variants.is_empty() && variants.len() <= 32,
            coded("variants-count", "Servono da 1 a 32 varianti esplicite.")
        );
        let d =
            Dictionary::compile(dictionary_rules(1, candidate)).map_err(recode("rule-invalid"))?;
        let c = CompiledCatalog::compile(
            Catalog {
                schema_version: 1,
                version: "validation".into(),
                rules: vec![],
            },
            "it",
        )?;
        check_examples(
            candidate
                .positive_examples
                .iter()
                .chain(&candidate.negative_examples),
            &d,
            &c,
            false,
        )?;
    } else {
        CompiledCatalog::compile(
            Catalog {
                schema_version: 1,
                version: "validation".into(),
                rules: vec![cleanup_rule(1, candidate).unwrap()],
            },
            &candidate.language.replace('*', "it"),
        )
        .map_err(recode("rule-invalid"))?;
    }
    Ok(())
}
fn check_examples<'a>(
    examples: impl Iterator<Item = &'a Example>,
    dictionary: &Dictionary,
    cleanup: &CompiledCatalog,
    enabled: bool,
) -> Result<()> {
    for e in examples {
        let mut p = TextProcessor::with_rules(
            FormatMode::SpokenCommands,
            enabled,
            dictionary.clone(),
            cleanup.clone(),
        );
        p.update(&e.input, true)?;
        let (confirmed, partial) = p.parts();
        let output = format!("{confirmed}{partial}");
        ensure!(
            output == e.output,
            coded(
                "example-mismatch",
                format!(
                    "Conflitto sull'esempio «{}»: atteso «{}», ottenuto «{}».",
                    e.input, e.output, output
                )
            )
        );
    }
    Ok(())
}
fn current(connection: &Connection) -> Result<i64> {
    Ok(
        connection.query_row("SELECT version FROM suggestion_state WHERE id=1", [], |r| {
            r.get(0)
        })?,
    )
}
fn rules_at(connection: &Connection, version: i64) -> Result<Vec<ApprovedRule>> {
    if version == 0 {
        return Ok(vec![]);
    }
    let json: String = connection
        .query_row(
            "SELECT rules_json FROM suggestion_publications WHERE version=?1",
            [version],
            |r| r.get(0),
        )
        .context("Versione non disponibile.")?;
    Ok(serde_json::from_str(&json)?)
}
// Read-only projection used by analysis history, with the same publication
// semantics as the current suggestions snapshot.
pub(crate) fn active_proposal_ids(connection: &Connection) -> Result<Vec<i64>> {
    Ok(rules_at(connection, current(connection)?)?
        .into_iter()
        .map(|rule| rule.id)
        .collect())
}
fn compile(
    dictionary: &DictionarySnapshot,
    rules: &[ApprovedRule],
    version: i64,
    language: &str,
) -> Result<(Dictionary, CompiledCatalog)> {
    compile_with_baseline(
        dictionary,
        rules,
        version,
        language,
        CompiledCatalog::builtin().definition().clone(),
    )
}
fn compile_with_baseline(
    dictionary: &DictionarySnapshot,
    rules: &[ApprovedRule],
    version: i64,
    language: &str,
    mut catalog: Catalog,
) -> Result<(Dictionary, CompiledCatalog)> {
    let language = language.split('-').next().unwrap_or(language);
    let mut ds = dictionary.rules(Some(language));
    if version != 0 {
        catalog.version = format!("cleanup-review-v1:{version}");
    }
    for rule in rules {
        let c = &rule.candidate;
        if !c.group_ids.is_empty()
            && !c
                .group_ids
                .iter()
                .any(|g| dictionary.selected_group_ids.contains(g))
        {
            continue;
        }
        if language == "auto" || c.language == "*" || c.language == language {
            ds.extend(dictionary_rules(rule.id, c));
        }
        if let Some(c) = cleanup_rule(rule.id, c) {
            catalog.rules.push(c);
        }
    }
    Ok((
        Dictionary::compile(ds)?,
        CompiledCatalog::compile(catalog, language)?,
    ))
}
fn validate_combination(dictionary: &DictionarySnapshot, rules: &[ApprovedRule]) -> Result<()> {
    for rule in rules {
        validate(&rule.candidate, dictionary)?;
    }
    match combination_failures(dictionary, rules)?.into_iter().next() {
        Some(failure) => Err(failure),
        None => Ok(()),
    }
}
/// Every expectation the combined dictionary, rules and builtins fail to meet, in a stable
/// order. An error is reserved for a set that does not compile at all.
/// A caller that adds one thing to an already published set compares the two lists, so an
/// older inconsistency is not blamed on the newcomer.
fn combination_failures(
    dictionary: &DictionarySnapshot,
    rules: &[ApprovedRule],
) -> Result<Vec<anyhow::Error>> {
    let mut failures = vec![];
    let mut expect = |examples: &mut dyn Iterator<Item = &Example>,
                      compiled: &Dictionary,
                      catalog: &CompiledCatalog| {
        for example in examples {
            if let Err(failure) = check_examples(std::iter::once(example), compiled, catalog, true)
            {
                failures.push(failure);
            }
        }
    };
    // Test all-groups and each individual group scope. No hidden automatic context selection.
    let mut scopes = vec![
        vec![],
        dictionary.selected_group_ids.clone(),
        dictionary.groups.iter().map(|g| g.id).collect(),
    ];
    scopes.extend(dictionary.groups.iter().map(|g| vec![g.id]));
    for groups in scopes {
        let mut d = dictionary.clone();
        d.selected_group_ids = groups;
        for language in ["it", "en", "auto"] {
            let (compiled, catalog) = compile(&d, rules, 1, language)?;
            if !compiled.conflicts().is_empty() {
                // Nothing else is meaningful while two variants compete.
                return Ok(vec![anyhow::anyhow!(
                    "Conflitto tra varianti del dizionario e regole proposte: {:?}.",
                    compiled.conflicts()
                )]);
            }
            for rule in rules {
                let c = &rule.candidate;
                if (language == "auto" || c.language == "*" || c.language == language)
                    && (c.group_ids.is_empty()
                        || c.group_ids
                            .iter()
                            .any(|id| d.selected_group_ids.contains(id)))
                {
                    expect(
                        &mut c.positive_examples.iter().chain(&c.negative_examples),
                        &compiled,
                        &catalog,
                    );
                }
            }
            for rule in &catalog.definition().rules {
                if language == "auto" || rule.language == "*" || rule.language == language {
                    expect(
                        &mut rule.positive_examples.iter().chain(&rule.negative_examples),
                        &compiled,
                        &catalog,
                    );
                }
            }
            // Manual dictionary rules also keep their own expected behavior through cleanup/commands.
            for term in d.rules(Some(language)) {
                let input = [
                    term.context.before.as_deref(),
                    Some(term.variant.as_str()),
                    term.context.after.as_deref(),
                ]
                .into_iter()
                .flatten()
                .collect::<Vec<_>>()
                .join(" ");
                let expected = [
                    term.context.before.as_deref(),
                    Some(term.preferred.as_str()),
                    term.context.after.as_deref(),
                ]
                .into_iter()
                .flatten()
                .collect::<Vec<_>>()
                .join(" ");
                expect(
                    &mut std::iter::once(&Example {
                        input,
                        output: expected,
                    }),
                    &compiled,
                    &catalog,
                );
            }
            for input in [
                "non va bene",
                "molto molto importante",
                "quel ragionamento di prima non va bene",
                "la dispositivo",
                "Mario 42",
                "do not remove 123",
                "very very important",
            ] {
                expect(
                    &mut std::iter::once(&Example {
                        input: input.into(),
                        output: input.into(),
                    }),
                    &compiled,
                    &catalog,
                );
            }
        }
    }
    Ok(failures)
}
pub(crate) fn evidence(connection: &Connection, reference: EvidenceRef) -> Result<Evidence> {
    ensure!(
        reference.session_id > 0
            && !(reference.revision_id.is_some() && reference.original_index.is_some()),
        reject("evidence-reference", "Riferimento evidenza non valido.")
    );
    let (produced, raw): (String, Option<String>) = connection
        .query_row(
            "SELECT produced_text, original_asr_json FROM transcriptions WHERE id=?1",
            [reference.session_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?
        .ok_or_else(|| {
            reject(
                "session-missing",
                "Sessione sorgente eliminata o inesistente.",
            )
        })?;
    let (before, after, human_confirmed) = if let Some(id) = reference.revision_id {
        connection.query_row("SELECT r.before_text,r.text,EXISTS(SELECT 1 FROM confirmed_corrections c WHERE c.revision_id=r.id) AND r.reverted=0 FROM history_revisions r WHERE r.id=?1 AND r.transcription_id=?2", params![id,reference.session_id], |r|Ok((r.get(0)?,Some(r.get(1)?),r.get(2)?))).optional()?.ok_or_else(|| reject("revision-foreign","Revisione non appartenente alla sessione."))?
    } else if let Some(index) = reference.original_index {
        let parts: Vec<String> = serde_json::from_str(
            &raw.ok_or_else(|| reject("asr-unavailable", "Originale ASR non disponibile."))?,
        )?;
        (
            parts
                .get(index as usize)
                .ok_or_else(|| reject("segment-missing", "Segmento originale inesistente."))?
                .clone(),
            None,
            false,
        )
    } else {
        (produced, None, false)
    };
    Ok(Evidence {
        reference,
        before,
        after,
        human_confirmed,
    })
}
pub(crate) fn contains_example(source: &str, example: &str) -> bool {
    source.match_indices(example).any(|(offset, _)| {
        // Processor words are whitespace-delimited. Only terminal prose punctuation
        // may follow a match; combining marks and URL/code syntax remain in the token.
        source[..offset]
            .chars()
            .next_back()
            .is_none_or(char::is_whitespace)
            && source[offset + example.len()..]
                .trim_start_matches([',', '.', ':', ';', '!', '?'])
                .chars()
                .next()
                .is_none_or(char::is_whitespace)
    })
}
fn validate_evidence(candidate: &Candidate, e: &Evidence) -> Result<()> {
    if e.reference.revision_id.is_some() {
        ensure!(
            e.human_confirmed,
            coded(
                "revision-not-confirmed",
                "La revisione deve essere ricordata e non annullata."
            )
        );
    }
    ensure!(
        candidate
            .positive_examples
            .iter()
            .any(|example| contains_example(&e.before, &example.input)
                && e.after
                    .as_ref()
                    .is_none_or(|after| contains_example(after, &example.output))),
        coded(
            "evidence-lacks-example",
            "L'evidenza non contiene alcun esempio positivo proposto."
        )
    );
    Ok(())
}
fn read_evidence(connection: &Connection, id: i64) -> Result<Vec<Evidence>> {
    let refs = connection.prepare("SELECT session_id,revision_id,original_index FROM suggestion_evidence WHERE proposal_id=?1 ORDER BY session_id,revision_id,original_index")?.query_map([id], |r|Ok(EvidenceRef{session_id:r.get(0)?,revision_id:r.get(1)?,original_index:r.get(2)?}))?.collect::<rusqlite::Result<Vec<_>>>()?;
    refs.into_iter().map(|r| evidence(connection, r)).collect()
}
impl SuggestionStore {
    pub fn open(history_path: std::path::PathBuf, dictionary_path: std::path::PathBuf) -> Self {
        Self::new(
            HistoryStore::new(history_path),
            DictionaryStore::new(dictionary_path),
        )
    }
    pub fn new(history: HistoryStore, dictionary: DictionaryStore) -> Self {
        Self {
            history,
            dictionary,
        }
    }
    fn access<T>(&self, operation: impl FnOnce(&mut Connection) -> Result<T>) -> Result<T> {
        // Initialize/migrate before attaching. Always acquire dictionary before history, then
        // release its Rust mutex. SQLite owns the cross-process read/write serialization.
        self.dictionary.snapshot()?;
        let path = self.dictionary.path()?;
        self.history.access(|connection| {
            connection.execute(
                "ATTACH DATABASE ?1 AS vocabulary",
                [path.to_str().context("Percorso dizionario non valido")?],
            )?;
            operation(connection)
        })
    }
    pub fn ingest(&self, request: IngestRequest) -> Result<ReviewSnapshot> {
        self.ingest_scoped(request, None, None, &|| false)?;
        self.snapshot()
    }
    /// Analysis capability validates the prepared allowlist and privacy within this transaction.
    pub(crate) fn ingest_prepared(
        &self,
        request: IngestRequest,
        batch_id: i64,
        attempt_id: i64,
    ) -> std::result::Result<(), IngestFailure> {
        self.ingest_scoped(request, Some((batch_id, attempt_id)), None, &|| false)
            .map_err(IngestFailure::classify)
    }
    pub(crate) fn ingest_finding(
        &self,
        finding: crate::analysis::findings::Finding,
        batch_id: i64,
        attempt_id: i64,
        cancel: &dyn crate::analysis::Cancellation,
    ) -> std::result::Result<(), IngestFailure> {
        let result = if let Some(candidate) = finding.candidate.clone() {
            self.ingest_scoped(
                IngestRequest {
                    candidate,
                    evidence: finding.evidence.clone(),
                },
                Some((batch_id, attempt_id)),
                Some(&finding),
                cancel,
            )
        } else {
            self.access(|connection| {
                let tx=connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
                crate::analysis::validate_scope(&tx,batch_id,&finding.evidence)?;
                ensure!(tx.query_row("SELECT EXISTS(SELECT 1 FROM analysis_attempts WHERE id=?1 AND batch_id=?2 AND state='running')",params![attempt_id,batch_id],|r|r.get::<_,bool>(0))?,"Tentativo non attivo.");
                finding.validate(&tx).map_err(CandidateRejection)?;
                ensure!(!cancel.cancelled(),"Analisi annullata.");
                let exists:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM analysis_findings WHERE attempt_id=?1 AND identity=?2)",params![attempt_id,finding.key()?],|r|r.get(0))?;
                if !exists { finding.save(&tx,attempt_id,None,"no-change",None)?; }
                tx.commit()?; Ok(())
            })
        };
        result.map_err(IngestFailure::classify)
    }
    fn ingest_scoped(
        &self,
        request: IngestRequest,
        scope: Option<(i64, i64)>,
        finding: Option<&crate::analysis::findings::Finding>,
        cancel: &dyn crate::analysis::Cancellation,
    ) -> Result<()> {
        ensure!(
            !request.evidence.is_empty() && request.evidence.len() <= 64,
            reject("evidence-count", "Servono da 1 a 64 riferimenti reali.")
        );
        let candidate = normalize(request.candidate);
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let dictionary = crate::dictionary::read_schema_snapshot(&tx,"vocabulary")?;
            if let Some((id,attempt))=scope { crate::analysis::validate_scope(&tx,id,&request.evidence)?; ensure!(tx.query_row("SELECT EXISTS(SELECT 1 FROM analysis_attempts WHERE id=?1 AND batch_id=?2 AND state='running')",params![attempt,id],|r|r.get::<_,bool>(0))?,"Tentativo non attivo."); }
            if let (Some(finding),Some((_,attempt)))=(finding,scope) {
                finding.validate(&tx).map_err(CandidateRejection)?;
                let exists:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM analysis_findings WHERE attempt_id=?1 AND identity=?2)",params![attempt,finding.key()?],|r|r.get(0))?;
                if exists { return Ok(()); }
            }
            validate(&candidate,&dictionary).map_err(CandidateRejection)?;
            for reference in &request.evidence { validate_evidence(&candidate,&evidence(&tx,reference.clone())?).map_err(CandidateRejection)?; }
            // These validated repetitions are already handled for every new dictation.
            // Consume the analysis result without creating another review card or rule.
            if finding.is_some() && handled_by_local_cleanup(&candidate)? {
                ensure!(!cancel.cancelled(), "Analisi annullata.");
                tx.commit()?;
                return Ok(());
            }
            let identity = canonical(&candidate)?;
            let existing: Option<(i64,String)> = tx.query_row("SELECT id,candidate_json FROM suggestions WHERE identity=?1",[&identity],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
            let created = existing.is_none();
            let id = if let Some((id,json)) = existing {
                let old:Candidate=serde_json::from_str(&json)?;
                // A duplicate cannot silently alter a reviewed definition or its examples.
                for reference in &request.evidence { validate_evidence(&old,&evidence(&tx,reference.clone())?).map_err(CandidateRejection)?; }
                id
            } else {
                tx.execute("INSERT INTO suggestions(identity,candidate_json,state) VALUES(?1,?2,'proposed')",params![identity,serde_json::to_string(&candidate)?])?; tx.last_insert_rowid()
            };
            if created { tx.execute("INSERT INTO suggestion_auto_origin VALUES(?1,?2)",params![id,finding.is_some_and(|f|f.confidence.is_some())])?; }
            let mut inserted=0;
            let mut new_source=false;
            for r in &request.evidence {
                // Any view establishes a known session. Only a different validated
                // human revision can add a new source within that same session.
                let known:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM suggestion_evidence WHERE proposal_id=?1 AND session_id=?2 AND (?3 IS NULL OR revision_id=?3))",params![id,r.session_id,r.revision_id],|r|r.get(0))?;
                new_source |= !known;
                inserted += tx.execute("INSERT OR IGNORE INTO suggestion_evidence VALUES(?1,?2,?3,?4,?5)",params![id,r.session_id,r.revision_id,r.original_index,serde_json::to_string(r)?])?;
            }
            if inserted > 0 { tx.execute("UPDATE suggestions SET revision=revision+1,state=CASE WHEN state='rejected' AND ?2 THEN 'proposed' ELSE state END WHERE id=?1",params![id,new_source])?; }
            if let Some((_,attempt))=scope {
                let revision:i64=tx.query_row("SELECT revision FROM suggestions WHERE id=?1",[id],|r|r.get(0))?;
                let relation=if created {"created"} else if inserted>0 {"updated"} else {"existing"};
                tx.execute("INSERT INTO analysis_attempt_proposals(attempt_id,proposal_id,relation,revision) VALUES(?1,?2,?3,?4) ON CONFLICT(attempt_id,proposal_id) DO UPDATE SET revision=excluded.revision,relation=CASE WHEN analysis_attempt_proposals.relation='created' THEN 'created' WHEN analysis_attempt_proposals.relation='updated' OR excluded.relation='updated' THEN 'updated' ELSE 'existing' END",params![attempt,id,relation,revision])?;
                tx.execute("UPDATE analysis_attempts SET accepted=accepted+1 WHERE id=?1",[attempt])?;
            }
            if let (Some(finding),Some((batch,attempt)))=(finding,scope) {
                let (outcome,publication)=auto_publish(&tx,&dictionary,id,batch,attempt,finding,cancel)?;
                finding.save(&tx,attempt,Some(id),outcome,publication)?;
            }
            ensure!(!cancel.cancelled(),"Analisi annullata.");
            tx.commit()?; Ok(())
        })
    }
    pub fn snapshot(&self) -> Result<ReviewSnapshot> {
        self.access(|connection| {
            let tx = connection.transaction()?;
            let dictionary = crate::dictionary::read_schema_snapshot(&tx, "vocabulary")?;
            let result = read_snapshot(&tx, &dictionary)?;
            tx.commit()?;
            Ok(result)
        })
        .map(with_conflicts)
    }
    pub fn review(&self, request: ReviewRequest) -> Result<ReviewSnapshot> {
        text(&request.request_key, 128)?;
        if matches!(request.action, ReviewAction::Restore { .. }) {
            ensure!(
                request.id.is_none() && request.expected_revision.is_none(),
                "Il ripristino non riguarda una singola proposta."
            );
        } else {
            ensure!(
                request.id.is_some_and(|id| id > 0)
                    && request.expected_revision.is_some_and(|v| v > 0),
                "Proposta o revisione non valida."
            );
        }
        // Processor validation happens outside write locks. The publication transaction
        // rechecks both versions and the proposal revision before any mutation.
        let mut replayed = None;
        let prepared = self.access(|connection| {
            let tx = connection.transaction()?;
            let duplicate: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM suggestion_actions WHERE request_key=?1)",
                [&request.request_key],
                |r| r.get(0),
            )?;
            if duplicate {
                return Ok(None);
            }
            let version = current(&tx)?;
            let dictionary = crate::dictionary::read_schema_snapshot(&tx, "vocabulary")?;
            ensure!(
                version == request.expected_version
                    && dictionary.version == request.dictionary_version,
                "Le regole sono cambiate. Aggiorna prima di confermare."
            );
            let mut active = rules_at(&tx, version)?;
            match &request.action {
                ReviewAction::Approve => {
                    let id = request.id.context("Proposta mancante")?;
                    let (json, revision): (String, i64) = tx.query_row(
                        "SELECT candidate_json,revision FROM suggestions WHERE id=?1",
                        [id],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )?;
                    ensure!(
                        request.expected_revision == Some(revision),
                        "Proposta aggiornata. Rileggi prima di confermare."
                    );
                    let rule = ApprovedRule {
                        id,
                        candidate: serde_json::from_str(&json)?,
                    };
                    // Every publication is replayed first. A manual approval may still
                    // publish a rule that changes nothing; the count is kept with it.
                    replayed = replay::changed_by(&tx, &dictionary, &active, &rule).ok();
                    active.push(rule);
                }
                ReviewAction::Disable => active.retain(|r| Some(r.id) != request.id),
                ReviewAction::Restore { version } => active = rules_at(&tx, *version)?,
                _ => return Ok(None),
            }
            tx.commit()?;
            Ok(Some((dictionary, active)))
        })?;
        if let Some((dictionary, active)) = &prepared {
            validate_combination(dictionary, active)?;
        }
        self.access(|connection| {
            let tx=connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let dictionary=crate::dictionary::read_schema_snapshot(&tx,"vocabulary")?;
            let action_identity=serde_json::to_string(&(request.id,request.expected_revision,request.expected_version,request.dictionary_version,&request.action))?;
            let action_identity = format!("{:x}", Sha256::digest(action_identity.as_bytes()));
            let prior:Option<String>=tx.query_row("SELECT action FROM suggestion_actions WHERE request_key=?1",[&request.request_key],|r|r.get(0)).optional()?;
            if let Some(prior)=prior { ensure!(prior==action_identity,"Chiave richiesta già usata per un'altra azione.");return Ok(()); }
            let version=current(&tx)?;
            ensure!(request.expected_version==version && request.dictionary_version==dictionary.version,"Le regole sono cambiate. Aggiorna prima di confermare.");
            let mut active=rules_at(&tx,version)?;
            if let Some(id)=request.id {
                let identity:String=tx.query_row("SELECT identity FROM suggestions WHERE id=?1",[id],|r|r.get(0))?;
                remember_veto(&tx,&identity)?;
            }
            if matches!(request.action,ReviewAction::Restore{..}) {
                for rule in &active { remember_veto(&tx,&canonical(&rule.candidate)?)?; }
            }
            let mut publication=None;
            if let ReviewAction::Restore {version:target}=request.action {
                ensure!(target>=0 && target<version,"Scegli una versione precedente.");
                active=rules_at(&tx,target)?; publication=Some(format!("restore:{target}"));
            } else {
                let id=request.id.context("Proposta mancante.")?;
                let (json,revision,state,approved):(String,i64,String,Option<i64>)=tx.query_row("SELECT candidate_json,revision,state,approved_at FROM suggestions WHERE id=?1",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
                ensure!(request.expected_revision==Some(revision),"Proposta aggiornata. Rileggi prima di confermare.");
                let candidate:Candidate=serde_json::from_str(&json)?;
                match &request.action {
                    ReviewAction::Approve => {
                        ensure!(!active.iter().any(|r|r.id==id),"Regola già attiva.");
                        ensure!(state!="rejected","Modifica la proposta rifiutata prima di approvarla.");
                        validate(&candidate,&dictionary)?;
                        if approved.is_none() { let refs=read_evidence(&tx,id)?;ensure!(!refs.is_empty(),"Nessuna evidenza disponibile.");for e in &refs {validate_evidence(&candidate,e)?;} }
                        active.push(ApprovedRule{id,candidate}); publication=Some(format!("approve:{id}"));
                        tx.execute("UPDATE suggestions SET state='approved', approved_at=COALESCE(approved_at,?1),rejection_reason=NULL,revision=revision+1 WHERE id=?2",params![now()?,id])?;
                    },
                    ReviewAction::Disable => { let before=active.len();active.retain(|r|r.id!=id);ensure!(active.len()!=before,"Regola non attiva.");publication=Some(format!("disable:{id}"));tx.execute("UPDATE suggestions SET revision=revision+1 WHERE id=?1",[id])?; },
                    ReviewAction::Reject{reason} => {ensure!(approved.is_none(),"Disattiva una regola già approvata.");text(reason,2000)?;tx.execute("UPDATE suggestions SET state='rejected',rejection_reason=?1,revision=revision+1 WHERE id=?2",params![reason,id])?;},
                    ReviewAction::Defer => {ensure!(approved.is_none(),"Regola già approvata.");tx.execute("UPDATE suggestions SET state='deferred',revision=revision+1 WHERE id=?1",[id])?;},
                    ReviewAction::Edit{candidate} => {
                        ensure!(approved.is_none(),"Le definizioni approvate sono immutabili. Crea una nuova proposta.");
                        let candidate=normalize(*candidate.clone()); validate(&candidate,&dictionary)?;
                        for e in read_evidence(&tx,id)? {validate_evidence(&candidate,&e)?;}
                        tx.execute("UPDATE suggestions SET candidate_json=?1,identity=?2,state='proposed',rejection_reason=NULL,revision=revision+1 WHERE id=?3",params![serde_json::to_string(&candidate)?,canonical(&candidate)?,id]).context("Esiste già una proposta uguale. Unisci le evidenze tramite l'analisi.")?;
                    },
                    ReviewAction::Restore{..}=>unreachable!(),
                }
            }
            if let Some(action)=publication {
                ensure!(prepared.is_some(),"Validazione della pubblicazione mancante.");
                tx.execute("INSERT INTO suggestion_publications(previous_version,action,created_at,rules_json,dictionary_version,builtin_version) VALUES(?1,?2,?3,?4,?5,?6)",params![version,action,now()?,serde_json::to_string(&active)?,dictionary.version,voiceflow_core::cleanup::BUILTIN_VERSION])?;
                let next=tx.last_insert_rowid();tx.execute("UPDATE suggestion_state SET version=?1 WHERE id=1",[next])?;
                if let (Some(id),Some(counts),ReviewAction::Approve)=(request.id,replayed,&request.action) { replay::record(&tx,next,id,counts)?; }
                // Invalidate stale cards after rollback as well as direct mutations.
                tx.execute("UPDATE suggestions SET revision=revision+1 WHERE approved_at IS NOT NULL",[])?;
            }
            if let Some(id)=request.id {
                let identity:String=tx.query_row("SELECT identity FROM suggestions WHERE id=?1",[id],|r|r.get(0))?;
                remember_veto(&tx,&identity)?;
            }
            // Audit stores metadata only: rejected/source text must not survive source deletion.
            tx.execute("INSERT INTO suggestion_actions VALUES(?1,?2,?3,?4,?5)",params![request.request_key,request.id,action_identity,current(&tx)?,now()?])?;
            tx.commit()?;Ok(())
        })?;
        self.snapshot()
    }
    /// Reconstruct a historical combination without modifying current activation.
    /// Revisions from before the C2 upgrade can be unavailable; never reconstruct them from current terms.
    pub fn archived_session_snapshot(
        &self,
        dictionary_revision: i64,
        publication: i64,
        language: &str,
    ) -> Result<SessionRules> {
        self.access(|connection| {
            let tx = connection.transaction()?;
            let json: String = tx
                .query_row(
                    "SELECT definition_json FROM vocabulary.dictionary_versions WHERE revision=?1",
                    [dictionary_revision],
                    |r| r.get(0),
                )
                .context("Definizione storica del dizionario non disponibile")?;
            let dictionary: DictionarySnapshot = serde_json::from_str(&json)?;
            let builtin_version: String = if publication == 0 {
                voiceflow_core::cleanup::BUILTIN_VERSION.into()
            } else {
                tx.query_row(
                    "SELECT builtin_version FROM suggestion_publications WHERE version=?1",
                    [publication],
                    |r| r.get(0),
                )?
            };
            let baseline: String = tx.query_row(
                "SELECT definition_json FROM suggestion_catalogs WHERE version=?1",
                [builtin_version],
                |r| r.get(0),
            )?;
            let rules = rules_at(&tx, publication)?;
            let (compiled, cleanup) = compile_with_baseline(
                &dictionary,
                &rules,
                publication,
                language,
                serde_json::from_str(&baseline)?,
            )?;
            let groups = dictionary
                .groups
                .iter()
                .filter(|g| dictionary.selected_group_ids.contains(&g.id))
                .map(|g| format!("{}:{}", g.id, g.name))
                .collect();
            tx.commit()?;
            Ok(SessionRules {
                dictionary: compiled,
                cleanup,
                groups,
                dictionary_version: format!(
                    "dictionary-v2:{dictionary_revision};suggestions-v1:{publication}"
                ),
            })
        })
    }
    pub fn session_snapshot(&self, language: &str) -> Result<SessionRules> {
        self.access(|connection| {
            let tx = connection.transaction()?;
            let dictionary = crate::dictionary::read_schema_snapshot(&tx, "vocabulary")?;
            let version = current(&tx)?;
            let active = rules_at(&tx, version)?;
            let (compiled, cleanup) = compile(&dictionary, &active, version, language)?;
            let groups = dictionary
                .groups
                .iter()
                .filter(|g| dictionary.selected_group_ids.contains(&g.id))
                .map(|g| format!("{}:{}", g.id, g.name))
                .collect();
            let result = SessionRules {
                dictionary: compiled,
                cleanup,
                groups,
                dictionary_version: format!(
                    "dictionary-v2:{};suggestions-v1:{version}",
                    dictionary.version
                ),
            };
            tx.commit()?;
            Ok(result)
        })
    }
}
fn identity_hash(identity: &str) -> String {
    format!("{:x}", Sha256::digest(identity.as_bytes()))
}
pub(crate) fn remember_veto(c: &Connection, identity: &str) -> Result<()> {
    c.execute(
        "INSERT OR IGNORE INTO suggestion_human_veto VALUES(?1)",
        [identity_hash(identity)],
    )?;
    Ok(())
}
fn auto_publish(
    c: &Connection,
    dictionary: &DictionarySnapshot,
    id: i64,
    batch: i64,
    attempt: i64,
    finding: &crate::analysis::findings::Finding,
    cancel: &dyn crate::analysis::Cancellation,
) -> Result<(&'static str, Option<i64>)> {
    let config = crate::analysis::settings(c)?.0;
    if !c.query_row(
        "SELECT eligible FROM suggestion_auto_origin WHERE proposal_id=?1",
        [id],
        |r| r.get::<_, bool>(0),
    )? {
        return Ok(("blocked-legacy", None));
    }
    if finding.confidence.is_none() {
        return Ok(("manual-unknown", None));
    }
    if finding.confidence.is_some_and(|v| v <= 90) {
        return Ok(("manual-confidence", None));
    }
    if !config.auto_approve_high_confidence {
        return Ok(("manual-policy", None));
    }
    let (json, identity, state, approved): (String, String, String, Option<i64>) = c.query_row(
        "SELECT candidate_json,identity,state,approved_at FROM suggestions WHERE id=?1",
        [id],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
    )?;
    let veto: bool = c.query_row(
        "SELECT EXISTS(SELECT 1 FROM suggestion_human_veto WHERE identity=?1)",
        [identity_hash(&identity)],
        |r| r.get(0),
    )?;
    if veto || state == "rejected" || state == "deferred" {
        return Ok(("blocked-human", None));
    }
    let version = current(c)?;
    let mut active = rules_at(c, version)?;
    if active.iter().any(|r| r.id == id) {
        return Ok(("already-active", Some(version)));
    }
    if approved.is_some() {
        return Ok(("blocked-human", None));
    }
    let candidate: Candidate = serde_json::from_str(&json)?;
    // Old unscored hypotheses cannot acquire a score by being re-presented by the provider.
    if candidate != normalize(finding.candidate.clone().context("Regola mancante")?) {
        return Ok(("blocked-changed", None));
    }
    for evidence in read_evidence(c, id)? {
        if c.query_row(
            "SELECT EXISTS(SELECT 1 FROM analysis_exclusions WHERE session_id=?1)",
            [evidence.reference.session_id],
            |r| r.get::<_, bool>(0),
        )? || validate_evidence(&candidate, &evidence).is_err()
        {
            return Ok(("blocked-evidence", None));
        }
    }
    let rule = ApprovedRule { id, candidate };
    let published = active.clone();
    active.push(rule.clone());
    if validate_combination(dictionary, &active).is_err() {
        return Ok(("blocked-conflict", None));
    }
    // A conflict is reported as such above. A valid rule that would not have changed a
    // single saved dictation is left to a person.
    let Ok(replayed) = replay::changed_by(c, dictionary, &published, &rule) else {
        return Ok(("blocked-conflict", None));
    };
    if replayed.0 == 0 {
        return Ok(("manual-no-effect", None));
    }
    // Recheck within the publication transaction, after expensive compiler validation.
    crate::analysis::validate_scope(c, batch, &finding.evidence)?;
    ensure!(
        crate::analysis::settings(c)?.0.auto_approve_high_confidence && !cancel.cancelled(),
        "Analisi annullata."
    );
    ensure!(c.query_row("SELECT EXISTS(SELECT 1 FROM analysis_attempts WHERE id=?1 AND batch_id=?2 AND state='running')",params![attempt,batch],|r|r.get::<_,bool>(0))?,"Tentativo non attivo.");
    c.execute("INSERT INTO suggestion_publications(previous_version,action,created_at,rules_json,dictionary_version,builtin_version) VALUES(?1,?2,?3,?4,?5,?6)",params![version,format!("auto-approve:{id}"),now()?,serde_json::to_string(&active)?,dictionary.version,voiceflow_core::cleanup::BUILTIN_VERSION])?;
    let next = c.last_insert_rowid();
    c.execute("UPDATE suggestion_state SET version=?1 WHERE id=1", [next])?;
    replay::record(c, next, id, replayed)?;
    c.execute(
        "UPDATE suggestions SET state='approved',approved_at=?1,rejection_reason=NULL WHERE id=?2",
        params![now()?, id],
    )?;
    c.execute(
        "UPDATE suggestions SET revision=revision+1 WHERE approved_at IS NOT NULL",
        [],
    )?;
    c.execute(
        "INSERT INTO suggestion_actions VALUES(?1,?2,?3,?4,?5)",
        params![
            format!("analysis-auto:{attempt}:{}", finding.key()?),
            id,
            "auto-approve",
            next,
            now()?
        ],
    )?;
    Ok(("auto-approved", Some(next)))
}
fn with_conflicts(mut snapshot: ReviewSnapshot) -> ReviewSnapshot {
    let active: Vec<_> = snapshot
        .proposals
        .iter()
        .filter(|p| p.state == "approved")
        .map(|p| ApprovedRule {
            id: p.id,
            candidate: p.candidate.clone(),
        })
        .collect();
    for proposal in &mut snapshot.proposals {
        let mut proposed = active.clone();
        if !proposed.iter().any(|r| r.id == proposal.id) {
            proposed.push(ApprovedRule {
                id: proposal.id,
                candidate: proposal.candidate.clone(),
            });
        }
        proposal.conflicts = validate(&proposal.candidate, &snapshot.validation_dictionary)
            .and_then(|_| validate_combination(&snapshot.validation_dictionary, &proposed))
            .err()
            .map(|e| vec![e.to_string()])
            .unwrap_or_default();
    }
    snapshot
}
fn read_snapshot(
    connection: &Connection,
    dictionary: &DictionarySnapshot,
) -> Result<ReviewSnapshot> {
    let version = current(connection)?;
    let active = rules_at(connection, version)?;
    let rows=connection.prepare("SELECT id,revision,candidate_json,state,rejection_reason,approved_at FROM suggestions ORDER BY id DESC")?.query_map([],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,i64>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,Option<String>>(4)?,r.get::<_,Option<i64>>(5)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
    let mut proposals = vec![];
    for (id, revision, json, state, rejection_reason, approved_at) in rows {
        let candidate: Candidate = serde_json::from_str(&json)?;
        let state = if approved_at.is_some() {
            if active.iter().any(|r| r.id == id) {
                "approved"
            } else {
                "disabled"
            }
            .into()
        } else {
            state
        };
        let conflicts = vec![];
        let scored: Option<(Option<u8>, String)> = connection.query_row(
            "SELECT confidence,outcome FROM analysis_findings WHERE proposal_id=?1 AND candidate_hash=?2 ORDER BY id DESC LIMIT 1",
            params![id,candidate_hash(&candidate)?], |r| Ok((r.get(0)?,r.get(1)?))
        ).optional()?;
        if state == "proposed" && scored.is_some() && handled_by_local_cleanup(&candidate)? {
            let veto: bool = connection.query_row(
                "SELECT EXISTS(SELECT 1 FROM suggestion_human_veto WHERE identity=?1)",
                [identity_hash(&canonical(&candidate)?)],
                |r| r.get(0),
            )?;
            if !veto {
                continue;
            }
        }
        proposals.push(Proposal {
            id,
            revision,
            candidate,
            state,
            rejection_reason,
            evidence: read_evidence(connection, id)?,
            conflicts,
            approved_at,
            confidence: scored.as_ref().and_then(|(confidence, _)| *confidence),
            analysis_outcome: scored.map(|(_, outcome)| outcome),
        });
    }
    let publications=connection.prepare("SELECT version,previous_version,action,created_at,rules_json FROM suggestion_publications ORDER BY version DESC")?.query_map([],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get::<_,String>(4)?)))?.collect::<rusqlite::Result<Vec<(i64,Option<i64>,String,i64,String)>>>()?.into_iter().map(|(version,previous_version,action,created_at,json)|Ok(Publication{version,previous_version,action,created_at,proposal_ids:serde_json::from_str::<Vec<ApprovedRule>>(&json)?.into_iter().map(|r|r.id).collect()})).collect::<Result<_>>()?;
    Ok(ReviewSnapshot {
        validation_dictionary: dictionary.clone(),
        proposals,
        publications,
        version,
        dictionary_version: dictionary.version,
        groups: dictionary.groups.clone(),
    })
}

fn handled_by_local_cleanup(candidate: &Candidate) -> Result<bool> {
    if !matches!(
        &candidate.definition,
        Definition::Cleanup {
            operation: Operation::Repeat { .. } | Operation::RepeatPhrase { .. },
            ..
        }
    ) {
        return Ok(false);
    }
    for example in candidate
        .positive_examples
        .iter()
        .chain(&candidate.negative_examples)
    {
        let mut processor = TextProcessor::new(FormatMode::Faithful, true);
        processor.update(&example.input, true)?;
        if processor.parts().0 != example.output {
            return Ok(false);
        }
    }
    Ok(!candidate.positive_examples.is_empty())
}
#[tauri::command]
pub async fn suggestions_snapshot(
    store: tauri::State<'_, SuggestionStore>,
) -> Result<ReviewSnapshot, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.snapshot())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}
#[tauri::command]
pub async fn suggestions_review(
    store: tauri::State<'_, SuggestionStore>,
    request: ReviewRequest,
) -> Result<ReviewSnapshot, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.review(request))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::history::CompletedDictation;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture {
        path: std::path::PathBuf,
        store: SuggestionStore,
    }
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "voiceflow-c2-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            let store = SuggestionStore::open(path.join("history.db"), path.join("dictionary.db"));
            Self { path, store }
        }
        fn record(&self, key: &str, input: &str) -> i64 {
            self.store
                .history
                .record(&CompletedDictation {
                    session_key: key,
                    created_at: 10,
                    text: input,
                    duration_seconds: 1.,
                    language: "it",
                    original_asr: Some(&[input.into()]),
                    processing: None,
                })
                .unwrap();
            self.store
                .history
                .access(|c| {
                    Ok(c.query_row(
                        "SELECT id FROM transcriptions WHERE session_key=?1",
                        [key],
                        |r| r.get(0),
                    )?)
                })
                .unwrap()
        }
        fn ingest(&self, id: i64, candidate: Candidate) -> ReviewSnapshot {
            self.store
                .ingest(IngestRequest {
                    candidate,
                    evidence: vec![EvidenceRef {
                        session_id: id,
                        revision_id: None,
                        original_index: Some(0),
                    }],
                })
                .unwrap()
        }
        fn remember(&self, session_id: i64, key: &str) -> i64 {
            self.store.history.access(|c| {
                c.execute("INSERT INTO history_revisions(transcription_id,request_key,created_at,before_text,text) VALUES(?1,?2,1,'voice flow','VoiceFlow')", params![session_id,key])?;
                let revision = c.last_insert_rowid();
                c.execute("INSERT INTO confirmed_corrections VALUES(?1,'recognition',1)", [revision])?;
                Ok(revision)
            }).unwrap()
        }
        fn act(
            &self,
            s: &ReviewSnapshot,
            id: Option<i64>,
            action: ReviewAction,
            key: &str,
        ) -> Result<ReviewSnapshot> {
            self.store.review(ReviewRequest {
                id,
                expected_revision: id
                    .and_then(|id| s.proposals.iter().find(|p| p.id == id).map(|p| p.revision)),
                expected_version: s.version,
                dictionary_version: s.dictionary_version,
                request_key: key.into(),
                action,
            })
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
    fn terminology() -> Candidate {
        Candidate {
            definition: Definition::Terminology {
                preferred: "VoiceFlow".into(),
                variants: vec!["voice flow".into()],
                matching: MatchMode::Exact,
                context: RuleContext::default(),
            },
            language: "it".into(),
            group_ids: vec![],
            reason: "Grafia del progetto".into(),
            positive_examples: vec![Example {
                input: "voice flow".into(),
                output: "VoiceFlow".into(),
            }],
            negative_examples: vec![Example {
                input: "voice flower".into(),
                output: "voice flower".into(),
            }],
        }
    }
    fn cleanup() -> Candidate {
        Candidate {
            definition: Definition::Cleanup {
                operation: Operation::RemoveFalseStart {
                    source: vec!["volevo".into(), "vorrei".into()],
                },
                context: Default::default(),
            },
            language: "it".into(),
            group_ids: vec![],
            reason: "Falsa partenza circoscritta".into(),
            positive_examples: vec![Example {
                input: "volevo vorrei chiedere".into(),
                output: "vorrei chiedere".into(),
            }],
            negative_examples: vec![Example {
                input: "volevo chiedere".into(),
                output: "volevo chiedere".into(),
            }],
        }
    }
    fn rendered(r: SessionRules, input: &str) -> String {
        let mut p =
            TextProcessor::with_rules(FormatMode::SpokenCommands, true, r.dictionary, r.cleanup);
        p.update(input, true).unwrap();
        let (a, b) = p.parts();
        format!("{a}{b}")
    }
    #[test]
    fn lifecycle_atomic_publication_archive_restore_and_session_snapshot() {
        let f = Fixture::new();
        let id = f.record("one", "voice flow");
        let s = f.ingest(id, terminology());
        let proposal = s.proposals[0].id;
        let before = f.store.session_snapshot("it").unwrap();
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow"),
            "voice flow"
        );
        let s = f
            .act(&s, Some(proposal), ReviewAction::Approve, "approve")
            .unwrap();
        assert_eq!(s.version, 1);
        assert_eq!(s.proposals[0].state, "approved");
        assert_eq!(rendered(before, "voice flow"), "voice flow");
        let active = f.store.session_snapshot("it-IT").unwrap();
        assert_eq!(
            active.dictionary_version,
            "dictionary-v2:1;suggestions-v1:1"
        );
        assert_eq!(rendered(active, "voice flow"), "VoiceFlow");
        let s = f
            .act(&s, Some(proposal), ReviewAction::Disable, "disable")
            .unwrap();
        assert_eq!(s.version, 2);
        assert_eq!(s.proposals[0].state, "disabled");
        let s = f
            .act(&s, None, ReviewAction::Restore { version: 1 }, "restore")
            .unwrap();
        assert_eq!(s.version, 3);
        assert_eq!(s.proposals[0].state, "approved");
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow"),
            "VoiceFlow"
        );
        let reopened =
            SuggestionStore::open(f.path.join("history.db"), f.path.join("dictionary.db"));
        assert_eq!(reopened.snapshot().unwrap().version, 3);
        assert_eq!(
            reopened
                .history
                .access(|c| Ok(c.query_row(
                    "SELECT text FROM transcriptions WHERE id=?1",
                    [id],
                    |r| r.get::<_, String>(0)
                )?))
                .unwrap(),
            "voice flow"
        );
        assert_eq!(
            reopened
                .dictionary
                .access(|c| Ok(c.query_row(
                    "SELECT COUNT(*) FROM dictionary_versions",
                    [],
                    |r| r.get::<_, i64>(0)
                )?))
                .unwrap(),
            1
        );
    }
    #[test]
    fn dedup_rejected_stays_rejected_unless_new_identifiable_evidence() {
        let f = Fixture::new();
        let first = f.record("one", "voice flow");
        let s = f.ingest(first, terminology());
        let id = s.proposals[0].id;
        let s = f
            .act(
                &s,
                Some(id),
                ReviewAction::Reject {
                    reason: "Non desiderata".into(),
                },
                "reject",
            )
            .unwrap();
        let revision = s.proposals[0].revision;
        let s = f.ingest(first, terminology());
        assert_eq!(s.proposals.len(), 1);
        assert_eq!(s.proposals[0].state, "rejected");
        assert_eq!(s.proposals[0].revision, revision);
        assert_eq!(s.proposals[0].evidence.len(), 1);
        let second = f.record("two", "uso voice flow");
        let s = f.ingest(second, terminology());
        assert_eq!(s.proposals.len(), 1);
        assert_eq!(s.proposals[0].state, "proposed");
        assert_eq!(s.proposals[0].evidence.len(), 2);
        assert_eq!(
            s.proposals[0].rejection_reason.as_deref(),
            Some("Non desiderata")
        );
    }
    #[test]
    fn rejected_human_evidence_stays_rejected_for_both_nonhuman_views_and_retries() {
        for first_view in [None, Some(0)] {
            let f = Fixture::new();
            let source = f.record("source", "voice flow");
            let revision = f.remember(source, "human");
            let s = f
                .store
                .ingest(IngestRequest {
                    candidate: terminology(),
                    evidence: vec![EvidenceRef {
                        session_id: source,
                        revision_id: Some(revision),
                        original_index: None,
                    }],
                })
                .unwrap();
            let s = f
                .act(
                    &s,
                    Some(s.proposals[0].id),
                    ReviewAction::Reject {
                        reason: "No".into(),
                    },
                    "reject",
                )
                .unwrap();
            let mut expected_revision = s.proposals[0].revision;
            for (index, view) in [
                first_view,
                if first_view.is_none() { Some(0) } else { None },
            ]
            .into_iter()
            .enumerate()
            {
                let request = IngestRequest {
                    candidate: terminology(),
                    evidence: vec![EvidenceRef {
                        session_id: source,
                        revision_id: None,
                        original_index: view,
                    }],
                };
                expected_revision += 1;
                for _ in 0..2 {
                    let s = f.store.ingest(request.clone()).unwrap();
                    assert_eq!(s.proposals[0].state, "rejected");
                    assert_eq!(s.proposals[0].revision, expected_revision);
                    assert_eq!(s.proposals[0].evidence.len(), index + 2);
                    assert_eq!(s.proposals[0].rejection_reason.as_deref(), Some("No"));
                    assert_eq!(s.version, 0);
                }
            }
        }
    }
    #[test]
    fn rejected_nonhuman_evidence_reopens_only_for_valid_new_revision_or_session() {
        for view in [None, Some(0)] {
            let f = Fixture::new();
            let source = f.record("source", "voice flow");
            let s = f
                .store
                .ingest(IngestRequest {
                    candidate: terminology(),
                    evidence: vec![EvidenceRef {
                        session_id: source,
                        revision_id: None,
                        original_index: view,
                    }],
                })
                .unwrap();
            let mut s = f
                .act(
                    &s,
                    Some(s.proposals[0].id),
                    ReviewAction::Reject {
                        reason: "No".into(),
                    },
                    "reject",
                )
                .unwrap();
            for key in ["first-human", "new-human"] {
                let revision = f.remember(source, key);
                let request = IngestRequest {
                    candidate: terminology(),
                    evidence: vec![EvidenceRef {
                        session_id: source,
                        revision_id: Some(revision),
                        original_index: None,
                    }],
                };
                f.store
                    .history
                    .access(|c| {
                        c.execute(
                            "UPDATE history_revisions SET reverted=1 WHERE id=?1",
                            [revision],
                        )?;
                        Ok(())
                    })
                    .unwrap();
                assert!(f.store.ingest(request.clone()).is_err());
                assert_eq!(
                    f.store.snapshot().unwrap().proposals[0].revision,
                    s.proposals[0].revision
                );
                assert_eq!(f.store.snapshot().unwrap().proposals[0].state, "rejected");
                f.store
                    .history
                    .access(|c| {
                        c.execute(
                            "UPDATE history_revisions SET reverted=0 WHERE id=?1",
                            [revision],
                        )?;
                        Ok(())
                    })
                    .unwrap();
                s = f.store.ingest(request.clone()).unwrap();
                assert_eq!(s.proposals[0].state, "proposed");
                let retry = f.store.ingest(request.clone()).unwrap();
                assert_eq!(retry.proposals[0].revision, s.proposals[0].revision);
                s = f
                    .act(
                        &s,
                        Some(s.proposals[0].id),
                        ReviewAction::Reject {
                            reason: "No".into(),
                        },
                        key,
                    )
                    .unwrap();
                let retry = f.store.ingest(request).unwrap();
                assert_eq!(retry.proposals[0].state, "rejected");
                assert_eq!(retry.proposals[0].revision, s.proposals[0].revision);
            }
            let second = f.record("new-session", "voice flow");
            let request = IngestRequest {
                candidate: terminology(),
                evidence: vec![EvidenceRef {
                    session_id: second,
                    revision_id: None,
                    original_index: view,
                }],
            };
            s = f.store.ingest(request.clone()).unwrap();
            assert_eq!(s.proposals[0].state, "proposed");
            let retry = f.store.ingest(request).unwrap();
            assert_eq!(retry.proposals[0].revision, s.proposals[0].revision);
            assert_eq!(s.version, 0);
        }
    }
    #[test]
    fn evidence_boundaries_preserve_supported_unicode_tokens_and_terminal_punctuation() {
        for source in [
            "voice flow\u{301}",
            "voice flow\u{1ab0}\u{20dd}",
            "\u{301}voice flow",
            "x-voice flow",
            "voice flow/code",
            "voice flow.else",
            "voice flow,\u{301}",
            "voice flow'",
            "voice flow’",
            "voice flow_",
        ] {
            assert!(!contains_example(source, "voice flow"), "{source:?}");
        }
        for (source, example) in [
            ("uso voice flow, poi", "voice flow"),
            ("voice flow...!?\naltro", "voice flow"),
            ("\tvoice flow\u{a0}altro", "voice flow"),
            ("uso cafe\u{301}.", "cafe\u{301}"),
            ("uso café!", "café"),
            ("dell’API;", "dell’API"),
            ("日本語。", "日本語。"),
            ("voice flow\u{301} e voice flow.", "voice flow"),
        ] {
            assert!(
                contains_example(source, example),
                "{source:?} / {example:?}"
            );
        }
    }
    #[test]
    fn combining_marks_cannot_supply_input_or_human_output_evidence() {
        let f = Fixture::new();
        for (index, mark) in ["\u{301}", "\u{1ab0}\u{20dd}"].into_iter().enumerate() {
            let source = f.record(&format!("source-{index}"), &format!("voice flow{mark}"));
            for view in [None, Some(0)] {
                assert!(
                    f.store
                        .ingest(IngestRequest {
                            candidate: terminology(),
                            evidence: vec![EvidenceRef {
                                session_id: source,
                                revision_id: None,
                                original_index: view
                            }]
                        })
                        .is_err()
                );
            }
            let revision = f.remember(source, &format!("human-{index}"));
            f.store
                .history
                .access(|c| {
                    c.execute(
                        "UPDATE history_revisions SET text=?1 WHERE id=?2",
                        params![format!("VoiceFlow{mark}"), revision],
                    )?;
                    Ok(())
                })
                .unwrap();
            assert!(
                f.store
                    .ingest(IngestRequest {
                        candidate: terminology(),
                        evidence: vec![EvidenceRef {
                            session_id: source,
                            revision_id: Some(revision),
                            original_index: None
                        }]
                    })
                    .is_err()
            );
        }
        assert!(f.store.snapshot().unwrap().proposals.is_empty());
        let source = f.record("supported", "uso cafe\u{301}, poi");
        let mut candidate = terminology();
        if let Definition::Terminology {
            variants,
            preferred,
            ..
        } = &mut candidate.definition
        {
            *variants = vec!["cafe\u{301}".into()];
            *preferred = "Caffè".into();
        }
        candidate.positive_examples = vec![Example {
            input: "cafe\u{301}".into(),
            output: "Caffè".into(),
        }];
        assert_eq!(f.ingest(source, candidate).proposals[0].state, "proposed");
    }
    #[test]
    fn source_deletion_purges_unapproved_text_and_keeps_explicitly_approved_definition() {
        let f = Fixture::new();
        let source = f.record("source", "voice flow");
        let s = f.ingest(source, terminology());
        let s = f
            .act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Reject {
                    reason: "private marker".into(),
                },
                "reject",
            )
            .unwrap();
        assert_eq!(s.proposals.len(), 1);
        f.store.history.delete(source).unwrap();
        let s = f.store.snapshot().unwrap();
        assert!(s.proposals.is_empty());
        assert_eq!(
            f.store
                .history
                .access(|c| Ok(c.query_row(
                    "SELECT COUNT(*) FROM suggestion_actions",
                    [],
                    |r| r.get::<_, i64>(0)
                )?))
                .unwrap(),
            0
        );
        let source = f.record("second", "voice flow");
        let s = f.ingest(source, terminology());
        let s = f
            .act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve",
            )
            .unwrap();
        f.store.history.delete(source).unwrap();
        let after = f.store.snapshot().unwrap();
        assert_eq!(after.version, s.version);
        assert!(after.proposals[0].evidence.is_empty());
        assert_eq!(after.proposals[0].state, "approved");
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow"),
            "VoiceFlow"
        );
    }
    #[test]
    fn conflicts_with_manual_dictionary_block_publication_and_rollback_all_writes() {
        let f = Fixture::new();
        f.store.dictionary.save_term(serde_json::from_value(serde_json::json!({"id":null,"text":"Wrong","groupIds":[],"variants":["voice flow"],"language":"it","matching":"exact"})).unwrap()).unwrap();
        let source = f.record("source", "voice flow");
        let s = f.ingest(source, terminology());
        assert!(!s.proposals[0].conflicts.is_empty());
        assert!(
            f.act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve"
            )
            .is_err()
        );
        let after = f.store.snapshot().unwrap();
        assert_eq!(after.version, 0);
        assert_eq!(after.proposals[0].state, "proposed");
        assert!(after.proposals[0].approved_at.is_none());
        assert!(after.publications.is_empty());
    }
    #[test]
    fn combined_cleanup_dictionary_examples_fail_even_without_dictionary_conflict() {
        let f = Fixture::new();
        let source = f.record("source", "volevo vorrei chiedere");
        let s = f.ingest(source, cleanup());
        assert!(s.proposals[0].conflicts.is_empty());
        let _s = f
            .act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve",
            )
            .unwrap();
        assert_eq!(
            rendered(
                f.store.session_snapshot("it").unwrap(),
                "volevo vorrei chiedere"
            ),
            "vorrei chiedere"
        );
        let mut term = terminology();
        term.definition = Definition::Terminology {
            preferred: "desidero".into(),
            variants: vec!["vorrei".into()],
            matching: MatchMode::Exact,
            context: Default::default(),
        };
        term.positive_examples = vec![Example {
            input: "vorrei".into(),
            output: "desidero".into(),
        }];
        let other = f.record("other", "vorrei");
        let s = f.ingest(other, term);
        let p = s.proposals.iter().find(|p| p.state == "proposed").unwrap();
        assert!(!p.conflicts.is_empty());
        assert!(
            f.act(&s, Some(p.id), ReviewAction::Approve, "conflict")
                .is_err()
        );
        assert_eq!(f.store.snapshot().unwrap().version, 1);
        assert_eq!(s.publications.len(), 1);
    }
    #[test]
    fn idempotency_stale_revision_and_failure_injection() {
        let f = Fixture::new();
        let source = f.record("source", "voice flow");
        let s = f.ingest(source, terminology());
        let id = s.proposals[0].id;
        let approved = f.act(&s, Some(id), ReviewAction::Approve, "key").unwrap();
        let again = f.act(&s, Some(id), ReviewAction::Approve, "key").unwrap();
        assert_eq!(approved.version, again.version);
        assert_eq!(again.publications.len(), 1);
        assert!(f.act(&s, Some(id), ReviewAction::Defer, "key").is_err());
        assert!(f.act(&s, Some(id), ReviewAction::Disable, "stale").is_err());
        f.store.history.access(|c|{c.execute_batch("CREATE TRIGGER reject_publication BEFORE INSERT ON suggestion_publications BEGIN SELECT RAISE(ABORT,'fixture disk failure');END;")?;Ok(())}).unwrap();
        assert!(
            f.act(&again, Some(id), ReviewAction::Disable, "fail")
                .is_err()
        );
        let after = f.store.snapshot().unwrap();
        assert_eq!(after.version, 1);
        assert_eq!(after.proposals[0].state, "approved");
        assert_eq!(after.proposals[0].revision, again.proposals[0].revision);
    }
    #[test]
    fn forged_evidence_and_unconfirmed_or_reverted_revision_are_rejected() {
        let f = Fixture::new();
        let source = f.record("source", "voice flow");
        let reference = EvidenceRef {
            session_id: source,
            revision_id: None,
            original_index: Some(9),
        };
        assert!(
            f.store
                .ingest(IngestRequest {
                    candidate: terminology(),
                    evidence: vec![reference]
                })
                .is_err()
        );
        let revision=f.store.history.access(|c|{c.execute("INSERT INTO history_revisions(transcription_id,request_key,created_at,before_text,text) VALUES(?1,'fixture',1,'voice flow','VoiceFlow')",[source])?;Ok(c.last_insert_rowid())}).unwrap();
        let request = IngestRequest {
            candidate: terminology(),
            evidence: vec![EvidenceRef {
                session_id: source,
                revision_id: Some(revision),
                original_index: None,
            }],
        };
        assert!(f.store.ingest(request.clone()).is_err());
        f.store
            .history
            .access(|c| {
                c.execute(
                    "INSERT INTO confirmed_corrections VALUES(?1,'recognition',1)",
                    [revision],
                )?;
                Ok(())
            })
            .unwrap();
        let s = f.store.ingest(request.clone()).unwrap();
        assert!(s.proposals[0].evidence[0].human_confirmed);
        f.store
            .history
            .access(|c| {
                c.execute(
                    "UPDATE history_revisions SET reverted=1 WHERE id=?1",
                    [revision],
                )?;
                Ok(())
            })
            .unwrap();
        assert!(f.store.snapshot().unwrap().proposals.is_empty());
        assert!(
            f.act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve"
            )
            .is_err()
        );
        assert!(f.store.ingest(request).is_err());
    }
    #[test]
    fn concurrent_instances_merge_evidence_and_only_one_stale_approval_wins() {
        let f = Fixture::new();
        let a = f.record("a", "voice flow");
        let b = f.record("b", "voice flow");
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let handles: Vec<_> = [a, b]
            .into_iter()
            .map(|session_id| {
                let store =
                    SuggestionStore::open(f.path.join("history.db"), f.path.join("dictionary.db"));
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    store.ingest(IngestRequest {
                        candidate: terminology(),
                        evidence: vec![EvidenceRef {
                            session_id,
                            revision_id: None,
                            original_index: Some(0),
                        }],
                    })
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap().unwrap();
        }
        let s = f.store.snapshot().unwrap();
        assert_eq!(s.proposals.len(), 1);
        assert_eq!(s.proposals[0].evidence.len(), 2);
        let id = s.proposals[0].id;
        let revision = s.proposals[0].revision;
        let handles: Vec<_> = ["a", "b"]
            .into_iter()
            .map(|key| {
                let store =
                    SuggestionStore::open(f.path.join("history.db"), f.path.join("dictionary.db"));
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    store.review(ReviewRequest {
                        id: Some(id),
                        expected_revision: Some(revision),
                        expected_version: 0,
                        dictionary_version: 1,
                        request_key: key.into(),
                        action: ReviewAction::Approve,
                    })
                })
            })
            .collect();
        let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
        assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
        assert_eq!(f.store.snapshot().unwrap().publications.len(), 1);
    }
    #[test]
    fn archives_reconstruct_dictionary_after_manual_edits_and_missing_old_versions_error() {
        let f = Fixture::new();
        let source = f.record("a", "voice flow");
        let s = f.ingest(source, terminology());
        let _s = f
            .act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve",
            )
            .unwrap();
        f.store.dictionary.save_term(serde_json::from_value(serde_json::json!({"id":null,"text":"NeMo","groupIds":[],"variants":["ne mo"],"language":"it","matching":"exact"})).unwrap()).unwrap();
        assert_eq!(
            rendered(
                f.store.archived_session_snapshot(1, 1, "it").unwrap(),
                "voice flow ne mo"
            ),
            "VoiceFlow ne mo"
        );
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow ne mo"),
            "VoiceFlow NeMo"
        );
        assert!(f.store.archived_session_snapshot(999, 1, "it").is_err());
        let definition: String = f
            .store
            .history
            .access(|c| {
                Ok(c.query_row(
                    "SELECT definition_json FROM suggestion_catalogs WHERE version=?1",
                    [voiceflow_core::cleanup::BUILTIN_VERSION],
                    |r| r.get(0),
                )?)
            })
            .unwrap();
        assert_eq!(
            serde_json::from_str::<Catalog>(&definition).unwrap(),
            *CompiledCatalog::builtin().definition()
        );
    }
    #[test]
    fn same_session_produced_view_does_not_reopen_rejection_and_edits_never_activate() {
        let f = Fixture::new();
        let source = f.record("a", "voice flow");
        let s = f.ingest(source, terminology());
        let id = s.proposals[0].id;
        let _s = f
            .act(
                &s,
                Some(id),
                ReviewAction::Reject {
                    reason: "No".into(),
                },
                "reject",
            )
            .unwrap();
        let s = f
            .store
            .ingest(IngestRequest {
                candidate: terminology(),
                evidence: vec![EvidenceRef {
                    session_id: source,
                    revision_id: None,
                    original_index: None,
                }],
            })
            .unwrap();
        assert_eq!(s.proposals[0].state, "rejected");
        let mut changed = terminology();
        changed.reason = "Decisione umana aggiornata".into();
        let s = f
            .act(
                &s,
                Some(id),
                ReviewAction::Edit {
                    candidate: Box::new(changed),
                },
                "edit",
            )
            .unwrap();
        assert_eq!(s.version, 0);
        assert_eq!(s.proposals[0].state, "proposed");
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow"),
            "voice flow"
        );
    }
    #[test]
    fn suggestion_schema_failure_rolls_back_and_reopen_preserves_legacy_history() {
        let f = Fixture::new();
        let db = Connection::open(f.path.join("history.db")).unwrap();
        db.execute_batch("CREATE TABLE suggestion_publications(version TEXT); CREATE TABLE suggestion_evidence(broken TEXT);").unwrap();
        assert!(
            f.store
                .history
                .record(&CompletedDictation {
                    session_key: "legacy",
                    created_at: 1,
                    text: "prima",
                    duration_seconds: 1.,
                    language: "it",
                    original_asr: None,
                    processing: None
                })
                .is_err()
        );
        assert_eq!(
            db.pragma_query_value(None, "user_version", |r| r.get::<_, i64>(0))
                .unwrap(),
            0
        );
        db.execute_batch("DROP TABLE suggestion_publications;DROP TABLE suggestion_evidence;")
            .unwrap();
        drop(db);
        let source = f.record("source", "voice flow");
        assert_eq!(f.store.snapshot().unwrap().version, 0);
        f.ingest(source, terminology());
    }
    #[test]
    fn group_snapshot_and_streaming_pause_keep_approved_version_until_next_session() {
        use voiceflow_core::streaming::{StreamingDecoder, StreamingSession};
        struct Decoder {
            text: String,
        }
        impl StreamingDecoder for Decoder {
            fn chunk_samples(&self) -> usize {
                4
            }
            fn feed(&mut self, _: &[f32]) -> Result<()> {
                self.text = "voice flow".into();
                Ok(())
            }
            fn text(&self) -> String {
                self.text.clone()
            }
            fn reset(&mut self) {
                self.text.clear();
            }
        }
        let f = Fixture::new();
        f.store
            .dictionary
            .save_group(
                serde_json::from_value(serde_json::json!({"id":null,"name":"Progetto"})).unwrap(),
            )
            .unwrap();
        f.store.dictionary.select_groups(vec![1]).unwrap();
        let source = f.record("a", "voice flow");
        let mut candidate = terminology();
        candidate.group_ids = vec![1];
        let s = f.ingest(source, candidate);
        let _s = f
            .act(
                &s,
                Some(s.proposals[0].id),
                ReviewAction::Approve,
                "approve",
            )
            .unwrap();
        let rules = f.store.session_snapshot("it").unwrap();
        assert_eq!(rules.groups, ["1:Progetto"]);
        let mut session = StreamingSession::with_rules(
            Decoder {
                text: String::new(),
            },
            FormatMode::SpokenCommands,
            true,
            rules.dictionary,
            rules.cleanup,
        );
        session.push(&[0.1; 4]).unwrap();
        session.flush_utterance().unwrap();
        f.store.dictionary.select_groups(vec![]).unwrap();
        let s = f.store.snapshot().unwrap();
        f.act(
            &s,
            Some(s.proposals[0].id),
            ReviewAction::Disable,
            "disable",
        )
        .unwrap();
        session.push(&[0.1; 4]).unwrap();
        let final_text = session.finish().unwrap().text();
        assert_eq!(final_text, "VoiceFlow VoiceFlow");
        assert_eq!(session.cleanup_version(), "cleanup-review-v1:1");
        assert_eq!(
            session.original_utterances().unwrap(),
            ["voice flow", "voice flow"]
        );
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), "voice flow"),
            "voice flow"
        );
    }
    #[test]
    fn phrase_disfluency_ingest_edit_publish_disable_restore_and_session_snapshot() {
        let f = Fixture::new();
        let text = "le ultime le ultime chat";
        let id = f.record("phrase", text);
        let mut candidate = cleanup();
        candidate.definition = Definition::Cleanup {
            operation: Operation::RepeatPhrase {
                phrase: vec!["le".into(), "ultime".into()],
            },
            context: voiceflow_core::cleanup::Context {
                after: Some("chat".into()),
                ..Default::default()
            },
        };
        candidate.positive_examples = vec![Example {
            input: text.into(),
            output: "le ultime chat".into(),
        }];
        candidate.negative_examples = vec![Example {
            input: "le ultime le ultime pagine".into(),
            output: "le ultime le ultime pagine".into(),
        }];
        let s = f.ingest(id, candidate.clone());
        let proposal = s.proposals[0].id;
        assert_eq!(s.version, 0);
        assert_eq!(s.proposals[0].state, "proposed");
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), text),
            text
        );
        candidate.reason = "Ripetizione di frase rivista".into();
        let s = f
            .act(
                &s,
                Some(proposal),
                ReviewAction::Edit {
                    candidate: Box::new(candidate.clone()),
                },
                "edit-phrase",
            )
            .unwrap();
        assert_eq!(s.proposals[0].candidate, candidate);
        assert_eq!(s.version, 0);
        let before = f.store.session_snapshot("it").unwrap();
        let s = f
            .act(&s, Some(proposal), ReviewAction::Approve, "approve-phrase")
            .unwrap();
        assert_eq!(s.version, 1);
        assert_eq!(rendered(before, text), text);
        let active = f.store.session_snapshot("it").unwrap();
        let s = f
            .act(&s, Some(proposal), ReviewAction::Disable, "disable-phrase")
            .unwrap();
        assert_eq!(s.version, 2);
        assert_eq!(rendered(active, text), "le ultime chat");
        assert_eq!(
            rendered(f.store.session_snapshot("it").unwrap(), text),
            text
        );
        let s = f
            .act(
                &s,
                None,
                ReviewAction::Restore { version: 1 },
                "restore-phrase",
            )
            .unwrap();
        assert_eq!(s.version, 3);
        let reopened =
            SuggestionStore::open(f.path.join("history.db"), f.path.join("dictionary.db"));
        assert_eq!(
            reopened.snapshot().unwrap().proposals[0].candidate,
            candidate
        );
        assert_eq!(
            rendered(reopened.session_snapshot("it").unwrap(), text),
            "le ultime chat"
        );
        assert_eq!(
            reopened
                .history
                .access(|c| Ok(c.query_row(
                    "SELECT text FROM transcriptions WHERE id=?1",
                    [id],
                    |r| r.get::<_, String>(0)
                )?))
                .unwrap(),
            text
        );
    }

    #[test]
    fn replay_counts_changed_sessions_for_published_and_proposed_rules() {
        let f = Fixture::new();
        let first = f.record("one", "apri voice flow e poi di nuovo voice flow adesso");
        f.record("two", "volevo vorrei chiedere di voice flower");
        f.record("three", "uso voice flow");
        f.record("four", "niente da cambiare");
        // No saved original: never replayed, never counted.
        f.store
            .history
            .record(&CompletedDictation {
                session_key: "legacy",
                created_at: 10,
                text: "voice flow",
                duration_seconds: 1.,
                language: "it",
                original_asr: None,
                processing: None,
            })
            .unwrap();
        let s = f.ingest(first, terminology());
        let term = s.proposals[0].id;
        let s = f.ingest(first + 1, cleanup());
        let clean = s.proposals[0].id;
        let report = f.store.replay().unwrap();
        assert_eq!((report.sessions, report.version), (4, 0));
        let rule = |report: &replay::ReplayReport, id: i64| {
            let r = report.rules.iter().find(|r| r.proposal_id == id).unwrap();
            (r.active, r.changed_sessions, r.excerpts.len())
        };
        assert_eq!(rule(&report, term), (false, Some(2), 2));
        assert_eq!(rule(&report, clean), (false, Some(1), 1));
        let excerpt = &report
            .rules
            .iter()
            .find(|r| r.proposal_id == clean)
            .unwrap()
            .excerpts[0];
        assert_eq!(
            (
                excerpt.session_id,
                excerpt.before.as_str(),
                excerpt.after.as_str()
            ),
            (
                first + 1,
                "volevo vorrei chiedere di voice flower",
                "vorrei chiedere di voice flower"
            )
        );
        assert!(
            report
                .rules
                .iter()
                .all(|r| r.at_publication.is_none() && r.usage.is_none())
        );
        // Publishing replays first and keeps the count with the publication.
        let s = f
            .act(&s, Some(term), ReviewAction::Approve, "approve")
            .unwrap();
        let report = f.store.replay().unwrap();
        assert_eq!(rule(&report, term), (true, Some(2), 2));
        // The other rule is now measured on top of the published one: same effect.
        assert_eq!(rule(&report, clean), (false, Some(1), 1));
        let published = report.rules.iter().find(|r| r.proposal_id == term).unwrap();
        assert_eq!(
            published.at_publication,
            Some(replay::PublicationReplay {
                publication: s.version,
                changed_sessions: 2,
                sessions: 4
            })
        );
        let text = serde_json::to_string(&report).unwrap();
        assert!(text.contains("VoiceFlow e poi di nuovo VoiceFlow"));
        // Counts only reach the database.
        f.store
            .history
            .access(|c| {
                let row: (i64, i64, i64, i64) = c.query_row(
                    "SELECT publication,proposal_id,changed_sessions,sessions FROM suggestion_replays",
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
                )?;
                assert_eq!(row, (s.version, term, 2, 4));
                Ok(())
            })
            .unwrap();
        // A rejected hypothesis is not replayed.
        let s = f.store.snapshot().unwrap();
        f.act(
            &s,
            Some(clean),
            ReviewAction::Reject {
                reason: "no".into(),
            },
            "reject",
        )
        .unwrap();
        assert!(
            f.store
                .replay()
                .unwrap()
                .rules
                .iter()
                .all(|r| r.proposal_id != clean)
        );
    }
    #[test]
    fn rule_activations_are_counted_only_in_sessions_saved_after_approval() {
        let f = Fixture::new();
        let id = f.record("one", "voice flow");
        let s = f.ingest(id, terminology());
        let term = s.proposals[0].id;
        f.act(&s, Some(term), ReviewAction::Approve, "approve")
            .unwrap();
        let approved_at = f.store.snapshot().unwrap().proposals[0]
            .approved_at
            .unwrap();
        let metadata =
            crate::service::processing_metadata(&crate::settings::Settings::default()).unwrap();
        let save = |key: &str, created_at: i64, hits: Option<&[(&str, u32)]>| {
            let mut metadata = metadata.clone();
            metadata.rule_hits =
                hits.map(|hits| hits.iter().map(|(k, n)| ((*k).to_owned(), *n)).collect());
            f.store
                .history
                .record(&CompletedDictation {
                    session_key: key,
                    created_at,
                    text: "testo",
                    duration_seconds: 1.,
                    language: "it",
                    original_asr: Some(&["testo".into()]),
                    processing: Some(&metadata),
                })
                .unwrap();
        };
        let key = format!("dictionary:{}", -term);
        // Before the approval, or without the counter: not part of the denominator.
        save("early", approved_at - 1, Some(&[(key.as_str(), 9)]));
        save("untracked", approved_at + 1, None);
        save(
            "fired",
            approved_at + 2,
            Some(&[(key.as_str(), 3), ("cleanup:builtin", 1)]),
        );
        save("quiet", approved_at + 3, Some(&[]));
        let report = f.store.replay().unwrap();
        assert_eq!(
            report.rules[0].usage,
            Some(replay::RuleUsage {
                activations: 3,
                sessions_with_activation: 1,
                tracked_sessions: 2
            })
        );
    }
}
