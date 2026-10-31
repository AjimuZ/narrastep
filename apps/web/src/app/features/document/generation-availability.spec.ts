// Tests for the availability rule and the Document route guard: the screen is absent unless a model is enabled.
import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, UrlTree, provideRouter } from '@angular/router';
import { Observable, firstValueFrom, of, throwError } from 'rxjs';
import { describe, expect, it } from 'vitest';

import { GenerationModel, GenerationModelList, ModelInstallState } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import {
  GenerationAvailability,
  generationAvailableGuard,
  isGenerationAvailable,
} from './generation-availability';

/** A catalog answer with a Light model in the given state and a Quality model not installed. */
function list(
  light: ModelInstallState,
  localGeneration: 'available' | 'disabled' = 'available',
): GenerationModelList {
  return {
    localGeneration,
    items: [
      { id: 'gemma-4-e2b', state: light },
      { id: 'gemma-4-e4b', state: 'not_installed' },
    ] as GenerationModel[],
  };
}

describe('isGenerationAvailable', () => {
  it('is true only with the kill switch off and a model enabled', () => {
    expect(isGenerationAvailable(list('enabled'))).toBe(true);
    expect(isGenerationAvailable(list('installed'))).toBe(false);
    expect(isGenerationAvailable(list('downloading'))).toBe(false);
    expect(isGenerationAvailable(list('failed'))).toBe(false);
    expect(isGenerationAvailable(list('not_installed'))).toBe(false);
    expect(isGenerationAvailable(list('enabled', 'disabled'))).toBe(false);
    expect(isGenerationAvailable({ localGeneration: 'available', items: [] })).toBe(false);
  });
});

describe('generationAvailableGuard', () => {
  /** Runs the guard for project-1 with the given `GET /models` answer. */
  async function run(models: Observable<GenerationModelList>): Promise<boolean | UrlTree> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: { listModels: () => models } },
      ],
    });
    const route = {
      paramMap: new Map([['projectId', 'project-1']]),
    } as unknown as ActivatedRouteSnapshot;
    const result = TestBed.runInInjectionContext(() =>
      generationAvailableGuard(route, {} as never),
    );
    return firstValueFrom(result as Observable<boolean | UrlTree>);
  }

  it('lets the route open while a model is enabled', async () => {
    expect(await run(of(list('enabled')))).toBe(true);
  });

  it.each([
    ['no model is enabled', of(list('installed'))],
    ['local generation is disabled', of(list('enabled', 'disabled'))],
    ['the API cannot be reached', throwError(() => new Error('offline'))],
  ])('redirects to the review when %s', async (_name, models) => {
    const result = await run(models);

    expect(result).toBeInstanceOf(UrlTree);
    expect(TestBed.inject(Router).serializeUrl(result as UrlTree)).toBe(
      '/projects/project-1/review',
    );
  });
});

describe('GenerationAvailability', () => {
  it('treats a failed request as not available', async () => {
    TestBed.configureTestingModule({
      providers: [
        { provide: ApiService, useValue: { listModels: () => throwError(() => new Error('x')) } },
      ],
    });

    expect(await firstValueFrom(TestBed.inject(GenerationAvailability).check())).toBe(false);
  });
});
