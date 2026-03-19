// API Base URL
const API_BASE = '/api';

// Current state
let currentView = 'jobs';
let currentJobId = null;
let refreshIntervals = [];
let videoRefreshInterval = null;
let confirmCallback = null;

// =============================================================================
// Theme Toggle & Presets
// =============================================================================

const THEME_PRESETS = {
    cosmic:  { name: 'Cosmic',  colors: ['#56b8d6', '#a78bfa', '#e8457e'] },
    ocean:   { name: 'Ocean',   colors: ['#38bdf8', '#818cf8', '#a78bfa'] },
    forest:  { name: 'Forest',  colors: ['#4ade80', '#a3e635', '#f59e0b'] },
    sunset:  { name: 'Sunset',  colors: ['#fb923c', '#f472b6', '#c084fc'] },
    ember:   { name: 'Ember',   colors: ['#ef4444', '#f97316', '#fbbf24'] },
    minimal: { name: 'Minimal', colors: ['#a1a1aa', '#d4d4d8', '#f4f4f5'] },
};

function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
    }
    const preset = localStorage.getItem('themePreset') || 'cosmic';
    document.documentElement.setAttribute('data-preset', preset);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    if (currentView === 'storage') loadStorage();
    renderThemePresets();
}

function setThemePreset(preset) {
    document.documentElement.setAttribute('data-preset', preset);
    localStorage.setItem('themePreset', preset);
    if (currentView === 'storage') loadStorage();
    renderThemePresets();
}

function renderThemePresets() {
    const grid = document.getElementById('theme-preset-grid');
    if (!grid) return;
    const current = localStorage.getItem('themePreset') || 'cosmic';
    grid.innerHTML = Object.entries(THEME_PRESETS).map(([key, preset]) => `
        <div class="theme-preset-card ${key === current ? 'active' : ''}" onclick="setThemePreset('${key}')">
            <div class="theme-preset-swatches">
                ${preset.colors.map(c => `<span style="background:${c};"></span>`).join('')}
            </div>
            <div class="theme-preset-name">${preset.name}</div>
        </div>
    `).join('');
}

function getTimeFormat() {
    return localStorage.getItem('timeFormat') || '24';
}

function setTimeFormat(format) {
    localStorage.setItem('timeFormat', format);
}

// Apply theme immediately (before DOMContentLoaded)
initTheme();

// =============================================================================
// Universal Utility Functions
// =============================================================================

/**
 * Get element value with optional parsing and default
 * @param {string} id - Element ID
 * @param {Object} options - { parse: 'int'|'float'|'bool', default: any }
 */
function getValue(id, options = {}) {
    const element = document.getElementById(id);
    if (!element) return options.default ?? null;
    
    let value = element.type === 'checkbox' ? element.checked : element.value;
    
    // Handle empty string
    if (value === '' && options.default !== undefined) {
        return options.default;
    }
    
    // Parse if requested
    if (options.parse === 'int') return parseInt(value) || options.default || 0;
    if (options.parse === 'float') return parseFloat(value) || options.default || 0;
    if (options.parse === 'bool') return Boolean(value);
    
    return value || options.default || null;
}

/**
 * Set element value (works with inputs, selects, checkboxes)
 */
function setValue(id, value) {
    const element = document.getElementById(id);
    if (!element) return;
    
    if (element.type === 'checkbox') {
        element.checked = Boolean(value);
    } else {
        element.value = value ?? '';
    }
}

/**
 * Universal API request wrapper with error handling
 * @param {string} endpoint - API endpoint (relative to API_BASE)
 * @param {Object} options - fetch options { method, body, query }
 */
async function apiRequest(endpoint, options = {}) {
    const { method = 'GET', body = null, rawBody = null, query = null } = options;
    
    // Build URL with query params
    let url = `${API_BASE}${endpoint}`;
    if (query) {
        const params = new URLSearchParams();
        Object.entries(query).forEach(([key, val]) => {
            if (val !== null && val !== undefined) params.append(key, val);
        });
        const queryStr = params.toString();
        if (queryStr) url += `?${queryStr}`;
    }
    
    const fetchOptions = { method, headers: {} };
    
    if (rawBody) {
        // FormData or other raw body — let browser set Content-Type (multipart boundary)
        fetchOptions.body = rawBody;
    } else if (body) {
        fetchOptions.headers['Content-Type'] = 'application/json';
        fetchOptions.body = JSON.stringify(body);
    }
    
    const response = await fetch(url, fetchOptions);
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        const message = typeof error.detail === 'string' 
            ? error.detail 
            : Array.isArray(error.detail) 
                ? error.detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ')
                : `Request failed: ${response.status}`;
        throw new Error(message);
    }
    
    // Return parsed JSON or null for 204 responses
    return response.status === 204 ? null : await response.json();
}

/**
 * Batch get values from multiple elements
 * @param {Object} config - { elementId: { parse?, default? } }
 * @returns {Object} - { elementId: value }
 */
function getValues(config) {
    const result = {};
    Object.entries(config).forEach(([key, opts]) => {
        result[key] = getValue(key, opts || {});
    });
    return result;
}

/**
 * Batch set values to multiple elements
 * @param {Object} values - { elementId: value }
 */
function setValues(values) {
    Object.entries(values).forEach(([id, value]) => {
        setValue(id, value);
    });
}

/**
 * Clear form elements
 * @param {string[]} ids - Array of element IDs to clear
 */

// =============================================================================
// End Universal Utilities
// =============================================================================

// Notification system
let _notificationTimer = null;

function showNotification(message, type = 'success') {
    const toast = document.getElementById('notification-toast');
    const messageEl = document.getElementById('notification-message');
    
    if (_notificationTimer) clearTimeout(_notificationTimer);
    
    messageEl.textContent = message;
    toast.className = `notification-toast ${type}`;
    
    setTimeout(() => toast.classList.add('show'), 10);
    
    _notificationTimer = setTimeout(() => {
        toast.classList.remove('show');
        _notificationTimer = null;
    }, 3000);
}

function dismissNotification() {
    if (_notificationTimer) {
        clearTimeout(_notificationTimer);
        _notificationTimer = null;
    }
    document.getElementById('notification-toast').classList.remove('show');
}

// ─── Event Log ────────────────────────────────────────────────
let _eventPanelOpen = false;
let _lastSeenEventTimestamp = localStorage.getItem('lastSeenEventTimestamp') || '';
let _eventPollTimer = null;

function toggleEventPanel() {
    const panel = document.getElementById('event-panel');
    _eventPanelOpen = !_eventPanelOpen;
    panel.style.display = _eventPanelOpen ? 'block' : 'none';
    if (_eventPanelOpen) {
        fetchEvents().then(() => {
            // Mark all as seen
            const body = document.getElementById('event-panel-body');
            const firstItem = body.querySelector('.event-item');
            if (firstItem) {
                _lastSeenEventTimestamp = firstItem.dataset.timestamp || '';
                localStorage.setItem('lastSeenEventTimestamp', _lastSeenEventTimestamp);
            }
            updateEventBadge(0);
        });
    }
}

async function fetchEvents() {
    try {
        const res = await fetch('/api/events/');
        if (!res.ok) return;
        const events = await res.json();
        renderEvents(events);
        if (!_eventPanelOpen) {
            const unseen = events.filter(e => e.timestamp > _lastSeenEventTimestamp).length;
            updateEventBadge(unseen);
        }
    } catch (e) { /* silently fail */ }
}

function renderEvents(events) {
    const body = document.getElementById('event-panel-body');
    if (!events.length) {
        body.innerHTML = '<div class="event-empty">No events yet</div>';
        return;
    }
    body.innerHTML = events.map(ev => {
        const validCats = ['job', 'video', 'import', 'export', 'system'];
        const cat = validCats.includes(ev.category) ? ev.category : 'system';
        const time = formatEventTime(ev.timestamp);
        const ts = escapeHtml(ev.timestamp || '');
        return `<div class="event-item" data-timestamp="${ts}">
            <span class="event-item-dot cat-${cat}"></span>
            <div class="event-item-content">
                <div class="event-item-msg">${escapeHtml(ev.message)}</div>
                <div class="event-item-time">${time}</div>
            </div>
        </div>`;
    }).join('');
}

function formatEventTime(isoStr) {
    try {
        const d = new Date(isoStr);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return 'Just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr}h ago`;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
}

function updateEventBadge(count) {
    const btn = document.querySelector('.event-bell');
    if (count > 0) {
        btn.classList.add('has-unseen');
    } else {
        btn.classList.remove('has-unseen');
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function startEventPolling() {
    fetchEvents();
    _eventPollTimer = setInterval(fetchEvents, 30000);
}

function refreshEventsSoon() {
    setTimeout(fetchEvents, 500);
}

// Close event panel on outside click
document.addEventListener('click', (e) => {
    if (!_eventPanelOpen) return;
    const panel = document.getElementById('event-panel');
    const bell = document.querySelector('.event-bell');
    if (!panel.contains(e.target) && !bell.contains(e.target)) {
        _eventPanelOpen = false;
        panel.style.display = 'none';
    }
});
// ─── End Event Log ────────────────────────────────────────────

// Confirmation system
function showConfirm(message, callback) {
    const modal = document.getElementById('confirm-modal');
    const messageEl = document.getElementById('confirm-message');
    
    messageEl.textContent = message;
    confirmCallback = callback;
    
    modal.classList.add('active');
}

function closeConfirmModal(confirmed) {
    const modal = document.getElementById('confirm-modal');
    modal.classList.remove('active');
    
    if (confirmCallback) {
        confirmCallback(confirmed);
        confirmCallback = null;
    }
}

/**
 * Show a confirmation dialog and execute an action if confirmed.
 * Replaces the repeated showConfirm + if(confirmed) pattern.
 * @param {string} message - Confirmation message
 * @param {Function} actionFn - Async function to run if confirmed
 * @param {Object} opts - Options: { closeModalId: string to close before action }
 */
function confirmAction(message, actionFn, opts = {}) {
    showConfirm(message, async (confirmed) => {
        if (confirmed) {
            if (opts.closeModalId) closeModal(opts.closeModalId);
            await actionFn();
        }
    });
}

/**
 * Toggle visibility of a field group and optionally set required attributes.
 * @param {string} checkboxId - ID of the controlling checkbox
 * @param {string} containerId - ID of the container to show/hide
 * @param {Object} opts - { requiredIds: string[], display: string, onToggle: fn }
 */
function toggleFieldGroup(checkboxId, containerId, opts = {}) {
    const enabled = document.getElementById(checkboxId).checked;
    const container = document.getElementById(containerId);
    if (enabled) {
        container.classList.remove('disabled');
    } else {
        container.classList.add('disabled');
    }
    if (opts.requiredIds) {
        opts.requiredIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.required = enabled;
        });
    }
    if (opts.onToggle) opts.onToggle(enabled);
}

/**
 * Set a button's disabled state with consistent styling.
 * @param {string|Element} btnOrId - Button element or ID
 * @param {boolean} disabled - Whether to disable
 */
function setButtonState(btnOrId, disabled) {
    const btn = typeof btnOrId === 'string' ? document.getElementById(btnOrId) : btnOrId;
    if (!btn) return;
    btn.disabled = disabled;
}

// =============================================================================
// SelectionManager — reusable bulk selection for card grids
// =============================================================================

const compareMode = { active: false };

class SelectionManager {
    constructor({ name, cardSelector, dataAttr, controlsId, countId, toggleBtnId, deleteEndpoint, deleteBodyKey, favoriteEndpoint, itemLabel, onReload }) {
        this.name = name;
        this.selected = new Set();
        this.cardSelector = cardSelector;
        this.dataAttr = dataAttr;
        this.controlsId = controlsId;
        this.countId = countId;
        this.toggleBtnId = toggleBtnId;
        this.deleteEndpoint = deleteEndpoint;
        this.deleteBodyKey = deleteBodyKey;
        this.favoriteEndpoint = favoriteEndpoint;
        this.itemLabel = itemLabel;
        this.onReload = onReload;
    }

    has(id) { return this.selected.has(id); }
    get size() { return this.selected.size; }

    handleCardClick(id, event, openFn) {
        if (event.target.type === 'checkbox') return;
        if (compareMode.active || this.selected.size > 0) {
            this.toggle(id, event);
        } else {
            openFn(id);
        }
    }

    toggle(id, event) {
        if (event) event.stopPropagation();
        if (this.selected.has(id)) {
            this.selected.delete(id);
        } else {
            // In compare mode, limit to 4 selections
            if (compareMode.active && this.name === 'videos' && this.selected.size >= 4) return;
            this.selected.add(id);
        }
        const card = document.querySelector(`${this.cardSelector}[${this.dataAttr}="${id}"]`);
        if (card) {
            card.classList.toggle('selected', this.selected.has(id));
            const cb = card.querySelector('.capture-checkbox');
            if (cb) cb.checked = this.selected.has(id);
        }
        this.updateControls();
    }

    toggleAll() {
        const cards = document.querySelectorAll(`${this.cardSelector}[${this.dataAttr}]`);
        const allSelected = cards.length > 0 && [...cards].every(c => this.selected.has(parseInt(c.getAttribute(this.dataAttr))));
        cards.forEach(card => {
            const id = parseInt(card.getAttribute(this.dataAttr));
            if (allSelected) {
                this.selected.delete(id);
                card.classList.remove('selected');
                const cb = card.querySelector('.capture-checkbox');
                if (cb) cb.checked = false;
            } else {
                this.selected.add(id);
                card.classList.add('selected');
                const cb = card.querySelector('.capture-checkbox');
                if (cb) cb.checked = true;
            }
        });
        this.updateControls();
    }

    updateControls() {
        const count = this.selected.size;
        const controls = document.getElementById(this.controlsId);

        // In compare mode, hide the regular selection toolbar
        if (this.name === 'videos' && compareMode.active) {
            controls.style.display = 'none';
            updateCompareModeText(count);
            return;
        }

        controls.style.display = count > 0 ? 'flex' : 'none';
        document.getElementById(this.countId).textContent = `${count} selected`;

        // Show compare button when 2-4 videos selected (outside compare mode)
        if (this.name === 'videos') {
            const compareBtn = document.getElementById('video-compare-selected-btn');
            if (compareBtn) compareBtn.style.display = (count >= 2 && count <= 4) ? '' : 'none';
        }

        const cards = document.querySelectorAll(`${this.cardSelector}[${this.dataAttr}]`);
        const allSelected = cards.length > 0 && [...cards].every(c => this.selected.has(parseInt(c.getAttribute(this.dataAttr))));
        document.getElementById(this.toggleBtnId).textContent = allSelected ? 'Clear Selection' : 'Select Visible';
    }

    clear() {
        this.selected.clear();
        document.querySelectorAll(`${this.cardSelector}.selected`).forEach(c => c.classList.remove('selected'));
        document.querySelectorAll(`${this.cardSelector} .capture-checkbox`).forEach(cb => cb.checked = false);
        this.updateControls();
    }

    deleteSelected() {
        const count = this.selected.size;
        if (count === 0) return;
        const plural = count > 1 ? 's' : '';
        confirmAction(
            `Are you sure you want to delete ${count} ${this.itemLabel}${plural}?`,
            async () => {
                try {
                    const result = await apiRequest(this.deleteEndpoint, {
                        method: 'POST',
                        body: { [this.deleteBodyKey]: [...this.selected] }
                    });
                    if (result.errors && result.errors.length > 0) {
                        showNotification(`Deleted ${result.deleted} of ${result.requested} ${this.itemLabel}s. Some errors occurred.`, 'warning');
                    } else {
                        showNotification(`Deleted ${result.deleted} ${this.itemLabel}${result.deleted > 1 ? 's' : ''}`);
                    }
                    this.selected.clear();
                    this.onReload();
                    this.updateControls();
                    refreshEventsSoon();
                } catch (error) {
                    showNotification(`Failed to delete ${this.itemLabel}s`, 'error');
                }
            }
        );
    }

    favoriteSelected(isFavorite) {
        const count = this.selected.size;
        if (count === 0) return;
        (async () => {
            try {
                await apiRequest(this.favoriteEndpoint, {
                    method: 'POST',
                    body: { ids: [...this.selected], is_favorite: isFavorite }
                });
                showNotification(`${count} ${this.itemLabel}${count > 1 ? 's' : ''} ${isFavorite ? 'favorited' : 'unfavorited'}`);
                this.onReload();
            } catch (error) {
                showNotification('Failed to update favorites', 'error');
            }
        })();
    }
}

// Prevent Enter key from submitting forms
function preventEnterSubmit(event) {
    if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') {
        event.preventDefault();
        return false;
    }
}

// Toggle time window fields visibility
let _timeWindowListenersAdded = false;
function toggleTimeWindow() {
    toggleFieldGroup('time_window_enabled', 'time-window-fields', {
        requiredIds: ['time_window_start', 'time_window_end'],
        onToggle: (enabled) => {
            if (enabled && !_timeWindowListenersAdded) {
                document.getElementById('time_window_start').addEventListener('change', updateDurationEstimate);
                document.getElementById('time_window_end').addEventListener('change', updateDurationEstimate);
                _timeWindowListenersAdded = true;
            }
            updateDurationEstimate();
        }
    });
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    setupNavigation();
    
    // Measure navbar height for detail view layout calculations
    const navbar = document.querySelector('.navbar');
    if (navbar) {
        const updateNavbarHeight = () => {
            document.documentElement.style.setProperty('--navbar-height', navbar.offsetHeight + 'px');
        };
        updateNavbarHeight();
        window.addEventListener('resize', updateNavbarHeight);
    }
    // Load tags globally (needed by tag pickers in modals)
    loadTagManager();
    
    // Start event log polling
    startEventPolling();
    
    // Route based on current URL path (with hash fallback for old bookmarks)
    handleRoute();
    
    // Setup refresh intervals
    refreshIntervals.push(setInterval(() => {
        if (currentView === 'jobs') loadJobs();
    }, 5000));
    
    // Setup event listeners for job creation form
    const startInput = document.getElementById('start_datetime');
    const intervalInput = document.getElementById('interval_seconds');
    
    if (startInput) {
        startInput.addEventListener('change', updateEndDateMin);
    }
    if (intervalInput) {
        intervalInput.addEventListener('change', updateEndDateMin);
    }
    
    // Setup range checkbox — duration estimate updates
    document.getElementById('use_range').addEventListener('change', (e) => {
        if (e.target.checked) {
            setTimeout(() => updateVideoDurationEstimate(), 100);
        } else {
            updateVideoDurationEstimate();
        }
    });
    
    // Setup framerate change listener for video modal
    const videoFramerateInput = document.getElementById('video_framerate');
    if (videoFramerateInput) {
        videoFramerateInput.addEventListener('change', () => {
            if (window.currentJobId) {
                updateVideoDurationEstimate();
            }
        });
        videoFramerateInput.addEventListener('input', debounce(() => {
            if (window.currentJobId) {
                updateVideoDurationEstimate();
            }
        }, 300));
    }
    
    // Setup duration estimate listeners for job creation form
    const jobCreationEstimateFields = ['start_datetime', 'end_datetime', 'interval_seconds', 'framerate', 
                                       'time_window_enabled', 'time_window_start', 'time_window_end'];
    jobCreationEstimateFields.forEach(fieldId => {
        const element = document.getElementById(fieldId);
        if (element) {
            element.addEventListener('change', updateDurationEstimate);
            element.addEventListener('input', updateDurationEstimate);
        }
    });
    
    // Trigger initial duration estimate
    updateDurationEstimate();
    
    // Mount create-job auto-build overlay widget
    initCreateJobOverlay();
    
    // Prevent Enter key from submitting forms
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('keydown', preventEnterSubmit);
    });
});

// Navigation & Routing
const _routeMap = {
    '/': 'jobs', '/jobs': 'jobs', '/timelapses': 'videos',
    '/captures': 'captures', '/storage': 'storage', '/settings': 'settings',
};
const _viewToPath = {
    'jobs': '/jobs', 'videos': '/timelapses', 'captures': '/captures',
    'storage': '/storage', 'settings': '/settings',
    'job-detail': '/jobs', 'video-detail': '/timelapses',
};

function parseRoute(pathname) {
    pathname = pathname || window.location.pathname;
    // Direct view match
    if (_routeMap[pathname]) return { view: _routeMap[pathname], id: null };
    // Detail routes: /jobs/:id, /timelapses/:id
    const jobMatch = pathname.match(/^\/jobs\/(\d+)$/);
    if (jobMatch) return { view: 'job-detail', id: parseInt(jobMatch[1]) };
    const videoMatch = pathname.match(/^\/timelapses\/(\d+)$/);
    if (videoMatch) return { view: 'video-detail', id: parseInt(videoMatch[1]) };
    // Unknown route falls back to jobs
    return { view: 'jobs', id: null };
}

function handleRoute(pushState = false) {
    const route = parseRoute();
    if (route.view === 'job-detail' && route.id) {
        switchView('job-detail', pushState);
        loadJobDetail(route.id);
    } else if (route.view === 'video-detail' && route.id) {
        switchView('video-detail', pushState);
        loadVideoDetail(route.id);
    } else {
        switchView(route.view, pushState);
    }
}

function navigateTo(path, replace = false) {
    if (replace) {
        history.replaceState({ path }, '', path);
    } else {
        history.pushState({ path }, '', path);
    }
    handleRoute(false);
}

function setupNavigation() {
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            navigateTo(e.currentTarget.getAttribute('href'));
        });
    });
    
    // Handle browser back/forward buttons
    window.addEventListener('popstate', (e) => {
        // If a modal is open, close the topmost one
        const activeModals = document.querySelectorAll('.modal.active');
        if (activeModals.length > 0) {
            _closingFromPopstate = true;
            _modalHistoryDepth = Math.max(0, _modalHistoryDepth - 1);
            const topModal = activeModals[activeModals.length - 1];
            if (topModal.id === 'comparison-modal') {
                closeComparison();
            } else if (topModal.id === 'confirm-modal') {
                topModal.classList.remove('active');
            } else {
                closeModal(topModal.id);
            }
            _closingFromPopstate = false;
            return;
        }
        
        handleRoute(false);
    });
}

function switchView(view, pushState = true) {
    // Clean up video detail polling when leaving that view
    if (view !== 'video-detail' && _videoDetailPollInterval) {
        clearInterval(_videoDetailPollInterval);
        _videoDetailPollInterval = null;
    }
    
    // Update navigation highlights (Jobs active for job-detail, Timelapses for video-detail)
    const navView = (view === 'job-detail') ? 'jobs' : (view === 'video-detail') ? 'videos' : view;
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.toggle('active', link.dataset.view === navView);
    });
    
    // Update content
    document.querySelectorAll('.view').forEach(v => {
        v.classList.toggle('active', v.id === `${view}-view`);
    });
    
    // Lock page scroll when a detail view is active (sidebar stays static, only config scrolls)
    const isDetail = (view === 'job-detail' || view === 'video-detail');
    document.body.classList.toggle('detail-view-active', isDetail);
    
    currentView = view;
    
    // Push to browser history if requested
    if (pushState && _viewToPath[view]) {
        history.pushState({ path: _viewToPath[view] }, '', _viewToPath[view]);
    }
    
    // Load data for list views
    if (view === 'jobs') loadJobs();
    if (view === 'videos') loadVideos();
    if (view === 'storage') loadStorage();
    if (view === 'settings') loadSettings();
    if (view === 'captures') loadCaptures();
}

// Size detail panels so sidebar + main fill viewport below the header.
// Called after loading a detail view and on window resize.
function sizeDetailPanels() {
    const sidebar = document.querySelector('.detail-sidebar');
    const main = document.querySelector('.detail-main');
    if (!sidebar || !main) return;
    
    // Measure where the panels start
    const top = sidebar.getBoundingClientRect().top;
    const available = window.innerHeight - top;
    
    sidebar.style.maxHeight = available + 'px';
    sidebar.style.overflowY = 'auto';
    main.style.maxHeight = available + 'px';
    main.style.overflowY = 'auto';
}

window.addEventListener('resize', () => {
    if (currentView === 'job-detail' || currentView === 'video-detail') {
        sizeDetailPanels();
    }
});

// Jobs
let allJobs = [];

async function loadJobs() {
    try {
        const jobs = await apiRequest('/jobs/');
        allJobs = jobs;
        // Initialize status filter (once)
        const statusWrap = document.getElementById('job-status-filter-wrap');
        if (statusWrap && !statusWrap._statusFilterSelected) {
            renderStatusFilter('job-status-filter-wrap', () => filterJobs());
        }
        // Initialize tag filter (once)
        const tagWrap = document.getElementById('job-tag-filter-wrap');
        if (tagWrap && !tagWrap._tagFilterSelected) {
            renderTagFilter('job-tag-filter-wrap', () => filterJobs());
        }
        filterJobs();
        updateJobWarningBadge(jobs);
    } catch (error) {
        console.error('Failed to load jobs:', error);
    }
}

function filterJobs() {
    const search = (document.getElementById('job-search').value || '').toLowerCase();
    const activeStatuses = getStatusFilterValues('job-status-filter-wrap');
    const sort = document.getElementById('job-sort').value;
    
    let filtered = allJobs;
    
    if (search) {
        filtered = filtered.filter(j =>
            j.name.toLowerCase().includes(search) ||
            j.url.toLowerCase().includes(search)
        );
    }
    
    if (activeStatuses.length > 0) {
        filtered = filtered.filter(j => activeStatuses.includes(j.status));
    }
    
    // Tag filter
    const selectedTagIds = getTagFilterIds('job-tag-filter-wrap');
    if (selectedTagIds.length > 0) {
        filtered = filtered.filter(j =>
            j.tags && j.tags.some(t => selectedTagIds.includes(t.id))
        );
    }
    
    // Sort
    filtered = [...filtered];
    switch (sort) {
        case 'created_asc': filtered.sort((a, b) => a.created_at.localeCompare(b.created_at)); break;
        case 'name_asc': filtered.sort((a, b) => a.name.localeCompare(b.name)); break;
        case 'name_desc': filtered.sort((a, b) => b.name.localeCompare(a.name)); break;
        case 'captures_desc': filtered.sort((a, b) => b.capture_count - a.capture_count); break;
        default: filtered.sort((a, b) => b.created_at.localeCompare(a.created_at)); break;
    }
    
    const hasFilters = search || activeStatuses.length > 0 || selectedTagIds.length > 0;
    document.getElementById('job-filter-reset').style.display = hasFilters ? '' : 'none';
    
    renderJobs(filtered);
}

function resetJobFilters() {
    document.getElementById('job-search').value = '';
    clearStatusFilter('job-status-filter-wrap');
    clearTagFilter('job-tag-filter-wrap');
    filterJobs();
}

