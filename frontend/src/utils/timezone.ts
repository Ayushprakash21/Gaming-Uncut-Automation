/**
 * Centralized Timezone Standardization for Gaming Uncut Automation.
 *
 * Guarantees that all user-facing date and time displays are formatted
 * in Indian Standard Time (IST, Asia/Kolkata, UTC+05:30).
 *
 * Rules:
 * 1. Database & APIs transmit UTC ISO timestamps.
 * 2. Conversions are strictly timezone-aware via Intl.DateTimeFormat (timeZone: 'Asia/Kolkata').
 * 3. Never adds manual 5h 30m offsets.
 * 4. Standard UI format: 19 Nov 2026, 05:22 AM IST
 * 5. Compact table format: 19/11/2026, 05:22 AM IST
 * 6. Always displays the IST suffix.
 */

export const IST_TIMEZONE = 'Asia/Kolkata';

export interface DateFormatOptions {
  compact?: boolean;
  fallback?: string;
}

export interface TimeFormatOptions {
  includeSeconds?: boolean;
  fallback?: string;
}

/**
 * Safely parses any input timestamp string, number, or Date as UTC.
 * Prevents browser environments from accidentally misinterpreting naive UTC
 * strings as local device time.
 */
export function parseAsUTC(input: string | number | Date | null | undefined): Date | null {
  if (input === null || input === undefined) return null;
  if (input instanceof Date) {
    return isNaN(input.getTime()) ? null : input;
  }
  let str = String(input).trim();
  if (
    !str ||
    str.toLowerCase() === 'null' ||
    str.toLowerCase() === 'none' ||
    str.toLowerCase() === 'pending' ||
    str.toLowerCase() === 'n/a'
  ) {
    return null;
  }

  // Handle SQLite format "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DDTHH:MM:SS"
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(str)) {
    str = str.replace(' ', 'T');
  }

  // If no timezone offset is present, explicitly treat as UTC
  if (!/Z|[+-]\d{2}:?\d{2}$/i.test(str)) {
    str += 'Z';
  }

  const date = new Date(str);
  return isNaN(date.getTime()) ? null : date;
}

/**
 * Formats a timestamp into Indian Standard Time (IST).
 * Standard: 19 Nov 2026, 05:22 AM IST
 * Compact:  19/11/2026, 05:22 AM IST
 */
export function formatDateTimeIST(
  input: string | number | Date | null | undefined,
  options: DateFormatOptions = {}
): string {
  const date = parseAsUTC(input);
  if (!date) return options.fallback ?? 'N/A';

  if (options.compact) {
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: IST_TIMEZONE,
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
    return formatter.format(date).replace(/am|pm/i, (m) => m.toUpperCase()) + ' IST';
  }

  const formatter = new Intl.DateTimeFormat('en-GB', {
    timeZone: IST_TIMEZONE,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
  return formatter.format(date).replace(/am|pm/i, (m) => m.toUpperCase()) + ' IST';
}

/**
 * Formats only the date portion in Indian Standard Time (IST).
 * Standard: 19 Nov 2026 IST
 * Compact:  19/11/2026 IST
 */
export function formatDateIST(
  input: string | number | Date | null | undefined,
  options: DateFormatOptions = {}
): string {
  const date = parseAsUTC(input);
  if (!date) return options.fallback ?? 'N/A';

  if (options.compact) {
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: IST_TIMEZONE,
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
    return formatter.format(date) + ' IST';
  }

  const formatter = new Intl.DateTimeFormat('en-GB', {
    timeZone: IST_TIMEZONE,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
  return formatter.format(date) + ' IST';
}

/**
 * Formats only the time portion in Indian Standard Time (IST).
 * Standard: 05:22 AM IST
 * With Seconds: 05:22:15 AM IST
 */
export function formatTimeIST(
  input: string | number | Date | null | undefined,
  options: TimeFormatOptions = {}
): string {
  const date = parseAsUTC(input);
  if (!date) return options.fallback ?? 'N/A';

  const formatter = new Intl.DateTimeFormat('en-GB', {
    timeZone: IST_TIMEZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: options.includeSeconds ? '2-digit' : undefined,
    hour12: true,
  });
  return formatter.format(date).replace(/am|pm/i, (m) => m.toUpperCase()) + ' IST';
}
