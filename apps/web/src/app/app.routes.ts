import { Routes } from '@angular/router';

import { pendingReviewChangesGuard } from './features/review/pending-review-changes.guard';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'projects',
  },
  {
    path: 'projects',
    loadComponent: () =>
      import('./features/projects/projects-page.component').then(
        (module) => module.ProjectsPageComponent,
      ),
    title: 'Projects · Narrastep',
  },
  {
    path: 'projects/new',
    loadComponent: () =>
      import('./features/new-project/new-project.component').then(
        (module) => module.NewProjectComponent,
      ),
    title: 'New project · Narrastep',
  },
  {
    path: 'projects/:projectId',
    loadComponent: () =>
      import('./features/processing/processing.component').then(
        (module) => module.ProcessingComponent,
      ),
    title: 'Processing · Narrastep',
  },
  {
    path: 'projects/:projectId/review',
    canDeactivate: [pendingReviewChangesGuard],
    loadComponent: () =>
      import('./features/review/review.component').then((module) => module.ReviewComponent),
    title: 'Review · Narrastep',
  },
  {
    path: 'projects/:projectId/export',
    loadComponent: () =>
      import('./features/export/export.component').then((module) => module.ExportComponent),
    title: 'Export · Narrastep',
  },
  {
    path: '**',
    loadComponent: () =>
      import('./features/not-found.component').then((module) => module.NotFoundComponent),
    title: 'Page not found · Narrastep',
  },
];
