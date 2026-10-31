// Settings screen tests: license before install, real progress, enable/disable/delete, the kill switch, language switch and Italian text.
import { Dialog } from '@angular/cdk/dialog';
import { LOCALE_ID } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { clearTranslations, loadTranslations } from '@angular/localize';
import { Subject, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import messagesIt from '../../../locale/messages.it.json';
import { GenerationModel, GenerationModelList } from '../../core/api.models';
import { LanguageService } from '../../core/i18n/language.service';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { SettingsComponent } from './settings.component';

const GB = 1024 * 1024 * 1024;

const LIGHT: GenerationModel = {
  id: 'gemma-4-e2b',
  label: 'Light',
  state: 'not_installed',
  downloadedBytes: 0,
  totalBytes: 3 * GB,
  files: [{ name: 'gemma-4-E2B-it-Q4_0.gguf', sizeBytes: 3 * GB }],
  repository: 'ggml-org/gemma-4-E2B-it-GGUF',
  revision: '6938c58e698502037401a5f19f6905f2fb2f7dee',
  licenseName: 'Apache License 2.0',
  licenseUrl: 'https://ai.google.dev/gemma/docs/gemma_4_license',
  errorMessage: null,
  updatedAt: null,
};

const QUALITY: GenerationModel = {
  ...LIGHT,
  id: 'gemma-4-e4b',
  label: 'Quality',
  totalBytes: 5 * GB,
  repository: 'ggml-org/gemma-4-E4B-it-GGUF',
};

/** A list answer with the given Light state and Quality not installed. */
function listWith(light: Partial<GenerationModel> = {}): GenerationModelList {
  return { localGeneration: 'available', items: [{ ...LIGHT, ...light }, QUALITY] };
}

describe('SettingsComponent', () => {
  /** Creates the screen with a controllable delete confirmation and a stand-in language service. */
  async function setup(api: Partial<Record<keyof ApiService, unknown>>, locale = 'en') {
    const confirmation = new Subject<boolean | undefined>();
    const openDialog = vi.fn(() => ({ closed: confirmation.asObservable() }));
    const switchTo = vi.fn(() => Promise.resolve(true));
    await TestBed.configureTestingModule({
      imports: [SettingsComponent],
      providers: [
        { provide: LOCALE_ID, useValue: locale },
        { provide: ApiService, useValue: { monitorModel: () => new Subject(), ...api } },
        { provide: LanguageService, useValue: { current: locale, switchTo } },
      ],
    })
      .overrideProvider(Dialog, { useValue: { open: openDialog } })
      .compileComponents();
    const fixture = TestBed.createComponent(SettingsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return {
      fixture,
      element: fixture.nativeElement as HTMLElement,
      confirmation,
      openDialog,
      switchTo,
    };
  }

  /** The button of the first model row whose visible text starts with `text`. */
  function button(element: HTMLElement, text: string, row = 0): HTMLButtonElement {
    const rows = element.querySelectorAll('.model-row');
    const found = Array.from(rows[row]!.querySelectorAll('button')).find((candidate) =>
      candidate.textContent!.trim().startsWith(text),
    );
    if (!found) throw new Error(`No button "${text}" in row ${row}`);
    return found;
  }

  afterEach(() => {
    clearTranslations();
    document.documentElement.lang = '';
    vi.useRealTimers();
  });

  it('lists each model with its state, size and license link', async () => {
    const { element } = await setup({ listModels: () => of(listWith()) });

    const rows = Array.from(element.querySelectorAll('.model-row'));
    expect(rows).toHaveLength(2);
    expect(rows[0]!.querySelector('h3')!.textContent!.trim()).toBe('Light');
    expect(rows[1]!.querySelector('h3')!.textContent!.trim()).toBe('Quality');
    expect(rows[0]!.querySelector('.ns-pill')!.textContent!.trim()).toBe('Not installed');
    expect(rows[0]!.querySelector('.model-facts')!.textContent).toContain('3.0 GB');
    expect(rows[1]!.querySelector('.model-facts')!.textContent).toContain('5.0 GB');
    const link = rows[0]!.querySelector('a')!;
    expect(link.textContent!.trim()).toBe('Apache License 2.0');
    expect(link.getAttribute('href')).toBe(LIGHT.licenseUrl);
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('shows the license name and link before any download and downloads only after acceptance', async () => {
    const downloadModel = vi.fn(() => of({ ...LIGHT, state: 'downloading' as const }));
    const { fixture, element } = await setup({
      listModels: () => of(listWith()),
      downloadModel,
    });

    button(element, 'Install').click();
    fixture.detectChanges();

    const panel = element.querySelector('.model-install')!;
    expect(panel.textContent).toContain('Apache License 2.0');
    expect(panel.querySelector('a')!.getAttribute('href')).toBe(LIGHT.licenseUrl);
    expect(panel.textContent).toContain(LIGHT.licenseUrl);
    expect(panel.textContent).toContain('3.0 GB');
    expect(panel.textContent).toContain('huggingface.co/ggml-org/gemma-4-E2B-it-GGUF');
    expect(downloadModel).not.toHaveBeenCalled();
    // The row shows the license once: in the panel while it is open.
    expect(element.querySelectorAll('.model-row')[0]!.querySelectorAll('a')).toHaveLength(1);

    button(element, 'Accept the license and download').click();
    fixture.detectChanges();

    expect(downloadModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('.model-install')).toBeNull();
    expect(element.querySelector('[role="status"]')!.textContent).toContain(
      'Download of the Light model started.',
    );
  });

  it('closes the license panel without any request when the user cancels', async () => {
    const downloadModel = vi.fn();
    const { fixture, element } = await setup({
      listModels: () => of(listWith()),
      downloadModel,
    });

    button(element, 'Install').click();
    fixture.detectChanges();
    button(element, 'Cancel').click();
    fixture.detectChanges();

    expect(downloadModel).not.toHaveBeenCalled();
    expect(element.querySelector('.model-install')).toBeNull();
    expect(button(element, 'Install')).toBeTruthy();
  });

  it('follows the real download progress until the model is installed', async () => {
    const progress = new Subject<GenerationModel>();
    const monitorModel = vi.fn(() => progress.asObservable());
    const { fixture, element } = await setup({
      listModels: () => of(listWith()),
      downloadModel: () => of({ ...LIGHT, state: 'downloading' as const }),
      monitorModel,
    });

    button(element, 'Install').click();
    fixture.detectChanges();
    button(element, 'Accept the license and download').click();
    fixture.detectChanges();
    expect(monitorModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');

    progress.next({ ...LIGHT, state: 'downloading', downloadedBytes: 1.5 * GB });
    fixture.detectChanges();
    const row = element.querySelector('.model-row')!;
    expect(row.querySelector('[role="img"]')!.getAttribute('aria-label')).toBe(
      'Download of the Light model, 50%',
    );
    expect(row.querySelector('.model-progress__text')!.textContent).toContain('1.5 GB of 3.0 GB');
    expect(row.querySelector('.ns-pill')!.textContent!.trim()).toBe('Downloading');
    expect(button(element, 'Cancel download')).toBeTruthy();

    progress.next({ ...LIGHT, state: 'verifying', downloadedBytes: 3 * GB });
    fixture.detectChanges();
    expect(row.querySelector('[role="progressbar"]')!.getAttribute('aria-label')).toBe(
      'Checking the downloaded files',
    );

    progress.next({ ...LIGHT, state: 'installed', downloadedBytes: 3 * GB });
    progress.complete();
    fixture.detectChanges();
    expect(row.querySelector('.ns-pill')!.textContent!.trim()).toBe('Installed');
    expect(button(element, 'Enable')).toBeTruthy();
    expect(button(element, 'Delete')).toBeTruthy();
  });

  it('resumes watching a download that was running when the screen opened', async () => {
    const monitorModel = vi.fn(() => new Subject<GenerationModel>());
    const { element } = await setup({
      listModels: () => of(listWith({ state: 'downloading', downloadedBytes: 1 * GB })),
      monitorModel,
    });

    expect(monitorModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('.model-row [role="img"]')!.getAttribute('aria-label')).toContain(
      '33%',
    );
  });

  it('stops polling when the screen is left', async () => {
    const progress = new Subject<GenerationModel>();
    const { fixture } = await setup({
      listModels: () => of(listWith({ state: 'downloading' })),
      monitorModel: () => progress.asObservable(),
    });
    expect(progress.observed).toBe(true);

    fixture.destroy();

    expect(progress.observed).toBe(false);
  });

  it('cancels a download and returns the model to not installed', async () => {
    const cancelModelDownload = vi.fn(() => of(LIGHT));
    const progress = new Subject<GenerationModel>();
    const { fixture, element } = await setup({
      listModels: () => of(listWith({ state: 'downloading', downloadedBytes: 1 * GB })),
      cancelModelDownload,
      monitorModel: () => progress.asObservable(),
    });

    button(element, 'Cancel download').click();
    fixture.detectChanges();

    expect(cancelModelDownload).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(progress.observed).toBe(false);
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Not installed');
    expect(element.querySelector('[role="status"]')!.textContent).toContain('cancelled');
  });

  it('enables and disables an installed model', async () => {
    const enableModel = vi.fn(() => of({ ...LIGHT, state: 'enabled' as const }));
    const disableModel = vi.fn(() => of({ ...LIGHT, state: 'installed' as const }));
    const { fixture, element } = await setup({
      listModels: () => of(listWith({ state: 'installed' })),
      enableModel,
      disableModel,
    });

    button(element, 'Enable').click();
    fixture.detectChanges();
    expect(enableModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Enabled');
    expect(element.querySelector('[role="status"]')!.textContent).toContain(
      'The Light model is enabled.',
    );

    button(element, 'Disable').click();
    fixture.detectChanges();
    expect(disableModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Installed');
  });

  it('deletes a model only after an explicit confirmation', async () => {
    const deleteModel = vi.fn(() => of(LIGHT));
    const { fixture, element, confirmation, openDialog } = await setup({
      listModels: () => of(listWith({ state: 'enabled' })),
      deleteModel,
    });

    button(element, 'Delete').click();
    fixture.detectChanges();

    expect(openDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        disableClose: true,
        data: expect.objectContaining({
          danger: true,
          title: 'Delete the Light model?',
          message: expect.stringContaining('frees 3.0 GB'),
        }),
      }),
    );
    expect(deleteModel).not.toHaveBeenCalled();

    confirmation.next(true);
    fixture.detectChanges();

    expect(deleteModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Not installed');
    expect(element.querySelector('[role="status"]')!.textContent).toContain('3.0 GB freed');
  });

  it('keeps the model when the deletion is cancelled', async () => {
    const deleteModel = vi.fn();
    const { fixture, element, confirmation } = await setup({
      listModels: () => of(listWith({ state: 'installed' })),
      deleteModel,
    });

    button(element, 'Delete').click();
    confirmation.next(false);
    fixture.detectChanges();

    expect(deleteModel).not.toHaveBeenCalled();
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Installed');
  });

  it('shows the refusal of the API and keeps the model', async () => {
    const refusal = new NarrastepApiError(
      'MODEL_IN_USE',
      'The model cannot be deleted while a generation runs.',
    );
    const { fixture, element, confirmation } = await setup({
      listModels: () => of(listWith({ state: 'installed' })),
      deleteModel: () => throwError(() => refusal),
    });

    button(element, 'Delete').click();
    confirmation.next(true);
    fixture.detectChanges();

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'The model cannot be deleted while a generation runs.',
    );
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe('Installed');
  });

  it('explains a failed download and offers to try again', async () => {
    const downloadModel = vi.fn(() => of({ ...LIGHT, state: 'downloading' as const }));
    const { fixture, element } = await setup({
      listModels: () =>
        of(
          listWith({
            state: 'failed',
            errorMessage: 'The connection ended after 1.0 GB of gemma-4-E2B-it-Q4_0.gguf.',
          }),
        ),
      downloadModel,
    });

    const row = element.querySelector('.model-row')!;
    expect(row.querySelector('.ns-notice--error')!.textContent).toContain(
      'The connection ended after 1.0 GB',
    );
    expect(row.querySelector('.ns-pill')!.textContent!.trim()).toBe('Failed');

    button(element, 'Try again').click();
    fixture.detectChanges();

    expect(downloadModel).toHaveBeenCalledExactlyOnceWith('gemma-4-e2b');
    expect(element.querySelector('[role="status"]')!.textContent).toContain('started again');
  });

  it('hides every model control when the kill switch is set', async () => {
    const { element } = await setup({
      listModels: () => of({ localGeneration: 'disabled', items: [LIGHT, QUALITY] }),
    });

    const window = element.querySelector('.generation-window')!;
    expect(window.querySelectorAll('button, a, input, select')).toHaveLength(0);
    expect(element.querySelector('.model-list')).toBeNull();
    expect(window.textContent).toContain('NARRASTEP_LOCAL_GENERATION=disabled');
  });

  it('shows a recoverable error when the models cannot be loaded', async () => {
    const listModels = vi
      .fn()
      .mockReturnValueOnce(throwError(() => new Error('offline')))
      .mockReturnValue(of(listWith()));
    const { fixture, element } = await setup({ listModels });

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'could not reach its local service',
    );
    fixture.componentInstance.load();
    fixture.detectChanges();

    expect(element.querySelector('[role="alert"]')).toBeNull();
    expect(element.querySelectorAll('.model-row')).toHaveLength(2);
  });

  it('offers the language switch and marks the current language', async () => {
    const { element, switchTo } = await setup({ listModels: () => of(listWith()) });

    const options = Array.from(
      element.querySelectorAll<HTMLButtonElement>('.language-options button'),
    );
    expect(options.map((option) => option.textContent!.trim())).toEqual(['English', 'Italiano']);
    expect(options.map((option) => option.getAttribute('aria-current'))).toEqual(['true', null]);
    expect(options.map((option) => option.getAttribute('lang'))).toEqual(['en', 'it']);

    options[1]!.click();

    expect(switchTo).toHaveBeenCalledExactlyOnceWith('it');
  });

  it('reads Italian with the Italian catalog', async () => {
    loadTranslations(messagesIt.translations);
    document.documentElement.lang = 'it';
    const { fixture, element, openDialog } = await setup(
      { listModels: () => of(listWith()) },
      'it',
    );

    expect(element.querySelector('h1')!.textContent).toBe('Impostazioni');
    expect(element.querySelector('.model-row h3')!.textContent!.trim()).toBe('Leggero');
    expect(element.querySelector('.model-row .ns-pill')!.textContent!.trim()).toBe(
      'Non installato',
    );
    expect(element.querySelector('.language-options')!.getAttribute('aria-label')).toBe(
      "Lingua dell'interfaccia",
    );

    button(element, 'Installa').click();
    fixture.detectChanges();
    expect(element.querySelector('.model-install')!.textContent).toContain('Licenza');
    expect(button(element, 'Accetta la licenza e scarica')).toBeTruthy();
    button(element, 'Annulla').click();
    fixture.componentInstance.confirmDelete(LIGHT);
    expect(openDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        data: expect.objectContaining({
          title: 'Eliminare il modello Leggero?',
          message: expect.stringContaining('libera 3,0 GB'),
        }),
      }),
    );
  });

  it('shows the generic failure sentence outside English', async () => {
    loadTranslations(messagesIt.translations);
    document.documentElement.lang = 'it';
    const { element } = await setup(
      {
        listModels: () =>
          of(listWith({ state: 'failed', errorMessage: 'The connection ended early.' })),
      },
      'it',
    );

    const failure = element.querySelector('.model-row .ns-notice--error')!.textContent!;
    expect(failure).toContain('Il download non è terminato');
    expect(failure).not.toContain('The connection ended early.');
  });
});
