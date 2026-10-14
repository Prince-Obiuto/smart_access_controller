/** Date-only administrative fields use Lagos and retain existing time precision. */
export function lagosDate(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Lagos", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function administrativeDate(value: string, existing?: string): string | undefined {
  if (!value) return undefined;
  if (existing && lagosDate(existing) === value) return existing;
  return value + "T00:00:00+01:00";
}

export function lagosDateTime(value?: string): string {
  const date = lagosDate(value);
  if (!date || !value) return "Not set";
  const time = new Date(value).toLocaleTimeString("en-GB", {
    timeZone: "Africa/Lagos", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  return `${date} ${time} Lagos`;
}
