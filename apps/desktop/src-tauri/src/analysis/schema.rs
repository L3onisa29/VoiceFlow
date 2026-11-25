//! Validator for the bounded JSON Schema vocabulary used by analysis-output-v1.json.
//! Schema ownership stays with the checked-in artifact; unsupported keywords fail tests.
use serde_json::Value;
use std::sync::OnceLock;
fn schema() -> &'static Value {
    static SCHEMA: OnceLock<Value> = OnceLock::new();
    SCHEMA.get_or_init(|| {
        serde_json::from_str(include_str!(
            "../../../../../docs/schemas/analysis-output-v1.json"
        ))
        .expect("checked-in analysis schema")
    })
}
pub(super) fn candidate(value: &Value) -> bool {
    if value
        .as_object()
        .is_some_and(|o| o.len() == 2 && o.contains_key("candidate") && o.contains_key("evidence"))
    {
        return matches(
            &value["candidate"],
            &schema()["$defs"]["candidate"],
            schema(),
        ) && matches(
            &value["evidence"],
            &schema()["properties"]["candidates"]["items"]["properties"]["evidence"],
            schema(),
        );
    }
    matches(
        value,
        &schema()["properties"]["candidates"]["items"],
        schema(),
    )
}
/// One "wrong spelling -> intended term" pair of the `misspellings` list.
pub(super) fn misspelling(value: &Value) -> bool {
    matches(value, &schema()["$defs"]["misspelling"], schema())
}
fn matches(value: &Value, rule: &Value, root: &Value) -> bool {
    if let Some(reference) = rule.get("$ref").and_then(Value::as_str) {
        return reference
            .strip_prefix('#')
            .and_then(|path| root.pointer(path))
            .is_some_and(|rule| matches(value, rule, root));
    }
    if let Some(alternatives) = rule.get("anyOf").and_then(Value::as_array) {
        return alternatives.iter().any(|rule| matches(value, rule, root));
    }
    if rule.get("const").is_some_and(|expected| expected != value) {
        return false;
    }
    if rule
        .get("enum")
        .and_then(Value::as_array)
        .is_some_and(|allowed| !allowed.contains(value))
    {
        return false;
    }
    match rule.get("type").and_then(Value::as_str) {
        Some("object") => value.as_object().is_some_and(|object| {
            let Some(properties) = rule.get("properties").and_then(Value::as_object) else {
                return false;
            };
            rule.get("required")
                .and_then(Value::as_array)
                .is_none_or(|required| {
                    required
                        .iter()
                        .all(|key| key.as_str().is_some_and(|key| object.contains_key(key)))
                })
                && object.iter().all(|(key, value)| {
                    properties
                        .get(key)
                        .is_some_and(|rule| matches(value, rule, root))
                })
        }),
        Some("array") => value.as_array().is_some_and(|values| {
            bound(values.len() as u64, rule, "minItems", "maxItems")
                && values
                    .iter()
                    .all(|value| matches(value, &rule["items"], root))
        }),
        Some("string") => value
            .as_str()
            .is_some_and(|s| bound(s.chars().count() as u64, rule, "minLength", "maxLength")),
        Some("integer") => value
            .as_u64()
            .is_some_and(|n| bound(n, rule, "minimum", "maximum")),
        Some("boolean") => value.is_boolean(),
        Some("null") => value.is_null(),
        None => rule.get("enum").is_some() || rule.get("const").is_some(),
        _ => false,
    }
}
fn bound(n: u64, rule: &Value, min: &str, max: &str) -> bool {
    rule.get(min)
        .and_then(Value::as_u64)
        .is_none_or(|min| n >= min)
        && rule
            .get(max)
            .and_then(Value::as_u64)
            .is_none_or(|max| n <= max)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn schema_uses_only_the_supported_validation_vocabulary() {
        fn check(rule: &Value) {
            let object = rule.as_object().expect("schema object");
            if object.contains_key("enum") || object.contains_key("const") {
                assert_eq!(
                    object.get("type").and_then(Value::as_str),
                    Some("string"),
                    "OpenAI structured output requires a type alongside string enum/const"
                );
            }
            for key in object.keys() {
                assert!(
                    [
                        "$id",
                        "$schema",
                        "$ref",
                        "$defs",
                        "type",
                        "additionalProperties",
                        "properties",
                        "required",
                        "items",
                        "anyOf",
                        "enum",
                        "const",
                        "minimum",
                        "maximum",
                        "minLength",
                        "maxLength",
                        "minItems",
                        "maxItems"
                    ]
                    .contains(&key.as_str()),
                    "unsupported {key}"
                );
            }
            if object.get("type").and_then(Value::as_str) == Some("object") {
                assert_eq!(
                    object.get("additionalProperties"),
                    Some(&Value::Bool(false))
                );
            }
            for key in ["properties", "$defs"] {
                if let Some(values) = object.get(key).and_then(Value::as_object) {
                    for value in values.values() {
                        check(value);
                    }
                }
            }
            if let Some(items) = object.get("items") {
                check(items);
            }
            if let Some(values) = object.get("anyOf").and_then(Value::as_array) {
                for value in values {
                    check(value);
                }
            }
        }
        check(schema());
    }
}
