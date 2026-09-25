import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";
const minute = z.number().int().min(0).max(1439);
export const scheduleSchema = z.object({
  enabled: z.boolean(),
  startMinute: minute,
  endMinute: minute,
});
const statusSchema = z.object({
  schedule: scheduleSchema,
  installed: z.boolean(),
  loaded: z.boolean(),
  matchesConfiguration: z.boolean(),
  helperAvailable: z.boolean(),
  workerRunning: z.boolean(),
  recording: z.boolean(),
  phase: z.string(),
  leaseUntil: z.number(),
  nextAttempt: z.number(),
  retryCount: z.number().int().nonnegative(),
  lastError: z.string().nullable(),
});
export type Schedule = z.infer<typeof scheduleSchema>;
export type SchedulerStatus = z.infer<typeof statusSchema>;
export const schedulerApi = {
  status: async () => statusSchema.parse(await invoke("scheduler_status")),
  configure: async (schedule: Schedule, consent: boolean) =>
    statusSchema.parse(
      await invoke("scheduler_configure", {
        schedule: scheduleSchema.parse(schedule),
        consent,
      }),
    ),
};
export function timeValue(minute: number) {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}
export function minuteValue(time: string) {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + m;
}
