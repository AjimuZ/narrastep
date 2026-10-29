// New project screen: project type, name and options, then one video upload, then the processing start.
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, finalize } from 'rxjs';

import { Project, ProjectMode } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { PROJECT_TYPES } from '../../shared/project-types';
import { PixelIconComponent, SegmentedGaugeComponent, WindowComponent } from '../../shared/ui';

const SUPPORTED_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'mkv']);

@Component({
  selector: 'ns-new-project',
  imports: [
    PixelIconComponent,
    ReactiveFormsModule,
    RouterLink,
    SegmentedGaugeComponent,
    WindowComponent,
  ],
  templateUrl: './new-project.component.html',
  styleUrl: './new-project.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NewProjectComponent implements OnInit {
  readonly currentStep = signal<1 | 2 | 3>(1);
  readonly project = signal<Project | null>(null);
  readonly selectedFile = signal<File | null>(null);
  readonly fileError = signal<string | null>(null);
  readonly requestError = signal<string | null>(null);
  readonly creating = signal(false);
  readonly uploading = signal(false);
  readonly uploadPercent = signal(0);
  readonly starting = signal(false);
  readonly isDragging = signal(false);
  readonly isResuming = signal(false);
  readonly showValidationSummary = signal(false);
  /** Whether the "More options" disclosure (language and description) is expanded. */
  readonly moreOptionsOpen = signal(false);

  readonly projectForm = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.maxLength(120)]],
    description: ['', [Validators.maxLength(500)]],
    sourceLanguage: ['auto', [Validators.required, Validators.pattern(/^(auto|[a-z]{2,3})$/i)]],
    mode: ['tutorial' as ProjectMode, [Validators.required]],
  });

  /** The four ADR 0009 project types, in the documented order, with one line of help each. */
  readonly projectTypes = PROJECT_TYPES;

  /** Names of the three wizard steps, in order; step `n` is `steps[n - 1]`. */
  readonly steps = ['Project', 'Video', 'Processing'] as const;

  readonly languageSuggestions: readonly { code: string; label: string }[] = [
    { code: 'auto', label: 'Detect automatically' },
    { code: 'ar', label: 'Arabic' },
    { code: 'zh', label: 'Chinese' },
    { code: 'nl', label: 'Dutch' },
    { code: 'en', label: 'English' },
    { code: 'fr', label: 'French' },
    { code: 'de', label: 'German' },
    { code: 'hi', label: 'Hindi' },
    { code: 'it', label: 'Italian' },
    { code: 'ja', label: 'Japanese' },
    { code: 'ko', label: 'Korean' },
    { code: 'pl', label: 'Polish' },
    { code: 'pt', label: 'Portuguese' },
    { code: 'ru', label: 'Russian' },
    { code: 'es', label: 'Spanish' },
    { code: 'sv', label: 'Swedish' },
    { code: 'tr', label: 'Turkish' },
    { code: 'uk', label: 'Ukrainian' },
  ];

  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  /** Restores an unfinished draft or uploaded project selected from the local dashboard. */
  ngOnInit(): void {
    const projectId = this.route.snapshot.queryParamMap.get('projectId');
    if (!projectId) {
      return;
    }
    this.isResuming.set(true);
    this.creating.set(true);
    this.api
      .getProject(projectId)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.creating.set(false)),
        catchError((error: unknown) => {
          this.requestError.set(
            this.errorText(
              error,
              'This project could not be opened. Go back to projects and try again.',
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((project) => {
        this.project.set(project);
        this.projectForm.patchValue({
          name: project.name,
          description: project.description ?? '',
          sourceLanguage: project.sourceLanguage,
          mode: project.mode,
        });
        if (project.status === 'draft') {
          this.currentStep.set(2);
          return;
        }
        if (project.status === 'uploaded') {
          this.currentStep.set(3);
          return;
        }
        void this.router.navigate(['/projects', project.id]);
      });
  }

  /**
   * Returns the name a project gets when the field is left empty: `{Type label} {YYYY-MM-DD}`,
   * using the selected type and the local calendar date. Never fails; an unknown type falls back
   * to the generic label.
   */
  defaultName(now: Date = new Date()): string {
    const type = this.projectTypes.find(
      (option) => option.value === this.projectForm.controls.mode.value,
    );
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${type?.label ?? 'Generic'} ${now.getFullYear()}-${month}-${day}`;
  }

  /** Persists valid project metadata before accepting a potentially large video. */
  createProject(): void {
    if (this.projectForm.invalid || this.creating()) {
      this.projectForm.markAllAsTouched();
      this.showValidationSummary.set(this.projectForm.invalid);
      // An error inside the collapsed disclosure would otherwise be invisible.
      const { description, sourceLanguage } = this.projectForm.controls;
      if (description.invalid || sourceLanguage.invalid) {
        this.moreOptionsOpen.set(true);
      }
      return;
    }

    this.showValidationSummary.set(false);
    const value = this.projectForm.getRawValue();
    this.creating.set(true);
    this.requestError.set(null);
    this.api
      .createProject({
        name: value.name.trim() || this.defaultName(),
        description: value.description.trim() || null,
        sourceLanguage: value.sourceLanguage.trim().toLowerCase(),
        mode: value.mode,
      })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.creating.set(false)),
        catchError((error: unknown) => {
          this.requestError.set(
            this.errorText(error, 'The project could not be created. Try again.'),
          );
          return EMPTY;
        }),
      )
      .subscribe((project) => {
        this.project.set(project);
        this.currentStep.set(2);
      });
  }

  /** Keeps the disclosure state in sync with the native `<details>` element. */
  onMoreOptionsToggle(event: Event): void {
    this.moreOptionsOpen.set((event.target as HTMLDetailsElement).open);
  }

  /** Validates the first file selected through the native picker. */
  onFileInput(event: Event): void {
    if (this.uploading()) {
      return;
    }
    const input = event.target as HTMLInputElement;
    const file = input.files?.item(0) ?? null;
    this.selectFile(file);
  }

  /** Allows the browser drop target while keeping the operation copy-only. */
  allowDrop(event: DragEvent): void {
    event.preventDefault();
    if (this.uploading()) {
      this.isDragging.set(false);
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = 'none';
      }
      return;
    }
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
    this.isDragging.set(true);
  }

  /** Clears visual drag state when the pointer leaves the upload target. */
  leaveDropZone(event: DragEvent): void {
    event.preventDefault();
    this.isDragging.set(false);
  }

  /** Accepts exactly one dropped file and applies the same validation as the picker. */
  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragging.set(false);
    if (this.uploading()) {
      return;
    }
    const files = event.dataTransfer?.files;
    if (!files || files.length !== 1) {
      this.selectedFile.set(null);
      this.fileError.set('Drop one video at a time.');
      return;
    }
    this.selectFile(files.item(0));
  }

  /** Streams the validated source to the already-created project workspace. */
  uploadVideo(): void {
    const project = this.project();
    const file = this.selectedFile();
    if (!project || !file || this.fileError() || this.uploading()) {
      return;
    }

    this.uploading.set(true);
    this.uploadPercent.set(0);
    this.requestError.set(null);
    this.api
      .uploadVideo(project.id, file)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.uploading.set(false)),
        catchError((error: unknown) => {
          this.requestError.set(
            this.errorText(
              error,
              'The video could not be stored. Try again or choose another video.',
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((progress) => {
        this.uploadPercent.set(progress.percent);
        if (progress.state === 'complete') {
          this.currentStep.set(3);
        }
      });
  }

  /** Explicitly queues local processing only after the user confirms the long-running operation. */
  startProcessing(): void {
    const project = this.project();
    if (!project || this.starting()) {
      return;
    }

    this.starting.set(true);
    this.requestError.set(null);
    this.api
      .startProcessing(project.id)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.starting.set(false)),
        catchError((error: unknown) => {
          this.requestError.set(this.errorText(error, 'Processing could not start. Try again.'));
          return EMPTY;
        }),
      )
      .subscribe(() => void this.router.navigate(['/projects', project.id]));
  }

  /** Formats a file byte count for upload confirmation without changing its stored value. */
  formatFileSize(bytes: number): string {
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** Applies cheap browser checks while leaving the configurable size limit to the local API. */
  private selectFile(file: File | null): void {
    if (this.uploading()) {
      return;
    }
    this.fileError.set(null);
    this.requestError.set(null);
    this.selectedFile.set(null);

    if (!file) {
      return;
    }
    const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!SUPPORTED_EXTENSIONS.has(extension)) {
      this.fileError.set('This file type is not supported. Choose an MP4, WebM, MOV or MKV video.');
      return;
    }
    if (file.size === 0) {
      this.fileError.set('The file is empty. Choose another video.');
      return;
    }
    this.selectedFile.set(file);
  }

  /** Prefers the backend's stable user-safe message over a generic transport fallback. */
  private errorText(error: unknown, fallback: string): string {
    return error instanceof NarrastepApiError ? error.message : fallback;
  }
}
