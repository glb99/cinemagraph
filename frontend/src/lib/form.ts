/** Number inputs are kept as strings in component state (what the DOM actually
 * holds) and parsed once at submit time, so a half-typed "0." or a cleared
 * field never round-trips through NaN. */
export function numberField(value: string, fallback: number): number {
  const parsed = Number(value);
  return value.trim() === "" || Number.isNaN(parsed) ? fallback : parsed;
}

/** `undefined` for a blank field -- the generated form-data serializer skips
 * undefined/null entries entirely, which is exactly what the API's optional
 * fields want (omitted, not sent as an empty string). */
export function optionalNumberField(value: string): number | undefined {
  const parsed = Number(value);
  return value.trim() === "" || Number.isNaN(parsed) ? undefined : parsed;
}
