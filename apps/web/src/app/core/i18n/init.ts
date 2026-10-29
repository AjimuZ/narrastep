// Side-effect module imported first by main.ts: applies the active language's catalog before any $localize call runs.
// The $localize runtime itself is a build polyfill (angular.json), so it exists before this module runs.
import { registerLocaleData } from '@angular/common';
import localeIt from '@angular/common/locales/it';
import { loadTranslations } from '@angular/localize';

import messagesEn from '../../../locale/messages.en.json';
import messagesIt from '../../../locale/messages.it.json';
import { Language, detectLanguage } from './language';

const CATALOGS: Record<Language, { translations: Record<string, string> }> = {
  en: messagesEn,
  it: messagesIt,
};

/** Language chosen for this page load; it only changes through a reload. */
export const ACTIVE_LANGUAGE: Language = detectLanguage(
  globalThis.localStorage,
  globalThis.navigator?.languages ?? [],
);

registerLocaleData(localeIt);
loadTranslations(CATALOGS[ACTIVE_LANGUAGE].translations);
if (globalThis.document) {
  globalThis.document.documentElement.lang = ACTIVE_LANGUAGE;
}
