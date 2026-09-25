import { Globe2 } from "lucide-react";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
} from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { settingsSchema, type Settings } from "@/lib/desktop";

type DictationSettingsProps = {
  settings: Settings | undefined;
  locked: boolean;
  onChange: (settings: Settings) => void;
};

export function DictationSettings({
  settings,
  locked,
  onChange,
}: DictationSettingsProps) {
  return (
    <FieldGroup className="grid grid-cols-1 gap-5 xl:grid-cols-3">
      <Field data-disabled={locked || !settings}>
        <FieldLabel htmlFor="language">
          <Globe2 aria-hidden="true" />
          Lingua
        </FieldLabel>
        <Select
          disabled={locked || !settings}
          value={settings?.language ?? "auto"}
          onValueChange={(language) => {
            if (settings)
              void onChange(settingsSchema.parse({ ...settings, language }));
          }}
        >
          <SelectTrigger id="language" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="auto">Rilevamento automatico</SelectItem>
              <SelectItem value="italian">Italiano</SelectItem>
              <SelectItem value="english">English</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <FieldDescription>
          Italiano e inglese, anche nella stessa giornata.
        </FieldDescription>
      </Field>
      <Field data-disabled={locked || !settings}>
        <FieldLabel htmlFor="cleanup">Pulizia locale</FieldLabel>
        <Select
          disabled={locked || !settings}
          value={settings?.cleanup ? "on" : "off"}
          onValueChange={(value) => {
            if (settings)
              void onChange({ ...settings, cleanup: value === "on" });
          }}
        >
          <SelectTrigger id="cleanup" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="off">Disattivata</SelectItem>
              <SelectItem value="on">Riduci false partenze</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <FieldDescription>
          Riduce casi come "il la scheda" in italiano. Le ultime 7 parole
          restano modificabili. Nessuna correzione grammaticale.
        </FieldDescription>
      </Field>
      <Field data-disabled={locked || !settings}>
        <FieldLabel htmlFor="format">Come vuoi il tuo testo</FieldLabel>
        <Select
          disabled={locked || !settings}
          value={settings?.format ?? "faithful"}
          onValueChange={(format) => {
            if (settings)
              void onChange(settingsSchema.parse({ ...settings, format }));
          }}
        >
          <SelectTrigger id="format" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="faithful">Dettatura fedele</SelectItem>
              <SelectItem value="spoken-commands">
                Comandi per paragrafi e liste
              </SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <FieldDescription>
          {settings?.format === "spoken-commands"
            ? "Pronuncia “a capo”, “nuovo paragrafo” o “punto elenco”."
            : settings?.cleanup
              ? "Comandi vocali letterali. Pulizia locale attiva."
              : "Conserva le tue parole, con punteggiatura automatica."}
        </FieldDescription>
      </Field>
    </FieldGroup>
  );
}
