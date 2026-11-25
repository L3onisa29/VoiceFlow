import type { ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Activity, Mic, Plus, Square } from "lucide-react";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { settingsSections } from "@/components/settings/settings-navigation";
import { useDictation, useDictationState } from "@/hooks/use-dictation";
import { primaryNavigation, settingsNavigation } from "./navigation";

function Shortcut({ keys }: { keys: string[] }) {
  return (
    <CommandShortcut>
      <KbdGroup>
        {keys.map((key) => (
          <Kbd key={key}>{key}</Kbd>
        ))}
      </KbdGroup>
    </CommandShortcut>
  );
}

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const state = useDictationState("phase", "modelReady");
  const { permissions, busy, execute } = useDictation((s) => ({
    permissions: s.permissions,
    busy: s.busy,
    execute: s.execute,
  }));
  // Same enable rules as RecordingControls.
  const active = state?.phase === "recording" || state?.phase === "paused";
  const processing =
    state?.phase === "loading" ||
    state?.phase === "stopping" ||
    state?.phase === "finalizing";
  const dictationDisabled =
    busy ||
    processing ||
    (!active && (!state?.modelReady || !permissions?.microphone));
  const run = (action: () => void) => {
    onOpenChange(false);
    action();
  };
  const SettingsIcon = settingsNavigation.icon;
  const item = (
    value: string,
    label: ReactNode,
    onSelect: () => void,
    options: {
      keywords?: string[];
      shortcut?: string[];
      icon?: ReactNode;
    } = {},
  ) => (
    <CommandItem
      key={value}
      value={value}
      keywords={options.keywords}
      onSelect={() => run(onSelect)}
    >
      {options.icon}
      <span>{label}</span>
      {options.shortcut && <Shortcut keys={options.shortcut} />}
    </CommandItem>
  );

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Comandi"
      description="Cerca una pagina o un'azione."
    >
      <Command>
        <CommandInput placeholder="Cerca pagine e azioni…" />
        <CommandList>
          <CommandEmpty>Nessun risultato.</CommandEmpty>
          <CommandGroup heading="Vai a">
            {primaryNavigation.map(({ to, label, icon: Icon }, index) =>
              item(label, label, () => void navigate({ to }), {
                icon: <Icon aria-hidden="true" />,
                shortcut: ["⌘", String(index + 1)],
              }),
            )}
            {item(
              "Revisione: Esecuzioni",
              "Revisione: Esecuzioni",
              () => void navigate({ to: "/review/runs" }),
              {
                keywords: ["analisi", "esiti"],
                icon: <Activity aria-hidden="true" />,
              },
            )}
            {item(
              settingsNavigation.label,
              settingsNavigation.label,
              () => void navigate({ to: settingsNavigation.to }),
              {
                icon: <SettingsIcon aria-hidden="true" />,
                shortcut: ["⌘", ","],
              },
            )}
            {settingsSections.map(({ id, label, icon: Icon, keywords }) =>
              item(
                `Impostazioni: ${label}`,
                `Impostazioni: ${label}`,
                () =>
                  void navigate({ to: "/settings", search: { section: id } }),
                {
                  keywords: keywords.split(" "),
                  icon: <Icon aria-hidden="true" />,
                },
              ),
            )}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Azioni">
            <CommandItem
              value={active ? "Termina dettatura" : "Inizia dettatura"}
              disabled={dictationDisabled}
              onSelect={() =>
                run(
                  () =>
                    void execute(
                      active ? "stop_recording" : "toggle_recording",
                    ),
                )
              }
            >
              {active ? (
                <Square aria-hidden="true" />
              ) : (
                <Mic aria-hidden="true" />
              )}
              <span>{active ? "Termina dettatura" : "Inizia dettatura"}</span>
              <Shortcut keys={["⌥", "Spazio"]} />
            </CommandItem>
            {item(
              "Aggiungi parola",
              "Aggiungi parola",
              () => void navigate({ to: "/dictionary", search: { new: 1 } }),
              {
                keywords: ["dizionario", "termine"],
                icon: <Plus aria-hidden="true" />,
              },
            )}
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
