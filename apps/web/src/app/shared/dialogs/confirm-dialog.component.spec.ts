// Unit tests for the confirmation dialog: the decision it returns and its keyboard behavior.
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { ConfirmDialogComponent } from './confirm-dialog.component';

describe('ConfirmDialogComponent', () => {
  /** Renders the dialog with a spy dialog reference. */
  async function setup(): Promise<{ root: HTMLElement; close: ReturnType<typeof vi.fn> }> {
    const close = vi.fn();
    await TestBed.configureTestingModule({
      imports: [ConfirmDialogComponent],
      providers: [
        {
          provide: DIALOG_DATA,
          useValue: { title: 'Delete?', message: 'Gone.', confirmLabel: 'Delete', danger: true },
        },
        { provide: DialogRef, useValue: { close } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ConfirmDialogComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return { root: fixture.nativeElement as HTMLElement, close };
  }

  /** Finds a dialog button by its visible text. */
  function button(root: HTMLElement, text: string): HTMLButtonElement {
    return [...root.querySelectorAll('button')].find(
      (item) => item.textContent?.trim() === text,
    ) as HTMLButtonElement;
  }

  it('confirms only through the confirm button', async () => {
    const { root, close } = await setup();

    button(root, 'Cancel').click();
    expect(close).toHaveBeenLastCalledWith(false);
    button(root, 'Delete').click();
    expect(close).toHaveBeenLastCalledWith(true);
  });

  it('cancels with Escape from any control inside the dialog', async () => {
    const { root, close } = await setup();

    button(root, 'Delete').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );

    expect(close).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledWith(false);
  });
});
