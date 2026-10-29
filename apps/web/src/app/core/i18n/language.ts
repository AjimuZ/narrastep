// Supported interface languages, their stored choice and first-visit detection (ADR 0015).

/** Interface languages; the API and the source code stay English. */
export type Language = 'en' | 'it';

/** Languages in the order the switch shows them, with the short text on its buttons. */
export const LANGUAGES: readonly { code: Language; short: string; name: string }[] = [
  { code: 'en', short: 'EN', name: 'English' },
  { code: 'it', short: 'IT', name: 'Italiano' },
];

/** Local-storage key of the chosen language. */
export const LANGUAGE_STORAGE_KEY = 'narrastep.language';

/** Narrows a stored or detected value to a supported language. */
export function isLanguage(value: unknown): value is Language {
  return LANGUAGES.some((language) => language.code === value);
}

/**
 * Picks the language to start in: the stored choice, else Italian when the browser prefers
 * Italian, else English. Storage that is missing, blocked or holds an unknown value is ignored.
 */
export function detectLanguage(
  storage: Pick<Storage, 'getItem'> | null | undefined,
  browserLanguages: readonly string[],
): Language {
  let stored: unknown = null;
  try {
    stored = storage?.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    // Blocked storage behaves like an empty one.
  }
  if (isLanguage(stored)) {
    return stored;
  }
  const first = browserLanguages[0]?.toLowerCase() ?? '';
  return first === 'it' || first.startsWith('it-') ? 'it' : 'en';
}

/** Locale of the running page (`<html lang>`, set at startup from the active language); English before that. */
export function activeLocale(): string {
  return globalThis.document?.documentElement.lang || 'en';
}
