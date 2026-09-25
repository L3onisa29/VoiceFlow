import { SessionPrivacy } from "@/components/analysis/session-privacy";
import { useEffect, useRef, useState } from "react";
import { Copy, Save, Undo2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  correctionCategorySchema,
  historyApi,
  type CorrectionCategory,
  type HistoryDetail,
} from "@/lib/history";

const categories: Record<CorrectionCategory, string> = {
  recognition: "Riconoscimento",
  cleanup: "Pulizia",
  style: "Stile",
};

export function HistoryEditor({
  id,
  onChanged,
  onBusy,
}: {
  id: number;
  onChanged: () => void;
  onBusy: (busy: boolean) => void;
}) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [category, setCategory] = useState<CorrectionCategory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  // Reuse the key if delivery fails after the database committed the edit.
  const attempt = useRef<{ text: string; version: number; key: string } | null>(
    null,
  );
  useEffect(() => {
    let active = true;
    setError(null);
    void historyApi
      .detail(id)
      .then((result) => {
        if (active) {
          setDetail(result);
          setDraft(result.entry.text);
        }
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    return () => {
      active = false;
    };
  }, [id, retry]);

  async function mutate(
    operation: () => Promise<HistoryDetail>,
    message: string,
  ) {
    setBusy(true);
    onBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await operation();
      setDetail(result);
      setDraft(result.entry.text);
      setCategory(null);
      setNotice(message);
      onChanged();
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  async function save() {
    if (!detail || busy) return;
    if (
      attempt.current?.text !== draft ||
      attempt.current.version !== detail.editVersion
    ) {
      attempt.current = {
        text: draft,
        version: detail.editVersion,
        key: crypto.randomUUID(),
      };
    }
    const requestKey = attempt.current.key;
    await mutate(
      () =>
        historyApi.edit({
          id,
          expectedVersion: detail.editVersion,
          requestKey,
          text: draft,
        }),
      "Correzione salvata. Nessuna sostituzione automatica attivata.",
    );
  }
  async function copy() {
    setError(null);
    try {
      await historyApi.copy(id);
      setNotice("Testo salvato copiato.");
    } catch (error) {
      setError(String(error));
    }
  }
  const dirty = detail !== null && draft !== detail.entry.text;
  const current = detail?.revisions.find(
    (revision) => revision.id === detail.currentRevision,
  );

  return (
    <>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      )}
      {!detail ? (
        error ? (
          <Button
            variant="outline"
            onClick={() => setRetry((value) => value + 1)}
          >
            Riprova
          </Button>
        ) : (
          <Skeleton className="h-64" />
        )
      ) : (
        <>
          <ScrollArea
            className="max-h-[60vh]"
            viewportProps={{
              className: "max-h-[60vh]",
              "aria-label": "Dettagli della trascrizione",
              tabIndex: 0,
            }}
          >
            <div className="flex flex-col gap-5 pr-4">
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="history-corrected-text">
                    Testo salvato
                  </FieldLabel>
                  <Textarea
                    id="history-corrected-text"
                    value={draft}
                    disabled={busy}
                    onChange={(event) => {
                      setDraft(event.target.value);
                      setNotice(null);
                    }}
                    className="min-h-36"
                  />
                  <FieldDescription>
                    Modifica liberamente il testo. Parole, durata e velocità
                    restano riferite alla dettatura originale.
                  </FieldDescription>
                </Field>
              </FieldGroup>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy || !dirty || !draft.trim()}
                  onClick={() => void save()}
                >
                  <Save data-icon="inline-start" />
                  Salva correzione
                </Button>
                <Button
                  variant="outline"
                  disabled={busy || dirty || !current}
                  onClick={() =>
                    void mutate(
                      () => historyApi.undo(id, detail.editVersion),
                      "Correzione annullata e relativa conferma rimossa.",
                    )
                  }
                >
                  <Undo2 data-icon="inline-start" />
                  Annulla ultima correzione
                </Button>
                {dirty && (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setDraft(detail.entry.text)}
                  >
                    Scarta modifiche
                  </Button>
                )}
              </div>
              {current && (
                <FieldGroup>
                  <Field>
                    <FieldLabel>Ricorda questa correzione</FieldLabel>
                    <FieldDescription>
                      Indica perché hai modificato il testo. Questa conferma
                      conserva un esempio da rivedere, senza attivare regole o
                      sostituzioni.
                    </FieldDescription>
                    <ToggleGroup
                      type="single"
                      variant="outline"
                      value={category ?? ""}
                      disabled={busy || dirty}
                      aria-label="Categoria della correzione"
                      onValueChange={(value) =>
                        setCategory(
                          value ? correctionCategorySchema.parse(value) : null,
                        )
                      }
                      className="flex-wrap justify-start"
                    >
                      {Object.entries(categories).map(([value, label]) => (
                        <ToggleGroupItem key={value} value={value}>
                          {label}
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                    {current.category && (
                      <FieldDescription>
                        Già ricordata come{" "}
                        {categories[current.category].toLocaleLowerCase(
                          "it-IT",
                        )}
                        .
                      </FieldDescription>
                    )}
                    <Button
                      variant="outline"
                      disabled={busy || dirty || !category}
                      onClick={() =>
                        category &&
                        void mutate(
                          () => historyApi.remember(id, current.id, category),
                          "Correzione ricordata. Le regole attive non sono cambiate.",
                        )
                      }
                    >
                      Ricorda questa correzione
                    </Button>
                  </Field>
                </FieldGroup>
              )}
              <SessionPrivacy id={id} disabled={busy} />
              <Separator />
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="history-produced-text">
                    Risultato elaborato originale
                  </FieldLabel>
                  <Textarea
                    id="history-produced-text"
                    readOnly
                    value={detail.producedText}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="history-original-asr">
                    Originale ASR
                  </FieldLabel>
                  {detail.originalAsr === null ? (
                    <FieldDescription>
                      Originale ASR non disponibile per questa trascrizione
                      precedente.
                    </FieldDescription>
                  ) : (
                    <>
                      <Textarea
                        id="history-original-asr"
                        readOnly
                        value={detail.originalAsr.join("\n")}
                      />
                      <FieldDescription>
                        Testo esatto del riconoscitore prima di filtri, comandi
                        e pulizia. Ogni segmento concluso con una pausa è
                        mostrato su una nuova riga. Può contenere marcatori del
                        modello.
                      </FieldDescription>
                    </>
                  )}
                </Field>
              </FieldGroup>
              <div className="flex flex-col gap-2">
                <p className="text-sm font-medium">Dati della sessione</p>
                <p className="text-sm text-muted-foreground">
                  Lingua: {detail.entry.language}
                </p>
                {detail.processing ? (
                  <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs wrap-anywhere">
                    <dt>Modello</dt>
                    <dd>{detail.processing.modelId}</dd>
                    <dt>Revisione prevista</dt>
                    <dd>{detail.processing.modelRevision}</dd>
                    <dt>Provenienza</dt>
                    <dd>{detail.processing.modelProvenance}</dd>
                    <dt>Decoder</dt>
                    <dd>{detail.processing.decoderVersion}</dd>
                    <dt>Processore</dt>
                    <dd>{detail.processing.processorVersion}</dd>
                    <dt>Formato</dt>
                    <dd>{detail.processing.format}</dd>
                    <dt>Pulizia</dt>
                    <dd>
                      {detail.processing.cleanup ? "Attiva" : "Disattiva"} ·{" "}
                      {detail.processing.cleanupVersion}
                    </dd>
                    <dt>Gruppi</dt>
                    <dd>{detail.processing.groups.join(", ") || "Nessuno"}</dd>
                    <dt>Dizionario</dt>
                    <dd>
                      {detail.processing.dictionaryVersion ??
                        "Nessuna sostituzione applicata"}
                    </dd>
                  </dl>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Versioni di elaborazione non disponibili per questa
                    trascrizione precedente.
                  </p>
                )}
              </div>
              {detail.revisions.length > 0 && (
                <div className="flex flex-col gap-3">
                  <p className="text-sm font-medium">
                    Revisioni confermate dall'utente
                  </p>
                  {detail.revisions.map((revision) => (
                    <div key={revision.id} className="flex flex-col gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">
                          Revisione {revision.id}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {new Date(revision.createdAt).toLocaleString("it-IT")}
                        </span>
                        {revision.reverted ? (
                          <Badge variant="outline">Annullata</Badge>
                        ) : revision.category ? (
                          <Badge>{categories[revision.category]}</Badge>
                        ) : (
                          <Badge variant="outline">Solo modifica</Badge>
                        )}
                      </div>
                      <p className="whitespace-pre-wrap wrap-anywhere text-sm">
                        <span className="text-muted-foreground">Prima: </span>
                        {revision.beforeText}
                      </p>
                      <p className="whitespace-pre-wrap wrap-anywhere text-sm">
                        <span className="text-muted-foreground">Dopo: </span>
                        {revision.text}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </ScrollArea>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy || dirty}
              onClick={() => void copy()}
            >
              <Copy data-icon="inline-start" />
              Copia testo salvato
            </Button>
          </DialogFooter>
        </>
      )}
    </>
  );
}
