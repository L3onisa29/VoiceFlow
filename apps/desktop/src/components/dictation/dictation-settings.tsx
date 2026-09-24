import { Globe2 } from "lucide-react";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
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
    <FieldGroup className="settings-grid">
      <Field>
        <FieldLabel htmlFor="language">
          <Globe2 className="size-3.5" />
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
        <p className="field-hint">
          Italiano e inglese, anche nella stessa giornata.
        </p>
      </Field>
      <Field>
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
        <p className="field-hint">
          {settings?.format === "spoken-commands"
            ? "Pronuncia “a capo”, “nuovo paragrafo” o “punto elenco”."
            : "Conserva le tue parole, con punteggiatura automatica."}
        </p>
      </Field>
    </FieldGroup>
  );
}
