// Storage screen tests: figures, the guarded removal with its confirmation, refusals and Italian text.
import { Dialog } from '@angular/cdk/dialog';
import { LOCALE_ID } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { clearTranslations, loadTranslations } from '@angular/localize';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import messagesIt from '../../../locale/messages.it.json';
import { ProjectStorage, StorageSummary } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { StorageComponent } from './storage.component';

const MB = 1024 * 1024;

const READY: ProjectStorage = {
  projectId: 'project-1',
  name: 'Claims workflow',
  status: 'completed',
  totalBytes: 520 * MB,
  categories: {
    source: 500 * MB,
    audio: 10 * MB,
    transcript: 0,
    screenshots: 6 * MB,
    exports: 4 * MB,
    other: 0,
  },
  sourceVideoBytes: 500 * MB,
  sourceRemovedAt: null,
  removalBlockedReason: null,
};

const NEEDS_PACKAGE: ProjectStorage = {
  ...READY,
  projectId: 'project-2',
  name: 'Weekly meeting',
  status: 'review',
  totalBytes: 100 * MB,
  categories: { ...READY.categories, source: 100 * MB, audio: 0, screenshots: 0, exports: 0 },
  removalBlockedReason: 'package_required',
};

const SUMMARY: StorageSummary = {
  totalBytes: 620 * MB,
  freeBytes: 20 * 1024 * MB,
  items: [READY, NEEDS_PACKAGE],
};

