// Locale-aware numbers, sizes and spoken-language names for the active interface language.
import { activeLocale } from './language';

/** Formats a number with a fixed count of decimals using the active locale's separators. */
export function formatDecimal(value: number, digits: number): string {
  return new Intl.NumberFormat(activeLocale(), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** File size in KB below one megabyte, else MB, with one decimal; the unit symbols are international. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${formatDecimal(bytes / 1024, 1)} KB`;
  }
  return `${formatDecimal(bytes / (1024 * 1024), 1)} MB`;
}

/** Name of a spoken language in the active interface language; the code itself when unknown. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames([activeLocale()], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
}
