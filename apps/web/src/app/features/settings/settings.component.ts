// Settings screen: the interface language and the local generation model manager (install, enable, delete).
import { Dialog, DialogModule } from '@angular/cdk/dialog';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  Injector,
  afterNextRender,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Observable, Subscription, catchError, filter, finalize } from 'rxjs';

import { GenerationModel, GenerationModelList } from '../../core/api.models';
import { errorText } from '../../core/i18n/error-text';
import { formatFileSize } from '../../core/i18n/format';
import { LANGUAGES, Language, activeLocale } from '../../core/i18n/language';
import { LanguageService } from '../../core/i18n/language.service';
import { ApiService } from '../../core/services/api.service';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../shared/dialogs/confirm-dialog.component';
import { StatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  SegmentedGaugeComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';

/**
 * Lets the user switch the interface language and manage the optional local generation models. A
 * model is installed only after its license name and link were shown and accepted; the download
 * progress is the real byte count from the API. When the API reports the kill switch
 * (`localGeneration: "disabled"`), no model and no model control is shown at all.
 */
@Component({
  selector: 'ns-settings',
  imports: [
    ActivityLineComponent,
    DialogModule,
    PixelIconComponent,
    SegmentedGaugeComponent,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SettingsComponent {
  readonly languages = LANGUAGES;
  readonly list = signal<GenerationModelList | null>(null);
  readonly loading = signal(true);
  readonly errorMessage = signal<string | null>(null);
  /** Model whose request is in flight; its buttons are disabled meanwhile. */
  readonly busyModelId = signal<string | null>(null);
  /** Model whose license and download size are shown, waiting for the user's acceptance. */
  readonly installingModelId = signal<string | null>(null);
  /** Result of the last action, announced politely to screen readers. */
  readonly notice = signal<string | null>(null);

  protected readonly language = inject(LanguageService);

  private readonly api = inject(ApiService);
  private readonly dialog = inject(Dialog);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  /** One running poll per downloading model; every poll stops when the screen is left. */
  private readonly watches = new Map<string, Subscription>();

  /** Loads the models when the route is entered. */
  constructor() {
    this.destroyRef.onDestroy(() => this.watches.forEach((watch) => watch.unsubscribe()));
    this.load();
  }

  /** Reloads the model list and resumes watching any download that is still running. */
  load(): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.api
      .listModels()
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error));
          return EMPTY;
        }),
      )
      .subscribe((list) => {
        this.list.set(list);
        if (list.localGeneration === 'available') {
          list.items.filter(isInProgress).forEach((model) => this.watch(model.id));
        }
      });
  }

  /** Saves the language and reloads; an unsaved-changes check of another screen may cancel it. */
  switchLanguage(code: Language): void {
    void this.language.switchTo(code);
  }

  /** Shows the license name and link with the download size; nothing is requested yet. */
  openInstall(model: GenerationModel): void {
    this.installingModelId.set(model.id);
  }

  /** Closes the license panel without downloading and puts focus back on the model. */
  cancelInstall(model: GenerationModel): void {
    this.installingModelId.set(null);
    this.focusModel(model.id);
  }

  /** Starts the download after the license was shown and accepted, then follows its progress. */
  acceptAndDownload(model: GenerationModel): void {
    this.installingModelId.set(null);
    this.act(
      model,
      this.api.downloadModel(model.id),
      $localize`:@@settings.notice.download_started:Download of the ${this.modelName(model)}:NAME: model started.`,
    );
  }

  /** Retries a failed download; the partial files are resumed by the API. */
  retryDownload(model: GenerationModel): void {
    this.act(
      model,
      this.api.downloadModel(model.id),
      $localize`:@@settings.notice.download_resumed:Download of the ${this.modelName(model)}:NAME: model started again.`,
    );
  }

  /** Stops a download or its verification; the API deletes the partial files. */
  cancelDownload(model: GenerationModel): void {
    this.watches.get(model.id)?.unsubscribe();
    this.watches.delete(model.id);
    this.act(
      model,
      this.api.cancelModelDownload(model.id),
      $localize`:@@settings.notice.download_cancelled:Download of the ${this.modelName(model)}:NAME: model cancelled.`,
    );
  }

  /** Enables an installed model so documents can be generated with it. */
  enable(model: GenerationModel): void {
    this.act(
      model,
      this.api.enableModel(model.id),
      $localize`:@@settings.notice.enabled:The ${this.modelName(model)}:NAME: model is enabled.`,
    );
  }

  /** Disables the model and keeps its files. */
  disable(model: GenerationModel): void {
    this.act(
      model,
      this.api.disableModel(model.id),
      $localize`:@@settings.notice.disabled:The ${this.modelName(model)}:NAME: model is disabled. Its files are kept.`,
    );
  }

  /**
   * Asks for confirmation, naming the model and the space it frees, then deletes its files.
   * Cancelling or pressing Escape changes nothing; an API refusal is shown above the models.
   */
  confirmDelete(model: GenerationModel): void {
    const name = this.modelName(model);
    const data: ConfirmDialogData = {
      title: $localize`:@@settings.delete_title:Delete the ${name}:NAME: model?`,
      message: $localize`:@@settings.delete_message:This removes the model files and frees ${this.size(model.totalBytes)}:SIZE: on this computer. Documents already generated stay. You can install the model again later, which downloads it again.`,
      confirmLabel: $localize`:@@settings.delete_confirm:Delete model`,
      danger: true,
    };

    this.dialog
      .open<boolean>(ConfirmDialogComponent, {
        data,
        ariaLabel: $localize`:@@settings.delete_aria:Confirm deletion of the ${name}:NAME: model`,
        disableClose: true,
      })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() =>
        this.act(
          model,
          this.api.deleteModel(model.id),
          $localize`:@@settings.notice.deleted:The ${name}:NAME: model was deleted. ${this.size(model.totalBytes)}:SIZE: freed.`,
        ),
      );
  }

  /** Formats a byte count with the active language's separators. */
  size(bytes: number): string {
    return formatFileSize(bytes);
  }

  /** Whether the kill switch hides every model control. */
  isDisabled(list: GenerationModelList): boolean {
    return list.localGeneration === 'disabled';
  }

  /** Name of the option in the active language; an unknown model keeps the API label. */
  modelName(model: GenerationModel): string {
    switch (model.id) {
      case 'gemma-4-e2b':
        return $localize`:@@settings.model.light:Light`;
      case 'gemma-4-e4b':
        return $localize`:@@settings.model.quality:Quality`;
      default:
        return model.label;
    }
  }

  /** One line on what the option is for, in the active language. */
  modelHelp(model: GenerationModel): string | null {
    switch (model.id) {
      case 'gemma-4-e2b':
        return $localize`:@@settings.model.light_help:The default. Smaller and faster, needs less memory.`;
      case 'gemma-4-e4b':
        return $localize`:@@settings.model.quality_help:Larger and slower, needs more memory. Choose it when Light is not good enough.`;
      default:
        return null;
    }
  }

  /** State word and pill tone of the model. */
  statusLabel(model: GenerationModel): StatusLabel {
    switch (model.state) {
      case 'not_installed':
        return { label: $localize`:@@settings.state.not_installed:Not installed`, tone: 'info' };
      case 'downloading':
        return { label: $localize`:@@settings.state.downloading:Downloading`, tone: 'wait' };
      case 'verifying':
        return { label: $localize`:@@settings.state.verifying:Verifying`, tone: 'wait' };
      case 'installed':
        return { label: $localize`:@@settings.state.installed:Installed`, tone: 'info' };
      case 'enabled':
        return { label: $localize`:@@settings.state.enabled:Enabled`, tone: 'ok' };
      case 'failed':
        return { label: $localize`:@@settings.state.failed:Failed`, tone: 'error' };
    }
  }

  /** Share of the download that arrived, from 0 to 100, from the real byte counts. */
  percent(model: GenerationModel): number {
    if (model.totalBytes <= 0) {
      return 0;
    }
    return Math.min(100, Math.floor((model.downloadedBytes / model.totalBytes) * 100));
  }

  /** Accessible name of the download gauge, with the value. */
  gaugeLabel(model: GenerationModel): string {
    return $localize`:@@settings.gauge_label:Download of the ${this.modelName(model)}:NAME: model, ${this.percent(model)}:PERCENT:%`;
  }

  /** Bytes received of the total, for example “1.2 GB of 3.4 GB”. */
  progressText(model: GenerationModel): string {
    return $localize`:@@settings.progress:${this.size(model.downloadedBytes)}:DONE: of ${this.size(model.totalBytes)}:TOTAL:`;
  }

  /** Text of the install button, naming the model so each button of the list is distinct. */
  installLabel(model: GenerationModel): string {
    return $localize`:@@settings.install_aria:Install the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the Enable button. */
  enableLabel(model: GenerationModel): string {
    return $localize`:@@settings.enable_aria:Enable the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the Disable button. */
  disableLabel(model: GenerationModel): string {
    return $localize`:@@settings.disable_aria:Disable the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the Delete button. */
  deleteLabel(model: GenerationModel): string {
    return $localize`:@@settings.delete_aria_label:Delete the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the Cancel download button. */
  cancelDownloadLabel(model: GenerationModel): string {
    return $localize`:@@settings.cancel_download_aria:Cancel download of the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the Try again button of a failed download. */
  retryLabel(model: GenerationModel): string {
    return $localize`:@@settings.retry_aria:Try again, download of the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the license link, which opens the license outside the app. */
  licenseLinkLabel(model: GenerationModel): string {
    return $localize`:@@settings.license_link_aria:${model.licenseName}:LICENSE:, license of the ${this.modelName(model)}:NAME: model`;
  }

  /** Accessible name of the license and size panel. */
  installPanelLabel(model: GenerationModel): string {
    return $localize`:@@settings.install_panel_aria:Install the ${this.modelName(model)}:NAME: model`;
  }

  /**
   * Why a download failed. English shows the API's reason as sent, because it names the file and
   * the cause; every other language shows a general sentence, since the reason has no translation.
   */
  failureText(model: GenerationModel): string {
    if (model.errorMessage && activeLocale().toLowerCase().startsWith('en')) {
      return model.errorMessage;
    }
    return $localize`:@@settings.failed_text:The download did not finish. The files already received are kept, so trying again continues from there.`;
  }

  /**
   * Runs one model request: disables the model's buttons, replaces the model with the answer, says
   * what happened and follows the download when the answer is still in progress. An API refusal
   * is shown above the models and the model keeps its last known state.
   */
  private act(model: GenerationModel, request: Observable<GenerationModel>, done: string): void {
    this.busyModelId.set(model.id);
    this.errorMessage.set(null);
    this.notice.set(null);
    request
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.busyModelId.set(null)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error));
          return EMPTY;
        }),
      )
      .subscribe((updated) => {
        this.replace(updated);
        this.notice.set(done);
        if (isInProgress(updated)) {
          this.watch(updated.id);
        }
        this.focusModel(updated.id);
      });
  }

  /** Polls a model until its download ends, replacing it in the list after each answer. */
  private watch(modelId: string): void {
    if (this.watches.has(modelId)) {
      return;
    }
    const watch = this.api
      .monitorModel(modelId)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.watches.delete(modelId)),
      )
      .subscribe((model) => this.replace(model));
    this.watches.set(modelId, watch);
  }

  /** Puts a fresh answer in the list in place of the model with the same identifier. */
  private replace(updated: GenerationModel): void {
    this.list.update((current) =>
      current === null
        ? current
        : {
            ...current,
            items: current.items.map((model) => (model.id === updated.id ? updated : model)),
          },
    );
  }

  /**
   * Moves focus to the model's name after an action removed the button that had it. The name is
   * not a tab stop, so keyboard order is unchanged.
   */
  private focusModel(modelId: string): void {
    afterNextRender(() => document.getElementById(`model-title-${modelId}`)?.focus(), {
      injector: this.injector,
    });
  }

  /** Translates a known API error code, else the API message; hides unknown implementation details. */
  private errorText(error: unknown): string {
    return errorText(
      error,
      $localize`:@@settings.error_unreachable:Narrastep could not reach its local service. Check that it is running, then try again.`,
    );
  }
}

/** Whether a download or its verification is still running. */
function isInProgress(model: GenerationModel): boolean {
  return model.state === 'downloading' || model.state === 'verifying';
}
