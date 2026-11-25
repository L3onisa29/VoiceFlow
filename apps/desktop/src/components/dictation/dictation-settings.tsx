import { useEffect, useState } from "react";
import { toast } from "sonner";
import { SettingsRow } from "@/components/settings/settings-row";
import { FieldGroup } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { saveSettings, settingsSchema, type Settings } from "@/lib/desktop";
import { toUserMessage } from "@/lib/errors";

type DictationSettingsProps = {
  settings: Settings | undefined;
  locked: boolean;
};

/**
 * Language and text preferences. Each select applies on change; the chosen
 * value is shown until the engine publishes the new settings, and a failed
 * write reverts it.
 */
export function DictationSettings({
  settings,
  locked,
}: DictationSettingsProps) {
  const [pending, setPending] = useState<Settings | null>(null);
  const [saving, setSaving] = useState<keyof Settings | null>(null);

  // The engine echoes saved settings in its state: drop the optimistic copy.
  useEffect(() => setPending(null), [settings]);

  const current = pending ?? settings;
  const disabled = locked || !settings || saving !== null;

  async function apply(key: keyof Settings, value: unknown) {
    if (!current) return;
    const result = settingsSchema.safeParse({ ...current, [key]: value });
    if (!result.success) return;
    setPending(result.data);
    setSaving(key);
    try {
      await saveSettings(result.data);
    } catch (error) {
      setPending(null);
      toast.error(toUserMessage(error));
    } finally {
      setSaving(null);
    }
  }

  return (
    <FieldGroup className="gap-0">
      <SettingsRow
        htmlFor="language"
        label="Lingua"
        description="Lingua del parlato da trascrivere."
        disabled={disabled}
        saving={saving === "language"}
      >
        <Select
          disabled={disabled}
          value={current?.language ?? "auto"}
          onValueChange={(language) => void apply("language", language)}
        >
          <SelectTrigger
            id="language"
            className="w-full"
            aria-describedby="language-description"
          >
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
      </SettingsRow>
      <SettingsRow
        htmlFor="cleanup"
        label="Pulizia locale"
        description="Riduce false partenze come “il la scheda”, senza correggere la grammatica."
        disabled={disabled}
        saving={saving === "cleanup"}
      >
        <Select
          disabled={disabled}
          value={current?.cleanup ? "on" : "off"}
          onValueChange={(value) => void apply("cleanup", value === "on")}
        >
          <SelectTrigger
            id="cleanup"
            className="w-full"
            aria-describedby="cleanup-description"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="off">Disattivata</SelectItem>
              <SelectItem value="on">Riduci false partenze</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
      </SettingsRow>
      <SettingsRow
        htmlFor="format"
        label="Formato del testo"
        description={
          current?.format === "spoken-commands"
            ? "Pronuncia “a capo”, “nuovo paragrafo” o “punto elenco”."
            : "Conserva le tue parole, con punteggiatura automatica."
        }
        disabled={disabled}
        saving={saving === "format"}
      >
        <Select
          disabled={disabled}
          value={current?.format ?? "faithful"}
          onValueChange={(format) => void apply("format", format)}
        >
          <SelectTrigger
            id="format"
            className="w-full"
            aria-describedby="format-description"
          >
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
      </SettingsRow>
    </FieldGroup>
  );
}