function renderJobs(jobs) {
    const container = document.getElementById('jobs-list');
    const countEl = document.getElementById('job-count');
    if (countEl) countEl.textContent = `${allJobs.length} jobs`;
    
    if (jobs.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>No jobs yet</h3>
                <p>Create your first timelapse job to get started</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = jobs.map((job, idx) => {
        const thumbnailHtml = job.latest_capture 
            ? `<div class="job-thumbnail" style="background-image: url('${API_BASE}/captures/${job.latest_capture.id}/thumbnail'); background-size: cover; background-position: center; height: 120px; border-radius: var(--radius-md); margin-bottom: 1rem;"></div>`
            : `<div class="job-thumbnail" style="background: var(--border-color); height: 120px; border-radius: var(--radius-md); margin-bottom: 1rem; display: flex; align-items: center; justify-content: center; color: var(--text-secondary);">No captures yet</div>`;
        
        // Determine status display
        let statusLabel, statusClass;
        if (job.status === 'warning') {
            statusLabel = '⚠ Warning';
            statusClass = 'warning';
        } else if (job.status === 'sleeping') {
            statusLabel = 'Sleeping';
            statusClass = 'sleeping';
        } else if (job.status === 'disabled') {
            statusLabel = '⏸ Disabled';
            statusClass = 'disabled';
        } else {
            statusLabel = job.status.charAt(0).toUpperCase() + job.status.slice(1);
            statusClass = job.status;
        }
        
        // Build time window info
        let timeWindowInfo = '';
        if (job.time_window_enabled) {
            timeWindowInfo = `<div><strong>Time Window:</strong> ${job.time_window_start} - ${job.time_window_end}</div>`;
        }
        
        // Last capture info
        let lastCaptureInfo = '';
        if (job.latest_capture && job.latest_capture.captured_at) {
            lastCaptureInfo = `<div><strong>Last Capture:</strong> ${formatDateTime(job.latest_capture.captured_at)}</div>`;
        } else if (job.capture_count === 0) {
            lastCaptureInfo = `<div><strong>Last Capture:</strong> No captures yet</div>`;
        }
        
        // Next capture info
        let nextCaptureInfo = '';
        // Use next_scheduled_capture_at from scheduler (schedule-based) if available, fallback to next_capture_at
        const nextCapture = job.next_scheduled_capture_at || job.next_capture_at;
        if (nextCapture && job.status !== 'disabled' && job.status !== 'completed') {
            nextCaptureInfo = `<div><strong>Next Capture:</strong> ${formatDateTime(nextCapture)}</div>`;
        }
        
        let nextAutoBuildInfo = '';
        if (job.next_auto_build_at && job.auto_build_enabled) {
            nextAutoBuildInfo = `<div><strong>Next Auto-Build:</strong> ${formatDateTime(job.next_auto_build_at)}</div>`;
        }
        
        return `
        <div class="job-card" style="--i:${idx}" onclick="navigateTo('/jobs/${job.id}')">
            ${thumbnailHtml}
            <div class="job-card-header">
                <div class="job-card-title">${escapeHtml(job.name)}</div>
            </div>
            <div class="job-info">
                <div><strong>${job.stream_type === 'device' ? 'Device:' : 'Stream URL:'}</strong> <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; max-width: 250px; vertical-align: bottom;">${escapeHtml(job.stream_type === 'device' ? job.url : getStreamHost(job.url))}</span></div>
                <div><strong>Interval:</strong> ${job.interval_seconds}s</div>
                <div><strong>Capture:</strong> ${(job.capture_quality || 'maximum').charAt(0).toUpperCase() + (job.capture_quality || 'maximum').slice(1)}${job.capture_resolution && job.capture_resolution !== 'native' ? ` @ ${job.capture_resolution}` : ''}</div>
                ${timeWindowInfo}
                ${job.start_datetime ? `<div><strong>Start:</strong> ${formatDateTimeNoSeconds(job.start_datetime)}</div>` : ''}
                ${job.end_datetime ? `<div><strong>End:</strong> ${formatDateTimeNoSeconds(job.end_datetime)}</div>` : '<div><strong>Ongoing capture</strong></div>'}
                ${nextCaptureInfo}
                ${nextAutoBuildInfo}
                ${lastCaptureInfo}
                <div style="margin-top: 0.5rem;">
                    <a href="#" onclick="event.stopPropagation(); viewJobCaptures(${job.id}); return false;" 
                       style="color: var(--primary-color); text-decoration: underline; font-weight: 500;"
                       title="View captures">
                        ${job.capture_count} captures
                    </a> · 
                    <span class="stat-inline">${formatBytes(job.storage_size)}</span>
                </div>
            </div>
            <div class="job-card-badges">
                ${job.tags && job.tags.length ? `<div class="card-tags">${job.tags.map(t => tagChipHTML(t, true)).join('')}</div>` : ''}
                ${job.auto_build_enabled ? '<span class="auto-build-badge">Auto-Build</span>' : ''}
                <span class="job-status ${statusClass}">${statusLabel}</span>
            </div>
        </div>
    `;
    }).join('');
}

async function loadJobDetail(jobId) {
    try {
        const [job, capturesData] = await Promise.all([
            apiRequest(`/jobs/${jobId}`),
            apiRequest('/captures/', { query: { job_id: jobId, page: 1, page_size: 1, sort_order: 'desc' } })
        ]);
        
        const content = document.getElementById('job-detail-content');
        const title = document.getElementById('job-detail-title');
        
        // Update page title
        title.textContent = job.name;
        
        // End datetime will be set by initializeEditTimePickers if present
        
        // Determine status display
        let statusLabel, statusClass;
        if (job.status === 'warning') {
            statusLabel = 'Warning';
            statusClass = 'warning';
        } else if (job.status === 'sleeping') {
            statusLabel = 'Sleeping (Outside Time Window)';
            statusClass = 'sleeping';
        } else if (job.status === 'disabled') {
            statusLabel = 'Disabled';
            statusClass = 'disabled';
        } else {
            statusLabel = job.status.charAt(0).toUpperCase() + job.status.slice(1);
            statusClass = job.status;
        }
        
        // Time window info
        let timeWindowHtml = '';
        if (job.time_window_enabled) {
            timeWindowHtml = `
                <div class="info-box" style="margin: 1rem 0;">
                    <div class="info-box">
                        <div>
                            <strong>Time Window Enabled</strong>
                            <p class="mt-sm text-base">Captures only happen between <strong>${job.time_window_start}</strong> and <strong>${job.time_window_end}</strong> each day.</p>
                            ${job.time_window_start > job.time_window_end ? '<p class="mt-sm text-xs" style="opacity: 0.8;">⏰ This window spans midnight (e.g., captures from evening to early morning)</p>' : ''}
                        </div>
                    </div>
                </div>
            `;
        }
        
        // Last capture info
        let lastCaptureHtml = '';
        if (capturesData.captures && capturesData.captures.length > 0) {
            lastCaptureHtml = `<div><strong>Last Capture:</strong> ${formatDateTime(capturesData.captures[0].captured_at)}</div>`;
        } else {
            lastCaptureHtml = `<div><strong>Last Capture:</strong> No captures yet</div>`;
        }
        
        // Next capture info
        let nextCaptureHtml = '';
        // Use next_scheduled_capture_at from scheduler (schedule-based) if available, fallback to next_capture_at
        const nextCapture = job.next_scheduled_capture_at || job.next_capture_at;
        if (nextCapture && job.status !== 'disabled' && job.status !== 'completed') {
            nextCaptureHtml = `<div><strong>Next Capture:</strong> ${formatDateTime(nextCapture)}</div>`;
        }
        
        let nextAutoBuildHtml = '';
        if (job.next_auto_build_at && job.auto_build_enabled) {
            nextAutoBuildHtml = `<div><strong>Next Auto-Build:</strong> ${formatDateTime(job.next_auto_build_at)}</div>`;
        }
        
        content.innerHTML = `
                <div class="detail-layout">
                    <div class="detail-sidebar">
                        ${capturesData.captures && capturesData.captures.length > 0 ? `
                            <img src="${API_BASE}/captures/${capturesData.captures[0].id}/image" alt="Latest capture" onclick="openOverlayLightbox(this)" title="Click to enlarge">
                        ` : ''}
                        <div class="detail-meta">
                            <div><strong>Status:</strong> <span class="job-status ${statusClass}">${statusLabel}</span></div>
                            <div><strong>Start:</strong> ${formatDateTimeNoSeconds(job.start_datetime)}</div>
                            ${job.end_datetime ? `<div><strong>End:</strong> ${formatDateTimeNoSeconds(job.end_datetime)}</div>` : ''}
                            ${nextCaptureHtml}
                            ${nextAutoBuildHtml}
                            ${lastCaptureHtml}
                            <div><strong>Storage:</strong> ${formatBytes(job.storage_size)}</div>
                            <div><strong>Pattern:</strong> <span class="text-xs" style="color: var(--text-secondary);">${escapeHtml(job.naming_pattern || '{job_name}_{count}_{timestamp}')}</span></div>
                            <div><strong>Folder:</strong> <span class="text-xs" style="word-break: break-all;">${escapeHtml(job.capture_path)}</span></div>
                            <div class="detail-meta-actions">
                                <strong>Captures:</strong>
                                <a href="#" onclick="event.stopPropagation(); viewJobCaptures(${job.id}); return false;" 
                                   style="color: var(--primary-color); text-decoration: none;" title="View captures">
                                    ${job.capture_count}
                                </a>
                                <button class="btn-icon" onclick="event.stopPropagation(); manualCapture(${job.id}, '${escapeHtml(job.name)}')" title="Take Snapshot">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                                        <circle cx="12" cy="13" r="4"></circle>
                                    </svg>
                                </button>
                                <button class="btn-icon" onclick="event.stopPropagation(); openCompareModal(${job.id})" title="Compare Captures">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <rect x="2" y="2" width="20" height="20" rx="2"/>
                                        <path d="M12 2v20"/>
                                        <circle cx="7.5" cy="7.5" r="1.5"/>
                                        <path d="M6 18l3-4 2 2 4-5 3 4"/>
                                        <rect x="12" y="2" width="10" height="20" rx="2" fill="currentColor" opacity="0.15" stroke="none"/>
                                    </svg>
                                </button>
                                <button class="btn-icon" onclick="event.stopPropagation(); performMaintenanceScan(${job.id}, '${escapeHtml(job.name)}')" title="Sync">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="23 4 23 10 17 10"></polyline>
                                        <polyline points="1 20 1 14 7 14"></polyline>
                                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        <div class="detail-sidebar-actions">
                            <button id="save-job-btn" class="btn btn-primary" onclick="saveJobChanges(${job.id})" disabled style="width: 100%;">
                                Save Changes
                            </button>
                            <button class="btn btn-accent btn-sm" onclick="event.stopPropagation(); showProcessVideoModal(${job.id}, '${escapeHtml(job.name)}')" style="width: 100%;">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                                Build Timelapse
                            </button>
                            <div class="detail-sidebar-actions-row">
                                ${job.status !== 'completed' ? 
                                    `<button class="btn-icon" onclick="confirmCompleteJob(${job.id}, '${escapeHtml(job.name)}')" title="Complete Job">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                                    </button>` : ''
                                }
                                ${job.status === 'active' || job.status === 'sleeping' ? 
                                    `<button class="btn-icon" onclick="confirmDisableJob(${job.id}, '${escapeHtml(job.name)}')" title="Disable Job">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="10" y1="15" x2="10" y2="9"/><line x1="14" y1="15" x2="14" y2="9"/></svg>
                                    </button>` :
                                    job.status === 'disabled' ?
                                    `<button class="btn-icon" onclick="confirmEnableJob(${job.id}, '${escapeHtml(job.name)}')" title="Enable Job">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg>
                                    </button>` : ''
                                }
                                <button class="btn-icon" onclick="duplicateJob(${job.id})" title="Duplicate Job">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                                    </svg>
                                </button>
                                <button class="btn-icon" onclick="exportJob(${job.id}, '${escapeHtml(job.name)}')" title="Export Job">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                                    </svg>
                                </button>
                                <button class="btn-icon" onclick="deleteJob(${job.id}, '${escapeHtml(job.name)}')" title="Delete Job">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="3 6 5 6 21 6"></polyline>
                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                        <line x1="10" y1="11" x2="10" y2="17"></line>
                                        <line x1="14" y1="11" x2="14" y2="17"></line>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </div>

                    <div class="detail-main">
                        ${job.status === 'warning' && job.warning_message ? `
                        <div class="info-box" style="margin-bottom: 1rem; border-left-color: var(--warning-color);">
                            <div class="info-box">
                                <span style="font-size: 1.25rem;">⚠</span>
                                <div>
                                    <strong>Capture Warning</strong>
                                    <p class="mt-sm text-base">${escapeHtml(job.warning_message)}</p>
                                    <p class="mt-sm text-xs" style="opacity: 0.8;">Verify settings for the job. The job will continue attempting captures in case this is a temporary issue.</p>
                                </div>
                            </div>
                        </div>
                        ` : ''}
                        ${timeWindowHtml}
                        <!-- Source -->
                        <div class="form-section">
                            <div class="form-section-title">Source</div>
                    <div class="form-group" style="margin-bottom: 0.75rem;">
                        ${job.stream_type === 'device' ? `
                        <label>Camera Device</label>
                        <div class="source-row">
                            <select id="edit_device_path" class="form-control" style="flex: 1; min-width: 0;">
                                <option value="${escapeHtml(job.url)}" selected>${escapeHtml(job.url)}</option>
                            </select>
                            <input type="hidden" id="edit_url" value="${escapeHtml(job.url)}">
                            <button type="button" class="compare-btn" onclick="refreshDevices('edit_device_path')" title="Refresh devices" style="padding: 0.625rem 0.5rem;">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <polyline points="23 4 23 10 17 10"></polyline>
                                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                                </svg>
                            </button>
                            <button type="button" class="compare-btn" onclick="previewStream('edit_device_path', 'edit-preview-result', 'edit_capture_quality', 'edit_capture_resolution', 'edit-source-info', 'edit-source-dimensions')" style="white-space: nowrap; display: flex; align-items: center; gap: 0.35rem;">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                    <circle cx="12" cy="12" r="3"></circle>
                                </svg>
                                Preview
                            </button>
                            <div class="warn-after-group">
                                <label>Warn After</label>
                                <input type="number" id="edit_warning_threshold" class="form-control" value="${job.warning_threshold || 3}" min="1" max="50">
                            </div>
                        </div>
                        ` : `
                        <label>Stream URL *</label>
                        <div class="source-row">
                            <input type="text" id="edit_url" class="form-control" value="${escapeHtml(job.url)}" required style="flex: 1; min-width: 0;">
                            <button type="button" class="compare-btn" onclick="previewStream('edit_url', 'edit-preview-result', 'edit_capture_quality', 'edit_capture_resolution', 'edit-source-info', 'edit-source-dimensions')" style="white-space: nowrap; display: flex; align-items: center; gap: 0.35rem;">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                    <circle cx="12" cy="12" r="3"></circle>
                                </svg>
                                Preview
                            </button>
                            <div class="warn-after-group">
                                <label>Warn After</label>
                                <input type="number" id="edit_warning_threshold" class="form-control" value="${job.warning_threshold || 3}" min="1" max="50">
                            </div>
                        </div>
                        `}
                        <div id="edit-preview-result" class="test-result"></div>
                    </div>

                    <div class="form-row-wrap" style="gap: 1rem; margin-top: 0.75rem;">
                        <div class="form-group" style="flex: 1; min-width: 140px; margin-bottom: 0;">
                            <label>Capture Quality</label>
                            <select id="edit_capture_quality" class="form-control">
                                <option value="maximum" ${(!job.capture_quality || job.capture_quality === 'maximum') ? 'selected' : ''}>Maximum</option>
                                <option value="high" ${job.capture_quality === 'high' ? 'selected' : ''}>High</option>
                                <option value="medium" ${job.capture_quality === 'medium' ? 'selected' : ''}>Medium</option>
                                <option value="low" ${job.capture_quality === 'low' ? 'selected' : ''}>Low</option>
                            </select>
                        </div>
                        <div class="form-group" style="flex: 1; min-width: 140px; margin-bottom: 0;">
                            <label>Capture Resolution</label>
                            <select id="edit_capture_resolution" class="form-control">
                                <option value="native" selected>Native</option>
                                ${job.capture_resolution && job.capture_resolution !== 'native' ? `<option value="${escapeHtml(job.capture_resolution)}" selected>${escapeHtml(job.capture_resolution)}</option>` : ''}
                            </select>
                        </div>
                        <div class="form-group" id="edit-source-info" style="flex: 0 0 auto; display: none; align-self: flex-end; padding-bottom: 0.35rem; margin-bottom: 0;">
                            <small id="edit-source-dimensions" style="color: var(--text-secondary);"></small>
                        </div>
                    </div>
                </div>

                <div class="form-section">
                    <div class="form-section-title">Schedule</div>
                    <div class="form-group" style="margin-bottom: 1rem;">
                        <label>End Date & Time</label>
                        <input type="datetime-local" id="edit_end_datetime" class="form-control" style="max-width: 280px;">
                        <small style="color: var(--text-secondary);">Leave empty for ongoing capture</small>
                    </div>
                    
                    <div class="form-group mb-lg">
                        <div class="form-row-wrap gap-md">
                            <label class="form-row" style="cursor: pointer; margin: 0;">
                                <input type="checkbox" id="edit_time_window_enabled" ${job.time_window_enabled ? 'checked' : ''} style="cursor: pointer;" onchange="toggleEditTimeWindow()">
                                <span><strong>Daily Time Window</strong></span>
                            </label>
                            <div id="edit-time-window-fields" class="toggle-fields form-row ${job.time_window_enabled ? '' : 'disabled'}">
                                <label class="text-sm" style="margin: 0;">Start</label>
                                <div class="time-picker-container" style="margin: 0;">
                                    <input type="time" id="edit_time_window_start_time" class="form-control" style="padding: 0.3rem 0.5rem;">
                                </div>
                                <input type="hidden" id="edit_time_window_start">
                                <label class="text-sm" style="margin: 0;">End</label>
                                <div class="time-picker-container" style="margin: 0;">
                                    <input type="time" id="edit_time_window_end_time" class="form-control" style="padding: 0.3rem 0.5rem;">
                                </div>
                                <input type="hidden" id="edit_time_window_end">
                            </div>
                        </div>
                    </div>

                    <div class="form-row-wrap" style="gap: 1rem; align-items: flex-start;">
                        <div class="form-group" style="margin-bottom: 0;">
                            <label>Capture Interval (s) *</label>
                            <input type="number" id="edit_interval_seconds" class="form-control" value="${job.interval_seconds}" min="10" required style="width: 140px;">
                        </div>
                        <div class="form-group" style="margin-bottom: 0;">
                            <label>Timelapse FPS</label>
                            <input type="number" id="edit_framerate" class="form-control" value="30" min="1" max="120" required style="width: 140px;">
                        </div>
                        <div class="duration-estimate" id="edit-duration-estimate" style="flex: 1; min-width: 200px; margin: 0;"></div>
                    </div>
                </div>

                <div class="form-section">
                    <div class="form-section-title">Auto Build</div>
                    <div class="form-group" style="margin-bottom: 1rem;">
                        <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; margin-bottom: 0.5rem;">
                            <input type="checkbox" id="edit_auto_build_enabled" ${job.auto_build_enabled ? 'checked' : ''} style="cursor: pointer;" onchange="toggleEditAutoBuildFields()">
                            <span><strong>Enable Auto-Build</strong></span>
                        </label>
                        <small style="color: var(--text-secondary); display: block; margin-left: 1.5rem;">Automatically build timelapse videos on a recurring schedule</small>
                    </div>

                    <div id="edit-auto-build-fields" class="toggle-fields ${job.auto_build_enabled ? '' : 'disabled'}" style="margin-bottom: 1rem; margin-left: 1.5rem;">
                        <div class="form-group" style="margin-bottom: 0.75rem;">
                            <label>Build Interval</label>
                            <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                                <input type="number" id="edit_auto_build_interval_hours" class="form-control" min="1" max="8760" value="${job.auto_build_interval_hours || 168}" style="width: 100px;">
                                <small style="color: var(--text-secondary);">hours</small>
                                <div class="auto-build-presets" style="display: flex; gap: 0.25rem; flex-wrap: wrap; margin-left: 0.5rem;">
                                    <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 1)">Hourly</button>
                                    <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 24)">Daily</button>
                                    <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 168)">Weekly</button>
                                    <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 720)">Monthly</button>
                                </div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group flex-1">
                                <label>FPS</label>
                                <input type="number" id="edit_auto_build_fps" class="form-control" min="1" max="120" value="${job.auto_build_fps || 30}">
                            </div>
                            <div class="form-group flex-1">
                                <label>Quality</label>
                                <select id="edit_auto_build_quality" class="form-control">
                                    <option value="low" ${job.auto_build_quality === 'low' ? 'selected' : ''}>Low</option>
                                    <option value="medium" ${(!job.auto_build_quality || job.auto_build_quality === 'medium') ? 'selected' : ''}>Medium</option>
                                    <option value="high" ${job.auto_build_quality === 'high' ? 'selected' : ''}>High</option>
                                    <option value="maximum" ${job.auto_build_quality === 'maximum' ? 'selected' : ''}>Maximum</option>
                                </select>
                            </div>
                            <div class="form-group flex-1">
                                <label>Resolution</label>
                                <select id="edit_auto_build_resolution" class="form-control">
                                    <option value="3840x2160" ${job.auto_build_resolution === '3840x2160' ? 'selected' : ''}>4K (3840x2160)</option>
                                    <option value="1920x1080" ${(!job.auto_build_resolution || job.auto_build_resolution === '1920x1080') ? 'selected' : ''}>Full HD (1920x1080)</option>
                                    <option value="1280x720" ${job.auto_build_resolution === '1280x720' ? 'selected' : ''}>HD (1280x720)</option>
                                    <option value="640x480" ${job.auto_build_resolution === '640x480' ? 'selected' : ''}>SD (640x480)</option>
                                </select>
                            </div>
                        </div>
                        <div id="edit-ab-overlay-container"></div>
                        ${job.last_auto_build_at ? `<small style="color: var(--text-secondary);">Last auto-build: ${formatDateTime(job.last_auto_build_at)}</small>` : ''}
                    </div>
                </div>

                <div class="form-section">
                    <div class="form-section-title">Tags</div>
                    <div class="form-group" style="margin-bottom: 1rem;">
                        <div class="tag-picker" id="edit-job-tags"></div>
                    </div>
                </div>

                    </div><!-- /detail-main -->
                </div><!-- /detail-layout -->

                <input type="hidden" id="edit_start_datetime" value="${job.start_datetime}">
        `;
        
        // Scroll to top of detail page
        content.scrollTop = 0;
        
        // Size the detail panels to fill viewport (sidebar static, main scrolls)
        sizeDetailPanels();
        
        // Initialize custom time pickers for edit modal
        initializeEditTimePickers(job);
        
        // Initialize edit overlay section
        initEditJobOverlay(job);
        
        // Populate capture resolution dropdown from persisted source dimensions
        if (job.source_width && job.source_height) {
            const options = _generateResolutionOptions(job.source_width, job.source_height, job.capture_resolution || 'native');
            _populateResolutionDropdown('edit_capture_resolution', options, job.capture_resolution || 'native');
            _nativeDimensions['edit_capture_resolution'] = { w: job.source_width, h: job.source_height };
            document.getElementById('edit-source-info').style.display = 'block';
            document.getElementById('edit-source-dimensions').textContent = `Source: ${job.source_width}x${job.source_height}`;
        }
        
        // Load device list for device-type jobs
        if (job.stream_type === 'device') {
            refreshDevices('edit_device_path').then(() => {
                const sel = document.getElementById('edit_device_path');
                if (sel) sel.value = job.url;
            });
        }
        
        // Add native resolution option to auto-build dropdown
        if (job.capture_count > 0) {
            fetch(`${API_BASE}/captures/job/${job.id}/time-range`).then(r => r.json()).then(tr => {
                if (tr.native_resolution) {
                    const sel = document.getElementById('edit_auto_build_resolution');
                    if (sel && !sel.querySelector(`option[value="${tr.native_resolution}"]`)) {
                        const opt = document.createElement('option');
                        opt.value = tr.native_resolution;
                        opt.textContent = `Native (${tr.native_resolution})`;
                        sel.insertBefore(opt, sel.firstChild);
                        if (job.auto_build_resolution === tr.native_resolution) sel.value = tr.native_resolution;
                    }
                }
            }).catch(() => {});
        }
        
        // Render tag picker with auto-save on toggle
        renderTagPicker('edit-job-tags', (job.tags || []).map(t => t.id), (tagIds) => {
            apiRequest(`/jobs/${job.id}`, { method: 'PATCH', body: { tag_ids: tagIds } })
                .then(() => loadJobs())
                .catch(err => showNotification(err.message || 'Failed to update tags', 'error'));
        });
        
        // Track changes to enable/disable save button
        setupJobEditChangeTracking(job);
    } catch (error) {
        console.error('Failed to load job details:', error);
        showNotification('Failed to load job details', 'error');
        navigateTo('/jobs');
    }
}

// Compatibility wrapper for internal calls that need to navigate to job detail
function showJobDetails(jobId) {
    navigateTo(`/jobs/${jobId}`);
}

async function createJob(event) {
    event.preventDefault();
    
    // Get all form values using universal utility
    const values = getValues({
        job_name: {},
        job_url: {},
        start_datetime: {},
        end_datetime: {},
        interval_seconds: { parse: 'int' },
        framerate: { parse: 'int' },
        warning_threshold: { parse: 'int' },
        naming_pattern: {},
        time_window_enabled: { parse: 'bool' },
        time_window_start: {},
        time_window_end: {},
        capture_quality: {},
        capture_resolution: {},
        auto_build_enabled: { parse: 'bool' },
        auto_build_interval_hours: { parse: 'int' },
        auto_build_fps: { parse: 'int' },
        auto_build_quality: {},
        auto_build_resolution: {}
    });
    
    // Validate framerate
    if (!values.framerate || values.framerate < 1 || values.framerate > 120) {
        showNotification('Framerate must be between 1 and 120 FPS', 'error');
        return;
    }
    
    // Determine source URL and stream type based on source type toggle
    let jobUrl, stream_type;
    const warningThresholdVal = values.warning_threshold || 3;
    if (_createSourceType === 'device') {
        jobUrl = document.getElementById('device_path').value;
        if (!jobUrl) {
            showNotification('Please select a camera device', 'error');
            return;
        }
        stream_type = 'device';
    } else {
        jobUrl = values.job_url;
        if (!jobUrl) {
            showNotification('Please enter a stream URL', 'error');
            return;
        }
        stream_type = jobUrl.toLowerCase().startsWith('rtsp://') || jobUrl.toLowerCase().startsWith('rtsps://') ? 'rtsp' : 'http';
    }
    
    // Validate dates
    const startDate = new Date(values.start_datetime);
    
    if (values.end_datetime) {
        const endDate = new Date(values.end_datetime);
        const now = new Date();
        
        if (endDate <= startDate) {
            showNotification('End date must be after start date', 'error');
            return;
        }
        
        if (endDate < now) {
            showNotification('End date cannot be in the past', 'error');
            return;
        }
        
        const minEnd = new Date(startDate.getTime() + values.interval_seconds * 1000);
        if (endDate < minEnd) {
            showNotification(`End date must be at least ${values.interval_seconds} seconds after start date`, 'error');
            return;
        }
    }
    
    const formData = {
        name: values.job_name,
        url: jobUrl,
        stream_type: stream_type,
        start_datetime: datetimeLocalToISO(values.start_datetime),
        end_datetime: values.end_datetime ? datetimeLocalToISO(values.end_datetime) : null,
        interval_seconds: values.interval_seconds,
        framerate: values.framerate,
        naming_pattern: values.naming_pattern,
        warning_threshold: warningThresholdVal,
        time_window_enabled: values.time_window_enabled,
        time_window_start: values.time_window_enabled ? values.time_window_start : null,
        time_window_end: values.time_window_enabled ? values.time_window_end : null,
        auto_build_enabled: values.auto_build_enabled,
        auto_build_interval_hours: values.auto_build_interval_hours || 168,
        auto_build_fps: values.auto_build_fps || 30,
        auto_build_quality: values.auto_build_quality || 'medium',
        auto_build_resolution: values.auto_build_resolution || '1920x1080',
        auto_build_text_overlay: values.auto_build_enabled ? JSON.stringify(readOverlayConfig('create-ab')) : null,
        capture_quality: values.capture_quality || 'maximum',
        capture_resolution: values.capture_resolution || 'native',
        source_width: _nativeDimensions['capture_resolution']?.w || null,
        source_height: _nativeDimensions['capture_resolution']?.h || null,
        tag_ids: getSelectedTagIds('create-job-tags')
    };
    
    try {
        await apiRequest('/jobs/', { method: 'POST', body: formData });
        
        closeModal('create-job-modal');
        document.getElementById('create-job-form').reset();
        loadJobs();
        showNotification(`Job "${formData.name}" created successfully!`);
        refreshEventsSoon();
    } catch (error) {
        console.error('Failed to create job:', error);
        showNotification(`Failed to create job: ${error.message}`, 'error');
    }
}

function setupJobEditChangeTracking(originalJob) {
    const saveBtn = document.getElementById('save-job-btn');
    if (!saveBtn) return;

    // Initially disabled
    setButtonState(saveBtn, true);

    // Snapshot initial form values to compare against
    const getFormSnapshot = () => {
        const vals = {};
        trackedFields.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            vals[id] = el.type === 'checkbox' ? el.checked : el.value;
        });
        // Include overlay position
        const grid = document.getElementById('edit-ab-overlay-grid');
        const activeBtn = grid?.querySelector('.pos-btn.active');
        vals['_overlay_pos'] = activeBtn?.dataset.pos || '';
        return vals;
    };

    const trackedFields = [
        'edit_interval_seconds',
        'edit_end_datetime',
        'edit_time_window_enabled',
        'edit_time_window_start_time',
        'edit_time_window_end_time',
        'edit_url',
        'edit_device_path',
        'edit_stream_type',
        'edit_warning_threshold',
        'edit_capture_quality',
        'edit_capture_resolution',
        'edit_auto_build_enabled',
        'edit_auto_build_interval_hours',
        'edit_auto_build_fps',
        'edit_auto_build_quality',
        'edit_auto_build_resolution',
        'edit-ab-overlay-enabled',
        'edit-ab-overlay-text',
        'edit-ab-overlay-font',
        'edit-ab-overlay-size',
        'edit-ab-overlay-bold',
        'edit-ab-overlay-color',
        'edit-ab-overlay-bg',
        'edit-ab-overlay-bg-color',
        'edit-ab-overlay-bg-opacity'
    ];

    // Take snapshot after a tick so DOM values are fully populated
    let initialSnapshot = null;
    setTimeout(() => { initialSnapshot = getFormSnapshot(); }, 50);

    const checkForChanges = () => {
        if (!initialSnapshot) { setButtonState(saveBtn, true); return; }
        const current = getFormSnapshot();
        const hasChanges = Object.keys(initialSnapshot).some(k =>
            String(initialSnapshot[k]) !== String(current[k])
        );
        setButtonState(saveBtn, !hasChanges);
    };

    trackedFields.forEach(fieldId => {
        const field = document.getElementById(fieldId);
        if (field) {
            field.addEventListener('change', checkForChanges);
            field.addEventListener('input', checkForChanges);
        }
    });

    // Duration estimate watchers
    const estimateFields = ['edit_interval_seconds', 'edit_framerate', 'edit_end_datetime', 
                            'edit_time_window_enabled', 'edit_time_window_start_time', 'edit_time_window_end_time'];
    estimateFields.forEach(fieldId => {
        const field = document.getElementById(fieldId);
        if (field) {
            field.addEventListener('change', updateEditDurationEstimate);
            field.addEventListener('input', updateEditDurationEstimate);
        }
    });

    // Trigger initial estimate
    setTimeout(updateEditDurationEstimate, 100);

    // Track overlay position grid clicks
    const overlayGrid = document.getElementById('edit-ab-overlay-grid');
    if (overlayGrid) {
        overlayGrid.querySelectorAll('.pos-btn').forEach(btn => {
            btn.addEventListener('click', () => setTimeout(checkForChanges, 10));
        });
    }
}

function confirmDisableJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to disable the job "${jobName}"? The job will stop capturing images until re-enabled.`,
        () => updateJobStatus(jobId, 'disabled', jobName)
    );
}

function confirmEnableJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to enable the job "${jobName}"? The job will start capturing images according to its schedule.`,
        () => updateJobStatus(jobId, 'active', jobName)
    );
}

function confirmCompleteJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to complete the job "${jobName}"? This will set the job's end time to now and mark it as completed.`,
        () => completeJob(jobId, jobName)
    );
}

async function completeJob(jobId, jobName) {
    try {
        await apiRequest(`/jobs/${jobId}`, {
            method: 'PATCH',
            body: { status: 'completed', end_datetime: new Date().toISOString() }
        });
        loadJobs();
        loadJobDetail(jobId);
        showNotification(`Job "${jobName}" completed successfully`);
        refreshEventsSoon();
    } catch (error) {
        console.error('Failed to complete job:', error);
        showNotification('Failed to complete job', 'error');
    }
}

async function updateJobStatus(jobId, status, jobName) {
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { status } });
        loadJobs();
        loadJobDetail(jobId);
        const action = status === 'active' ? 'enabled' : 'disabled';
        showNotification(`Job "${jobName}" ${action} successfully`);
    } catch (error) {
        console.error('Failed to update job:', error);
        showNotification('Failed to update job', 'error');
    }
}

async function updateJobEndTime(jobId) {
    const endDatetimeInput = document.getElementById('edit_end_datetime');
    const rawValue = endDatetimeInput.value || null;
    const endDatetime = rawValue ? datetimeLocalToISO(rawValue) : null;
    
    if (endDatetime) {
        const endTime = new Date(endDatetime);
        if (endTime <= new Date()) {
            showNotification('End time must be in the future', 'error');
            return;
        }
    }
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { end_datetime: endDatetime } });
        loadJobDetail(jobId);
        loadJobs();
        showNotification('End time updated successfully');
    } catch (error) {
        console.error('Failed to update end time:', error);
        showNotification(error.message || 'Failed to update end time', 'error');
    }
}

async function updateJobUrl(jobId) {
    const editDeviceEl = document.getElementById('edit_device_path');
    const url = editDeviceEl ? editDeviceEl.value : document.getElementById('edit_url').value.trim();
    
    if (!url) {
        showNotification('URL cannot be empty', 'error');
        return;
    }
    
    let stream_type;
    if (url.startsWith('/dev/video')) {
        stream_type = 'device';
    } else if (url.toLowerCase().startsWith('rtsp://') || url.toLowerCase().startsWith('rtsps://')) {
        stream_type = 'rtsp';
    } else {
        stream_type = 'http';
    }
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { url, stream_type } });
        showNotification('Stream URL updated successfully');
        loadJobDetail(jobId);
    } catch (error) {
        console.error('Failed to update URL:', error);
        showNotification(error.message || 'Failed to update URL', 'error');
    }
}

async function updateJobInterval(jobId) {
    const interval = parseInt(document.getElementById('edit_interval_seconds').value);
    
    if (!interval || interval < 10) {
        showNotification('Interval must be at least 10 seconds', 'error');
        return;
    }
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { interval_seconds: interval } });
        showNotification('Capture interval updated successfully');
        loadJobDetail(jobId);
    } catch (error) {
        console.error('Failed to update interval:', error);
        showNotification(error.message || 'Failed to update interval', 'error');
    }
}

function toggleEditTimeWindow() {
    toggleFieldGroup('edit_time_window_enabled', 'edit-time-window-fields', {
        requiredIds: ['edit_time_window_start', 'edit_time_window_end']
    });
}

function toggleAutoBuildFields() {
    toggleFieldGroup('auto_build_enabled', 'auto-build-fields');
}

function toggleEditAutoBuildFields() {
    toggleFieldGroup('edit_auto_build_enabled', 'edit-auto-build-fields');
}

function setAutoBuildInterval(inputId, hours) {
    const input = document.getElementById(inputId);
    input.value = hours;
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

async function saveJobChanges(jobId) {
    // Collect all form values
    const interval = parseInt(document.getElementById('edit_interval_seconds').value);
    // URL may come from device picker or text input
    const editDeviceEl = document.getElementById('edit_device_path');
    const url = editDeviceEl ? editDeviceEl.value : document.getElementById('edit_url').value.trim();
    const endDatetimeRaw = document.getElementById('edit_end_datetime').value || null;
    const endDatetime = endDatetimeRaw ? datetimeLocalToISO(endDatetimeRaw) : null;
    const timeWindowEnabled = document.getElementById('edit_time_window_enabled').checked;
    const timeWindowStart = document.getElementById('edit_time_window_start').value;
    const timeWindowEnd = document.getElementById('edit_time_window_end').value;
    const warningThreshold = parseInt(document.getElementById('edit_warning_threshold').value) || 3;
    const autoBuildEnabled = document.getElementById('edit_auto_build_enabled').checked;
    const autoBuildIntervalHours = parseInt(document.getElementById('edit_auto_build_interval_hours').value) || 168;
    const autoBuildFps = parseInt(document.getElementById('edit_auto_build_fps').value) || 30;
    const autoBuildQuality = document.getElementById('edit_auto_build_quality').value;
    const autoBuildResolution = document.getElementById('edit_auto_build_resolution').value;
    const captureQuality = document.getElementById('edit_capture_quality').value;
    const captureResolution = document.getElementById('edit_capture_resolution').value;
    
    // Validate required fields
    if (!url) {
        showNotification('URL cannot be empty', 'error');
        return;
    }
    
    if (!interval || interval < 10) {
        showNotification('Interval must be at least 10 seconds', 'error');
        return;
    }
    
    // Validate time window if enabled
    if (timeWindowEnabled && (!timeWindowStart || !timeWindowEnd)) {
        showNotification('Both start and end times are required when time window is enabled', 'error');
        return;
    }
    
    // Validate end time if provided
    if (endDatetime) {
        const endTime = new Date(endDatetime);
        const now = new Date();
        
        if (endTime <= now) {
            showNotification('End time must be in the future', 'error');
            return;
        }
        
        // Check if end time is at least one interval in the future
        const minEndTime = new Date(now.getTime() + interval * 1000);
        if (endTime < minEndTime) {
            showNotification(`End time must be at least ${interval} seconds in the future`, 'error');
            return;
        }
    }
    
    // Detect stream type from URL
    let stream_type;
    if (url.startsWith('/dev/video')) {
        stream_type = 'device';
    } else if (url.toLowerCase().startsWith('rtsp://') || url.toLowerCase().startsWith('rtsps://')) {
        stream_type = 'rtsp';
    } else {
        stream_type = 'http';
    }
    
    // Build update payload
    const updateData = {
        interval_seconds: interval,
        url: url,
        stream_type: stream_type,
        end_datetime: endDatetime,
        time_window_enabled: timeWindowEnabled,
        time_window_start: timeWindowEnabled ? timeWindowStart : null,
        time_window_end: timeWindowEnabled ? timeWindowEnd : null,
        warning_threshold: warningThreshold,
        auto_build_enabled: autoBuildEnabled,
        auto_build_interval_hours: autoBuildIntervalHours,
        auto_build_fps: autoBuildFps,
        auto_build_quality: autoBuildQuality,
        auto_build_resolution: autoBuildResolution,
        auto_build_text_overlay: JSON.stringify(readOverlayConfig('edit-ab')),
        capture_quality: captureQuality,
        capture_resolution: captureResolution
    };
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: updateData });
        await loadJobs();
        loadJobDetail(jobId);
        showNotification('Job settings updated successfully');
    } catch (error) {
        console.error('Failed to update job:', error);
        showNotification(error.message || 'Failed to update job', 'error');
    }
}

