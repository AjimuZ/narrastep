// Unit tests of the screen and window source picker with in-memory Electron stand-ins.
'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { MESSAGES, messagesFor } = require('../src/messages.cjs');
const { WINDOW_WEB_PREFERENCES } = require('../src/policy.cjs');
const {
  buildPickerPageUrl,
  choiceUrl,
  createSourcePicker,
  parseChoiceUrl,
} = require('../src/source-picker.cjs');
const { FakeEvent, createFakeElectron } = require('./support/fake-electron.cjs');

const PNG = 'data:image/png;base64,iVBORw0KGgo=';

/** Returns a `desktopCapturer` source stand-in with an inline thumbnail. */
function source(id, name, thumbnail = PNG) {
  return { id, name, thumbnail: { isEmpty: () => thumbnail === '', toDataURL: () => thumbnail } };
}

const SCREEN = source('screen:0:0', 'Screen 1');
const WINDOW = source('window:42:0', 'Notes');

/** Decodes the HTML of a `data:text/html` URL. */
function decodePage(url) {
  return decodeURIComponent(url.slice(url.indexOf(',') + 1));
}

/** Starts a pick on fakes and waits until the picker window has loaded its page. */
async function startPick({ language = 'en', parent = null, ...fakeOptions } = {}) {
  const fake = createFakeElectron(fakeOptions);
  const { pickSource } = createSourcePicker({
    BrowserWindow: fake.electron.BrowserWindow,
    desktopCapturer: fake.electron.desktopCapturer,
    getParent: () => parent,
    getLanguage: () => language,
  });
  const result = pickSource();
  await new Promise(setImmediate);
  return {
    ...fake,
    desktopCapturer: fake.electron.desktopCapturer,
    pickSource,
    result,
    picker: fake.windows.at(-1),
  };
}

/** Emits a navigation of the picker page to `url` and returns whether it was cancelled. */
function navigate(picker, url, eventName = 'will-frame-navigate') {
  const event = new FakeEvent({ url, isMainFrame: true });
  picker.webContents.emit(eventName, event);
  return event.defaultPrevented;
}

describe('picker page', () => {
  const sources = [
    { id: 'screen:0:0', name: 'Screen 1', thumbnailUrl: PNG },
    { id: 'window:42:0', name: '<img src=x onerror=alert(1)> & "Notes"', thumbnailUrl: '' },
  ];

  it('has no script, blocks every resource but inline images and styles, and links by index', () => {
    const url = buildPickerPageUrl('en', MESSAGES.en, sources);
    assert.ok(url.startsWith('data:text/html;charset=utf-8,'));
    const html = decodePage(url);
    assert.ok(!/<script/i.test(html));
    assert.ok(!/\son[a-z]+=/i.test(html.replace(/&lt;img[^>]*&gt;/g, '')));
    assert.ok(!html.includes('<form'));
    assert.ok(html.includes("default-src 'none'; img-src data:; style-src 'unsafe-inline'"));
    assert.ok(html.includes(`href="${choiceUrl(0)}"`));
    assert.ok(html.includes(`href="${choiceUrl(1)}"`));
    assert.ok(html.includes(`href="${choiceUrl(null)}"`));
    assert.ok(html.includes(`src="${PNG}"`));
  });

  it('escapes source names and lists screens before windows', () => {
    const html = decodePage(buildPickerPageUrl('en', MESSAGES.en, [sources[1], sources[0]]));
    assert.ok(!html.includes('<img src=x'));
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; &amp; &quot;Notes&quot;'));
    assert.ok(html.indexOf(MESSAGES.en.pickerScreens) < html.indexOf(MESSAGES.en.pickerWindows));
    assert.ok(html.indexOf('Screen 1') < html.indexOf('&lt;img'));
    // The link keeps the position in the list the main process holds, not the position on screen.
    assert.ok(html.indexOf(choiceUrl(1)) < html.indexOf(choiceUrl(0)));
  });

  it('shows a blank box instead of a thumbnail that is not an inline PNG', () => {
    const hostile = [{ id: 'screen:0:0', name: 'S', thumbnailUrl: 'https://example.com/a.png' }];
    const html = decodePage(buildPickerPageUrl('en', MESSAGES.en, hostile));
    assert.ok(!html.includes('example.com'));
    assert.ok(html.includes('class="blank"'));
  });

  it('is written in English or Italian with the matching lang attribute', () => {
    const en = decodePage(buildPickerPageUrl('en', messagesFor('en'), sources));
    const it_ = decodePage(buildPickerPageUrl('it', messagesFor('it'), sources));
    assert.match(en, /<html lang="en">/);
    assert.ok(en.includes(MESSAGES.en.pickerTitle) && en.includes(MESSAGES.en.pickerCancel));
    assert.match(it_, /<html lang="it">/);
    for (const key of [
      'pickerTitle',
      'pickerBody',
      'pickerScreens',
      'pickerWindows',
      'pickerCancel',
    ]) {
      assert.ok(it_.includes(MESSAGES.it[key]), key);
      assert.notEqual(MESSAGES.it[key], MESSAGES.en[key], key);
    }
  });

  it('says so when nothing can be captured', () => {
    const html = decodePage(buildPickerPageUrl('it', MESSAGES.it, []));
    assert.ok(html.includes(MESSAGES.it.pickerEmpty));
    assert.ok(html.includes(`href="${choiceUrl(null)}"`));
  });
});

