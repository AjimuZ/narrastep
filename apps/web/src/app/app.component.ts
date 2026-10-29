// Application shell: compact header with the product name and service health, a vertical menu and the routed screen.
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { catchError, filter, map, of, startWith, switchMap, timer } from 'rxjs';

import { ApiService } from './core/services/api.service';
import { StatusLabel } from './shared/status-labels';
import { PixelIconComponent, PixelIconName, StatusPillComponent } from './shared/ui';

/** How often the header asks the local API for its health. */
export const HEALTH_INTERVAL_MS = 30_000;

/** Health pill states; `checking` is shown only until the first answer. */
export type ShellHealth = 'checking' | 'ok' | 'degraded' | 'offline';

const HEALTH_LABELS: Record<ShellHealth, StatusLabel> = {
  checking: { label: 'Checking services', tone: 'info' },
  ok: { label: 'Services ready', tone: 'ok' },
  degraded: { label: 'Processing unavailable', tone: 'wait' },
  offline: { label: 'Services offline', tone: 'error' },
};

interface MenuEntry {
  label: string;
  path: string;
  icon: PixelIconName;
  /** Whether a router URL (without query or fragment) belongs to this entry. */
  matches: (url: string) => boolean;
}

/**
 * Menu entries of the shell. Record (P05) and Settings (P06) are added by their packages: the
 * roadmap forbids enabled entries for screens that do not exist yet.
 */
export const MENU_ENTRIES: readonly MenuEntry[] = [
  {
    label: 'Projects',
    path: '/projects',
    icon: 'folder',
    // Every project screen belongs to Projects except the new-project form.
    matches: (url) => url === '/projects' || (url.startsWith('/projects/') && !isNewProject(url)),
  },
  { label: 'New project', path: '/projects/new', icon: 'plus', matches: isNewProject },
];

function isNewProject(url: string): boolean {
  return url === '/projects/new';
}

@Component({
  selector: 'ns-root',
  imports: [PixelIconComponent, RouterLink, RouterOutlet, StatusPillComponent],
  template: `
    <a class="skip-link" href="#main-content">Skip to main content</a>
    <header class="shell-header">
      <a class="shell-brand" routerLink="/projects">Narrastep</a>
      <ns-status-pill
        class="shell-health"
        role="status"
        [label]="healthLabel().label"
        [tone]="healthLabel().tone"
      />
    </header>
    <div class="shell-body">
      <nav class="shell-menu" aria-label="Main menu">
        <ul>
          @for (entry of menu; track entry.path) {
            <li>
              <a
                class="ns-menu-entry"
                [routerLink]="entry.path"
                [attr.aria-current]="entry.matches(currentPath()) ? 'page' : null"
              >
                <ns-pixel-icon [name]="entry.icon" />
                <span>{{ entry.label }}</span>
              </a>
            </li>
          }
        </ul>
      </nav>
      <main id="main-content" class="shell-content" tabindex="-1">
        <router-outlet />
      </main>
    </div>
  `,
  styles: `
    :host {
      display: flex;
      min-height: 100vh;
      flex-direction: column;
    }

    .skip-link {
      position: fixed;
      z-index: 1100;
      top: var(--space-2);
      left: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border: 1px solid var(--frame);
      border-radius: var(--radius);
      background: var(--color-bg-panel);
      color: var(--color-text-strong);
      transform: translateY(-200%);
    }

    .skip-link:focus {
      transform: none;
    }

    .shell-header {
      position: sticky;
      z-index: 20;
      top: 0;
      display: flex;
      min-height: 3rem;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2) var(--space-4);
      padding: var(--space-2) var(--page-gutter);
      border-bottom: 1px solid var(--frame);
      background: var(--color-bg-deep);
    }

    .shell-brand {
      color: var(--color-text-strong);
      font: 600 var(--text-xl) / 1.2 var(--font-pixel);
      text-decoration: none;
    }

    .shell-body {
      display: grid;
      flex: 1;
      grid-template-columns: 13rem minmax(0, 1fr);
    }

    .shell-menu {
      border-right: 1px solid var(--frame);
      background: var(--color-bg-elevated);
    }

    .shell-menu ul {
      position: sticky;
      top: 3rem;
      display: grid;
      gap: var(--space-1);
      margin: 0;
      padding: var(--space-3) var(--space-2);
      list-style: none;
    }

    .shell-content {
      min-width: 0;
      outline: none;
    }

    /* Below 768 px the vertical menu becomes a bar under the header with the same entries. */
    @media (max-width: 47.99rem) {
      .shell-body {
        grid-template-columns: minmax(0, 1fr);
        grid-template-rows: auto 1fr;
      }

      .shell-menu {
        border-right: 0;
        border-bottom: 1px solid var(--frame);
      }

      .shell-menu ul {
        position: static;
        display: flex;
        flex-wrap: wrap;
        padding: var(--space-2) var(--page-gutter);
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  protected readonly menu = MENU_ENTRIES;

  /** Current route path without query string or fragment, used for `aria-current`. */
  protected readonly currentPath = toSignal(
    this.router.events.pipe(
      filter((event): event is NavigationEnd => event instanceof NavigationEnd),
      map((event) => pathOf(event.urlAfterRedirects)),
      startWith(pathOf(this.router.url)),
    ),
    { initialValue: '/' },
  );

  /**
   * Local service health, asked at start and every `HEALTH_INTERVAL_MS`. A failed request means the
   * API cannot be reached and shows "Services offline"; the next tick tries again.
   */
  private readonly health = toSignal(
    timer(0, HEALTH_INTERVAL_MS).pipe(
      switchMap(() =>
        this.api.health().pipe(
          map((response): ShellHealth => (response.status === 'ok' ? 'ok' : 'degraded')),
          catchError(() => of<ShellHealth>('offline')),
        ),
      ),
    ),
    { initialValue: 'checking' as ShellHealth },
  );

  protected readonly healthLabel = computed(() => HEALTH_LABELS[this.health()]);
}

/** Path part of a router URL: `/projects/1?x=2#y` becomes `/projects/1`. */
function pathOf(url: string): string {
  return url.split(/[?#]/, 1)[0] || '/';
}