async function deleteJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to delete the job "${jobName}"? This will permanently remove the job and all its captures. Timelapse videos will be preserved.`,
        async () => {
            try {
                await apiRequest(`/jobs/${jobId}`, { method: 'DELETE' });
                navigateTo('/jobs');
                loadJobs();
                showNotification(`Job "${jobName}" and all captures deleted successfully`);
                refreshEventsSoon();
            } catch (error) {
                console.error('Failed to delete job:', error);
                showNotification(`Failed to delete job "${jobName}"`, 'error');
            }
        }
    );
}

async function testUrl() {
    previewStream('job_url', 'test-result', 'capture_quality', 'capture_resolution', 'source-info', 'source-dimensions');
}

async function testDevice() {
    const devicePath = document.getElementById('device_path').value;
    if (!devicePath) {
        showNotification('Please select a camera device first', 'warning');
        return;
    }
    previewStream('device_path', 'device-test-result', 'capture_quality', 'capture_resolution', 'source-info', 'source-dimensions');
}

// Current source type state for create modal
let _createSourceType = 'network';

function setSourceType(type) {
    _createSourceType = type;
    const networkBtn = document.getElementById('source-type-network');
    const deviceBtn = document.getElementById('source-type-device');
    const urlInput = document.getElementById('job_url');
    const deviceSelect = document.getElementById('device_path');
    const refreshBtn = document.getElementById('device-refresh-btn');
    
    if (type === 'device') {
        networkBtn.classList.remove('active');
        deviceBtn.classList.add('active');
        urlInput.style.display = 'none';
        urlInput.removeAttribute('required');
        deviceSelect.style.display = '';
        refreshBtn.style.display = '';
        refreshDevices('device_path');
    } else {
        deviceBtn.classList.remove('active');
        networkBtn.classList.add('active');
        deviceSelect.style.display = 'none';
        refreshBtn.style.display = 'none';
        urlInput.style.display = '';
        urlInput.setAttribute('required', '');
    }
    // Clear preview results
    document.getElementById('test-result').innerHTML = '';
    document.getElementById('device-test-result').innerHTML = '';
}

async function refreshDevices(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    select.innerHTML = '<option value="">Scanning...</option>';
    try {
        const devices = await apiRequest('/devices/');
        select.innerHTML = '';
        if (devices.length === 0) {
            select.innerHTML = '<option value="">No cameras detected</option>';
            return;
        }
        for (const d of devices) {
            const opt = document.createElement('option');
            opt.value = d.path;
            opt.textContent = `${d.name} (${d.path})`;
            select.appendChild(opt);
        }
    } catch (err) {
        select.innerHTML = '<option value="">Error loading devices</option>';
    }
}

function _generateResolutionOptions(width, height, currentValue) {
    const options = [{ value: 'native', label: `Native (${width}x${height})` }];
    const aspect = width / height;
    // Common downscale widths
    const widths = [3840, 2560, 1920, 1280, 960, 640];
    for (const w of widths) {
        if (w >= width) continue;
        const h = Math.round(w / aspect);
        // Ensure even dimensions for ffmpeg
        const hEven = h % 2 === 0 ? h : h + 1;
        options.push({ value: `${w}x${hEven}`, label: `${w}x${hEven}` });
    }
    return options;
}

function _populateResolutionDropdown(selectId, options, currentValue) {
    const select = document.getElementById(selectId);
    if (!select) return;
    select.innerHTML = '';
    for (const opt of options) {
        const el = document.createElement('option');
        el.value = opt.value;
        el.textContent = opt.label;
        if (opt.value === currentValue) el.selected = true;
        select.appendChild(el);
    }
}

// Track native source dimensions per resolution dropdown
const _nativeDimensions = {};

async function previewStream(urlInputId, resultDivId, qualityId, resolutionId, infoId, dimsId) {
    const url = document.getElementById(urlInputId).value;
    const resultDiv = document.getElementById(resultDivId);
    
    if (!url) {
        showNotification('Please enter a URL or select a device first', 'warning');
        return;
    }
    
    // Determine stream type for the query
    const streamType = url.startsWith('/dev/video') ? 'device' : null;
    
    resultDiv.innerHTML = '<div style="display:flex;justify-content:center;padding:2rem 0;"><div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:8px;padding:0.75rem 1.5rem;display:flex;align-items:center;gap:0.5rem;"><span class="spinner" style="width:14px;height:14px;border-width:2px;"></span><span style="color:var(--text-secondary);font-size:0.85rem;">Loading preview</span></div></div>';
    resultDiv.className = 'test-result';
    
    try {
        const query = { url };
        if (streamType) query.stream_type = streamType;
        if (qualityId) {
            const qEl = document.getElementById(qualityId);
            if (qEl) query.quality = qEl.value;
        }
        if (resolutionId) {
            const rEl = document.getElementById(resolutionId);
            if (rEl) query.resolution = rEl.value;
        }
        
        const result = await apiRequest('/jobs/test-url', { method: 'POST', query });
        
        if (result.success) {
            const sizeStr = result.image_size ? ` (${formatBytes(result.image_size)})` : '';
            resultDiv.className = 'test-result';
            resultDiv.innerHTML = `
                <img src="${result.image_data}" alt="Preview capture" style="max-width: 100%; margin-top: 10px; border: 1px solid var(--border-color); border-radius: 4px;">
                <small style="color: var(--text-secondary); display: block; margin-top: 4px;">Capture size: ${sizeStr}</small>
            `;
            
            // Use native dimensions from backend, or fall back to previously cached
            if (result.source_width && result.source_height && resolutionId) {
                _nativeDimensions[resolutionId] = { w: result.source_width, h: result.source_height };
            }
            
            const cached = _nativeDimensions[resolutionId];
            if (cached && resolutionId) {
                const currentRes = document.getElementById(resolutionId)?.value || 'native';
                const options = _generateResolutionOptions(cached.w, cached.h, currentRes);
                _populateResolutionDropdown(resolutionId, options, currentRes);
                
                if (infoId && dimsId) {
                    document.getElementById(infoId).style.display = 'block';
                    document.getElementById(dimsId).textContent = `Source: ${cached.w}x${cached.h}`;
                }
            }
        } else {
            resultDiv.className = 'test-result error';
            resultDiv.innerHTML = `<p style="color: var(--danger-color); margin-top: 10px;">${result.message}</p>`;
        }
    } catch (error) {
        resultDiv.className = 'test-result error';
        resultDiv.innerHTML = `<p style="color: var(--danger-color); margin-top: 10px;">Error: Please check the URL.</p>`;
    }
}

// Videos
// Cached video list for client-side filtering
let allVideos = [];
let currentFilteredVideos = [];
const VIDEO_PAGE_SIZE = 24;
let videosDisplayed = 0;
const videoSelection = new SelectionManager({
    name: 'videos',
    cardSelector: '.video-gallery-card',
    dataAttr: 'data-video-id',
    controlsId: 'video-selection-controls',
    countId: 'video-selected-count',
    toggleBtnId: 'video-toggle-selection-btn',
    deleteEndpoint: '/videos/delete-multiple',
    deleteBodyKey: 'video_ids',
    favoriteEndpoint: '/videos/favorite',
    itemLabel: 'timelapse',
    onReload: () => loadVideos()
});

async function loadVideos() {
    try {
        const videos = await apiRequest('/videos/');
        allVideos = videos;
        
        // Check if any videos are processing
        const hasProcessing = videos.some(v => v.status === 'processing');
        
        // Start refresh interval if there are processing videos
        if (hasProcessing && !videoRefreshInterval) {
            videoRefreshInterval = setInterval(loadVideos, 5000);
        }
        // Stop refresh interval if no processing videos
        else if (!hasProcessing && videoRefreshInterval) {
            clearInterval(videoRefreshInterval);
            videoRefreshInterval = null;
        }
        
        populateVideoFilters(videos);
        filterVideos({ preserveSelection: true });
    } catch (error) {
        console.error('Failed to load videos:', error);
    }
}

function populateVideoFilters(videos) {
    const yearSelect = document.getElementById('video-year-filter');
    const currentYear = yearSelect.value;
    const years = [...new Set(videos.map(v => new Date(v.created_at).getFullYear()))].sort((a, b) => b - a);
    yearSelect.innerHTML = '<option value="">All Years</option>' +
        years.map(y => `<option value="${y}">${y}</option>`).join('');
    yearSelect.value = currentYear;
    
    // Initialize tag filter (once)
    const tagWrap = document.getElementById('video-tag-filter-wrap');
    if (tagWrap && !tagWrap._tagFilterSelected) {
        renderTagFilter('video-tag-filter-wrap', () => filterVideos());
    }
}

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function onYearFilterChange() {
    const yearVal = document.getElementById('video-year-filter').value;
    const monthSelect = document.getElementById('video-month-filter');
    
    if (!yearVal) {
        monthSelect.innerHTML = '<option value="">All Months</option>';
        monthSelect.value = '';
        monthSelect.disabled = true;
    } else {
        // Populate months that have videos in the selected year
        const year = parseInt(yearVal);
        const months = [...new Set(
            allVideos
                .filter(v => new Date(v.created_at).getFullYear() === year)
                .map(v => new Date(v.created_at).getMonth())
        )].sort((a, b) => a - b);
        
        monthSelect.innerHTML = '<option value="">All Months</option>' +
            months.map(m => `<option value="${m}">${MONTH_NAMES[m]}</option>`).join('');
        monthSelect.value = '';
        monthSelect.disabled = false;
    }
    
    filterVideos();
}

function resetVideoFilters() {
    document.getElementById('video-search').value = '';
    document.getElementById('video-year-filter').value = '';
    document.getElementById('video-month-filter').value = '';
    document.getElementById('video-month-filter').disabled = true;
    document.getElementById('video-source-filter').value = '';
    clearTagFilter('video-tag-filter-wrap');
    videoFavoritesOnly = false;
    const favBtn = document.getElementById('video-fav-filter');
    if (favBtn) favBtn.classList.remove('active');
    videoSharedOnly = false;
    const shareBtn = document.getElementById('video-share-filter');
    if (shareBtn) shareBtn.classList.remove('active');
    filterVideos();
}

function filterVideos(opts = {}) {
    const search = (document.getElementById('video-search').value || '').toLowerCase();
    const yearFilter = document.getElementById('video-year-filter').value;
    const monthFilter = document.getElementById('video-month-filter').value;
    const sourceFilter = document.getElementById('video-source-filter').value;
    const selectedTags = getTagFilterIds('video-tag-filter-wrap');
    
    let filtered = allVideos;
    
    if (search) {
        filtered = filtered.filter(v =>
            v.name.toLowerCase().includes(search) ||
            (v.job_name && v.job_name.toLowerCase().includes(search))
        );
    }
    
    if (yearFilter) {
        const year = parseInt(yearFilter);
        filtered = filtered.filter(v => new Date(v.created_at).getFullYear() === year);
        
        if (monthFilter !== '') {
            const month = parseInt(monthFilter);
            filtered = filtered.filter(v => new Date(v.created_at).getMonth() === month);
        }
    }
    
    if (sourceFilter === 'imported') {
        filtered = filtered.filter(v => v.build_source === 'imported');
    } else if (sourceFilter === 'built') {
        filtered = filtered.filter(v => v.build_source !== 'imported');
    }
    
    if (videoFavoritesOnly) {
        filtered = filtered.filter(v => v.is_favorite);
    }
    
    if (videoSharedOnly) {
        filtered = filtered.filter(v => !!v.share_token);
    }
    
    if (selectedTags.length > 0) {
        filtered = filtered.filter(v => v.tags && selectedTags.every(tid => v.tags.some(t => t.id === tid)));
    }
    
    // Sort
    const sort = document.getElementById('video-sort')?.value || 'created_desc';
    filtered = [...filtered];
    switch (sort) {
        case 'created_asc': filtered.sort((a, b) => a.created_at.localeCompare(b.created_at)); break;
        case 'name_asc': filtered.sort((a, b) => a.name.localeCompare(b.name)); break;
        case 'name_desc': filtered.sort((a, b) => b.name.localeCompare(a.name)); break;
        case 'duration_desc': filtered.sort((a, b) => (b.duration_seconds || 0) - (a.duration_seconds || 0)); break;
        case 'duration_asc': filtered.sort((a, b) => (a.duration_seconds || 0) - (b.duration_seconds || 0)); break;
        default: filtered.sort((a, b) => b.created_at.localeCompare(a.created_at)); break;
    }
    
    // Show/hide reset button
    const hasFilters = search || yearFilter || monthFilter !== '' || sourceFilter || videoFavoritesOnly || videoSharedOnly || selectedTags.length > 0;
    document.getElementById('video-filter-reset').style.display = hasFilters ? '' : 'none';
    
    const countEl = document.getElementById('video-count');
    countEl.textContent = `${filtered.length} videos`;
    
    currentFilteredVideos = filtered;
    videosDisplayed = 0;
    if (!opts.preserveSelection) videoSelection.clear();
    document.getElementById('videos-list').innerHTML = '';
    showMoreVideos();
}

function showMoreVideos() {
    const batch = currentFilteredVideos.slice(videosDisplayed, videosDisplayed + VIDEO_PAGE_SIZE);
    if (batch.length === 0 && videosDisplayed === 0) {
        renderVideos([], true);
        return;
    }
    renderVideos(batch, false);
    videosDisplayed += batch.length;
    updateLoadMoreButton();
}

function updateLoadMoreButton() {
    let btn = document.getElementById('video-load-more');
    const remaining = currentFilteredVideos.length - videosDisplayed;
    if (remaining > 0) {
        if (!btn) {
            btn = document.createElement('button');
            btn.id = 'video-load-more';
            btn.className = 'btn btn-secondary';
            btn.onclick = showMoreVideos;
            document.getElementById('videos-list').insertAdjacentElement('afterend', btn);
        }
        const next = Math.min(remaining, VIDEO_PAGE_SIZE);
        btn.textContent = `Load More (${remaining} remaining)`;
        btn.style.display = '';
    } else if (btn) {
        btn.style.display = 'none';
    }
}

function renderVideos(videos, isEmpty) {
    const container = document.getElementById('videos-list');
    
    if (isEmpty) {
        const hasFilter = document.getElementById('video-search').value ||
            document.getElementById('video-year-filter').value;
        container.innerHTML = `
            <div class="empty-state">
                <h3>${hasFilter ? 'No matching videos' : 'No processed videos'}</h3>
                <p>${hasFilter ? 'Try adjusting your search or filters' : 'Click <strong>+ Build Timelapse</strong> above to create your first timelapse video'}</p>
            </div>
        `;
        return;
    }
    
    const playIcon = `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
    const filmIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/><line x1="17" y1="17" x2="22" y2="17"/></svg>`;
    
    const html = videos.map((video, idx) => {
        const globalIdx = videosDisplayed + idx;
        const isCompleted = video.status === 'completed';
        const isProcessing = video.status === 'processing';
        const thumbSrc = video.thumbnail_path ? `${API_BASE}/videos/${video.id}/thumbnail` : '';
        const isSelected = videoSelection.has(video.id);
        
        return `
        <div class="video-gallery-card ${isSelected ? 'selected' : ''}" style="--i:${globalIdx}" 
             data-video-id="${video.id}"
             onclick="videoSelection.handleCardClick(${video.id}, event, openVideoDetail)" title="${escapeHtml(video.name)}">
            <input type="checkbox" class="capture-checkbox"
                   ${isSelected ? 'checked' : ''}
                   onclick="event.stopPropagation(); videoSelection.toggle(${video.id}, event)">
            <button class="card-fav-btn ${video.is_favorite ? 'favorited' : ''}" 
                    onclick="event.stopPropagation(); toggleFavorite('videos', ${video.id}, ${video.is_favorite ? 'true' : 'false'})"
                    title="${video.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
                <svg class="fav-heart-icon" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
            </button>
            <div class="video-gallery-thumb">
                ${thumbSrc ? 
                    `<img src="${thumbSrc}" alt="" loading="lazy">` : 
                    `<div class="thumb-placeholder">${filmIcon}</div>`
                }
                ${isCompleted ? `<div class="video-gallery-duration">${formatDuration(video.duration_seconds)}</div>` : ''}
                ${video.share_token ? '<div class="video-gallery-shared" title="Shared"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg></div>' : ''}
                ${isProcessing ? `<div class="video-gallery-status"><span class="job-status processing">Processing</span></div>` : ''}
                ${video.status === 'failed' ? `<div class="video-gallery-status"><span class="job-status completed" style="background:var(--danger-color)">Failed</span></div>` : ''}
                ${isCompleted ? `<div class="video-gallery-play">${playIcon}</div>` : ''}
            </div>
            ${isProcessing ? `
                <div class="video-gallery-progress">
                    <div class="progress-bar"><div class="progress-fill" style="width: ${video.progress}%"></div></div>
                    <div class="progress-text">${Math.round(video.progress)}%</div>
                </div>
            ` : ''}
            <div class="video-gallery-info">
                <div class="video-gallery-name">${escapeHtml(video.name)}</div>
                <div class="video-gallery-job">${video.build_source === 'imported' && !video.job_name ? '<span class="auto-build-badge imported">Imported</span>' : (video.job_name ? escapeHtml(video.job_name) : 'No job')}${video.build_source === 'imported' && video.job_name ? ' <span class="auto-build-badge imported">Imported</span>' : ''}${video.build_source === 'auto' ? ' <span class="auto-build-badge">Auto</span>' : ''}</div>
                ${video.tags && video.tags.length ? `<div class="card-tags">${video.tags.map(t => tagChipHTML(t, true)).join('')}</div>` : ''}
            </div>
        </div>`;
    }).join('');
    
    container.insertAdjacentHTML('beforeend', html);
}

let _currentVideoDetailId = null;
let _videoDetailPollInterval = null;

async function loadVideoDetail(videoId) {
    try {
        const video = await apiRequest(`/videos/${videoId}`);
        _currentVideoDetailId = video.id;
        
        const title = document.getElementById('video-detail-title');
        const meta = document.getElementById('video-detail-meta');
        const actions = document.getElementById('video-detail-actions');
        const player = document.getElementById('video-detail-player');
        const source = document.getElementById('video-detail-source');
        
        title.textContent = video.name;
        cancelVideoRename();
        
        // Set up video player
        if (video.status === 'completed') {
            source.src = `${API_BASE}/videos/${video.id}/download`;
            player.load();
            player.style.display = 'block';
        } else {
            player.style.display = 'none';
        }
        
        // Show progress bar for processing videos
        let progressContainer = document.getElementById('video-detail-progress');
        if (!progressContainer) {
            progressContainer = document.createElement('div');
            progressContainer.id = 'video-detail-progress';
            meta.parentNode.insertBefore(progressContainer, meta);
        }
        if (video.status === 'processing') {
            progressContainer.innerHTML = `
                <div style="margin-bottom: 1rem;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem;">
                        <span style="font-size:0.85rem;color:var(--text-secondary);">Building timelapse...</span>
                        <span style="font-size:0.85rem;font-weight:600;color:var(--primary-color);">${Math.round(video.progress)}%</span>
                    </div>
                    <div class="progress-bar" style="height:6px;">
                        <div class="progress-fill" style="width:${video.progress}%;"></div>
                    </div>
                </div>`;
            // Auto-refresh while processing
            if (!_videoDetailPollInterval) {
                _videoDetailPollInterval = setInterval(() => {
                    const route = parseRoute();
                    if (route.view === 'video-detail' && route.id) {
                        loadVideoDetail(route.id);
                    } else {
                        clearInterval(_videoDetailPollInterval);
                        _videoDetailPollInterval = null;
                    }
                }, 3000);
            }
        } else {
            progressContainer.innerHTML = '';
            if (_videoDetailPollInterval) {
                clearInterval(_videoDetailPollInterval);
                _videoDetailPollInterval = null;
            }
        }
        
        // Build metadata in 3 dense rows of 4 columns each
        let metaHtml = '';
        
        // Row 1: Job, Duration, Size, Status
        const jobDisplay = video.job_name
            ? (video.job_id
                ? `<a href="/jobs/${video.job_id}" class="job-link" onclick="event.preventDefault(); navigateToJob(${video.job_id})">${escapeHtml(video.job_name)}</a>`
                : escapeHtml(video.job_name))
            : '<span class="auto-build-badge imported">Imported</span>';
        const jobEditBtn = `<button class="btn-icon" onclick="showVideoJobPicker(${video.id}, ${video.job_id || 'null'})" title="Change job" style="padding:0 0.25rem;margin-left:0.25rem;vertical-align:middle;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>`;
        const jobVal = `${jobDisplay}${jobEditBtn}`;
        const sizeVal = (video.status === 'completed' && video.file_size) ? formatBytes(video.file_size) : 'N/A';
        metaHtml += `<dt>Job</dt><dd>${jobVal}</dd><dt>Duration</dt><dd>${formatDuration(video.duration_seconds)}</dd>`;
        metaHtml += `<dt>Size</dt><dd>${sizeVal}</dd><dt>Status</dt><dd><span class="job-status ${video.status}">${video.status}</span></dd>`;
        
        // Row 2: Start, End, Created, Completed
        metaHtml += `<dt>Start</dt><dd>${video.start_time ? formatDateTimeNoSeconds(video.start_time) : 'N/A'}</dd>`;
        metaHtml += `<dt>End</dt><dd>${video.end_time ? formatDateTimeNoSeconds(video.end_time) : 'N/A'}</dd>`;
        metaHtml += `<dt>Created</dt><dd>${formatDateTimeNoSeconds(video.created_at)}</dd>`;
        metaHtml += `<dt>Completed</dt><dd>${video.completed_at ? formatDateTimeNoSeconds(video.completed_at) : 'N/A'}</dd>`;
        
        // Row 3: Resolution, Framerate, Quality, Frames
        metaHtml += `<dt>Resolution</dt><dd>${video.resolution}</dd><dt>Framerate</dt><dd>${video.framerate} fps</dd>`;
        metaHtml += `<dt>Quality</dt><dd>${video.quality}</dd><dt>Frames</dt><dd>${video.total_frames.toLocaleString()}</dd>`;
        
        meta.innerHTML = metaHtml;
        
        // Build editable tags section for video
        const tagsContainer = document.getElementById('video-detail-tags');
        if (tagsContainer) {
            const currentTagIds = (video.tags || []).map(t => t.id);
            tagsContainer.innerHTML = `
                <div style="margin: 0.5rem 0;">
                    <label style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:0.25rem;display:block;">Tags</label>
                    <div class="tag-picker" id="video-detail-tag-picker"></div>
                </div>
            `;
            tagsContainer.style.display = 'block';
            renderTagPicker('video-detail-tag-picker', currentTagIds, (tagIds) => {
                apiRequest(`/videos/${video.id}/tags`, { method: 'PUT', body: { tag_ids: tagIds } })
                    .then(() => loadVideos())
                    .catch(err => showNotification(err.message || 'Failed to update tags', 'error'));
            });
        }
        
        // Build actions
        let actionsHtml = '';
        if (video.status === 'processing') {
            actionsHtml += `<button class="btn btn-danger btn-sm" onclick="cancelVideoBuild(${video.id}, '${escapeHtml(video.name)}')">Cancel</button>`;
        } else {
            if (video.status === 'completed') {
                actionsHtml += `<a href="${API_BASE}/videos/${video.id}/download" class="btn btn-primary btn-sm">Download</a>`;
            }
            actionsHtml += `<button class="btn btn-danger btn-sm" onclick="deleteVideoFromDetail(${video.id}, '${escapeHtml(video.name)}')">Delete</button>`;
            if (video.status === 'completed') {
                actionsHtml += shareToggleHTML(video.id, video.share_token || null);
            }
        }
        actions.innerHTML = actionsHtml;
    } catch (error) {
        console.error('Failed to load video details:', error);
        showNotification('Failed to load video details', 'error');
        navigateTo('/timelapses');
    }
}

// Compatibility wrapper
function openVideoDetail(videoId) {
    navigateTo(`/timelapses/${videoId}`);
}

function closeVideoDetail() {
    if (_videoDetailPollInterval) {
        clearInterval(_videoDetailPollInterval);
        _videoDetailPollInterval = null;
    }
    const player = document.getElementById('video-detail-player');
    if (player) {
        player.pause();
        player.currentTime = 0;
    }
    navigateTo('/timelapses');
}

async function deleteVideoFromDetail(videoId, videoName) {
    confirmAction(
        `Are you sure you want to delete "${videoName}"?`,
        async () => {
            try {
                await apiRequest(`/videos/${videoId}`, { method: 'DELETE' });
                closeVideoDetail();
                loadVideos();
                showNotification(`Video "${videoName}" deleted successfully`);
                refreshEventsSoon();
            } catch (error) {
                showNotification(`Failed to delete video "${videoName}"`, 'error');
            }
        }
    );
}

function startVideoRename() {
    const title = document.getElementById('video-detail-title');
    const input = document.getElementById('video-rename-input');
    const btn = document.getElementById('video-rename-btn');
    const saveBtn = document.getElementById('video-rename-save');
    const cancelBtn = document.getElementById('video-rename-cancel');
    
    input.value = title.textContent;
    title.style.display = 'none';
    btn.style.display = 'none';
    input.style.display = '';
    saveBtn.style.display = '';
    cancelBtn.style.display = '';
    input.focus();
    input.select();
    input.onkeydown = (e) => {
        if (e.key === 'Enter') saveVideoRename();
        if (e.key === 'Escape') cancelVideoRename();
    };
}

function cancelVideoRename() {
    const title = document.getElementById('video-detail-title');
    const input = document.getElementById('video-rename-input');
    const btn = document.getElementById('video-rename-btn');
    const saveBtn = document.getElementById('video-rename-save');
    const cancelBtn = document.getElementById('video-rename-cancel');
    
    title.style.display = '';
    btn.style.display = '';
    input.style.display = 'none';
    saveBtn.style.display = 'none';
    cancelBtn.style.display = 'none';
}

async function saveVideoRename() {
    const input = document.getElementById('video-rename-input');
    const newName = input.value.trim();
    if (!newName) {
        showNotification('Name cannot be empty', 'error');
        return;
    }
    try {
        const updated = await apiRequest(`/videos/${_currentVideoDetailId}`, {
            method: 'PATCH', body: { name: newName }
        });
        document.getElementById('video-detail-title').textContent = updated.name;
        cancelVideoRename();
        loadVideos();
        showNotification(`Video renamed to "${updated.name}"`);
    } catch (error) {
        showNotification(error.message || 'Failed to rename video', 'error');
    }
}

async function showVideoJobPicker(videoId, currentJobId) {
    // Fetch jobs list
    let jobs;
    try {
        const resp = await apiRequest('/jobs/');
        jobs = resp.jobs || resp;
    } catch (e) {
        showNotification('Failed to load jobs', 'error');
        return;
    }

    // Find the dd element for Job in the detail meta grid
    const metaDl = document.querySelector('#video-detail-meta');
    const dtElements = metaDl ? metaDl.querySelectorAll('dt') : [];
    let jobDd = null;
    for (const dt of dtElements) {
        if (dt.textContent.trim() === 'Job') {
            jobDd = dt.nextElementSibling;
            break;
        }
    }
    if (!jobDd) return;

    // Build inline dropdown
    const options = [`<option value="">None (Imported)</option>`]
        .concat(jobs.map(j => `<option value="${j.id}" ${j.id === currentJobId ? 'selected' : ''}>${escapeHtml(j.name)}</option>`));

    jobDd.innerHTML = `
        <select id="video-job-select" class="form-control" style="font-size:0.8rem;padding:0.15rem 0.3rem;display:inline-block;width:auto;max-width:160px;">
            ${options.join('')}
        </select>
        <button class="btn-icon" onclick="saveVideoJob(${videoId})" title="Save" style="padding:0 0.25rem;color:var(--success);vertical-align:middle;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        </button>
        <button class="btn-icon" onclick="loadVideoDetail(${videoId})" title="Cancel" style="padding:0 0.25rem;color:var(--text-secondary);vertical-align:middle;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    `;
}

async function saveVideoJob(videoId) {
    const select = document.getElementById('video-job-select');
    if (!select) return;
    const jobId = select.value ? parseInt(select.value) : null;
    try {
        await apiRequest(`/videos/${videoId}/job`, {
            method: 'PUT', body: { job_id: jobId }
        });
        loadVideoDetail(videoId);
        loadVideos();
        showNotification('Job updated');
    } catch (error) {
        showNotification(error.message || 'Failed to update job', 'error');
    }
}

async function cancelVideoBuild(videoId, videoName) {
    confirmAction(
        `Cancel the build for "${videoName}"? The partial file will be deleted.`,
        async () => {
            try {
                await apiRequest(`/videos/${videoId}/cancel`, { method: 'POST' });
                closeVideoDetail();
                loadVideos();
                refreshEventsSoon();
                showNotification(`Build cancelled for "${videoName}"`);
            } catch (error) {
                showNotification(`Failed to cancel: ${error.message}`, 'error');
            }
        }
    );
}

// ── Shared Links ──────────────────────────────────────────────────────────

function shareToggleHTML(videoId, shareToken) {
    const isShared = !!shareToken;
    const url = shareToken ? `${window.location.origin}/shared/${shareToken}` : '';
    return `
        <div class="share-toggle-inline" id="share-toggle-wrap">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
            <span style="font-size:0.8rem;">Share</span>
            <label class="toggle-switch">
                <input type="checkbox" ${isShared ? 'checked' : ''} onchange="toggleShare(${videoId}, this.checked)">
                <span class="toggle-slider"></span>
            </label>
            ${isShared ? `
            <div class="share-link-url">
                <input type="text" value="${url}" readonly onclick="this.select()">
                <button class="btn-icon" onclick="copyShareLink(this, '${url}')" title="Copy">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
            </div>` : ''}
        </div>
    `;
}

async function toggleShare(videoId, enabled) {
    try {
        const result = await apiRequest('/shared/toggle', {
            method: 'POST',
            body: { video_id: videoId, enabled }
        });
        const wrap = document.getElementById('share-toggle-wrap');
        if (wrap) {
            wrap.outerHTML = shareToggleHTML(videoId, result.token).trim();
        }
        loadVideos();
        showNotification(enabled ? 'Sharing enabled' : 'Sharing disabled');
    } catch (error) {
        showNotification(error.message || 'Failed to toggle sharing', 'error');
    }
}

function copyShareLink(btn, url) {
    navigator.clipboard.writeText(url).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '✓';
        setTimeout(() => { btn.innerHTML = orig; }, 1500);
    });
}

async function loadSharedVideosList() {
    const container = document.getElementById('shared-videos-list');
    if (!container) return;
    try {
        const links = await apiRequest('/shared/');
        if (links.length === 0) {
            container.innerHTML = '<span style="color:var(--text-secondary);font-size:0.85rem;">No shared videos</span>';
            return;
        }
        container.innerHTML = links.map(link => {
            const url = `${window.location.origin}/shared/${link.token}`;
            return `
                <div class="shared-video-item">
                    <span class="shared-video-name">${escapeHtml(link.video_name || 'Unknown')}</span>
                    <div class="share-link-url">
                        <input type="text" value="${url}" readonly onclick="this.select()">
                        <button class="btn-icon" onclick="copyShareLink(this, '${url}')" title="Copy">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        </button>
                    </div>
                    <button class="tag-action-btn tag-action-delete" onclick="disableShareFromSettings(${link.video_id})" title="Disable sharing">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                    </button>
                </div>
            `;
        }).join('');
    } catch (error) {
        container.innerHTML = '<span class="text-danger text-sm">Failed to load shared videos</span>';
    }
}