describe('choice URLs', () => {
  it('round-trips an index and a cancellation', () => {
    assert.deepEqual(parseChoiceUrl(choiceUrl(0)), { type: 'choose', index: 0 });
    assert.deepEqual(parseChoiceUrl(choiceUrl(12)), { type: 'choose', index: 12 });
    assert.deepEqual(parseChoiceUrl(choiceUrl(null)), { type: 'cancel' });
  });

  it('rejects every URL that is not one of the page links', () => {
    for (const value of [
      'https://narrastep-picker.invalid/choose/-1',
      'https://narrastep-picker.invalid/choose/01',
      'https://narrastep-picker.invalid/choose/1?x=1',
      'https://narrastep-picker.invalid/choose/screen:0:0',
      'https://narrastep-picker.invalid/choose/10000',
      'http://narrastep-picker.invalid/cancel',
      'https://example.com/cancel',
      'http://127.0.0.1:4200/',
      '',
      null,
      undefined,
      42,
    ]) {
      assert.equal(parseChoiceUrl(value), null, String(value));
    }
  });
});

describe('createSourcePicker', () => {
  it('lists screens and windows, then opens a script-free modal window with isolated preferences', async () => {
    const parent = { id: 'main' };
    const { desktopCapturer, picker, result, calls } = await startPick({
      sources: [SCREEN, WINDOW],
      parent,
    });
    assert.deepEqual(desktopCapturer.requests[0].types, ['screen', 'window']);
    assert.equal(desktopCapturer.requests[0].fetchWindowIcons, false);
    assert.deepEqual(picker.options.webPreferences, { ...WINDOW_WEB_PREFERENCES });
    assert.equal(picker.options.parent, parent);
    assert.equal(picker.options.modal, true);
    assert.equal(picker.options.show, false);
    assert.equal(picker.webContents.loads.length, 1);
    assert.ok(!decodePage(picker.webContents.loads[0]).includes('<script'));
    assert.ok(calls.indexOf('desktopCapturer.getSources') < calls.indexOf('BrowserWindow.new'));
    picker.emit('ready-to-show');
    assert.equal(picker.state.visible, true);
    navigate(picker, choiceUrl(null));
    assert.equal(await result, null);
  });

  it('resolves to the chosen source, cancels the navigation and closes the window', async () => {
    const { picker, result } = await startPick({ sources: [SCREEN, WINDOW] });
    assert.equal(navigate(picker, choiceUrl(1)), true);
    assert.equal(await result, WINDOW);
    assert.equal(picker.state.destroyed, true);
  });

  it('resolves to null when the person cancels', async () => {
    const { picker, result } = await startPick({ sources: [SCREEN] });
    assert.equal(navigate(picker, choiceUrl(null)), true);
    assert.equal(await result, null);
    assert.equal(picker.state.destroyed, true);
  });

  it('resolves to null when the window is closed without a choice', async () => {
    const { picker, result } = await startPick({ sources: [SCREEN] });
    picker.close();
    assert.equal(await result, null);
  });

  it('ignores other navigations and an index outside the list', async () => {
    const { picker, result } = await startPick({ sources: [SCREEN] });
    assert.equal(navigate(picker, 'https://example.com/choose/0'), false);
    assert.equal(picker.state.destroyed, false);
    assert.equal(navigate(picker, choiceUrl(5)), true);
    assert.equal(await result, null);
  });

  it('settles once: a second answer does not change the result', async () => {
    const { picker, result } = await startPick({ sources: [SCREEN, WINDOW] });
    navigate(picker, choiceUrl(0));
    navigate(picker, choiceUrl(1));
    assert.equal(await result, SCREEN);
  });

  it('opens one picker at a time', async () => {
    const { pickSource, picker, result, windows } = await startPick({ sources: [SCREEN] });
    assert.equal(await pickSource(), null);
    assert.equal(windows.length, 1);
    navigate(picker, choiceUrl(0));
    assert.equal(await result, SCREEN);
    const again = pickSource();
    await new Promise(setImmediate);
    assert.equal(windows.length, 2);
    navigate(windows[1], choiceUrl(null));
    assert.equal(await again, null);
  });

  it('resolves to null instead of rejecting when the sources cannot be listed', async () => {
    const { result, windows } = await startPick({ sourcesError: new Error('no permission') });
    assert.equal(await result, null);
    assert.equal(windows.length, 0);
  });

  it('shows the page in Italian and without a parent window when none exists', async () => {
    const { picker, result } = await startPick({ language: 'it', sources: [SCREEN] });
    assert.match(decodePage(picker.webContents.loads[0]), /<html lang="it">/);
    assert.equal(picker.options.modal, false);
    assert.equal(picker.options.parent, undefined);
    picker.close();
    await result;
  });
});
