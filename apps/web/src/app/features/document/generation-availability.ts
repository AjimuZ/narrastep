// Decides whether local document generation can be used and guards the Document route with that answer.
import { Injectable, inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Observable, catchError, map, of } from 'rxjs';

import { GenerationModelList } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';

/**
 * Whether documents can be generated: the kill switch allows local generation and at least one
 * model is `enabled`. A model that is only installed, downloading or failed does not count.
 */
export function isGenerationAvailable(list: GenerationModelList): boolean {
  return (
    list.localGeneration === 'available' && list.items.some((model) => model.state === 'enabled')
  );
}

/** Reads `GET /models` to tell the shell and the Document route whether generation is usable. */
@Injectable({ providedIn: 'root' })
export class GenerationAvailability {
  private readonly api = inject(ApiService);

  /**
   * Emits once: true when generation is available, false when it is off, not installed, not
   * enabled, or when the API cannot be reached (the feature is then treated as absent).
   */
  check(): Observable<boolean> {
    return this.api.listModels().pipe(
      map(isGenerationAvailable),
      catchError(() => of(false)),
    );
  }
}

/**
 * Lets `/projects/:projectId/document` open only when generation is available; otherwise it sends
 * the user to the review of the same project, so the screen is absent rather than empty.
 */
export const generationAvailableGuard: CanActivateFn = (route) => {
  const router = inject(Router);
  const projectId = route.paramMap.get('projectId');
  return inject(GenerationAvailability)
    .check()
    .pipe(
      map(
        (available) =>
          available ||
          router.createUrlTree(projectId ? ['/projects', projectId, 'review'] : ['/projects']),
      ),
    );
};