async function disableShareFromSettings(videoId) {
    try {
        await apiRequest('/shared/toggle', {
            method: 'POST',
            body: { video_id: videoId, enabled: false }
        });
        await loadSharedVideosList();
        showNotification('Sharing disabled');
    } catch (error) {
        showNotification('Failed to disable sharing', 'error');
    }
}

// ── Video Comparison ──────────────────────────────────────────────────────

let comparisonState = { playing: false, players: [], animFrame: null };

function toggleCompareMode() {
    compareMode.active = !compareMode.active;
    const btn = document.getElementById('compare-mode-btn');
    const banner = document.getElementById('compare-mode-banner');
    const gallery = document.getElementById('videos-view');

    if (compareMode.active) {
        videoSelection.clear();
        btn.classList.add('active');
        banner.style.display = 'flex';
        gallery.classList.add('compare-mode');
        updateCompareModeText(0);
    } else {
        videoSelection.clear();
        btn.classList.remove('active');
        banner.style.display = 'none';
        gallery.classList.remove('compare-mode');
    }
}

function updateCompareModeText(count) {
    const text = document.getElementById('compare-mode-text');
    const launchBtn = document.getElementById('compare-launch-btn');
    if (!text) return;
    if (count < 2) {
        const remaining = 2 - count;
        text.textContent = remaining === 2 ? 'Select 2-4 timelapses to compare' : 'Select at least 1 more';
        if (launchBtn) launchBtn.style.display = 'none';
    } else {
        text.textContent = `${count} selected`;
        if (launchBtn) launchBtn.style.display = '';
    }
}

async function openComparison() {
    const ids = [...videoSelection.selected];
    if (ids.length < 2 || ids.length > 4) return;

    try {
        const videos = await Promise.all(ids.map(id => apiRequest(`/videos/${id}`)));

        const container = document.getElementById('comparison-players');
        container.innerHTML = '';
        container.className = `comparison-players compare-grid-${videos.length}`;

        const players = [];
        videos.forEach(video => {
            const side = document.createElement('div');
            side.className = 'comparison-side';
            const player = document.createElement('video');
            player.preload = 'metadata';
            const source = document.createElement('source');
            source.src = `${API_BASE}/videos/${video.id}/download`;
            source.type = 'video/mp4';
            player.appendChild(source);
            const label = document.createElement('div');
            label.className = 'comparison-label';
            const meta = `${video.resolution} · ${video.framerate}fps · ${formatDuration(video.duration_seconds)}`;
            label.innerHTML = `<strong>${escapeHtml(video.name)}</strong><span>${meta}</span>`;
            side.appendChild(player);
            side.appendChild(label);
            container.appendChild(side);
            players.push(player);
        });

        comparisonState.players = players;
        comparisonState.playing = false;

        document.getElementById('compare-play-icon').style.display = '';
        document.getElementById('compare-pause-icon').style.display = 'none';
        document.getElementById('compare-scrubber').value = 0;
        document.getElementById('compare-time-current').textContent = '0:00';
        document.getElementById('compare-time-duration').textContent = '0:00';

        let loaded = 0;
        const onMeta = () => {
            loaded++;
            if (loaded >= players.length) {
                const maxDur = Math.max(...players.map(p => p.duration || 0));
                document.getElementById('compare-time-duration').textContent = formatDuration(maxDur);
            }
        };
        players.forEach(p => p.addEventListener('loadedmetadata', onMeta, { once: true }));

        showModal('comparison-modal');
    } catch (error) {
        showNotification('Failed to load videos for comparison', 'error');
    }
}

function closeComparison() {
    const modal = document.getElementById('comparison-modal');
    const wasActive = modal.classList.contains('active');
    const { players, animFrame } = comparisonState;
    players.forEach(p => { p.pause(); p.currentTime = 0; });
    if (animFrame) cancelAnimationFrame(animFrame);
    comparisonState.playing = false;
    comparisonState.animFrame = null;
    comparisonState.players = [];
    modal.classList.remove('active');
    if (compareMode.active) toggleCompareMode();
    
    if (wasActive && _modalHistoryDepth > 0 && !_closingFromPopstate) {
        _modalHistoryDepth--;
        history.back();
    }
}

function toggleComparisonPlay() {
    const { players } = comparisonState;
    if (players.length === 0) return;

    if (comparisonState.playing) {
        players.forEach(p => p.pause());
        comparisonState.playing = false;
        if (comparisonState.animFrame) cancelAnimationFrame(comparisonState.animFrame);
        document.getElementById('compare-play-icon').style.display = '';
        document.getElementById('compare-pause-icon').style.display = 'none';
    } else {
        if (players.every(p => p.ended)) {
            players.forEach(p => { p.currentTime = 0; });
        }
        // Adjust playback rates so all finish at the same time
        const maxDur = Math.max(...players.map(p => p.duration || 1));
        players.forEach(p => { p.playbackRate = (p.duration || 1) / maxDur; });

        players.forEach(p => p.play());
        comparisonState.playing = true;
        document.getElementById('compare-play-icon').style.display = 'none';
        document.getElementById('compare-pause-icon').style.display = '';
        syncComparisonLoop();
    }
}

function syncComparisonLoop() {
    if (!comparisonState.playing) return;
    const { players } = comparisonState;
    const maxDur = Math.max(...players.map(p => p.duration || 1));

    const avgProgress = players.reduce((sum, p) => sum + (p.currentTime / (p.duration || 1)), 0) / players.length;

    const scrubber = document.getElementById('compare-scrubber');
    scrubber.value = Math.round(avgProgress * 1000);
    document.getElementById('compare-time-current').textContent = formatDuration(avgProgress * maxDur);

    if (players.every(p => p.ended)) {
        comparisonState.playing = false;
        if (comparisonState.animFrame) cancelAnimationFrame(comparisonState.animFrame);
        document.getElementById('compare-play-icon').style.display = '';
        document.getElementById('compare-pause-icon').style.display = 'none';
        return;
    }

    comparisonState.animFrame = requestAnimationFrame(syncComparisonLoop);
}

function scrubComparison(value) {
    const { players } = comparisonState;
    if (players.length === 0) return;
    const position = value / 1000;
    players.forEach(p => { p.currentTime = position * (p.duration || 0); });

    const maxDur = Math.max(...players.map(p => p.duration || 0));
    document.getElementById('compare-time-current').textContent = formatDuration(position * maxDur);
}

async function showProcessVideoModal(jobId, jobName) {
    try {
        // Reset form
        document.getElementById('process-video-form').reset();
        document.getElementById('use_range').checked = false;
        document.getElementById('capture-range-fields').classList.add('disabled');
        document.getElementById('video-duration-estimate').innerHTML = '<span style="color: var(--text-secondary); font-size: 0.85rem;">No job selected</span>';
        document.getElementById('available-range-info').style.display = 'none';
        // Reset text overlay
        const buildOverlayContainer = document.getElementById('build-overlay-container');
        if (buildOverlayContainer) buildOverlayContainer.innerHTML = '';
        window._overlayPreviewCaptureId = null;
        window._overlayJobName = null;
        const previewImage = document.getElementById('builder-preview-image');
        const previewPlaceholder = document.getElementById('builder-preview-placeholder');
        if (previewImage) previewImage.style.display = 'none';
        if (previewPlaceholder) previewPlaceholder.style.display = 'flex';
        
        const jobSelector = document.getElementById('job-selector-group');
        const jobSelect = document.getElementById('process_job_select');
        
        if (jobId && jobName) {
            // Opened from job details — hide selector, pre-select job
            jobSelector.style.display = 'none';
            jobSelect.removeAttribute('required');
            document.getElementById('process_job_id').value = jobId;
            document.querySelector('#process-video-modal .modal-header h3').textContent = `Build Timelapse - ${jobName}`;
            await populateVideoFormFromJob(jobId, jobName);
        } else {
            // Opened from Timelapses page — show job selector
            jobSelector.style.display = '';
            jobSelect.setAttribute('required', 'required');
            document.getElementById('process_job_id').value = '';
            document.querySelector('#process-video-modal .modal-header h3').textContent = 'Build Timelapse';
            
            // Populate job dropdown
            await populateJobSelector();
            
            // Disable create button until a job is selected
            const createBtn = document.getElementById('create-video-btn');
            if (createBtn) {
                setButtonState(createBtn, true);
            }
        }
        
        showModal('process-video-modal');
        
        // Render tag picker — pre-select job's tags when opened from job details
        let preselectedTagIds = [];
        if (jobId) {
            const job = allJobs.find(j => j.id === jobId);
            if (job?.tags) preselectedTagIds = job.tags.map(t => t.id);
        }
        renderTagPicker('build-video-tags', preselectedTagIds);
    } catch (error) {
        console.error('Failed to load modal data:', error);
        showNotification('Failed to load data for timelapse creation', 'error');
    }
}

async function populateJobSelector() {
    const jobSelect = document.getElementById('process_job_select');
    jobSelect.innerHTML = '<option value="">Select a job...</option>';
    
    try {
        const jobs = await apiRequest('/jobs/');
        
        // Only show jobs that have captures
        const jobsWithCaptures = jobs.filter(j => j.capture_count > 0);
        
        jobsWithCaptures.forEach(job => {
            const option = document.createElement('option');
            option.value = job.id;
            option.textContent = `${job.name} (${job.capture_count} captures)`;
            option.dataset.jobName = job.name;
            option.dataset.framerate = job.framerate;
            jobSelect.appendChild(option);
        });
        
        if (jobsWithCaptures.length === 0) {
            jobSelect.innerHTML = '<option value="">No jobs with captures available</option>';
        }
    } catch (error) {
        console.error('Failed to load jobs:', error);
        jobSelect.innerHTML = '<option value="">Failed to load jobs</option>';
    }
}

async function onJobSelectChange() {
    const jobSelect = document.getElementById('process_job_select');
    const selectedOption = jobSelect.options[jobSelect.selectedIndex];
    const jobId = jobSelect.value;
    
    // Hide preview when no job selected
    const previewImage = document.getElementById('builder-preview-image');
    const previewPlaceholder = document.getElementById('builder-preview-placeholder');
    if (previewImage) previewImage.style.display = 'none';
    if (previewPlaceholder) previewPlaceholder.style.display = 'flex';
    
    if (!jobId) {
        document.getElementById('process_job_id').value = '';
        document.getElementById('video-duration-estimate').innerHTML = '<span style="color: var(--text-secondary); font-size: 0.85rem;">No job selected</span>';
        document.getElementById('available-range-info').style.display = 'none';
        const createBtn = document.getElementById('create-video-btn');
        if (createBtn) {
            setButtonState(createBtn, true);
        }
        return;
    }
    
    const jobName = selectedOption.dataset.jobName;
    document.getElementById('process_job_id').value = jobId;
    
    try {
        await populateVideoFormFromJob(parseInt(jobId), jobName);
    } catch (error) {
        console.error('Failed to load job data:', error);
        showNotification('Failed to load job data', 'error');
    }
}

async function populateVideoFormFromJob(jobId, jobName) {
    const [job, timeRange, latestCaptures] = await Promise.all([
        fetch(`${API_BASE}/jobs/${jobId}`).then(r => r.json()),
        fetch(`${API_BASE}/captures/job/${jobId}/time-range`).then(r => r.json()),
        apiRequest('/captures/', { query: { job_id: jobId, page_size: 1, sort_order: 'desc' } })
    ]);
    
    // Show latest capture preview
    const previewImage = document.getElementById('builder-preview-image');
    const previewPlaceholder = document.getElementById('builder-preview-placeholder');
    if (previewImage && latestCaptures.captures && latestCaptures.captures.length > 0) {
        const cap = latestCaptures.captures[0];
        const img = document.getElementById('job-preview-img');
        const imgUrl = `${API_BASE}/captures/${cap.id}/image`;
        img.src = imgUrl;
        img._originalSrc = imgUrl;
        document.getElementById('job-preview-label').textContent = `Latest capture: ${formatDateTime(cap.captured_at)}`;
        previewImage.style.display = 'flex';
        if (previewPlaceholder) previewPlaceholder.style.display = 'none';
        // Store capture_id for text overlay preview
        window._overlayPreviewCaptureId = cap.id;
        window._overlayJobName = jobName;
        // Mount overlay widget now that we have a preview image
        initBuildOverlay();
    }
    
    const captureCount = timeRange.count;
    
    // Store original timestamp strings for API queries
    window.firstCaptureTimeStr = timeRange.first_capture_time;
    window.lastCaptureTimeStr = timeRange.last_capture_time;
    
    // Generate timestamp in the same format as backend (YYYYMMDD_HHMMSS)
    const now = new Date();
    const timestamp = now.getFullYear() +
        String(now.getMonth() + 1).padStart(2, '0') +
        String(now.getDate()).padStart(2, '0') + '_' +
        String(now.getHours()).padStart(2, '0') +
        String(now.getMinutes()).padStart(2, '0') +
        String(now.getSeconds()).padStart(2, '0');
    
    // Set values
    document.getElementById('process_job_id').value = jobId;
    document.getElementById('video_name').value = `${jobName}_${timestamp}`;
    document.getElementById('video_framerate').value = job.framerate;
    
    // Add native resolution option if available
    const resSelect = document.getElementById('video_resolution');
    const existingNative = resSelect.querySelector('option[value^="native:"]');
    if (existingNative) existingNative.remove();
    if (timeRange.native_resolution) {
        const nativeOpt = document.createElement('option');
        nativeOpt.value = timeRange.native_resolution;
        nativeOpt.textContent = `Native (${timeRange.native_resolution})`;
        resSelect.insertBefore(nativeOpt, resSelect.firstChild);
        resSelect.value = timeRange.native_resolution;
        toggleCustomResolution();
    }
    
    // Store capture count for duration calculation
    document.getElementById('video_framerate').setAttribute('data-capture-count', captureCount);
    
    // Set time range inputs to first and last capture times
    if (captureCount > 0) {
        // Parse ISO timestamps - use capture times as they represent actual available data
        const firstDate = new Date(timeRange.first_capture_time);
        const lastDate = new Date(timeRange.last_capture_time);
        
        // Store globally for validation
        window.firstCaptureTime = firstDate;
        window.lastCaptureTime = lastDate;
        
        // Display available time range in 24-hour format
        const rangeInfo = document.getElementById('available-range-info');
        const rangeSpan = document.getElementById('capture-time-range');
        
        if (rangeInfo && rangeSpan) {
            const use12 = getTimeFormat() === '12';
            const formatOptions = { 
                year: 'numeric', 
                month: '2-digit', 
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: use12
            };
            const locale = use12 ? 'en-US' : 'en-CA';
            const firstFormatted = firstDate.toLocaleString(locale, formatOptions);
            const lastFormatted = lastDate.toLocaleString(locale, formatOptions);
            rangeSpan.textContent = `${firstFormatted} - ${lastFormatted}`;
            rangeInfo.style.display = 'block';
        }
        
        // Set start/end datetime-local inputs directly
        const startInput = document.getElementById('video_start_datetime');
        const endInput = document.getElementById('video_end_datetime');
        
        if (startInput) startInput.value = isoToDatetimeLocal(timeRange.first_capture_time);
        if (endInput) endInput.value = isoToDatetimeLocal(timeRange.last_capture_time);
        
        // Store job ID for time range queries
        window.currentJobId = jobId;
        
        // Set up event listeners for duration updates when time range changes
        const updateDuration = debounce(updateVideoDurationEstimate, 300);
        
        [startInput, endInput].forEach(input => {
            if (input) {
                input.addEventListener('change', updateDuration);
                input.addEventListener('input', updateDuration);
            }
        });
    }
    
    // Reset the use_range checkbox
    document.getElementById('use_range').checked = false;
    document.getElementById('capture-range-fields').classList.add('disabled');
    
    // Calculate and display initial duration
    updateVideoDurationEstimate();
    
    // Enable the create button now that a job is loaded
    const createBtn = document.getElementById('create-video-btn');
    if (createBtn) {
        createBtn.disabled = false;
    }
    
    // Update tag picker with job's tags
    const jobTagIds = (job.tags || []).map(t => t.id);
    renderTagPicker('build-video-tags', jobTagIds);
}

function updateVideoDurationEstimate() {
    const framerate = parseInt(document.getElementById('video_framerate').value) || 30;
    const useRange = document.getElementById('use_range')?.checked;
    
    if (useRange) {
        const startTimeInput = document.getElementById('video_start_datetime');
        const endTimeInput = document.getElementById('video_end_datetime');
        
        if (!startTimeInput?.value || !endTimeInput?.value || !window.currentJobId) {
            return;
        }
        
        // Convert datetime-local values to ISO for API query
        const startTimeStr = datetimeLocalToISO(startTimeInput.value);
        const endTimeStr = datetimeLocalToISO(endTimeInput.value);
        
        fetch(`${API_BASE}/captures/job/${window.currentJobId}/time-range?start_time=${encodeURIComponent(startTimeStr)}&end_time=${encodeURIComponent(endTimeStr)}`)
            .then(r => r.json())
            .then(data => displayDurationEstimate(data.count, framerate))
            .catch(error => console.error('Failed to get capture count:', error));
    } else {
        const captureCount = parseInt(document.getElementById('video_framerate').getAttribute('data-capture-count')) || 0;
        displayDurationEstimate(captureCount, framerate);
    }
}

function displayDurationEstimate(captureCount, framerate) {
    const createBtn = document.getElementById('create-video-btn');
    const useRange = document.getElementById('use_range')?.checked;
    
    // Validate custom time range against available captures
    const rangeInfo = document.getElementById('available-range-info');
    let rangeWarning = false;
    
    if (useRange && window.firstCaptureTime && window.lastCaptureTime) {
        const startTimeInput = document.getElementById('video_start_datetime');
        const endTimeInput = document.getElementById('video_end_datetime');
        
        if (startTimeInput?.value && endTimeInput?.value) {
            const customStart = new Date(startTimeInput.value);
            const customEnd = new Date(endTimeInput.value);
            
            if (customEnd < window.firstCaptureTime || customStart > window.lastCaptureTime) {
                rangeWarning = true;
                if (rangeInfo) {
                    rangeInfo.style.display = '';
                    rangeInfo.style.borderLeftColor = 'var(--danger-color)';
                    rangeInfo.style.color = 'var(--danger-color)';
                    rangeInfo.innerHTML = `<strong>Warning:</strong> Selected range is outside available captures! Available: ${formatDateTime(window.firstCaptureTime.toISOString())} – ${formatDateTime(window.lastCaptureTime.toISOString())}`;
                }
                captureCount = 0;
            }
        }
    }
    
    if (captureCount === 0 && !rangeWarning && useRange) {
        if (rangeInfo) {
            rangeInfo.style.display = '';
            rangeInfo.style.borderLeftColor = 'var(--danger-color)';
            rangeInfo.style.color = 'var(--danger-color)';
            rangeInfo.innerHTML = `<strong>Warning:</strong> No captures in selected time range!`;
        }
    } else if (!rangeWarning && rangeInfo) {
        rangeInfo.style.borderLeftColor = '';
        rangeInfo.style.color = '';
    }
    
    if (captureCount === 0) {
        document.getElementById('video-duration-estimate').innerHTML = `
            <span style="font-weight: 600;">0s</span>
            <span style="color: var(--text-secondary); font-size: 0.85rem;"> · 0 captures @ ${framerate} FPS</span>
        `;
        if (createBtn) setButtonState(createBtn, true);
        return;
    }
    
    // Enable create button when captures exist
    if (createBtn) {
        setButtonState(createBtn, false);
    }
    
    const durationSeconds = captureCount / framerate;
    const minutes = Math.floor(durationSeconds / 60);
    const seconds = Math.floor(durationSeconds % 60);
    
    document.getElementById('video-duration-estimate').innerHTML = `
        <span style="font-weight: 600;">${minutes}m ${seconds}s</span>
        <span style="color: var(--text-secondary); font-size: 0.85rem;"> · ${captureCount} captures @ ${framerate} FPS</span>
    `;
}

// Debounce helper function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function toggleCustomResolution() {
    const resolutionSelect = document.getElementById('video_resolution');
    const customResolutionDiv = document.getElementById('custom-resolution');
    const customWidth = document.getElementById('custom_width');
    const customHeight = document.getElementById('custom_height');
    
    if (resolutionSelect.value === 'custom') {
        customResolutionDiv.style.display = 'flex';
        customWidth.required = true;
        customHeight.required = true;
    } else {
        customResolutionDiv.style.display = 'none';
        customWidth.required = false;
        customHeight.required = false;
    }
}

// ─── Text Overlay Component ───
// Reusable overlay widget: generateOverlayHTML(prefix) → readOverlayConfig(prefix)
// Three instances: "build" (manual build modal), "create-ab" (create-job auto-build),
// "edit-ab" (edit-job auto-build). All share the same HTML generator and config reader.

let _overlayPreviewTimeout = null;
let _overlayFontsLoaded = false;
let _overlayFontsCache = [];

const OVERLAY_POSITIONS = [
    { pos: 'top-left',      icon: '↖' },
    { pos: 'top-center',    icon: '↑' },
    { pos: 'top-right',     icon: '↗' },
    { pos: 'middle-left',   icon: '←' },
    { pos: 'middle-center', icon: '●' },
    { pos: 'middle-right',  icon: '→' },
    { pos: 'bottom-left',   icon: '↙' },
    { pos: 'bottom-center', icon: '↓' },
    { pos: 'bottom-right',  icon: '↘' },
];

/**
 * Generate overlay widget HTML for a container.
 * @param {string} prefix  - Unique prefix for element IDs (e.g., "build", "create-ab", "edit-ab")
 * @param {object} opts    - { label: string, showPreview: bool, onchange: string|null }
 */
function generateOverlayHTML(prefix, opts = {}) {
    const label = opts.label || 'Add Text Overlay';
    const showPreview = opts.showPreview || false;
    const onchangeAttr = opts.onchange ? ` onchange="${opts.onchange}"` : '';
    const inputEvent = opts.onchange ? ` oninput="${opts.onchange}" onchange="${opts.onchange}"` : '';

    const gridBtns = OVERLAY_POSITIONS.map(p =>
        `<button type="button" class="pos-btn${p.pos === 'bottom-left' ? ' active' : ''}" data-pos="${p.pos}" title="${p.pos}">${p.icon}</button>`
    ).join('');

    const fontOpts = _overlayFontsCache.length
        ? _overlayFontsCache.map(f => `<option value="${f.name}">${f.name}</option>`).join('')
        : '<option value="DejaVu Sans">DejaVu Sans</option>';

    const controlsHtml = `
            <div style="display:flex; gap:0.75rem; align-items:center;">
                <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:0.35rem;">
                    <div style="display:flex; gap:0.5rem; align-items:flex-end;">
                        <div style="flex: 1 1 0; min-width:0;">
                            <label style="font-size:0.7rem; margin-bottom:2px; display:block;">Text</label>
                            <input type="text" id="${prefix}-overlay-text" class="form-control" value="{job_name}" placeholder="{job_name}"${inputEvent}>
                        </div>
                        <div style="flex: 0 0 163px;">
                            <label style="font-size:0.7rem; margin-bottom:2px; display:block;">Font</label>
                            <select id="${prefix}-overlay-font" class="form-control"${inputEvent}>${fontOpts}</select>
                        </div>
                        <div style="flex:0 0 56px;">
                            <label style="font-size:0.7rem; margin-bottom:2px; display:block;">Size</label>
                            <input type="number" id="${prefix}-overlay-size" class="form-control" value="5" min="1" max="20" step="1"${inputEvent}>
                        </div>
                        <div style="flex:0 0 36px;">
                            <label style="font-size:0.7rem; margin-bottom:2px; display:block;">Bold</label>
                            <input type="checkbox" id="${prefix}-overlay-bold"${onchangeAttr} style="margin-top:10px; margin-left:6px;">
                        </div>
                    </div>
                    <small style="color: var(--text-secondary); font-size:0.6rem;"><code>{job_name}</code> <code>{date}</code> <code>{time}</code> <code>{datetime}</code> <code>{frame}</code></small>
                    <div style="display:flex; gap:0.3rem; align-items:center;">
                        <span style="font-size:0.8rem; color:var(--text-secondary); white-space:nowrap;">Text:</span>
                        <input type="color" id="${prefix}-overlay-color" value="#FFFFFF" style="border:none;padding:0;height:18px;width:18px;border-radius:50%;cursor:pointer;background:none;"${inputEvent}>
                        <input type="range" id="${prefix}-overlay-color-opacity" min="0" max="100" value="100" style="flex:1; height:14px;"${inputEvent}>
                        <span id="${prefix}-overlay-color-opacity-label" style="width:30px;text-align:right;font-size:0.75rem;color:var(--text-secondary);">100%</span>
                        <input type="checkbox" id="${prefix}-overlay-bg" checked${onchangeAttr} style="margin:0 0 0 0.25rem;">
                        <span style="font-size:0.8rem; color:var(--text-secondary); white-space:nowrap;">Background:</span>
                        <input type="color" id="${prefix}-overlay-bg-color" value="#000000" style="border:none;padding:0;height:18px;width:18px;border-radius:50%;cursor:pointer;background:none;"${inputEvent}>
                        <input type="range" id="${prefix}-overlay-bg-opacity" min="0" max="100" value="50" style="flex:1; height:14px;"${inputEvent}>
                        <span id="${prefix}-overlay-opacity-label" style="width:30px;text-align:right;font-size:0.75rem;color:var(--text-secondary);">50%</span>
                    </div>
                </div>
                <div style="flex:0 0 auto;">
                    <div class="overlay-position-grid" id="${prefix}-overlay-grid">${gridBtns}</div>
                </div>
            </div>`;

    const previewHtml = showPreview ? `
            <div class="overlay-preview-panel" id="${prefix}-overlay-preview-panel">
                <img id="${prefix}-overlay-preview-img" alt="Overlay preview" style="border-radius: var(--radius-lg); border: 1px solid var(--border-color); display: none;">
                <div id="${prefix}-overlay-preview-placeholder" style="width:100%; aspect-ratio:16/9; background:var(--surface-color); border:1px dashed var(--border-color); border-radius:var(--radius-lg); display:flex; align-items:center; justify-content:center; color:var(--text-secondary); font-size:0.8rem;">
                    No preview
                </div>
            </div>` : '';

    // Preview on top, controls below
    const fieldsInner = showPreview
        ? `<div class="overlay-layout">\n${previewHtml}\n<div class="overlay-controls">${controlsHtml}</div>\n</div>`
        : controlsHtml;

    return `
        <div class="form-group" style="margin-top: 0.5rem;">
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <input type="checkbox" id="${prefix}-overlay-enabled"${onchangeAttr}>
                <span>${label}</span>
            </div>
            <small style="color: var(--text-secondary); display: block; margin-left: 1.5rem;">Burn text into each frame during timelapse rendering</small>
        </div>
        <div id="${prefix}-overlay-fields" class="toggle-fields disabled">
            ${fieldsInner}
        </div>`;
}

function openOverlayLightbox(imgEl) {
    if (!imgEl || !imgEl.src) return;
    const lb = document.createElement('div');
    lb.className = 'overlay-lightbox';
    lb.innerHTML = `<img src="${imgEl.src}" alt="Preview">`;
    lb.addEventListener('click', () => lb.remove());
    document.body.appendChild(lb);
}

/**
 * Initialize an overlay widget after its HTML is in the DOM.
 * Sets up position grid clicks and toggle behavior.
 */
function initOverlayWidget(prefix, opts = {}) {
    const cb = document.getElementById(`${prefix}-overlay-enabled`);
    const fields = document.getElementById(`${prefix}-overlay-fields`);
    if (!cb || !fields) return;

    // Toggle visibility
    cb.addEventListener('change', () => {
        if (cb.checked) {
            fields.classList.remove('disabled');
            if (!_overlayFontsLoaded) loadOverlayFonts();
        } else {
            fields.classList.add('disabled');
        }
        if (opts.onToggle) opts.onToggle(cb.checked);
    });

    // Position grid clicks
    const grid = document.getElementById(`${prefix}-overlay-grid`);
    if (grid) {
        grid.querySelectorAll('.pos-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                grid.querySelectorAll('.pos-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (opts.onChange) opts.onChange();
            });
        });
    }

    // Opacity label sync
    const opSlider = document.getElementById(`${prefix}-overlay-bg-opacity`);
    const opLabel = document.getElementById(`${prefix}-overlay-opacity-label`);
    if (opSlider && opLabel) {
        opSlider.addEventListener('input', () => { opLabel.textContent = opSlider.value + '%'; });
    }
    const colorOpSlider = document.getElementById(`${prefix}-overlay-color-opacity`);
    const colorOpLabel = document.getElementById(`${prefix}-overlay-color-opacity-label`);
    if (colorOpSlider && colorOpLabel) {
        colorOpSlider.addEventListener('input', () => { colorOpLabel.textContent = colorOpSlider.value + '%'; });
    }
}

/** Read overlay config from any widget by prefix. Returns config object or null. */
function readOverlayConfig(prefix) {
    const cb = document.getElementById(`${prefix}-overlay-enabled`);
    if (!cb || !cb.checked) return null;
    const text = document.getElementById(`${prefix}-overlay-text`)?.value || '';
    if (!text.trim()) return null;
    const grid = document.getElementById(`${prefix}-overlay-grid`);
    const activeBtn = grid?.querySelector('.pos-btn.active');
    return {
        enabled: true,
        text,
        font: document.getElementById(`${prefix}-overlay-font`)?.value || 'DejaVu Sans',
        font_size: parseInt(document.getElementById(`${prefix}-overlay-size`)?.value) || 5,
        bold: document.getElementById(`${prefix}-overlay-bold`)?.checked || false,
        color: document.getElementById(`${prefix}-overlay-color`)?.value || '#FFFFFF',
        color_opacity: parseInt(document.getElementById(`${prefix}-overlay-color-opacity`)?.value || '100') / 100,
        position: activeBtn?.dataset.pos || 'bottom-left',
        background: document.getElementById(`${prefix}-overlay-bg`)?.checked !== false,
        background_color: document.getElementById(`${prefix}-overlay-bg-color`)?.value || '#000000',
        background_opacity: parseInt(document.getElementById(`${prefix}-overlay-bg-opacity`)?.value || '50') / 100
    };
}

/** Write overlay config into any widget by prefix. */
function writeOverlayConfig(prefix, config) {
    if (!config) return;
    const el = id => document.getElementById(`${prefix}-overlay-${id}`);
    if (el('enabled')) el('enabled').checked = !!config.enabled;
    const fields = document.getElementById(`${prefix}-overlay-fields`);
    if (fields) {
        if (config.enabled) {
            fields.classList.remove('disabled');
        } else {
            fields.classList.add('disabled');
        }
    }
    if (el('text')) el('text').value = config.text || '';
    if (el('font')) el('font').value = config.font || 'DejaVu Sans';
    if (el('size')) el('size').value = config.font_size || 5;
    if (el('bold')) el('bold').checked = !!config.bold;
    if (el('color')) el('color').value = config.color || '#FFFFFF';
    if (el('color-opacity')) el('color-opacity').value = Math.round((config.color_opacity ?? 1.0) * 100);
    const colorOpLabel = document.getElementById(`${prefix}-overlay-color-opacity-label`);
    if (colorOpLabel) colorOpLabel.textContent = Math.round((config.color_opacity ?? 1.0) * 100) + '%';
    if (el('bg')) el('bg').checked = config.background !== false;
    if (el('bg-color')) el('bg-color').value = config.background_color || '#000000';
    if (el('bg-opacity')) el('bg-opacity').value = Math.round((config.background_opacity ?? 0.5) * 100);
    const opLabel = document.getElementById(`${prefix}-overlay-opacity-label`);
    if (opLabel) opLabel.textContent = Math.round((config.background_opacity ?? 0.5) * 100) + '%';
    // Set position
    const grid = document.getElementById(`${prefix}-overlay-grid`);
    if (grid && config.position) {
        grid.querySelectorAll('.pos-btn').forEach(b => b.classList.remove('active'));
        const match = grid.querySelector(`[data-pos="${config.position}"]`);
        if (match) match.classList.add('active');
    }
}

/** Mount an overlay widget into a container element. */
function mountOverlayWidget(containerId, prefix, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = generateOverlayHTML(prefix, opts);
    initOverlayWidget(prefix, opts);
}

async function loadOverlayFonts() {
    try {
        _overlayFontsCache = await apiRequest('/videos/fonts');
        // Update all font selects currently in the DOM
        document.querySelectorAll('[id$="-overlay-font"]').forEach(sel => {
            sel.innerHTML = _overlayFontsCache.map(f =>
                `<option value="${f.name}">${f.name}</option>`
            ).join('');
        });
        _overlayFontsLoaded = true;
    } catch (e) {
        console.error('Failed to load fonts:', e);
    }
}

// ─── Build Modal Overlay (with live preview) ───

