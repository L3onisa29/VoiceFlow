import { notFound } from "@tanstack/react-router";
import { z } from "zod";

// Search values arrive JSON-parsed (`?id=3` is the number 3). Invalid values
// fall back to undefined instead of failing the route.
export const optionalIdSearch = z
  .number()
  .int()
  .positive()
  .max(Number.MAX_SAFE_INTEGER)
  .optional()
  .catch(undefined);

// Path segments are always strings: accept canonical positive integers only.
const idParam = z
  .string()
  .regex(/^[1-9]\d*$/)
  .transform(Number)
  .refine(Number.isSafeInteger);

/** Path params parser: numeric ids, or the route's notFoundComponent. */
export function idParams<const K extends string>(...keys: K[]) {
  const schema = z.object(
    Object.fromEntries(keys.map((key) => [key, idParam])) as Record<
      K,
      typeof idParam
    >,
  );
  return {
    parse: (raw: Record<K, string>) => {
      const result = schema.safeParse(raw);
      if (!result.success) throw notFound();
      return result.data as Record<K, number>;
    },
    stringify: (params: Record<K, number>) =>
      Object.fromEntries(
        keys.map((key) => [key, String(params[key])]),
      ) as Record<K, string>,
  };
}

/**
 * Legacy redirects forward the raw path segments unchanged, so an invalid id
 * reaches the new route and gets its not-found state. Typed as the parsed ids.
 */
export function forwardIdParams<K extends string>(params: Record<K, string>) {
  return params as unknown as Record<K, number>;
}
