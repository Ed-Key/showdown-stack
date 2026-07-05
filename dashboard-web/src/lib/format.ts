export function fmtPct(value: number | null | undefined): string {
  if (value == null) return 'n/a';
  return `${Math.round(value * 10) / 10}%`;
}

export function fmtNumber(value: number | null | undefined): string {
  if (value == null) return '0';
  return new Intl.NumberFormat('en-US').format(value);
}

export function labelOrFallback(value: string | null | undefined, fallback: string): string {
  const trimmed = String(value || '').trim();
  return trimmed || fallback;
}