function initBuildOverlay() {
    mountOverlayWidget('build-overlay-container', 'build', {
        label: 'Add Text Overlay',
        onchange: 'debouncedOverlayPreview()',
        onToggle: (enabled) => { if (enabled) debouncedOverlayPreview(); else resetOverlayPreview(); },
        onChange: () => debouncedOverlayPreview(),
    });
}

function debouncedOverlayPreview() {
    clearTimeout(_overlayPreviewTimeout);
    _overlayPreviewTimeout = setTimeout(updateOverlayPreview, 400);
    // Sync opacity label
    const opSlider = document.getElementById('build-overlay-bg-opacity');
    const opLabel = document.getElementById('build-overlay-opacity-label');
    if (opSlider && opLabel) opLabel.textContent = opSlider.value + '%';
}

async function updateOverlayPreview() {
    const config = readOverlayConfig('build');
    const captureId = window._overlayPreviewCaptureId;
    if (!config || !captureId) { resetOverlayPreview(); return; }

    try {
        const resp = await fetch(`${API_BASE}/videos/text-overlay-preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Referer': window.location.href },
            body: JSON.stringify({ capture_id: captureId, config, job_name: window._overlayJobName || 'Sample Job' })
        });
        if (!resp.ok) { console.error('Build overlay preview API error:', resp.status); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const img = document.getElementById('job-preview-img');
        if (img) {
            if (img._overlayUrl) URL.revokeObjectURL(img._overlayUrl);
            img._overlayUrl = url;
            img.src = url;
        }
    } catch (e) { console.error('Overlay preview error:', e); }
}

function resetOverlayPreview() {
    const img = document.getElementById('job-preview-img');
    if (img) {
        if (img._overlayUrl) {
            URL.revokeObjectURL(img._overlayUrl);
            img._overlayUrl = null;
        }
        // Restore original thumbnail
        if (img._originalSrc) {
            img.src = img._originalSrc;
        }
    }
}

// ─── Auto-Build Overlay (create-job and edit-job) ───

let _createOverlayPreviewTimeout = null;

function initCreateJobOverlay() {
    const triggerCreatePreview = () => {
        clearTimeout(_createOverlayPreviewTimeout);
        _createOverlayPreviewTimeout = setTimeout(() => updateOverlayFromUrl('create-ab'), 300);
    };

    mountOverlayWidget('create-ab-overlay-container', 'create-ab', {
        label: 'Add Text Overlay',
        showPreview: true,
        onchange: '_createAbOverlayChanged()',
        onToggle: (enabled) => { if (enabled) fetchOverlayPreviewFromUrl('create-ab'); },
        onChange: () => triggerCreatePreview(),
    });
    window._createAbOverlayChanged = triggerCreatePreview;
}

let _editOverlayPreviewTimeout = null;

function initEditJobOverlay(job) {
    // Store job context for preview
    window._editOverlayJob = job;

    const triggerEditPreview = () => {
        clearTimeout(_editOverlayPreviewTimeout);
        _editOverlayPreviewTimeout = setTimeout(() => updateGenericOverlayPreview('edit-ab', job), 300);
    };

    mountOverlayWidget('edit-ab-overlay-container', 'edit-ab', {
        label: 'Add Text Overlay',
        showPreview: true,
        onchange: '_editAbOverlayChanged()',
        onToggle: (enabled) => { if (enabled) triggerEditPreview(); },
        onChange: () => triggerEditPreview(),
    });

    // Expose for inline onchange attr
    window._editAbOverlayChanged = triggerEditPreview;

    // Load existing config from job
    if (job.auto_build_text_overlay) {
        try {
            const config = JSON.parse(job.auto_build_text_overlay);
            if (config) writeOverlayConfig('edit-ab', config);
        } catch (e) {}
    }

    // Load preview image from latest capture
    loadOverlayPreviewImage('edit-ab', job);
}

async function loadOverlayPreviewImage(prefix, job) {
    const img = document.getElementById(`${prefix}-overlay-preview-img`);
    const placeholder = document.getElementById(`${prefix}-overlay-preview-placeholder`);
    if (!img) return;

    if (placeholder) placeholder.innerHTML = '<div style="font-size:0.8rem; color:var(--text-secondary);">Loading preview…</div>';

    try {
        // Use the job's latest capture for the preview
        const capsData = await apiRequest('/captures/', { query: { job_id: job.id, page_size: 1, sort_order: 'desc' } });
        if (capsData.captures && capsData.captures.length > 0) {
            const cap = capsData.captures[0];
            img._captureId = cap.id;
            img._originalSrc = `${API_BASE}/captures/${cap.id}/image`;
            img.src = img._originalSrc;
            img.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
            // If overlay is already enabled, render preview
            const cb = document.getElementById(`${prefix}-overlay-enabled`);
            if (cb && cb.checked) {
                setTimeout(() => updateGenericOverlayPreview(prefix, job), 200);
            }
        } else {
            // No captures -- try to fetch from the job's URL
            showOverlayPreviewPlaceholder(prefix, 'No captures yet', job.url);
        }
    } catch (e) {
        console.error('loadOverlayPreviewImage error:', e);
        showOverlayPreviewPlaceholder(prefix, 'Preview unavailable');
    }
}

function showOverlayPreviewPlaceholder(prefix, message, streamUrl) {
    const placeholder = document.getElementById(`${prefix}-overlay-preview-placeholder`);
    if (!placeholder) return;
    const refreshBtn = streamUrl
        ? `<button type="button" class="btn btn-secondary" style="font-size:0.75rem; padding:0.3rem 0.6rem; margin-top:0.4rem;" onclick="fetchOverlayPreviewFromUrl('${prefix}')">
               Fetch Preview
           </button>`
        : '';
    placeholder.innerHTML = `
        <div style="text-align:center;">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:0.4; margin-bottom:0.25rem;">
                <rect x="2" y="2" width="20" height="20" rx="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <path d="m21 15-5-5L5 21"/>
            </svg>
            <div style="font-size:0.8rem;">${message}</div>
            ${refreshBtn}
        </div>`;
}

async function fetchOverlayPreviewFromUrl(prefix) {
    const placeholder = document.getElementById(`${prefix}-overlay-preview-placeholder`);
    const img = document.getElementById(`${prefix}-overlay-preview-img`);
    if (!img) return;

    // Determine URL source based on context
    let url;
    if (prefix === 'create-ab') {
        url = document.getElementById('job_url')?.value;
    } else if (prefix === 'edit-ab') {
        url = document.getElementById('edit_url')?.value;
    }
    if (!url) {
        showOverlayPreviewPlaceholder(prefix, 'Enter a stream URL first');
        return;
    }

    if (placeholder) placeholder.innerHTML = '<div style="font-size:0.8rem; color:var(--text-secondary);">Loading preview…</div>';

    try {
        const result = await apiRequest('/jobs/test-url', { method: 'POST', query: { url } });
        if (result.success && result.image_data) {
            img._base64 = result.image_data;
            img._originalSrc = result.image_data;
            img.src = result.image_data;
            img.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
            // If overlay is enabled, render with overlay
            const cb = document.getElementById(`${prefix}-overlay-enabled`);
            if (cb && cb.checked) {
                setTimeout(() => updateOverlayFromUrl(prefix), 100);
            }
        } else {
            showOverlayPreviewPlaceholder(prefix, 'Could not fetch preview', url);
        }
    } catch (e) {
        showOverlayPreviewPlaceholder(prefix, 'Could not fetch preview', url);
    }
}

async function updateOverlayFromUrl(prefix) {
    const config = readOverlayConfig(prefix);
    const img = document.getElementById(`${prefix}-overlay-preview-img`);
    if (!img) return;
    if (!config) {
        if (img._originalSrc) img.src = img._originalSrc;
        if (img._overlayUrl) { URL.revokeObjectURL(img._overlayUrl); img._overlayUrl = null; }
        return;
    }
    // Need either capture_id or base64
    const hasCaptureId = img._captureId;
    const hasBase64 = img._base64;
    if (!hasCaptureId && !hasBase64) return;

    const jobName = prefix === 'create-ab'
        ? (document.getElementById('job_name')?.value || 'New Job')
        : (window._editOverlayJob?.name || 'Sample Job');

    try {
        const body = { config, job_name: jobName };
        if (hasCaptureId) body.capture_id = img._captureId;
        else body.image_data = img._base64;

        const resp = await fetch(`${API_BASE}/videos/text-overlay-preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Referer': window.location.href },
            body: JSON.stringify(body)
        });
        if (!resp.ok) throw new Error('Preview failed');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        if (img._overlayUrl) URL.revokeObjectURL(img._overlayUrl);
        img._overlayUrl = url;
        img.src = url;
    } catch (e) { console.error('Overlay preview error:', e); }
}

async function updateGenericOverlayPreview(prefix, job) {
    const config = readOverlayConfig(prefix);
    const img = document.getElementById(`${prefix}-overlay-preview-img`);
    if (!img) return;
    // Delegate to URL-based previewer if we have base64 but no capture
    if (!img._captureId && img._base64) {
        return updateOverlayFromUrl(prefix);
    }
    if (!img._captureId) return;
    if (!config) {
        // Restore original
        if (img._originalSrc) img.src = img._originalSrc;
        if (img._overlayUrl) { URL.revokeObjectURL(img._overlayUrl); img._overlayUrl = null; }
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/videos/text-overlay-preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Referer': window.location.href },
            body: JSON.stringify({ capture_id: img._captureId, config, job_name: job.name || 'Sample Job' })
        });
        if (!resp.ok) { console.error('Overlay preview API error:', resp.status); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        if (img._overlayUrl) URL.revokeObjectURL(img._overlayUrl);
        img._overlayUrl = url;
        img.src = url;
    } catch (e) { console.error('Edit overlay preview error:', e); }
}

async function processVideo(event) {
    event.preventDefault();
    
    const useRange = document.getElementById('use_range').checked;
    let resolution = document.getElementById('video_resolution').value;
    
    // Handle custom resolution
    if (resolution === 'custom') {
        const width = document.getElementById('custom_width').value;
        const height = document.getElementById('custom_height').value;
        resolution = `${width}x${height}`;
    }
    
    const framerate = parseInt(document.getElementById('video_framerate').value);
    
    // Validate framerate
    if (!framerate || framerate < 1) {
        showNotification('Framerate must be at least 1 FPS', 'error');
        return;
    }
    
    const formData = {
        job_id: parseInt(document.getElementById('process_job_id').value),
        name: document.getElementById('video_name').value,
        resolution: resolution,
        framerate: framerate,
        quality: document.getElementById('video_quality').value,
        start_time: useRange ? datetimeLocalToISO(document.getElementById('video_start_datetime').value) : null,
        end_time: useRange ? datetimeLocalToISO(document.getElementById('video_end_datetime').value) : null,
        text_overlay: readOverlayConfig('build'),
        tag_ids: getSelectedTagIds('build-video-tags')
    };
    
    try {
        const video = await apiRequest('/videos/', { method: 'POST', body: formData });
        document.getElementById('process-video-modal').classList.remove('active');
        if (_modalHistoryDepth > 0) _modalHistoryDepth--;
        document.getElementById('process-video-form').reset();
        navigateTo(`/timelapses/${video.id}`);
        showNotification('Video processing started');
    } catch (error) {
        console.error('Failed to process video:', error);
        showNotification(`Failed to start processing: ${error.message || 'Unknown error'}`, 'error');
    }
}

function navigateToJob(jobId) {
    navigateTo(`/jobs/${jobId}`);
}

// Modal management
let _modalHistoryDepth = 0;

function showModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.add('active');
    
    // Always scroll to top when opening any modal
    const modalContent = modal.querySelector('.modal-content');
    if (modalContent) {
        modalContent.scrollTop = 0;
    }
    
    // Push history state so back button can close modal
    _modalHistoryDepth++;
    history.pushState({ modal: true, depth: _modalHistoryDepth }, '');
}

let _closingFromPopstate = false;

function closeModal(modalId) {
    const wasActive = document.getElementById(modalId).classList.contains('active');
    document.getElementById(modalId).classList.remove('active');
    
    // Pop history entry if we pushed one and this isn't from popstate
    if (wasActive && _modalHistoryDepth > 0 && !_closingFromPopstate) {
        _modalHistoryDepth--;
        history.back();
    }
    
    // Clear form when closing create job modal
    if (modalId === 'create-job-modal') {
        document.getElementById('create-job-form').reset();
        document.getElementById('test-result').innerHTML = '';
        // Reset datetime to now for next time modal opens
        setDefaultStartTime();
    }
    
    // Clean up import staging session when closing import modal
    if (modalId === 'import-modal') {
        resetImportModal();
    }
    
    // Clean up video modal listeners to prevent memory leaks
    if (modalId === 'process-video-modal' && window._videoModalListeners) {
        const startInput = document.getElementById('video_start_datetime');
        const endInput = document.getElementById('video_end_datetime');
        
        [startInput, endInput].forEach(input => {
            if (input && window._videoModalListeners) {
                input.removeEventListener('change', window._videoModalListeners);
                input.removeEventListener('input', window._videoModalListeners);
            }
        });
        
        window._videoModalListeners = null;
        window.currentJobId = null;
        window.firstCaptureTime = null;
        window.lastCaptureTime = null;

        // Clean up overlay state
        resetOverlayPreview();
        const buildOverlayContainer = document.getElementById('build-overlay-container');
        if (buildOverlayContainer) buildOverlayContainer.innerHTML = '';
        window._overlayPreviewCaptureId = null;
        window._overlayJobName = null;
    }
}

// Close the topmost active modal on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        // Close lightbox first if open
        const lightbox = document.querySelector('.overlay-lightbox');
        if (lightbox) {
            lightbox.remove();
            return;
        }
        
        // Check custom modals first (confirm dialog)
        const confirmModal = document.getElementById('confirm-modal');
        if (confirmModal && confirmModal.classList.contains('active')) {
            confirmModal.classList.remove('active');
            return;
        }
        
        // Close the last opened standard modal
        const modals = document.querySelectorAll('.modal.active');
        if (modals.length > 0) {
            const topModal = modals[modals.length - 1];
            closeModal(topModal.id);
        }
    }
});

// ── Directory Import ──────────────────────────────────────────────────────

// ── Import modal (redesigned) ──────────────────────────
let _importSessionId = null;
let _importAnalysis = null;
let _importBrowsePath = '/imports';
let _importSourcePath = null;

function showImportModal() {
    resetImportModal();
    showModal('import-modal');
    loadImportBrowse();
}

function resetImportModal() {
    // Clean up any active staging session on the server
    if (_importSessionId) {
        apiRequest(`/import/${_importSessionId}`, { method: 'DELETE' }).catch(() => {});
    }
    _importSessionId = null;
    _importAnalysis = null;
    _importSourcePath = null;
    document.getElementById('import-source-panels').style.display = 'flex';
    document.getElementById('import-staging-preview').style.display = 'none';
    document.getElementById('import-upload-progress').style.display = 'none';
    document.getElementById('import-folder-input').value = '';
    const fileInput = document.getElementById('import-file-input');
    if (fileInput) fileInput.value = '';
    document.getElementById('import-job-name').value = '';
    const metaBanner = document.getElementById('import-export-metadata');
    metaBanner.style.display = 'none';
    metaBanner.innerHTML = '';
    const execBtn = document.getElementById('import-execute-btn');
    execBtn.disabled = false;
    execBtn.textContent = 'Import';
}

// --- Upload handling ---
function handleImportDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    
    // Use webkitGetAsEntry for recursive folder traversal
    const items = e.dataTransfer.items;
    if (!items || !items.length) return;
    
    const entries = [];
    for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry ? items[i].webkitGetAsEntry() : null;
        if (entry) entries.push(entry);
    }
    
    if (entries.length === 0) {
        // Fallback for browsers without webkitGetAsEntry
        if (e.dataTransfer.files.length) handleImportFiles(e.dataTransfer.files);
        return;
    }
    
    // Capture root folder/file name for job name suggestion
    if (entries.length === 1 && entries[0].isDirectory) {
        _importSourcePath = '/upload/' + entries[0].name;
    } else {
        _importSourcePath = null;
    }
    
    // Recursively collect all files from entries
    collectFilesFromEntries(entries).then(files => {
        if (files.length) handleImportFiles(files);
        else showNotification('No supported files found', 'error');
    });
}

function collectFilesFromEntries(entries) {
    return new Promise(resolve => {
        const files = [];
        let pending = entries.length;
        if (pending === 0) { resolve(files); return; }
        
        function readEntry(entry) {
            if (entry.isFile) {
                entry.file(file => {
                    files.push(file);
                    if (--pending === 0) resolve(files);
                }, () => { if (--pending === 0) resolve(files); });
            } else if (entry.isDirectory) {
                const reader = entry.createReader();
                const readBatch = () => {
                    reader.readEntries(batch => {
                        if (batch.length === 0) {
                            if (--pending === 0) resolve(files);
                            return;
                        }
                        pending += batch.length;
                        batch.forEach(readEntry);
                        readBatch(); // Continue reading (batched at 100 entries)
                    }, () => { if (--pending === 0) resolve(files); });
                };
                readBatch();
            } else {
                if (--pending === 0) resolve(files);
            }
        }
        
        entries.forEach(readEntry);
    });
}

async function handleImportFiles(files) {
    if (!files || !files.length) return;

    const progressWrap = document.getElementById('import-upload-progress');
    const progressBar = document.getElementById('import-progress-bar');
    const progressText = document.getElementById('import-progress-text');
    progressWrap.style.display = 'block';
    progressBar.style.width = '0%';

    // Convert to array (may be FileList or Array)
    const fileArr = Array.from(files);
    const total = fileArr.length;
    progressText.textContent = `Uploading ${total} file(s)...`;

    try {
        let result;
        const CHUNK_SIZE = 50; // Upload in batches of 50 files
        
        if (total === 1) {
            const formData = new FormData();
            formData.append('file', fileArr[0]);
            result = await apiRequest('/import/upload', { method: 'POST', rawBody: formData });
        } else if (total <= CHUNK_SIZE) {
            const formData = new FormData();
            for (const f of fileArr) formData.append('files', f);
            result = await apiRequest('/import/upload-batch', { method: 'POST', rawBody: formData });
        } else {
            // Upload in chunks, reusing the same session
            let uploaded = 0;
            for (let i = 0; i < total; i += CHUNK_SIZE) {
                const chunk = fileArr.slice(i, i + CHUNK_SIZE);
                const formData = new FormData();
                for (const f of chunk) formData.append('files', f);
                
                if (result) {
                    // Subsequent chunks: append to existing session
                    formData.append('session_id', result.session_id);
                }
                result = await apiRequest('/import/upload-batch', { method: 'POST', rawBody: formData });
                
                uploaded += chunk.length;
                const pct = Math.round((uploaded / total) * 100);
                progressBar.style.width = `${pct}%`;
                progressText.textContent = `Uploading ${uploaded}/${total} files...`;
            }
        }

        progressBar.style.width = '100%';
        progressText.textContent = 'Upload complete. Analyzing...';
        _importSessionId = result.session_id;
        await analyzeAndShowPreview();
    } catch (error) {
        progressText.textContent = 'Upload failed';
        showNotification(error.message || 'Upload failed', 'error');
    }
}

// --- Server path browser ---
async function loadImportBrowse(path) {
    if (path) _importBrowsePath = path;
    
    try {
        const result = await apiRequest(`/import/browse?path=${encodeURIComponent(_importBrowsePath)}`);
        _importBrowsePath = result.path;
        document.getElementById('import-browse-path').textContent = _importBrowsePath;

        const list = document.getElementById('import-browse-list');
        const scanBtn = document.getElementById('import-scan-btn');
        
        if (!result.entries.length) {
            list.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-secondary);font-size:0.85rem;">Empty directory</div>';
            scanBtn.disabled = true;
            return;
        }

        scanBtn.disabled = false;
        list.innerHTML = result.entries.map(e => {
            const icon = e.type === 'folder' ? '📁' :
                         e.type === 'image' ? '🖼️' :
                         e.type === 'video' ? '🎬' :
                         e.type === 'archive' ? '📦' : '📄';
            const sizeStr = e.type === 'folder' ? '' : formatBytes(e.size);
            const clickAction = e.type === 'folder'
                ? `loadImportBrowse('${escapeHtml(_importBrowsePath + '/' + e.name)}')`
                : '';
            return `<div class="import-browse-item" ${clickAction ? `onclick="${clickAction}"` : ''}>
                <span class="browse-icon">${icon}</span>
                <span class="browse-name">${escapeHtml(e.name)}</span>
                <span class="browse-size">${sizeStr}</span>
            </div>`;
        }).join('');
    } catch (error) {
        document.getElementById('import-browse-list').innerHTML =
            `<div style="padding:1rem;text-align:center;color:var(--danger);font-size:0.85rem;">${escapeHtml(error.message || 'Failed to browse')}</div>`;
        document.getElementById('import-scan-btn').disabled = true;
    }
}

function browseImportParent() {
    const parts = _importBrowsePath.split('/').filter(Boolean);
    if (parts.length <= 1) return; // Already at root
    parts.pop();
    loadImportBrowse('/' + parts.join('/'));
}

async function scanImportCurrentPath() {
    try {
        const result = await apiRequest('/import/scan', {
            method: 'POST',
            body: { path: _importBrowsePath }
        });
        _importSessionId = result.session_id;
        _importSourcePath = result.source_path || _importBrowsePath;
        await analyzeAndShowPreview();
    } catch (error) {
        showNotification(error.message || 'Scan failed', 'error');
    }
}

// --- Analysis & Preview ---
async function analyzeAndShowPreview() {
    if (!_importSessionId) return;

    try {
        _importAnalysis = await apiRequest(`/import/${_importSessionId}/analyze`);
        showImportPreview();
    } catch (error) {
        showNotification(error.message || 'Analysis failed', 'error');
    }
}

async function showImportPreview() {
    const a = _importAnalysis;
    if (!a) return;

    // Fetch jobs list for video linking dropdown
    let _importJobs = [];
    try {
        const resp = await apiRequest('/jobs/');
        _importJobs = resp.jobs || resp;
    } catch (e) { /* non-critical */ }

    // Hide source panels, show preview
    document.getElementById('import-source-panels').style.display = 'none';
    document.getElementById('import-staging-preview').style.display = 'block';

    // Export metadata banner
    const metaBanner = document.getElementById('import-export-metadata');
    if (a.export_metadata) {
        const m = a.export_metadata;
        const details = [];
        if (m.stream_type) details.push(`Type: ${m.stream_type}`);
        if (m.interval_seconds) details.push(`Interval: ${m.interval_seconds}s`);
        if (m.time_window_start && m.time_window_end && m.time_window_start !== m.time_window_end)
            details.push(`Window: ${m.time_window_start} - ${m.time_window_end}`);
        if (m.exported_at) details.push(`Exported: ${formatDateTimeNoSeconds(m.exported_at)}`);
        const tagHtml = m.tags && m.tags.length
            ? '<br>' + m.tags.map(t => `<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:0.75rem;background:${escapeHtml(t.color)}22;color:${escapeHtml(t.color)};border:1px solid ${escapeHtml(t.color)}44;margin:2px 2px 0 0;">${escapeHtml(t.name)}</span>`).join('')
            : '';
        metaBanner.innerHTML = `
            <div class="info-box" style="margin-bottom:0.75rem;border-left:3px solid var(--primary);">
                <strong>ChronoSnap Export Detected</strong><br>
                <small style="color:var(--text-secondary);">
                    Original job: "${escapeHtml(m.name || 'Unknown')}"${details.length ? '<br>' + details.join(' · ') : ''}
                </small>${tagHtml}
            </div>
        `;
        metaBanner.style.display = 'block';
    } else {
        metaBanner.style.display = 'none';
        metaBanner.innerHTML = '';
    }

    // Images
    const imgSection = document.getElementById('import-preview-images');
    if (a.image_count > 0) {
        imgSection.style.display = 'block';
        document.getElementById('import-images-info').innerHTML = `
            <strong>${a.image_count.toLocaleString()} images</strong> · ${formatBytes(a.image_total_size)}<br>
            <small style="color:var(--text-secondary);">
                Range: ${formatDateTimeNoSeconds(a.image_first)} → ${formatDateTimeNoSeconds(a.image_last)}
            </small>
        `;
        // Pre-fill from export metadata, otherwise from folder name
        const nameInput = document.getElementById('import-job-name');
        if (!nameInput.value) {
            if (a.export_metadata && a.export_metadata.name) {
                nameInput.value = a.export_metadata.name;
            } else {
                const srcPath = _importSourcePath || _importBrowsePath || '';
                const parts = srcPath.split('/').filter(Boolean);
                const folderName = parts.length > 1 ? parts[parts.length - 1] : '';
                nameInput.value = folderName || 'Imported';
            }
        }
    } else {
        imgSection.style.display = 'none';
    }

    // Videos
    const vidSection = document.getElementById('import-preview-videos');
    if (a.video_count > 0) {
        vidSection.style.display = 'block';
        const dupes = a.video_duplicates || {};
        const dupeCount = Object.keys(dupes).length;
        const importableCount = a.video_count - dupeCount;
        document.getElementById('import-videos-list').innerHTML = `
            <div class="info-box" style="margin-bottom:0.75rem;">
                <strong>${importableCount} video(s)</strong> · ${formatBytes(a.video_total_size)}
                ${dupeCount ? `<br><small style="color:var(--warning);">⚠ ${dupeCount} duplicate(s) will be skipped</small>` : ''}
            </div>
            ${a.videos.map(v => {
                const baseName = v.file_name.replace(/\.[^.]+$/, '');
                const dupe = dupes[v.file_name];
                const res = v.width && v.height ? `${v.width}×${v.height}` : '';
                const dur = v.duration ? formatDuration(v.duration) : '';
                const thumbUrl = v.has_thumbnail
                    ? `${API_BASE}/import/${_importSessionId}/thumbnail/${encodeURIComponent(v.file_name)}`
                    : '';
                if (dupe) {
                    const matchLabel = dupe.match_type === 'hash' ? 'Exact match' : 'Size + duration match';
                    return `<div class="import-video-card" data-filename="${escapeHtml(v.file_name)}" data-duplicate="true" style="opacity:0.5;pointer-events:none;">
                        ${thumbUrl
                            ? `<img src="${thumbUrl}" alt="" style="width:60px;height:44px;object-fit:cover;border-radius:4px;flex-shrink:0;">`
                            : '<span style="font-size:1.5rem;flex-shrink:0;">🎬</span>'}
                        <div class="video-meta" style="flex:1;min-width:0;">
                            <div style="font-size:0.85rem;padding:0.25rem 0;font-weight:500;">${escapeHtml(baseName)}</div>
                            <small>${[res, dur, formatBytes(v.file_size), v.codec].filter(Boolean).join(' · ')}</small>
                        </div>
                        <span class="duplicate-badge" title="${matchLabel}: '${escapeHtml(dupe.existing_name)}'">⚠ Duplicate</span>
                    </div>`;
                }
                return `<div class="import-video-card" data-filename="${escapeHtml(v.file_name)}">
                    ${thumbUrl
                        ? `<img src="${thumbUrl}" alt="" style="width:60px;height:44px;object-fit:cover;border-radius:4px;flex-shrink:0;">`
                        : '<span style="font-size:1.5rem;flex-shrink:0;">🎬</span>'}
                    <div class="video-meta" style="flex:1;min-width:0;">
                        <input type="text" class="form-control" value="${escapeHtml(baseName)}" data-file="${escapeHtml(v.file_name)}" style="font-size:0.85rem;padding:0.25rem 0.5rem;margin-bottom:0.25rem;">
                        <select class="form-control import-video-job" data-file="${escapeHtml(v.file_name)}" style="font-size:0.75rem;padding:0.15rem 0.3rem;margin-bottom:0.25rem;">
                            <option value="">No job (Imported)</option>
                            ${_importJobs.map(j => `<option value="${j.id}">${escapeHtml(j.name)}</option>`).join('')}
                        </select>
                        <small>${[res, dur, formatBytes(v.file_size), v.codec].filter(Boolean).join(' · ')}</small>
                    </div>
                    <button class="btn-icon" onclick="removeImportVideo(this)" title="Remove" style="flex-shrink:0;color:var(--danger);padding:0.25rem;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>`;
            }).join('')}
        `;
    } else {
        vidSection.style.display = 'none';
    }

    // Errors
    const errSection = document.getElementById('import-preview-errors');
    if (a.error_count > 0) {
        errSection.style.display = 'block';
        document.getElementById('import-errors-info').innerHTML = `
            <strong>${a.error_count} skipped</strong><br>
            <small style="color:var(--text-secondary);">
                ${a.errors.slice(0, 5).map(e => `${escapeHtml(e.file_name)}: ${escapeHtml(e.reason)}`).join('<br>')}
                ${a.error_count > 5 ? `<br>...and ${a.error_count - 5} more` : ''}
            </small>
        `;
    } else {
        errSection.style.display = 'none';
    }

    // Disable execute if nothing importable
    const hasImages = a.image_count > 0;
    const hasVideos = document.querySelectorAll('.import-video-card:not([data-duplicate])').length > 0;
    document.getElementById('import-execute-btn').disabled = (!hasImages && !hasVideos);
}

function removeImportVideo(btn) {
    const card = btn.closest('.import-video-card');
    if (!card) return;
    card.remove();
    
    // Update video count display (exclude duplicates)
    const remaining = document.querySelectorAll('.import-video-card:not([data-duplicate])').length;
    const vidSection = document.getElementById('import-preview-videos');
    if (remaining === 0) {
        // Hide only if no duplicates either
        const dupeCards = document.querySelectorAll('.import-video-card[data-duplicate]').length;
        if (dupeCards === 0) vidSection.style.display = 'none';
        else {
            const infoBox = vidSection.querySelector('.info-box');
            if (infoBox) infoBox.innerHTML = `<strong>0 video(s)</strong>`;
        }
    } else {
        const infoBox = vidSection.querySelector('.info-box');
        if (infoBox) infoBox.innerHTML = `<strong>${remaining} video(s)</strong>`;
    }
    
    // Update execute button state
    const hasImages = _importAnalysis && _importAnalysis.image_count > 0;
    document.getElementById('import-execute-btn').disabled = (!hasImages && remaining === 0);
}

// --- Execute import ---
async function executeImport() {
    if (!_importSessionId || !_importAnalysis) return;

    const body = {};

    // Image config
    if (_importAnalysis.image_count > 0) {
        const jobName = document.getElementById('import-job-name').value.trim();
        if (!jobName) { showNotification('Enter a job name for images', 'error'); return; }
        body.image_job_name = jobName;
        // Include tags from export metadata if present
        if (_importAnalysis.export_metadata && _importAnalysis.export_metadata.tags) {
            body.image_tags = _importAnalysis.export_metadata.tags;
        }
    }

    // Video configs — only include remaining (non-removed) cards
    const videoCards = document.querySelectorAll('.import-video-card:not([data-duplicate]) input[data-file]');
    if (videoCards.length > 0) {
        body.videos = Array.from(videoCards).map(input => {
            const card = input.closest('.import-video-card');
            const jobSelect = card ? card.querySelector('select.import-video-job') : null;
            const config = {
                file_name: input.dataset.file,
                name: input.value.trim() || input.dataset.file.replace(/\.[^.]+$/, ''),
            };
            if (jobSelect && jobSelect.value) {
                config.job_id = parseInt(jobSelect.value);
            }
            return config;
        });
    }

    try {
        document.getElementById('import-execute-btn').disabled = true;
        document.getElementById('import-execute-btn').textContent = 'Importing...';

        const result = await apiRequest(`/import/${_importSessionId}/execute`, {
            method: 'POST',
            body
        });

        const parts = [];
        if (result.images) parts.push(`${result.images.imported_count} images as "${result.images.job_name}"`);
        if (result.videos && result.videos.length) parts.push(`${result.videos.length} video(s)`);
        
        closeModal('import-modal');
        _importSessionId = null; // Already cleaned by execute
        showNotification(`Imported ${parts.join(' and ')}`, 'success');
        refreshEventsSoon();
        loadJobs();
        loadVideos();
    } catch (error) {
        showNotification(error.message || 'Import failed', 'error');
        document.getElementById('import-execute-btn').disabled = false;
        document.getElementById('import-execute-btn').textContent = 'Import';
    }
}

async function loadNamingPattern() {
    try {
        const result = await apiRequest('/settings/naming-pattern');
        document.getElementById('default-naming-pattern').value = result.naming_pattern;
    } catch (e) {
        document.getElementById('default-naming-pattern').value = '{job_name}_{count}_{timestamp}';
    }
}

async function saveNamingPattern() {
    const input = document.getElementById('default-naming-pattern');
    const pattern = input.value.trim();
    if (!pattern) {
        showNotification('Naming pattern must not be empty', 'error');
        return;
    }
    try {
        await apiRequest('/settings/naming-pattern', { method: 'PUT', body: { naming_pattern: pattern } });
        showNotification('Default naming pattern updated', 'success');
    } catch (error) {
        showNotification(error.message || 'Failed to update naming pattern', 'error');
    }
}

function updateNamingPreview() {
    const pattern = document.getElementById('naming_pattern').value || '{job_name}_{count}_{timestamp}';
    const jobName = document.getElementById('job_name')?.value || 'MyJob';
    const now = new Date();
    const ts = now.getFullYear() + (now.getMonth()+1+'').padStart(2,'0') + (now.getDate()+'').padStart(2,'0')
        + '_' + (now.getHours()+'').padStart(2,'0') + (now.getMinutes()+'').padStart(2,'0') + (now.getSeconds()+'').padStart(2,'0');
    const example = pattern
        .replace('{job_name}', jobName)
        .replace('{count}', '000001')
        .replace(/\{num(?::0?(\d+)d)?\}/, '000001')
        .replace('{timestamp}', ts);
    const el = document.getElementById('naming-preview');
    if (el) el.textContent = `Example: ${example}.jpg`;
}

async function showCreateJobModal() {
    // Reset the form to clear any previous values
    document.getElementById('create-job-form').reset();
    
    // Reset source type to network
    _createSourceType = 'network';
    setSourceType('network');
    
    // Clear test results and estimates
    document.getElementById('test-result').innerHTML = '';
    document.getElementById('device-test-result').innerHTML = '';
    document.getElementById('duration-estimate').innerHTML = '';
    
    // Set default datetime to now
    setDefaultStartTime();
    
    // Load default naming pattern from settings
    try {
        const resp = await apiRequest('/settings/naming-pattern');
        document.getElementById('naming_pattern').value = resp.naming_pattern || '{job_name}_{count}_{timestamp}';
    } catch {
        document.getElementById('naming_pattern').value = '{job_name}_{count}_{timestamp}';
    }
    updateNamingPreview();
    
    // Set initial min for end date
    updateEndDateMin();
    
    // Reset time window
    document.getElementById('time_window_enabled').checked = false;
    document.getElementById('time-window-fields').classList.add('disabled');
    
    // Reset auto-build
    document.getElementById('auto_build_enabled').checked = false;
    document.getElementById('auto-build-fields').classList.add('disabled');
    
    showModal('create-job-modal');
    
    // Render tag picker
    renderTagPicker('create-job-tags');
    
    // Trigger initial duration estimate with default values
    setTimeout(() => {
        updateDurationEstimate();
    }, 100);
}

async function exportJob(jobId, jobName) {
    try {
        const estimate = await apiRequest(`/jobs/${jobId}/export/estimate`);
        const totalSize = formatBytes(estimate.total_size);
        const details = [];
        if (estimate.capture_count > 0) details.push(`${estimate.capture_count} captures (${formatBytes(estimate.capture_size)})`);
        if (estimate.video_count > 0) details.push(`${estimate.video_count} video(s) (${formatBytes(estimate.video_size)})`);
        
        if (!details.length) {
            showNotification('No files to export for this job', 'error');
            return;
        }
        
        confirmAction(
            `Export "${jobName}"? ${details.join(', ')} — estimated ${totalSize}`,
            async () => {
                showNotification('Building export...', 'info');
                try {
                    const response = await fetch(`${API_BASE}/jobs/${jobId}/export`, {
                        method: 'POST',
                    });
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({ detail: 'Export failed' }));
                        throw new Error(err.detail || 'Export failed');
                    }
                    const blob = await response.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = response.headers.get('content-disposition')?.match(/filename="(.+)"/)?.[1] || `${jobId}_export.zip`;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                    showNotification('Export downloaded', 'success');
                    refreshEventsSoon();
                } catch (error) {
                    showNotification(error.message || 'Export failed', 'error');
                }
            },
            { closeModalId: null }
        );
    } catch (error) {
        showNotification(error.message || 'Failed to estimate export', 'error');
    }
}

async function duplicateJob(jobId) {
    try {
        const job = await apiRequest(`/jobs/${jobId}`);
        
        // Open create modal and pre-fill with job data
        document.getElementById('create-job-form').reset();
        document.getElementById('test-result').innerHTML = '';
        document.getElementById('device-test-result').innerHTML = '';
        document.getElementById('duration-estimate').innerHTML = '';
        
        // Set source type based on job
        if (job.stream_type === 'device') {
            setSourceType('device');
            const deviceSelect = document.getElementById('device_path');
            deviceSelect.innerHTML = `<option value="${escapeHtml(job.url)}" selected>${escapeHtml(job.url)}</option>`;
            refreshDevices('device_path').then(() => {
                deviceSelect.value = job.url;
            });
        } else {
            setSourceType('network');
            document.getElementById('job_url').value = job.url;
        }
        document.getElementById('warning_threshold').value = job.warning_threshold || 3;
        
        // Pre-fill fields from source job
        document.getElementById('job_name').value = `${job.name} (Copy)`;
        document.getElementById('interval_seconds').value = job.interval_seconds;
        document.getElementById('framerate').value = job.framerate || 30;
        document.getElementById('naming_pattern').value = job.naming_pattern || '{job_name}_{count}_{timestamp}';
        document.getElementById('capture_quality').value = job.capture_quality || 'maximum';
        // Populate resolution dropdown from source dimensions before setting value
        if (job.source_width && job.source_height) {
            const options = _generateResolutionOptions(job.source_width, job.source_height, job.capture_resolution || 'native');
            _populateResolutionDropdown('capture_resolution', options, job.capture_resolution || 'native');
            _nativeDimensions['capture_resolution'] = { w: job.source_width, h: job.source_height };
        } else {
            document.getElementById('capture_resolution').value = job.capture_resolution || 'native';
        }
        updateNamingPreview();
        
        // Set start to now
        setDefaultStartTime();
        
        // Copy end date if it exists
        if (job.end_datetime) {
            document.getElementById('end_datetime').value = isoToDatetimeLocal(job.end_datetime);
        }
        
        // Copy time window settings
        const twEnabled = document.getElementById('time_window_enabled');
        const twFields = document.getElementById('time-window-fields');
        if (job.time_window_enabled) {
            twEnabled.checked = true;
            twFields.classList.remove('disabled');
            if (job.time_window_start) {
                document.getElementById('time_window_start_time').value = job.time_window_start;
                document.getElementById('time_window_start_time').dispatchEvent(new Event('change'));
            }
            if (job.time_window_end) {
                document.getElementById('time_window_end_time').value = job.time_window_end;
                document.getElementById('time_window_end_time').dispatchEvent(new Event('change'));
            }
        } else {
            twEnabled.checked = false;
            twFields.classList.add('disabled');
        }
        
        // Copy auto-build settings
        const abEnabled = document.getElementById('auto_build_enabled');
        const abFields = document.getElementById('auto-build-fields');
        if (job.auto_build_enabled) {
            abEnabled.checked = true;
            abFields.classList.remove('disabled');
            document.getElementById('auto_build_interval_hours').value = job.auto_build_interval_hours || 168;
            document.getElementById('auto_build_fps').value = job.auto_build_fps || 30;
            document.getElementById('auto_build_quality').value = job.auto_build_quality || 'medium';
            document.getElementById('auto_build_resolution').value = job.auto_build_resolution || '1920x1080';
            
            // Copy text overlay settings
            if (job.auto_build_text_overlay) {
                try {
                    const overlayConfig = JSON.parse(job.auto_build_text_overlay);
                    // Mount widget first, then populate
                    initCreateJobOverlay();
                    writeOverlayConfig('create-ab', overlayConfig);
                } catch (e) { /* ignore invalid overlay config */ }
            }
        } else {
            abEnabled.checked = false;
            abFields.classList.add('disabled');
        }
        
        updateEndDateMin();
        showModal('create-job-modal');
        
        // Pre-select tags from source job
        renderTagPicker('create-job-tags', (job.tags || []).map(t => t.id));
        
        setTimeout(() => updateDurationEstimate(), 100);
    } catch (error) {
        console.error('Failed to duplicate job:', error);
        showNotification('Failed to duplicate job', 'error');
    }
}

// Update minimum end date based on start date and interval
function updateEndDateMin() {
    const startInput = document.getElementById('start_datetime');
    const endInput = document.getElementById('end_datetime');
    const intervalInput = document.getElementById('interval_seconds');
    
    if (startInput && startInput.value && intervalInput) {
        const intervalSeconds = parseInt(intervalInput.value) || 60;
        // Min validation is handled by validation logic in createJob()
    }
}

function getStreamHost(url) {
    // Extract protocol and host/domain from URL (e.g., http://example.com:8080)
    // This removes the path, query params, and fragments
    try {
        const urlObj = new URL(url);
        return `${urlObj.protocol}//${urlObj.host}`;
    } catch (e) {
        // If URL parsing fails, return first 30 chars
        return url.length > 30 ? url.substring(0, 30) + '...' : url;
    }
}

