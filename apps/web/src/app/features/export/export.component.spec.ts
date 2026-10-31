// Unit tests for the Export screen: package creation, history, estimate and copy.
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { DocumentGeneration, Project, ProjectExport } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import { ExportComponent } from './export.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Claims workflow',
  description: null,
  mode: 'task_briefing',
  sourceLanguage: 'en',
  glossary: [],
  detectedLanguage: 'en',
  status: 'review',
  createdAt: '2026-07-15T10:00:00Z',
  updatedAt: '2026-07-15T10:10:00Z',
  videoAssetId: 'asset-1',
  activeJobId: null,
};

const PREVIOUS_EXPORT: ProjectExport = {
  id: 'export-1',
  projectId: PROJECT.id,
  status: 'completed',
  includeOriginalVideo: false,
  progressPercent: 100,
  currentStage: 'completed',
  errorCode: null,
  errorMessage: null,
  sizeBytes: 524_288,
  sequence: 1,
  downloadName: '2026-07-15_tutorial_demo_export-01.zip',
  createdAt: '2026-07-15T10:20:00Z',
  completedAt: '2026-07-15T10:21:00Z',
};

const QUEUED_EXPORT: ProjectExport = {
  ...PREVIOUS_EXPORT,
  id: 'export-2',
  status: 'queued',
  includeOriginalVideo: true,
  progressPercent: 0,
  currentStage: 'queued',
  sizeBytes: null,
  sequence: 2,
  downloadName: '2026-07-15_tutorial_demo_export-02.zip',
  createdAt: '2026-07-15T10:30:00Z',
  completedAt: null,
};

