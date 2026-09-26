import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";
const id = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const context = z.object({
  before: z.string().nullable(),
  after: z.string().nullable(),
  exceptBefore: z.array(z.string()),
  exceptAfter: z.array(z.string()),
});
const example = z.object({ input: z.string(), output: z.string() });
const operation = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("remove-false-start"),
    source: z.array(z.string()),
  }),
  z.object({ kind: z.literal("repeat"), word: z.string() }),
  z.object({
    kind: z.literal("repeat-phrase"),
    phrase: z.array(z.string()).min(2).max(3),
  }),
  z.object({
    kind: z.literal("elision"),
    first: z.string(),
    prefix: z.string(),
  }),
]);
export const candidateSchema = z.object({
  definition: z.discriminatedUnion("category", [
    z.object({
      category: z.literal("terminology"),
      preferred: z.string(),
      variants: z.array(z.string()),
      matching: z.enum(["exact", "unicode-lowercase"]),
      context,
    }),
    z.object({ category: z.literal("cleanup"), operation, context }),
  ]),
  language: z.enum(["*", "it", "en"]),
  groupIds: z.array(id),
  reason: z.string(),
  positiveExamples: z.array(example).min(1).max(16),
  negativeExamples: z.array(example).min(1).max(16),
});
const proposalSchema = z.object({
  id,
  revision: id,
  candidate: candidateSchema,
  state: z.enum(["proposed", "approved", "rejected", "deferred", "disabled"]),
  rejectionReason: z.string().nullable(),
  approvedAt: z.number().nullable(),
  confidence: z.number().int().min(0).max(100).nullable().default(null),
  analysisOutcome: z.string().nullable().default(null),
  conflicts: z.array(z.string()),
  evidence: z.array(
    z.object({
      sessionId: id,
      revisionId: id.nullable(),
      originalIndex: z.number().int().nonnegative().nullable(),
      before: z.string(),
      after: z.string().nullable(),
      humanConfirmed: z.boolean(),
    }),
  ),
});
export const reviewSchema = z.object({
  proposals: z.array(proposalSchema),
  version: z.number().int().nonnegative(),
  dictionaryVersion: id,
  groups: z.array(z.object({ id, name: z.string() })),
  publications: z.array(
    z.object({
      version: id,
      previousVersion: z.number().nullable(),
      action: z.string(),
      createdAt: z.number(),
      proposalIds: z.array(id),
    }),
  ),
});
export type Candidate = z.infer<typeof candidateSchema>;
export type Proposal = z.infer<typeof proposalSchema>;
export type ReviewSnapshot = z.infer<typeof reviewSchema>;
export type ReviewAction =
  | { kind: "approve" | "defer" | "disable" }
  | { kind: "reject"; reason: string }
  | { kind: "edit"; candidate: Candidate }
  | { kind: "restore"; version: number };
export type ReviewRequest = {
  id: number | null;
  expectedRevision: number | null;
  expectedVersion: number;
  dictionaryVersion: number;
  requestKey: string;
  action: ReviewAction;
};
export const suggestionsApi = {
  read: async () => reviewSchema.parse(await invoke("suggestions_snapshot")),
  review: async (request: ReviewRequest) =>
    reviewSchema.parse(await invoke("suggestions_review", { request })),
};
export const stateLabels = {
  proposed: "Da valutare",
  approved: "Approvata · attiva",
  rejected: "Rifiutata",
  deferred: "Rimandata",
  disabled: "Approvata · disattivata",
} as const;
