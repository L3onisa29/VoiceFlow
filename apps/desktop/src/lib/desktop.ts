import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

export const settingsSchema = z.object({
  language: z.enum(["auto", "italian", "english"]),
  format: z.enum(["faithful", "spoken-commands"]),
  cleanup: z.boolean().default(false),
});
export type Settings = z.infer<typeof settingsSchema>;
export const stateSchema = z.object({
  revision: z.number(),
  phase: z.enum([
    "idle",
    "loading",
    "recording",
    "paused",
    "finalizing",
    "complete",
    "error",
  ]),
  modelReady: z.boolean(),
  modelPresent: z.boolean(),
  finalText: z.string(),
  message: z.string().nullable(),
  level: z.number(),
  elapsedSeconds: z.number(),
  device: z.string(),
  settings: settingsSchema,
  transcript: z.object({
    confirmed: z.string(),
    partial: z.string(),
    audioSeconds: z.number(),
    inferenceSeconds: z.number(),
    chunks: z.number(),
    finished: z.boolean(),
  }),
});
export type DictationState = z.infer<typeof stateSchema>;
export const permissionsSchema = z.object({
  microphone: z.boolean(),
  microphoneStatus: z.string(),
  accessibility: z.boolean(),
});
export type Permissions = z.infer<typeof permissionsSchema>;
export async function getState() {
  return stateSchema.parse(await invoke("get_state"));
}
export async function getPermissions() {
  return permissionsSchema.parse(await invoke("get_permissions"));
}
export type Action =
  | "toggle_recording"
  | "stop_recording"
  | "pause_recording"
  | "cancel_recording"
  | "load_model"
  | "copy_transcript"
  | "request_microphone";
export async function action(name: Action) {
  await invoke(name);
}
export async function saveSettings(settings: Settings) {
  await invoke("save_settings", { settings: settingsSchema.parse(settings) });
}
export async function openPrivacy(section: "microphone" | "accessibility") {
  await invoke("open_privacy", { section });
}
