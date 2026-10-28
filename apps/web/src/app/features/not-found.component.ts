import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'ns-not-found',
  imports: [RouterLink],
  template: `
    <section class="page panel not-found">
      <p class="eyebrow">404</p>
      <h1>That page is not available</h1>
      <p class="muted">The address may be incorrect, or the project may have been removed.</p>
      <a class="button button--primary" routerLink="/projects">Return to projects</a>
    </section>
  `,
  styles: `
    .not-found {
      max-width: 42rem;
      margin-top: clamp(2rem, 10vh, 7rem);
      padding: clamp(2rem, 7vw, 4rem);
      border-color: var(--dk-border-strong);
      text-align: center;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotFoundComponent {}
