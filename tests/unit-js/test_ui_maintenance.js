'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.env.APP_JS_PATH || '/frontend/static/js/app.js', 'utf8');
const noop = () => {};
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const TEST_TIMEOUT_MS = 2000;

async function runWithTimeout(fn) {
    let timer;
    try {
        await Promise.race([
            Promise.resolve().then(fn),
            new Promise((resolve, reject) => {
                timer = setTimeout(() => reject(new Error(`Timed out after ${TEST_TIMEOUT_MS} ms`)), TEST_TIMEOUT_MS);
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

function harness(path = '/') {
    const ids = new Map(), listeners = new Map(), windowListeners = new Map();
    const timers = new Map(), intervals = new Map(), storage = new Map();
    let nextTimer = 0;
    let document;
    class Element {
        constructor(tag = 'div', id = '') {
            this.tagName = tag.toUpperCase();
            this.id = id;
            this.children = [];
            this.attributes = new Map();
            this.classes = new Set();
            this.events = new Map();
            this.classList = {
                add: name => this.classes.add(name),
                remove: name => this.classes.delete(name),
                contains: name => this.classes.has(name),
                toggle: (name, force = !this.classes.has(name)) => {
                    if (force) this.classes.add(name); else this.classes.delete(name);
                    return force;
                },
            };
            this.style = { setProperty(name, value) { this[name] = value; } };
            this.value = '';
            this.type = '';
            this.textContent = '';
            this.markup = '';
            this.checked = this.disabled = this.hidden = this.inert = false;
            this.isConnected = true;
            this.dataset = {};
            this.offsetParent = {};
        }
        get innerHTML() {
            return this.markup || this.textContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
        set innerHTML(value) { this.markup = value; }
        setAttribute(name, value) {
            this.attributes.set(name, String(value));
            if (name === 'inert') this.inert = true;
        }
        getAttribute(name) { return this.attributes.get(name) ?? null; }
        removeAttribute(name) { this.attributes.delete(name); if (name === 'inert') this.inert = false; }
        matches(selector) {
            if (selector === ':disabled') return this.disabled || !!this.parentElement?.closest('fieldset:disabled');
            const nots = [...selector.matchAll(/:not\(([^)]+)\)/g)].map(match => match[1]);
            if (nots.some(part => this.matches(part))) return false;
            selector = selector.replace(/:not\([^)]+\)/g, '');
            if (selector.includes(':disabled') && !this.matches(':disabled')) return false;
            const tag = selector.match(/^[a-z]+/i)?.[0];
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            const id = selector.match(/#([\w-]+)/)?.[1];
            if (id && this.id !== id) return false;
            if ([...selector.matchAll(/\.([\w-]+)/g)].some(match => !this.classes.has(match[1]))) return false;
            for (const [, name, value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
                const actual = name === 'inert' ? (this.inert ? '' : null) :
                    name === 'type' ? this.type || null : this.getAttribute(name);
                if (actual === null || (value !== undefined && actual !== value)) return false;
            }
            return true;
        }
        closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
        contains(node) { return node === this || this.children.some(child => child.contains(node)); }
        appendChild(node) {
            if (node.parentElement) node.parentElement.children = node.parentElement.children.filter(child => child !== node);
            node.parentElement = node.parentNode = this;
            this.children.push(node);
            return node;
        }
        querySelectorAll(selector) {
            const nodes = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
            return nodes.filter(node => selector.split(',').some(part => node.matches(part.trim())));
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        addEventListener(type, handler) {
            if (!this.events.has(type)) this.events.set(type, []);
            this.events.get(type).push(handler);
        }
        removeEventListener(type, handler) {
            this.events.set(type, (this.events.get(type) || []).filter(fn => fn !== handler));
        }
        dispatchEvent(event) { (this.events.get(event.type) || []).forEach(fn => fn(event)); }
        focus() { document.activeElement = this; }
        getClientRects() {
            return this.hidden || this.style.display === 'none' || this.style.visibility === 'hidden' ? [] : [1];
        }
        load() {}
        pause() {}
        reset() {}
    }
    const body = new Element('body');
    const get = id => {
        if (!ids.has(id)) {
            const el = new Element('div', id);
            ids.set(id, el);
            body.appendChild(el);
        }
        return ids.get(id);
    };
    document = {
        body, documentElement: new Element('html'), activeElement: body,
        getElementById: get, createElement: tag => new Element(tag),
        querySelectorAll: selector => body.querySelectorAll(selector),
        querySelector: selector => body.querySelector(selector),
        addEventListener(type, fn, capture = false) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push({ fn, capture });
        },
        removeEventListener(type, fn) {
            listeners.set(type, (listeners.get(type) || []).filter(entry => entry.fn !== fn));
        },
    };
    const location = { pathname: path, search: '' };
    const historyCalls = [];
    const history = {
        pushState(state, title, pathname) {
            historyCalls.push(['push', state]);
            if (pathname) location.pathname = pathname;
        },
        replaceState(state, title, pathname) { if (pathname) location.pathname = pathname; },
        back() { historyCalls.push(['back']); },
    };
    const sandbox = {
        document, location, history, console: { ...console, error: noop },
        getComputedStyle(node) {
            for (let cursor = node; cursor; cursor = cursor.parentElement) {
                if (cursor.style.visibility) return { visibility: cursor.style.visibility };
            }
            return { visibility: 'visible' };
        },
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        navigator: { userAgent: 'node-test' }, URL, URLSearchParams, AbortController,
        setTimeout(fn, ms) { timers.set(++nextTimer, { fn, ms }); return nextTimer; },
        clearTimeout: id => timers.delete(id),
        setInterval(fn, ms) { intervals.set(++nextTimer, { fn, ms }); return nextTimer; },
        clearInterval: id => intervals.delete(id),
        addEventListener(type, fn) {
            if (!windowListeners.has(type)) windowListeners.set(type, []);
            windowListeners.get(type).push(fn);
        },
        matchMedia: () => ({ matches: false }), Image: function () {},
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    const notifications = [];
    sandbox.showNotification = (...args) => notifications.push(args);
    for (const name of ['loadHomepage', 'loadJobs', 'loadVideos', 'loadStorage', 'loadSettings']) sandbox[name] = noop;
    const run = script => vm.runInContext(script, sandbox);
    const modal = (id, controls = []) => {
        const node = get(id);
        node.classList.add('modal');
        controls.forEach(control => node.appendChild(control));
        return node;
    };
    const control = (tag = 'button', id = '') => {
        const node = new Element(tag, id);
        if (id) ids.set(id, node);
        return node;
    };
    const key = (key, opts = {}) => {
        const event = { key, ...opts, preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.immediate = this.stopped = true; } };
        const ordered = [...(listeners.get('keydown') || [])].sort((a, b) => Number(b.capture) - Number(a.capture));
        for (const { fn, capture } of ordered) {
            if (event.immediate || (event.stopped && !capture)) break;
            fn(event);
        }
        return event;
    };
    return { sandbox, document, get, run, modal, control, key, notifications, timers, intervals, historyCalls,
        pop: state => (windowListeners.get('popstate') || []).forEach(fn => fn({ state })),
        windowEvent: (type, event) => (windowListeners.get(type) || []).forEach(fn => fn(event)),
        tick(ms) {
            for (const [id, timer] of [...timers]) if (timer.ms === ms) { timers.delete(id); timer.fn(); }
            for (const timer of [...intervals.values()]) if (timer.ms === ms) timer.fn();
        },
    };
}
const video = (id, status = 'completed') => ({
    id, status, name: `Video ${id}`, quality: 'high', resolution: '160x96',
    framerate: 5, total_frames: 10, duration_seconds: 2, file_size: 1000,
    created_at: '2026-01-01T00:00:00Z', progress: 10,
});

function historyDriver(h, previousPath = '/jobs/19') {
    const entries = [
        { path: previousPath, state: { path: previousPath } },
        { path: h.sandbox.location.pathname, state: { path: h.sandbox.location.pathname } },
    ];
    let index = 1, target = null, coalesced = 0;
    const calls = [];
    // Model an asynchronous browser that coalesces overlapping traversals.
    h.sandbox.history.pushState = (state, title, path = entries[index].path) => {
        calls.push(['push', state, path]);
        entries.splice(index + 1);
        entries.push({ state, path });
        index++;
        h.sandbox.location.pathname = path;
    };
    h.sandbox.history.replaceState = (state, title, path = entries[index].path) => {
        calls.push(['replace', state, path]);
        entries[index] = { state, path };
        h.sandbox.location.pathname = path;
    };
    h.sandbox.history.back = () => {
        calls.push(['back']);
        if (target !== null) { coalesced++; return; }
        if (index > 0) target = index - 1;
    };
    return {
        calls, get coalesced() { return coalesced; },
        next() {
            if (target === null) return false;
            index = target;
            target = null;
            h.sandbox.location.pathname = entries[index].path;
            h.pop(entries[index].state);
            return true;
        },
        drain() {
            let events = 0;
            while (this.next()) assert(++events < 20, 'History traversal must drain in a bounded number of events');
        },
        back() { h.sandbox.history.back(); this.drain(); },
    };
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test('FPS population', () => {
    const h = harness();
    assert.match(h.sandbox.renderJobScheduleSection({ framerate: 5 }), /id="edit_framerate"[^>]*value="5"/);
});
test('FPS-only dirty tracking', () => {
    const h = harness();
    h.sandbox.updateEditDurationEstimate = noop;
    h.get('edit_framerate').value = '5';
    h.sandbox.setupJobEditChangeTracking({});
    assert.strictEqual(h.get('save-job-btn').dataset.editReady, 'false');
    h.tick(50);
    assert.strictEqual(h.get('save-job-btn').dataset.editReady, 'true');
    assert(h.get('save-job-btn').disabled);
    h.get('edit_framerate').value = '6';
    h.get('edit_framerate').dispatchEvent({ type: 'input' });
    assert(!h.get('save-job-btn').disabled);
    h.get('edit_framerate').value = '5';
    h.get('edit_framerate').dispatchEvent({ type: 'change' });
    assert(h.get('save-job-btn').disabled);
});
function jobForm(h) {
    h.get('edit_interval_seconds').value = '60';
    h.get('edit_device_path').value = 'https://picsum.photos/seed/chronosnap-e2e/200.jpg';
    h.sandbox.readOverlayConfig = () => null;
    h.sandbox.loadJobDetail = noop;
}
test('FPS PATCH payload', async () => {
    const h = harness();
    jobForm(h);
    const requests = [];
    h.sandbox.apiRequest = async (url, options) => requests.push({ url, options });
    for (const fps of [1, 5, 120]) {
        h.get('edit_framerate').value = String(fps);
        await h.sandbox.saveJobChanges(7);
        assert.strictEqual(requests.at(-1).options.body.framerate, fps);
    }
});
test('FPS invalid values', async () => {
    const h = harness();
    jobForm(h);
    h.sandbox.updateEditDurationEstimate = noop;
    h.get('edit_framerate').value = '5';
    h.sandbox.setupJobEditChangeTracking({});
    h.tick(50);
    let requests = 0;
    h.sandbox.apiRequest = async () => { requests++; };
    for (const value of ['', '0', '-1', '121', '1.5', '12abc', 'Infinity']) {
        h.get('edit_framerate').value = value;
        h.get('edit_framerate').dispatchEvent({ type: 'input' });
        assert.strictEqual(h.get('save-job-btn').disabled, false, 'Invalid FPS remains an unsaved change');
        await h.sandbox.saveJobChanges(7);
        assert.strictEqual(requests, 0, `Invalid FPS ${JSON.stringify(value)} must not PATCH`);
        assert.match(h.notifications.at(-1)[0], /Framerate/);
        assert.strictEqual(h.get('save-job-btn').disabled, false, 'Save-time validation must not silently disable the editor');
    }
});
test('video latest navigation and stale errors', async () => {
    const h = harness('/timelapses/29');
    const old = deferred(), latest = deferred();
    h.sandbox.apiRequest = url => url === '/videos/29' ? old.promise : latest.promise;
    h.sandbox.handleRoute();
    h.sandbox.navigateTo('/timelapses/43');
    latest.resolve(video(43));
    await flush();
    old.resolve(video(29, 'processing'));
    await flush();
    assert.strictEqual(h.get('video-detail-title').textContent, 'Video 43');
    assert.strictEqual(h.run('_currentVideoDetailId'), 43);
    assert.strictEqual(h.timers.size + h.intervals.size, 0, 'Stale processing response must not start a poll');
    const error = deferred();
    h.sandbox.apiRequest = () => error.promise;
    const pending = h.sandbox.loadVideoDetail(43);
    h.sandbox.navigateTo('/jobs');
    error.reject(new Error('Old detail failed'));
    await pending;
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    assert.strictEqual(h.run('_currentVideoDetailId'), null);
    assert.strictEqual(h.notifications.length, 0);
});
test('video route-away and same-ID re-entry', async () => {
    const h = harness('/timelapses/29');
    const old = deferred(), latest = deferred();
    let calls = 0;
    h.sandbox.apiRequest = () => ++calls === 1 ? old.promise : latest.promise;
    h.sandbox.handleRoute();
    h.sandbox.navigateTo('/jobs');
    h.sandbox.navigateTo('/timelapses/29');
    latest.resolve({ ...video(29), name: 'New visit' });
    await flush();
    old.resolve({ ...video(29), name: 'Previous visit' });
    await flush();
    assert.strictEqual(h.get('video-detail-title').textContent, 'New visit');
});
test('video processing polls serialize slow requests', async () => {
    const h = harness('/timelapses/29');
    let calls = 0;
    const slow = deferred();
    h.sandbox.apiRequest = () => ++calls === 1 ? Promise.resolve(video(29, 'processing')) : slow.promise;
    h.sandbox.handleRoute();
    await flush();
    h.tick(3000);
    h.tick(3000);
    h.tick(3000);
    assert.strictEqual(calls, 2, 'A slow processing GET must not overlap another poll');
    slow.resolve({ ...video(29, 'processing'), name: 'Slow result accepted' });
    await flush();
    assert.strictEqual(h.get('video-detail-title').textContent, 'Slow result accepted');
    assert.strictEqual(h.timers.size + h.intervals.size, 1, 'Only one next poll may be scheduled');
    h.sandbox.navigateTo('/jobs');
    h.tick(3000);
    assert.strictEqual(calls, 2);
});
test('off-route refresh cannot invalidate the active request', async () => {
    for (const kind of ['video', 'job']) {
        const isVideo = kind === 'video';
        const h = harness(isVideo ? '/timelapses/43' : '/jobs/43');
        const latest = deferred();
        h.sandbox.apiRequest = url => url === '/captures/' ? Promise.resolve({}) : latest.promise;
        if (!isVideo) {
            h.sandbox.initJobDetailPostRender = noop;
            for (const name of ['renderJobSidebar', 'renderJobWarning', 'renderJobTimeWindow', 'renderJobSourceSection',
                'renderJobScheduleSection', 'renderJobAutoBuildSection']) h.sandbox[name] = () => '';
        }
        h.sandbox.handleRoute();
        await h.sandbox[isVideo ? 'loadVideoDetail' : 'loadJobDetail'](29);
        latest.resolve(isVideo ? video(43) : { id: 43, name: 'Latest job' });
        await flush();
        assert.strictEqual(h.get(`${kind}-detail-title`).textContent, isVideo ? 'Video 43' : 'Latest job');
    }
});
test('job detail latest response and stale failures', async () => {
    const h = harness('/jobs/1');
    const old = deferred(), fresh = deferred();
    h.sandbox.initJobDetailPostRender = noop;
    for (const name of ['renderJobSidebar', 'renderJobWarning', 'renderJobTimeWindow', 'renderJobSourceSection',
        'renderJobScheduleSection', 'renderJobAutoBuildSection']) h.sandbox[name] = () => '';
    h.sandbox.apiRequest = url => url === '/jobs/1' ? old.promise : url === '/jobs/2' ? fresh.promise : Promise.resolve({});
    h.sandbox.handleRoute();
    h.sandbox.navigateTo('/jobs/2');
    fresh.resolve({ id: 2, name: 'Latest job' });
    await flush();
    old.resolve({ id: 1, name: 'Stale job' });
    await flush();
    assert.strictEqual(h.get('job-detail-title').textContent, 'Latest job');
    const pending = deferred();
    h.sandbox.apiRequest = () => pending.promise;
    const loading = h.sandbox.loadJobDetail(2);
    h.sandbox.navigateTo('/settings');
    pending.reject(new Error('Old job failed'));
    await loading;
    assert.strictEqual(h.sandbox.location.pathname, '/settings');
    assert.strictEqual(h.notifications.length, 0);
});
test('captures latest page/filter and route lifetime', async () => {
    const h = harness('/captures');
    h.run("currentView = 'captures'");
    const first = deferred(), second = deferred();
    let count = 0;
    const rendered = [];
    h.sandbox.apiRequest = () => ++count === 1 ? first.promise : second.promise;
    h.sandbox.renderCaptures = captures => rendered.push(captures.map(capture => capture.id));
    h.sandbox.renderPagination = noop;
    h.run('captureSelection.updateControls = () => {}');
    const old = h.sandbox.loadCapturesPage();
    h.run("capturesState.currentPage = 2; capturesState.tagFilter = '3'");
    const latest = h.sandbox.loadCapturesPage();
    second.resolve({ total: 1, captures: [{ id: 2 }] });
    await latest;
    first.resolve({ total: 10, captures: [{ id: 1 }] });
    await old;
    assert.deepStrictEqual(rendered, [[2]], 'Pagination replaces rather than appends and must reject an old filter');
    const pending = deferred();
    h.sandbox.apiRequest = () => pending.promise;
    const loading = h.sandbox.loadCapturesPage();
    h.sandbox.navigateTo('/jobs');
    pending.reject(new Error('Old captures failed'));
    await loading;
    assert.strictEqual(h.notifications.length, 0);
});
test('job post-render async controls respect route lifetime', async () => {
    const h = harness('/jobs/7');
    h.sandbox.initializeEditTimePickers = h.sandbox.initEditJobOverlay = h.sandbox.setupJobEditChangeTracking =
        h.sandbox.renderTagPicker = noop;
    const devices = deferred(), range = deferred();
    h.sandbox.apiRequest = () => devices.promise;
    h.sandbox.fetch = () => range.promise;
    let active = true;
    h.sandbox.initJobDetailPostRender({ id: 7, stream_type: 'device', url: '/dev/disposable-camera',
        capture_count: 1 }, {}, () => active);
    active = false;
    h.get('edit_device_path').innerHTML = 'Current device choices';
    h.get('edit_auto_build_resolution').innerHTML = 'Current resolutions';
    devices.resolve([{ path: '/dev/disposable-camera', name: 'Synthetic unit-test device' }]);
    range.resolve({ json: async () => ({ native_resolution: '160x96' }) });
    await flush();
    assert.strictEqual(h.get('edit_device_path').innerHTML, 'Current device choices');
    assert.strictEqual(h.get('edit_device_path').value, '');
    assert.strictEqual(h.get('edit_auto_build_resolution').children.length, 0);
    const caps = deferred();
    h.sandbox.apiRequest = () => caps.promise;
    active = true;
    const pending = h.sandbox.loadOverlayPreviewImage('edit-ab', { id: 7 }, () => active);
    active = false;
    h.get('edit-ab-overlay-preview-placeholder').innerHTML = 'Current placeholder';
    caps.resolve({ captures: [] });
    await pending;
    assert.strictEqual(h.get('edit-ab-overlay-preview-placeholder').innerHTML, 'Current placeholder');
});
test('captures job-filter setup rejects route-away results', async () => {
    const h = harness('/captures');
    h.get('captures-job-filter').options = [{}];
    h.run("currentView = 'captures'");
    const jobs = deferred();
    const requests = [];
    h.sandbox.apiRequest = url => { requests.push(url); return jobs.promise; };
    const loading = h.sandbox.loadCaptures();
    h.sandbox.navigateTo('/settings');
    jobs.resolve([{ id: 7, name: 'Stale filter option' }]);
    await loading;
    assert.deepStrictEqual(requests, ['/jobs/']);
    assert.strictEqual(h.get('captures-job-filter').children.length, 0);
    assert.strictEqual(h.notifications.length, 0);
});
test('inactive shared toggle groups preserve independent disabled state', () => {
    const h = harness();
    h.sandbox.updateDurationEstimate = noop;
    for (const [checkboxId, groupId, toggle] of [
        ['time_window_enabled', 'time-window-fields', () => h.sandbox.toggleTimeWindow()],
        ['auto_build_enabled', 'auto-build-fields', () => h.sandbox.toggleAutoBuildFields()],
        ['edit_time_window_enabled', 'edit-time-window-fields', () => h.sandbox.toggleEditTimeWindow()],
        ['edit_auto_build_enabled', 'edit-auto-build-fields', () => h.sandbox.toggleEditAutoBuildFields()],
        ['use_range', 'capture-range-fields', () => h.sandbox.toggleFieldGroup('use_range', 'capture-range-fields')],
    ]) {
        const group = h.get(groupId);
        const field = h.control('input'), independentlyDisabled = h.control('input');
        independentlyDisabled.disabled = true;
        group.appendChild(field);
        group.appendChild(independentlyDisabled);
        h.get(checkboxId).checked = false;
        toggle();
        assert.strictEqual(group.inert, true, `${groupId} must be excluded from focus and the accessibility tree`);
        assert(group.classList.contains('disabled'));
        h.sandbox.testField = field;
        assert.strictEqual(h.run('canFocusModalTarget(testField)'), false);
        h.get(checkboxId).checked = true;
        toggle();
        assert.strictEqual(group.inert, false);
        assert(!group.classList.contains('disabled'));
        assert.strictEqual(h.run('canFocusModalTarget(testField)'), true);
        assert.strictEqual(independentlyDisabled.disabled, true, 'Enabling a group must not enable an independently disabled control');
        assert.strictEqual(field.disabled, false, 'Group state must not overwrite individual input state');
    }
});
test('inactive generated editor and overlay markup starts inert', () => {
    const h = harness();
    for (const enabled of [false, true]) {
        for (const [id, html] of [
            ['edit-time-window-fields', h.sandbox.renderJobScheduleSection({ time_window_enabled: enabled })],
            ['edit-auto-build-fields', h.sandbox.renderJobAutoBuildSection({ auto_build_enabled: enabled })],
        ]) {
            const openingTag = html.match(new RegExp(`<div id="${id}"[^>]+>`))[0];
            assert.strictEqual(/\binert\b/.test(openingTag), !enabled, `${id} must render its actual interactive state`);
        }
    }
    for (const prefix of ['build', 'create-ab', 'edit-ab']) {
        const openingTag = h.sandbox.generateOverlayHTML(prefix).match(new RegExp(`<div id="${prefix}-overlay-fields"[^>]+>`))[0];
        assert(/\binert\b/.test(openingTag));
    }
});
test('inactive overlay initialization toggling and config writes agree', () => {
    const h = harness();
    h.sandbox.loadOverlayFonts = noop;
    for (const prefix of ['build', 'create-ab', 'edit-ab']) {
        const checkbox = h.get(`${prefix}-overlay-enabled`), group = h.get(`${prefix}-overlay-fields`);
        const field = h.control('input');
        group.appendChild(field);
        h.sandbox.initOverlayWidget(prefix);
        assert.strictEqual(group.inert, true, 'Widget initialization must match the unchecked checkbox');
        checkbox.checked = true;
        checkbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(group.inert, false);
        assert(!group.classList.contains('disabled'));
        h.sandbox.writeOverlayConfig(prefix, { enabled: false });
        assert.strictEqual(group.inert, true);
        assert(group.classList.contains('disabled'));
        h.sandbox.writeOverlayConfig(prefix, { enabled: true });
        assert.strictEqual(group.inert, false);
        assert(!group.classList.contains('disabled'));
        checkbox.checked = false;
        checkbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(group.inert, true);
    }
});
test('inactive create reset and duplicate paths restore group state', async () => {
    const h = harness();
    for (const name of ['setSourceType', 'setDefaultStartTime', 'updateNamingPreview', 'updateEndDateMin',
        'showModal', 'renderTagPicker', 'updateDurationEstimate']) h.sandbox[name] = noop;
    const timeGroup = h.get('time-window-fields'), autoGroup = h.get('auto-build-fields');
    h.sandbox.apiRequest = async () => ({});
    await h.sandbox.showCreateJobModal();
    assert.strictEqual(timeGroup.inert, true);
    assert.strictEqual(autoGroup.inert, true);
    for (const enabled of [true, false]) {
        h.sandbox.apiRequest = async () => ({
            name: 'Synthetic job', url: 'https://picsum.photos/seed/chronosnap-e2e/200.jpg',
            time_window_enabled: enabled, auto_build_enabled: enabled,
        });
        await h.sandbox.duplicateJob(7);
        assert.strictEqual(timeGroup.inert, !enabled);
        assert.strictEqual(autoGroup.inert, !enabled);
    }
    assert.strictEqual(h.notifications.length, 0);
});
test('inactive build resets and job population restore capture range state', async () => {
    const h = harness();
    for (const name of ['resetOverlayPreview', 'populateJobSelector', 'showModal', 'renderTagPicker',
        'updateVideoDurationEstimate']) h.sandbox[name] = noop;
    h.document.querySelector = () => h.get('build-heading');
    const group = h.get('capture-range-fields');
    await h.sandbox.showProcessVideoModal();
    assert.strictEqual(group.inert, true);
    h.get('use_range').checked = true;
    h.sandbox.toggleFieldGroup('use_range', group.id);
    assert.strictEqual(group.inert, false);
    h.sandbox.fetch = async () => ({ json: async () => ({ framerate: 5, count: 0 }) });
    h.sandbox.apiRequest = async () => ({ captures: [] });
    await h.sandbox.populateVideoFormFromJob(7, 'Synthetic job');
    assert.strictEqual(group.inert, true);
    assert.strictEqual(h.get('use_range').checked, false);
    assert.strictEqual(h.notifications.length, 0);
});
test('legacy modal focus Tab cycle and inert restoration', () => {
    const h = harness();
    const opener = h.get('opener');
    opener.focus();
    const first = h.control('input'), last = h.control();
    first.setAttribute('data-modal-initial-focus', '');
    const disabledGroup = h.control('fieldset');
    disabledGroup.disabled = true;
    const disabledField = h.control('input');
    disabledGroup.appendChild(disabledField);
    const hiddenForm = h.control();
    hiddenForm.style.visibility = 'hidden';
    hiddenForm.appendChild(h.control());
    const modal = h.modal('legacy-modal', [first, last, disabledGroup, hiddenForm]);
    const alreadyInert = h.get('already-inert');
    alreadyInert.inert = true;
    h.sandbox.showModal(modal.id);
    assert.strictEqual(h.document.activeElement, first);
    assert(opener.inert);
    assert(!modal.inert);
    assert(h.key('Tab', { shiftKey: true }).defaultPrevented);
    assert.strictEqual(h.document.activeElement, last, 'Disabled fieldset descendants must not be tabbable');
    assert(h.key('Tab').defaultPrevented);
    assert.strictEqual(h.document.activeElement, first);
    h.key('Escape');
    assert(!modal.classList.contains('active'));
    assert(!opener.inert);
    assert(alreadyInert.inert, 'Previously inert UI must stay inert after modal close');
    assert.strictEqual(h.document.activeElement, opener);
});
test('nested Confirm priority cancellation and history', () => {
    const h = harness();
    h.sandbox.setupNavigation();
    const first = h.control(), last = h.control();
    h.modal('legacy-modal', [first, last]);
    const cancel = h.control();
    cancel.setAttribute('data-modal-initial-focus', '');
    const confirm = h.modal('confirm-modal', [cancel]);
    h.modal('later-dom-modal');
    h.sandbox.showModal('legacy-modal');
    last.focus();
    let decision = null;
    h.sandbox.showConfirm('Really?', confirmed => { decision = confirmed; });
    assert.strictEqual(h.document.activeElement, cancel);
    assert(h.get('legacy-modal').inert);
    h.key('Escape');
    assert.strictEqual(decision, false);
    assert(!confirm.classList.contains('active'));
    assert(h.get('legacy-modal').classList.contains('active'));
    assert.strictEqual(h.document.activeElement, last);
    h.pop({ modal: true, depth: 1 });
    assert(h.get('legacy-modal').classList.contains('active'), 'Our own history.back must not close the underlying dialog');
    h.key('Escape', { repeat: true });
    assert(h.get('legacy-modal').classList.contains('active'), 'Held Escape must not close the underlying dialog');
    h.pop(null);
    assert(!h.get('legacy-modal').classList.contains('active'));
    assert.strictEqual(h.run('_modalOpenState.size'), 0);
});
test('history serializes same-tick nested closes on coalescing browsers', () => {
    const h = harness('/jobs');
    const driver = historyDriver(h);
    h.sandbox.setupNavigation();
    const opener = h.get('opener');
    opener.focus();
    h.modal('legacy-modal', [h.control()]);
    h.modal('confirm-modal', [h.control()]);
    h.sandbox.showModal('legacy-modal');
    let decision;
    h.sandbox.showConfirm('Same-tick close', confirmed => { decision = confirmed; });
    h.sandbox.closeConfirmModal(false);
    h.sandbox.closeModal('legacy-modal');
    assert.strictEqual(decision, false);
    assert.strictEqual(h.document.querySelectorAll('.modal.active').length, 0);
    assert.strictEqual(driver.calls.filter(call => call[0] === 'back').length, 1,
        'The second close must wait for the first traversal event');
    driver.drain();
    assert.strictEqual(driver.coalesced, 0);
    assert.strictEqual(h.run('_pendingModalBacks'), 0);
    assert.strictEqual(h.run('_modalOpenState.size'), 0);
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    assert.strictEqual(h.document.activeElement, opener);
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs/19', 'A later Back must not be swallowed');
});
test('history defers fast reopen pushes until previous closes drain', () => {
    const h = harness('/jobs');
    const driver = historyDriver(h);
    h.sandbox.setupNavigation();
    h.sandbox.loadJobDetail = noop;
    const opener = h.get('opener');
    opener.focus();
    const modal = h.modal('create-job-modal', [h.control()]);
    h.modal('confirm-modal', [h.control()]);
    h.sandbox.showModal(modal.id);
    h.sandbox.showConfirm('Nested Escape', noop);
    h.key('Escape');
    h.key('Escape');
    assert.strictEqual(h.document.activeElement, opener);
    h.sandbox.showModal(modal.id);
    assert.strictEqual(driver.calls.filter(call => call[0] === 'push').length, 2,
        'Reopening must not push onto a history index still being traversed');
    driver.drain();
    assert(modal.classList.contains('active'), 'Finishing old traversals must not close the reopened dialog');
    assert.strictEqual(driver.calls.filter(call => call[0] === 'push').length, 3);
    driver.back();
    assert(!modal.classList.contains('active'));
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    assert.strictEqual(h.document.activeElement, opener);
    assert.strictEqual(h.run('_pendingModalBacks'), 0);
    assert.strictEqual(h.run('_modalOpenState.size'), 0);
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs/19');
});
test('history cancels a deferred opening closed before it commits', () => {
    const h = harness('/jobs');
    const driver = historyDriver(h);
    h.sandbox.setupNavigation();
    h.modal('legacy-modal', [h.control()]);
    h.modal('confirm-modal', [h.control()]);
    h.sandbox.showModal('legacy-modal');
    h.sandbox.showConfirm('Closing both', noop);
    h.sandbox.closeConfirmModal(false);
    h.sandbox.closeModal('legacy-modal');
    h.sandbox.showModal('legacy-modal');
    h.sandbox.closeModal('legacy-modal');
    driver.drain();
    assert.strictEqual(driver.calls.filter(call => call[0] === 'push').length, 2,
        'A cancelled deferred opening must never create a history entry');
    assert.strictEqual(driver.calls.filter(call => call[0] === 'back').length, 2);
    assert.strictEqual(h.run('_pendingModalBacks'), 0);
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs/19');
});
test('history defers SPA navigation until modal traversals drain', () => {
    const h = harness('/jobs');
    const driver = historyDriver(h);
    h.sandbox.setupNavigation();
    h.modal('legacy-modal', [h.control()]);
    h.modal('confirm-modal', [h.control()]);
    h.sandbox.showModal('legacy-modal');
    h.sandbox.showConfirm('Navigate after closing', noop);
    h.sandbox.closeConfirmModal(false);
    h.sandbox.navigateTo('/settings');
    h.sandbox.navigateTo('/storage');
    assert.strictEqual(h.sandbox.location.pathname, '/jobs', 'Navigation must not replace the in-flight traversal target');
    driver.drain();
    assert.strictEqual(h.sandbox.location.pathname, '/storage');
    assert.strictEqual(h.document.querySelectorAll('.modal.active').length, 0);
    assert.strictEqual(h.run('_pendingModalBacks'), 0);
    assert.strictEqual(driver.calls.filter(call => call[0] === 'push' && call[2] === '/settings').length, 0,
        'Only the latest queued route navigation should commit');
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    assert.strictEqual(h.run('currentView'), 'jobs');
});
test('history queued navigation closes a modal opened while traversal was pending', () => {
    const h = harness('/jobs');
    const driver = historyDriver(h);
    h.sandbox.setupNavigation();
    h.sandbox.loadJobDetail = noop;
    h.modal('modal-a', [h.control()]);
    const modalB = h.modal('modal-b', [h.control()]);
    h.sandbox.showModal('modal-a');
    h.sandbox.closeModal('modal-a');
    h.sandbox.navigateTo('/settings');
    h.sandbox.showModal('modal-b');
    assert(modalB.classList.contains('active'));
    assert.strictEqual(h.run("_modalOpenState.get(document.getElementById('modal-b')).historyPushed"), false);
    driver.drain();
    assert.strictEqual(h.sandbox.location.pathname, '/settings');
    assert(!modalB.classList.contains('active'), 'Committed route navigation must close the deferred modal');
    assert.strictEqual(h.run('_modalOpenState.size'), 0);
    assert.strictEqual(h.run('_pendingModalBacks'), 0);
    assert.strictEqual(h.run('_modalHistoryBackInFlight'), false);
    assert.strictEqual(h.run('_modalHistoryNavigation'), null);
    assert.strictEqual(driver.calls.filter(call => call[0] === 'push' && call[1].modal).length, 1,
        'The modal opened during navigation must never leave a phantom history entry');
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs');
    assert.strictEqual(h.document.querySelectorAll('.modal.active').length, 0);
    driver.back();
    assert.strictEqual(h.sandbox.location.pathname, '/jobs/19');
});
test('history successful Trim preserves the source route after committed or deferred opening', async () => {
    for (const deferredOpening of [false, true]) {
        const h = harness('/timelapses/43');
        const driver = historyDriver(h, '/jobs');
        h.sandbox.setupNavigation();
        h.run("currentView = 'video-detail'");
        h.sandbox.renderVideoTrim = h.sandbox.refreshEventsSoon = h.sandbox.loadVideoDetail = noop;
        if (deferredOpening) {
            h.modal('legacy-modal', [h.control()]);
            h.sandbox.showModal('legacy-modal');
            h.sandbox.closeModal('legacy-modal');
        }
        h.modal('trim-video-modal', [h.control()]);
        h.sandbox.showModal('trim-video-modal');
        h.sandbox.testTrim = {
            videoId: 43, start: 1, end: 0, busy: false, invalid: false,
            info: { revision: 'r' }, controller: new AbortController(),
        };
        h.run('_trimState = testTrim');
        h.sandbox.apiRequest = async () => ({ id: 99 });
        await h.sandbox.submitVideoTrim({ preventDefault: noop });
        assert.strictEqual(h.run('_trimState'), null);
        driver.drain();
        assert.strictEqual(h.sandbox.location.pathname, '/timelapses/99');
        assert.strictEqual(h.run('_pendingModalBacks'), 0);
        assert.strictEqual(driver.coalesced, 0);
        driver.back();
        assert.strictEqual(h.sandbox.location.pathname, '/timelapses/43',
            'Even an opening whose push was deferred must not replace its source route');
    }
});
test('Confirm takes priority over Join and Trim keyboard traps', () => {
    for (const kind of ['join', 'trim']) {
        const h = harness();
        const cancel = h.control(), accept = h.control();
        cancel.setAttribute('data-modal-initial-focus', '');
        h.modal('confirm-modal', [cancel, accept]);
        const underlying = h.modal(`${kind}-video-modal`, [h.control()]);
        h.run(kind === 'join' ? '_joinState = { pickerOpen: true }' : '_trimState = {}');
        h.document.addEventListener('keydown', h.sandbox[kind === 'join' ?
            'handleJoinModalKeydown' : 'handleTrimModalKeydown'], true);
        h.sandbox.showModal(underlying.id);
        let decision;
        h.sandbox.showConfirm('Confirm before closing?', confirmed => { decision = confirmed; });
        h.key('Tab', { shiftKey: true });
        assert.strictEqual(h.document.activeElement, accept);
        h.key('Escape');
        assert.strictEqual(decision, false);
        assert(underlying.classList.contains('active'));
        assert(h.run(kind === 'join' ? '_joinState.pickerOpen' : '_trimState !== null'));
    }
});
test('generic comparison close cleans playback and animation', () => {
    const h = harness();
    let paused = 0, cancelled = 0;
    const player = { pause() { paused++; }, currentTime: 9 };
    h.sandbox.player = player;
    h.sandbox.cancelAnimationFrame = id => { cancelled = id; };
    h.run('comparisonState.players = [player]; comparisonState.playing = true; comparisonState.animFrame = 7');
    h.modal('comparison-modal', [h.control()]);
    h.sandbox.showModal('comparison-modal');
    h.key('Escape');
    assert.strictEqual(paused, 1);
    assert.strictEqual(player.currentTime, 0);
    assert.strictEqual(cancelled, 7);
    assert.strictEqual(h.run('comparisonState.players.length'), 0);
    assert.strictEqual(h.run('comparisonState.playing'), false);
});
test('modal cleanup through backdrop and route navigation', () => {
    const h = harness('/jobs');
    h.sandbox.setupNavigation();
    const opener = h.get('opener');
    opener.focus();
    const modal = h.modal('capture-preview-modal', [h.control()]);
    h.run('capturesState.currentCaptureId = 7');
    h.sandbox.showModal(modal.id);
    h.windowEvent('mousedown', { target: modal });
    h.windowEvent('mouseup', { target: modal });
    assert(!modal.classList.contains('active'));
    assert.strictEqual(h.run('capturesState.currentCaptureId'), null);
    assert(!opener.inert);
    h.sandbox.showModal('capture-preview-modal');
    opener.isConnected = false;
    h.sandbox.navigateTo('/settings');
    assert(!modal.classList.contains('active'));
    assert(!h.get('settings-view').inert);
    assert.notStrictEqual(h.document.activeElement, opener, 'Never restore focus to a removed opener');
});
test('theme radios and stable native focus', () => {
    const h = harness();
    h.sandbox.renderThemePresets();
    const html = h.get('theme-preset-grid').innerHTML;
    assert.strictEqual((html.match(/type="radio"/g) || []).length, 6);
    assert.match(html, /name="theme-preset"/);
    assert.match(html, /value="cosmic"[^>]*checked/);
    assert.match(html, /<label class="theme-preset-card/);
    const cards = ['cosmic', 'ocean'].map(value => {
        const label = h.control('label');
        label.classList.add('theme-preset-card');
        const radio = h.control('input');
        radio.type = 'radio';
        radio.value = value;
        radio.setAttribute('name', 'theme-preset');
        label.appendChild(radio);
        h.get('theme-preset-grid').appendChild(label);
        return radio;
    });
    cards[1].focus();
    h.sandbox.setThemePreset('ocean');
    assert.strictEqual(h.document.activeElement, cards[1]);
    assert(cards[1].checked && !cards[0].checked);
    assert(cards[1].parentElement.classList.contains('active'));
    assert.strictEqual(h.get('theme-preset-grid').innerHTML, html, 'Selecting a radio must not replace the focused input');
});
test('tag display and selection semantics', () => {
    const h = harness();
    const tag = { id: 7, name: '<Private "tag">', color: '#ffffff' };
    h.sandbox.testTag = tag;
    h.run('allTags = [testTag]');
    const display = h.sandbox.tagChipHTML(tag);
    assert.match(display, /^<span class="tag-chip"/);
    assert.match(display, /--tag-color:#ffffff/);
    assert(!/[;"]color:#ffffff;/.test(display));
    assert(display.includes('&lt;Private'));
    h.sandbox.renderTagPicker('picker', [7]);
    const html = h.get('picker').innerHTML;
    assert.match(html, /<button type="button" class="tag-chip selected"/);
    assert.match(html, /aria-pressed="true"/);
    assert(!html.includes('opacity:'));
    const button = h.control();
    button.classList.add('selected');
    h.sandbox.toggleTagInPicker(button);
    assert.strictEqual(button.getAttribute('aria-pressed'), 'false');
    assert.strictEqual(button.style.opacity, undefined);
});
test('generated control labels and canonical tokens', () => {
    const h = harness();
    const sections = [
        h.sandbox.renderJobSourceSection({ url: 'https://picsum.photos/seed/chronosnap-e2e/200.jpg' }),
        h.sandbox.renderJobScheduleSection({ framerate: 5 }),
        h.sandbox.renderJobAutoBuildSection({}),
        h.sandbox.generateOverlayHTML('build'),
    ];
    for (const html of sections) {
        for (const match of html.matchAll(/<(?:input|select)\b[^>]+>/g)) {
            const field = match[0];
            if (/type="hidden"/.test(field)) continue;
            const id = field.match(/\bid="([^"]+)"/)?.[1];
            const wrapped = html.lastIndexOf('<label', match.index) > html.lastIndexOf('</label>', match.index);
            assert(/aria-label(?:ledby)?=/.test(field) || wrapped || (id && html.includes(`for="${id}"`)),
                `Generated field lacks an explicit name: ${field}`);
        }
    }
    assert(!/var\(--(?:bg-secondary|bg-hover|text-muted|bg-primary|hover-bg|input-bg|purple|primary|danger)(?:[,)]|$)/.test(source),
        'All generated consumers must use canonical theme tokens');
});
test('auto-build editor shares responsive field spacing', () => {
    const h = harness();
    const html = h.sandbox.renderJobAutoBuildSection({});
    const openingTag = html.match(/<div id="edit-auto-build-fields"[^>]+>/)[0];
    assert.doesNotMatch(openingTag, /margin-left/,
        'An inline indent must not override mobile field spacing');
    const styles = fs.readFileSync('/frontend/static/css/style.css', 'utf8');
    assert.match(styles, /#auto-build-fields,\s*#edit-auto-build-fields\s*\{\s*margin-left:\s*0;/,
        'Create and edit Auto Build fields must both fit narrow screens');
});
test('main interface uses local system fonts and assets', () => {
    const html = fs.readFileSync('/frontend/index.html', 'utf8');
    const styles = fs.readFileSync('/frontend/static/css/style.css', 'utf8');
    assert.match(styles, /body\s*\{[^}]*font-family:\s*system-ui\b/,
        'Use the platform system font without downloading a font');
    assert.doesNotMatch(html, /<(?:link|script)\b[^>]+(?:href|src)=["'](?:https?:)?\/\//i,
        'The main interface must load its assets locally');
    assert.doesNotMatch(styles, /(?:@import\s*(?:url\()?|url\()\s*["']?(?:https?:)?\/\//i,
        'Styles must not import external fonts or assets');
});
test('proven orphan functions removed', () => {
    for (const name of ['updateJobEndTime', 'updateJobUrl', 'updateJobInterval', 'toUTCString', 'toISOStringForQuery',
        'performMaintenanceImport', 'performMaintenanceCleanup', 'setupDateTimePickerSync', 'setDateTimePickerValue',
        'showJobDetails', 'joinMoveClip', 'setValues']) {
        assert(!new RegExp(`function ${name}\\(`).test(source), `Remove orphan ${name}`);
    }
    assert(!source.includes('const next = Math.min(remaining, VIDEO_PAGE_SIZE)'));
});

(async () => {
    let failures = 0;
    const selected = tests.filter(({ name }) => !process.env.UI_TEST || name.includes(process.env.UI_TEST));
    for (const { name, fn } of selected) {
        try { await runWithTimeout(fn); console.log(`PASS ${name}`); }
        catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`); }
    }
    console.log(`${selected.length - failures}/${selected.length} UI maintenance tests passed`);
    process.exitCode = failures ? 1 : 0;
})();
