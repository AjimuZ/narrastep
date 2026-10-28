import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Project, UploadProgress } from '../../core/api.models';
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

  it('keeps the user on project information when the required name is missing', async () => {
    const { component, createProject } = await setup();

    component.createProject();

    expect(createProject).not.toHaveBeenCalled();
    expect(component.currentStep()).toBe(1);
    expect(component.projectForm.controls.name.touched).toBe(true);
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

  it('rejects unsupported files before any upload request', async () => {
    const { component } = await setup();
    const file = new File(['not a video'], 'notes.txt', { type: 'text/plain' });
    const event = { target: { files: { item: () => file } } } as unknown as Event;

    component.onFileInput(event);

    expect(component.selectedFile()).toBeNull();
    expect(component.fileError()).toContain('MP4, WebM, MOV, or MKV');
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

    const progressElement = fixture.nativeElement.querySelector('progress') as HTMLProgressElement;
    const fileInput = fixture.nativeElement.querySelector('#video-file') as HTMLInputElement;
    expect(uploadVideo).toHaveBeenCalledWith(PROJECT.id, file);
    expect(component.uploading()).toBe(true);
    expect(component.currentStep()).toBe(2);
    expect(fileInput.disabled).toBe(true);
    expect(progressElement.value).toBe(37);
    expect(progressElement.closest('[aria-live="polite"]')?.textContent).toContain('37%');

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
  });
});
