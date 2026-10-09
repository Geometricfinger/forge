// Synthetic JavaScript coverage control; not a production implementation.
export function validateConcurrency(value) {
  if (!Number.isInteger(value) || value < 1) throw new Error("invalid concurrency");
  return value;
}
