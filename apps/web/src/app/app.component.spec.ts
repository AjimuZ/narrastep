// Shell tests: the menu marks the current screen and the header shows the real service health.
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppComponent, HEALTH_INTERVAL_MS, MENU_ENTRIES } from './app.component';
import { HealthResponse } from './core/api.models';
import { ApiService } from './core/services/api.service';

@Component({ template: '' })
class BlankComponent {}

const HEALTHY: HealthResponse = {
  status: 'ok',
  version: '0.2.0',
  database: 'ok',
  redis: 'ok',
  worker: 'ok',
};

async function setup(health: () => Observable<HealthResponse>) {
  await TestBed.configureTestingModule({
    imports: [AppComponent],
    providers: [
      provideRouter([
        { path: 'projects', component: BlankComponent },
        { path: 'projects/new', component: BlankComponent },
        { path: 'projects/:id', component: BlankComponent },
        { path: 'record', component: BlankComponent },
      ]),
      { provide: ApiService, useValue: { health: vi.fn(health) } },
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(AppComponent);
  fixture.detectChanges();
  const element: HTMLElement = fixture.nativeElement;
  return { fixture, element, router: TestBed.inject(Router) };
}

function currentEntries(element: HTMLElement): string[] {
  return Array.from(element.querySelectorAll('nav [aria-current="page"]')).map(
    (entry) => entry.textContent?.trim() ?? '',
  );
}

describe('AppComponent shell', () => {
  afterEach(() => vi.useRealTimers());

  it('shows only the product name and lists the menu entries that exist', async () => {
    const { element } = await setup(() => of(HEALTHY));
    expect(element.querySelector('.shell-brand')?.textContent?.trim()).toBe('Narrastep');
    const nav = element.querySelector('nav[aria-label="Main menu"]') as HTMLElement;
    expect(Array.from(nav.querySelectorAll('a')).map((a) => a.textContent?.trim())).toEqual([
      'Projects',
      'Record',
      'New project',
      'Storage',
    ]);
    expect(MENU_ENTRIES.map((entry) => entry.path)).toEqual([
      '/projects',
      '/record',
      '/projects/new',
      '/storage',
    ]);
  });

  it('marks the current menu entry with aria-current', async () => {
    const { fixture, element, router } = await setup(() => of(HEALTHY));
    await router.navigateByUrl('/projects/new');
    fixture.detectChanges();
    expect(currentEntries(element)).toEqual(['New project']);
    await router.navigateByUrl('/projects/abc?tab=1');
    fixture.detectChanges();
    expect(currentEntries(element)).toEqual(['Projects']);
    await router.navigateByUrl('/record?recording=rec-1');
    fixture.detectChanges();
    expect(currentEntries(element)).toEqual(['Record']);
  });

  it('reports healthy, degraded and unreachable services with a word', async () => {
    vi.useFakeTimers();
    const answers = new Subject<HealthResponse>();
    let calls = 0;
    const { fixture, element } = await setup(() => {
      calls += 1;
      return calls === 3 ? throwError(() => new Error('offline')) : answers.asObservable();
    });
    const pill = () => element.querySelector('.ns-pill')?.textContent?.trim();

    expect(pill()).toBe('Checking services');
    vi.advanceTimersByTime(0);
    answers.next(HEALTHY);
    fixture.detectChanges();
    expect(pill()).toBe('Services ready');

    vi.advanceTimersByTime(HEALTH_INTERVAL_MS);
    answers.next({ ...HEALTHY, status: 'degraded', worker: 'unavailable' });
    fixture.detectChanges();
    expect(pill()).toBe('Processing unavailable');

    vi.advanceTimersByTime(HEALTH_INTERVAL_MS);
    fixture.detectChanges();
    expect(pill()).toBe('Services offline');
    expect(element.querySelector('.ns-pill--error')).not.toBeNull();
  });
});
