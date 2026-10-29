// Tabs primitive: an accessible tab list; the caller renders the panels.
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  inject,
  input,
  model,
} from '@angular/core';

export interface TabItem {
  id: string;
  /** Already translated tab text. */
  label: string;
}

let nextTabsId = 0;

/**
 * Tab list with roving focus: Left/Right move and select, Home/End jump to the ends. Each tab is
 * `{idPrefix}-tab-{id}` and controls `{idPrefix}-panel-{id}`; the caller gives its panels those
 * ids with `role="tabpanel"` and `aria-labelledby` set to the tab id.
 */
@Component({
  selector: 'ns-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ns-tabs" role="tablist" [attr.aria-label]="label()">
      @for (tab of tabs(); track tab.id) {
        <button
          type="button"
          role="tab"
          class="ns-tab"
          [id]="idPrefix() + '-tab-' + tab.id"
          [attr.aria-selected]="tab.id === selected()"
          [attr.aria-controls]="idPrefix() + '-panel-' + tab.id"
          [attr.tabindex]="tab.id === selected() ? 0 : -1"
          (click)="selected.set(tab.id)"
          (keydown)="onKeydown($event)"
        >
          {{ tab.label }}
        </button>
      }
    </div>
  `,
})
export class TabsComponent {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  readonly tabs = input.required<TabItem[]>();
  /** Id of the selected tab; two-way bindable. */
  readonly selected = model.required<string>();
  /** Translated name of the tab list. */
  readonly label = input.required<string>();
  readonly idPrefix = input(`ns-tabs-${nextTabsId++}`);

  /** Moves the selection (and focus) for arrow, Home and End keys; other keys are ignored. */
  protected onKeydown(event: KeyboardEvent): void {
    const tabs = this.tabs();
    const current = tabs.findIndex((tab) => tab.id === this.selected());
    let next: number;
    switch (event.key) {
      case 'ArrowRight':
        next = (current + 1) % tabs.length;
        break;
      case 'ArrowLeft':
        next = (current - 1 + tabs.length) % tabs.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    const target = tabs[next];
    if (!target) return;
    event.preventDefault();
    this.selected.set(target.id);
    // Focus after the roving tabindex has been rendered.
    queueMicrotask(() =>
      this.host.nativeElement
        .querySelector<HTMLElement>(`[id="${this.idPrefix()}-tab-${target.id}"]`)
        ?.focus(),
    );
  }
}
