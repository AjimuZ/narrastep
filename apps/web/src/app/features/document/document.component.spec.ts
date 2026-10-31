// Document screen tests: generate, per-part progress, result beside the video, regenerate, cancel, failures and Italian text.
import { registerLocaleData } from '@angular/common';
import localeIt from '@angular/common/locales/it';
import { LOCALE_ID } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { clearTranslations, loadTranslations } from '@angular/localize';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import messagesIt from '../../../locale/messages.it.json';
import { DocumentGeneration, Project } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { DocumentComponent } from './document.component';

const PROJECT = {
  id: 'project-1',
  name: 'Contract search',
  videoAsset: { id: 'asset-1', sourceRemovedAt: null },
} as unknown as Project;

const QUEUED: DocumentGeneration = {
  id: 'generation-2',
  projectId: 'project-1',
  modelId: 'gemma-4-e2b',
  status: 'queued',
  currentStage: 'queued',
  progressPercent: 0,
  message: 'Waiting for the local worker.',
  partsTotal: 0,
  partsCompleted: 0,
  language: null,
  promptVersion: 'generation-prompts-v1',
  documentAvailable: false,
  sizeBytes: null,
  errorCode: null,
  errorMessage: null,
  version: 1,
  createdAt: '2026-07-15T10:00:00Z',
  startedAt: null,
  completedAt: null,
};

const COMPLETED: DocumentGeneration = {
  ...QUEUED,
  id: 'generation-1',
  status: 'completed',
  currentStage: 'completed',
  progressPercent: 100,
  partsTotal: 3,
  partsCompleted: 3,
  documentAvailable: true,
  sizeBytes: 300,
  completedAt: '2026-07-15T10:05:00Z',
};

const MARKDOWN = `<!--
narrastep generated document
promptVersion: generation-prompts-v1
-->

# Contract search

**1. Summary**

The customer opens **Search** [00:02].

1. Select Search.
2. Open the record.

- Not stated in the recording.
`;

