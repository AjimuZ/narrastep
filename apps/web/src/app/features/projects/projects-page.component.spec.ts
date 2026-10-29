import { Dialog } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Project } from '../../core/api.models';
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
    expect(fixture.nativeElement.textContent).toContain('Your project library is empty');
    expect(
      (fixture.nativeElement.querySelector('a[href="/projects/new"]') as HTMLAnchorElement)
        .textContent,
    ).toContain('Create project');
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
});