// ===== DateTime Utility Functions =====
// 
// TIMEZONE APPROACH:
// - Backend stores datetimes in ISO format with timezone (e.g., "2025-12-22T14:30:00-06:00")
// - Frontend custom pickers work in user's local browser time
// - When sending to backend: browser Date objects automatically include timezone
// - When displaying from backend: formatDateTime() parses ISO string and displays in browser's local time
// - This ensures timestamps are always shown in the user's local time while maintaining timezone info
//
// DATETIME FORMAT:
// - Always use 24-hour format for display (00:00 - 23:59)
// - Custom pickers use dropdowns (date picker + hour/minute selects) to avoid locale issues
// - Hidden inputs store values in ISO format for API submission

function toUTCString(localDateTimeString) {
    // Convert datetime-local format (YYYY-MM-DDTHH:mm) to ISO string format
    // Note: Backend stores in local time, not UTC, so we format as local ISO
    // Seconds are set to :00 for start times (schedule grid alignment)
    // and :59 for end times to be more inclusive
    if (!localDateTimeString) return null;
    const date = new Date(localDateTimeString);
    // Format as ISO but without timezone info to match backend's datetime.now() format
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}`;
}

function toISOStringForQuery(localDateTimeString, isEndTime) {
    // Convert datetime-local format to ISO string for database queries
    // For end times, add 59 seconds to include the entire minute
    if (!localDateTimeString) return null;
    const date = new Date(localDateTimeString);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = isEndTime ? '59' : '00';  // Use 59 for end times to be inclusive
    return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}`;
}

function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    const use12 = getTimeFormat() === '12';
    return date.toLocaleString(use12 ? 'en-US' : 'en-CA', { 
        year: 'numeric', 
        month: '2-digit', 
        day: '2-digit',
        hour: '2-digit', 
        minute: '2-digit', 
        second: '2-digit',
        hour12: use12 
    });
}

function formatDateTimeNoSeconds(isoString) {
    if (!isoString) return 'N/A';
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    const use12 = getTimeFormat() === '12';
    return date.toLocaleString(use12 ? 'en-US' : 'en-CA', { 
        year: 'numeric', 
        month: '2-digit', 
        day: '2-digit',
        hour: '2-digit', 
        minute: '2-digit', 
        hour12: use12 
    });
}

/** Convert a datetime-local input value to ISO with local timezone offset */
function datetimeLocalToISO(value) {
    if (!value) return '';
    const dt = new Date(value);
    if (isNaN(dt.getTime())) return value;
    const pad = n => String(n).padStart(2, '0');
    const offset = -dt.getTimezoneOffset();
    const sign = offset >= 0 ? '+' : '-';
    const offH = pad(Math.floor(Math.abs(offset) / 60));
    const offM = pad(Math.abs(offset) % 60);
    return `${dt.getFullYear()}-${pad(dt.getMonth()+1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}:00${sign}${offH}:${offM}`;
}

/** Convert an ISO datetime string to datetime-local input format */
function isoToDatetimeLocal(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDuration(seconds) {
    if (!seconds) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

// Universal Duration Estimator
// Can be used for job creation, job editing, and anywhere else duration estimates are needed
function calculateAndDisplayDuration(options) {
    const {
        startDateId,
        endDateId,
        intervalId,
        framerateId,
        timeWindowEnabledId,
        timeWindowStartId,
        timeWindowEndId,
        displayElementId
    } = options;
    
    const startDate = document.getElementById(startDateId)?.value;
    const endDate = document.getElementById(endDateId)?.value;
    const interval = parseInt(document.getElementById(intervalId)?.value);
    const framerate = parseInt(document.getElementById(framerateId)?.value) || 30;
    const timeWindowEnabled = document.getElementById(timeWindowEnabledId)?.checked || false;
    const timeWindowStart = document.getElementById(timeWindowStartId)?.value;
    const timeWindowEnd = document.getElementById(timeWindowEndId)?.value;
    const displayElement = document.getElementById(displayElementId);
    
    if (!displayElement || !startDate || !interval) {
        if (displayElement) displayElement.innerHTML = '';
        return;
    }
    
    // Helper function to calculate captures with time window
    function calculateCaptures(durationSeconds) {
        if (!timeWindowEnabled || !timeWindowStart || !timeWindowEnd) {
            return Math.floor(durationSeconds / interval);
        }
        
        if (endDate) {
            const jobStart = new Date(startDate);
            const jobEnd = new Date(endDate);
            const [startHour, startMin] = timeWindowStart.split(':').map(Number);
            const [endHour, endMin] = timeWindowEnd.split(':').map(Number);
            
            const isSameTime = startHour === endHour && startMin === endMin;
            const windowSpansMidnight = startHour > endHour || (startHour === endHour && startMin > endMin);
            
            let totalCaptures = 0;
            let current = new Date(jobStart);
            const maxIterations = 1000;
            let iterations = 0;
            
            while (current < jobEnd && iterations < maxIterations) {
                iterations++;
                const currentDate = new Date(current);
                const currentDay = new Date(currentDate.getFullYear(), currentDate.getMonth(), currentDate.getDate());
                
                let dayWindowStart = new Date(currentDay);
                dayWindowStart.setHours(startHour, startMin, 0, 0);
                
                let dayWindowEnd = new Date(currentDay);
                dayWindowEnd.setHours(endHour, endMin, 0, 0);
                
                if (isSameTime) {
                    dayWindowEnd = new Date(dayWindowStart);
                    dayWindowEnd.setMinutes(dayWindowEnd.getMinutes() + 1);
                } else if (windowSpansMidnight) {
                    dayWindowEnd.setDate(dayWindowEnd.getDate() + 1);
                }
                
                // If current time is already past today's window end, move to next day's window start
                if (current >= dayWindowEnd) {
                    // Advance to next day at the window start time
                    const nextDay = new Date(currentDay);
                    nextDay.setDate(nextDay.getDate() + 1);
                    nextDay.setHours(startHour, startMin, 0, 0);
                    current = nextDay;
                    continue;
                }
                
                // If current time is before today's window start, move to window start
                if (current < dayWindowStart) {
                    current = new Date(dayWindowStart);
                }
                
                // Calculate the actual capture window for this day
                // Must be within both the daily time window AND the job duration
                const effectiveStart = current;
                const effectiveEnd = Math.min(dayWindowEnd, jobEnd);
                const windowDuration = effectiveEnd - effectiveStart;
                
                if (windowDuration > 0) {
                    totalCaptures += Math.floor((windowDuration / 1000) / interval);
                }
                
                // Move to the next day's window start
                const nextDay = new Date(dayWindowEnd);
                if (!windowSpansMidnight) {
                    nextDay.setDate(nextDay.getDate() + 1);
                    nextDay.setHours(startHour, startMin, 0, 0);
                }
                current = nextDay;
            }
            
            return totalCaptures;
        } else {
            const [startHour, startMin] = timeWindowStart.split(':').map(Number);
            const [endHour, endMin] = timeWindowEnd.split(':').map(Number);
            
            let windowSeconds;
            if (startHour === endHour && startMin === endMin) {
                windowSeconds = 60;
            } else if (startHour > endHour || (startHour === endHour && startMin > endMin)) {
                const minutesUntilMidnight = (23 - startHour) * 60 + (60 - startMin);
                const minutesAfterMidnight = endHour * 60 + endMin;
                windowSeconds = (minutesUntilMidnight + minutesAfterMidnight) * 60;
            } else {
                const totalMinutes = (endHour - startHour) * 60 + (endMin - startMin);
                windowSeconds = totalMinutes * 60;
            }
            
            const capturesPerDay = Math.floor(windowSeconds / interval);
            return Math.floor((durationSeconds / 86400) * capturesPerDay);
        }
    }
    
    const windowNote = timeWindowEnabled && timeWindowStart && timeWindowEnd
        ? ` <small style="color: var(--text-secondary);">(${timeWindowStart}-${timeWindowEnd} window)</small>`
        : '';
    
    if (endDate) {
        const start = new Date(startDate);
        const end = new Date(endDate);
        const durationSeconds = (end - start) / 1000;
        const captures = calculateCaptures(durationSeconds);
        
        displayElement.innerHTML = `
            <h4>Estimated Video Duration${windowNote}</h4>
            <div class="duration-grid">
                <div class="duration-item">
                    <div class="duration-fps">${captures.toLocaleString()} captures @ ${framerate} FPS</div>
                    <div class="duration-time">${formatDuration(captures / framerate)}</div>
                </div>
            </div>
        `;
    } else {
        const durations = [
            { label: '1 Hour', seconds: 3600 },
            { label: '1 Day', seconds: 86400 },
            { label: '1 Week', seconds: 604800 },
            { label: '1 Month', seconds: 2592000 }
        ];
        
        let boxes = durations.map(dur => {
            const captures = calculateCaptures(dur.seconds);
            return `
                <div class="duration-item">
                    <div class="duration-label">${dur.label}</div>
                    <div class="duration-time">${formatDuration(captures / framerate)}</div>
                    <div class="duration-fps">${captures.toLocaleString()} captures</div>
                </div>`;
        }).join('');

        displayElement.innerHTML = `
            <h4>Est. Duration @ ${framerate} FPS (Ongoing)${windowNote}</h4>
            <div class="duration-grid">${boxes}</div>
        `;
    }
}

// Job Creation Duration Estimator - calls universal calculator
function updateDurationEstimate() {
    calculateAndDisplayDuration({
        startDateId: 'start_datetime',
        endDateId: 'end_datetime',
        intervalId: 'interval_seconds',
        framerateId: 'framerate',
        timeWindowEnabledId: 'time_window_enabled',
        timeWindowStartId: 'time_window_start',
        timeWindowEndId: 'time_window_end',
        displayElementId: 'duration-estimate'
    });
}

// Job Edit Duration Estimator - calls universal calculator
function updateEditDurationEstimate() {
    calculateAndDisplayDuration({
        startDateId: 'edit_start_datetime',
        endDateId: 'edit_end_datetime',
        intervalId: 'edit_interval_seconds',
        framerateId: 'edit_framerate',
        timeWindowEnabledId: 'edit_time_window_enabled',
        timeWindowStartId: 'edit_time_window_start',
        timeWindowEndId: 'edit_time_window_end',
        displayElementId: 'edit-duration-estimate'
    });
}

// Close modals on outside click — only if mousedown AND mouseup both on backdrop
let _modalMouseDownTarget = null;
window.addEventListener('mousedown', function(e) {
    _modalMouseDownTarget = e.target;
}, true);

window.addEventListener('mouseup', function(e) {
    if (_modalMouseDownTarget &&
        _modalMouseDownTarget === e.target &&
        e.target.classList.contains('modal') &&
        e.target.id !== 'confirm-modal') {
        e.target.classList.remove('active');
    }
    _modalMouseDownTarget = null;
}, true);

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    refreshIntervals.forEach(interval => clearInterval(interval));
    if (videoRefreshInterval) {
        clearInterval(videoRefreshInterval);
        videoRefreshInterval = null;
    }
});

// ===== Maintenance Functions =====

let maintenanceData = null;

async function manualCapture(jobId, jobName) {
    confirmAction(
        `Take a manual snapshot for "${jobName}"? This will not affect the scheduled capture timing.`,
        async () => {
            showNotification('Capturing snapshot...', 'info');
            
            try {
                await apiRequest(`/jobs/${jobId}/capture`, { method: 'POST' });
                
                showNotification('Snapshot captured successfully!', 'success');
                
                // Refresh job details to show updated capture count
                await loadJobDetail(jobId);
            } catch (error) {
                console.error('Manual capture failed:', error);
                showNotification(`Snapshot failed: ${error.message}`, 'error');
            }
        }
    );
}

async function performMaintenanceScan(jobId, jobName) {
    const modal = document.getElementById('maintenance-modal');
    const title = document.getElementById('maintenance-title');
    const content = document.getElementById('maintenance-content');
    
    // Update modal title with job name
    title.textContent = `${jobName} - Capture Sync`;
    
    // Show scanning message
    content.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <div style="font-size: 2rem; margin-bottom: 1rem;">🔍</div>
            <p>Scanning database records and disk files for "${escapeHtml(jobName)}"...</p>
            <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                This may take a moment...
            </p>
        </div>
    `;
    showModal('maintenance-modal');
    
    try {
        maintenanceData = await apiRequest(`/jobs/${jobId}/maintenance/scan`, { method: 'POST' });
        displayMaintenanceResults(jobId, jobName);
        
    } catch (error) {
        console.error('Maintenance scan failed:', error);
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <div style="font-size: 2rem; margin-bottom: 1rem;">❌</div>
                <p style="color: var(--danger);">Failed to scan captures</p>
                <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    ${escapeHtml(error.message)}
                </p>
                <button class="btn btn-secondary mt-lg" onclick="closeMaintenance()">Close</button>
            </div>
        `;
    }
}

function displayMaintenanceResults(jobId, jobName) {
    const content = document.getElementById('maintenance-content');
    const data = maintenanceData;
    
    if (data.missing_count === 0 && (data.orphaned_count === 0 || !data.orphaned_count)) {
        // No issues found
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <h3 style="margin-bottom: 0.5rem;">Sync Results</h3>
                <p style="color: var(--text-secondary);">
                    All ${data.total_captures} database records match files on disk. Everything is in sync.
                </p>
                <button class="btn btn-primary mt-xl" onclick="closeMaintenance()">Close</button>
            </div>
        `;
    } else {
        // Issues found - show details
        const missingList = data.missing_files && data.missing_files.length > 0 ? data.missing_files.map(file => `
            <div style="padding: 0.4rem 0.5rem; background: var(--card-bg); border-radius: 3px; margin-bottom: 0.25rem; border-left: 2px solid var(--danger);">
                <div style="font-size: 0.8rem; color: var(--text-primary); word-break: break-all; font-family: monospace; line-height: 1.3;">
                    ${escapeHtml(file.file_path)}
                </div>
                <div style="font-size: 0.7rem; color: var(--text-secondary); margin-top: 0.15rem; line-height: 1.2;">
                    ${formatDateTime(file.captured_at)} • ${formatBytes(file.file_size)}
                </div>
            </div>
        `).join('') : '';
        
        content.innerHTML = `
            <div>
                <div style="text-align: center; margin-bottom: 1.5rem;">
                    <h3 style="margin-bottom: 0.5rem;">Sync Results</h3>
                </div>
                
                <div style="background: var(--bg-secondary); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem;">
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem;">
                        <div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary);">DB Records</div>
                            <div style="font-size: 1.5rem; font-weight: bold;">${data.total_captures}</div>
                        </div>
                        <div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary);">Records Without Files</div>
                            <div style="font-size: 1.5rem; font-weight: bold; color: var(--danger);">${data.missing_count}</div>
                        </div>
                        <div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary);">Files Without Records</div>
                            <div style="font-size: 1.5rem; font-weight: bold; color: var(--warning-color);">${data.orphaned_count || 0}</div>
                        </div>
                        <div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary);">In Sync</div>
                            <div style="font-size: 1.5rem; font-weight: bold; color: var(--success);">${data.existing_count}</div>
                        </div>
                    </div>
                </div>
                
                ${data.missing_count > 0 ? `
                <div style="margin-bottom: 1.5rem;">
                    <h4 style="margin-bottom: 0.5rem;">Records Without Files (${data.missing_count}):</h4>
                    <p style="color: var(--text-secondary); font-size: 0.8rem; margin-bottom: 0.5rem;">
                        These database records reference files that were not found on disk.
                    </p>
                    <div style="max-height: 300px; overflow-y: auto; padding: 0.4rem; background: var(--surface-color); border: 1px solid var(--border-color); border-radius: 6px;">
                        ${missingList}
                    </div>
                </div>
                
                <div style="background: var(--surface-hover); border: 1px solid var(--warning-color); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem;">
                    <strong style="color: var(--warning-color);">⚠ Caution</strong>
                    <p style="color: var(--text-secondary); margin-top: 0.5rem; margin-bottom: 0; font-size: 0.875rem;">
                        Submitting will <strong>permanently remove</strong> these database records. Before proceeding, verify that the files are truly 
                        gone and not simply inaccessible due to a changed volume mount, unmounted drive, or moved directory. 
                        This action cannot be undone.
                    </p>
                </div>
                ` : ''}
                
                ${data.orphaned_count > 0 ? `
                <div style="margin-bottom: 1.5rem; ${data.missing_count > 0 ? 'padding-top: 1rem; border-top: 2px solid var(--border-color);' : ''}">
                    <h4 style="margin-bottom: 0.5rem;">Files Without Records (${data.orphaned_count}):</h4>
                    <p style="color: var(--text-secondary); font-size: 0.8rem; margin-bottom: 0.5rem;">
                        These files exist on disk but have no matching database record.
                    </p>
                    <div style="max-height: 300px; overflow-y: auto; padding: 0.4rem; background: var(--surface-color); border: 1px solid var(--border-color); border-radius: 6px;">
                        ${(data.orphaned_files && data.orphaned_files.length > 0) ? data.orphaned_files.map(f => `
                            <div style="padding: 0.4rem 0.5rem; background: var(--card-bg); border-radius: 3px; margin-bottom: 0.25rem; border-left: 2px solid var(--warning-color);">
                                <div style="font-size: 0.8rem; color: var(--text-primary); word-break: break-all; font-family: monospace; line-height: 1.3;">
                                    ${escapeHtml(f.file_path)}
                                </div>
                                <div style="font-size: 0.7rem; color: var(--text-secondary); margin-top: 0.15rem; line-height: 1.2;">
                                    ${formatDateTime(f.captured_at)} • ${formatBytes(f.file_size)}
                                </div>
                            </div>
                        `).join('') : '<div style="padding: 1rem; text-align: center; color: var(--text-secondary);">No orphaned files data</div>'}
                    </div>
                </div>
                
                <div style="background: var(--surface-hover); border: 1px solid var(--primary-color); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem;">
                    <strong style="color: var(--primary-color);">Info</strong>
                    <p style="color: var(--text-secondary); margin-top: 0.5rem; margin-bottom: 0; font-size: 0.875rem;">
                        Submitting will create database records for these files so they appear in the capture history. 
                        Timestamps will be extracted from filenames, EXIF data, or file modification times. This action cannot be undone.
                    </p>
                </div>
                ` : ''}
                
                ${data.missing_count > 0 || data.orphaned_count > 0 ? `
                <div class="modal-actions">
                    <button class="btn btn-secondary" onclick="closeMaintenance()">Cancel</button>
                    <button class="btn btn-primary" onclick="confirmMaintenanceSubmit(${jobId}, '${escapeHtml(jobName)}')">
                        Submit
                    </button>
                </div>
                ` : `
                <div class="modal-actions">
                    <button class="btn btn-primary" onclick="closeMaintenance()">Close</button>
                </div>
                `}
            </div>
        `;
    }
}

function confirmMaintenanceSubmit(jobId, jobName) {
    const data = maintenanceData;
    const parts = [];
    if (data.missing_count > 0) parts.push(`remove ${data.missing_count} database record(s) with no matching file`);
    if (data.orphaned_count > 0) parts.push(`import ${data.orphaned_count} file(s) with no database record`);
    const summary = parts.join(' and ');
    
    confirmAction(
        `This will ${summary}. This cannot be undone.`,
        () => performMaintenanceActions(jobId, jobName)
    );
}

async function performMaintenanceActions(jobId, jobName) {
    const content = document.getElementById('maintenance-content');
    const data = maintenanceData;
    
    content.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <p>Applying sync changes...</p>
        </div>
    `;
    
    try {
        let cleanupResult = null;
        let importResult = null;
        
        // Perform cleanup if there are missing files
        if (data.missing_count > 0) {
            const captureIds = data.missing_files.map(f => f.id);
            cleanupResult = await apiRequest(`/jobs/${jobId}/maintenance/cleanup`, {
                method: 'POST',
                body: { capture_ids: captureIds }
            });
        }
        
        // Perform import if there are orphaned files
        if (data.orphaned_count > 0) {
            importResult = await apiRequest(`/jobs/${jobId}/maintenance/import`, {
                method: 'POST',
                body: { orphaned_files: data.orphaned_files }
            });
        }
        
        // Show success with combined results
        let resultHtml = `
            <div style="text-align: center; padding: 2rem;">
                <h3 style="margin-bottom: 0.5rem;">Maintenance Complete</h3>
                <p style="color: var(--text-secondary); margin-bottom: 1rem;">Successfully processed all actions</p>
                <div style="background: var(--bg-secondary); padding: 1rem; border-radius: 8px; display: inline-block; text-align: left;">
        `;
        
        if (cleanupResult) {
            resultHtml += `
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Records removed:</strong> ${cleanupResult.deleted_count}
                    </div>
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Size recovered:</strong> ${formatBytes(cleanupResult.size_recovered)}
                    </div>
            `;
        }
        
        if (importResult) {
            resultHtml += `
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Files imported:</strong> ${importResult.imported_count}
                    </div>
            `;
        }
        
        // Use the final result for capture count and storage
        const finalResult = importResult || cleanupResult;
        resultHtml += `
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Total captures:</strong> ${finalResult.new_capture_count}
                    </div>
                    <div>
                        <strong>Total storage:</strong> ${formatBytes(finalResult.new_storage_size)}
                    </div>
                </div>
                <div style="margin-top: 1.5rem;">
                    <button class="btn btn-primary" onclick="closeMaintenance(); loadJobs()">Close</button>
                </div>
            </div>
        `;
        
        content.innerHTML = resultHtml;
        showNotification(`Maintenance completed successfully for "${jobName}"`, 'success');
        
    } catch (error) {
        console.error('Maintenance failed:', error);
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <p style="color: var(--danger);">Failed to complete maintenance</p>
                <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    ${escapeHtml(error.message)}
                </p>
                <button class="btn btn-secondary mt-lg" onclick="closeMaintenance()">Close</button>
            </div>
        `;
        showNotification('Maintenance failed', 'error');
    }
}

async function performMaintenanceImport(jobId, jobName) {
    const content = document.getElementById('maintenance-content');
    
    content.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <div style="font-size: 2rem; margin-bottom: 1rem;">📥</div>
            <p>Importing orphaned files...</p>
        </div>
    `;
    
    try {
        const result = await apiRequest(`/jobs/${jobId}/maintenance/import`, {
            method: 'POST',
            body: { orphaned_files: maintenanceData.orphaned_files }
        });
        
        // Show success
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <h3 style="margin-bottom: 0.5rem;">Import Complete</h3>
                <p style="color: var(--text-secondary); margin-bottom: 1rem;">
                    Imported ${result.imported_count} file(s) into the database
                </p>
                <div style="background: var(--bg-secondary); padding: 1rem; border-radius: 8px; display: inline-block; text-align: left;">
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Files imported:</strong> ${result.imported_count}
                    </div>
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Total captures:</strong> ${result.new_capture_count}
                    </div>
                    <div>
                        <strong>Total storage:</strong> ${formatBytes(result.new_storage_size)}
                    </div>
                </div>
                <div style="margin-top: 1.5rem;">
                    <button class="btn btn-primary" onclick="closeMaintenance(); loadJobs()">Close</button>
                </div>
            </div>
        `;
        
        showNotification(`Successfully imported ${result.imported_count} file(s) for "${jobName}"`, 'success');
        
    } catch (error) {
        console.error('Maintenance import failed:', error);
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <p style="color: var(--danger);">Failed to import files</p>
                <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    ${escapeHtml(error.message)}
                </p>
                <button class="btn btn-secondary mt-lg" onclick="closeMaintenance()">Close</button>
            </div>
        `;
        showNotification('Maintenance import failed', 'error');
    }
}

