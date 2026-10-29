// Language tests: first-visit detection and the switch that stores the choice and reloads.
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LANGUAGE_STORAGE_KEY, detectLanguage } from './language';
import { LanguageService, RELOAD_PAGE } from './language.service';

function storageWith(value: string | null): Pick<Storage, 'getItem'> {
  return { getItem: () => value };
}

describe('detectLanguage', () => {
  it('uses the stored choice before the browser language', () => {
    expect(detectLanguage(storageWith('en'), ['it-IT'])).toBe('en');
    expect(detectLanguage(storageWith('it'), ['en-US'])).toBe('it');
  });

  it('starts in Italian only for an Italian browser, otherwise in English', () => {
    expect(detectLanguage(storageWith(null), ['it'])).toBe('it');
    expect(detectLanguage(storageWith(null), ['it-CH', 'en'])).toBe('it');
    expect(detectLanguage(storageWith(null), ['en-GB', 'it'])).toBe('en');
    expect(detectLanguage(storageWith(null), ['fr-FR'])).toBe('en');
    expect(detectLanguage(storageWith(null), [])).toBe('en');
  });

  it('ignores an unknown stored value and blocked storage', () => {
    expect(detectLanguage(storageWith('de'), ['it'])).toBe('it');
    expect(detectLanguage(null, ['it'])).toBe('it');
    const blocked = {
      getItem: () => {
        throw new Error('blocked');
      },
    };
    expect(detectLanguage(blocked, ['it'])).toBe('it');
  });
});

describe('LanguageService', () => {
  const reload = vi.fn();

  beforeEach(() => {
    reload.mockClear();
    globalThis.localStorage.clear();
    TestBed.configureTestingModule({ providers: [{ provide: RELOAD_PAGE, useValue: reload }] });
  });

  afterEach(() => globalThis.localStorage.clear());

  it('stores the new language and reloads the page', async () => {
    const service = TestBed.inject(LanguageService);
    expect(service.current).toBe('en');
    expect(await service.switchTo('it')).toBe(true);
    expect(globalThis.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('it');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does nothing when the language is already active', async () => {
    const service = TestBed.inject(LanguageService);
    expect(await service.switchTo('en')).toBe(false);
    expect(globalThis.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();
    expect(reload).not.toHaveBeenCalled();
  });

  it('keeps the page and the choice when the unsaved-changes check refuses', async () => {
    const service = TestBed.inject(LanguageService);
    service.registerLeaveCheck(() => of(false));
    expect(await service.switchTo('it')).toBe(false);
    expect(globalThis.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();
    expect(reload).not.toHaveBeenCalled();
  });

  it('reloads after the check allows it and ignores a check that was removed', async () => {
    const service = TestBed.inject(LanguageService);
    const refuse = vi.fn(() => false);
    const allow = vi.fn(() => of(true));
    const unregister = service.registerLeaveCheck(refuse);
    service.registerLeaveCheck(allow);
    unregister();
    expect(await service.switchTo('it')).toBe(true);
    expect(refuse).not.toHaveBeenCalled();
    expect(allow).toHaveBeenCalledTimes(1);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
