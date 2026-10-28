import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  selector: 'ns-root',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <a class="skip-link" href="#main-content">Skip to main content</a>
    <header class="app-header">
      <div class="app-header__inner">
        <a class="brand" routerLink="/projects" aria-label="Narrastep project list">
          <span class="brand__mark" aria-hidden="true">N</span>
          <span>
            <strong>Narrastep</strong>
            <small>Local-first documentation</small>
          </span>
        </a>
        <nav aria-label="Primary navigation">
          <a routerLink="/projects" routerLinkActive="active">Projects</a>
          <a class="button button--primary button--small" routerLink="/projects/new">
            New project
          </a>
        </nav>
      </div>
    </header>
    <main id="main-content" tabindex="-1">
      <router-outlet />
    </main>
  `,
  styles: `
    :host {
      display: block;
      min-height: 100vh;
    }

    .skip-link {
      position: fixed;
      z-index: 1100;
      top: 0.5rem;
      left: 0.5rem;
      padding: 0.75rem;
      border-radius: var(--dk-radius-sm);
      background: var(--dk-text-strong);
      color: var(--dk-bg);
      transform: translateY(-150%);
    }

    .skip-link:focus {
      transform: translateY(0);
    }

    .app-header {
      position: sticky;
      z-index: 20;
      top: 0;
      min-height: 4.5rem;
      border-bottom: 1px solid var(--dk-border);
      background: color-mix(in srgb, var(--dk-bg) 88%, transparent);
      box-shadow: 0 8px 28px rgba(5, 8, 15, 0.12);
      backdrop-filter: blur(16px);
    }

    .app-header__inner {
      display: flex;
      width: min(100%, var(--dk-content-width));
      min-height: 4.5rem;
      align-items: center;
      justify-content: space-between;
      gap: var(--dk-space-4);
      margin-inline: auto;
      padding: 0.75rem var(--dk-page-gutter);
    }

    .brand {
      display: inline-flex;
      align-items: center;
      gap: var(--dk-space-3);
      color: var(--dk-text-strong);
      text-decoration: none;
    }

    .brand__mark {
      display: grid;
      width: 2.5rem;
      height: 2.5rem;
      place-items: center;
      border: 1px solid var(--dk-border-strong);
      border-radius: var(--dk-radius);
      background: linear-gradient(145deg, var(--dk-accent), var(--dk-accent-strong));
      color: var(--dk-on-accent);
      font-weight: 800;
      box-shadow:
        inset 0 1px rgba(255, 255, 255, 0.24),
        0 6px 16px rgba(184, 61, 91, 0.34);
    }

    .brand strong,
    .brand small {
      display: block;
    }

    .brand small {
      color: var(--dk-muted);
      font-size: 0.68rem;
      letter-spacing: 0.01em;
    }

    nav {
      display: flex;
      align-items: center;
      gap: var(--dk-space-4);
    }

    nav > a:not(.button) {
      padding: 0.65rem 0.25rem;
      color: var(--dk-muted);
      font-size: 0.9rem;
      font-weight: 650;
      text-decoration: none;
    }

    nav > a.active:not(.button) {
      color: var(--dk-text-strong);
      box-shadow: 0 2px var(--dk-accent);
    }

    @media (max-width: 34rem) {
      .app-header,
      .app-header__inner {
        min-height: 4rem;
      }

      .app-header__inner {
        padding-block: var(--dk-space-2);
      }

      .brand__mark {
        width: 2.25rem;
        height: 2.25rem;
      }

      .brand small,
      nav > a:not(.button) {
        display: none;
      }

      nav {
        gap: var(--dk-space-2);
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {}
