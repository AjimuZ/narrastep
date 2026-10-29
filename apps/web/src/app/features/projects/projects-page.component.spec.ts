// Projects screen tests: deletion, loading, errors, search, sort, type filter, state words and open recordings.
import { Dialog } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Project, StartProcessingRequest } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { ProjectsPageComponent } from './projects-page.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Claims workflow',
  description: 'A local claims walkthrough.',
  mode: 'task_briefing',
  sourceLanguage: 'en',
  detectedLanguage: 'en',
  status: 'review',
  createdAt: '2026-07-15T10:00:00Z',
  updatedAt: '2026-07-15T10:10:00Z',
  videoAssetId: 'asset-1',
  activeJobId: null,
};

describe('ProjectsPageComponent', () => {
  /** Creates the dashboard with a controllable accessible confirmation result. */
  async function setup(
    deleteProject: ReturnType<typeof vi.fn>,
    listProjects: ReturnType<typeof vi.fn> = vi.fn(() => of({ items: [PROJECT], total: 1 })),
  ): Promise<{
    fixture: ReturnType<typeof TestBed.createComponent<ProjectsPageComponent>>;
    confirmation: Subject<boolean | undefined>;
    openDialog: ReturnType<typeof vi.fn>;
  }> {
    const confirmation = new Subject<boolean | undefined>();
    const openDialog = vi.fn(() => ({ closed: confirmation.asObservable() }));
    await TestBed.configureTestingModule({
      imports: [ProjectsPageComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            listProjects,
            deleteProject,
          },
        },
      ],
    })
      .overrideProvider(Dialog, { useValue: { open: openDialog } })
      .compileComponents();
    const fixture = TestBed.createComponent(ProjectsPageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return { fixture, confirmation, openDialog };
  }

  it('deletes local evidence only after an accessible explicit confirmation', async () => {
    const deletion = new Subject<void>();
    const deleteProject = vi.fn(() => deletion.asObservable());
    const { fixture, confirmation, openDialog } = await setup(deleteProject);
    const component = fixture.componentInstance;

    component.confirmDelete(PROJECT);

    expect(openDialog).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        ariaLabel: `Confirm deletion of ${PROJECT.name}`,
        disableClose: true,
      }),
    );
    expect(deleteProject).not.toHaveBeenCalled();

    confirmation.next(true);
    expect(deleteProject).toHaveBeenCalledWith(PROJECT.id);
    expect(component.deletingProjectId()).toBe(PROJECT.id);

    deletion.next();
    deletion.complete();
    fixture.detectChanges();

    expect(component.deletingProjectId()).toBeNull();
    expect(component.projects()).toEqual([]);
    expect(component.total()).toBe(0);
  });

  it('keeps a project visible and presents the stable deletion error', async () => {
    const deleteProject = vi.fn(() =>
      throwError(
        () =>
          new NarrastepApiError(
            'PROJECT_BUSY',
            'Wait for the active project operation to finish.',
            {},
            409,
          ),
      ),
    );
    const { fixture, confirmation } = await setup(deleteProject);
    const component = fixture.componentInstance;

    component.confirmDelete(PROJECT);
    confirmation.next(true);
    fixture.detectChanges();

    expect(component.projects()).toEqual([PROJECT]);
    expect(component.total()).toBe(1);
    expect(component.errorMessage()).toBe('Wait for the active project operation to finish.');
    expect(
      (fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement).textContent,
    ).toContain('Wait for the active project operation to finish.');
  });

  it('announces loading and then presents an actionable empty project state', async () => {
    const projectsResponse = new Subject<{ items: Project[]; total: number }>();
    const listProjects = vi.fn(() => projectsResponse.asObservable());
    const { fixture } = await setup(
      vi.fn(() => of(undefined)),
      listProjects,
    );

    const loadingRegion = fixture.nativeElement.querySelector(
      '[aria-label="Loading projects"]',
    ) as HTMLElement;
    expect(loadingRegion.getAttribute('aria-busy')).toBe('true');
    expect(loadingRegion.textContent).toContain('Loading projects');

    projectsResponse.next({ items: [], total: 0 });
    projectsResponse.complete();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[aria-busy="true"]')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('No projects yet');
    expect(
      (fixture.nativeElement.querySelector('a[href="/projects/new"]') as HTMLAnchorElement)
        .textContent,
    ).toContain('New project');
    expect(
      (fixture.nativeElement.querySelector('.empty-actions a[href="/record"]') as HTMLAnchorElement)
        .textContent,
    ).toContain('Record');
  });

  it('exposes collection failures as a retryable alert', async () => {
    const listProjects = vi.fn(() =>
      throwError(
        () =>
          new NarrastepApiError(
            'DATABASE_UNAVAILABLE',
            'The local project database is temporarily unavailable.',
            {},
            503,
          ),
      ),
    );
    const { fixture } = await setup(
      vi.fn(() => of(undefined)),
      listProjects,
    );

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    const retryButton = alert.querySelector('button') as HTMLButtonElement;
    expect(alert.textContent).toContain('local project database is temporarily unavailable');
    expect(retryButton.textContent).toContain('Try again');

    retryButton.click();
    expect(listProjects).toHaveBeenCalledTimes(2);
  });

  it('searches and sorts the local project library without another API call', async () => {
    const olderProject: Project = {
      ...PROJECT,
      id: 'project-2',
      name: 'Account setup',
      updatedAt: '2026-07-14T10:10:00Z',
    };
    const listProjects = vi.fn(() => of({ items: [PROJECT, olderProject], total: 2 }));
    const { fixture } = await setup(
      vi.fn(() => of(undefined)),
      listProjects,
    );
    const component = fixture.componentInstance;

    component.updateSearch({ target: { value: 'account' } } as unknown as Event);
    expect(component.visibleProjects()).toEqual([olderProject]);
    component.updateSearch({ target: { value: '' } } as unknown as Event);
    component.updateSortOrder({ target: { value: 'name_asc' } } as unknown as Event);

    expect(component.visibleProjects().map((project) => project.name)).toEqual([
      'Account setup',
      'Claims workflow',
    ]);
    expect(listProjects).toHaveBeenCalledTimes(1);
  });

  it('names every project type in the library instead of showing API values', async () => {
    const { fixture } = await setup(vi.fn(() => of(undefined)));
    const component = fixture.componentInstance;

    expect(component.modeLabel('task_briefing')).toBe('Task briefing');
    expect(component.modeLabel('meeting')).toBe('Meeting');
    expect(component.modeLabel('tutorial')).toBe('Tutorial');
    expect(component.modeLabel('general')).toBe('Generic');
    const typeCell = (fixture.nativeElement as HTMLElement).querySelector('[data-label="Type"]');
    expect(typeCell?.textContent?.trim()).toBe('Task briefing');
  });

  it('shows the status as a word in a pill and filters by type', async () => {
    const meeting: Project = {
      ...PROJECT,
      id: 'project-3',
      name: 'Sprint review',
      mode: 'meeting',
    };
    const { fixture } = await setup(
      vi.fn(() => of(undefined)),
      vi.fn(() => of({ items: [PROJECT, meeting], total: 2 })),
    );
    const component = fixture.componentInstance;
    const element: HTMLElement = fixture.nativeElement;

    expect(element.querySelector('[data-label="Status"] .ns-pill')?.textContent?.trim()).toBe(
      'Ready for review',
    );
    expect(element.querySelector('.ns-pill--ok')).not.toBeNull();

    component.updateTypeFilter({ target: { value: 'meeting' } } as unknown as Event);
    fixture.detectChanges();
    expect(component.visibleProjects()).toEqual([meeting]);
    expect(element.querySelectorAll('.project-row').length).toBe(1);
    expect(element.textContent).toContain('1 of 2 shown');
  });

  it('shows open recordings as Recording or Recording interrupted and opens them in the recorder', async () => {
    const recordingProject = (id: string, interrupted: boolean): Project => ({
      ...PROJECT,
      id,
      name: `Recording ${id}`,
      status: 'draft',
      videoAssetId: null,
      recording: { id: `rec-${id}`, state: 'recording', interrupted },
    });
    const saved: Project = {
      ...PROJECT,
      id: 'saved',
      status: 'uploaded',
      recording: { id: 'rec-saved', state: 'completed', interrupted: false },
    };
    const { fixture } = await setup(
      vi.fn(() => of(undefined)),
      vi.fn(() =>
        of({
          items: [recordingProject('live', false), recordingProject('lost', true), saved],
          total: 3,
        }),
      ),
    );
    const element: HTMLElement = fixture.nativeElement;
    const pills = Array.from(element.querySelectorAll('[data-label="Status"] .ns-pill')).map(
      (pill) => pill.textContent?.trim(),
    );
    expect(pills).toEqual(['Recording', 'Recording interrupted', 'Not processed']);
    expect(element.querySelector('.page-header a[href="/record"]')?.textContent).toContain(
      'Record',
    );

    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    fixture.componentInstance.openProject(recordingProject('lost', true));
    expect(navigate).toHaveBeenCalledWith(['/record'], { queryParams: { recording: 'rec-lost' } });
    fixture.componentInstance.openProject(saved);
    expect(navigate).toHaveBeenLastCalledWith(['/projects/new'], {
      queryParams: { projectId: 'saved' },
    });
  });
  describe('Process (P05.4)', () => {
    const NOT_PROCESSED: Project = {
      ...PROJECT,
      id: 'recorded',
      name: 'Recording 2026-10-29 14:05',
      mode: null,
      status: 'uploaded',
      activeJobId: null,
      recording: { id: 'rec-1', state: 'completed', interrupted: false },
    };

    /** Creates the page with a Not processed recording, a reviewed project and a mocked start call. */
    async function setupProcess(startProcessing: ReturnType<typeof vi.fn>): Promise<{
      fixture: ReturnType<typeof TestBed.createComponent<ProjectsPageComponent>>;
      dialogResult: Subject<StartProcessingRequest | undefined>;
      openDialog: ReturnType<typeof vi.fn>;
      navigate: ReturnType<typeof vi.fn>;
    }> {
      const dialogResult = new Subject<StartProcessingRequest | undefined>();
      const openDialog = vi.fn(() => ({ closed: dialogResult.asObservable() }));
      await TestBed.configureTestingModule({
        imports: [ProjectsPageComponent],
        providers: [
          provideRouter([]),
          {
            provide: ApiService,
            useValue: {
              listProjects: () => of({ items: [NOT_PROCESSED, PROJECT], total: 2 }),
              startProcessing,
            },
          },
        ],
      })
        .overrideProvider(Dialog, { useValue: { open: openDialog } })
        .compileComponents();
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      const fixture = TestBed.createComponent(ProjectsPageComponent);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return { fixture, dialogResult, openDialog, navigate };
    }

    it('offers Process only on Not processed rows and shows a missing type as Not chosen', async () => {
      const { fixture } = await setupProcess(vi.fn());
      const element: HTMLElement = fixture.nativeElement;
      const rows = Array.from(element.querySelectorAll('.project-row'));

      const processButtons = rows.map((row) =>
        Array.from(row.querySelectorAll('button')).some(
          (item) => item.textContent?.trim() === 'Process',
        ),
      );
      expect(processButtons).toEqual([true, false]);
      // Process replaces Open on a Not processed row; the other row keeps Open.
      const openButtons = rows.map((row) =>
        Array.from(row.querySelectorAll('button')).some(
          (item) => item.textContent?.trim() === 'Open',
        ),
      );
      expect(openButtons).toEqual([false, true]);
      expect(rows[0]?.querySelector('[data-label="Type"]')?.textContent?.trim()).toBe('Not chosen');
      expect(
        rows[0]?.querySelector('button[aria-label^="Process"]')?.getAttribute('aria-label'),
      ).toBe('Process Recording 2026-10-29 14:05');
    });

    it('filters the projects whose type is not chosen', async () => {
      const { fixture } = await setupProcess(vi.fn());
      const component = fixture.componentInstance;

      component.updateTypeFilter({ target: { value: 'none' } } as unknown as Event);

      expect(component.visibleProjects().map((project) => project.id)).toEqual(['recorded']);
    });

    it('starts processing with the dialog choices and opens the processing screen', async () => {
      const startProcessing = vi.fn(() => of({ id: 'job-1' }));
      const { fixture, dialogResult, openDialog, navigate } = await setupProcess(startProcessing);

      fixture.componentInstance.processProject(NOT_PROCESSED);
      expect(openDialog).toHaveBeenCalledOnce();
      expect(startProcessing).not.toHaveBeenCalled();

      dialogResult.next({ mode: 'meeting', sourceLanguage: 'it' });

      expect(startProcessing).toHaveBeenCalledWith('recorded', {
        mode: 'meeting',
        sourceLanguage: 'it',
      });
      expect(navigate).toHaveBeenCalledWith(['/projects', 'recorded']);
    });

    it('starts nothing when the dialog is cancelled', async () => {
      const startProcessing = vi.fn(() => of({ id: 'job-1' }));
      const { fixture, dialogResult, navigate } = await setupProcess(startProcessing);

      fixture.componentInstance.processProject(NOT_PROCESSED);
      dialogResult.next(undefined);

      expect(startProcessing).not.toHaveBeenCalled();
      expect(navigate).not.toHaveBeenCalled();
    });

    it('shows the API refusal in its language and stays on the list', async () => {
      const startProcessing = vi.fn(() =>
        throwError(
          () =>
            new NarrastepApiError(
              'PROJECT_TYPE_REQUIRED',
              'Choose a project type before processing this project.',
              {},
              409,
            ),
        ),
      );
      const { fixture, dialogResult, navigate } = await setupProcess(startProcessing);

      fixture.componentInstance.processProject(NOT_PROCESSED);
      dialogResult.next({ mode: 'meeting' });
      fixture.detectChanges();

      expect(navigate).not.toHaveBeenCalled();
      expect(fixture.nativeElement.querySelector('[role="alert"]')?.textContent).toContain(
        'Choose a project type before processing',
      );
    });
  });
});
