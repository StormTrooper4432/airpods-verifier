/**
 * Serial masking for anything the sheet prints.
 *
 * The filed records were masked by hand when their JSON was authored, which
 * left the live record printing a stranger's serial in full 400px further up
 * the same page. Two opposite standards, and the live one is the worse case:
 * by the surface's own framing the box belongs to a seller the visitor has
 * just met.
 *
 * So masking happens here, at the boundary every report crosses, rather than
 * in any single finding's text. A rule added later cannot leak a serial by
 * wording its detail differently.
 *
 * The buyer loses nothing they need: the box is in their hand, and the sheet
 * tells them to read the serial off it for Apple's coverage lookup.
 */

/**
 * Apple serials observed on this hardware are 10 uppercase alphanumerics, or
 * 12 on AirPods Pro 1. Both a letter and a digit are required so ordinary
 * words, all-digit UPCs and model numbers (A2698) are left alone.
 */
const SERIAL = /\b(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,12}\b/g;

/** Keep enough for the buyer to match against the box, never the whole value. */
export function maskSerial(token: string): string {
  return token.slice(0, 4) + '•'.repeat(token.length - 6) + token.slice(-2);
}

export function redactSerials(text: string): string {
  return text.replace(SERIAL, maskSerial);
}

/** Walks a report and masks every string a reader could see. */
export function redactReport<T>(report: T): T {
  const walk = (value: unknown): unknown => {
    if (typeof value === 'string') return redactSerials(value);
    if (Array.isArray(value)) return value.map(walk);
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, walk(v)]),
      );
    }
    return value;
  };
  return walk(report) as T;
}
