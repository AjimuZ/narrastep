// Projects screen: the local project list with search, type and status filters, sort and deletion.
import { DatePipe } from '@angular/common';
import { Dialog, DialogModule } from '@angular/cdk/dialog';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, filter, finalize, switchMap } from 'rxjs';

import { Project, ProjectMode, ProjectStatus } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import { errorText } from '../../core/i18n/error-text';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../shared/dialogs/confirm-dialog.component';
import { projectTypeLabel, projectTypes } from '../../shared/project-types';
import { projectStatusOptions, StatusLabel, projectStatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';

@Component({
  selector: 'ns-projects-page',
  imports: [
    ActivityLineComponent,
    DatePipe,
    DialogModule,
    PixelIconComponent,
    RouterLink,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './projects-page.component.html',
  styleUrl: './projects-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProjectsPageComponent {
  readonly projects = signal<Project[]>([]);
  readonly total = signal(0);
  readonly loading = signal(true);
  readonly errorMessage = signal<string | null>(null);
  readonly deletingProjectId = signal<string | null>(null);
  readonly searchQuery = signal('');
  readonly statusFilter = signal<'all' | ProjectStatus>('all');
  readonly typeFilter = signal<'all' | ProjectMode>('all');
  readonly projectTypes = projectTypes();
  readonly statusOptions = projectStatusOptions();
  readonly sortOrder = signal<'updated_desc' | 'created_desc' | 'name_asc'>('updated_desc');
  readonly visibleProjects = computed(() => {
    const query = this.searchQuery().trim().toLocaleLowerCase();
    const status = this.statusFilter();
    const type = this.typeFilter();
    const projects = this.projects().filter((project) => {
      const matchesQuery =
        query.length === 0 ||
        project.name.toLocaleLowerCase().includes(query) ||
        (project.description ?? '').toLocaleLowerCase().includes(query);
      return (
        matchesQuery &&
        (status === 'all' || project.status === status) &&
        (type === 'all' || project.mode === type)
      );
    });

    return projects.sort((left, right) => {
      if (this.sortOrder() === 'name_asc') {
        return left.name.localeCompare(right.name);
      }
      const field = this.sortOrder() === 'created_desc' ? 'createdAt' : 'updatedAt';
      return Date.parse(right[field]) - Date.parse(left[field]);
    });
  });

  private readonly api = inject(ApiService);
  private readonly dialog = inject(Dialog);
  private readonly destroyRef = inject(DestroyRef);
  private readonly router = inject(Router);

  /** Loads persisted local projects when the dashboard route is entered. */
  constructor() {
    this.loadProjects();
  }

  /** Reloads the collection and exposes a recoverable error state to the user. */
  loadProjects(): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.api
      .listProjects()
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error));
          return EMPTY;
        }),
      )
      .subscribe((response) => {
        this.projects.set(response.items);
        this.total.set(response.total);
      });
  }

  /** Opens the route that matches the project's current business lifecycle state. */
  openProject(project: Project): void {
    if (project.status === 'draft' || project.status === 'uploaded') {
      void this.router.navigate(['/projects/new'], { queryParams: { projectId: project.id } });
      return;
    }
    if (project.status === 'review' || project.status === 'completed') {
      void this.router.navigate(['/projects', project.id, 'review']);
      return;
    }
    if (project.status === 'exporting') {
      void this.router.navigate(['/projects', project.id, 'export']);
      return;
    }
    void this.router.navigate(['/projects', project.id]);
  }

  /** Requires an accessible destructive confirmation before deleting local evidence. */
  confirmDelete(project: Project): void {
    const data: ConfirmDialogData = {
      title: $localize`:@@projects.delete_title:Delete “${project.name}:NAME:”?`,
      message: $localize`:@@projects.delete_message:The video, transcript, screenshots and packages of this project are deleted from this computer. This cannot be undone.`,
      confirmLabel: $localize`:@@projects.delete_confirm:Delete project`,
      danger: true,
    };

    this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: $localize`:@@projects.delete_aria:Confirm deletion of ${project.name}:NAME:`,
        disableClose: true,
      })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        switchMap(() => {
          this.deletingProjectId.set(project.id);
          this.errorMessage.set(null);
          return this.api.deleteProject(project.id).pipe(
            finalize(() => this.deletingProjectId.set(null)),
            catchError((error: unknown) => {
              this.errorMessage.set(this.errorText(error));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.projects.update((projects) => projects.filter((item) => item.id !== project.id));
        this.total.update((total) => Math.max(0, total - 1));
      });
  }

  /** Presents project types as product language instead of API enum syntax. */
  modeLabel(mode: ProjectMode): string {
    return projectTypeLabel(mode);
  }

  /** State word and pill tone of a project row. */
  statusLabel(project: Project): StatusLabel {
    return projectStatusLabel(project.status);
  }

  /** Limits the current local list to one project type. */
  updateTypeFilter(event: Event): void {
    this.typeFilter.set((event.target as HTMLSelectElement).value as 'all' | ProjectMode);
  }

  /** Applies a local project-library search without issuing network requests. */
  updateSearch(event: Event): void {
    this.searchQuery.set((event.target as HTMLInputElement).value);
  }

  /** Limits the current local list to one project lifecycle status. */
  updateStatusFilter(event: Event): void {
    this.statusFilter.set((event.target as HTMLSelectElement).value as 'all' | ProjectStatus);
  }

  /** Reorders the current local list using the selected stable business field. */
  updateSortOrder(event: Event): void {
    this.sortOrder.set(
      (event.target as HTMLSelectElement).value as 'updated_desc' | 'created_desc' | 'name_asc',
    );
  }

  /** Formats an uploaded video duration for the compact dashboard row. */
  durationLabel(project: Project): string {
    const durationSeconds = project.videoAsset?.durationSeconds;
    if (durationSeconds === null || durationSeconds === undefined) {
      return $localize`:@@common.unknown:Unknown`;
    }
    const minutes = Math.floor(durationSeconds / 60);
    const seconds = Math.floor(durationSeconds % 60);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }

  /** Title of the list window with the total number of projects. */
  listTitle(): string {
    return $localize`:@@projects.list_title:All projects (${this.total()}:COUNT:)`;
  }

  /** Accessible name of the Open button, which shows only a verb. */
  openLabel(project: Project): string {
    return $localize`:@@projects.open_aria:Open ${project.name}:NAME:`;
  }

  /** Accessible name of the Delete button, which shows only a verb. */
  deleteLabel(project: Project): string {
    return $localize`:@@projects.delete_aria_label:Delete ${project.name}:NAME:`;
  }

  /** Translates a known API error code, else the API message; hides unknown implementation details. */
  private errorText(error: unknown): string {
    return errorText(
      error,
      $localize`:@@projects.error_unreachable:Narrastep could not reach its local service. Check that it is running, then try again.`,
    );
  }
}
