import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const recordingSchema = z.object({
  duration: z.number().nonnegative(),
  hypothesis: z.string(),
  hypothesisLanguage: z.string(),
  recordedAt: z.number(),
  device: z.string(),
});
const promptSchema = z.object({
  id: z.string().min(1),
  text: z.string(),
  split: z.enum(["train", "test"]),
  skipped: z.boolean(),
  recording: recordingSchema.nullable(),
});
const statusSchema = z.object({
  available: z.boolean(),
  folder: z.string(),
  language: z.string(),
  prompts: z.array(promptSchema),
  recorded: z.number().int().nonnegative(),
  recordedTest: z.number().int().nonnegative(),
  seconds: z.number().nonnegative(),
  recording: z.string().nullable(),
});
export type ProfilePrompt = z.infer<typeof promptSchema>;
export type ProfileStatus = z.infer<typeof statusSchema>;

/** Avanzamento ed esito di una lettura. Mai audio, mai testo. */
export const profileEventSchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("progress"),
    id: z.string(),
    seconds: z.number(),
    level: z.number(),
  }),
  z.object({ kind: z.literal("saved"), id: z.string() }),
  z.object({ kind: z.literal("cancelled"), id: z.string() }),
  z.object({ kind: z.literal("error"), id: z.string(), message: z.string() }),
]);
export type ProfileEvent = z.infer<typeof profileEventSchema>;

export const voiceProfileKey = ["voice-profile"] as const;
export const voiceProfileApi = {
  status: async () => statusSchema.parse(await invoke("voice_profile_status")),
  start: (id: string) => invoke<void>("voice_profile_start", { id }),
  stop: () => invoke<void>("voice_profile_stop"),
  cancel: () => invoke<void>("voice_profile_cancel"),
  skip: async (id: string, skipped: boolean) =>
    statusSchema.parse(await invoke("voice_profile_skip", { id, skipped })),
  deleteAll: async () =>
    statusSchema.parse(await invoke("voice_profile_delete")),
};

/** La prima frase ancora da leggere a partire da `from`, girando una volta sola. */
export function nextPending(prompts: ProfilePrompt[], from: number) {
  for (let step = 0; step < prompts.length; step++) {
    const index = (from + step) % prompts.length;
    if (!prompts[index].recording && !prompts[index].skipped) return index;
  }
  return null;
}
export function profileDuration(seconds: number) {
  const total = Math.round(seconds);
  return total < 60
    ? `${total} s`
    : `${Math.floor(total / 60)} min ${total % 60} s`;
}
/** Counters shown in the progress header. */
export function profileCounts(prompts: ProfilePrompt[]) {
  return {
    total: prompts.length,
    test: prompts.filter((p) => p.split === "test").length,
    skipped: prompts.filter((p) => p.skipped).length,
  };
}
/** Microphone level (0–100) from the RMS level sent by the recorder. */
export function levelPercent(level: number) {
  return Math.round(Math.min(1, level * 12) * 100);
}
