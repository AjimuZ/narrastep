// Language switch: stores the choice and reloads, after the unsaved-review-changes confirmation.
import { DOCUMENT } from '@angular/common';
import { Injectable, InjectionToken, LOCALE_ID, inject } from '@angular/core';
import { Observable, firstValueFrom, isObservable } from 'rxjs';

import { LANGUAGE_STORAGE_KEY, Language } from './language';

/** Reloads the page; replaced in tests so a switch does not navigate the test runner. */
export const RELOAD_PAGE = new InjectionToken<() => void>('RELOAD_PAGE', {
  providedIn: 'root',
  factory: () => {
    const document = inject(DOCUMENT);
    return () => document.defaultView?.location.reload();
  },
});

/** Answers whether leaving the current screen is allowed (true), possibly after asking the user. */
export type LeaveCheck = () => boolean | Observable<boolean>;

@Injectable({ providedIn: 'root' })
export class LanguageService {
  private readonly reload = inject(RELOAD_PAGE);
  private readonly checks = new Set<LeaveCheck>();

  /** Language this page was started in (the app's `LOCALE_ID`); it changes only through a reload. */
  readonly current: Language = inject(LOCALE_ID).toLowerCase().startsWith('it') ? 'it' : 'en';

  /**
   * Registers a screen's unsaved-changes check, run before a language switch reloads the page.
   * Returns the function that removes it; call it when the screen is destroyed.
   */
  registerLeaveCheck(check: LeaveCheck): () => void {
    this.checks.add(check);
    return () => this.checks.delete(check);
  }

  /**
   * Saves the language and reloads. Returns false and changes nothing when a registered check
   * refuses (the user kept their unsaved edits) or the language is already active.
   */
  async switchTo(language: Language): Promise<boolean> {
    if (language === this.current) {
      return false;
    }
    for (const check of this.checks) {
      const result = check();
      const allowed = isObservable(result) ? await firstValueFrom(result) : result;
      if (!allowed) {
        return false;
      }
    }
    try {
      globalThis.localStorage?.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch {
      // Blocked storage: the reload then starts in the browser language.
    }
    this.reload();
    return true;
  }
}
