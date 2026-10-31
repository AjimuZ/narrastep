// Italian rendering tests: with the Italian catalog loaded, labels, error texts and screens read Italian.
import { LOCALE_ID } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { clearTranslations, loadTranslations } from '@angular/localize';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import messagesIt from '../../../locale/messages.it.json';
import { AppComponent } from '../../app.component';
import { projectStatusLabel } from '../../shared/status-labels';
import { projectTypes } from '../../shared/project-types';
import { ApiService } from '../services/api.service';
import { NarrastepApiError } from '../services/api-error.interceptor';
import { errorText, localizedError } from './error-text';
import { formatFileSize } from './format';
import { RELOAD_PAGE } from './language.service';

describe('Italian interface', () => {
  const reload = vi.fn();

  beforeAll(() => {
    loadTranslations(messagesIt.translations);
    document.documentElement.lang = 'it';
  });

  afterAll(() => {
    clearTranslations();
    document.documentElement.lang = '';
    globalThis.localStorage.clear();
  });

  it('translates state words and project types', () => {
    expect(projectStatusLabel('review').label).toBe('Pronto per la revisione');
    expect(projectStatusLabel('completed').label).toBe('Pacchetto pronto');
    expect(projectTypes().map((type) => type.label)).toEqual([
      'Spiegazione di un compito',
      'Riunione',
      'Tutorial',
      'Generico',
    ]);
  });

  it('translates API error codes and keeps the API message for unknown codes', () => {
    const busy = new NarrastepApiError('PROJECT_BUSY', 'Wait for the active project operation.');
    expect(errorText(busy, 'fallback')).toContain('Attendi');
    const unknown = new NarrastepApiError('SOMETHING_NEW', 'Something new happened.');
    expect(errorText(unknown, 'fallback')).toBe('Something new happened.');
    expect(errorText(new Error('boom'), 'Riprova.')).toBe('Riprova.');
    expect(localizedError('OCR_FAILED', null, 'x')).toContain('riconoscimento');
    expect(localizedError(null, null, 'Fallback')).toBe('Fallback');
  });

  it('formats sizes with the Italian decimal comma', () => {
    expect(formatFileSize(1.5 * 1024 * 1024)).toBe('1,5 MB');
    expect(formatFileSize(2.5 * 1024 * 1024 * 1024)).toBe('2,5 GB');
  });

  it('translates the storage errors', () => {
    expect(localizedError('PACKAGE_REQUIRED', null, 'x')).toContain('pacchetto');
    expect(localizedError('SOURCE_VIDEO_REMOVED', null, 'x')).toContain('rimosso');
  });

  it('translates the model download errors', () => {
    expect(localizedError('LOCAL_GENERATION_DISABLED', null, 'x')).toContain('disattivata');
    expect(localizedError('MODEL_NOT_FOUND', null, 'x')).toContain('modello');
    expect(localizedError('MODEL_STATE_CONFLICT', null, 'x')).toContain('Ricarica');
  });

  it('translates the model import errors', () => {
    expect(localizedError('LICENSE_NOT_ACCEPTED', null, 'x')).toContain('licenza');
    expect(localizedError('MODEL_IMPORT_FILES_MISSING', null, 'x')).toContain('cartella');
    expect(localizedError('MODEL_IMPORT_NOT_PINNED', null, 'x')).toContain('non è stato importato');
  });

  it('translates the generation out-of-memory error', () => {
    expect(localizedError('GENERATION_OUT_OF_MEMORY', null, 'x')).toContain('memoria libera');
  });

  it('renders the shell, the language switch and the health word in Italian', async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [
        provideRouter([]),
        { provide: LOCALE_ID, useValue: 'it' },
        { provide: RELOAD_PAGE, useValue: reload },
        {
          provide: ApiService,
          useValue: {
            health: () =>
              of({ status: 'ok', version: '0', database: 'ok', redis: 'ok', worker: 'ok' }),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;

    const nav = element.querySelector('nav') as HTMLElement;
    expect(nav.getAttribute('aria-label')).toBe('Menu principale');
    expect(Array.from(nav.querySelectorAll('a')).map((a) => a.textContent?.trim())).toEqual([
      'Progetti',
      'Registra',
      'Nuovo progetto',
      'Spazio',
    ]);
    expect(element.querySelector('.skip-link')?.textContent?.trim()).toBe(
      'Vai al contenuto principale',
    );
    expect(element.querySelector('.ns-pill')?.textContent?.trim()).toBe('Servizi pronti');

    const switchButtons = Array.from(
      element.querySelectorAll<HTMLButtonElement>('.shell-language button'),
    );
    expect(switchButtons.map((button) => button.textContent?.trim())).toEqual(['EN', 'IT']);
    expect(switchButtons.map((button) => button.getAttribute('aria-current'))).toEqual([
      null,
      'true',
    ]);
    switchButtons[0]!.click();
    await fixture.whenStable();
    expect(globalThis.localStorage.getItem('narrastep.language')).toBe('en');
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