describe('StorageComponent', () => {
  /** Creates the screen with a controllable confirmation result. */
  async function setup(api: Partial<Record<keyof ApiService, unknown>>, locale = 'en') {
    const confirmation = new Subject<boolean | undefined>();
    const openDialog = vi.fn(() => ({ closed: confirmation.asObservable() }));
    await TestBed.configureTestingModule({
      imports: [StorageComponent],
      providers: [
        provideRouter([]),
        { provide: LOCALE_ID, useValue: locale },
        { provide: ApiService, useValue: api },
      ],
    })
      .overrideProvider(Dialog, { useValue: { open: openDialog } })
      .compileComponents();
    const fixture = TestBed.createComponent(StorageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, element: fixture.nativeElement as HTMLElement, confirmation, openDialog };
  }

  afterEach(() => {
    clearTranslations();
    document.documentElement.lang = '';
  });

  it('lists the space of each project, the totals and the free space', async () => {
    const { element } = await setup({ getStorage: () => of(SUMMARY) });

    expect(element.querySelector('.totals')!.textContent).toContain('620.0 MB');
    expect(element.querySelector('.totals')!.textContent).toContain('20.0 GB');
    const rows = Array.from(element.querySelectorAll('.storage-row'));
    expect(rows).toHaveLength(2);
    expect(rows[0]!.querySelector('h3')!.textContent).toBe('Claims workflow');
    expect(rows[0]!.querySelector('.storage-total')!.textContent!.trim()).toBe('520.0 MB');
    // Empty parts are not listed, so a row shows only what the project really holds.
    const shares = rows[0]!.querySelector('.storage-shares')!.textContent!;
    expect(shares).toContain('Original video');
    expect(shares).toContain('500.0 MB');
    expect(shares).not.toContain('Transcript');
  });

  it('offers removal only when allowed and explains why otherwise', async () => {
    const { element } = await setup({ getStorage: () => of(SUMMARY) });
    const rows = Array.from(element.querySelectorAll('.storage-row'));

    expect(rows[0]!.querySelector('button.ns-button--danger')!.textContent).toContain(
      'Remove original video',
    );
    expect(rows[1]!.querySelector('button')).toBeNull();
    expect(rows[1]!.querySelector('.storage-reason')!.textContent).toContain(
      'Create a package of the current review first.',
    );
    expect(rows[1]!.querySelector('a')!.getAttribute('href')).toBe('/projects/project-2/export');
  });

  it('removes the video only after an explicit confirmation and updates the figures', async () => {
    const removal = new Subject<ProjectStorage>();
    const removeSourceVideo = vi.fn(() => removal.asObservable());
    const { fixture, element, confirmation, openDialog } = await setup({
      getStorage: () => of(SUMMARY),
      removeSourceVideo,
    });

    fixture.componentInstance.confirmRemoval(READY);

    expect(openDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        disableClose: true,
        data: expect.objectContaining({
          danger: true,
          title: 'Remove the original video of “Claims workflow”?',
          message: expect.stringContaining('This frees 500.0 MB'),
        }),
      }),
    );
    expect(removeSourceVideo).not.toHaveBeenCalled();

    confirmation.next(true);
    fixture.detectChanges();
    expect(removeSourceVideo).toHaveBeenCalledWith('project-1');
    expect(element.querySelector('button.ns-button--danger')!.hasAttribute('disabled')).toBe(true);

    removal.next({
      ...READY,
      totalBytes: 20 * MB,
      categories: { ...READY.categories, source: 0 },
      sourceVideoBytes: 0,
      sourceRemovedAt: '2026-10-29T12:00:00Z',
      removalBlockedReason: 'already_removed',
    });
    removal.complete();
    fixture.detectChanges();

    expect(element.querySelector('.totals')!.textContent).toContain('120.0 MB');
    expect(element.querySelector('.storage-total')!.textContent!.trim()).toBe('20.0 MB');
    expect(element.querySelector('.storage-reason')!.textContent).toContain(
      'The original video was removed.',
    );
    expect(element.querySelector('[role="status"]')!.textContent).toContain('500.0 MB freed');
  });

  it('keeps everything when the confirmation is cancelled', async () => {
    const removeSourceVideo = vi.fn();
    const { fixture, element, confirmation } = await setup({
      getStorage: () => of(SUMMARY),
      removeSourceVideo,
    });

    fixture.componentInstance.confirmRemoval(READY);
    confirmation.next(false);
    fixture.detectChanges();

    expect(removeSourceVideo).not.toHaveBeenCalled();
    expect(element.querySelector('.storage-total')!.textContent!.trim()).toBe('520.0 MB');
  });

  it('shows the refusal of the API and keeps the row', async () => {
    const refusal = new NarrastepApiError(
      'PACKAGE_REQUIRED',
      'Create a package of the current review before removing the original video.',
    );
    const { fixture, element, confirmation } = await setup({
      getStorage: () => of(SUMMARY),
      removeSourceVideo: () => throwError(() => refusal),
    });

    fixture.componentInstance.confirmRemoval(READY);
    confirmation.next(true);
    fixture.detectChanges();

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'Create a package of the current review before removing the original video.',
    );
    expect(element.querySelector('.storage-total')!.textContent!.trim()).toBe('520.0 MB');
  });

  it('shows a recoverable error when the figures cannot be loaded', async () => {
    const getStorage = vi
      .fn()
      .mockReturnValueOnce(throwError(() => new Error('offline')))
      .mockReturnValue(of(SUMMARY));
    const { fixture, element } = await setup({ getStorage });

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'could not reach its local service',
    );
    fixture.componentInstance.load();
    fixture.detectChanges();

    expect(element.querySelector('[role="alert"]')).toBeNull();
    expect(element.querySelectorAll('.storage-row')).toHaveLength(2);
  });

  it('says so when there are no projects', async () => {
    const { element } = await setup({
      getStorage: () => of({ totalBytes: 0, freeBytes: null, items: [] }),
    });

    expect(element.textContent).toContain('Projects you create will show their size here.');
    expect(element.querySelector('.totals')!.textContent).toContain('Unknown');
  });

  it('reads Italian with the Italian catalog', async () => {
    loadTranslations(messagesIt.translations);
    // Number formatting follows the document language, as set at startup.
    document.documentElement.lang = 'it';
    const { fixture, element } = await setup({ getStorage: () => of(SUMMARY) }, 'it');

    expect(element.querySelector('h1')!.textContent).toBe('Spazio');
    expect(element.querySelector('button.ns-button--danger')!.textContent).toContain(
      'Rimuovi video originale',
    );
    expect(element.querySelector('.storage-reason')!.textContent).toContain(
      'Crea prima un pacchetto della revisione attuale.',
    );
    expect(element.querySelector('.storage-total')!.textContent!.trim()).toBe('520,0 MB');

    const openDialog = TestBed.inject(Dialog).open as unknown as ReturnType<typeof vi.fn>;
    fixture.componentInstance.confirmRemoval(READY);
    expect(openDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        data: expect.objectContaining({
          title: 'Rimuovere il video originale di “Claims workflow”?',
          confirmLabel: 'Rimuovi video originale',
          message: expect.stringContaining('Libera 500,0 MB'),
        }),
      }),
    );
  });
});
