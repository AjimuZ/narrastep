// Narrastep screen and window source picker: a script-free local page in its own modal window, driven by cancelled navigations (ADR 0014).
'use strict';

const { escapeHtml } = require('./fallback-page.cjs');
const { messagesFor } = require('./messages.cjs');
const { WINDOW_WEB_PREFERENCES } = require('./policy.cjs');

/**
 * Host of the picker page's links. `.invalid` is reserved (RFC 2606) and never resolves; the
 * navigation is cancelled in the main process, so nothing is requested, and the session filter
 * would cancel it anyway. The page has no script and no IPC channel, so a cancelled link
 * navigation is its only way to answer.
 */
const CHOICE_HOST = 'narrastep-picker.invalid';

/** Colors of the fallback page, so the picker matches it. */
const COLORS = Object.freeze({
  background: '#2d2f42',
  text: '#f7e6ea',
  focus: '#f6798c',
  card: '#3b3e57',
});

/** Preferred size of the picker window in pixels. */
const PICKER_SIZE = Object.freeze({ width: 760, height: 560 });

/** Size of the thumbnails requested from `desktopCapturer`, in pixels. */
const THUMBNAIL_SIZE = Object.freeze({ width: 320, height: 180 });

/** Accepts only the inline PNG that `NativeImage.toDataURL()` produces. */
const THUMBNAIL_PATTERN = /^data:image\/png;base64,[A-Za-z0-9+/=]*$/;

/**
 * Returns the `https://` URL of the link that chooses the source at `index` of the list shown
 * by the page, or cancels when `index` is `null`.
 */
function choiceUrl(index) {
  return `https://${CHOICE_HOST}/${index === null ? 'cancel' : `choose/${index}`}`;
}

/**
 * Parses a navigation URL of the picker page. Returns `{ type: 'cancel' }`,
 * `{ type: 'choose', index }` with a non-negative integer, or `null` for any other value, so a
 * URL that is not one of the page's own links is never treated as an answer.
 */
function parseChoiceUrl(url) {
  if (typeof url !== 'string') {
    return null;
  }
  const match =
    /^https:\/\/narrastep-picker\.invalid\/(?:(cancel)|choose\/(0|[1-9][0-9]{0,3}))$/.exec(url);
  if (match === null) {
    return null;
  }
  return match[1] === 'cancel' ? { type: 'cancel' } : { type: 'choose', index: Number(match[2]) };
}

/** Returns one `<li>` link for `source` at `index`: thumbnail when it is an inline PNG, and name. */
function sourceItem(source, index) {
  const thumbnail = THUMBNAIL_PATTERN.test(source.thumbnailUrl)
    ? `<img src="${escapeHtml(source.thumbnailUrl)}" alt="" width="160" height="90">`
    : '<span class="blank" aria-hidden="true"></span>';
  return `<li><a href="${choiceUrl(index)}">${thumbnail}<span>${escapeHtml(source.name)}</span></a></li>`;
}

/**
 * Returns a `data:text/html` URL of the picker page in `language` (`'en'` or `'it'`) with
 * `messages` from `messagesFor(language)`. `sources` is a list of `{ id, name, thumbnailUrl }`;
 * the link of the source at position `i` answers `choose/i`, and the page lists screens (ids
 * starting with `screen:`) before windows. Names are escaped; thumbnails must be inline PNG data
 * URLs, otherwise a blank box is shown. The page has no script and a policy that blocks every
 * resource except inline images and styles.
 */
