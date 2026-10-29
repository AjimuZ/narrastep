import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Project, ProjectExport } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import { ExportComponent } from './export.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Claims workflow',
  description: null,
  mode: 'task_briefing',
  sourceLanguage: 'en',
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
    expect(fixture.nativeElement.textContent).toContain(
      'one evidence file and up to 19 images by default.',
    );
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
    expect(fixture.nativeElement.textContent).toContain('Current export');

    exportUpdates.complete();
  });
});
