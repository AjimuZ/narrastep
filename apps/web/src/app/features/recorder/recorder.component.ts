// Recorder screen: source checks, live microphone level, recording with pause and stop, then save with an optional name.
import { Dialog, DialogModule } from '@angular/cdk/dialog';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  HostListener,
  OnInit,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  EMPTY,
  Observable,
  catchError,
  filter,
  finalize,
  firstValueFrom,
  forkJoin,
  map,
  of,
  switchMap,
} from 'rxjs';

import { Project, Recording } from '../../core/api.models';
import { errorText } from '../../core/i18n/error-text';
import { formatFileSize } from '../../core/i18n/format';
import { LanguageService } from '../../core/i18n/language.service';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../shared/dialogs/confirm-dialog.component';
import { StatusLabel, recordingStatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  SegmentedGaugeComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';
import {
  DEFAULT_MAX_FRAME_RATE,
  MICROPHONE_MIME_TYPES,
  SCREEN_MIME_TYPES_VIDEO_ONLY,
  SCREEN_MIME_TYPES_WITH_AUDIO,
  ScreenSourceKind,
  captureSupported,
  desktopWindowSystem,
  listMicrophones,
  nextShownLevel,
  pickMimeType,
  requestMicrophone,
  requestScreen,
  screenSourceKind,
  stopStreams,
  watchLevel,
} from './media-capture';
import { EngineTrack, RecordingEngine } from './recording-engine';

/**
 * Screen phases. `checks` picks the sources; `starting` opens the session; `recording` covers
 * recording and paused (see the engine state); `stopping` waits for the last chunks of the
 * recorders; `save` asks for the name; `saving` waits for the server to join the video; `saved`
 * confirms. `loading` reads an interrupted recording opened from Projects; `gone` means it no
 * longer exists or was already saved.
 */
export type RecorderPhase =
  | 'checks'
  | 'starting'
  | 'recording'
  | 'stopping'
  | 'save'
  | 'saving'
  | 'saved'
  | 'loading'
  | 'gone';

/** Microphone state in the checks: not asked yet, being asked, on, or why it is unavailable. */
export type MicrophoneState = 'off' | 'asking' | 'on' | 'blocked' | 'missing' | 'failed';

/** How often the timer on the screen is refreshed while recording. */
const TICK_MS = 250;

/**
 * Elapsed time as `MM:SS`, or `H:MM:SS` from one hour. Negative or non-finite input reads as 0.
 */
export function formatElapsed(ms: number): string {
  const totalSeconds = Number.isFinite(ms) && ms > 0 ? Math.floor(ms / 1000) : 0;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mmss = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  return hours > 0 ? `${hours}:${mmss}` : mmss;
}

/**
 * Name a new recording gets: `Recording YYYY-MM-DD HH:MM` in the interface language and the
 * local time (docs/RECORDING.md). Sent when the session is created, so the project has it even
 * when the recording is never saved.
 */
export function defaultRecordingName(now: Date = new Date()): string {
  const pad = (value: number) => value.toString().padStart(2, '0');
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
  return $localize`:@@recorder.default_name:Recording ${stamp}:DATE:`;
}

/**
 * The recorder (`/record`, docs/UI.md and docs/RECORDING.md). The user chooses a screen, allows
 * the microphone and sees whether system audio is shared and how loud the microphone is; Start
 * recording opens a session and runs a `RecordingEngine` that uploads chunks while recording.
 * Stop shows one optional name field with Save and Discard. Saving completes the session on the
 * server and never starts processing. `/record?recording={id}` opens an interrupted session to
 * save what was recorded or discard it.
 *
 * Leaving the screen, switching the language or closing the tab while recording or uploading asks
 * first; leaving stops the recording and keeps the uploaded chunks for a later save.
 */
@Component({
  selector: 'ns-recorder',
  imports: [
    ActivityLineComponent,
    DialogModule,
    PixelIconComponent,
    RouterLink,
    SegmentedGaugeComponent,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './recorder.component.html',
  styleUrl: './recorder.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RecorderComponent implements OnInit {
  readonly supported = captureSupported();
  /** Which system the desktop window runs on, or null in a browser; shapes the system audio notes. */
  readonly desktopSystem = desktopWindowSystem();
  readonly saveTitle = $localize`:@@recorder.save_title:Save recording`;
  readonly phase = signal<RecorderPhase>('checks');

  readonly screenStream = signal<MediaStream | null>(null);
  readonly screenKind = signal<ScreenSourceKind>('unknown');
  readonly screenError = signal<string | null>(null);
  /** True when the shared surface came with an audio track (system or tab audio). */
  readonly hasSystemAudio = computed(() => (this.screenStream()?.getAudioTracks().length ?? 0) > 0);

  readonly microphoneStream = signal<MediaStream | null>(null);
  readonly microphoneState = signal<MicrophoneState>('off');
  readonly microphones = signal<MediaDeviceInfo[]>([]);
  readonly microphoneId = signal<string | null>(null);
  /** Live microphone level from 0 to 100; analysed in the page only. */
  readonly level = signal(0);

  /** Start needs a screen and something to transcribe: system audio or the microphone. */
  readonly canStart = computed(
    () =>
      this.phase() === 'checks' &&
      this.screenStream() !== null &&
      (this.hasSystemAudio() || this.microphoneState() === 'on'),
  );

  readonly session = signal<Recording | null>(null);
  readonly projectName = signal('');
  readonly engine = signal<RecordingEngine | null>(null);
  readonly elapsedMs = signal(0);
  /** True when this screen opened an interrupted session instead of recording it. */
  readonly resumed = signal(false);
  /** Whether the recording ran with these sources; frozen at start for the save summary. */
  readonly recordedSystemAudio = signal(false);
  readonly recordedMicrophone = signal(false);

  readonly saveName = signal('');
  readonly requestError = signal<string | null>(null);
  readonly saveError = signal<string | null>(null);
  /** True after the server refused the save because parts are missing: offer the gap save. */
  readonly incomplete = signal(false);
  readonly savedProject = signal<Project | null>(null);
  readonly discarding = signal(false);

  readonly paused = computed(() => this.engine()?.state() === 'paused');
  readonly pendingChunks = computed(() => this.engine()?.pendingChunks() ?? 0);
  readonly retrying = computed(() => this.engine()?.retrying() ?? false);
  readonly uploadedBytes = computed(
    () => this.engine()?.uploadedBytes() ?? this.session()?.storedBytes ?? 0,
  );

  private readonly api = inject(ApiService);
  private readonly dialog = inject(Dialog);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly language = inject(LanguageService);
  private stopLevel: (() => void) | null = null;
  private ticker: ReturnType<typeof setInterval> | null = null;

  constructor() {
    // A chunk the server cannot store (full disk, size limit) ends the recording: later chunks
    // would fail the same way. What was stored can still be saved.
    effect(() => {
      const error = this.engine()?.fatalError();
      if (error && this.phase() === 'recording') {
        this.saveError.set(
          errorText(
            error,
            $localize`:@@recorder.error_upload:The recording stopped because a part could not be stored. Save what was recorded, or discard it.`,
          ),
        );
        void this.stop();
      }
    });
    this.destroyRef.onDestroy(this.language.registerLeaveCheck(() => this.canDeactivate()));
    this.destroyRef.onDestroy(() => this.release());
  }

  /** Opens an interrupted session from `?recording=` (Projects), else starts with the checks. */
  ngOnInit(): void {
    const recordingId = this.route.snapshot.queryParamMap.get('recording');
    if (!recordingId) {
      return;
    }
    this.resumed.set(true);
    this.phase.set('loading');
    this.api
      .getRecording(recordingId)
      .pipe(
        switchMap((recording) =>
          forkJoin({ recording: of(recording), project: this.api.getProject(recording.projectId) }),
        ),
        takeUntilDestroyed(this.destroyRef),
        catchError((error: unknown) => {
          this.requestError.set(
            errorText(
              error,
              $localize`:@@recorder.error_open:This recording could not be opened. Go back to projects and try again.`,
            ),
          );
          this.phase.set('gone');
          return EMPTY;
        }),
      )
      .subscribe(({ recording, project }) => {
        this.session.set(recording);
        this.projectName.set(project.name);
        if (recording.state !== 'recording') {
          this.requestError.set(
            $localize`:@@recorder.already_saved:This recording is already saved. Open it from Projects.`,
          );
          this.phase.set('gone');
          return;
        }
        this.phase.set('save');
      });
  }

  /** Asks for a screen, window or tab; replaces a previous choice. Never throws. */
  async chooseScreen(): Promise<void> {
    this.screenError.set(null);
    try {
      const stream = await requestScreen(DEFAULT_MAX_FRAME_RATE);
      stopStreams(this.screenStream());
      this.screenStream.set(stream);
      this.screenKind.set(screenSourceKind(stream));
      // The browser's own "Stop sharing" button ends the video track.
      stream.getVideoTracks()[0]?.addEventListener('ended', () => this.onScreenEnded(stream));
    } catch (error) {
      this.screenError.set(screenErrorText(error));
    }
  }

  /** Turns the microphone on (the default or `deviceId`) and starts the level gauge. Never throws. */
  async useMicrophone(deviceId: string | null = null): Promise<void> {
    this.microphoneState.set('asking');
    try {
      const stream = await requestMicrophone(deviceId);
      this.replaceMicrophone(stream);
      this.microphoneState.set('on');
      this.microphoneId.set(stream.getAudioTracks()[0]?.getSettings().deviceId ?? deviceId);
      this.microphones.set(await listMicrophones().catch(() => []));
    } catch (error) {
      this.replaceMicrophone(null);
      this.microphoneState.set(microphoneFailure(error));
    }
  }

  /** Switches to the microphone chosen in the device list. */
  selectMicrophone(event: Event): void {
    void this.useMicrophone((event.target as HTMLSelectElement).value || null);
  }

  /**
   * Opens a session named after the date and starts both recorders. On failure the screen stays in
   * the checks with an error, and a session already opened is discarded so no empty project is left.
   */
  async start(): Promise<void> {
    const screen = this.screenStream();
    if (!this.canStart() || !screen) {
      return;
    }
    this.phase.set('starting');
    this.requestError.set(null);
    this.saveError.set(null);
    this.incomplete.set(false);
    const name = defaultRecordingName();
    let recording: Recording;
    try {
      recording = await firstValueFrom(this.api.createRecording({ name }));
    } catch (error) {
      this.requestError.set(
        errorText(
          error,
          $localize`:@@recorder.error_start:The recording could not start. Check that Narrastep is running, then try again.`,
        ),
      );
      this.phase.set('checks');
      return;
    }
    try {
      const engine = new RecordingEngine(
        await this.engineTracks(screen, recording),
        recording.capture.chunkSeconds * 1000,
        (track, sequence, chunk, offset) =>
          firstValueFrom(
            this.api
              .uploadRecordingChunk(recording.id, track, sequence, chunk, offset)
              // A duplicate means an earlier attempt was stored but its answer was lost, so
              // its bytes were never counted.
              .pipe(map((ack) => ack.sizeBytes)),
          ),
      );
      this.session.set(recording);
      this.projectName.set(name);
      this.recordedSystemAudio.set(this.hasSystemAudio());
      this.recordedMicrophone.set(this.microphoneState() === 'on');
      this.engine.set(engine);
      engine.start();
      this.phase.set('recording');
      this.startTicker();
    } catch {
      this.api.discardRecording(recording.id).subscribe({ error: () => undefined });
      this.requestError.set(
        $localize`:@@recorder.error_format:This browser cannot record in a format Narrastep accepts. Use the desktop window, Chrome or Edge.`,
      );
      this.phase.set('checks');
    }
  }

  /** Pauses or resumes both tracks together. */
  togglePause(): void {
    const engine = this.engine();
    if (!engine) {
      return;
    }
    if (engine.state() === 'paused') {
      engine.resume();
    } else {
      engine.pause();
    }
    this.elapsedMs.set(engine.elapsedMs());
  }

  /** Stops both recorders, releases the screen and microphone and shows the save step. */
  async stop(): Promise<void> {
    const engine = this.engine();
    if (!engine || this.phase() !== 'recording') {
      return;
    }
    this.phase.set('stopping');
    await engine.stop();
    this.stopTicker();
    this.elapsedMs.set(engine.elapsedMs());
    this.releaseDevices();
    this.phase.set('save');
  }

  /** Name field input; an empty field keeps the date name. */
  updateSaveName(event: Event): void {
    this.saveName.set((event.target as HTMLInputElement).value);
  }

  /**
   * Completes the session into the project's source video with the typed name, if any. Processing
   * is never started. `stopAtGap` saves each track up to its first missing part; it is offered after
   * the server reports missing parts, and is the only save of an interrupted recording.
   */
  save(stopAtGap = false): void {
    const recording = this.session();
    if (!recording || this.pendingChunks() > 0 || this.phase() !== 'save') {
      return;
    }
    const name = this.saveName().trim();
    this.phase.set('saving');
    this.saveError.set(null);
    this.api
      .completeRecording(recording.id, { ...(name ? { name } : {}), stopAtGap })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((error: unknown) => {
          this.incomplete.set(
            error instanceof NarrastepApiError && error.code === 'RECORDING_INCOMPLETE',
          );
          this.saveError.set(saveErrorText(error));
          this.phase.set('save');
          return EMPTY;
        }),
      )
      .subscribe((project) => {
        this.savedProject.set(project);
        this.engine.set(null);
        this.phase.set('saved');
      });
  }

  /** After confirmation, deletes the session, its parts and its project, then opens Projects. */
  discard(): void {
    const recording = this.session();
    if (!recording) {
      return;
    }
    const data: ConfirmDialogData = {
      title: $localize`:@@recorder.discard_title:Discard this recording?`,
      message: $localize`:@@recorder.discard_message:The recording and its project are deleted from this computer. This cannot be undone.`,
      confirmLabel: $localize`:@@recorder.discard:Discard`,
      danger: true,
    };
    this.dialog
      .open<boolean>(ConfirmDialogComponent, { data, disableClose: true })
      .closed.pipe(
        filter((confirmed): confirmed is true => confirmed === true),
        switchMap(() => {
          this.discarding.set(true);
          this.saveError.set(null);
          this.engine()?.abort();
          return this.api.discardRecording(recording.id).pipe(
            finalize(() => this.discarding.set(false)),
            catchError((error: unknown) => {
              this.saveError.set(
                errorText(
                  error,
                  $localize`:@@recorder.error_discard:The recording could not be discarded. Try again.`,
                ),
              );
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.engine.set(null);
        void this.router.navigate(['/projects']);
      });
  }

  /** Clears the finished recording and returns to the checks for a new one. */
  recordAgain(): void {
    this.release();
    this.session.set(null);
    this.savedProject.set(null);
    this.engine.set(null);
    this.saveName.set('');
    this.saveError.set(null);
    this.requestError.set(null);
    this.incomplete.set(false);
    this.elapsedMs.set(0);
    this.screenStream.set(null);
    this.microphoneStream.set(null);
    this.microphoneState.set('off');
    this.level.set(0);
    this.phase.set('checks');
  }

  /**
   * Route and language-switch guard. While recording or uploading it asks first; leaving then
   * stops the recording and gives up parts not yet uploaded. Otherwise leaving is allowed.
   */
  canDeactivate(): boolean | Observable<boolean> {
    if (!this.isBusy()) {
      return true;
    }
    const data: ConfirmDialogData = {
      title: $localize`:@@recorder.leave_title:Stop recording and leave?`,
      message: $localize`:@@recorder.leave_message:The recording stops. Parts already uploaded are kept, and the project appears in Projects as Recording interrupted.`,
      confirmLabel: $localize`:@@recorder.leave_confirm:Stop and leave`,
      danger: true,
    };
    return this.dialog
      .open<boolean>(ConfirmDialogComponent, { data, disableClose: true })
      .closed.pipe(
        map((confirmed) => confirmed === true),
        map((leave) => {
          if (leave) {
            this.release();
          }
          return leave;
        }),
      );
  }

  /** Asks the browser to confirm closing or reloading the tab while recording or uploading. */
  @HostListener('window:beforeunload', ['$event'])
  protected onBeforeUnload(event: BeforeUnloadEvent): void {
    if (this.isBusy()) {
      event.preventDefault();
      // Older Chromium versions still need `returnValue` to show the prompt.
      event.returnValue = '';
    }
  }

  /** Word and tone of the recording pill. */
  stateLabel(): StatusLabel {
    if (this.resumed()) {
      return recordingStatusLabel(this.session()?.interrupted ?? true);
    }
    return this.paused()
      ? { label: $localize`:@@recorder.state.paused:Paused`, tone: 'info' }
      : recordingStatusLabel(false);
  }

  /** Word for the shared surface. */
  screenLabel(): string {
    if (!this.screenStream()) {
      return $localize`:@@recorder.screen.none:Not chosen`;
    }
    switch (this.screenKind()) {
      case 'monitor':
        return $localize`:@@recorder.screen.monitor:Entire screen`;
      case 'window':
        return $localize`:@@recorder.screen.window:Window`;
      case 'browser':
        return $localize`:@@recorder.screen.browser:Browser tab`;
      default:
        return $localize`:@@recorder.screen.shared:Shared`;
    }
  }

  /** Word for the microphone state in the checks. */
  microphoneLabel(): string {
    switch (this.microphoneState()) {
      case 'off':
        return $localize`:@@recorder.mic.off:Off`;
      case 'asking':
        return $localize`:@@recorder.mic.asking:Waiting for permission`;
      case 'on':
        return $localize`:@@recorder.mic.on:On`;
      case 'blocked':
        return $localize`:@@recorder.mic.blocked:Blocked`;
      case 'missing':
        return $localize`:@@recorder.mic.missing:Not found`;
      case 'failed':
        return $localize`:@@recorder.mic.failed:Not available`;
    }
  }

  /** Accessible name of the level gauge, with the value. */
  levelLabel(): string {
    return $localize`:@@recorder.level_label:Microphone level ${this.level()}:LEVEL:%`;
  }

  /** Name shown for a microphone in the device list; browsers hide labels until allowed. */
  microphoneName(device: MediaDeviceInfo, index: number): string {
    return device.label || $localize`:@@recorder.mic.unnamed:Microphone ${index + 1}:NUMBER:`;
  }

  /** Recorded length for the timer and the save summary. */
  elapsedText(): string {
    return formatElapsed(this.elapsedMs());
  }

  /** Uploaded size for the recording and save windows. */
  uploadedText(): string {
    return formatFileSize(this.uploadedBytes());
  }

  /** Placeholder and help of the name field: the name the project keeps when it stays empty. */
  nameHelp(): string {
    return $localize`:@@recorder.name_help:Leave empty to keep “${this.projectName()}:NAME:”.`;
  }

  /** Sentence for the parts still uploading after stop. */
  pendingText(): string {
    return $localize`:@@recorder.pending:Uploading the last parts: ${this.pendingChunks()}:COUNT: left.`;
  }

  /** Confirmation sentence of the saved window. */
  savedText(): string {
    return $localize`:@@recorder.saved_text:“${this.savedProject()?.name ?? ''}:NAME:” is in Projects as Not processed. Process it when you are ready.`;
  }

  private isBusy(): boolean {
    return (
      this.phase() === 'starting' ||
      this.phase() === 'recording' ||
      this.phase() === 'stopping' ||
      (this.engine()?.busy() ?? false)
    );
  }

  /**
   * Tracks for the engine: the screen (video plus system audio when shared) and the microphone
   * when on. Applies the session's frame-rate limit to the screen; a browser that refuses keeps its
   * rate. Throws when no recordable format exists.
   */
  private async engineTracks(screen: MediaStream, recording: Recording): Promise<EngineTrack[]> {
    const video = screen.getVideoTracks()[0];
    const maxFrameRate = recording.capture.maxFrameRate;
    if (video && maxFrameRate !== DEFAULT_MAX_FRAME_RATE) {
      await video
        .applyConstraints({ frameRate: { ideal: maxFrameRate, max: maxFrameRate } })
        .catch(() => undefined);
    }
    const screenType = pickMimeType(
      this.hasSystemAudio() ? SCREEN_MIME_TYPES_WITH_AUDIO : SCREEN_MIME_TYPES_VIDEO_ONLY,
    );
    if (!screenType) {
      throw new Error('No supported screen recording format.');
    }
    const tracks: EngineTrack[] = [
      {
        track: 'screen',
        stream: screen,
        mimeType: screenType,
        videoBitsPerSecond: recording.capture.videoBitsPerSecond,
      },
    ];
    const microphone = this.microphoneStream();
    if (microphone && this.microphoneState() === 'on') {
      const microphoneType = pickMimeType(MICROPHONE_MIME_TYPES);
      if (!microphoneType) {
        throw new Error('No supported microphone recording format.');
      }
      tracks.push({ track: 'microphone', stream: microphone, mimeType: microphoneType });
    }
    return tracks;
  }

  /** The shared surface ended: stop a running recording, or forget the choice in the checks. */
  private onScreenEnded(stream: MediaStream): void {
    if (this.screenStream() !== stream) {
      return;
    }
    if (this.phase() === 'recording') {
      void this.stop();
      return;
    }
    if (this.phase() === 'checks') {
      this.screenStream.set(null);
      this.screenError.set(
        $localize`:@@recorder.screen_ended:Screen sharing ended. Choose the screen again.`,
      );
    }
  }

  private replaceMicrophone(stream: MediaStream | null): void {
    this.stopLevel?.();
    this.stopLevel = null;
    stopStreams(this.microphoneStream());
    this.microphoneStream.set(stream);
    this.level.set(0);
    if (stream) {
      this.stopLevel = watchLevel(stream, (percent) =>
        this.level.update((previous) => nextShownLevel(previous, percent)),
      );
    }
  }

  private startTicker(): void {
    this.stopTicker();
    this.ticker = setInterval(() => this.elapsedMs.set(this.engine()?.elapsedMs() ?? 0), TICK_MS);
  }

  private stopTicker(): void {
    if (this.ticker !== null) {
      clearInterval(this.ticker);
      this.ticker = null;
    }
  }

  /** Stops the level gauge and releases the screen share and the microphone. */
  private releaseDevices(): void {
    this.stopLevel?.();
    this.stopLevel = null;
    this.level.set(0);
    stopStreams(this.screenStream(), this.microphoneStream());
  }

  /** Stops everything this screen holds: recorders, uploads, timer and devices. */
  private release(): void {
    this.engine()?.abort();
    this.stopTicker();
    this.releaseDevices();
  }
}

/** Translated reason a screen request failed; the user cancelling is not an error worth alarm. */
function screenErrorText(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return $localize`:@@recorder.screen_denied:No screen was shared. Choose screen to try again.`;
  }
  return $localize`:@@recorder.screen_failed:The screen could not be captured. Try again, or use the desktop window, Chrome or Edge.`;
}

/** Microphone state for a failed request. */
function microphoneFailure(error: unknown): MicrophoneState {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'blocked';
    if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'missing';
  }
  return 'failed';
}

/**
 * Text for a failed save. A join failure gets a recorder wording, because the shared
 * `FFMPEG_FAILED` text speaks about processing; every other code uses the shared translation.
 */
function saveErrorText(error: unknown): string {
  if (error instanceof NarrastepApiError && error.code === 'FFMPEG_FAILED') {
    return $localize`:@@recorder.error_join:The recorded parts could not be joined into a video. Try to save again, or discard the recording.`;
  }
  return errorText(
    error,
    $localize`:@@recorder.error_save:The recording could not be saved. Check that Narrastep is running, then try again.`,
  );
}