function buildPickerPageUrl(language, messages, sources) {
  const groups = [
    [messages.pickerScreens, (source) => source.id.startsWith('screen:')],
    [messages.pickerWindows, (source) => !source.id.startsWith('screen:')],
  ];
  const sections = groups
    .map(([title, belongs]) => {
      const items = sources
        .map((source, index) => (belongs(source) ? sourceItem(source, index) : ''))
        .filter((item) => item !== '');
      return items.length === 0
        ? ''
        : `<section>\n<h2>${escapeHtml(title)}</h2>\n<ul>\n${items.join('\n')}\n</ul>\n</section>`;
    })
    .filter((section) => section !== '');
  const list =
    sections.length === 0 ? `<p>${escapeHtml(messages.pickerEmpty)}</p>` : sections.join('\n');
  const html = `<!doctype html>
<html lang="${language === 'it' ? 'it' : 'en'}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(messages.pickerTitle)}</title>
<style>
body { margin: 0; background: ${COLORS.background}; color: ${COLORS.text}; font: 1rem/1.5 system-ui, sans-serif; }
main { max-width: 44rem; margin: 0 auto; padding: 1.5rem; }
h1 { font-size: 1.5rem; margin: 0 0 0.5rem; }
h2 { font-size: 1.125rem; margin: 1.5rem 0 0.5rem; }
ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: 0.75rem; }
li a { display: flex; flex-direction: column; gap: 0.5rem; height: 100%; box-sizing: border-box; padding: 0.5rem; background: ${COLORS.card}; border: 2px solid transparent; color: inherit; text-decoration: none; }
li a:hover { border-color: ${COLORS.text}; }
li img, li .blank { width: 100%; height: auto; aspect-ratio: 16 / 9; background: ${COLORS.background}; object-fit: contain; }
li span:last-child { overflow-wrap: anywhere; font-size: 0.875rem; }
a:focus-visible { outline: 3px solid ${COLORS.focus}; outline-offset: 3px; }
p.actions { margin: 1.5rem 0 0; }
p.actions a { color: inherit; font-weight: 700; }
</style>
</head>
<body>
<main>
<h1>${escapeHtml(messages.pickerTitle)}</h1>
<p>${escapeHtml(messages.pickerBody)}</p>
${list}
<p class="actions"><a href="${choiceUrl(null)}">${escapeHtml(messages.pickerCancel)}</a></p>
</main>
</body>
</html>
`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

/**
 * Creates the picker for the Electron modules `BrowserWindow` and `desktopCapturer`.
 * `getParent()` returns the window the picker is modal to (or `null`) and `getLanguage()` the
 * language of the shell's texts. Returns `{ pickSource }`.
 *
 * `pickSource()` resolves to the chosen `DesktopCapturerSource`, or to `null` when the person
 * cancels, closes the picker, another picker is already open, or the sources cannot be listed.
 * It never rejects. It opens one picker window that passes through the same web contents guards
 * as every window (the `web-contents-created` event), shows the screens and windows from
 * `desktopCapturer`, and closes before it resolves. The choice is the index into the list the
 * main process holds, never an id taken from a URL.
 */
function createSourcePicker({ BrowserWindow, desktopCapturer, getParent, getLanguage }) {
  let open = false;

  async function pickSource() {
    if (open) {
      return null;
    }
    open = true;
    try {
      const sources = await desktopCapturer.getSources({
        types: ['screen', 'window'],
        thumbnailSize: { ...THUMBNAIL_SIZE },
        fetchWindowIcons: false,
      });
      const shown = sources.map((source) => ({
        id: String(source.id),
        name: String(source.name),
        thumbnailUrl: source.thumbnail?.isEmpty?.() ? '' : (source.thumbnail?.toDataURL() ?? ''),
      }));
      const language = getLanguage();
      const parent = getParent();
      const window = new BrowserWindow({
        title: 'Narrastep',
        width: PICKER_SIZE.width,
        height: PICKER_SIZE.height,
        minWidth: 320,
        minHeight: 320,
        show: false,
        parent: parent ?? undefined,
        modal: Boolean(parent),
        minimizable: false,
        maximizable: false,
        backgroundColor: COLORS.background,
        autoHideMenuBar: true,
        webPreferences: { ...WINDOW_WEB_PREFERENCES },
      });
      return await new Promise((resolve) => {
        let settled = false;
        const settle = (value) => {
          if (settled) {
            return;
          }
          settled = true;
          resolve(value);
          if (!window.isDestroyed()) {
            window.close();
          }
        };
        const answer = (event) => {
          const choice = parseChoiceUrl(event.url);
          if (choice === null) {
            return;
          }
          event.preventDefault();
          settle(choice.type === 'choose' ? (sources[choice.index] ?? null) : null);
        };
        window.once('ready-to-show', () => window.show());
        window.on('closed', () => settle(null));
        window.webContents.on('will-frame-navigate', answer);
        window.webContents
          .loadURL(buildPickerPageUrl(language, messagesFor(language), shown))
          .catch(() => settle(null));
      });
    } catch {
      return null;
    } finally {
      open = false;
    }
  }

  return { pickSource };
}

module.exports = {
  CHOICE_HOST,
  buildPickerPageUrl,
  choiceUrl,
  createSourcePicker,
  parseChoiceUrl,
};
