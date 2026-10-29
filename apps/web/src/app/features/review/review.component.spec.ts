// Unit tests for the review screen: editing, structure changes, screenshots and markup.
import { Dialog } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { EMPTY, Observable, Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import {
  Project,
  ScreenshotAssociation,
  SegmentScreenshot,
  SplitSegmentRequest,
  TranscriptSegment,
} from '../../core/api.models';
import { LanguageService, RELOAD_PAGE } from '../../core/i18n/language.service';
import { ApiService } from '../../core/services/api.service';
import { ConfirmDialogComponent } from '../../shared/dialogs/confirm-dialog.component';
import { ImagePreviewDialogComponent } from '../../shared/dialogs/image-preview-dialog.component';
import { SplitSegmentDialogComponent } from '../../shared/dialogs/split-segment-dialog.component';
import { ReviewComponent } from './review.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Search tutorial',
  description: null,
  mode: 'tutorial',
  sourceLanguage: 'en',
  detectedLanguage: 'en',
  status: 'review',
  createdAt: '2026-07-15T10:00:00Z',
  updatedAt: '2026-07-15T10:10:00Z',
  videoAssetId: 'asset-1',
  activeJobId: null,
};

const SEGMENT: TranscriptSegment = {
  id: 'segment-1',
  projectId: PROJECT.id,
  segmentIndex: 0,
  startSeconds: 1,
  endSeconds: 5,
  speakerLabel: null,
  rawText: 'Select Search.',
  reviewedText: 'Select Search.',
  confidence: 0.91,
  createdAt: '2026-07-15T10:10:00Z',
  updatedAt: '2026-07-15T10:10:00Z',
};

const PRIMARY: SegmentScreenshot = {
  id: 'shot-primary',
  projectId: PROJECT.id,
  timestampSeconds: 3,
  width: 1280,
  height: 720,
  qualityScore: 0.9,
  isCandidate: true,
  role: 'primary',
  isUserSelected: false,
  createdAt: '2026-07-15T10:10:00Z',
};

const ALTERNATIVE: SegmentScreenshot = {
  ...PRIMARY,
  id: 'shot-alternative',
  timestampSeconds: 4,
  role: 'alternative',
};

const SECOND_SEGMENT: TranscriptSegment = {
  ...SEGMENT,
  id: 'segment-2',
  segmentIndex: 1,
  startSeconds: 5,
  endSeconds: 9,
  rawText: 'Open the result.',
  reviewedText: 'Open the result.',
};

const SECOND_SCREENSHOT: SegmentScreenshot = {
  ...PRIMARY,
  id: 'shot-second',
  timestampSeconds: 7,
};