async function performMaintenanceCleanup(jobId, jobName) {
    const content = document.getElementById('maintenance-content');
    
    // Show cleaning message
    content.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <p>Cleaning up database records...</p>
        </div>
    `;
    
    try {
        const captureIds = maintenanceData.missing_files.map(f => f.id);
        
        const result = await apiRequest(`/jobs/${jobId}/maintenance/cleanup`, {
            method: 'POST',
            body: { capture_ids: captureIds }
        });
        
        // Show success
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <h3 style="margin-bottom: 0.5rem;">Cleanup Complete</h3>
                <p style="color: var(--text-secondary); margin-bottom: 1rem;">
                    Removed ${result.deleted_count} database record(s)
                </p>
                <div style="background: var(--bg-secondary); padding: 1rem; border-radius: 8px; display: inline-block; text-align: left;">
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Size recovered:</strong> ${formatBytes(result.size_recovered)}
                    </div>
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Remaining captures:</strong> ${result.new_capture_count}
                    </div>
                    <div>
                        <strong>Current storage:</strong> ${formatBytes(result.new_storage_size)}
                    </div>
                </div>
                <div style="margin-top: 1.5rem;">
                    <button class="btn btn-primary" onclick="closeMaintenance(); loadJobs()">Close</button>
                </div>
            </div>
        `;
        
        showNotification(`Successfully cleaned up ${result.deleted_count} missing file record(s)`, 'success');
        
    } catch (error) {
        console.error('Maintenance cleanup failed:', error);
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <p style="color: var(--danger);">Failed to cleanup records</p>
                <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    ${escapeHtml(error.message)}
                </p>
                <button class="btn btn-secondary mt-lg" onclick="closeMaintenance()">Close</button>
            </div>
        `;
    }
}

function closeMaintenance() {
    const modal = document.getElementById('maintenance-modal');
    modal.classList.remove('active');
    maintenanceData = null;
}

// ===== Custom 24-Hour Time Picker Functions =====

function initializeTimePickers() {
    // Setup universal time input sync for time window fields only
    // Date/time pickers are set up per-modal to avoid conflicts
    setupTimeInputSync('time_window_start');
    setupTimeInputSync('time_window_end');
    
    // Set default start time to now
    setDefaultStartTime();
}

// Universal time input sync function for time-only fields (no date)
// Used for time window start/end times
function setupTimeInputSync(baseId) {
    const timeInput = document.getElementById(`${baseId}_time`);
    const hiddenInput = document.getElementById(baseId);
    
    if (!timeInput || !hiddenInput) return;
    
    const syncValue = () => {
        const time = timeInput.value;
        if (time) {
            hiddenInput.value = time;
        } else {
            hiddenInput.value = '';
        }
        // Dispatch change event so listeners on hidden input get notified
        hiddenInput.dispatchEvent(new Event('change'));
    };
    
    timeInput.addEventListener('change', syncValue);
}

function setupDateTimePickerSync(baseId, hiddenId) {
    const dateInput = document.getElementById(`${baseId}_date`);
    const hourSelect = document.getElementById(`${baseId}_hour`);
    const minuteSelect = document.getElementById(`${baseId}_minute`);
    const hiddenInput = document.getElementById(hiddenId || `${baseId}_datetime`);
    
    if (!dateInput || !hourSelect || !minuteSelect || !hiddenInput) return;
    
    const syncValue = () => {
        const date = dateInput.value;
        const hour = hourSelect.value;
        const minute = minuteSelect.value;
        
        if (date && hour && minute) {
            hiddenInput.value = `${date}T${hour}:${minute}`;
        } else {
            hiddenInput.value = '';
        }
        // Dispatch change event so listeners on hidden input get notified
        hiddenInput.dispatchEvent(new Event('change'));
    };
    
    dateInput.addEventListener('change', syncValue);
    hourSelect.addEventListener('change', syncValue);
    minuteSelect.addEventListener('change', syncValue);
}

function setDefaultStartTime() {
    const input = document.getElementById('start_datetime');
    if (input) {
        const now = new Date();
        input.value = isoToDatetimeLocal(now.toISOString());
    }
}

function initializeEditTimePickers(job) {
    // Setup universal time input sync for time windows
    setupTimeInputSync('edit_time_window_start');
    setupTimeInputSync('edit_time_window_end');
    
    // Set initial values for time window if enabled
    if (job.time_window_enabled && job.time_window_start && job.time_window_end) {
        setTimeInputValue('edit_time_window_start', job.time_window_start);
        setTimeInputValue('edit_time_window_end', job.time_window_end);
    }
    
    // Set initial value for end datetime if present
    if (job.end_datetime) {
        const input = document.getElementById('edit_end_datetime');
        if (input) input.value = isoToDatetimeLocal(job.end_datetime);
    }
}

// Universal function for setting time input values
// Used for time window start/end times
function setTimeInputValue(baseId, timeString) {
    if (!timeString) return;
    
    const timeInput = document.getElementById(`${baseId}_time`);
    
    if (timeInput) {
        timeInput.value = timeString;
        timeInput.dispatchEvent(new Event('change'));
    }
}

function setDateTimePickerValue(baseId, datetimeString, hiddenId) {
    if (!datetimeString) return;
    
    const dt = new Date(datetimeString);
    // Use local date components to avoid UTC conversion issues
    const year = dt.getFullYear();
    const month = String(dt.getMonth() + 1).padStart(2, '0');
    const day = String(dt.getDate()).padStart(2, '0');
    const dateStr = `${year}-${month}-${day}`;
    const hour = dt.getHours().toString().padStart(2, '0');
    const minute = dt.getMinutes().toString().padStart(2, '0');
    
    const dateInput = document.getElementById(`${baseId}_date`);
    const hourSelect = document.getElementById(`${baseId}_hour`);
    const minuteSelect = document.getElementById(`${baseId}_minute`);
    
    if (dateInput) dateInput.value = dateStr;
    if (hourSelect) hourSelect.value = hour;
    if (minuteSelect) minuteSelect.value = minute;
    
    // Trigger sync
    if (dateInput) dateInput.dispatchEvent(new Event('change'));
}

// Initialize time pickers on page load
document.addEventListener('DOMContentLoaded', initializeTimePickers);

// Storage Functions
let storageCharts = {};

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function getChartColors() {
    const style = getComputedStyle(document.documentElement);
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
        purple: style.getPropertyValue('--purple-mid').trim() || '#8b5cf6',
        blue: '#3b82f6',
        green: '#22c55e',
        amber: '#f59e0b',
        rose: '#f43f5e',
        cyan: '#06b6d4',
        indigo: '#6366f1',
        pink: '#ec4899',
        teal: '#14b8a6',
        orange: '#f97316',
        textPrimary: style.getPropertyValue('--text-primary').trim() || '#fff',
        textSecondary: style.getPropertyValue('--text-secondary').trim() || '#9ca3af',
        border: style.getPropertyValue('--border-color').trim() || '#374151',
        cardBg: style.getPropertyValue('--card-bg').trim() || '#1f2937',
        isDark
    };
}

const JOB_CHART_PALETTE = [
    '#8b5cf6', '#3b82f6', '#22c55e', '#f59e0b', '#f43f5e',
    '#06b6d4', '#6366f1', '#ec4899', '#14b8a6', '#f97316',
    '#a78bfa', '#60a5fa', '#4ade80', '#fbbf24', '#fb7185'
];

function destroyStorageCharts() {
    Object.values(storageCharts).forEach(chart => {
        if (chart) chart.destroy();
    });
    storageCharts = {};
}

async function loadStorage() {
    try {
        const data = await apiRequest('/storage/stats');
        renderStorageDashboard(data);
    } catch (error) {
        console.error('Failed to load storage:', error);
        showNotification('Failed to load storage stats', 'error');
    }
}

function renderStorageDashboard(data) {
    // Summary cards
    document.getElementById('stat-total-captures').textContent = data.captures_total_count.toLocaleString();
    document.getElementById('stat-total-videos').textContent = data.videos_total_count.toLocaleString();
    document.getElementById('stat-total-storage').textContent = formatBytes(data.captures_total_size + data.videos_total_size);
    document.getElementById('stat-disk-free').textContent = data.disk_total > 0 ? formatBytes(data.disk_free) : 'N/A';

    destroyStorageCharts();
    const colors = getChartColors();

    Chart.defaults.color = colors.textSecondary;
    Chart.defaults.borderColor = colors.border;

    renderDonutChart(data, colors);
    renderDiskChart(data, colors);
    renderJobChart(data, colors);
}

function renderDonutChart(data, colors) {
    const ctx = document.getElementById('storage-donut-chart');
    if (!ctx) return;

    const captureSize = data.captures_total_size;
    const videoSize = data.videos_total_size;

    if (captureSize === 0 && videoSize === 0) {
        storageCharts.donut = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['No Data'],
                datasets: [{ data: [1], backgroundColor: [colors.border], borderWidth: 0 }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: true, position: 'bottom', labels: { color: colors.textSecondary } },
                    tooltip: { enabled: false }
                }
            }
        });
        return;
    }

    storageCharts.donut = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: [`Captures (${formatBytes(captureSize)})`, `Timelapses (${formatBytes(videoSize)})`],
            datasets: [{
                data: [captureSize, videoSize],
                backgroundColor: [colors.purple, colors.blue],
                borderWidth: 2,
                borderColor: colors.cardBg,
                hoverBorderColor: colors.cardBg
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '60%',
            plugins: {
                legend: { position: 'bottom', labels: { color: colors.textSecondary, padding: 16, usePointStyle: true, pointStyle: 'circle' } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : 0;
                            return ` ${ctx.label}: ${pct}%`;
                        }
                    }
                }
            }
        }
    });
}

function renderDiskChart(data, colors) {
    const ctx = document.getElementById('storage-disk-chart');
    if (!ctx) return;

    if (data.disk_total === 0) {
        storageCharts.disk = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Unavailable'],
                datasets: [{ data: [1], backgroundColor: [colors.border], borderWidth: 0 }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: true, position: 'bottom', labels: { color: colors.textSecondary } },
                    tooltip: { enabled: false }
                }
            }
        });
        return;
    }

    const appUsed = data.captures_total_size + data.videos_total_size;
    const otherUsed = Math.max(0, data.disk_used - appUsed);
    const free = data.disk_free;

    storageCharts.disk = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: [
                `ChronoSnap (${formatBytes(appUsed)})`,
                `Other (${formatBytes(otherUsed)})`,
                `Free (${formatBytes(free)})`
            ],
            datasets: [{
                data: [appUsed, otherUsed, free],
                backgroundColor: [colors.purple, colors.amber, colors.green],
                borderWidth: 2,
                borderColor: colors.cardBg,
                hoverBorderColor: colors.cardBg
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '60%',
            plugins: {
                legend: { position: 'bottom', labels: { color: colors.textSecondary, padding: 16, usePointStyle: true, pointStyle: 'circle' } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = ((ctx.parsed / data.disk_total) * 100).toFixed(1);
                            return ` ${ctx.label}: ${pct}%`;
                        }
                    }
                }
            }
        }
    });
}

function renderJobChart(data, colors) {
    const ctx = document.getElementById('storage-job-chart');
    if (!ctx) return;

    const jobs = data.jobs.filter(j => j.total_size > 0);

    if (jobs.length === 0) {
        storageCharts.jobs = new Chart(ctx, {
            type: 'bar',
            data: { labels: ['No data'], datasets: [{ data: [0], backgroundColor: [colors.border] }] },
            options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false } } }
        });
        return;
    }

    const labels = jobs.map(j => j.job_name);
    const captureData = jobs.map(j => j.capture_size);
    const videoData = jobs.map(j => j.video_size);

    storageCharts.jobs = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Captures',
                    data: captureData,
                    backgroundColor: colors.purple + 'cc',
                    borderColor: colors.purple,
                    borderWidth: 1,
                    borderRadius: 4
                },
                {
                    label: 'Timelapses',
                    data: videoData,
                    backgroundColor: colors.blue + 'cc',
                    borderColor: colors.blue,
                    borderWidth: 1,
                    borderRadius: 4
                }
            ]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: colors.textSecondary, usePointStyle: true, pointStyle: 'circle', padding: 16 }
                },
                tooltip: {
                    callbacks: {
                        label: (ctx) => ` ${ctx.dataset.label}: ${formatBytes(ctx.parsed.x)}`
                    }
                }
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: {
                        color: colors.textSecondary,
                        callback: (val) => formatBytes(val)
                    },
                    grid: { color: colors.border + '40' }
                },
                y: {
                    stacked: true,
                    ticks: { color: colors.textSecondary },
                    grid: { display: false }
                }
            }
        }
    });

    // Dynamically size bar chart height based on job count
    const container = ctx.closest('.storage-chart-wide');
    if (container) {
        const height = Math.max(200, jobs.length * 50 + 80);
        container.style.maxHeight = height + 'px';
        ctx.style.maxHeight = height + 'px';
    }
}

// Settings Functions
async function loadSettings() {
    const apiKeyInput = document.getElementById('api-key-display');
    if (!apiKeyInput) {
        console.error('API key input element not found');
        return;
    }
    
    try {
        const data = await apiRequest('/settings/api-key');
        apiKeyInput.value = data.api_key;
    } catch (error) {
        console.error('Failed to load settings:', error);
        showNotification('Failed to load settings', 'error');
    }
    
    // Sync time format toggle state
    updateTimeFormatButtons();

    // Load theme presets
    renderThemePresets();

    // Load webhook settings
    loadWebhookSettings();
    loadTagManager();
    loadSharedVideosList();
    loadNamingPattern();
    
    // Load version and check for updates
    try {
        const ver = await apiRequest('/settings/version');
        const el = document.getElementById('app-version');
        if (el) el.textContent = `ChronoSnap v${ver.version}`;
        if (ver.update_available && ver.latest) {
            const badge = document.getElementById('update-badge');
            if (badge) {
                badge.textContent = `v${ver.latest} available`;
                badge.href = ver.release_url || 'https://github.com/kernelkaribou/timelapse-manager/releases';
                badge.style.display = '';
            }
        }
    } catch (e) { /* non-critical */ }
}

function updateTimeFormatButtons() {
    const current = getTimeFormat();
    const btn24 = document.getElementById('time-format-24');
    const btn12 = document.getElementById('time-format-12');
    if (btn24 && btn12) {
        btn24.className = current === '24' ? 'btn btn-sm btn-primary' : 'btn btn-sm btn-secondary';
        btn12.className = current === '12' ? 'btn btn-sm btn-primary' : 'btn btn-sm btn-secondary';
    }
}

function toggleTimeFormat(format) {
    setTimeFormat(format);
    updateTimeFormatButtons();
    // Refresh visible data to apply new format
    loadJobs();
    loadVideos();
    showNotification(`Time format set to ${format}-hour`, 'success');
}

async function copyApiKey() {
    const input = document.getElementById('api-key-display');
    try {
        await navigator.clipboard.writeText(input.value);
        showNotification('API key copied to clipboard', 'success');
    } catch (error) {
        // Fallback for older browsers
        input.select();
        document.execCommand('copy');
        showNotification('API key copied to clipboard', 'success');
    }
}

async function regenerateApiKey() {
    confirmAction(
        'Are you sure you want to regenerate the API key? This will invalidate the current key and any external integrations using it will need to be updated.',
        async () => {
            try {
                const data = await apiRequest('/settings/api-key/regenerate', { method: 'POST' });
                document.getElementById('api-key-display').value = data.api_key;
                showNotification('API key regenerated successfully', 'success');
            } catch (error) {
                console.error('Failed to regenerate API key:', error);
                showNotification('Failed to regenerate API key', 'error');
            }
        }
    );
}

// Webhook Settings Functions
let webhookDirty = false;
let webhookSnapshot = null;

function getWebhookCurrentValues() {
    return {
        url: document.getElementById('webhook-url').value.trim(),
        enabled: document.getElementById('webhook-enabled').checked,
        template: document.getElementById('webhook-template').value.trim(),
        events: [...document.querySelectorAll('.webhook-event-cb:checked')].map(cb => cb.value).sort().join(','),
    };
}

function markWebhookDirty() {
    if (webhookSnapshot) {
        const cur = getWebhookCurrentValues();
        webhookDirty = cur.url !== webhookSnapshot.url
            || cur.enabled !== webhookSnapshot.enabled
            || cur.template !== webhookSnapshot.template
            || cur.events !== webhookSnapshot.events;
    } else {
        webhookDirty = true;
    }
    const btn = document.getElementById('webhook-save-btn');
    if (btn) btn.disabled = !webhookDirty;
    updateWebhookToggleState();
}

function updateWebhookToggleState() {
    const url = document.getElementById('webhook-url').value.trim();
    const toggle = document.getElementById('webhook-enabled');
    if (!url) {
        toggle.disabled = true;
        toggle.checked = false;
    } else {
        toggle.disabled = false;
    }
}

async function loadWebhookSettings() {
    try {
        const data = await apiRequest('/settings/webhook');
        document.getElementById('webhook-url').value = data.webhook_url || '';
        document.getElementById('webhook-enabled').checked = data.webhook_enabled;
        document.getElementById('webhook-template').value = data.webhook_payload_template || '{"title": "{title}", "message": "{message}"}';
        // Load event filters — if empty array, check all (backwards-compatible default)
        const events = data.webhook_events || [];
        document.querySelectorAll('.webhook-event-cb').forEach(cb => {
            cb.checked = events.length === 0 || events.includes(cb.value);
        });
        updateWebhookToggleState();
        webhookSnapshot = getWebhookCurrentValues();
        webhookDirty = false;
        const btn = document.getElementById('webhook-save-btn');
        if (btn) btn.disabled = true;
    } catch (error) {
        console.error('Failed to load webhook settings:', error);
    }
}

function isValidWebhookUrl(url) {
    if (!url.startsWith('http://') && !url.startsWith('https://')) return false;
    try {
        const parsed = new URL(url);
        return parsed.hostname.length > 0;
    } catch { return false; }
}

async function saveWebhookSettings() {
    const url = document.getElementById('webhook-url').value.trim();
    const template = document.getElementById('webhook-template').value.trim();
    const defaultTemplate = '{"title": "{title}", "message": "{message}"}';

    if (url && !isValidWebhookUrl(url)) {
        showNotification('Webhook URL must be a valid http:// or https:// URL', 'error');
        return;
    }

    const settings = {
        webhook_enabled: document.getElementById('webhook-enabled').checked && !!url,
        webhook_url: url,
        webhook_payload_template: template || defaultTemplate,
        webhook_events: [...document.querySelectorAll('.webhook-event-cb:checked')].map(cb => cb.value),
    };

    // If template was empty, update the textarea to show the default
    if (!template) {
        document.getElementById('webhook-template').value = defaultTemplate;
    }

    // Validate JSON template
    try {
        const testPayload = settings.webhook_payload_template
            .replace(/\{job_name\}/g, 'test')
            .replace(/\{job_id\}/g, '0')
            .replace(/\{failure_count\}/g, '3')
            .replace(/\{error_message\}/g, 'test')
            .replace(/\{title\}/g, 'test')
            .replace(/\{message\}/g, 'test');
        JSON.parse(testPayload);
    } catch (e) {
        showNotification('Payload template must produce valid JSON', 'error');
        return;
    }

    try {
        await apiRequest('/settings/webhook', {
            method: 'PUT',
            body: settings
        });
        webhookSnapshot = getWebhookCurrentValues();
        webhookDirty = false;
        const btn = document.getElementById('webhook-save-btn');
        if (btn) btn.disabled = true;
        showNotification('Webhook settings saved', 'success');
    } catch (error) {
        console.error('Failed to save webhook settings:', error);
        showNotification('Failed to save webhook settings', 'error');
    }
}

async function testWebhook() {
    const url = document.getElementById('webhook-url').value.trim();
    const template = document.getElementById('webhook-template').value;

    if (!url) {
        showNotification('Enter a webhook URL first', 'error');
        return;
    }

    if (!isValidWebhookUrl(url)) {
        showNotification('Webhook URL must be a valid http:// or https:// URL', 'error');
        return;
    }

    try {
        const data = await apiRequest('/settings/webhook/test', {
            method: 'POST',
            body: { url, payload_template: template }
        });
        showNotification(data.message, data.success ? 'success' : 'error');
    } catch (error) {
        console.error('Webhook test failed:', error);
        showNotification('Webhook test failed', 'error');
    }
}

// =============================================================================
// Tags Manager & Tag Picker
// =============================================================================

let allTags = [];

const TAG_COLORS = [
    '#ef4444', '#f97316', '#f59e0b', '#eab308',
    '#22c55e', '#10b981', '#14b8a6', '#06b6d4',
    '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7',
    '#ec4899', '#f43f5e', '#78716c', '#64748b'
];

function colorSwatchHTML(containerId, selectedColor) {
    return `<div class="color-swatch-row" id="${containerId}">${TAG_COLORS.map(c =>
        `<span class="color-swatch${c === selectedColor ? ' selected' : ''}" style="background:${c};" data-color="${c}" onclick="selectSwatch(this)"></span>`
    ).join('')}</div>`;
}

function selectSwatch(el) {
    el.parentElement.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('selected'));
    el.classList.add('selected');
}

function getSwatchColor(containerId, fallback = '#6366f1') {
    const selected = document.querySelector(`#${containerId} .color-swatch.selected`);
    return selected ? selected.dataset.color : fallback;
}

async function loadTagManager() {
    try {
        allTags = await apiRequest('/tags/');
        renderTagList();
        // Render create swatches if not already populated
        const swatchContainer = document.getElementById('tag-create-swatches');
        if (swatchContainer && !swatchContainer.children.length) {
            swatchContainer.innerHTML = TAG_COLORS.map(c =>
                `<span class="color-swatch${c === '#6366f1' ? ' selected' : ''}" style="background:${c};" data-color="${c}" onclick="selectSwatch(this)"></span>`
            ).join('');
        }
    } catch (error) {
        console.error('Failed to load tags:', error);
    }
}

function renderTagList() {
    const container = document.getElementById('tag-list');
    if (!container) return;

    if (allTags.length === 0) {
        container.innerHTML = '<div style="color: var(--text-secondary); font-size: 0.85rem; padding: 0.5rem;">No tags yet. Create one above.</div>';
        return;
    }

    container.innerHTML = allTags.map(tag => `
        <div class="tag-list-item" data-tag-id="${tag.id}">
            <span class="tag-chip" style="background: ${tag.color}22; color: ${tag.color}; border: 1px solid ${tag.color}44;">
                <span style="width:8px;height:8px;border-radius:50%;background:${tag.color};display:inline-block;"></span>
                ${escapeHtml(tag.name)}
            </span>
            <span class="tag-usage">${tag.job_count} job${tag.job_count !== 1 ? 's' : ''}, ${tag.video_count} video${tag.video_count !== 1 ? 's' : ''}</span>
            <span class="tag-actions">
                <button onclick="editTagInline(${tag.id})" title="Edit" class="tag-action-btn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                </button>
                <button onclick="deleteTag(${tag.id}, '${escapeHtml(tag.name)}')" title="Delete" class="tag-action-btn tag-action-delete">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                </button>
            </span>
        </div>
    `).join('');
}

async function createTag() {
    const nameInput = document.getElementById('tag-create-name');
    const color = getSwatchColor('tag-create-swatches');
    const name = nameInput.value.trim();
    if (!name) {
        showNotification('Enter a tag name', 'error');
        return;
    }
    try {
        await apiRequest('/tags/', {
            method: 'POST',
            body: { name, color }
        });
        nameInput.value = '';
        await loadTagManager();
        showNotification(`Tag "${name}" created`);
    } catch (error) {
        showNotification(error.message || 'Failed to create tag', 'error');
    }
}

function editTagInline(tagId) {
    const tag = allTags.find(t => t.id === tagId);
    if (!tag) return;

    const item = document.querySelector(`.tag-list-item[data-tag-id="${tagId}"]`);
    if (!item) return;

    item.innerHTML = `
        <input type="text" class="form-control" value="${escapeHtml(tag.name)}" id="tag-edit-name-${tagId}" style="flex:1;max-width:150px;font-size:0.85rem;padding:0.25rem 0.5rem;">
        ${colorSwatchHTML(`tag-edit-swatches-${tagId}`, tag.color)}
        <button class="btn btn-sm btn-accent" onclick="saveTagEdit(${tagId})">Save</button>
        <button class="btn btn-sm btn-secondary" onclick="renderTagList()">Cancel</button>
    `;
    document.getElementById(`tag-edit-name-${tagId}`).focus();
}

async function saveTagEdit(tagId) {
    const name = document.getElementById(`tag-edit-name-${tagId}`).value.trim();
    const color = getSwatchColor(`tag-edit-swatches-${tagId}`);
    if (!name) {
        showNotification('Tag name cannot be empty', 'error');
        return;
    }
    try {
        await apiRequest(`/tags/${tagId}`, {
            method: 'PUT',
            body: { name, color }
        });
        await loadTagManager();
        showNotification('Tag updated');
    } catch (error) {
        showNotification(error.message || 'Failed to update tag', 'error');
    }
}

async function deleteTag(tagId, tagName) {
    confirmAction(`Delete tag "${tagName}"? It will be removed from all jobs and timelapses.`, async () => {
        try {
            await apiRequest(`/tags/${tagId}`, { method: 'DELETE' });
            await loadTagManager();
            showNotification(`Tag "${tagName}" deleted`);
        } catch (error) {
            showNotification('Failed to delete tag', 'error');
        }
    });
}

// Tag chip HTML helper
function tagChipHTML(tag, small = false) {
    const sizeStyle = small ? 'font-size:0.6rem;padding:0.1rem 0.4rem;' : '';
    return `<span class="tag-chip" style="${sizeStyle}background:${tag.color}22;color:${tag.color};border:1px solid ${tag.color}44;">
        <span style="width:6px;height:6px;border-radius:50%;background:${tag.color};display:inline-block;"></span>
        ${escapeHtml(tag.name)}</span>`;
}

// Tag picker for modals — renders all tags, toggles selection on click
// onToggle(tagIds): optional callback fired after every toggle (for auto-save)
function renderTagPicker(containerId, selectedTagIds = [], onToggle = null) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container._tagOnToggle = onToggle;
    container._tagPickerId = containerId;

    container.innerHTML = allTags.map(tag => {
        const isSelected = selectedTagIds.includes(tag.id);
        return `<span class="tag-chip ${isSelected ? 'selected' : ''}" data-tag-id="${tag.id}"
            style="background:${tag.color}22;color:${tag.color};border:1px solid ${tag.color}44;cursor:pointer;opacity:${isSelected ? '1' : '0.4'};"
            onclick="toggleTagInPicker(this)">
            <span style="width:6px;height:6px;border-radius:50%;background:${tag.color};display:inline-block;"></span>
            ${escapeHtml(tag.name)}</span>`;
    }).join('');

    // Render footer: form always in flow (reserves height), button overlays it
    let footer = container.parentElement.querySelector('.tag-picker-footer');
    if (!footer) {
        footer = document.createElement('div');
        footer.className = 'tag-picker-footer';
        container.parentElement.appendChild(footer);
    }
    const swatchesHTML = TAG_COLORS.map(c =>
        `<span class="color-swatch${c === '#6366f1' ? ' selected' : ''}" style="background:${c};" data-color="${c}" onclick="selectSwatch(this)"></span>`
    ).join('');
    footer.innerHTML = `
        <span class="tag-create-btn" onclick="showInlineTagCreate('${containerId}')" title="Create new tag">＋ New Tag</span>
        <div class="tag-inline-form" style="visibility:hidden;">
            <input type="text" class="form-control" placeholder="New tag name..." maxlength="50" tabindex="-1">
            <div class="color-swatch-row">${swatchesHTML}</div>
            <button type="button" class="btn btn-accent btn-sm" tabindex="-1">Add Tag</button>
            <button type="button" class="btn btn-secondary btn-sm" tabindex="-1">Cancel</button>
        </div>
    `;
}

function showInlineTagCreate(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const footer = container.parentElement.querySelector('.tag-picker-footer');
    if (!footer) return;
    const btn = footer.querySelector('.tag-create-btn');
    const form = footer.querySelector('.tag-inline-form');
    if (!form || !btn) return;

    btn.style.display = 'none';
    form.style.visibility = 'visible';

    // Wire up the buttons now that form is active
    const input = form.querySelector('input');
    input.removeAttribute('tabindex');
    form.querySelectorAll('button').forEach(b => b.removeAttribute('tabindex'));
    const addBtn = form.querySelectorAll('button')[0];
    const cancelBtn = form.querySelectorAll('button')[1];
    addBtn.onclick = () => submitInlineTag(containerId);
    cancelBtn.onclick = () => cancelInlineTagCreate(containerId);
    input.focus();
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') submitInlineTag(containerId);
        if (e.key === 'Escape') cancelInlineTagCreate(containerId);
    });
}

function cancelInlineTagCreate(containerId) {
    const container = document.getElementById(containerId);
    const footer = container?.parentElement.querySelector('.tag-picker-footer');
    if (!footer) return;
    const btn = footer.querySelector('.tag-create-btn');
    const form = footer.querySelector('.tag-inline-form');
    if (btn) btn.style.display = '';
    if (form) {
        form.style.visibility = 'hidden';
        form.querySelector('input').value = '';
    }
}

async function submitInlineTag(containerId) {
    const container = document.getElementById(containerId);
    const footer = container?.parentElement.querySelector('.tag-picker-footer');
    const form = footer?.querySelector('.tag-inline-form');
    if (!form) return;
    const name = form.querySelector('input').value.trim();
    const swatchRow = form.querySelector('.color-swatch-row');
    const selected = swatchRow?.querySelector('.color-swatch.selected');
    const color = selected ? selected.dataset.color : '#6366f1';
    if (!name) { showNotification('Enter a tag name', 'error'); return; }
    try {
        const newTag = await apiRequest('/tags/', { method: 'POST', body: { name, color } });
        allTags.push(newTag);
        const currentIds = getSelectedTagIds(containerId);
        currentIds.push(newTag.id);
        const onToggle = container._tagOnToggle || null;
        renderTagPicker(containerId, currentIds, onToggle);
        if (onToggle) onToggle(currentIds);
        showNotification(`Tag "${name}" created`);
    } catch (error) {
        showNotification(error.message || 'Failed to create tag', 'error');
    }
}

function toggleTagInPicker(el) {
    el.classList.toggle('selected');
    el.style.opacity = el.classList.contains('selected') ? '1' : '0.4';
    // Fire auto-save callback if present
    const container = el.closest('.tag-picker');
    if (container?._tagOnToggle) {
        const tagIds = getSelectedTagIds(container._tagPickerId);
        container._tagOnToggle(tagIds);
    }
}

function getSelectedTagIds(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.tag-chip.selected')).map(el => parseInt(el.dataset.tagId));
}

// Tag filter dropdown for galleries — searchable multi-select
function renderTagFilter(wrapId, onChange) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;

    wrap._tagFilterSelected = new Set();
    wrap._tagFilterOnChange = onChange;

    wrap.innerHTML = `
        <div class="tag-filter-trigger" onclick="toggleTagFilterDropdown('${wrapId}')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="1.5"/></svg>
            <span class="tag-filter-label">Tags</span>
        </div>
        <div class="tag-filter-dropdown" id="${wrapId}-dropdown">
            <div class="tag-filter-search">
                <input type="text" placeholder="Search tags..." oninput="filterTagDropdown('${wrapId}', this.value)">
            </div>
            <div class="tag-filter-list" id="${wrapId}-list"></div>
        </div>
    `;

    populateTagFilterList(wrapId);

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) {
            const dd = document.getElementById(`${wrapId}-dropdown`);
            if (dd) dd.classList.remove('open');
            const trigger = wrap.querySelector('.tag-filter-trigger');
            if (trigger) trigger.classList.remove('active');
        }
    });
}

function populateTagFilterList(wrapId) {
    const list = document.getElementById(`${wrapId}-list`);
    if (!list) return;
    const wrap = document.getElementById(wrapId);
    const selected = wrap?._tagFilterSelected || new Set();

    list.innerHTML = allTags.map(tag => `
        <div class="tag-filter-item ${selected.has(tag.id) ? 'selected' : ''}" data-tag-id="${tag.id}" data-name="${escapeHtml(tag.name.toLowerCase())}"
             onclick="toggleTagFilter('${wrapId}', ${tag.id})">
            <span class="tag-filter-check"></span>
            <span class="tag-filter-dot" style="background:${tag.color};"></span>
            <span>${escapeHtml(tag.name)}</span>
        </div>
    `).join('');

    if (allTags.length === 0) {
        list.innerHTML = '<div style="padding:0.5rem;color:var(--text-secondary);font-size:0.8rem;">No tags</div>';
    }
}

function toggleTagFilterDropdown(wrapId) {
    const dd = document.getElementById(`${wrapId}-dropdown`);
    const trigger = document.querySelector(`#${wrapId} .tag-filter-trigger`);
    if (!dd) return;
    const isOpen = dd.classList.toggle('open');
    if (trigger) trigger.classList.toggle('active', isOpen);
    if (isOpen) {
        populateTagFilterList(wrapId);
        const input = dd.querySelector('input');
        if (input) { input.value = ''; input.focus(); }
    }
}

