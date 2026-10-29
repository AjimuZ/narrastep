// Window primitive: a bounded panel with a title bar, optionally with stepped corner marks.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

let nextWindowId = 0;

/**
 * Titled panel. `title` is already translated by the caller and becomes the section's accessible
 * name; the body is projected. Use `primary` for the main window of a screen only.
 */
@Component({
  selector: 'ns-window',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      class="ns-window"
      [class.ns-window--primary]="primary()"
      [attr.aria-labelledby]="titleId"
    >
      <h2 class="ns-window__title" [id]="titleId">{{ title() }}</h2>
      <div class="ns-window__body"><ng-content /></div>
    </section>
  `,
  styles: `
    :host {
      display: block;
    }
  `,
  // A static `title="…"` attribute would also stay on the host and show a native tooltip over the
  // whole window; the input is the heading only, so the attribute is removed.
  host: { '[attr.title]': 'null' },
})
export class WindowComponent {
  readonly title = input.required<string>();
  /** Draws the corner marks; leave false for plain cards. */
  readonly primary = input(false);
  protected readonly titleId = `ns-window-title-${nextWindowId++}`;
}