describe('DocumentComponent', () => {
  /** Creates the screen with the given stored generations and API stand-ins. */
  async function setup(
    generations: DocumentGeneration[],
    api: Partial<Record<keyof ApiService, unknown>> = {},
    project: Project = PROJECT,
    locale = 'en',
  ) {
    await TestBed.configureTestingModule({
      imports: [DocumentComponent],
      providers: [
        provideRouter([]),
        { provide: LOCALE_ID, useValue: locale },
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(project),
            listGenerations: () => of(generations),
            getGenerationDocument: () => of(MARKDOWN),
            monitorGeneration: () => new Subject<DocumentGeneration>(),
            videoUrl: (id: string) => `/api/v1/projects/${id}/video`,
            ...api,
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(DocumentComponent);
    fixture.componentRef.setInput('projectId', 'project-1');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, element: fixture.nativeElement as HTMLElement };
  }

  /** The button whose visible text starts with `text`. */
  function button(element: HTMLElement, text: string): HTMLButtonElement {
    const found = Array.from(element.querySelectorAll('button')).find((candidate) =>
      candidate.textContent!.trim().startsWith(text),
    );
    if (!found) throw new Error(`No button "${text}"`);
    return found;
  }

  afterEach(() => {
    clearTranslations();
    document.documentElement.lang = '';
  });

  it('offers Generate document when nothing was generated yet and shows no result', async () => {
    const { element } = await setup([]);

    expect(element.querySelector('h1')!.textContent).toBe('Document');
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe(
      'Not generated',
    );
    expect(button(element, 'Generate document').disabled).toBe(false);
    expect(element.querySelector('.document-text')).toBeNull();
    expect(element.querySelector('video')).toBeNull();
  });

  it('does not offer copying or the package, which do not exist yet', async () => {
    const { element } = await setup([COMPLETED]);

    const names = Array.from(element.querySelectorAll('button, a')).map((control) =>
      control.textContent!.trim(),
    );
    expect(names).toEqual(['Back to review', 'Regenerate']);
  });

  it('starts a generation and follows the real progress per part until the document is ready', async () => {
    const progress = new Subject<DocumentGeneration>();
    const createGeneration = vi.fn(() => of(QUEUED));
    const monitorGeneration = vi.fn(() => progress.asObservable());
    const getGenerationDocument = vi.fn(() => of(MARKDOWN));
    const { fixture, element } = await setup([], {
      createGeneration,
      monitorGeneration,
      getGenerationDocument,
    });

    button(element, 'Generate document').click();
    fixture.detectChanges();

    expect(createGeneration).toHaveBeenCalledExactlyOnceWith('project-1');
    expect(monitorGeneration).toHaveBeenCalledExactlyOnceWith('generation-2');
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe(
      'Waiting to start',
    );
    expect(element.querySelector('.progress [role="progressbar"]')).not.toBeNull();
    expect(button(element, 'Cancel generation')).toBeTruthy();
    expect(element.querySelector('[role="status"]')!.textContent).toContain(
      'The document is being generated.',
    );

    progress.next({
      ...QUEUED,
      status: 'processing',
      currentStage: 'generating',
      progressPercent: 33,
      partsTotal: 3,
      partsCompleted: 1,
    });
    fixture.detectChanges();

    expect(element.querySelector('.progress__parts')!.textContent).toContain(
      'Parts written: 1 of 3',
    );
    expect(element.querySelector('.progress [role="img"]')!.getAttribute('aria-label')).toBe(
      'Document generation, 33%',
    );
    expect(element.querySelector('.progress__stage')!.textContent).toContain(
      'Writing the document',
    );
    expect(getGenerationDocument).not.toHaveBeenCalled();

    progress.next({ ...COMPLETED, id: 'generation-2' });
    progress.complete();
    fixture.detectChanges();

    expect(getGenerationDocument).toHaveBeenCalledExactlyOnceWith('generation-2');
    expect(element.querySelector('.document-text')).not.toBeNull();
    expect(button(element, 'Regenerate')).toBeTruthy();
  });

  it('shows the result with the video beside it', async () => {
    const { element } = await setup([COMPLETED]);

    const article = element.querySelector('.document-text')!;
    expect(article.querySelector('h3')!.textContent!.trim()).toBe('Contract search');
    expect(article.querySelectorAll('h3')[1]!.textContent!.trim()).toBe('1. Summary');
    expect(article.querySelector('strong')!.textContent).toBe('Search');
    expect(article.querySelector('p')!.textContent).toContain('The customer opens Search [00:02].');
    expect(article.querySelectorAll('ol li')).toHaveLength(2);
    expect(article.querySelectorAll('ul li')).toHaveLength(1);
    // The leading comment with the prompt version is not shown.
    expect(article.textContent).not.toContain('promptVersion');
    const video = element.querySelector('.video-window video')!;
    expect(video.getAttribute('src')).toBe('/api/v1/projects/project-1/video');
    expect(element.querySelector('.document-window')).not.toBeNull();
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe('Completed');
  });

  it('replaces the player with a notice once the original video was removed', async () => {
    const removed = {
      ...PROJECT,
      videoAsset: { id: 'asset-1', sourceRemovedAt: '2026-07-16T10:00:00Z' },
    } as unknown as Project;
    const { element } = await setup([COMPLETED], {}, removed);

    expect(element.querySelector('video')).toBeNull();
    expect(element.querySelector('.video-window')!.textContent).toContain(
      'The original video was removed from this computer',
    );
  });

  it('regenerates while the current document stays visible', async () => {
    const progress = new Subject<DocumentGeneration>();
    const createGeneration = vi.fn(() => of(QUEUED));
    const { fixture, element } = await setup([COMPLETED], {
      createGeneration,
      monitorGeneration: () => progress.asObservable(),
    });

    button(element, 'Regenerate').click();
    fixture.detectChanges();

    expect(createGeneration).toHaveBeenCalledExactlyOnceWith('project-1');
    expect(element.querySelector('.document-text')).not.toBeNull();
    expect(element.querySelector('.progress')).not.toBeNull();
    expect(() => button(element, 'Regenerate')).toThrow();
    expect(button(element, 'Cancel generation')).toBeTruthy();
  });

  it('resumes following a generation that was running when the screen opened', async () => {
    const monitorGeneration = vi.fn(() => new Subject<DocumentGeneration>());
    const running = {
      ...QUEUED,
      status: 'processing' as const,
      currentStage: 'generating',
      progressPercent: 33,
      partsTotal: 3,
      partsCompleted: 1,
    };
    const { element } = await setup([running], { monitorGeneration });

    expect(monitorGeneration).toHaveBeenCalledExactlyOnceWith('generation-2');
    expect(element.querySelector('.progress__parts')!.textContent).toContain(
      'Parts written: 1 of 3',
    );
  });

  it('stops polling when the screen is left', async () => {
    const progress = new Subject<DocumentGeneration>();
    const { fixture } = await setup([QUEUED], { monitorGeneration: () => progress.asObservable() });
    expect(progress.observed).toBe(true);

    fixture.destroy();

    expect(progress.observed).toBe(false);
  });

  it('cancels the running generation and disables the button while it stops', async () => {
    const cancelGeneration = vi.fn(() => of({ ...QUEUED, status: 'cancelling' as const }));
    const progress = new Subject<DocumentGeneration>();
    const { fixture, element } = await setup([{ ...QUEUED, status: 'processing' }], {
      cancelGeneration,
      monitorGeneration: () => progress.asObservable(),
    });

    button(element, 'Cancel generation').click();
    fixture.detectChanges();

    expect(cancelGeneration).toHaveBeenCalledExactlyOnceWith('generation-2');
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe('Cancelling');
    const waiting = button(element, 'Cancelling');
    expect(waiting.disabled).toBe(true);

    progress.next({ ...QUEUED, status: 'cancelled', currentStage: 'cancelled' });
    progress.complete();
    fixture.detectChanges();

    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe('Cancelled');
    expect(element.querySelector('.generation-window')!.textContent).toContain(
      'The last generation was cancelled.',
    );
    expect(button(element, 'Generate document')).toBeTruthy();
  });

  it('explains a failed generation by its code and offers to generate again', async () => {
    const failed: DocumentGeneration = {
      ...QUEUED,
      status: 'failed',
      currentStage: 'failed',
      errorCode: 'GENERATION_OUT_OF_MEMORY',
      errorMessage: 'The worker has less free memory than the model needs.',
    };
    const { element } = await setup([failed]);

    const failure = element.querySelector('.failure')!;
    expect(failure.textContent).toContain('GENERATION_OUT_OF_MEMORY');
    expect(failure.textContent).toContain('The worker has less free memory than the model needs.');
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe('Failed');
    expect(button(element, 'Generate document')).toBeTruthy();
  });

  it('shows the refusal of the API and does not follow anything', async () => {
    const monitorGeneration = vi.fn();
    const { fixture, element } = await setup([], {
      createGeneration: () =>
        throwError(
          () => new NarrastepApiError('GENERATION_MODEL_UNAVAILABLE', 'The model is not enabled.'),
        ),
      monitorGeneration,
    });

    button(element, 'Generate document').click();
    fixture.detectChanges();

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'The model is not enabled.',
    );
    expect(monitorGeneration).not.toHaveBeenCalled();
    expect(button(element, 'Generate document').disabled).toBe(false);
  });

  it('sends one request for a double click', async () => {
    const pending = new Subject<DocumentGeneration>();
    const createGeneration = vi.fn(() => pending.asObservable());
    const { fixture, element } = await setup([], { createGeneration });

    const generate = button(element, 'Generate document');
    generate.click();
    generate.click();
    fixture.detectChanges();

    expect(createGeneration).toHaveBeenCalledTimes(1);
    expect(generate.disabled).toBe(true);
  });

  it('offers to read the document again when reading it failed', async () => {
    const getGenerationDocument = vi
      .fn()
      .mockReturnValueOnce(throwError(() => new Error('offline')))
      .mockReturnValue(of(MARKDOWN));
    const { fixture, element } = await setup([COMPLETED], { getGenerationDocument });

    expect(element.querySelector('.document-window [role="alert"]')!.textContent).toContain(
      'The document could not be read.',
    );
    button(element, 'Try again').click();
    fixture.detectChanges();

    expect(getGenerationDocument).toHaveBeenCalledTimes(2);
    expect(element.querySelector('.document-text')).not.toBeNull();
  });

  it('shows a load error when the project cannot be read', async () => {
    const { element } = await setup([], {
      getProject: () => throwError(() => new Error('offline')),
    });

    expect(element.querySelector('[role="alert"]')!.textContent).toContain(
      'The document could not be loaded.',
    );
    expect(element.querySelector('.generation-window')).toBeNull();
  });

  it('reads Italian with the Italian catalog', async () => {
    loadTranslations(messagesIt.translations);
    registerLocaleData(localeIt);
    document.documentElement.lang = 'it';
    const failed: DocumentGeneration = {
      ...QUEUED,
      id: 'generation-3',
      status: 'failed',
      errorCode: 'GENERATION_OUT_OF_MEMORY',
      errorMessage: 'The worker has less free memory than the model needs.',
    };
    const { element } = await setup([failed, COMPLETED], {}, PROJECT, 'it');

    expect(element.querySelector('h1')!.textContent).toBe('Documento');
    expect(element.querySelector('.status-head .ns-pill')!.textContent!.trim()).toBe(
      'Non riuscito',
    );
    expect(element.querySelector('.failure')!.textContent).toContain("Non c'è abbastanza memoria");
    expect(button(element, 'Rigenera')).toBeTruthy();
    expect(element.querySelector('.document-window h2')!.textContent).toBe('Documento generato');
    expect(element.querySelector('.video-window h2')!.textContent).toBe('Video');
  });
});