function filterTagDropdown(wrapId, query) {
    const list = document.getElementById(`${wrapId}-list`);
    if (!list) return;
    const q = query.toLowerCase();
    list.querySelectorAll('.tag-filter-item').forEach(item => {
        item.style.display = item.dataset.name.includes(q) ? '' : 'none';
    });
}

function toggleTagFilter(wrapId, tagId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    const selected = wrap._tagFilterSelected;
    if (selected.has(tagId)) selected.delete(tagId);
    else selected.add(tagId);

    // Update item visual
    const item = document.querySelector(`#${wrapId}-list .tag-filter-item[data-tag-id="${tagId}"]`);
    if (item) item.classList.toggle('selected', selected.has(tagId));

    // Update trigger label
    updateTagFilterTrigger(wrapId);

    // Fire callback
    if (wrap._tagFilterOnChange) wrap._tagFilterOnChange([...selected]);
}

function updateTagFilterTrigger(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    const selected = wrap._tagFilterSelected;
    const label = wrap.querySelector('.tag-filter-label');
    if (!label) return;

    if (selected.size === 0) {
        label.textContent = 'Tags';
    } else if (selected.size <= 2) {
        const names = allTags.filter(t => selected.has(t.id)).map(t => t.name);
        label.textContent = names.join(', ');
    } else {
        label.innerHTML = `Tags <span class="tag-filter-count">${selected.size}</span>`;
    }
}

function getTagFilterIds(wrapId) {
    const wrap = document.getElementById(wrapId);
    return wrap?._tagFilterSelected ? [...wrap._tagFilterSelected] : [];
}

function clearTagFilter(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (wrap?._tagFilterSelected) wrap._tagFilterSelected.clear();
    updateTagFilterTrigger(wrapId);
}

// ── Status Filter Dropdown ─────────────────────────────────────────────

const JOB_STATUSES = [
    { value: 'active', label: 'Active', color: 'var(--success-color)' },
    { value: 'sleeping', label: 'Sleeping', color: 'var(--primary-color)' },
    { value: 'warning', label: 'Warning', color: 'var(--warning-color)' },
    { value: 'completed', label: 'Completed', color: 'var(--text-secondary)' },
    { value: 'disabled', label: 'Disabled', color: 'var(--text-muted, #666)' },
];

function renderStatusFilter(wrapId, onChange) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    
    wrap._statusFilterSelected = new Set();
    wrap._statusFilterOnChange = onChange;

    wrap.innerHTML = `
        <div class="tag-filter-trigger" onclick="toggleStatusFilterDropdown('${wrapId}')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
            <span class="tag-filter-label">Status</span>
        </div>
        <div class="tag-filter-dropdown" id="${wrapId}-dropdown">
            <div class="tag-filter-list" id="${wrapId}-list">
                ${JOB_STATUSES.map(s => `
                    <div class="tag-filter-item" data-status="${s.value}"
                         onclick="toggleStatusFilter('${wrapId}', '${s.value}')">
                        <span class="tag-filter-check"></span>
                        <span class="tag-filter-dot" style="background:${s.color};"></span>
                        <span>${s.label}</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) {
            const dd = document.getElementById(`${wrapId}-dropdown`);
            if (dd) dd.classList.remove('open');
            const trigger = wrap.querySelector('.tag-filter-trigger');
            if (trigger) trigger.classList.remove('active');
        }
    });
}

function toggleStatusFilterDropdown(wrapId) {
    const dd = document.getElementById(`${wrapId}-dropdown`);
    const trigger = document.querySelector(`#${wrapId} .tag-filter-trigger`);
    if (!dd) return;
    const isOpen = dd.classList.toggle('open');
    if (trigger) trigger.classList.toggle('active', isOpen);
}

function toggleStatusFilter(wrapId, status) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    const selected = wrap._statusFilterSelected;
    if (selected.has(status)) selected.delete(status);
    else selected.add(status);

    const item = document.querySelector(`#${wrapId}-list .tag-filter-item[data-status="${status}"]`);
    if (item) item.classList.toggle('selected', selected.has(status));

    updateStatusFilterTrigger(wrapId);
    if (wrap._statusFilterOnChange) wrap._statusFilterOnChange();
}

function updateStatusFilterTrigger(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    const selected = wrap._statusFilterSelected;
    const label = wrap.querySelector('.tag-filter-label');
    if (!label) return;
    const trigger = wrap.querySelector('.tag-filter-trigger');

    if (selected.size === 0) {
        label.textContent = 'Status';
        if (trigger) trigger.classList.remove('active');
    } else if (selected.size <= 2) {
        const names = JOB_STATUSES.filter(s => selected.has(s.value)).map(s => s.label);
        label.textContent = names.join(', ');
        if (trigger) trigger.classList.add('active');
    } else {
        label.innerHTML = `Status <span class="tag-filter-count">${selected.size}</span>`;
        if (trigger) trigger.classList.add('active');
    }
}

function getStatusFilterValues(wrapId) {
    const wrap = document.getElementById(wrapId);
    return wrap?._statusFilterSelected ? [...wrap._statusFilterSelected] : [];
}

function clearStatusFilter(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (wrap?._statusFilterSelected) {
        wrap._statusFilterSelected.clear();
        const items = document.querySelectorAll(`#${wrapId}-list .tag-filter-item`);
        items.forEach(i => i.classList.remove('selected'));
    }
    updateStatusFilterTrigger(wrapId);
}

// Nav Warning Badge
function updateJobWarningBadge(jobs) {
    const jobsLink = document.querySelector('.nav-link[data-view="jobs"]');
    if (!jobsLink) return;

    // Remove existing badge
    const existing = jobsLink.querySelector('.nav-warning-badge');
    if (existing) existing.remove();

    // Check if any job has warning status
    const hasWarnings = jobs && jobs.some(j => j.status === 'warning');
    if (hasWarnings) {
        const badge = document.createElement('span');
        badge.className = 'nav-warning-badge';
        badge.title = 'One or more jobs have warnings';
        jobsLink.appendChild(badge);
    }
}

// =============================================================================
// Captures Management
// =============================================================================

let capturesState = {
    currentPage: 1,
    pageSize: 16,
    sortOrder: 'desc',
    jobFilter: null,
    tagFilter: null,
    startTime: null,
    endTime: null,
    favoritesOnly: false,
    currentCaptureId: null
};

const captureSelection = new SelectionManager({
    name: 'captures',
    cardSelector: '.capture-card',
    dataAttr: 'data-capture-id',
    controlsId: 'selection-controls',
    countId: 'captures-selected-count',
    toggleBtnId: 'toggle-selection-btn',
    deleteEndpoint: '/captures/delete-multiple',
    deleteBodyKey: 'capture_ids',
    favoriteEndpoint: '/captures/favorite',
    itemLabel: 'capture',
    onReload: () => loadCapturesPage()
});

async function loadCaptures() {
    try {
        // Load job filter options first time
        const jobSelect = document.getElementById('captures-job-filter');
        if (jobSelect.options.length === 1) {
            const jobs = await apiRequest('/jobs/');
            jobs.forEach(job => {
                const option = document.createElement('option');
                option.value = job.id;
                option.textContent = job.name;
                jobSelect.appendChild(option);
            });
        }
        
        // Initialize tag filter (once)
        const tagWrap = document.getElementById('captures-tag-filter-wrap');
        if (tagWrap && !tagWrap._tagFilterSelected) {
            renderTagFilter('captures-tag-filter-wrap', () => applyCaptureSortAndFilter());
        }
        
        await loadCapturesPage();
    } catch (error) {
        console.error('Failed to load captures:', error);
        showNotification('Failed to load captures', 'error');
    }
}

async function scanOrphanedCaptures() {
    const modal = document.getElementById('maintenance-modal');
    const title = document.getElementById('maintenance-title');
    const content = document.getElementById('maintenance-content');
    
    title.textContent = 'Captures Cleanup';
    content.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <div style="font-size: 2rem; margin-bottom: 1rem;">🔍</div>
            <p>Scanning for orphaned captures...</p>
            <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                Checking filesystem and database...
            </p>
        </div>
    `;
    showModal('maintenance-modal');
    
    try {
        const data = await apiRequest('/captures/orphaned');
        displayOrphanedResults(data);
    } catch (error) {
        console.error('Orphaned scan failed:', error);
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <div style="font-size: 2rem; margin-bottom: 1rem;">❌</div>
                <p style="color: var(--danger);">Failed to scan for orphaned captures</p>
                <p style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 0.5rem;">
                    ${escapeHtml(error.message)}
                </p>
                <button class="btn btn-secondary mt-lg" onclick="closeMaintenance()">Close</button>
            </div>
        `;
    }
}

function displayOrphanedResults(data) {
    const content = document.getElementById('maintenance-content');
    
    if (!data.orphaned_groups || data.orphaned_groups.length === 0) {
        content.innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <h3 style="margin-bottom: 0.5rem;">All Clean</h3>
                <p style="color: var(--text-secondary);">
                    No orphaned captures found. All files and records belong to active jobs.
                </p>
                <button class="btn btn-primary mt-xl" onclick="closeMaintenance()">Close</button>
            </div>
        `;
        return;
    }
    
    const groupsHtml = data.orphaned_groups.map(group => {
        const typeLabel = group.type === 'both' ? 'Files + DB Records' 
            : group.type === 'database' ? 'DB Records Only' 
            : 'Files Only';
        const typeBadgeColor = group.type === 'both' ? 'var(--danger-color)' 
            : group.type === 'database' ? 'var(--primary-color)' 
            : 'var(--accent-color)';
        
        let details = '';
        if (group.type === 'filesystem') {
            details = `${group.file_count} file${group.file_count !== 1 ? 's' : ''} on disk · ${formatBytes(group.total_size)}`;
        } else if (group.type === 'database') {
            details = `${group.record_count} DB record${group.record_count !== 1 ? 's' : ''}`;
        } else {
            details = `${group.file_count} file${group.file_count !== 1 ? 's' : ''} on disk + ${group.record_count} DB record${group.record_count !== 1 ? 's' : ''} · ${formatBytes(group.total_size - (group.db_size || 0))}`;
        }
        
        // Build cleanup params based on type
        const cleanupArg = group.type === 'filesystem' 
            ? `'fs', '${escapeHtml(group.folder_path)}', null`
            : group.type === 'database'
            ? `'db', null, ${group.original_job_id}`
            : `'both', '${escapeHtml(group.folder_path)}', ${group.original_job_id}`;
        
        return `
            <div class="flex-between" style="padding: 0.75rem; background: var(--bg-color); border-radius: var(--radius-sm); margin-bottom: 0.5rem;">
                <div style="flex: 1; min-width: 0;">
                    <div class="form-row-wrap">
                        <strong>${escapeHtml(group.original_job_name)}</strong>
                        <span class="text-xs" style="padding: 0.1rem 0.4rem; border-radius: 0.25rem; background: ${typeBadgeColor}; color: var(--primary-text-on);">${typeLabel}</span>
                    </div>
                    <div class="text-sm text-secondary mt-sm">
                        ${details}
                    </div>
                </div>
                <button class="btn btn-danger btn-sm" style="white-space: nowrap; margin-left: 0.5rem;" 
                    onclick="deleteOrphanedGroup(${cleanupArg}, '${escapeHtml(group.original_job_name)}')">
                    Delete
                </button>
            </div>
        `;
    }).join('');
    
    // Summary line
    const summaryParts = [];
    if (data.total_fs_files > 0) summaryParts.push(`${data.total_fs_files} files on disk (${formatBytes(data.total_fs_size)})`);
    if (data.total_db_records > 0) summaryParts.push(`${data.total_db_records} DB records`);
    
    content.innerHTML = `
        <div>
            <div class="mb-lg">
                <strong class="text-warning">⚠ Orphaned Captures Found</strong>
                <div class="text-sm text-secondary mt-sm">
                    ${data.orphaned_groups.length} group${data.orphaned_groups.length > 1 ? 's' : ''} · ${summaryParts.join(' · ')}
                </div>
            </div>
            <div style="max-height: 400px; overflow-y: auto;">
                ${groupsHtml}
            </div>
            <div class="flex-between" style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border-color);">
                <button class="btn btn-secondary" onclick="closeMaintenance()">Close</button>
                ${data.orphaned_groups.length > 1 ? `
                    <button class="btn btn-danger" onclick="deleteAllOrphanedCaptures()">Delete All</button>
                ` : ''}
            </div>
        </div>
    `;
}

async function deleteOrphanedGroup(type, folderPath, jobId, jobName) {
    confirmAction(
        `Delete all orphaned captures from "${jobName}"? This will permanently remove ${type === 'database' ? 'database records' : type === 'both' ? 'files and database records' : 'files from disk'}.`,
        async () => {
            try {
                const body = {};
                if (folderPath) body.folders = [folderPath];
                if (jobId) body.job_ids = [jobId];
                
                const response = await apiRequest('/captures/orphaned/cleanup', {
                    method: 'POST',
                    body
                });
                
                const parts = [];
                if (response.total_folders_deleted > 0) parts.push(`${response.total_folders_deleted} folder(s)`);
                if (response.total_db_records_deleted > 0) parts.push(`${response.total_db_records_deleted} DB records`);
                if (response.total_freed > 0) parts.push(`${formatBytes(response.total_freed)} freed`);
                
                showNotification(`Cleaned up ${parts.join(', ')}`, 'success');
                const data = await apiRequest('/captures/orphaned');
                displayOrphanedResults(data);
                loadCapturesPage();
            } catch (error) {
                console.error('Failed to delete orphaned captures:', error);
                showNotification('Failed to delete orphaned captures', 'error');
            }
        }
    );
}

async function deleteAllOrphanedCaptures() {
    confirmAction(
        'Delete ALL orphaned captures? This will permanently remove all orphaned files and database records.',
        async () => {
            try {
                const response = await apiRequest('/captures/orphaned/cleanup', {
                    method: 'POST',
                    body: { delete_all: true }
                });
                
                const parts = [];
                if (response.total_folders_deleted > 0) parts.push(`${response.total_folders_deleted} folder(s)`);
                if (response.total_db_records_deleted > 0) parts.push(`${response.total_db_records_deleted} DB records`);
                if (response.total_freed > 0) parts.push(`${formatBytes(response.total_freed)} freed`);
                
                showNotification(`Cleaned up ${parts.join(', ')}`, 'success');
                const data = await apiRequest('/captures/orphaned');
                displayOrphanedResults(data);
                loadCapturesPage();
            } catch (error) {
                console.error('Failed to delete orphaned captures:', error);
                showNotification('Failed to delete orphaned captures', 'error');
            }
        }
    );
}

async function loadCapturesPage() {
    try {
        const query = {
            page: capturesState.currentPage,
            page_size: capturesState.pageSize,
            sort_order: capturesState.sortOrder
        };
        
        if (capturesState.jobFilter) {
            query.job_id = capturesState.jobFilter;
        }
        
        if (capturesState.startTime) {
            query.start_time = capturesState.startTime;
        }
        
        if (capturesState.endTime) {
            query.end_time = capturesState.endTime;
        }
        
        if (capturesState.favoritesOnly) {
            query.favorites_only = true;
        }
        
        if (capturesState.tagFilter) {
            query.tag_id = capturesState.tagFilter;
        }
        
        const data = await apiRequest('/captures/', { query });
        
        const countEl = document.getElementById('captures-count');
        countEl.textContent = `${data.total} captures`;
        
        renderCaptures(data.captures);
        renderPagination(data);
        captureSelection.updateControls();
    } catch (error) {
        console.error('Failed to load captures page:', error);
        showNotification('Failed to load captures', 'error');
    }
}

function renderCaptures(captures) {
    const grid = document.getElementById('captures-grid');
    
    if (captures.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary);">No captures found</div>';
        return;
    }
    
    grid.innerHTML = captures.map((capture, idx) => `
        <div class="capture-card ${captureSelection.has(capture.id) ? 'selected' : ''}" 
             style="--i:${idx}"
             data-capture-id="${capture.id}"
             onclick="captureSelection.handleCardClick(${capture.id}, event, showCapturePreview)">
            <input type="checkbox" 
                   class="capture-checkbox" 
                   ${captureSelection.has(capture.id) ? 'checked' : ''}
                   onclick="event.stopPropagation(); captureSelection.toggle(${capture.id}, event)">
            <button class="card-fav-btn ${capture.is_favorite ? 'favorited' : ''}" 
                    onclick="event.stopPropagation(); toggleFavorite('captures', ${capture.id}, ${capture.is_favorite ? 'true' : 'false'})"
                    title="${capture.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
                <svg class="fav-heart-icon" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
            </button>
            <img src="${API_BASE}/captures/${capture.id}/thumbnail" 
                 class="capture-thumbnail"
                 alt="Capture thumbnail"
                 onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22112%22%3E%3Crect width=%22200%22 height=%22112%22 fill=%22%231e293b%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%23cbd5e1%22 font-family=%22sans-serif%22%3ENo Preview%3C/text%3E%3C/svg%3E'">
            <div class="capture-info">
                <div class="capture-job-name">${escapeHtml(capture.job_name || 'Unknown Job')}</div>
                <div class="capture-time">${formatDateTime(capture.captured_at)}</div>
                ${capture.tags && capture.tags.length ? `<div class="card-tags">${capture.tags.map(t => tagChipHTML(t, true)).join('')}</div>` : ''}
            </div>
        </div>
    `).join('');
}

function renderPagination(data) {
    const container = document.getElementById('captures-pagination');
    
    if (data.total_pages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    const currentPage = data.page;
    const totalPages = data.total_pages;
    
    let pages = [];
    
    // Always show first page
    pages.push(1);
    
    // Show pages around current page
    for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
        if (!pages.includes(i)) pages.push(i);
    }
    
    // Always show last page
    if (totalPages > 1 && !pages.includes(totalPages)) {
        pages.push(totalPages);
    }
    
    container.innerHTML = `
        <button onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>
            Previous
        </button>
        ${pages.map((page, index) => {
            // Add ellipsis if there's a gap
            const gap = index > 0 && page - pages[index - 1] > 1 ? '<span class="pagination-ellipsis">...</span>' : '';
            return `${gap}<button class="${page === currentPage ? 'active' : ''}" onclick="goToPage(${page})">${page}</button>`;
        }).join('')}
        <button onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>
            Next
        </button>
        <span class="pagination-info">
            Page ${currentPage} of ${totalPages} (${data.total} captures)
        </span>
    `;
}

function goToPage(page) {
    capturesState.currentPage = page;
    loadCapturesPage();
}

function applyCaptureSortAndFilter() {
    const jobFilter = getValue('captures-job-filter');
    const selectedTags = getTagFilterIds('captures-tag-filter-wrap');
    const startTime = getValue('captures-start-time');
    const endTime = getValue('captures-end-time');
    const sortOrder = getValue('captures-sort-order');
    const pageSize = getValue('captures-page-size');
    
    capturesState.jobFilter = jobFilter || null;
    capturesState.tagFilter = selectedTags.length > 0 ? selectedTags.join(',') : null;
    capturesState.startTime = startTime ? new Date(startTime).toISOString() : null;
    capturesState.endTime = endTime ? new Date(endTime).toISOString() : null;
    capturesState.sortOrder = sortOrder || 'desc';
    capturesState.pageSize = parseInt(pageSize) || 16;
    capturesState.currentPage = 1;
    
    // Show/hide reset button
    const hasFilters = jobFilter || selectedTags.length > 0 || startTime || endTime || capturesState.favoritesOnly;
    document.getElementById('captures-filter-reset').style.display = hasFilters ? '' : 'none';
    
    loadCapturesPage();
}

function clearCaptureFilters() {
    setValue('captures-job-filter', '');
    clearTagFilter('captures-tag-filter-wrap');
    setValue('captures-start-time', '');
    setValue('captures-end-time', '');
    document.getElementById('captures-filter-reset').style.display = 'none';
    capturesState.jobFilter = null;
    capturesState.tagFilter = null;
    capturesState.startTime = null;
    capturesState.endTime = null;
    capturesState.favoritesOnly = false;
    capturesState.currentPage = 1;
    const favBtn = document.getElementById('captures-fav-filter');
    if (favBtn) favBtn.classList.remove('active');
    loadCapturesPage();
}

async function showCapturePreview(captureId) {
    try {
        const capture = await apiRequest(`/captures/${captureId}`);
        
        capturesState.currentCaptureId = captureId;
        
        // Populate modal
        document.getElementById('capture-preview-image').src = `${API_BASE}/captures/${captureId}/image`;
        document.getElementById('capture-detail-job').innerHTML = `<a href="/jobs/${capture.job_id}" onclick="event.preventDefault(); closeModal('capture-preview-modal'); navigateTo('/jobs/${capture.job_id}');" style="color: var(--primary-color); text-decoration: underline;">${escapeHtml(capture.job_name || 'Unknown Job')}</a>`;
        document.getElementById('capture-detail-time').textContent = formatDateTime(capture.captured_at);
        document.getElementById('capture-detail-size').textContent = formatBytes(capture.file_size);
        document.getElementById('capture-detail-path').textContent = capture.file_path;
        
        showModal('capture-preview-modal');
    } catch (error) {
        console.error('Failed to load capture preview:', error);
        showNotification(`Failed to load capture preview: ${error.message}`, 'error');
    }
}

function closeCapturePreview() {
    document.getElementById('capture-preview-modal').classList.remove('active');
    capturesState.currentCaptureId = null;
}

function deleteSingleCapture() {
    if (!capturesState.currentCaptureId) return;
    
    confirmAction('Are you sure you want to delete this capture? This action cannot be undone.', async () => {
        try {
            await apiRequest(`/captures/${capturesState.currentCaptureId}`, { method: 'DELETE' });
            showNotification('Capture deleted successfully', 'success');
            closeCapturePreview();
            loadCapturesPage();
        } catch (error) {
            console.error('Failed to delete capture:', error);
            showNotification('Failed to delete capture', 'error');
        }
    });
}

// Shared favorite toggle — works for both captures and videos
async function toggleFavorite(type, id, currentState) {
    const endpoint = type === 'captures' ? '/captures/favorite' : '/videos/favorite';
    try {
        await apiRequest(endpoint, {
            method: 'POST',
            body: { ids: [id], is_favorite: !currentState }
        });
        
        const selector = type === 'captures' 
            ? `[data-capture-id="${id}"]` 
            : `.video-gallery-card[data-video-id="${id}"]`;
        const card = document.querySelector(selector);
        if (card) {
            const btn = card.querySelector('.card-fav-btn');
            if (btn) {
                const nowFav = !currentState;
                btn.classList.toggle('favorited', nowFav);
                btn.title = nowFav ? 'Remove from favorites' : 'Add to favorites';
                btn.setAttribute('onclick', `event.stopPropagation(); toggleFavorite('${type}', ${id}, ${nowFav})`);
            }
        }
    } catch (error) {
        console.error('Failed to toggle favorite:', error);
        showNotification('Failed to update favorite', 'error');
    }
}

function toggleCaptureFavoritesFilter() {
    capturesState.favoritesOnly = !capturesState.favoritesOnly;
    capturesState.currentPage = 1;
    document.getElementById('captures-fav-filter').classList.toggle('active', capturesState.favoritesOnly);
    const jobFilter = getValue('captures-job-filter');
    const startTime = getValue('captures-start-time');
    const endTime = getValue('captures-end-time');
    document.getElementById('captures-filter-reset').style.display = 
        (jobFilter || startTime || endTime || capturesState.favoritesOnly) ? '' : 'none';
    loadCapturesPage();
}

let videoFavoritesOnly = false;
let videoSharedOnly = false;

function toggleVideoFavoritesFilter() {
    videoFavoritesOnly = !videoFavoritesOnly;
    document.getElementById('video-fav-filter').classList.toggle('active', videoFavoritesOnly);
    if (videoFavoritesOnly) {
        document.getElementById('video-filter-reset').style.display = '';
    }
    loadVideos();
}

function toggleVideoSharedFilter() {
    videoSharedOnly = !videoSharedOnly;
    document.getElementById('video-share-filter').classList.toggle('active', videoSharedOnly);
    if (videoSharedOnly) {
        document.getElementById('video-filter-reset').style.display = '';
    }
    loadVideos();
}

async function viewJobCaptures(jobId) {
    // Set the job filter in state AND dropdown BEFORE switching view
    capturesState.jobFilter = jobId;
    capturesState.currentPage = 1;
    
    // Ensure dropdown is populated and set to the correct value
    const jobSelect = document.getElementById('captures-job-filter');
    if (jobSelect && jobSelect.options.length === 1) {
        const jobs = await apiRequest('/jobs/');
        jobs.forEach(job => {
            const option = document.createElement('option');
            option.value = job.id;
            option.textContent = job.name;
            jobSelect.appendChild(option);
        });
    }
    
    // Set dropdown value to match the filter
    setValue('captures-job-filter', jobId);
    
    // Switch to captures view (this calls loadCaptures which will use the filter we just set)
    navigateTo('/captures');
}



// ===== Capture Comparison =====
let compareState = {
    jobId: null,
    captureA: null,
    captureB: null,
    mode: 'side',
    firstTime: null,
    lastTime: null
};

async function openCompareModal(preselectedJobId) {
    compareState = { jobId: null, captureA: null, captureB: null, mode: 'side', firstTime: null, lastTime: null };
    document.getElementById('compare-controls').style.display = 'none';
    document.getElementById('compare-display').style.display = 'none';
    document.getElementById('compare-empty').style.display = '';
    document.getElementById('compare-empty').textContent = 'Select a job to begin comparing captures';
    setCompareMode('side');

    const select = document.getElementById('compare-job-select');
    select.innerHTML = '<option value="">Choose a job...</option>';
    try {
        const jobs = await apiRequest('/jobs/');
        const jobsWithCaptures = jobs.filter(j => j.capture_count >= 2);
        jobsWithCaptures.forEach(job => {
            const opt = document.createElement('option');
            opt.value = job.id;
            opt.textContent = `${job.name} (${job.capture_count} captures)`;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load jobs for comparison:', e);
    }

    const activeFilter = preselectedJobId || capturesState.jobFilter;
    if (activeFilter) {
        select.value = activeFilter;
    }

    showModal('compare-modal');

    if (activeFilter && select.value) {
        await onCompareJobSelected();
    }
}

async function onCompareJobSelected() {
    const jobId = parseInt(document.getElementById('compare-job-select').value);
    if (!jobId) {
        document.getElementById('compare-controls').style.display = 'none';
        document.getElementById('compare-display').style.display = 'none';
        document.getElementById('compare-empty').style.display = '';
        document.getElementById('compare-empty').textContent = 'Select a job to begin comparing captures';
        return;
    }

    compareState.jobId = jobId;
    document.getElementById('compare-empty').textContent = 'Loading captures...';

    try {
        const range = await apiRequest(`/captures/job/${jobId}/time-range`);
        if (range.count < 2) {
            document.getElementById('compare-empty').textContent = 'This job needs at least 2 captures to compare.';
            document.getElementById('compare-controls').style.display = 'none';
            document.getElementById('compare-display').style.display = 'none';
            return;
        }

        compareState.firstTime = range.first_capture_time;
        compareState.lastTime = range.last_capture_time;

        const firstLocal = isoToDatetimeLocal(range.first_capture_time);
        const lastLocal = isoToDatetimeLocal(range.last_capture_time);

        const dateA = document.getElementById('compare-date-a');
        const dateB = document.getElementById('compare-date-b');
        dateA.min = firstLocal;
        dateA.max = lastLocal;
        dateB.min = firstLocal;
        dateB.max = lastLocal;

        document.getElementById('compare-controls').style.display = '';
        document.getElementById('compare-empty').style.display = 'none';

        await compareFirstLast();
    } catch (e) {
        console.error('Failed to load capture range:', e);
        document.getElementById('compare-empty').textContent = 'Failed to load capture data.';
    }
}

async function compareFirstLast() {
    if (!compareState.jobId || !compareState.firstTime || !compareState.lastTime) return;

    document.getElementById('compare-date-a').value = isoToDatetimeLocal(compareState.firstTime);
    document.getElementById('compare-date-b').value = isoToDatetimeLocal(compareState.lastTime);

    await loadCompareCaptures(compareState.firstTime, compareState.lastTime);
}

async function onCompareDateChanged(which) {
    const dateA = document.getElementById('compare-date-a').value;
    const dateB = document.getElementById('compare-date-b').value;

    if (!dateA || !dateB) return;

    const tsA = new Date(dateA).toISOString();
    const tsB = new Date(dateB).toISOString();

    if (tsB <= tsA) {
        showNotification('Capture B must be after Capture A', 'error');
        return;
    }

    await loadCompareCaptures(tsA, tsB);
}

async function loadCompareCaptures(timestampA, timestampB) {
    try {
        const [capA, capB] = await Promise.all([
            apiRequest(`/captures/job/${compareState.jobId}/nearest`, { query: { timestamp: timestampA } }),
            apiRequest(`/captures/job/${compareState.jobId}/nearest`, { query: { timestamp: timestampB } })
        ]);

        if (capA.id === capB.id) {
            document.getElementById('compare-display').style.display = 'none';
            document.getElementById('compare-empty').style.display = '';
            document.getElementById('compare-empty').textContent = 'Both dates resolve to the same capture. Try a wider range.';
            return;
        }

        compareState.captureA = capA;
        compareState.captureB = capB;

        const imgUrlA = `${API_BASE}/captures/${capA.id}/image`;
        const imgUrlB = `${API_BASE}/captures/${capB.id}/image`;
        const labelA = formatDateTime(capA.captured_at);
        const labelB = formatDateTime(capB.captured_at);

        // Side by side
        document.getElementById('compare-img-a').src = imgUrlA;
        document.getElementById('compare-img-b').src = imgUrlB;
        document.getElementById('compare-label-a').textContent = labelA;
        document.getElementById('compare-label-b').textContent = labelB;

        // Slider
        document.getElementById('compare-slider-img-a').src = imgUrlA;
        document.getElementById('compare-slider-img-b').src = imgUrlB;
        document.getElementById('compare-slider-label-a').textContent = labelA;
        document.getElementById('compare-slider-label-b').textContent = labelB;

        document.getElementById('compare-display').style.display = '';
        document.getElementById('compare-empty').style.display = 'none';

        updateSliderPosition(0.5);
    } catch (e) {
        console.error('Failed to load comparison captures:', e);
        showNotification('Failed to load captures for comparison', 'error');
    }
}

function setCompareMode(mode) {
    compareState.mode = mode;

    document.getElementById('compare-mode-side').classList.toggle('active', mode === 'side');
    document.getElementById('compare-mode-slider').classList.toggle('active', mode === 'slider');

    document.getElementById('compare-side-by-side').style.display = mode === 'side' ? '' : 'none';
    document.getElementById('compare-slider').style.display = mode === 'slider' ? '' : 'none';

    if (mode === 'slider') {
        const imgB = document.getElementById('compare-slider-img-b');
        if (imgB.complete && imgB.naturalWidth) {
            initSliderWidth();
        } else {
            imgB.onload = initSliderWidth;
        }
    }
}

function initSliderWidth() {
    const container = document.getElementById('compare-slider-container');
    if (container) {
        container.style.setProperty('--slider-full-width', container.offsetWidth + 'px');
        updateSliderPosition(0.5);
    }
}

function updateSliderPosition(ratio) {
    const overlay = document.getElementById('compare-slider-overlay');
    const handle = document.getElementById('compare-slider-handle');
    if (overlay && handle) {
        const pct = (ratio * 100).toFixed(2) + '%';
        overlay.style.width = pct;
        handle.style.left = pct;
    }
}

// Slider drag interaction
(function() {
    let isDragging = false;

    function getSliderRatio(e, container) {
        const rect = container.getBoundingClientRect();
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    }

    document.addEventListener('mousedown', function(e) {
        const container = document.getElementById('compare-slider-container');
        if (container && container.contains(e.target)) {
            isDragging = true;
            updateSliderPosition(getSliderRatio(e, container));
            e.preventDefault();
        }
    });

    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;
        const container = document.getElementById('compare-slider-container');
        if (container) updateSliderPosition(getSliderRatio(e, container));
    });

    document.addEventListener('mouseup', function() {
        isDragging = false;
    });

    document.addEventListener('touchstart', function(e) {
        const container = document.getElementById('compare-slider-container');
        if (container && container.contains(e.target)) {
            isDragging = true;
            updateSliderPosition(getSliderRatio(e, container));
        }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
        if (!isDragging) return;
        const container = document.getElementById('compare-slider-container');
        if (container) {
            updateSliderPosition(getSliderRatio(e, container));
            e.preventDefault();
        }
    }, { passive: false });

    document.addEventListener('touchend', function() {
        isDragging = false;
    });
})();