describe('ExportComponent', () => {
  it('creates an explicit export, follows it to completion, and exposes immutable downloads', async () => {
    const exportUpdates = new Subject<ProjectExport[]>();
    const createExport = vi.fn(() => of(QUEUED_EXPORT));
    const exportDownloadUrl = vi.fn((exportId: string) => `/api/v1/exports/${exportId}/download`);
    await TestBed.configureTestingModule({
      imports: [ExportComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            listExports: () => of([PREVIOUS_EXPORT]),
            listGenerations: () => of([]),
            estimateExport: () =>
              of({
                canonicalImageCount: 1,
                batchCount: 1,
                batchImageCopyCount: 1,
                estimatedZipBytes: 524_288,
                isApproximate: true,
                redundancyWarning: false,
              }),
            createExport,
            monitorExports: () => exportUpdates.asObservable(),
            exportDownloadUrl,
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ExportComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.completedExports()).toEqual([PREVIOUS_EXPORT]);
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.history-row .export-name')!.textContent!.trim()).toBe(
      PREVIOUS_EXPORT.downloadName,
    );
    expect(element.querySelector('.history-row .ns-pill')!.textContent).toBe('Ready');
    expect(element.textContent).toContain('one evidence file and up to 19 images by default.');
    expect(element.textContent).toContain('metadata/manifest.json (schema 0.3.0)');
    expect(element.querySelector('.estimate__number')!.textContent!.trim()).toBe('512.0 KB');
    const createButton = element.querySelector('button.ns-button--primary') as HTMLButtonElement;
    expect(createButton.textContent!.trim()).toBe('Create package');
    expect(element.querySelector('a[download]')!.textContent!.trim()).toBe('Download package');
    expect(element.querySelectorAll('.ns-window--primary')).toHaveLength(1);
    expect(
      (fixture.nativeElement.querySelector('a[download]') as HTMLAnchorElement).getAttribute(
        'href',
      ),
    ).toBe(`/api/v1/exports/${PREVIOUS_EXPORT.id}/download`);

    component.setIncludeOriginalVideo({ target: { checked: true } } as unknown as Event);
    component.createExport();
    fixture.detectChanges();

    expect(createExport).toHaveBeenCalledWith(PROJECT.id, { includeOriginalVideo: true });
    expect(component.currentExport()).toEqual(QUEUED_EXPORT);
    expect(component.creating()).toBe(true);
    expect(createButton.disabled).toBe(true);
    expect(createButton.textContent!.trim()).toBe('Creating package…');
    expect(element.querySelector('.current-export .ns-pill')!.textContent).toBe('Queued');
    expect(element.querySelector('.current-export [role="progressbar"]')).not.toBeNull();

    const completedExport: ProjectExport = {
      ...QUEUED_EXPORT,
      status: 'completed',
      progressPercent: 100,
      currentStage: 'completed',
      sizeBytes: 2_097_152,
      completedAt: '2026-07-15T10:31:00Z',
    };
    exportUpdates.next([completedExport, PREVIOUS_EXPORT]);
    exportUpdates.complete();
    fixture.detectChanges();

    expect(component.currentExport()).toBeNull();
    expect(component.completedExports()).toEqual([completedExport, PREVIOUS_EXPORT]);
    expect(component.creating()).toBe(false);
    const downloadLinks = fixture.nativeElement.querySelectorAll(
      'a[download]',
    ) as NodeListOf<HTMLAnchorElement>;
    expect(downloadLinks).toHaveLength(2);
    expect(downloadLinks[0]!.getAttribute('href')).toBe(
      `/api/v1/exports/${completedExport.id}/download`,
    );
  });

  it('renders an active persisted export without waiting for background monitoring to finish', async () => {
    const exportUpdates = new Subject<ProjectExport[]>();
    const monitorExports = vi.fn(() => exportUpdates.asObservable());
    await TestBed.configureTestingModule({
      imports: [ExportComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            listExports: () => of([QUEUED_EXPORT]),
            listGenerations: () => of([]),
            estimateExport: () =>
              of({
                canonicalImageCount: 1,
                batchCount: 1,
                batchImageCopyCount: 1,
                estimatedZipBytes: 524_288,
                isApproximate: true,
                redundancyWarning: false,
              }),
            monitorExports,
            exportDownloadUrl: () => '',
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ExportComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(monitorExports).toHaveBeenCalledWith(PROJECT.id);
    expect(fixture.componentInstance.loading()).toBe(false);
    expect(fixture.componentInstance.currentExport()).toEqual(QUEUED_EXPORT);
    expect(fixture.nativeElement.querySelector('[aria-busy="true"]')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('Current package');
    expect(fixture.nativeElement.textContent).toContain('Waiting to start');

    exportUpdates.complete();
  });

  it('shows Unknown for a missing estimate and a failed package with its error', async () => {
    const failedExport: ProjectExport = {
      ...PREVIOUS_EXPORT,
      id: 'export-3',
      status: 'failed',
      sizeBytes: null,
      errorCode: 'EXPORT_FAILED',
      errorMessage: 'The package could not be written. Free disk space and try again.',
      downloadName: 'narrastep-export-3.zip',
    };
    await TestBed.configureTestingModule({
      imports: [ExportComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            listExports: () => of([failedExport]),
            listGenerations: () => of([]),
            estimateExport: () => throwError(() => new Error('offline')),
            monitorExports: () => of([failedExport]),
            exportDownloadUrl: () => '',
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ExportComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('.estimate__number')!.textContent!.trim()).toBe('Unknown');
    expect(element.querySelector('.export-name')!.textContent!.trim()).toBe(
      'narrastep-export-3.zip',
    );
    expect(element.querySelector('.history-row .ns-pill')!.textContent).toBe('Failed');
    expect(element.querySelector('.history-error')!.textContent).toContain(
      'Free disk space and try again.',
    );
    expect(element.querySelector('a[download]')).toBeNull();
  });

  it('disables the original video option once the video was removed', async () => {
    await TestBed.configureTestingModule({
      imports: [ExportComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () =>
              of({
                ...PROJECT,
                videoAsset: {
                  id: 'asset-1',
                  projectId: PROJECT.id,
                  assetType: 'video',
                  originalFilename: 'demo.mp4',
                  mimeType: 'video/mp4',
                  sizeBytes: 1000,
                  durationSeconds: 4,
                  width: 640,
                  height: 360,
                  createdAt: '2026-07-15T10:00:00Z',
                  sourceRemovedAt: '2026-07-16T10:00:00Z',
                },
              }),
            listExports: () => of([PREVIOUS_EXPORT]),
            listGenerations: () => of([]),
            estimateExport: () => throwError(() => new Error('offline')),
            monitorExports: () => of([PREVIOUS_EXPORT]),
            exportDownloadUrl: () => '',
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ExportComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    const option = element.querySelector('#include-original-video') as HTMLInputElement;
    expect(option.disabled).toBe(true);
    expect(element.querySelector('#include-original-video-help')!.textContent).toContain(
      'The original video was removed from this computer',
    );
  });

  describe('generated document option', () => {
    const FINISHED: DocumentGeneration = {
      id: 'generation-2',
      projectId: PROJECT.id,
      modelId: 'gemma-4-e2b',
      status: 'completed',
      currentStage: 'completed',
      progressPercent: 100,
      message: 'Done',
      partsTotal: 1,
      partsCompleted: 1,
      language: null,
      promptVersion: 'generation-prompts-v1',
      documentAvailable: true,
      sizeBytes: 1200,
      errorCode: null,
      errorMessage: null,
      version: 4,
      createdAt: '2026-07-15T10:25:00Z',
      startedAt: '2026-07-15T10:25:01Z',
      completedAt: '2026-07-15T10:26:00Z',
    };

    /** Renders the screen with the given generations (newest first) and a stubbed `createExport`. */
    async function render(generations: DocumentGeneration[] | 'fails') {
      const createExport = vi.fn(() => of(QUEUED_EXPORT));
      await TestBed.configureTestingModule({
        imports: [ExportComponent],
        providers: [
          provideRouter([]),
          {
            provide: ApiService,
            useValue: {
              getProject: () => of(PROJECT),
              listExports: () => of([{ ...PREVIOUS_EXPORT, includeGenerationId: 'generation-1' }]),
              listGenerations: () =>
                generations === 'fails' ? throwError(() => new Error('offline')) : of(generations),
              estimateExport: () => throwError(() => new Error('offline')),
              createExport,
              // Finishes at once, so the screen accepts the next `createExport` call.
              monitorExports: () => of([{ ...QUEUED_EXPORT, status: 'completed' }]),
              exportDownloadUrl: () => '',
            },
          },
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(ExportComponent);
      fixture.componentRef.setInput('projectId', PROJECT.id);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return { fixture, createExport, element: fixture.nativeElement as HTMLElement };
    }

    it('offers the newest finished document, off by default, and sends its id only when chosen', async () => {
      const running: DocumentGeneration = {
        ...FINISHED,
        id: 'generation-3',
        status: 'processing',
        documentAvailable: false,
      };
      const older: DocumentGeneration = { ...FINISHED, id: 'generation-1' };
      const { fixture, createExport, element } = await render([running, FINISHED, older]);
      const option = element.querySelector('#include-generated-document') as HTMLInputElement;

      expect(option.checked).toBe(false);
      expect(element.querySelector('.document-option')!.textContent).toContain(
        'Include the generated document',
      );
      expect(element.querySelector('#include-generated-document-help')!.textContent).toContain(
        'Packages made earlier do not change.',
      );
      expect(element.querySelector('.file-list')!.textContent).not.toContain(
        '05-generated-document',
      );
      expect(element.querySelector('.history-row')!.textContent).toContain(
        'Generated document included',
      );

      fixture.componentInstance.createExport();
      expect(createExport).toHaveBeenLastCalledWith(PROJECT.id, { includeOriginalVideo: false });

      option.click();
      fixture.detectChanges();
      expect(element.querySelector('.file-list')!.textContent).toContain(
        '05-generated-document/document.md',
      );
      fixture.componentInstance.createExport();
      expect(createExport).toHaveBeenLastCalledWith(PROJECT.id, {
        includeOriginalVideo: false,
        includeGenerationId: 'generation-2',
      });
    });

    it('shows no option without a finished document or when the list cannot be read', async () => {
      const failed: DocumentGeneration = {
        ...FINISHED,
        status: 'failed',
        documentAvailable: false,
      };
      const first = await render([failed]);
      expect(first.element.querySelector('#include-generated-document')).toBeNull();
      expect(first.element.querySelector('button.ns-button--primary')).not.toBeNull();
      TestBed.resetTestingModule();

      const second = await render('fails');
      expect(second.element.querySelector('#include-generated-document')).toBeNull();
      expect(second.fixture.componentInstance.loading()).toBe(false);
    });
  });
});
