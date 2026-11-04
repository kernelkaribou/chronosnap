'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const noop = () => {};
const elements = new Map();
const capturedKeys = new Set();
function element() {
    return {
        innerHTML: '', textContent: '', value: '', disabled: false, hidden: false,
        style: {}, classList: { add: noop, remove: noop, contains: () => false },
        closest: () => null,
        setAttribute: noop, getAttribute: () => null, addEventListener: noop,
        removeEventListener: noop, focus() { document.activeElement = this; }, querySelector: () => null,
        querySelectorAll: () => [], offsetParent: {},
    };
}
const document = {
    documentElement: element(), body: element(), activeElement: element(),
    addEventListener(type, handler, capture) {
        if (type === 'keydown' && capture) capturedKeys.add(handler);
    },
    removeEventListener(type, handler, capture) {
        if (type === 'keydown' && capture) capturedKeys.delete(handler);
    },
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    },
    querySelector: () => element(), querySelectorAll: () => [],
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
    document, localStorage: storage, sessionStorage: storage, navigator: { userAgent: 'node-test' },
    console, setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams,
    location: { pathname: '/', search: '' },
    history: { pushState: noop, replaceState: noop, back: noop },
    matchMedia: () => ({ matches: false }), addEventListener: noop, removeEventListener: noop,
    Image: function () {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const appSource = fs.readFileSync('/frontend/static/js/app.js', 'utf8');
vm.runInContext(appSource, sandbox);
const state = () => vm.runInContext('_joinState', sandbox);
function setup() {
    sandbox.testState = {
        entries: [{ id: 1, name: 'A', resolution: '160x96', duration_seconds: 1, framerate: 5, total_frames: 5 },
            { id: 2, name: 'B', resolution: '160x96', duration_seconds: 2, framerate: 10, total_frames: 20 }],
        candidates: [{ id: 1, name: 'A', resolution: '160x96', duration_seconds: 1, framerate: 5, total_frames: 5 }],
        busy: false, validation: { duration_seconds: 3, clips: [{ revision: 'a' }, { revision: 'b' }] }, review: null,
        version: 0, searchVersion: 0, outputFps: 5, pickerOpen: false, drag: null,
        outputResolution: '160x96', resolutionChosen: false,
    };
    vm.runInContext('_joinState = testState', sandbox);
}

function joinReport() {
    const clips = state().entries.map((clip, index) => ({
        position: index + 1, video_id: clip.id, name: clip.name,
        source_fps: clip.framerate, total_frames: clip.total_frames,
        duration_seconds: clip.duration_seconds,
        resolution: clip.resolution,
        action: clip.framerate === state().outputFps ? 'copy' : 'retime',
        output_duration_seconds: clip.total_frames / state().outputFps,
        revision: `source-${clip.id}`,
    }));
    return {
        compatible: true, output_fps: state().outputFps, clips, warning: null,
        mode: 'copy', output_resolution: state().outputResolution,
        duration_seconds: clips.reduce((total, clip) => total + clip.output_duration_seconds, 0),
    };
}

(async () => {
    const appHtml = fs.readFileSync('/frontend/index.html', 'utf8');
    assert(appHtml.includes('onclick="showCreateJobModal()">Create</button>'),
        'The Jobs create action must use the same concise naming as Build and Join');
    assert(appHtml.includes('onclick="scanOrphanedCaptures()">Cleanup</button>'),
        'The Cleanup action must use a plain text label without an icon');
    assert(appHtml.includes('onclick="showProcessVideoModal()">Build</button>'),
        'The library build action must use the same concise naming as Join');
    assert(appHtml.includes('id="create-video-btn" class="btn btn-accent">Build</button>'),
        'The build submit action must use the same concise naming as Join');
    assert(!fs.readFileSync('/frontend/index.html', 'utf8').includes('video-detail-provenance'),
        'Video details must not include the joined playback-order section');
    assert(!appSource.includes('video-detail-provenance'),
        'Video details must not render into the removed playback-order section');
    assert(!appSource.includes('join_sources'),
        'Completed video details must not depend on transient source history');
    sandbox.cancelVideoRename = noop;
    sandbox.renderTagPicker = noop;
    document.getElementById('video-detail-player').load = noop;
    for (const [quality, label] of [
        ['preserved', 'Original quality preserved'],
        ['partly_converted', 'Partly converted · High'],
        ['high', 'Converted · High'],
    ]) {
        sandbox.apiRequest = async () => ({
            id: 99, name: 'Independent output', build_source: 'joined', status: 'completed',
            quality, resolution: '160x96', framerate: 5, total_frames: 10,
            duration_seconds: 2, file_size: 1000, created_at: '2026-01-01T00:00:00Z',
        });
        await sandbox.loadVideoDetail(99);
        assert(document.getElementById('video-detail-meta').innerHTML.includes(`<dt>Quality</dt><dd>${label}</dd>`),
            `Quality ${quality} must render without source records`);
    }
    sandbox.loadVideos = noop;
    sandbox.navigateTo = noop;
    sandbox.showNotification = noop;
    setup();
    document.getElementById('join-video-name').value = 'My custom join';
    state().validation = null;
    const report = joinReport();
    const submitted = [];
    sandbox.apiRequest = async (url, options) => {
        submitted.push({ url, options });
        if (url === '/videos/join/validate') {
            await sandbox.submitVideoJoin({ preventDefault: noop });
            return report;
        }
        assert.strictEqual(state().creating, true);
        await sandbox.submitVideoJoin({ preventDefault: noop });
        return { id: 123 };
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(submitted.map(request => request.url), ['/videos/join/validate']);
    assert.strictEqual(state().validation, report);
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
    assert.strictEqual((document.getElementById('join-video-order').innerHTML.match(/join-reviewed-preserved/g) || []).length, 1);
    assert.strictEqual((document.getElementById('join-video-order').innerHTML.match(/join-reviewed-adjusted/g) || []).length, 1);
    assert.match(document.getElementById('join-video-order').innerHTML, /<circle cx="12" cy="12" r="9"\/>/);
    assert.strictEqual(document.getElementById('join-review-announcement').textContent,
        'Review complete. Unchanged clips: 1. Timing-adjusted clips: 1. Clips needing conversion: 0. Join is ready.');
    assert.match(document.getElementById('join-video-order').innerHTML, /No changes needed/);
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(submitted.map(request => request.url), ['/videos/join/validate', '/videos/join']);
    assert.deepStrictEqual(submitted[1].options.body.revisions, ['source-1', 'source-2']);
    assert.strictEqual(submitted[1].options.body.name, 'My custom join');
    assert.strictEqual(submitted[1].options.body.output_fps, 5);
    assert.strictEqual(submitted[1].options.body.output_resolution, '160x96');
    assert.strictEqual(submitted[1].options.body.mode, 'copy');
    assert.strictEqual(state(), null);

    setup();
    state().entries.push(state().entries[0]);
    sandbox.joinMoveClip(2, -1);
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 1, 2]);
    sandbox.joinRemoveClip(0);
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2]);
    assert.strictEqual(document.activeElement, document.getElementById('join-grip-0'));
    assert.strictEqual(state().validation, null);
    assert.strictEqual(state().version, 2);
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Review');
    assert(!document.getElementById('join-video-order').innerHTML.includes('join-reviewed-'));
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');

    setup();
    state().candidates.push(state().entries[1], { ...state().entries[0], id: 3, name: 'C' });
    sandbox.openJoinPicker();
    const originalValidation = state().validation;
    sandbox.toggleJoinCandidate(1);
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2]);
    assert.deepStrictEqual(sandbox.getJoinPickerEntries().map(v => v.id), [2]);
    assert.strictEqual(state().validation, originalValidation);
    assert.strictEqual(state().version, 0);
    assert.strictEqual(document.activeElement, document.getElementById('join-select-1'));
    sandbox.toggleJoinCandidate(1);
    sandbox.toggleJoinCandidate(3);
    sandbox.toggleJoinCandidate(2);
    assert.deepStrictEqual(sandbox.getJoinPickerEntries().map(v => v.id), [1, 3]);
    sandbox.renderJoinCandidates();
    const selectedGallery = document.getElementById('join-video-candidates').innerHTML;
    assert.match(selectedGallery, /video-gallery-card join-candidate selected/);
    assert.match(selectedGallery, /gallery-checkbox-indicator/);
    assert.match(selectedGallery, /type="checkbox"/);
    assert(!selectedGallery.includes('in order'));
    assert(!selectedGallery.includes('join-count'));
    assert(!selectedGallery.includes('join-add-1'));
    assert(!selectedGallery.includes('join-remove-1'));
    assert.strictEqual(document.getElementById('join-video-picker-count').textContent, '2 selected');
    sandbox.applyJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 3]);
    assert.strictEqual(state().validation, null);
    assert.strictEqual(state().version, 1);
    assert.strictEqual(state().pickerOpen, false);
    assert.strictEqual(state().pickerSelection, null);
    assert.strictEqual(document.activeElement, document.getElementById('join-video-add'));

    setup();
    state().entries.reverse();
    state().candidates.push({ ...state().entries[1], id: 3 }, { ...state().entries[1], id: 4 });
    sandbox.openJoinPicker();
    sandbox.toggleJoinCandidate(4);
    sandbox.toggleJoinCandidate(3);
    assert.deepStrictEqual(sandbox.getJoinPickerEntries().map(v => v.id), [2, 1, 4, 3]);
    assert.deepStrictEqual(state().entries.map(v => v.id), [2, 1]);
    sandbox.applyJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [2, 1, 4, 3]);

    setup();
    state().entries.push(state().entries[0]);
    const repeatedValidation = state().validation;
    document.getElementById('join-video-errors').textContent = 'Previous compatibility warning';
    document.getElementById('join-video-status').textContent = 'Previous playback warning';
    sandbox.openJoinPicker();
    sandbox.toggleJoinCandidate(1);
    assert.deepStrictEqual(sandbox.getJoinPickerEntries().map(v => v.id), [2]);
    sandbox.toggleJoinCandidate(1);
    assert.deepStrictEqual(sandbox.getJoinPickerEntries().map(v => v.id), [1, 2, 1]);
    sandbox.applyJoinPicker();
    assert.strictEqual(state().validation, repeatedValidation);
    assert.strictEqual(state().version, 0);
    assert.strictEqual(document.getElementById('join-video-errors').textContent, 'Previous compatibility warning');
    assert.strictEqual(document.getElementById('join-video-status').textContent, 'Previous playback warning');
    sandbox.openJoinPicker();
    sandbox.toggleJoinCandidate(1);
    sandbox.closeJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2, 1]);
    assert.strictEqual(state().validation, repeatedValidation);
    assert.strictEqual(state().version, 0);
    sandbox.openJoinPicker();
    sandbox.toggleJoinCandidate(1);
    sandbox.applyJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [2]);
    assert.strictEqual(state().validation, null);

    setup();
    sandbox.openJoinPicker();
    state().drag = { from: 0 };
    const checked = state().validation;
    sandbox.toggleJoinCandidate(1);
    sandbox.applyJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2]);
    assert.strictEqual(state().validation, checked);
    assert.strictEqual(state().version, 0);

    setup();
    const savedQuerySelector = document.querySelector;
    document.querySelector = () => null;
    sandbox.joinRemoveClip(0);
    sandbox.joinRemoveClip(0);
    assert.strictEqual(document.activeElement, document.getElementById('join-video-add'));
    document.querySelector = savedQuerySelector;

    setup();
    sandbox.openJoinPicker();
    assert.strictEqual(state().pickerOpen, true);
    assert.strictEqual(document.getElementById('join-video-ordering').hidden, true);
    assert.strictEqual(document.getElementById('join-video-output').inert, true);
    assert.strictEqual(document.getElementById('join-video-picker').hidden, false);
    assert.strictEqual(document.getElementById('join-video-submit').disabled, true);
    sandbox.renderJoinOrder();
    assert.strictEqual(document.getElementById('join-video-submit').disabled, true);
    sandbox.handleJoinModalKeydown({ key: 'Escape', preventDefault: noop, stopPropagation: noop });
    assert.strictEqual(state().pickerOpen, false);
    assert.strictEqual(document.getElementById('join-video-ordering').hidden, false);
    assert.strictEqual(document.getElementById('join-video-output').inert, false);
    assert.strictEqual(document.getElementById('join-video-submit').disabled, false);
    assert.strictEqual(document.activeElement, document.getElementById('join-video-add'));

    setup();
    sandbox.openJoinPicker();
    state().candidates[0].thumbnail_path = 'thumbnail.jpg';
    state().candidates[0].name = '"><img src=x onerror=alert(1)>';
    sandbox.renderJoinCandidates();
    const gallery = document.getElementById('join-video-candidates').innerHTML;
    assert.match(gallery, /join-candidate-preview/);
    assert.match(gallery, /join-candidate/);
    assert.match(gallery, /&quot;/);
    assert.match(gallery, /aria-label="Select &quot;&gt;&lt;img src=x onerror=alert\(1\)&gt;"/);
    assert(!gallery.includes('<img src=x'));
    state().candidates[0].thumbnail_path = null;
    sandbox.renderJoinCandidates();
    assert.match(document.getElementById('join-video-candidates').innerHTML, /No preview/);

    setup();
    document.getElementById('join-video-fps').value = '10';
    sandbox.changeJoinFps();
    assert.strictEqual(state().outputFps, 10);
    assert.strictEqual(state().validation, null);
    assert.match(document.getElementById('join-video-total').textContent, /2.5s/);
    assert.strictEqual(sandbox.joinOutputDuration(state().entries[0]), 0.5);
    assert.match(document.getElementById('join-video-order').innerHTML, /5 fps · 1s/);
    assert.match(document.getElementById('join-video-order').innerHTML, /Frame rate changed to 10 FPS/);
    assert(!document.getElementById('join-video-order').innerHTML.includes('speed'));
    sandbox.joinGripKeydown({ key: 'ArrowDown', preventDefault: noop, stopPropagation: noop }, 0);
    assert.deepStrictEqual(state().entries.map(v => v.id), [2, 1]);
    sandbox.joinGripKeydown({ key: 'Home', preventDefault: noop, stopPropagation: noop }, 1);
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2]);
    sandbox.joinGripKeydown({ key: 'End', preventDefault: noop, stopPropagation: noop }, 0);
    assert.deepStrictEqual(state().entries.map(v => v.id), [2, 1]);

    const modal = document.getElementById('join-video-modal');
    const first = element(), last = element();
    modal.querySelectorAll = () => [first, last];
    modal.contains = target => target === first || target === last;
    document.activeElement = element();
    let prevented = false;
    sandbox.handleJoinModalKeydown({ key: 'Tab', preventDefault() { prevented = true; } });
    assert(prevented);
    assert.strictEqual(document.activeElement, first);
    sandbox.handleJoinModalKeydown({ key: 'Tab', shiftKey: true, preventDefault: noop });
    assert.strictEqual(document.activeElement, last);
    sandbox.handleJoinModalKeydown({ key: 'Tab', preventDefault: noop });
    assert.strictEqual(document.activeElement, first);

    setup();
    sandbox.openJoinPicker();
    state().busy = true;
    sandbox.joinMoveClip(0, 1);
    sandbox.joinRemoveClip(0);
    sandbox.toggleJoinCandidate(1);
    sandbox.applyJoinPicker();
    assert.strictEqual(state().pickerOpen, true);
    assert.deepStrictEqual(state().entries.map(v => v.id), [1, 2]);

    setup();
    state().entries = Array.from({ length: 100 }, () => state().candidates[0]);
    state().candidates.push({ ...state().candidates[0], id: 3 });
    sandbox.openJoinPicker();
    sandbox.toggleJoinCandidate(3);
    assert.strictEqual(state().entries.length, 100);
    assert.match(document.getElementById('join-picker-errors').textContent, /100/);
    assert.strictEqual(state().pickerSelection.size, 1);
    sandbox.toggleJoinCandidate(1);
    assert.strictEqual(sandbox.getJoinPickerEntries().length, 0);
    assert.strictEqual(document.getElementById('join-picker-errors').textContent, '');
    sandbox.toggleJoinCandidate(3);
    sandbox.toggleJoinCandidate(1);
    assert.strictEqual(state().pickerSelection.size, 1);
    assert.strictEqual(state().pickerSelection.has(1), false);
    assert.strictEqual(state().entries.length, 100);
    sandbox.applyJoinPicker();
    assert.deepStrictEqual(state().entries.map(v => v.id), [3]);

    setup();
    state().entries[0].name = '"><img src=x onerror=alert(1)>';
    sandbox.renderJoinOrder();
    assert(!document.getElementById('join-video-order').innerHTML.includes('<img src=x'));
    assert(document.getElementById('join-video-order').innerHTML.includes('&quot;'));

    setup();
    const requests = [];
    sandbox.apiRequest = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
    document.getElementById('join-video-search').value = 'old';
    const old = sandbox.loadJoinCandidates();
    document.getElementById('join-video-search').value = 'new';
    const fresh = sandbox.loadJoinCandidates();
    requests[1].resolve([{ id: 5, name: 'New', duration_seconds: 1 }]);
    await fresh;
    requests[0].resolve([{ id: 6, name: 'Old', duration_seconds: 1 }]);
    await old;
    assert.strictEqual(state().candidates[0].id, 5);
    assert.strictEqual(requests[1].options.query.search, 'new');
    assert.strictEqual(requests[1].options.query.offset, 0);

    setup();
    state().validation = null;
    const warningReport = joinReport();
    warningReport.warning = 'This profile may not play in every browser.';
    const warningRequests = [];
    sandbox.apiRequest = async (url, options) => {
        warningRequests.push(url);
        assert.strictEqual(options.body.output_fps, 5);
        return url === '/videos/join/validate' ? warningReport : { id: 124 };
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert(state().validation);
    assert.deepStrictEqual(warningRequests, ['/videos/join/validate']);
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
    assert.match(document.getElementById('join-video-order').innerHTML, /may not play/);
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(warningRequests, ['/videos/join/validate', '/videos/join']);
    assert.strictEqual(state(), null);

    setup();
    state().validation = null;
    const correctedReport = joinReport();
    Object.assign(correctedReport.clips[0], {
        total_frames: 10, duration_seconds: 2, output_duration_seconds: 2,
    });
    correctedReport.duration_seconds = 6;
    const correctedRequests = [];
    sandbox.apiRequest = async url => {
        correctedRequests.push(url);
        return correctedReport;
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(correctedRequests, ['/videos/join/validate']);
    assert.strictEqual(state().entries[0].total_frames, 10);
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');
    assert.match(document.getElementById('join-video-total').textContent, /6s/);
    sandbox.joinMoveClip(0, 1);
    assert.strictEqual(state().validation, null);

    setup();
    state().validation = null;
    const incompatibleReport = joinReport();
    incompatibleReport.compatible = false;
    incompatibleReport.clips[1].error = 'Codec configurations differ';
    const incompatibleRequests = [];
    sandbox.apiRequest = async url => {
        incompatibleRequests.push(url);
        return incompatibleReport;
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(incompatibleRequests, ['/videos/join/validate']);
    assert.strictEqual(state().validation, null);
    assert.match(document.getElementById('join-video-order').innerHTML, /Codec configurations differ/);
    assert.match(document.getElementById('join-video-order').innerHTML, /join-reviewed-error/);
    assert.match(document.getElementById('join-video-order').innerHTML, /Cannot join/);
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Review');
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');
    assert.strictEqual(state().entries.length, 2);
    assert.strictEqual(state().busy, false);
    assert.strictEqual(state().review, incompatibleReport);
    sandbox.joinMoveClip(0, 1);
    assert.strictEqual(state().review, null);
    assert(!document.getElementById('join-video-order').innerHTML.includes('join-reviewed-error'));

    setup();
    state().entries[1].resolution = '320x240';
    sandbox.renderJoinOrder();
    assert.strictEqual(state().outputResolution, '320x240');
    document.getElementById('join-video-resolution').value = '160x96';
    sandbox.changeJoinResolution();
    assert.strictEqual(state().validation, null);
    sandbox.joinMoveClip(0, 1);
    assert.strictEqual(state().outputResolution, '160x96');

    setup();
    state().validation = null;
    state().entries[1].resolution = '320x192';
    sandbox.renderJoinOrder();
    let resizeOrder = document.getElementById('join-video-order').innerHTML;
    assert.match(resizeOrder, /Resolution increased to 320x192 · Lossy re-encode/);
    assert.strictEqual((resizeOrder.match(/Lossy re-encode/g) || []).length, 1);
    assert(!resizeOrder.includes('No changes needed'));
    document.getElementById('join-video-resolution').value = '160x96';
    sandbox.changeJoinResolution();
    resizeOrder = document.getElementById('join-video-order').innerHTML;
    assert.match(resizeOrder, /Resolution decreased to 160x96 · Frame rate changed to 5 FPS · Lossy re-encode/);
    assert(!resizeOrder.includes('Resolution increased'));
    assert(!resizeOrder.includes('speed'));
    sandbox.joinMoveClip(0, 1);
    assert.strictEqual(state().outputResolution, '160x96');
    assert.match(document.getElementById('join-video-order').innerHTML, /Resolution decreased to 160x96/);
    sandbox.joinRemoveClip(0);
    resizeOrder = document.getElementById('join-video-order').innerHTML;
    assert(!resizeOrder.includes('Lossy re-encode'));
    assert(!resizeOrder.includes('Resolution increased'));
    assert(!resizeOrder.includes(' → '));

    setup();
    state().validation = null;
    state().entries[0].resolution = '320x96';
    state().entries[1].resolution = '160x192';
    state().outputResolution = '160x192';
    state().resolutionChosen = true;
    sandbox.renderJoinOrder();
    assert.match(document.getElementById('join-video-order').innerHTML, /Resolution changed to 160x192 · Lossy re-encode/);

    setup();
    state().entries[1].resolution = '320x192';
    state().outputResolution = '320x192';
    state().resolutionChosen = true;
    const verifiedResize = joinReport();
    verifiedResize.mode = 'selective';
    Object.assign(verifiedResize.clips[0], { action: 'reencode', scaling: 'downscale', padding: true });
    verifiedResize.clips[1].action = 'retime';
    state().validation = verifiedResize;
    sandbox.renderJoinOrder();
    resizeOrder = document.getElementById('join-video-order').innerHTML;
    assert.match(resizeOrder, /Resolution increased to 320x192 · Padding · Lossy re-encode/);
    assert.match(resizeOrder, /Frame rate changed to 5 FPS/);
    assert(!resizeOrder.includes('Resolution decreased'));
    assert(!resizeOrder.includes('Will be'));
    assert.strictEqual((resizeOrder.match(/Lossy re-encode/g) || []).length, 1);
    document.getElementById('join-video-fps').value = '10';
    sandbox.changeJoinFps();
    assert.strictEqual(state().validation, null);
    assert.match(document.getElementById('join-video-order').innerHTML, /Resolution increased to 320x192 · Frame rate changed to 10 FPS · Lossy re-encode/);

    for (const [source, plan, wording] of [
        [{}, { action: 'copy' }, 'No changes needed'],
        [{}, { action: 'retime' }, 'No changes needed'],
        [{ framerate: 10 }, { action: 'retime' }, 'Frame rate changed to 5 FPS'],
        [{ resolution: '80x48' }, { action: 'reencode', scaling: 'upscale', padding: true },
            'Resolution increased to 160x96 · Padding · Lossy re-encode'],
        [{ resolution: '320x192' }, { action: 'reencode', scaling: 'downscale', padding: false },
            'Resolution decreased to 160x96 · Lossy re-encode'],
        [{ resolution: '320x48' }, { action: 'reencode', scaling: 'downscale', padding: false },
            'Resolution changed to 160x96 · Lossy re-encode'],
        [{}, { action: 'reencode', scaling: 'downscale', padding: true }, 'Padding · Lossy re-encode'],
        [{}, { action: 'retime', padding: true }, 'No changes needed'],
        [{}, { action: 'reencode', scaling: 'none', padding: false }, 'Lossy re-encode'],
        [{}, { action: 'reencode', codec: 'vp9' }, 'Codec changed to H.264 · Lossy re-encode'],
        [{ resolution: '80x48', framerate: 10 }, { action: 'reencode', padding: true, codec: 'h264' },
            'Resolution increased to 160x96 · Frame rate changed to 5 FPS · Padding · Lossy re-encode'],
    ]) {
        setup();
        Object.assign(state().entries[0], source);
        state().validation = { compatible: true, mode: 'selective', clips: [plan] };
        assert.strictEqual(sandbox.joinProcessingHint(state().entries[0], 0), wording);
    }

    for (const [action, mode, compatible, error, kind, sourceFps = 5, sourceResolution = '160x96'] of [
        ['copy', 'copy', true, null, 'preserved'],
        ['copy', 'selective', true, null, 'preserved'],
        ['retime', 'copy', true, null, 'preserved'],
        ['retime', 'selective', true, null, 'preserved'],
        ['retime', 'copy', true, null, 'adjusted', 10],
        ['retime', 'selective', true, null, 'adjusted', 10],
        ['retime', 'copy', true, null, 'adjusted', 5, '320x192'],
        ['copy', 'copy', true, null, 'adjusted', 10],
        ['reencode', 'selective', true, null, 'reencode'],
        ['copy', 'all', true, null, 'reencode'],
        ['copy', 'copy', false, null, ''],
        ['copy', 'copy', undefined, null, ''],
        [undefined, 'copy', true, null, ''],
        ['copy', 'copy', false, 'Unsupported clip', 'error'],
    ]) {
        setup();
        Object.assign(state().entries[0], { framerate: sourceFps, resolution: sourceResolution });
        state().validation = { compatible, mode, clips: [{ action, error }] };
        assert.strictEqual(sandbox.joinReviewKind(0), kind);
    }
    setup();
    state().validation = null;
    assert.strictEqual(sandbox.joinReviewKind(0), '');

    for (const fps of [5, 10]) {
        setup();
        state().validation = null;
        state().outputFps = fps;
        const normalized = joinReport();
        normalized.clips.forEach(clip => { clip.action = 'retime'; });
        sandbox.apiRequest = async url => {
            assert.strictEqual(url, '/videos/join/validate');
            return normalized;
        };
        await sandbox.submitVideoJoin({ preventDefault: noop });
        assert.strictEqual(sandbox.joinReviewKind(0), fps === 5 ? 'preserved' : 'adjusted');
        assert.strictEqual(sandbox.joinReviewKind(1), fps === 10 ? 'preserved' : 'adjusted');
        for (const [index, clip] of state().entries.entries()) {
            assert.strictEqual(sandbox.joinProcessingHint(clip, index),
                clip.framerate === fps ? 'No changes needed' : `Frame rate changed to ${fps} FPS`);
        }
        assert(!document.getElementById('join-video-order').innerHTML.includes('Timing normalized'));
        assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
        assert.strictEqual(document.getElementById('join-review-announcement').textContent,
            'Review complete. Unchanged clips: 1. Timing-adjusted clips: 1. Clips needing conversion: 0. Join is ready.');
    }

    for (const mode of ['selective', 'all']) {
        setup();
        state().validation = null;
        const plan = joinReport();
        plan.mode = mode;
        plan.plan_token = 'c'.repeat(64);
        if (mode === 'all') plan.fallback_reason = 'Converted clips cannot safely match <reference>.';
        plan.clips.forEach((clip, index) => { clip.action = mode === 'all' || index ? 'reencode' : 'retime'; });
        const planRequests = [];
        sandbox.apiRequest = async (url, options) => {
            planRequests.push({ url, options });
            return url === '/videos/join/validate' ? plan : { id: 150 };
        };
        await sandbox.submitVideoJoin({ preventDefault: noop });
        assert.strictEqual(planRequests.length, 1);
        assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
        assert.strictEqual(document.getElementById('join-video-status').textContent, '');
        assert.match(document.getElementById('join-video-order').innerHTML, /Lossy re-encode/);
        assert.match(document.getElementById('join-video-order').innerHTML, /title="Re-encoding may reduce quality"/);
        assert.strictEqual((document.getElementById('join-video-order').innerHTML.match(/join-reviewed-reencode/g) || []).length,
            mode === 'all' ? 2 : 1);
        if (mode === 'all') {
            assert.match(document.getElementById('join-video-order').innerHTML, /cannot safely match &lt;reference&gt;/);
            assert.strictEqual((document.getElementById('join-video-order').innerHTML.match(/cannot safely match/g) || []).length, 1);
        }
        await sandbox.submitVideoJoin({ preventDefault: noop });
        assert.strictEqual(planRequests[1].options.body.mode, mode);
        assert.strictEqual(planRequests[1].options.body.plan_token, plan.plan_token);
        assert.strictEqual(state(), null);
    }

    setup();
    state().validation = null;
    const actualResolutionReport = joinReport();
    actualResolutionReport.clips[1].resolution = '320x240';
    sandbox.apiRequest = async () => actualResolutionReport;
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.strictEqual(state().outputResolution, '160x96');
    assert.strictEqual(document.getElementById('join-video-submit').textContent, 'Join');
    assert.strictEqual(document.getElementById('join-video-status').textContent, '');
    assert.strictEqual(state().entries[1].resolution, '320x240');

    setup();
    state().validation = null;
    const savedOrder = state().entries.map(v => v.id);
    const retryReport = joinReport();
    const retryRequests = [];
    sandbox.apiRequest = async url => {
        retryRequests.push(url);
        if (url === '/videos/join/validate') return retryReport;
        throw new Error('Joining is busy');
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(retryRequests, ['/videos/join/validate']);
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.deepStrictEqual(retryRequests, ['/videos/join/validate', '/videos/join']);
    assert.deepStrictEqual(state().entries.map(v => v.id), savedOrder);
    assert.strictEqual(state().validation, null);
    assert.match(document.getElementById('join-video-errors').textContent, /busy/);
    assert.strictEqual(state().busy, false);

    setup();
    state().validation = null;
    sandbox.apiRequest = async () => {
        const error = new Error('Choose an actual source FPS');
        error.detail = { available_fps: [30], clips: [
            { video_id: 1, source_fps: 30, total_frames: 30, duration_seconds: 1 },
            { video_id: 2, source_fps: 30, total_frames: 60, duration_seconds: 2 },
        ] };
        throw error;
    };
    await sandbox.submitVideoJoin({ preventDefault: noop });
    assert.strictEqual(state().outputFps, 30);
    assert.strictEqual(state().validation, null);
    assert.match(document.getElementById('join-video-errors').textContent, /Actual source FPS: 30/);

    setup();
    state().validation = null;
    const closingReport = joinReport();
    const closingRequests = [];
    let finishCheck;
    sandbox.apiRequest = url => {
        closingRequests.push(url);
        return new Promise(resolve => { finishCheck = resolve; });
    };
    const checking = sandbox.submitVideoJoin({ preventDefault: noop });
    sandbox.closeModal('join-video-modal');
    finishCheck(closingReport);
    await checking;
    assert.strictEqual(state(), null);
    assert.deepStrictEqual(closingRequests, ['/videos/join/validate']);

    setup();
    const notifications = [];
    sandbox.showNotification = message => notifications.push(message);
    let rejectCreation;
    sandbox.apiRequest = () => new Promise((resolve, reject) => { rejectCreation = reject; });
    const pending = sandbox.submitVideoJoin({ preventDefault: noop });
    sandbox.closeModal('join-video-modal');
    assert.strictEqual(state(), null);
    rejectCreation(new Error('Busy after closing'));
    await pending;
    assert(notifications.some(message => message.includes('Closing does not cancel')));
    assert(notifications.some(message => message.includes('Join could not start: Busy after closing')));
    sandbox.showModal = noop;
    vm.runInContext('videoSelection.selected = new Set([99])', sandbox);
    sandbox.apiRequest = async url => {
        if (url === '/videos/99') throw new Error('Selected video was deleted');
        return [{ id: 2, name: 'Still available', duration_seconds: 1 }];
    };
    await sandbox.showJoinVideoModal();
    assert.strictEqual(state().candidates[0].name, 'Still available');
    assert.match(document.getElementById('join-video-errors').textContent, /deleted/);
    assert.strictEqual(state().busy, false);
    assert(capturedKeys.has(sandbox.handleJoinModalKeydown));
    sandbox.openJoinPicker();
    sandbox.handleJoinModalKeydown({ key: 'Escape', preventDefault: noop, stopPropagation: noop });
    assert.strictEqual(state().pickerOpen, false);
    sandbox.closeModal('join-video-modal');
    assert(!capturedKeys.has(sandbox.handleJoinModalKeydown));

    const RealDate = vm.runInContext('Date', sandbox);
    const originalTimezone = process.env.TZ;
    let now = 0;
    process.env.TZ = 'America/Chicago';
    sandbox.Date = class extends RealDate {
        constructor(...args) { super(...(args.length ? args : [now])); }
        static now() { return now; }
    };
    try {
        vm.runInContext('videoSelection.selected.clear()', sandbox);
        sandbox.apiRequest = async () => [];
        for (const [instant, expectedName] of [
            ['2026-01-02T09:04:59.999Z', 'Joined_Timelapse_20260102_0304'],
            ['2027-01-01T05:59:59.999Z', 'Joined_Timelapse_20261231_2359'],
            ['2027-01-01T06:00:00.000Z', 'Joined_Timelapse_20270101_0000'],
        ]) {
            now = RealDate.parse(instant);
            await sandbox.showJoinVideoModal();
            assert.strictEqual(document.getElementById('join-video-name').value, expectedName,
                'Each opening must generate a fresh, zero-padded local timestamp without seconds');
            document.getElementById('join-video-name').value = 'A previous custom name';
            sandbox.closeModal('join-video-modal');
        }
    } finally {
        sandbox.Date = RealDate;
        if (originalTimezone === undefined) delete process.env.TZ;
        else process.env.TZ = originalTimezone;
    }

    console.log('PASS Review/Join, concise parameter changes, lossy-conversion warnings, padding, incompatible clips, ordering, picker, FPS timing, limits, escaping, focus, stale search, retry, and pending-submit close');
})().catch(error => { console.error(error); process.exitCode = 1; });
