import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

export const analysisReasoningSchema = z.enum([
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);
export const analysisReasoningOptions = [
  { value: "low", label: "Basso" },
  { value: "medium", label: "Medio · predefinito" },
  { value: "high", label: "Alto" },
  { value: "xhigh", label: "Molto alto" },
  { value: "max", label: "Massimo" },
] as const;

export const analysisSettingsSchema = z.object({
  enabled: z.boolean(),
  autoApproveHighConfidence: z.boolean().default(false),
  model: z
    .string()
    .min(1)
    .max(128)
    .regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/)
    .default("gpt-6-luna"),
  reasoningEffort: analysisReasoningSchema.default("medium"),
  maxSources: z.number().int().min(1).max(100),
  maxInputBytes: z.number().int().min(4096).max(262144),
  maxRequestsPerDay: z.number().int().min(1).max(20),
  requestTimeoutSeconds: z.number().int().min(1).max(600),
  maxSecondsPerDay: z.number().int().min(1).max(3600),
});
const count = z.number().int().nonnegative();
const statusSchema = z.object({
  settings: analysisSettingsSchema,
  requestsToday: count,
  inputBytesToday: count,
  elapsedSecondsToday: count,
  reservedSecondsToday: count,
  pendingSources: count,
  parkedSources: count.default(0),
  excludedSessions: count,
  lastState: z.string().nullable(),
  lastError: z.string().nullable(),
  acceptedCandidates: count,
  rejectedCandidates: count,
});
export type AnalysisSettings = z.infer<typeof analysisSettingsSchema>;
export type AnalysisStatus = z.infer<typeof statusSchema>;
export const analysisApi = {
  status: async () => statusSchema.parse(await invoke("analysis_status")),
  configure: async (settings: AnalysisSettings, consent: boolean) =>
    statusSchema.parse(
      await invoke("analysis_configure", {
        settings: analysisSettingsSchema.parse(settings),
        consent,
      }),
    ),
  excluded: async (id: number) =>
    z.boolean().parse(await invoke("analysis_excluded", { id })),
  exclude: (id: number, excluded: boolean) =>
    invoke<void>("analysis_exclude", { id, excluded }),
};
export { analysisErrors } from "./analysis-errors";
