import { Dialog, DialogModule } from '@angular/cdk/dialog';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  HostListener,
  OnInit,
  computed,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import {
  EMPTY,
  Observable,
  catchError,
  filter,
  finalize,
  forkJoin,
  map,
  of,
  switchMap,
} from 'rxjs';

import {
  Project,
  SegmentScreenshot,
  SplitSegmentRequest,
  TranscriptSegment,
} from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../shared/dialogs/confirm-dialog.component';
import {
  ImagePreviewDialogComponent,
  ImagePreviewDialogData,
} from '../../shared/dialogs/image-preview-dialog.component';
import {
  SplitSegmentDialogComponent,
  SplitSegmentDialogData,
} from '../../shared/dialogs/split-segment-dialog.component';

@Component({
  selector: 'ns-review',
  imports: [DialogModule, RouterLink],
  templateUrl: './review.component.html',
  styleUrl: './review.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewComponent implements OnInit {
  readonly projectId = input.required<string>();
  readonly project = signal<Project | null>(null);
  readonly segments = signal<TranscriptSegment[]>([]);
  readonly selectedSegmentId = signal<string | null>(null);
  readonly segmentScreenshots = signal<SegmentScreenshot[]>([]);
  readonly editedTexts = signal<Record<string, string>>({});
  readonly caretPositions = signal<Record<string, number>>({});
  readonly savingSegmentIds = signal<ReadonlySet<string>>(new Set());
  readonly mergeSelection = signal<ReadonlySet<string>>(new Set());
  readonly loading = signal(true);
  readonly screenshotLoading = signal(false);
  readonly structuralPending = signal(false);
  readonly screenshotPending = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly statusMessage = signal<string | null>(null);
  readonly segmentSearch = signal('');
  readonly evidenceFilter = signal<
    'all' | 'missing' | 'duplicate' | 'low_confidence' | 'manual' | 'problems'
  >('all');
  readonly editorPaneWidth = signal('1.35fr');

  readonly selectedSegment = computed(
    () => this.segments().find((segment) => segment.id === this.selectedSegmentId()) ?? null,
  );
  readonly primaryScreenshot = computed(
    () => this.segmentScreenshots().find((screenshot) => screenshot.role === 'primary') ?? null,
  );
  readonly alternativeScreenshots = computed(() =>
    this.segmentScreenshots().filter((screenshot) => screenshot.role !== 'primary'),
  );
  readonly dirtyCount = computed(
    () => this.segments().filter((segment) => this.isDirty(segment)).length,
  );
  readonly selectedForMergeCount = computed(() => this.mergeSelection().size);
  readonly filteredSegments = computed(() => {
    const query = this.segmentSearch().trim().toLocaleLowerCase();
    return this.segments().filter((segment) => {
      if (
        query &&
        !this.reviewedText(segment).toLocaleLowerCase().includes(query) &&
        !segment.rawText.toLocaleLowerCase().includes(query)
      ) {
        return false;
      }
      if (this.evidenceFilter() === 'low_confidence') {
        return segment.confidence !== null && segment.confidence < 0.7;
      }
      if (this.evidenceFilter() === 'all') {
        return true;
      }
      if (segment.id !== this.selectedSegmentId()) {
        return false;
      }
      const primary = this.primaryScreenshot();
      if (this.evidenceFilter() === 'missing') {
        return primary === null;
      }
      if (this.evidenceFilter() === 'manual') {
        return primary?.isUserSelected === true;
      }
      if (this.evidenceFilter() === 'duplicate') {
        return Boolean(primary?.clusterId);
      }
      return (
        primary === null ||
        (segment.confidence !== null && segment.confidence < 0.7) ||
        primary.textChangeScore === null
      );
    });
  });

  private readonly api = inject(ApiService);
  private readonly dialog = inject(Dialog);
  private readonly destroyRef = inject(DestroyRef);
  private readonly videoPlayer = viewChild<ElementRef<HTMLVideoElement>>('videoPlayer');
  private readonly segmentSearchInput =
    viewChild<ElementRef<HTMLInputElement>>('segmentSearchInput');
  private screenshotRequestVersion = 0;

  /** Loads the reviewed evidence model and selects the first active segment on route entry. */
  ngOnInit(): void {
    const storedPaneWidth = globalThis.localStorage?.getItem('narrastep.review.editorPaneWidth');
    if (storedPaneWidth && /^\d(?:\.\d{1,2})?fr$/.test(storedPaneWidth)) {
      this.editorPaneWidth.set(storedPaneWidth);
    }
    this.loading.set(true);
    this.errorMessage.set(null);
    forkJoin({
      project: this.api.getProject(this.projectId()),
      segments: this.api.listSegments(this.projectId()),
    })
      .pipe(
        switchMap(({ project, segments }) => {
          this.project.set(project);
          this.setSegments(segments);
          const firstSegment = segments[0];
          if (!firstSegment) {
            return of([] as SegmentScreenshot[]);
          }
          this.selectedSegmentId.set(firstSegment.id);
          return this.requestSegmentScreenshots(firstSegment.id);
        }),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error, 'Narrastep could not load review data.'));
          return EMPTY;
        }),
      )
      .subscribe((result) => {
        if (Array.isArray(result)) {
          this.segmentScreenshots.set(result);
          return;
        }
        this.applySegmentScreenshotResult(result);
      });
  }

  /** Warns before a browser tab closes while reviewed text remains unsaved. */
  @HostListener('window:beforeunload', ['$event'])
  protectBrowserUnload(event: BeforeUnloadEvent): void {
    if (this.dirtyCount() > 0) {
      event.preventDefault();
    }
  }

  /** Applies workspace shortcuts while protecting text editing and modal interactions. */
  @HostListener('document:keydown', ['$event'])
  handleWorkspaceShortcut(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    const isTyping =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      target?.isContentEditable === true;
    const saveShortcut = (event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 's';
    if (saveShortcut) {
      event.preventDefault();
      const segment = this.selectedSegment();
      if (segment) {
        this.saveSegment(segment);
      }
      return;
    }
    if (isTyping || document.querySelector('.cdk-overlay-container .cdk-dialog-container')) {
      return;
    }

    const key = event.key.toLocaleLowerCase();
    if (key === 'j' || key === 'k') {
      event.preventDefault();
      this.moveSegmentSelection(key === 'j' ? 1 : -1);
    } else if (event.key === ' ') {
      event.preventDefault();
      this.togglePlayback();
    } else if (event.key === '[' || event.key === ']') {
      event.preventDefault();
      this.seekVideo(event.key === '[' ? -2 : 2);
    } else if (event.key === '/') {
      event.preventDefault();
      this.segmentSearchInput()?.nativeElement.focus();
    } else if (key === 'm') {
      const segment = this.selectedSegment();
      if (segment) {
        this.toggleMergeSelection(segment.id, !this.mergeSelection().has(segment.id));
      }
    } else if (key === 'r') {
      this.recoverCurrentFrame();
    }
  }

  /** Returns an accessible confirmation result to the router when editor state is dirty. */
  canDeactivate(): boolean | Observable<boolean> {
    if (this.dirtyCount() === 0) {
      return true;
    }
    const data: ConfirmDialogData = {
      title: 'Discard unsaved transcript edits?',
      message: `${this.dirtyCount()} segment edit${this.dirtyCount() === 1 ? '' : 's'} will be lost if you leave this workspace.`,
      confirmLabel: 'Discard edits',
      danger: true,
    };
    return this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: 'Confirm leaving review with unsaved edits',
        disableClose: true,
      })
      .closed.pipe(map((confirmed) => confirmed === true));
  }

  /** Selects one segment, seeks the video without changing playback state, and loads its evidence. */
  selectSegment(segment: TranscriptSegment): void {
    this.selectedSegmentId.set(segment.id);
    const video = this.videoPlayer()?.nativeElement;
    if (video) {
      video.currentTime = segment.startSeconds;
    }
    document.getElementById(`segment-${segment.id}`)?.scrollIntoView({ block: 'nearest' });
    this.loadSegmentScreenshots(segment.id);
  }

  /** Jumps the native video to the currently selected segment without toggling play or pause. */
  jumpToSelectedSegment(): void {
    const segment = this.selectedSegment();
    const video = this.videoPlayer()?.nativeElement;
    if (segment && video) {
      video.currentTime = segment.startSeconds;
    }
  }

  /** Applies the user-selected native playback rate within a practical review range. */
  setPlaybackRate(event: Event): void {
    const select = event.target as HTMLSelectElement;
    const rate = Number(select.value);
    const video = this.videoPlayer()?.nativeElement;
    if (video && Number.isFinite(rate) && rate >= 0.5 && rate <= 2) {
      video.playbackRate = rate;
    }
  }

  /** Updates the local transcript search used by the structure and editor panel. */
  updateSegmentSearch(event: Event): void {
    this.segmentSearch.set((event.target as HTMLInputElement).value);
  }

  /** Applies one evidence-oriented filter to the current review structure. */
  updateEvidenceFilter(event: Event): void {
    this.evidenceFilter.set(
      (event.target as HTMLSelectElement).value as
        'all' | 'missing' | 'duplicate' | 'low_confidence' | 'manual' | 'problems',
    );
  }

  /** Persists the user-adjusted desktop transcript pane weight on this device only. */
  updateEditorPaneWidth(event: Event): void {
    const value = Number((event.target as HTMLInputElement).value);
    const width = `${value.toFixed(2)}fr`;
    this.editorPaneWidth.set(width);
    globalThis.localStorage?.setItem('narrastep.review.editorPaneWidth', width);
  }

  /** Selects the previous or next transcript segment for rapid keyboard review. */
  moveSegmentSelection(offset: -1 | 1): void {
    const segments = this.filteredSegments();
    if (segments.length === 0) {
      return;
    }
    const currentIndex = segments.findIndex((segment) => segment.id === this.selectedSegmentId());
    const nextIndex = Math.min(Math.max(currentIndex + offset, 0), segments.length - 1);
    this.selectSegment(segments[nextIndex]!);
  }

  /** Toggles native video playback without replacing browser media controls. */
  togglePlayback(): void {
    const video = this.videoPlayer()?.nativeElement;
    if (!video) {
      return;
    }
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
    }
  }

  /** Moves the native video cursor by a bounded relative amount. */
  seekVideo(offsetSeconds: number): void {
    const video = this.videoPlayer()?.nativeElement;
    if (!video) {
      return;
    }
    const duration = Number.isFinite(video.duration) ? video.duration : Number.MAX_SAFE_INTEGER;
    video.currentTime = Math.min(Math.max(video.currentTime + offsetSeconds, 0), duration);
  }

  /** Stores local reviewed text separately until the user explicitly saves it. */
  updateEditedText(segmentId: string, event: Event): void {
    if (this.structuralPending()) {
      return;
    }
    const textarea = event.target as HTMLTextAreaElement;
    this.editedTexts.update((texts) => ({ ...texts, [segmentId]: textarea.value }));
    this.caretPositions.update((positions) => ({
      ...positions,
      [segmentId]: textarea.selectionStart,
    }));
    this.statusMessage.set(null);
  }

  /** Captures the exact caret used as the default structural split boundary. */
  captureCaret(segmentId: string, event: Event): void {
    const textarea = event.target as HTMLTextAreaElement;
    this.caretPositions.update((positions) => ({
      ...positions,
      [segmentId]: textarea.selectionStart,
    }));
  }

  /** Returns pending reviewed text or the last persisted value for a segment. */
  reviewedText(segment: TranscriptSegment): string {
    return this.editedTexts()[segment.id] ?? segment.reviewedText;
  }

  /** Identifies whether one segment has local edits that are not yet persisted. */
  isDirty(segment: TranscriptSegment): boolean {
    return this.reviewedText(segment) !== segment.reviewedText;
  }

  /** Persists reviewed text while preserving the backend's immutable raw transcription. */
  saveSegment(segment: TranscriptSegment): void {
    if (
      this.structuralPending() ||
      !this.isDirty(segment) ||
      this.savingSegmentIds().has(segment.id)
    ) {
      return;
    }
    const submittedReviewedText = this.reviewedText(segment);
    this.setSegmentSaving(segment.id, true);
    this.errorMessage.set(null);
    this.api
      .updateSegment(segment.id, { reviewedText: submittedReviewedText })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.setSegmentSaving(segment.id, false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error, 'Narrastep could not save this segment.'));
          return EMPTY;
        }),
      )
      .subscribe((updated) => {
        this.segments.update((segments) =>
          segments.map((item) => (item.id === updated.id ? updated : item)),
        );
        this.editedTexts.update((texts) =>
          texts[updated.id] === submittedReviewedText ? omitKey(texts, updated.id) : texts,
        );
        this.statusMessage.set(`Segment ${updated.segmentIndex + 1} saved.`);
      });
  }

  /** Opens a timestamp-aware split dialog only after every transcript edit is safely persisted. */
  splitSegment(segment: TranscriptSegment): void {
    if (this.dirtyCount() > 0) {
      this.errorMessage.set('Save all transcript edits before splitting a segment.');
      return;
    }
    const text = segment.reviewedText;
    const caret = this.caretPositions()[segment.id] ?? Math.floor(text.length / 2);
    if (caret <= 0 || caret >= text.length) {
      this.errorMessage.set('Place the text caret between two characters before splitting.');
      return;
    }
    const ratio = caret / text.length;
    const splitSeconds = segment.startSeconds + (segment.endSeconds - segment.startSeconds) * ratio;
    const data: SplitSegmentDialogData = {
      leftReviewedText: text.slice(0, caret),
      rightReviewedText: text.slice(caret),
      splitSeconds,
      minSeconds: segment.startSeconds,
      maxSeconds: segment.endSeconds,
    };

    this.dialog
      .open<SplitSegmentRequest>(SplitSegmentDialogComponent, {
        data,
        ariaLabel: `Split segment ${segment.segmentIndex + 1}`,
        disableClose: true,
      })
      .closed.pipe(
        filter(
          (request): request is SplitSegmentRequest => request !== undefined && request !== null,
        ),
        switchMap((request) => {
          this.structuralPending.set(true);
          this.errorMessage.set(null);
          return this.api.splitSegment(segment.id, request).pipe(
            switchMap(() => this.api.listSegments(this.projectId())),
            finalize(() => this.structuralPending.set(false)),
            catchError((error: unknown) => {
              this.errorMessage.set(
                this.errorText(error, 'Narrastep could not split this segment.'),
              );
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((segments) => {
        this.setSegments(segments);
        const replacement = segments.find(
          (item) =>
            item.startSeconds >= segment.startSeconds && item.endSeconds <= segment.endSeconds,
        );
        if (replacement) {
          this.selectSegment(replacement);
        }
        this.statusMessage.set('Segment split and review order updated.');
      });
  }

  /** Adds or removes a segment from the explicit merge selection. */
  toggleMergeSelection(segmentId: string, checked: boolean): void {
    if (this.structuralPending()) {
      return;
    }
    this.mergeSelection.update((selection) => {
      const next = new Set(selection);
      if (checked) {
        next.add(segmentId);
      } else {
        next.delete(segmentId);
      }
      return next;
    });
  }

  /** Reads the checkbox state without relying on an untyped template cast. */
  onMergeCheckbox(segmentId: string, event: Event): void {
    this.toggleMergeSelection(segmentId, (event.target as HTMLInputElement).checked);
  }

  /** Validates ordered contiguity and confirms the irreversible structural merge. */
  mergeSelectedSegments(): void {
    if (this.dirtyCount() > 0) {
      this.errorMessage.set('Save all transcript edits before merging segments.');
      return;
    }
    const selected = this.segments().filter((segment) => this.mergeSelection().has(segment.id));
    if (selected.length < 2 || !areContiguous(selected)) {
      this.errorMessage.set('Select at least two adjacent transcript segments to merge.');
      return;
    }
    const data: ConfirmDialogData = {
      title: `Merge ${selected.length} transcript segments?`,
      message:
        'The reviewed text will be joined in order. Source segments remain archived in lineage history.',
      confirmLabel: 'Merge segments',
    };

    this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: `Confirm merging ${selected.length} segments`,
        disableClose: true,
      })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        switchMap(() => {
          this.structuralPending.set(true);
          this.errorMessage.set(null);
          return this.api.mergeSegments({ segmentIds: selected.map((segment) => segment.id) }).pipe(
            switchMap((merged) =>
              this.api
                .listSegments(this.projectId())
                .pipe(map((segments) => ({ merged, segments }))),
            ),
            finalize(() => this.structuralPending.set(false)),
            catchError((error: unknown) => {
              this.errorMessage.set(
                this.errorText(error, 'Narrastep could not merge these segments.'),
              );
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(({ merged, segments }) => {
        this.setSegments(segments);
        this.mergeSelection.set(new Set());
        const activeMerged = segments.find((segment) => segment.id === merged.id) ?? merged;
        this.selectSegment(activeMerged);
        this.statusMessage.set('Segments merged and review order updated.');
      });
  }

  /** Makes a generated candidate the user-selected primary and reloads authoritative associations. */
  selectPrimaryScreenshot(screenshot: SegmentScreenshot): void {
    const segment = this.selectedSegment();
    if (!segment || screenshot.role === 'primary' || this.screenshotPending()) {
      return;
    }
    this.screenshotPending.set(true);
    this.errorMessage.set(null);
    this.api
      .selectPrimaryScreenshot(segment.id, screenshot.id)
      .pipe(
        switchMap(() => this.api.listSegmentScreenshots(segment.id)),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.screenshotPending.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            this.errorText(error, 'Narrastep could not change the primary screenshot.'),
          );
          return EMPTY;
        }),
      )
      .subscribe((screenshots) => {
        if (this.selectedSegmentId() === segment.id) {
          this.segmentScreenshots.set(screenshots);
          this.statusMessage.set('Primary screenshot updated.');
        }
      });
  }

  /** Recovers the exact current source-video frame and makes it the selected primary evidence. */
  recoverCurrentFrame(): void {
    const segment = this.selectedSegment();
    const video = this.videoPlayer()?.nativeElement;
    if (!segment || !video || this.screenshotPending()) {
      return;
    }
    this.screenshotPending.set(true);
    this.errorMessage.set(null);
    this.api
      .recoverScreenshotFromVideo(segment.id, video.currentTime)
      .pipe(
        switchMap(() => this.api.listSegmentScreenshots(segment.id)),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.screenshotPending.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            this.errorText(error, 'Narrastep could not recover the current video frame.'),
          );
          return EMPTY;
        }),
      )
      .subscribe((screenshots) => {
        if (this.selectedSegmentId() === segment.id) {
          this.segmentScreenshots.set(screenshots);
          this.statusMessage.set('Current video frame saved as primary evidence.');
        }
      });
  }

  /** Removes only the primary association after explicit confirmation. */
  removePrimaryScreenshot(): void {
    const segment = this.selectedSegment();
    if (!segment || !this.primaryScreenshot() || this.screenshotPending()) {
      return;
    }
    const data: ConfirmDialogData = {
      title: 'Remove the primary screenshot?',
      message:
        'The segment will remain exportable without an image. Generated candidates are not deleted.',
      confirmLabel: 'Remove association',
      danger: true,
    };
    this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: 'Confirm removing primary screenshot association',
        disableClose: true,
      })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        switchMap(() => {
          this.screenshotPending.set(true);
          return this.api.removePrimaryScreenshot(segment.id).pipe(
            switchMap(() => this.api.listSegmentScreenshots(segment.id)),
            finalize(() => this.screenshotPending.set(false)),
            catchError((error: unknown) => {
              this.errorMessage.set(
                this.errorText(error, 'Narrastep could not remove the screenshot association.'),
              );
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((screenshots) => {
        if (this.selectedSegmentId() === segment.id) {
          this.segmentScreenshots.set(screenshots);
          this.statusMessage.set('Primary screenshot removed.');
        }
      });
  }

  /** Opens a keyboard-dismissible full-size local screenshot preview. */
  previewScreenshot(screenshot: SegmentScreenshot): void {
    const data: ImagePreviewDialogData = {
      imageUrl: this.api.screenshotUrl(screenshot.id),
      alt: `Screenshot at ${this.timeLabel(screenshot.timestampSeconds)}`,
      timestampLabel: this.timeLabel(screenshot.timestampSeconds),
    };
    this.dialog.open(ImagePreviewDialogComponent, {
      data,
      ariaLabel: 'Full-size screenshot preview',
    });
  }

  /** Builds the same-origin media URL consumed by the native player. */
  videoUrl(): string {
    return this.api.videoUrl(this.projectId());
  }

  /** Builds the lightweight local thumbnail URL used throughout routine review. */
  screenshotUrl(screenshotId: string): string {
    return this.api.screenshotThumbnailUrl(screenshotId);
  }

  /** Formats seconds as a stable HH:MM:SS.mmm review timestamp. */
  timeLabel(seconds: number): string {
    const safeSeconds = Math.max(0, seconds);
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const remainder = (safeSeconds % 60).toFixed(3).padStart(6, '0');
    return `${hours.toString().padStart(2, '0')}:${minutes
      .toString()
      .padStart(2, '0')}:${remainder}`;
  }

  /** Converts an optional confidence ratio into a compact review hint. */
  confidenceLabel(confidence: number | null): string {
    return confidence === null ? 'Not available' : `${Math.round(confidence * 100)}% confidence`;
  }

  /** Reloads screenshot associations whenever transcript selection changes. */
  private loadSegmentScreenshots(segmentId: string): void {
    this.requestSegmentScreenshots(segmentId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => this.applySegmentScreenshotResult(result));
  }

  /** Starts one screenshot request and gives it a version so stale responses cannot win a race. */
  private requestSegmentScreenshots(segmentId: string): Observable<SegmentScreenshotLoadResult> {
    const requestVersion = ++this.screenshotRequestVersion;
    this.screenshotLoading.set(true);
    this.segmentScreenshots.set([]);
    return this.api.listSegmentScreenshots(segmentId).pipe(
      map((screenshots) => ({ segmentId, requestVersion, screenshots })),
      finalize(() => {
        if (requestVersion === this.screenshotRequestVersion) {
          this.screenshotLoading.set(false);
        }
      }),
      catchError((error: unknown) => {
        if (requestVersion === this.screenshotRequestVersion) {
          this.errorMessage.set(
            this.errorText(error, 'Narrastep could not load screenshot candidates.'),
          );
        }
        return EMPTY;
      }),
    );
  }

  /** Applies screenshot evidence only when it still belongs to the latest selected segment. */
  private applySegmentScreenshotResult(result: SegmentScreenshotLoadResult): void {
    if (
      result.requestVersion === this.screenshotRequestVersion &&
      result.segmentId === this.selectedSegmentId()
    ) {
      this.segmentScreenshots.set(result.screenshots);
    }
  }

  /** Replaces active segments while pruning local editor and merge state for archived rows. */
  private setSegments(segments: TranscriptSegment[]): void {
    this.segments.set(segments);
    const activeIds = new Set(segments.map((segment) => segment.id));
    this.editedTexts.update((texts) =>
      Object.fromEntries(Object.entries(texts).filter(([id]) => activeIds.has(id))),
    );
    this.caretPositions.update((positions) =>
      Object.fromEntries(Object.entries(positions).filter(([id]) => activeIds.has(id))),
    );
    this.mergeSelection.update(
      (selection) => new Set([...selection].filter((segmentId) => activeIds.has(segmentId))),
    );
  }

  /** Updates per-segment save state without allowing callers to mutate a shared Set instance. */
  private setSegmentSaving(segmentId: string, saving: boolean): void {
    this.savingSegmentIds.update((segmentIds) => {
      const next = new Set(segmentIds);
      if (saving) {
        next.add(segmentId);
      } else {
        next.delete(segmentId);
      }
      return next;
    });
  }

  /** Uses only stable API messages when a backend operation fails. */
  private errorText(error: unknown, fallback: string): string {
    return error instanceof NarrastepApiError ? error.message : fallback;
  }
}

interface SegmentScreenshotLoadResult {
  segmentId: string;
  requestVersion: number;
  screenshots: SegmentScreenshot[];
}

/** Removes one editor entry without mutating the original signal value. */
function omitKey(values: Record<string, string>, key: string): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([entryKey]) => entryKey !== key));
}

/** Confirms that sorted active segment indices form one uninterrupted range. */
function areContiguous(segments: TranscriptSegment[]): boolean {
  return segments.every(
    (segment, index) =>
      index === 0 || segment.segmentIndex === segments[index - 1]!.segmentIndex + 1,
  );
}
