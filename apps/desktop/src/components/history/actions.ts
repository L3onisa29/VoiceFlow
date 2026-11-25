import { toast } from "sonner";
import { historyApi } from "@/lib/history";
import { toUserMessage } from "@/lib/errors";

/** Copies the saved text of a transcription and reports the outcome as a toast. */
export async function copyTranscription(id: number) {
  try {
    await historyApi.copy(id);
    toast.success("Testo copiato.");
    return true;
  } catch (error) {
    toast.error(toUserMessage(error));
    return false;
  }
}
