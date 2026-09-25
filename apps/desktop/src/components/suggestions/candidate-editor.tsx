import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
} from "@/components/ui/select";
import { DialogFooter } from "@/components/ui/dialog";
import type { Candidate, ReviewSnapshot } from "@/lib/suggestions";

function Choice({
  id,
  value,
  options,
  onChange,
}: {
  id: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
export function CandidateEditor({
  initial,
  groups,
  busy,
  onSave,
  onCancel,
}: {
  initial: Candidate;
  groups: ReviewSnapshot["groups"];
  busy: boolean;
  onSave: (candidate: Candidate) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const definition = draft.definition;
  const setDefinition = (definition: Candidate["definition"]) =>
    setDraft((draft) => ({ ...draft, definition }));
  const patchContext = (
    key: keyof Candidate["definition"]["context"],
    value: string | string[] | null,
  ) =>
    setDefinition({
      ...definition,
      context: { ...definition.context, [key]: value },
    });
  return (
    <form
      className="flex min-h-0 flex-1 flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(draft);
      }}
    >
      <div
        className="min-h-0 flex-1 overflow-y-auto px-1"
        data-testid="candidate-scroll"
      >
        <fieldset disabled={busy} className="min-w-0">
          <FieldGroup>
            {definition.category === "terminology" ? (
              <>
                <Field>
                  <FieldLabel htmlFor="preferred">Grafia preferita</FieldLabel>
                  <Input
                    id="preferred"
                    value={definition.preferred}
                    maxLength={200}
                    required
                    onChange={(e) =>
                      setDefinition({
                        ...definition,
                        preferred: e.target.value,
                      })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="variants">
                    Varianti, una per riga
                  </FieldLabel>
                  <Textarea
                    id="variants"
                    value={definition.variants.join("\n")}
                    required
                    onChange={(e) =>
                      setDefinition({
                        ...definition,
                        variants: e.target.value.split("\n"),
                      })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="matching">Corrispondenza</FieldLabel>
                  <Choice
                    id="matching"
                    value={definition.matching}
                    options={[
                      ["exact", "Esatta"],
                      ["unicode-lowercase", "Ignora maiuscole e minuscole"],
                    ]}
                    onChange={(value) =>
                      setDefinition({
                        ...definition,
                        matching: value as "exact" | "unicode-lowercase",
                      })
                    }
                  />
                </Field>
              </>
            ) : (
              <>
                <Field>
                  <FieldLabel htmlFor="operation">
                    Operazione di pulizia
                  </FieldLabel>
                  <Choice
                    id="operation"
                    value={definition.operation.kind}
                    options={[
                      [
                        "remove-false-start",
                        "Elimina la prima parola della falsa partenza",
                      ],
                      ["repeat", "Ripetizione di una parola funzionale"],
                      ["elision", "Elisione della / dell’"],
                    ]}
                    onChange={(value) =>
                      setDefinition({
                        ...definition,
                        operation:
                          value === "repeat"
                            ? { kind: "repeat", word: "il" }
                            : value === "elision"
                              ? {
                                  kind: "elision",
                                  first: "della",
                                  prefix: "dell'",
                                }
                              : { kind: "remove-false-start", source: [] },
                      })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="source">
                    {definition.operation.kind === "remove-false-start"
                      ? "Espressione completa, da 2 a 7 parole"
                      : definition.operation.kind === "repeat"
                        ? "Parola ripetuta"
                        : "Prefisso da conservare"}
                  </FieldLabel>
                  <Input
                    id="source"
                    required
                    value={
                      definition.operation.kind === "remove-false-start"
                        ? definition.operation.source.join(" ")
                        : definition.operation.kind === "repeat"
                          ? definition.operation.word
                          : definition.operation.prefix
                    }
                    onChange={(e) =>
                      setDefinition({
                        ...definition,
                        operation:
                          definition.operation.kind === "remove-false-start"
                            ? {
                                ...definition.operation,
                                source: e.target.value.split(" "),
                              }
                            : definition.operation.kind === "repeat"
                              ? {
                                  ...definition.operation,
                                  word: e.target.value,
                                }
                              : {
                                  ...definition.operation,
                                  prefix: e.target.value,
                                },
                      })
                    }
                  />
                </Field>
              </>
            )}
            <Field>
              <FieldLabel htmlFor="language">Lingua</FieldLabel>
              <Choice
                id="language"
                value={draft.language}
                options={[
                  ["*", "Tutte le lingue"],
                  ["it", "Italiano"],
                  ["en", "Inglese"],
                ]}
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    language: value as Candidate["language"],
                  })
                }
              />
            </Field>
            <FieldSet>
              <FieldLegend>Gruppi</FieldLegend>
              <p className="text-sm text-muted-foreground">
                Nessun gruppo selezionato: regola disponibile in ogni contesto.
              </p>
              {groups.map((g) => (
                <Field key={g.id} orientation="horizontal">
                  <Checkbox
                    id={`group-${g.id}`}
                    checked={draft.groupIds.includes(g.id)}
                    onCheckedChange={(checked) =>
                      setDraft({
                        ...draft,
                        groupIds: checked
                          ? [...draft.groupIds, g.id]
                          : draft.groupIds.filter((id) => id !== g.id),
                      })
                    }
                  />
                  <FieldLabel htmlFor={`group-${g.id}`}>{g.name}</FieldLabel>
                </Field>
              ))}
            </FieldSet>
            <FieldGroup className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {(
                [
                  ["before", "Solo dopo la parola"],
                  ["after", "Solo prima della parola"],
                  ["exceptBefore", "Escludi dopo, una parola per riga"],
                  ["exceptAfter", "Escludi prima, una parola per riga"],
                ] as const
              ).map(([key, label]) => (
                <Field key={key}>
                  <FieldLabel htmlFor={key}>{label}</FieldLabel>
                  {key === "before" || key === "after" ? (
                    <Input
                      id={key}
                      value={definition.context[key] ?? ""}
                      onChange={(e) =>
                        patchContext(key, e.target.value || null)
                      }
                    />
                  ) : (
                    <Textarea
                      id={key}
                      value={definition.context[key].join("\n")}
                      onChange={(e) =>
                        patchContext(
                          key,
                          e.target.value.split("\n").filter(Boolean),
                        )
                      }
                    />
                  )}
                </Field>
              ))}
            </FieldGroup>
            <Field>
              <FieldLabel htmlFor="reason">Motivazione</FieldLabel>
              <Textarea
                id="reason"
                required
                maxLength={2000}
                value={draft.reason}
                onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
              />
            </Field>
            {(["positiveExamples", "negativeExamples"] as const).map((kind) => (
              <FieldSet key={kind}>
                <FieldLegend>
                  {kind === "positiveExamples"
                    ? "Esempi da correggere"
                    : "Frasi di controllo"}
                </FieldLegend>
                {draft[kind].map((example, index) => (
                  <FieldGroup
                    key={index}
                    className="grid grid-cols-1 gap-3 sm:grid-cols-2"
                  >
                    {(["input", "output"] as const).map((part) => (
                      <Field key={part}>
                        <FieldLabel htmlFor={`${kind}-${index}-${part}`}>
                          {part === "input" ? "Prima" : "Risultato atteso"}{" "}
                          {index + 1}
                        </FieldLabel>
                        <Textarea
                          id={`${kind}-${index}-${part}`}
                          required
                          maxLength={2048}
                          value={example[part]}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              [kind]: draft[kind].map((item, i) =>
                                i === index
                                  ? { ...item, [part]: e.target.value }
                                  : item,
                              ),
                            })
                          }
                        />
                      </Field>
                    ))}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={draft[kind].length <= 1}
                      aria-label={`Rimuovi ${kind === "positiveExamples" ? "esempio" : "controllo"} ${index + 1}`}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          [kind]: draft[kind].filter((_, i) => i !== index),
                        })
                      }
                    >
                      Rimuovi esempio
                    </Button>
                  </FieldGroup>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={draft[kind].length >= 16}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      [kind]: [...draft[kind], { input: "", output: "" }],
                    })
                  }
                >
                  Aggiungi esempio
                </Button>
              </FieldSet>
            ))}
          </FieldGroup>
        </fieldset>
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={onCancel}
        >
          Annulla
        </Button>
        <Button type="submit" disabled={busy}>
          Salva proposta
        </Button>
      </DialogFooter>
    </form>
  );
}
