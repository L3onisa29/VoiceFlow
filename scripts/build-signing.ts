/** A local build must keep its macOS identity across changes to executable bytes. */
export type SigningConfig = { identity: string; requirement?: string };
export function selectIdentity(
  config: SigningConfig | undefined,
  requested?: string,
): string {
  const identity = requested || config?.identity;
  if (
    typeof identity !== "string" ||
    !identity.trim() ||
    identity.trim() === "-"
  )
    throw new Error(
      "Firma stabile mancante. Imposta APPLE_SIGNING_IDENTITY con un certificato del Portachiavi; le build ad-hoc sono disabilitate.",
    );
  if (config && identity !== config.identity)
    throw new Error(
      "Il certificato differisce da quello salvato per VoiceFlow. Cambio di identità bloccato per conservare i permessi macOS.",
    );
  return identity;
}
export function checkRequirement(output: string, previous?: string): string {
  const requirement = output.match(/^designated => (.+)$/m)?.[1]?.trim();
  if (
    !requirement ||
    requirement.includes("cdhash") ||
    !requirement.includes("anchor apple") ||
    !requirement.includes('identifier "com.voiceflow.desktop"')
  )
    throw new Error(
      "La build non ha un’identità Apple stabile per com.voiceflow.desktop.",
    );
  if (previous && requirement !== previous)
    throw new Error(
      "L’identità della nuova build è cambiata. Non installarla: i permessi macOS potrebbero essere persi.",
    );
  return requirement;
}