describe('ReviewComponent', () => {
  /** Creates the three-panel workspace with deterministic local evidence. */
  async function setup(options?: {
    segments?: TranscriptSegment[];
    listSegments?: ReturnType<typeof vi.fn>;
    listSegmentScreenshots?: ReturnType<typeof vi.fn>;
    updateSegment?: ReturnType<typeof vi.fn>;
    splitSegment?: ReturnType<typeof vi.fn>;
    mergeSegments?: ReturnType<typeof vi.fn>;
    removePrimaryScreenshot?: ReturnType<typeof vi.fn>;
    recoverScreenshotFromVideo?: ReturnType<typeof vi.fn>;
    openDialog?: ReturnType<typeof vi.fn>;
  }): Promise<{
    fixture: ReturnType<typeof TestBed.createComponent<ReviewComponent>>;
    updateSegment: ReturnType<typeof vi.fn>;
    selectPrimaryScreenshot: ReturnType<typeof vi.fn>;
    listSegments: ReturnType<typeof vi.fn>;
    splitSegment: ReturnType<typeof vi.fn>;
    mergeSegments: ReturnType<typeof vi.fn>;
    removePrimaryScreenshot: ReturnType<typeof vi.fn>;
    recoverScreenshotFromVideo: ReturnType<typeof vi.fn>;
    openDialog: ReturnType<typeof vi.fn>;
  }> {
    const updatedSegment = { ...SEGMENT, reviewedText: 'Select Search and review the results.' };
    const updateSegment = options?.updateSegment ?? vi.fn(() => of(updatedSegment));
    const listSegments = options?.listSegments ?? vi.fn(() => of(options?.segments ?? [SEGMENT]));
    const splitSegment = options?.splitSegment ?? vi.fn(() => of([SEGMENT]));
    const mergeSegments = options?.mergeSegments ?? vi.fn(() => of(SEGMENT));
    const removePrimaryScreenshot = options?.removePrimaryScreenshot ?? vi.fn(() => of(undefined));
    const openDialog = options?.openDialog ?? vi.fn(() => ({ closed: EMPTY }));
    const association = {
      id: 'association-2',
      segmentId: SEGMENT.id,
      screenshotId: ALTERNATIVE.id,
      role: 'primary',
      confidence: 1,
      isUserSelected: true,
      screenshot: ALTERNATIVE,
      createdAt: '2026-07-15T10:11:00Z',
    } satisfies ScreenshotAssociation;
    const selectPrimaryScreenshot = vi.fn(() => of(association));
    const recoverScreenshotFromVideo =
      options?.recoverScreenshotFromVideo ?? vi.fn(() => of(association));
    const screenshotsAfterSelection: SegmentScreenshot[] = [
      { ...ALTERNATIVE, role: 'primary', isUserSelected: true },
      { ...PRIMARY, role: 'alternative' },
    ];
    const listSegmentScreenshots =
      options?.listSegmentScreenshots ??
      vi
        .fn()
        .mockReturnValueOnce(of([PRIMARY, ALTERNATIVE]))
        .mockReturnValue(of(screenshotsAfterSelection));

    await TestBed.configureTestingModule({
      imports: [ReviewComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            listSegments,
            listSegmentScreenshots,
            updateSegment,
            splitSegment,
            mergeSegments,
            removePrimaryScreenshot,
            selectPrimaryScreenshot,
            videoUrl: () => '/api/v1/projects/project-1/video',
            screenshotUrl: (id: string) => `/api/v1/screenshots/${id}/content`,
            screenshotThumbnailUrl: (id: string) => `/api/v1/screenshots/${id}/thumbnail`,
            recoverScreenshotFromVideo,
          },
        },
      ],
    })
      .overrideProvider(Dialog, { useValue: { open: openDialog } })
      .compileComponents();
    const fixture = TestBed.createComponent(ReviewComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return {
      fixture,
      updateSegment,
      selectPrimaryScreenshot,
      listSegments,
      splitSegment,
      mergeSegments,
      removePrimaryScreenshot,
      recoverScreenshotFromVideo,
      openDialog,
    };
  }

  it('renders an accessible native selector with transcript as the default compact view', async () => {
    const { fixture } = await setup();
    const switcher = fixture.nativeElement.querySelector(
      '[role="radiogroup"][aria-label="Review workspace view"]',
    ) as HTMLElement;
    const controls = [...switcher.querySelectorAll('input[type="radio"]')] as HTMLInputElement[];

    expect(controls).toHaveLength(3);
    expect(controls.map((control) => control.getAttribute('aria-controls'))).toEqual([
      'review-video-panel',
      'review-transcript-panel',
      'review-screenshot-panel',
    ]);
    expect(controls[1]?.checked).toBe(true);
  });

  it('tracks reviewed text as unsaved until explicit persistence succeeds', async () => {
    const { fixture, updateSegment } = await setup();
    const component = fixture.componentInstance;
    const textarea = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
    textarea.value = 'Select Search and review the results.';
    textarea.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    expect(component.dirtyCount()).toBe(1);

    component.saveSegment(SEGMENT);
    fixture.detectChanges();

    expect(updateSegment).toHaveBeenCalledWith(SEGMENT.id, {
      reviewedText: 'Select Search and review the results.',
    });
    expect(component.dirtyCount()).toBe(0);
  });

  it('asks about unsaved edits before a language switch reloads the page', async () => {
    const reload = vi.fn();
    TestBed.configureTestingModule({ providers: [{ provide: RELOAD_PAGE, useValue: reload }] });
    const confirmation = new Subject<boolean>();
    const openDialog = vi.fn(() => ({ closed: confirmation }));
    const { fixture } = await setup({ openDialog });
    const component = fixture.componentInstance;
    component.editedTexts.set({ [SEGMENT.id]: 'Unsaved review text.' });
    const language = TestBed.inject(LanguageService);

    const switching = language.switchTo('it');
    expect(openDialog).toHaveBeenCalledTimes(1);
    confirmation.next(false);
    expect(await switching).toBe(false);
    expect(reload).not.toHaveBeenCalled();

    const confirmed = new Subject<boolean>();
    openDialog.mockReturnValue({ closed: confirmed });
    const retry = language.switchTo('it');
    confirmed.next(true);
    expect(await retry).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);

    fixture.destroy();
    expect(await language.switchTo('it')).toBe(true);
    expect(openDialog).toHaveBeenCalledTimes(2);
  });

  it('blocks a split while any other transcript segment has unsaved text', async () => {
    const { fixture } = await setup({ segments: [SEGMENT, SECOND_SEGMENT] });
    const component = fixture.componentInstance;
    component.editedTexts.set({ [SECOND_SEGMENT.id]: 'Unsaved review text.' });

    component.splitSegment(SEGMENT);

    expect(component.errorMessage()).toBe('Save all transcript edits before splitting a segment.');
  });

  it('reassigns a screenshot candidate through the association API', async () => {
    const { fixture, selectPrimaryScreenshot } = await setup();
    const component = fixture.componentInstance;

    component.selectPrimaryScreenshot(ALTERNATIVE);
    fixture.detectChanges();

    expect(selectPrimaryScreenshot).toHaveBeenCalledWith(SEGMENT.id, ALTERNATIVE.id);
    expect(component.primaryScreenshot()?.id).toBe(ALTERNATIVE.id);
    expect(component.primaryScreenshot()?.isUserSelected).toBe(true);
  });

  it('retains text typed after a save request until that newer edit is persisted', async () => {
    const saveResponse = new Subject<TranscriptSegment>();
    const updateSegment = vi.fn(() => saveResponse.asObservable() as Observable<TranscriptSegment>);
    const { fixture } = await setup({ updateSegment });
    const component = fixture.componentInstance;
    component.editedTexts.set({ [SEGMENT.id]: 'Submitted review text.' });

    component.saveSegment(SEGMENT);
    component.editedTexts.set({ [SEGMENT.id]: 'Newer text typed while saving.' });
    saveResponse.next({ ...SEGMENT, reviewedText: 'Submitted review text.' });
    saveResponse.complete();

    expect(updateSegment).toHaveBeenCalledWith(SEGMENT.id, {
      reviewedText: 'Submitted review text.',
    });
    expect(component.reviewedText(component.segments()[0]!)).toBe('Newer text typed while saving.');
    expect(component.dirtyCount()).toBe(1);
  });

  it('ignores a stale screenshot response after rapid segment selection', async () => {
    const staleScreenshots = new Subject<SegmentScreenshot[]>();
    const currentScreenshots = new Subject<SegmentScreenshot[]>();
    let firstSegmentRequestCount = 0;
    const listSegmentScreenshots = vi.fn((segmentId: string) => {
      if (segmentId === SEGMENT.id && firstSegmentRequestCount++ === 0) {
        return of([PRIMARY]);
      }
      return segmentId === SEGMENT.id
        ? staleScreenshots.asObservable()
        : currentScreenshots.asObservable();
    });
    const { fixture } = await setup({
      segments: [SEGMENT, SECOND_SEGMENT],
      listSegmentScreenshots,
    });
    const component = fixture.componentInstance;
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });

    component.selectSegment(SEGMENT);
    component.selectSegment(SECOND_SEGMENT);
    staleScreenshots.next([PRIMARY]);
    staleScreenshots.complete();

    expect(component.selectedSegmentId()).toBe(SECOND_SEGMENT.id);
    expect(component.segmentScreenshots()).toEqual([]);
    expect(component.screenshotLoading()).toBe(true);

    currentScreenshots.next([SECOND_SCREENSHOT]);
    currentScreenshots.complete();

    expect(component.segmentScreenshots()).toEqual([SECOND_SCREENSHOT]);
    expect(component.screenshotLoading()).toBe(false);
  });

  it('seeks to a selected segment without changing the native playback state', async () => {
    const { fixture } = await setup({ segments: [SEGMENT, SECOND_SEGMENT] });
    const component = fixture.componentInstance;
    const video = fixture.nativeElement.querySelector('video') as HTMLVideoElement;
    const segmentElement = fixture.nativeElement.querySelector(
      `#segment-${SECOND_SEGMENT.id}`,
    ) as HTMLElement;
    const scrollIntoView = vi.fn();
    Object.defineProperty(segmentElement, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    const play = vi.spyOn(video, 'play');
    const pause = vi.spyOn(video, 'pause');

    component.selectSegment(SECOND_SEGMENT);

    expect(video.currentTime).toBe(SECOND_SEGMENT.startSeconds);
    expect(play).not.toHaveBeenCalled();
    expect(pause).not.toHaveBeenCalled();
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    expect(component.selectedSegmentId()).toBe(SECOND_SEGMENT.id);
  });

  it('submits the exact reviewed text portions and refreshes active segments after a split', async () => {
    const leftChild: TranscriptSegment = {
      ...SEGMENT,
      id: 'segment-left',
      endSeconds: 3.25,
      reviewedText: 'Select ',
      rawText: 'Select',
    };
    const rightChild: TranscriptSegment = {
      ...SEGMENT,
      id: 'segment-right',
      segmentIndex: 1,
      startSeconds: 3.25,
      reviewedText: 'Search.',
      rawText: 'Search.',
    };
    const splitRequest: SplitSegmentRequest = {
      leftReviewedText: 'Select ',
      rightReviewedText: 'Search.',
      splitSeconds: 3.25,
    };
    const listSegments = vi
      .fn()
      .mockReturnValueOnce(of([SEGMENT]))
      .mockReturnValue(of([leftChild, rightChild]));
    const splitSegment = vi.fn(() => of([leftChild, rightChild]));
    const openDialog = vi.fn(() => ({ closed: of(splitRequest) }));
    const { fixture } = await setup({ listSegments, splitSegment, openDialog });
    const component = fixture.componentInstance;
    component.caretPositions.set({ [SEGMENT.id]: 7 });
    component.mergeSelection.set(new Set([SEGMENT.id]));

    component.splitSegment(SEGMENT);

    expect(openDialog).toHaveBeenCalledWith(
      SplitSegmentDialogComponent,
      expect.objectContaining({
        ariaLabel: 'Split segment 1',
        disableClose: true,
        data: expect.objectContaining({
          leftReviewedText: 'Select ',
          rightReviewedText: 'Search.',
          splitSeconds: 3,
        }),
      }),
    );
    expect(splitSegment).toHaveBeenCalledWith(SEGMENT.id, splitRequest);
    expect(listSegments).toHaveBeenCalledTimes(2);
    expect(component.segments()).toEqual([leftChild, rightChild]);
    expect(component.selectedSegmentId()).toBe(leftChild.id);
    expect(component.mergeSelection().size).toBe(0);
    expect(component.structuralPending()).toBe(false);
    expect(component.statusMessage()).toBe('Segment split and review order updated.');
  });

  it('locks transcript edits and merge selection while a structural request is active', async () => {
    const splitResult = new Subject<TranscriptSegment[]>();
    const splitRequest: SplitSegmentRequest = {
      leftReviewedText: 'Select ',
      rightReviewedText: 'Search.',
      splitSeconds: 3.25,
    };
    const { fixture } = await setup({
      splitSegment: vi.fn(() => splitResult.asObservable()),
      openDialog: vi.fn(() => ({ closed: of(splitRequest) })),
    });
    const component = fixture.componentInstance;
    component.caretPositions.set({ [SEGMENT.id]: 7 });

    component.splitSegment(SEGMENT);
    component.editedTexts.set({ [SEGMENT.id]: 'A late local edit.' });
    fixture.detectChanges();

    const textarea = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
    const mergeCheckbox = fixture.nativeElement.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    const saveButton = fixture.nativeElement.querySelector(
      '.segment__actions .ns-button--primary',
    ) as HTMLButtonElement;
    expect(component.structuralPending()).toBe(true);
    expect(textarea.disabled).toBe(true);
    expect(mergeCheckbox.disabled).toBe(true);
    expect(saveButton.disabled).toBe(true);

    splitResult.complete();
  });

  it('merges an ordered adjacent selection and refreshes the authoritative review order', async () => {
    const merged: TranscriptSegment = {
      ...SEGMENT,
      id: 'segment-merged',
      endSeconds: SECOND_SEGMENT.endSeconds,
      reviewedText: `${SEGMENT.reviewedText}\n${SECOND_SEGMENT.reviewedText}`,
      rawText: `${SEGMENT.rawText} ${SECOND_SEGMENT.rawText}`,
    };
    const listSegments = vi
      .fn()
      .mockReturnValueOnce(of([SEGMENT, SECOND_SEGMENT]))
      .mockReturnValue(of([merged]));
    const mergeSegments = vi.fn(() => of(merged));
    const openDialog = vi.fn(() => ({ closed: of(true) }));
    const { fixture } = await setup({ listSegments, mergeSegments, openDialog });
    const component = fixture.componentInstance;
    component.toggleMergeSelection(SEGMENT.id, true);
    component.toggleMergeSelection(SECOND_SEGMENT.id, true);

    component.mergeSelectedSegments();

    expect(openDialog).toHaveBeenCalledWith(
      ConfirmDialogComponent,
      expect.objectContaining({
        ariaLabel: 'Confirm merging 2 segments',
        disableClose: true,
      }),
    );
    expect(mergeSegments).toHaveBeenCalledWith({
      segmentIds: [SEGMENT.id, SECOND_SEGMENT.id],
    });
    expect(listSegments).toHaveBeenCalledTimes(2);
    expect(component.segments()).toEqual([merged]);
    expect(component.mergeSelection().size).toBe(0);
    expect(component.selectedSegmentId()).toBe(merged.id);
    expect(component.statusMessage()).toBe('Segments merged and review order updated.');
  });

  it('removes a primary association only after confirmation and keeps candidates available', async () => {
    const candidateAfterRemoval: SegmentScreenshot = {
      ...ALTERNATIVE,
      role: 'alternative',
      isUserSelected: false,
    };
    const listSegmentScreenshots = vi
      .fn()
      .mockReturnValueOnce(of([PRIMARY, ALTERNATIVE]))
      .mockReturnValue(of([candidateAfterRemoval]));
    const removePrimaryScreenshot = vi.fn(() => of(undefined));
    const openDialog = vi.fn(() => ({ closed: of(true) }));
    const { fixture } = await setup({
      listSegmentScreenshots,
      removePrimaryScreenshot,
      openDialog,
    });
    const component = fixture.componentInstance;

    component.removePrimaryScreenshot();
    fixture.detectChanges();

    expect(openDialog).toHaveBeenCalledWith(
      ConfirmDialogComponent,
      expect.objectContaining({
        ariaLabel: 'Confirm removing primary screenshot association',
        disableClose: true,
      }),
    );
    expect(removePrimaryScreenshot).toHaveBeenCalledWith(SEGMENT.id);
    expect(component.primaryScreenshot()).toBeNull();
    expect(component.alternativeScreenshots()).toEqual([candidateAfterRemoval]);
    expect(component.statusMessage()).toBe('Primary screenshot removed.');
    expect(fixture.nativeElement.textContent).toContain('No primary screenshot');
  });

  it('opens a labeled same-origin full-size screenshot preview', async () => {
    const openDialog = vi.fn(() => ({ closed: EMPTY }));
    const { fixture } = await setup({ openDialog });

    fixture.componentInstance.previewScreenshot(PRIMARY);

    expect(openDialog).toHaveBeenCalledWith(ImagePreviewDialogComponent, {
      data: {
        imageUrl: `/api/v1/screenshots/${PRIMARY.id}/content`,
        alt: 'Screenshot at 00:00:03.000',
        timestampLabel: '00:00:03.000',
      },
      ariaLabel: 'Full-size screenshot preview',
    });
  });

  it('recovers the exact current video frame and reloads primary evidence', async () => {
    const recoverScreenshotFromVideo = vi.fn(() =>
      of({
        id: 'association-recovered',
        segmentId: SEGMENT.id,
        screenshotId: ALTERNATIVE.id,
        role: 'primary' as const,
        confidence: 1,
        isUserSelected: true,
        screenshot: ALTERNATIVE,
        createdAt: '2026-07-15T10:12:00Z',
      }),
    );
    const { fixture } = await setup({ recoverScreenshotFromVideo });
    const video = fixture.nativeElement.querySelector('video') as HTMLVideoElement;
    video.currentTime = 2.75;

    fixture.componentInstance.recoverCurrentFrame();
    fixture.detectChanges();

    expect(recoverScreenshotFromVideo).toHaveBeenCalledWith(SEGMENT.id, 2.75);
    expect(fixture.componentInstance.statusMessage()).toBe(
      'Current video frame saved as primary evidence.',
    );
  });

  it('navigates segments with J while leaving typing shortcuts untouched', async () => {
    const { fixture } = await setup({ segments: [SEGMENT, SECOND_SEGMENT] });
    const component = fixture.componentInstance;

    component.handleWorkspaceShortcut(new KeyboardEvent('keydown', { key: 'j' }));

    expect(component.selectedSegmentId()).toBe(SECOND_SEGMENT.id);
    const textarea = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', bubbles: true }));
    expect(component.selectedSegmentId()).toBe(SECOND_SEGMENT.id);
  });

  it('presents both transcript and screenshot empty states when no active segments exist', async () => {
    const listSegmentScreenshots = vi.fn(() => of([]));
    const { fixture } = await setup({ segments: [], listSegmentScreenshots });

    expect(listSegmentScreenshots).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('No transcript segments');
    expect(fixture.nativeElement.textContent).toContain('Select a transcript segment');
    expect(fixture.nativeElement.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it('renders pane windows with the transcript as the only primary window and a current segment row', async () => {
    const { fixture } = await setup({ segments: [SEGMENT, SECOND_SEGMENT] });
    const root = fixture.nativeElement as HTMLElement;

    const titles = [...root.querySelectorAll('.ns-window__title')].map((title) =>
      title.textContent?.trim(),
    );
    expect(titles).toEqual(['Video', 'Segments', 'Transcript', 'Screenshot']);
    const primary = root.querySelectorAll('.ns-window--primary');
    expect(primary).toHaveLength(1);
    expect(primary[0]?.closest('#review-transcript-panel')).not.toBeNull();
    expect(root.querySelectorAll('.ns-button--primary')).toHaveLength(1);

    const rows = [...root.querySelectorAll('.segment-rail .ns-row')] as HTMLElement[];
    expect(rows).toHaveLength(2);
    expect(rows[0]?.getAttribute('aria-current')).toBe('true');
    expect(rows[1]?.hasAttribute('aria-current')).toBe(false);
    expect(root.textContent).toContain('2 segments');
  });

  it('links to the export screen and shows screenshot states as words', async () => {
    const { fixture } = await setup();
    const root = fixture.nativeElement as HTMLElement;

    const exportLink = [...root.querySelectorAll('a')].find(
      (link) => link.textContent?.trim() === 'Export',
    );
    expect(exportLink?.getAttribute('href')).toBe('/projects/project-1/export');
    const pills = [...root.querySelectorAll('ns-status-pill')].map((pill) =>
      pill.textContent?.trim(),
    );
    expect(pills).toContain('Primary');
  });

  it('shows the unsaved count and an unsaved word on the edited segment', async () => {
    const { fixture } = await setup();
    fixture.componentInstance.editedTexts.set({ [SEGMENT.id]: 'Changed text.' });
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;

    expect(root.querySelector('.unsaved[role="status"]')?.textContent).toContain('1 unsaved');
    expect(root.querySelector('.segment-rail__item.dirty')?.textContent).toContain('Unsaved');
    expect(root.querySelector('article.segment.dirty')?.textContent).toContain('Unsaved edit');
  });
});
