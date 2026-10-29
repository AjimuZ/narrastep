// Unit tests for the Process dialog: the required type, the optional language and its keyboard behavior.
import { DIALOG_DATA, DialogRef } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { ProjectMode } from '../../core/api.models';
import { ProcessDialogComponent } from './process-dialog.component';

describe('ProcessDialogComponent', () => {
  /** Renders the dialog for a project with the given type (null = not chosen yet) and language. */
  async function setup(
    mode: ProjectMode | null = null,
    sourceLanguage = 'auto',
  ): Promise<{
    fixture: ReturnType<typeof TestBed.createComponent<ProcessDialogComponent>>;
    root: HTMLElement;
    close: ReturnType<typeof vi.fn>;
  }> {
    const close = vi.fn();
    await TestBed.configureTestingModule({
      imports: [ProcessDialogComponent],
      providers: [
        { provide: DIALOG_DATA, useValue: { project: { name: 'Standup', mode, sourceLanguage } } },
        { provide: DialogRef, useValue: { close } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessDialogComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return { fixture, root: fixture.nativeElement as HTMLElement, close };
  }

  /** Finds a dialog button by its visible text. */
  function button(root: HTMLElement, text: string): HTMLButtonElement {
    return [...root.querySelectorAll('button')].find(
      (item) => item.textContent?.trim() === text,
    ) as HTMLButtonElement;
  }

  /** Picks the type card with the given label. */
  function pick(root: HTMLElement, label: string): void {
    const card = [...root.querySelectorAll<HTMLElement>('.type-card')].find((item) =>
      item.textContent?.includes(label),
    );
    const radio = card?.querySelector('input') as HTMLInputElement;
    radio.click();
  }

  it('names the project, offers the four types unselected and keeps the language under More options', async () => {
    const { root } = await setup();

    expect(root.querySelector('h2')?.textContent).toContain('Process “Standup”');
    const cards = [...root.querySelectorAll('.type-card strong')].map((item) => item.textContent);
    expect(cards).toEqual(['Task briefing', 'Meeting', 'Tutorial', 'Generic']);
    expect(root.querySelectorAll<HTMLInputElement>('.type-card input:checked').length).toBe(0);
    const details = root.querySelector<HTMLDetailsElement>('details.more-options');
    expect(details?.open).toBe(false);
    expect(details?.contains(root.querySelector('#process-language'))).toBe(true);
  });

  it('refuses to start without a type and says why', async () => {
    const { fixture, root, close } = await setup();

    button(root, 'Start processing').click();
    fixture.detectChanges();

    expect(close).not.toHaveBeenCalled();
    const error = root.querySelector('#process-type-error');
    expect(error?.textContent).toContain('Choose a project type');
    expect(error?.getAttribute('role')).toBe('alert');
    expect(root.querySelector('.type-card input')?.getAttribute('aria-describedby')).toBe(
      'process-type-error',
    );
  });

  it('closes with the chosen type and leaves the unchanged language out', async () => {
    const { fixture, root, close } = await setup();

    pick(root, 'Meeting');
    fixture.detectChanges();
    button(root, 'Start processing').click();

    expect(close).toHaveBeenCalledWith({ mode: 'meeting' });
  });

  it('preselects an existing type and sends a changed language in lower case', async () => {
    const { fixture, root, close } = await setup('tutorial', 'auto');
    expect(
      (root.querySelector('.type-card input:checked') as HTMLInputElement | null)?.closest(
        '.type-card',
      )?.textContent,
    ).toContain('Tutorial');

    const input = root.querySelector('#process-language') as HTMLInputElement;
    input.value = 'IT';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    button(root, 'Start processing').click();

    expect(close).toHaveBeenCalledWith({ mode: 'tutorial', sourceLanguage: 'it' });
  });

  it('opens More options and stays open when the language is not a code', async () => {
    const { fixture, root, close } = await setup('general');

    const input = root.querySelector('#process-language') as HTMLInputElement;
    input.value = 'not a code';
    input.dispatchEvent(new Event('input'));
    button(root, 'Start processing').click();
    fixture.detectChanges();

    expect(close).not.toHaveBeenCalled();
    expect(root.querySelector<HTMLDetailsElement>('details.more-options')?.open).toBe(true);
    expect(root.querySelector('#process-language-error')?.textContent).toContain(
      'not a language code',
    );
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });

  it('cancels with the Cancel button and with Escape', async () => {
    const { root, close } = await setup('general');

    button(root, 'Cancel').click();
    expect(close).toHaveBeenLastCalledWith(undefined);
    close.mockClear();
    button(root, 'Start processing').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
    expect(close).toHaveBeenCalledWith(undefined);
  });
});
