// Spoken-language suggestions shared by the new-project form and the Process dialog.
import { languageName } from '../core/i18n/format';
import { activeLocale } from '../core/i18n/language';

export interface LanguageOption {
  code: string;
  label: string;
}

/** Whisper codes offered as suggestions next to free entry of any supported code. */
const SUGGESTED_CODES = [
  'ar',
  'zh',
  'nl',
  'en',
  'fr',
  'de',
  'hi',
  'it',
  'ja',
  'ko',
  'pl',
  'pt',
  'ru',
  'es',
  'sv',
  'tr',
  'uk',
];

/**
 * Spoken-language suggestions: "Detect automatically" first, then the common languages sorted by
 * their name in the active language (names come from `Intl.DisplayNames`).
 */
export function languageSuggestions(): readonly LanguageOption[] {
  return [
    { code: 'auto', label: $localize`:@@new_project.language_auto:Detect automatically` },
    ...SUGGESTED_CODES.map((code) => ({ code, label: languageName(code) })).sort((left, right) =>
      left.label.localeCompare(right.label, activeLocale()),
    ),
  ];
}
