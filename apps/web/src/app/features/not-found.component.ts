// Fallback screen for an unknown route.
import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

import { WindowComponent } from '../shared/ui';

/** Says that the address does not exist and links back to the project list. */
@Component({
  selector: 'ns-not-found',
  imports: [RouterLink, WindowComponent],
  template: `
    <section class="page not-found">
      <header class="page-header"><h1>Page not found</h1></header>
      <ns-window title="This address does not exist" [primary]="true">
        <p class="muted">The link may be wrong, or the project was deleted.</p>
        <a class="ns-button ns-button--primary" routerLink="/projects">Open projects</a>
      </ns-window>
    </section>
  `,
  styles: `
    .not-found {
      max-width: 42rem;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotFoundComponent {}
