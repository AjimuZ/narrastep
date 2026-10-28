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

const SUPPORTED_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'mkv']);

@Component({
  selector: 'ns-new-project',
  imports: [ReactiveFormsModule, RouterLink],
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

  readonly projectForm = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(120)]],
    description: ['', [Validators.maxLength(500)]],
    sourceLanguage: ['auto', [Validators.required, Validators.pattern(/^(auto|[a-z]{2,3})$/i)]],
    mode: ['tutorial' as ProjectMode, [Validators.required]],
  });

  readonly modes: readonly { value: ProjectMode; label: string; detail: string }[] = [
    {
      value: 'tutorial',
      label: 'Tutorial',
      detail: 'Step-by-step work with clear actions and outcomes.',
    },
    {
      value: 'technical_walkthrough',
      label: 'Technical walkthrough',
      detail: 'A guided explanation of software or an implementation.',
    },
    {
      value: 'meeting',
      label: 'Meeting',
      detail: 'Discussion, decisions, and supporting on-screen context.',
    },
    {
      value: 'general',
      label: 'General',
      detail: 'Flexible source material without a specialized structure.',
    },
  ];

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
          this.requestError.set(this.errorText(error, 'Narrastep could not resume this project.'));
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

  /** Persists valid project metadata before accepting a potentially large video. */
  createProject(): void {
    if (this.projectForm.invalid || this.creating()) {
      this.projectForm.markAllAsTouched();
      this.showValidationSummary.set(this.projectForm.invalid);
      return;
    }

    this.showValidationSummary.set(false);
    const value = this.projectForm.getRawValue();
    this.creating.set(true);
    this.requestError.set(null);
    this.api
      .createProject({
        name: value.name.trim(),
        description: value.description.trim() || null,
        sourceLanguage: value.sourceLanguage.trim().toLowerCase(),
        mode: value.mode,
      })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.creating.set(false)),
        catchError((error: unknown) => {
          this.requestError.set(this.errorText(error, 'Narrastep could not create the project.'));
          return EMPTY;
        }),
      )
      .subscribe((project) => {
        this.project.set(project);
        this.currentStep.set(2);
      });
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
      this.fileError.set('Choose exactly one video file.');
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
            this.errorText(error, 'Narrastep could not validate or store this video.'),
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
          this.requestError.set(this.errorText(error, 'Narrastep could not queue processing.'));
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
      this.fileError.set('Choose an MP4, WebM, MOV, or MKV video.');
      return;
    }
    if (file.size === 0) {
      this.fileError.set('The selected file is empty.');
      return;
    }
    this.selectedFile.set(file);
  }

  /** Prefers the backend's stable user-safe message over a generic transport fallback. */
  private errorText(error: unknown, fallback: string): string {
    return error instanceof NarrastepApiError ? error.message : fallback;
  }
}
