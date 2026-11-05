'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const noop = () => {};
const elements = new Map();
const capturedKeys = new Set();
const animationFrames = new Map();
let nextAnimationFrame = 0;
function element() {
    const classes = new Set();
    const attributes = new Map();
    return {
        innerHTML: '', textContent: '', value: '', disabled: false, hidden: false,
        isConnected: true, children: [], matches: () => false,
        currentTime: 0, readyState: 1, paused: true,
        style: { setProperty(name, value) { this[name] = value; } },
        classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
        closest: () => null,
        setAttribute: (name, value) => attributes.set(name, value),
        removeAttribute: name => attributes.delete(name),
        getAttribute: name => attributes.get(name) ?? null, addEventListener: noop, removeEventListener: noop,
        focus() { document.activeElement = this; }, querySelector: () => null,
        querySelectorAll: () => [], getClientRects: () => [1], offsetParent: {},
        pause() { this.paused = true; }, play() { this.paused = false; return Promise.resolve(); },
        load: noop, cancelVideoFrameCallback: noop,
        getContext: () => ({ clearRect: noop }),
    };
}
const focusControls = [element(), element()];
const document = {
    documentElement: element(), body: element(), activeElement: element(),
    addEventListener(type, handler, capture) { if (type === 'keydown' && capture) capturedKeys.add(handler); },
    removeEventListener(type, handler, capture) { if (type === 'keydown' && capture) capturedKeys.delete(handler); },
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, Object.assign(element(), { id }));
        return elements.get(id);
    },
    querySelector: () => focusControls[0],
    querySelectorAll: selector => selector === '.modal.active' ?
        [...elements.values()].filter(node => node.id.endsWith('-modal') && node.classList.contains('active')) : focusControls,
    createElement() {
        const node = element();
        Object.defineProperty(node, 'innerHTML', { get() {
            return String(node.textContent).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        } });
        return node;
    },
};
const storage = { getItem: () => null, setItem: noop, removeItem: noop };
const sandbox = {
    document, localStorage: storage, sessionStorage: storage,
    navigator: { userAgent: 'node-test' }, console, setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval: noop, URLSearchParams, AbortController, DOMException,
    requestAnimationFrame(callback) {
        animationFrames.set(++nextAnimationFrame, callback);
        return nextAnimationFrame;
    },
    cancelAnimationFrame: id => animationFrames.delete(id),
    location: { pathname: '/', search: '' },
    history: { pushState: noop, replaceState: noop, back: noop },
    matchMedia: () => ({ matches: false }), addEventListener: noop, removeEventListener: noop,
    getComputedStyle: () => ({ visibility: 'visible' }),
    Image: function () {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const source = fs.readFileSync('/frontend/static/js/app.js', 'utf8');
const html = fs.readFileSync('/frontend/index.html', 'utf8');
vm.runInContext(source, sandbox);
const actualApiRequest = sandbox.apiRequest;
const state = () => vm.runInContext('_trimState', sandbox);
const get = id => document.getElementById(id);
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { resolve, reject, promise };
};
function setup(frames = 300, fps = 30) {
    sandbox.testState = {
        videoId: 7, start: 0, end: 0, busy: false, invalid: false,
        controller: new AbortController(), returnFocus: element(),
        info: { total_frames: frames, framerate: fps, max_trim_seconds: Math.floor((frames - fps) / fps), revision: 'r' },
    };
    vm.runInContext('_trimState = testState', sandbox);
    get('trim-video-modal').classList.add('active');
    get('trim-video-name').value = 'Example';
    sandbox.renderVideoTrim();
    return state();
}

(async () => {
    let notifications = [];
    let navigations = [];
    sandbox.showNotification = (...args) => notifications.push(args);
    sandbox.navigateTo = path => navigations.push(path);
    sandbox.loadVideos = sandbox.refreshEventsSoon = noop;
    sandbox.buildTrimFilmstrip = noop;
    assert.strictEqual((source.match(/onclick="showVideoTrim\(\$\{video\.id\}\)"/g) || []).length, 1,
        'Trim must be exposed only once, in selected-video detail actions');
    assert(!html.includes('onclick="showVideoTrim('), 'No global or bulk trim action');
    assert(html.includes('aria-label="Seconds removed from start"'));
    assert(html.includes('aria-label="Seconds removed from end"'));
    assert(html.includes('aria-label="Reset start trim"') && html.includes('aria-label="Reset end trim"'));
    const trimHtml = html.slice(html.indexOf('<div id="trim-video-modal"'), html.indexOf('<div id="join-video-modal"'));
    assert.doesNotMatch(trimHtml, /re-?encod|safe regions|original stays unchanged/i,
        'Trim must not add processing or provenance explanations to the source quality');
    assert(html.includes('type="button" class="close" aria-label="Close trim dialog"'),
        'Trim must use the shared modal close theme while preserving keyboard/button semantics');
    assert(!/<video id="trim-video-preview"[^>]*\bcontrols\b/.test(html),
        'The preview must not show the browser clock for the untrimmed source');
    assert(html.includes('aria-label="Preview playback position"'));

    const controller = new AbortController();
    let fetchOptions;
    sandbox.fetch = async (_, options) => {
        fetchOptions = options;
        return { ok: true, status: 200, json: async () => ({}) };
    };
    await actualApiRequest('/test', { method: 'POST', body: { example: true }, signal: controller.signal });
    assert.strictEqual(fetchOptions.signal, controller.signal);

    for (const [start, end, duration, elapsed] of [
        [0, 0, 4, 0], [1, 0, 3, 0], [0, 1, 3, 3],
        [0, 2, 2, 2], [1, 1, 2, 2], [0, 3, 1, 1],
    ]) {
        setup(40, 10);
        sandbox.setVideoTrimSeconds('start', start);
        if (end) sandbox.setVideoTrimSeconds('end', end);
        assert.strictEqual(get('trim-preview-time').textContent, `${elapsed}s / ${duration}s`,
            `Start ${start}s / end ${end}s must show the selected duration, not the original four-second clock`);
        assert.strictEqual(Number(get('trim-preview-position').max), duration * 10);
        assert.strictEqual(Number(get('trim-preview-position').value), elapsed * 10);
    }

    setup(40, 10);
    sandbox.setVideoTrimSeconds('start', 1);
    sandbox.setVideoTrimSeconds('end', 1);
    sandbox.seekVideoTrimPreview(10);
    assert.strictEqual(get('trim-video-preview').currentTime, 2);
    assert.strictEqual(get('trim-preview-time').textContent, '1s / 2s');
    sandbox.seekVideoTrimPreview(20);
    assert.strictEqual(get('trim-video-preview').currentTime, 2.9, 'The endpoint must not show an excluded frame');
    assert.strictEqual(get('trim-preview-time').textContent, '2s / 2s');
    sandbox.toggleVideoTrimPlayback();
    assert.strictEqual(get('trim-video-preview').currentTime, 1, 'Play at the endpoint must restart the selection');
    assert.strictEqual(get('trim-video-preview').paused, false);
    assert.strictEqual(get('trim-preview-toggle').getAttribute('aria-label'), 'Pause selection');
    assert.strictEqual(get('trim-preview-time').textContent, '0s / 2s');
    sandbox.toggleVideoTrimPlayback();
    assert.strictEqual(get('trim-video-preview').paused, true);
    assert.strictEqual(get('trim-preview-toggle').getAttribute('aria-label'), 'Play selection');
    get('trim-video-preview').currentTime = 1.125;
    sandbox.renderVideoTrimPlayback();
    assert.strictEqual(Number(get('trim-preview-position').value), 1.25,
        'Playback progress must move between video frames instead of snapping to integer frames');
    assert.strictEqual(get('trim-preview-time').textContent, '0s / 2s');
    for (const [key, frame] of [
        ['Home', 0], ['ArrowRight', 1], ['ArrowUp', 2], ['ArrowLeft', 1], ['ArrowDown', 0],
        ['PageUp', 10], ['PageDown', 0], ['End', 20], ['ArrowRight', 20], ['Home', 0],
    ]) {
        let prevented = false;
        sandbox.handleVideoTrimPlaybackKey({ key, preventDefault: () => { prevented = true; } });
        assert(prevented && Math.abs(Number(get('trim-preview-position').value) - frame) < 1e-8,
            `${key} must preserve frame-accurate keyboard scrubbing and selection bounds`);
    }
    sandbox.seekVideoTrimPreview(1.25);
    assert(Math.abs(Number(get('trim-preview-position').value) - 1) < 1e-8,
        'Manual scrubbing must still seek the closest frame');
    get('trim-video-preview').currentTime = 2.5;
    sandbox.renderVideoTrimPlayback();
    assert.strictEqual(get('trim-preview-time').textContent, '1s / 2s');
    get('trim-video-preview').currentTime = 3;
    sandbox.boundVideoTrimPreview(state());
    assert.strictEqual(get('trim-video-preview').currentTime, 2.9);
    assert.strictEqual(get('trim-preview-time').textContent, '2s / 2s');
    sandbox.seekVideoTrimPreview('invalid');
    assert(notifications.some(message => message[0].includes('valid number')));
    setup(301, 30);
    sandbox.setVideoTrimSeconds('start', 9);
    sandbox.seekVideoTrimPreview(31);
    assert.strictEqual(get('trim-preview-time').textContent, '1s / 1s');
    assert.strictEqual(Number(get('trim-preview-position').max), 31);
    assert.strictEqual(get('trim-preview-position').getAttribute('aria-valuetext'), '1s of 1s');
    get('trim-video-preview').readyState = 0;
    sandbox.renderVideoTrimPlayback();
    assert.strictEqual(get('trim-preview-controls').disabled, true, 'Playback must wait for metadata');
    get('trim-video-preview').readyState = 1;
    sandbox.renderVideoTrimPlayback();
    assert.strictEqual(get('trim-preview-controls').disabled, false);
    for (const flag of ['busy', 'stale', 'previewError']) {
        state()[flag] = true;
        sandbox.renderVideoTrimPlayback();
        assert.strictEqual(get('trim-preview-controls').disabled, true, `${flag} must disable playback`);
        const time = get('trim-video-preview').currentTime;
        sandbox.seekVideoTrimPreview(0);
        assert.strictEqual(get('trim-video-preview').currentTime, time);
        state()[flag] = false;
    }

    setup(301, 30);
    assert.strictEqual(Number(get('trim-start-range').max), 301);
    assert.strictEqual(Number(get('trim-start-range').step), 30);
    assert.match(get('trim-video-selection').textContent, /10.03s remaining/);
    assert.strictEqual(get('trim-video-submit').disabled, true);
    sandbox.setVideoTrimSeconds('start', 9);
    assert.strictEqual(state().start, 9);
    assert.match(get('trim-video-selection').textContent, /1.03s remaining/);
    assert.strictEqual(get('trim-video-submit').disabled, false);
    sandbox.setVideoTrimSeconds('end', 9);
    assert.strictEqual(state().end, 0, 'The handles cannot remove the final second');
    assert.match(get('trim-video-errors').textContent, /at least one second/);
    sandbox.setVideoTrimSeconds('start', 0);
    sandbox.setVideoTrimSeconds('end', 9);
    assert.strictEqual(state().end, 9);
    sandbox.setVideoTrimSeconds('end', 0);
    assert.strictEqual(get('trim-video-submit').disabled, true);
    for (const invalid of ['', -1, 0.1, 'not a number', Infinity]) {
        sandbox.setVideoTrimSeconds('start', invalid);
        assert.strictEqual(get('trim-video-submit').disabled, true);
        assert.match(get('trim-video-errors').textContent, /whole number/);
    }
    sandbox.setVideoTrimSeconds('start', 2);
    assert.strictEqual(state().invalid, false);
    sandbox.setVideoTrimSeconds('end', 3);
    const player = get('trim-video-preview');
    assert(Math.abs(player.currentTime - (301 / 30 - 3 - 1 / 30)) < 0.00001);
    player.currentTime = 0;
    sandbox.boundVideoTrimPreview(state());
    assert.strictEqual(player.currentTime, 2);
    player.currentTime = 10;
    player.paused = false;
    sandbox.boundVideoTrimPreview(state());
    assert.strictEqual(player.paused, true);
    assert(player.currentTime < 301 / 30 - 3);
    for (const [edge, key, expected] of [
        ['start', 'Home', 0], ['start', 'ArrowRight', 1], ['start', 'ArrowLeft', 0],
        ['end', 'Home', 0], ['end', 'ArrowLeft', 1], ['end', 'ArrowRight', 0],
        ['end', 'End', 9], ['end', 'ArrowUp', 9], ['end', 'Home', 0],
    ]) {
        let prevented = false;
        sandbox.handleVideoTrimRangeKey({ key, preventDefault: () => { prevented = true; } }, edge);
        assert(prevented && state()[edge] === expected, `${edge} ${key} must move in a consistent physical direction`);
    }

    sandbox.cancelVideoRename = sandbox.renderTagPicker = noop;
    sandbox.location.pathname = '/timelapses/7';
    vm.runInContext("currentView = 'video-detail'", sandbox);
    for (const [quality, label] of [
        ['preserved', 'Original quality preserved'],
        ['partly_converted', 'Partly converted · High'],
        ['low', 'low'],
        ['medium', 'medium'],
        ['high', 'high'],
        ['maximum', 'maximum'],
    ]) {
        sandbox.apiRequest = async () => ({
            id: 7, name: 'Selected', status: 'completed', build_source: 'trimmed',
            quality, resolution: '160x96', framerate: 5, total_frames: 10,
            duration_seconds: 2, file_size: 1000, created_at: '2026-01-01T00:00:00Z',
        });
        await sandbox.loadVideoDetail(7);
        const actions = get('video-detail-actions').innerHTML;
        assert(actions.indexOf('gif-popover-wrap') < actions.indexOf('showVideoTrim(7)'));
        assert(actions.indexOf('showVideoTrim(7)') < actions.indexOf('deleteVideoFromDetail(7'));
        assert(get('video-detail-meta').innerHTML.includes(`<dt>Quality</dt><dd>${label}</dd>`));
    }
    sandbox.cleanupVideoTrim();
    const refreshedTrimButton = element();
    get('video-detail-actions').querySelector = () => refreshedTrimButton;
    document.activeElement = document.body;
    await sandbox.loadVideoDetail(7);
    assert.strictEqual(document.activeElement, refreshedTrimButton,
        'History-driven detail refresh must restore focus to the replacement Trim button');
    const inspection = deferred();
    let inspectionSignal;
    sandbox.apiRequest = async (url, options) => {
        inspectionSignal = options.signal;
        return url.endsWith('/validate') ? inspection.promise : { name: '<img src=x onerror=alert(1)>' };
    };
    const opening = sandbox.showVideoTrim(7);
    assert.strictEqual(capturedKeys.size, 1);
    sandbox.closeModal('trim-video-modal');
    assert.strictEqual(inspectionSignal.aborted, true);
    inspection.resolve({ total_frames: 300, framerate: 30, max_trim_seconds: 9, revision: 'r' });
    await opening;
    assert.strictEqual(state(), null, 'Late inspections must not reopen a closed dialog');
    assert.strictEqual(capturedKeys.size, 0);

    sandbox.apiRequest = async url => url.endsWith('/validate') ?
        { total_frames: 300, framerate: 30, max_trim_seconds: 9, revision: 'r' } :
        { name: '<img src=x onerror=alert(1)>' };
    await sandbox.showVideoTrim(7);
    assert.strictEqual(get('trim-video-name').value, '<img src=x onerror=alert(1)>_Trimmed');
    assert.strictEqual(get('trim-video-name').innerHTML, '');
    player.readyState = 0;
    sandbox.setVideoTrimSeconds('end', 2);
    player.readyState = 1;
    player.onloadedmetadata();
    assert.strictEqual(get('trim-preview-time').textContent, '8s / 8s',
        'Selections edited while loading must keep their logical endpoint after metadata arrives');
    assert(Math.abs(player.currentTime - (8 - 1 / 30)) < 0.00001);
    for (const time of [8 - 1 / 30, 8 - 1 / 60]) {
        sandbox.seekVideoTrimPreview(239);
        player.currentTime = time;
        await player.play();
        player.onplay();
        assert.strictEqual(player.currentTime, time,
            'Resuming within the last retained frame must not rewind the preview');
        player.pause();
        player.onpause();
    }
    sandbox.seekVideoTrimPreview(240);
    await player.play();
    player.onplay();
    assert.strictEqual(player.currentTime, 0, 'Playing from the logical endpoint must still replay the selection');
    assert.strictEqual(state().previewAtEnd, false);
    player.pause();
    player.onpause();
    await player.play();
    player.onplay();
    assert.strictEqual(animationFrames.size, 1, 'Playback must schedule one display-rate animation');
    player.onplay();
    assert.strictEqual(animationFrames.size, 1, 'Repeated play events must not duplicate animation loops');
    let [frameId, animate] = [...animationFrames][0];
    animationFrames.delete(frameId);
    player.currentTime = 1.125;
    animate();
    assert.strictEqual(Number(get('trim-preview-position').value), 33.75);
    assert.strictEqual(animationFrames.size, 1);
    player.pause();
    player.onpause();
    assert.strictEqual(animationFrames.size, 0, 'Pausing must stop the animation');
    await player.play();
    player.onplay();
    [frameId, animate] = [...animationFrames][0];
    animationFrames.delete(frameId);
    player.currentTime = 8;
    animate();
    assert.strictEqual(player.paused, true);
    assert.strictEqual(get('trim-preview-time').textContent, '8s / 8s');
    assert.strictEqual(animationFrames.size, 0, 'Selection completion must stop the animation');
    await player.play();
    player.onplay();
    const lateAnimation = [...animationFrames][0][1];
    const activeTrim = state();
    const trimName = get('trim-video-name').value;
    const exitFullscreen = deferred();
    let escapeExits = 0;
    document.fullscreenElement = get('trim-preview-wrap');
    document.exitFullscreen = () => {
        escapeExits++;
        return exitFullscreen.promise.then(() => { document.fullscreenElement = null; });
    };
    const pressTrimEscape = (repeat = false) => {
        let consumed = 0;
        sandbox.handleTrimModalKeydown({
            key: 'Escape', repeat,
            preventDefault: () => { consumed++; },
            stopImmediatePropagation: () => { consumed++; },
        });
        assert.strictEqual(consumed, 2, 'Trim must consume Escape before the global modal handler');
    };
    activeTrim.busy = true;
    activeTrim.stale = true;
    pressTrimEscape();
    assert.strictEqual(state(), activeTrim, 'Fullscreen Escape must leave the Trim dialog open');
    assert.strictEqual(activeTrim.controller.signal.aborted, false);
    assert.strictEqual(player.paused, false, 'Leaving fullscreen must not interrupt playback');
    assert.strictEqual(activeTrim.end, 2);
    assert.strictEqual(get('trim-video-name').value, trimName);
    assert.strictEqual(escapeExits, 1, 'Fullscreen Escape must work even when processing controls are disabled');
    pressTrimEscape(true);
    assert.strictEqual(escapeExits, 1, 'Holding Escape must not request fullscreen exit repeatedly');
    exitFullscreen.resolve();
    await Promise.resolve();
    assert.strictEqual(document.fullscreenElement, null);
    pressTrimEscape(true);
    assert.strictEqual(state(), activeTrim, 'An Escape repeat after fullscreen exit must not close Trim');
    activeTrim.busy = false;
    activeTrim.stale = false;
    document.fullscreenElement = get('trim-preview-wrap');
    document.exitFullscreen = async () => { throw new Error('Exit denied'); };
    pressTrimEscape();
    await Promise.resolve();
    assert.strictEqual(state(), activeTrim, 'A fullscreen exit failure must preserve the selection');
    assert(notifications.some(message =>
        message[0] === 'Fullscreen preview could not close: Exit denied' && message[1] === 'error'));
    player.onerror();
    assert.strictEqual(get('trim-preview-controls').disabled, true);
    document.activeElement = focusControls[0];
    let prevented = false;
    sandbox.handleTrimModalKeydown({
        key: 'Tab', shiftKey: true, preventDefault: () => { prevented = true; },
    });
    assert(prevented && document.activeElement === focusControls[1]);
    let fullscreenExits = 0;
    document.fullscreenElement = get('trim-preview-wrap');
    document.exitFullscreen = async () => {
        fullscreenExits++;
        await Promise.resolve();
        document.fullscreenElement = null;
    };
    sandbox.cleanupVideoTrim();
    await Promise.resolve();
    assert.strictEqual(animationFrames.size, 0, 'Closing must cancel all preview animation');
    lateAnimation();
    assert.strictEqual(animationFrames.size, 0, 'A stale callback must not restart a closed preview');
    assert.strictEqual(fullscreenExits, 1, 'Closing a fullscreen preview must exit exactly once');
    assert.strictEqual(capturedKeys.size, 0);
    assert.strictEqual(player.onplay, null);
    assert.strictEqual(player.onpause, null);
    assert.strictEqual(player.onended, null);

    await sandbox.showVideoTrim(7);
    pressTrimEscape();
    assert.strictEqual(state(), null, 'A fresh Escape outside fullscreen must still close Trim');
    assert.strictEqual(capturedKeys.size, 0);

    setup();
    sandbox.setVideoTrimSeconds('start', 9);
    const creating = deferred();
    let creates = 0;
    sandbox.apiRequest = async (url, options) => {
        creates++;
        assert.strictEqual(url, '/videos/7/trim');
        assert.strictEqual(options.signal, undefined, 'Closing must not abort an accepted creation request');
        assert.strictEqual(options.body.trim_start_seconds, 9);
        assert.strictEqual(options.body.revision, 'r');
        return creating.promise;
    };
    const submit = sandbox.submitVideoTrim({ preventDefault: noop });
    await sandbox.submitVideoTrim({ preventDefault: noop });
    assert.strictEqual(creates, 1, 'A double submit must not create duplicate videos');
    assert.strictEqual(get('trim-video-controls').disabled, true);
    sandbox.closeModal('trim-video-modal');
    assert.strictEqual(state(), null);
    creating.resolve({ id: 99 });
    await submit;
    assert.deepStrictEqual(navigations, [], 'Late success must not hijack navigation after close');
    assert(notifications.some(message => message[0] === 'Video processing started'));

    setup();
    sandbox.setVideoTrimSeconds('end', 1);
    sandbox.apiRequest = async () => { throw Object.assign(new Error('Source changed'), { status: 409 }); };
    await sandbox.submitVideoTrim({ preventDefault: noop });
    assert.strictEqual(get('trim-video-submit').disabled, true);
    assert.match(get('trim-video-errors').textContent, /reopen Trim/);
    sandbox.setVideoTrimSeconds('start', 1);
    assert.strictEqual(get('trim-video-submit').disabled, true, 'Editing must not bypass stale-source inspection');
    sandbox.cleanupVideoTrim();

    notifications = [];
    setup();
    sandbox.setVideoTrimSeconds('start', 1);
    sandbox.apiRequest = async () => ({ id: 100 });
    await sandbox.submitVideoTrim({ preventDefault: noop });
    assert.deepStrictEqual(navigations, ['/timelapses/100']);
    assert.strictEqual(state(), null);
    assert.deepStrictEqual(notifications, [['Video processing started']],
        'Trimming must use the same success toast as a normal timelapse build');
    console.log('Video trim tests passed: whole-second ranges, resets, preview bounds, placement, quality, escaping, focus, cancellation and submission races.');
})().catch(error => { console.error(error); process.exitCode = 1; });
