import { spawnSync } from "node:child_process";
import { statSync } from "node:fs";

export type RunningDesktop = {
  pid: number;
  path: string;
  inode: string;
  status: "current" | "stale" | "other-path";
};

// lsof describes the mapped image, which can survive replacement of the file
// at the same path. A build timestamp alone cannot identify the running image.
export function compareDesktopImages(
  listing: string,
  executable: string,
  inode: string,
): RunningDesktop[] {
  const images: RunningDesktop[] = [];
  let pid = 0;
  let mappedInode = "";
  for (const line of listing.split("\n")) {
    if (line.startsWith("p")) {
      pid = Number(line.slice(1));
      mappedInode = "";
    } else if (line.startsWith("f")) {
      mappedInode = "";
    } else if (line.startsWith("i")) {
      mappedInode = line.slice(1);
    } else if (line.startsWith("n")) {
      const path = line.slice(1);
      if (!path.endsWith("/voiceflow-desktop")) continue;
      if (!Number.isSafeInteger(pid) || pid <= 0 || !/^\d+$/.test(mappedInode))
        throw new Error(
          "Impossibile identificare l’eseguibile VoiceFlow attivo.",
        );
      images.push({
        pid,
        path,
        inode: mappedInode,
        status:
          path !== executable
            ? "other-path"
            : mappedInode === inode
              ? "current"
              : "stale",
      });
    }
  }
  return images;
}

export function inspectDesktopRuntime(executable: string): RunningDesktop[] {
  const inode = statSync(executable, { bigint: true }).ino.toString();
  const result = spawnSync(
    "/usr/sbin/lsof",
    ["-a", "-d", "txt", "-c", "voiceflow", "-Fpin"],
    { encoding: "utf8" },
  );
  // lsof exits 1 with empty output when no process matches.
  if (
    result.error ||
    ![0, 1].includes(result.status ?? -1) ||
    result.stderr.trim() ||
    (result.status === 1 && result.stdout.trim())
  )
    throw new Error("Impossibile verificare il processo VoiceFlow con lsof.");
  return compareDesktopImages(result.stdout, executable, inode);
}

export function desktopRuntimeMessage(images: RunningDesktop[]): string {
  if (images.length === 0)
    return "VoiceFlow non è in esecuzione. La build non è stata avviata.";
  if (images.every((image) => image.status === "current"))
    return "VoiceFlow in esecuzione usa l’eseguibile del bundle verificato.";
  const pending = images
    .filter((image) => image.status !== "current")
    .map((image) => `PID ${image.pid}: ${image.path} (${image.status})`)
    .join("\n");
  return `VoiceFlow in esecuzione non usa la build verificata. Termina la sessione, esci dall’app e riapri questo bundle prima di provarla.\n${pending}`;
}
