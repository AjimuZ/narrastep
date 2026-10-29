// Unit tests of the New project screen: type cards, default name, options, upload and steps.
import { Dialog } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Project, StartProcessingRequest, UploadProgress } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import { NewProjectComponent } from './new-project.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Policy walkthrough',
  description: null,
  mode: 'tutorial',
  sourceLanguage: 'auto',
  detectedLanguage: null,
  status: 'draft',
  createdAt: '2026-07-15T10:00:00Z',
  updatedAt: '2026-07-15T10:00:00Z',
  videoAssetId: null,
  activeJobId: null,
};

describe('NewProjectComponent', () => {
  /** Creates an isolated wizard with a synchronous local API response. */
  async function setup(options?: { uploadVideo?: ReturnType<typeof vi.fn> }): Promise<{
    fixture: ReturnType<typeof TestBed.createComponent<NewProjectComponent>>;
    component: NewProjectComponent;
    createProject: ReturnType<typeof vi.fn>;
    uploadVideo: ReturnType<typeof vi.fn>;
  }> {
    const createProject = vi.fn(() => of(PROJECT));
    const uploadVideo = options?.uploadVideo ?? vi.fn(() => of());
    await TestBed.configureTestingModule({
      imports: [NewProjectComponent],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: { createProject, uploadVideo } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(NewProjectComponent);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance, createProject, uploadVideo };
  }

  it('keeps the user on project information when an optional field is invalid', async () => {
    const { fixture, component, createProject } = await setup();
    const details = (fixture.nativeElement as HTMLElement).querySelector('details.more-options');
    component.projectForm.controls.sourceLanguage.setValue('not a code');

    component.createProject();
    fixture.detectChanges();

    expect(createProject).not.toHaveBeenCalled();
    expect(component.currentStep()).toBe(1);
    expect(component.projectForm.controls.sourceLanguage.touched).toBe(true);
    expect(component.showValidationSummary()).toBe(true);
    // The error lives inside "More options", so the disclosure opens to show it.
    expect((details as HTMLDetailsElement).open).toBe(true);
  });

  it('shows the type cards before the name and keeps language and description under More options', async () => {
    const { fixture } = await setup();
    const host = fixture.nativeElement as HTMLElement;
    const details = host.querySelector<HTMLDetailsElement>('details.more-options');
    const cards = host.querySelector('.type-grid') as HTMLElement;
    const name = host.querySelector('#project-name') as HTMLElement;

    expect(cards.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(details?.querySelector('summary')?.textContent?.trim()).toBe('More options');
    expect(details?.open).toBe(false);
    expect(details?.contains(host.querySelector('#source-language'))).toBe(true);
    expect(details?.contains(host.querySelector('#project-description'))).toBe(true);
    expect(details?.contains(name)).toBe(false);
    expect(host.querySelector('label[for="project-name"]')?.textContent).toContain('optional');
  });

  it('creates the project with a type-and-date default name when the name is empty', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 5, 12, 0, 0));
    try {
      const { fixture, component, createProject } = await setup();
      const host = fixture.nativeElement as HTMLElement;
      const radios = host.querySelectorAll<HTMLInputElement>('.type-card input[type="radio"]');
      const nameInput = host.querySelector('#project-name') as HTMLInputElement;

      expect(nameInput.placeholder).toBe('Tutorial 2026-10-05');
      radios[1]?.click();
      fixture.detectChanges();
      expect(nameInput.placeholder).toBe('Meeting 2026-10-05');

      component.projectForm.controls.name.setValue('   ');
      component.createProject();

      expect(createProject).toHaveBeenCalledWith({
        name: 'Meeting 2026-10-05',
        description: null,
        sourceLanguage: 'auto',
        mode: 'meeting',
      });
      expect(component.currentStep()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('opens and closes More options from the native disclosure', async () => {
    const { fixture, component } = await setup();
    const details = (fixture.nativeElement as HTMLElement).querySelector(
      'details.more-options',
    ) as HTMLDetailsElement;

    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    expect(component.moreOptionsOpen()).toBe(true);
    details.open = false;
    details.dispatchEvent(new Event('toggle'));
    expect(component.moreOptionsOpen()).toBe(false);
  });

  it('persists valid metadata before moving to video selection', async () => {
    const { component, createProject } = await setup();
    component.projectForm.controls.name.setValue('  Policy walkthrough  ');
    component.projectForm.controls.description.setValue('  Search for a contract  ');
    component.projectForm.controls.sourceLanguage.setValue('it');

    component.createProject();

    expect(createProject).toHaveBeenCalledWith({
      name: 'Policy walkthrough',
      description: 'Search for a contract',
      sourceLanguage: 'it',
      mode: 'tutorial',
    });
    expect(component.project()).toEqual(PROJECT);
    expect(component.currentStep()).toBe(2);
  });

  it('offers the four project types and creates a task briefing', async () => {
    const { fixture, component, createProject } = await setup();
    const host = fixture.nativeElement as HTMLElement;
    const cardTitles = Array.from(host.querySelectorAll('.type-card strong')).map((title) =>
      title.textContent?.trim(),
    );
    const radios = host.querySelectorAll<HTMLInputElement>('.type-card input[type="radio"]');

    expect(cardTitles).toEqual(['Task briefing', 'Meeting', 'Tutorial', 'Generic']);
    expect(component.projectTypes.map((type) => type.value)).toEqual([
      'task_briefing',
      'meeting',
      'tutorial',
      'general',
    ]);

    radios[0]?.click();
    expect(component.projectForm.controls.mode.value).toBe('task_briefing');
    component.projectForm.controls.name.setValue('Fix the claims report');
    component.createProject();

    expect(createProject).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Fix the claims report', mode: 'task_briefing' }),
    );
  });

  it('rejects unsupported files before any upload request', async () => {
    const { component } = await setup();
    const file = new File(['not a video'], 'notes.txt', { type: 'text/plain' });
    const event = { target: { files: { item: () => file } } } as unknown as Event;

    component.onFileInput(event);

    expect(component.selectedFile()).toBeNull();
    expect(component.fileError()).toContain('MP4, WebM, MOV or MKV');
  });

  it('reports real upload progress and advances only after the backend completes validation', async () => {
    const progress = new Subject<UploadProgress>();
    const uploadVideo = vi.fn(() => progress.asObservable());
    const { fixture, component } = await setup({ uploadVideo });
    const file = new File(['local-video'], 'walkthrough.MP4', { type: 'video/mp4' });
    const event = { target: { files: { item: () => file } } } as unknown as Event;
    component.project.set(PROJECT);
    component.currentStep.set(2);
    component.onFileInput(event);

    component.uploadVideo();
    progress.next({ state: 'uploading', percent: 37, asset: null });
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const gauge = host.querySelector('ns-segmented-gauge [role="img"]') as HTMLElement;
    const fileInput = host.querySelector('#video-file') as HTMLInputElement;
    expect(uploadVideo).toHaveBeenCalledWith(PROJECT.id, file);
    expect(component.uploading()).toBe(true);
    expect(component.currentStep()).toBe(2);
    expect(fileInput.disabled).toBe(true);
    expect(gauge.getAttribute('aria-label')).toBe('Upload 37%');
    expect(gauge.closest('[aria-live="polite"]')?.textContent).toContain('37%');
    expect(host.querySelector('.wizard-actions button')?.textContent?.trim()).toBe('Uploading…');

    const replacement = new File(['other-video'], 'other.mp4', { type: 'video/mp4' });
    component.onFileInput({
      target: { files: { item: () => replacement } },
    } as unknown as Event);
    expect(component.selectedFile()).toBe(file);

    progress.next({ state: 'complete', percent: 100, asset: null });
    progress.complete();
    fixture.detectChanges();

    expect(component.uploading()).toBe(false);
    expect(component.uploadPercent()).toBe(100);
    expect(component.currentStep()).toBe(3);
    expect(fixture.nativeElement.textContent).toContain('Ready to process');
    expect(fixture.nativeElement.textContent).toContain('Start processing');
  });

  it('marks the current step and the finished steps in the step indicator', async () => {
    const { fixture, component } = await setup();
    const host = fixture.nativeElement as HTMLElement;
    const labels = () =>
      Array.from(host.querySelectorAll('.steps li')).map((step) => step.textContent?.trim());
    const current = () => host.querySelector('.steps [aria-current="step"]')?.textContent?.trim();

    expect(labels()).toEqual(['1 Project', '2 Video', '3 Processing']);
    expect(current()).toBe('1 Project');

    component.createProject();
    fixture.detectChanges();

    expect(current()).toBe('2 Video');
    expect(labels()[0]).toBe('Project (done)');
    expect(host.querySelector('ns-window h2')?.textContent?.trim()).toBe('Video');
  });

  it('opens More options and marks the description when it is too long', async () => {
    const { fixture, component, createProject } = await setup();
    const host = fixture.nativeElement as HTMLElement;
    component.projectForm.controls.description.setValue('x'.repeat(501));

    component.createProject();
    fixture.detectChanges();

    const description = host.querySelector('#project-description') as HTMLTextAreaElement;
    expect(createProject).not.toHaveBeenCalled();
    expect(host.querySelector<HTMLDetailsElement>('details.more-options')?.open).toBe(true);
    expect(description.getAttribute('aria-invalid')).toBe('true');
    expect(description.getAttribute('aria-describedby')).toContain('description-error');
    expect(host.querySelector('#description-error')?.textContent).toContain('500 characters');
  });

  it('labels step one with the Create project action and the name help', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 9, 8, 0, 0));
    try {
      const { fixture } = await setup();
      const host = fixture.nativeElement as HTMLElement;

      expect(host.querySelector('h1')?.textContent?.trim()).toBe('New project');
      expect(host.querySelector('button[type="submit"]')?.textContent?.trim()).toBe(
        'Create project',
      );
      expect(host.querySelector('#project-name-help')?.textContent?.trim()).toBe(
        'Leave empty to use “Tutorial 2026-01-09”.',
      );
    } finally {
      vi.useRealTimers();
    }
  });
  describe('Resuming a saved recording without a project type (P05.4)', () => {
    const SAVED_RECORDING: Project = { ...PROJECT, mode: null, status: 'uploaded' };

    /** Resumes `?projectId=` with a controllable Process dialog and a spy start call. */
    async function setupResume(): Promise<{
      component: NewProjectComponent;
      dialogResult: Subject<StartProcessingRequest | undefined>;
      startProcessing: ReturnType<typeof vi.fn>;
    }> {
      const dialogResult = new Subject<StartProcessingRequest | undefined>();
      const startProcessing = vi.fn(() => of({ id: 'job-1' }));
      await TestBed.configureTestingModule({
        imports: [NewProjectComponent],
        providers: [
          provideRouter([]),
          {
            provide: ApiService,
            useValue: { getProject: () => of(SAVED_RECORDING), startProcessing },
          },
          {
            provide: ActivatedRoute,
            useValue: { snapshot: { queryParamMap: { get: () => SAVED_RECORDING.id } } },
          },
        ],
      })
        .overrideProvider(Dialog, {
          useValue: { open: () => ({ closed: dialogResult.asObservable() }) },
        })
        .compileComponents();
      const fixture = TestBed.createComponent(NewProjectComponent);
      fixture.detectChanges();
      await fixture.whenStable();
      return { component: fixture.componentInstance, dialogResult, startProcessing };
    }

    it('opens the Process dialog before starting and sends the chosen type', async () => {
      const { component, dialogResult, startProcessing } = await setupResume();
      expect(component.currentStep()).toBe(3);

      component.startProcessing();
      expect(startProcessing).not.toHaveBeenCalled();
      dialogResult.next({ mode: 'task_briefing' });

      expect(startProcessing).toHaveBeenCalledWith(SAVED_RECORDING.id, { mode: 'task_briefing' });
    });

    it('starts nothing when the dialog is cancelled', async () => {
      const { component, dialogResult, startProcessing } = await setupResume();

      component.startProcessing();
      dialogResult.next(undefined);

      expect(startProcessing).not.toHaveBeenCalled();
      expect(component.starting()).toBe(false);
    });
  });
});
