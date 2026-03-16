// API Base URL
const API_BASE = '/api';

// Current state
let currentView = 'jobs';
let currentJobId = null;
let refreshIntervals = [];
let videoRefreshInterval = null;
let confirmCallback = null;

// =============================================================================
// Theme Toggle
// =============================================================================

function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
    }
    // Default is dark (handled by :root CSS)
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    // Re-render storage charts with updated theme colors
    if (currentView === 'storage') loadStorage();
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
    const { method = 'GET', body = null, query = null } = options;
    
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
    
    const fetchOptions = {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {}
    };
    
    if (body) {
        fetchOptions.body = JSON.stringify(body);
    }
    
    const response = await fetch(url, fetchOptions);
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(error.detail || `Request failed: ${response.status}`);
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
function clearValues(ids) {
    ids.forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        
        if (element.type === 'checkbox') {
            element.checked = false;
        } else if (element.tagName === 'FORM') {
            element.reset();
        } else {
            element.value = '';
        }
    });
}

// =============================================================================
// End Universal Utilities
// =============================================================================

// Notification system
function showNotification(message, type = 'success') {
    const toast = document.getElementById('notification-toast');
    const messageEl = document.getElementById('notification-message');
    
    messageEl.textContent = message;
    toast.className = `notification-toast ${type}`;
    
    // Show toast
    setTimeout(() => toast.classList.add('show'), 10);
    
    // Hide after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

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
    container.style.display = enabled ? (opts.display || 'block') : 'none';
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
    btn.style.opacity = disabled ? '0.5' : '1';
    btn.style.cursor = disabled ? 'not-allowed' : 'pointer';
}

// =============================================================================
// SelectionManager — reusable bulk selection for card grids
// =============================================================================

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
        if (this.selected.size > 0) {
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
        controls.style.display = count > 0 ? 'flex' : 'none';
        document.getElementById(this.countId).textContent = `${count} selected`;

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
    
    // Load tags globally (needed by tag pickers in modals)
    loadTagManager();
    
    // Load initial view based on URL hash or default to jobs
    const hash = window.location.hash.slice(1); // Remove #
    const validViews = ['jobs', 'videos', 'settings'];
    const initialView = validViews.includes(hash) ? hash : 'jobs';
    switchView(initialView, false); // false = don't push, use replaceState from setupNavigation
    
    // Setup refresh intervals
    refreshIntervals.push(setInterval(loadJobs, 5000)); // Refresh jobs every 5s
    
    // Setup event listeners for job creation form
    const startInput = document.getElementById('start_datetime');
    const intervalInput = document.getElementById('interval_seconds');
    
    if (startInput) {
        startInput.addEventListener('change', updateEndDateMin);
    }
    if (intervalInput) {
        intervalInput.addEventListener('change', updateEndDateMin);
    }
    
    // Setup range checkbox
    document.getElementById('use_range').addEventListener('change', (e) => {
        const captureRange = document.getElementById('capture-range');
        const startInput = document.getElementById('video_start_datetime');
        const endInput = document.getElementById('video_end_datetime');
        
        if (e.target.checked) {
            captureRange.style.display = 'flex';
            startInput.disabled = false;
            endInput.disabled = false;
            
            // Update duration estimate for the selected time range
            setTimeout(() => updateVideoDurationEstimate(), 100);
        } else {
            captureRange.style.display = 'none';
            startInput.disabled = true;
            endInput.disabled = true;
            // Revert to showing full duration estimate
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

// Navigation
function setupNavigation() {
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const view = e.currentTarget.dataset.view;
            switchView(view, true); // true = push to history
        });
    });
    
    // Handle browser back/forward buttons
    window.addEventListener('popstate', (e) => {
        if (e.state && e.state.view) {
            switchView(e.state.view, false); // false = don't push to history
        }
    });
    
    // Set initial history state
    const initialView = currentView || 'jobs';
    history.replaceState({ view: initialView }, '', `#${initialView}`);
}

function switchView(view, pushState = true) {
    // Update navigation
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.toggle('active', link.dataset.view === view);
    });
    
    // Update content
    document.querySelectorAll('.view').forEach(v => {
        v.classList.toggle('active', v.id === `${view}-view`);
    });
    
    currentView = view;
    
    // Push to browser history if requested
    if (pushState) {
        history.pushState({ view: view }, '', `#${view}`);
    }
    
    // Load data for view
    if (view === 'jobs') loadJobs();
    if (view === 'videos') loadVideos();
    if (view === 'storage') loadStorage();
    if (view === 'settings') loadSettings();
    if (view === 'captures') loadCaptures();
}

// Jobs
async function loadJobs() {
    try {
        const jobs = await apiRequest('/jobs/');
        renderJobs(jobs);
        updateJobWarningBadge(jobs);
    } catch (error) {
        console.error('Failed to load jobs:', error);
    }
}

function renderJobs(jobs) {
    const container = document.getElementById('jobs-list');
    
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
        if (job.warning_message && job.status !== 'disabled' && job.status !== 'completed') {
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
        
        return `
        <div class="job-card" style="--i:${idx}" onclick="showJobDetails(${job.id})">
            ${thumbnailHtml}
            <div class="job-card-header">
                <div class="job-card-title">${escapeHtml(job.name)}</div>
            </div>
            <div class="job-info">
                <div><strong>Stream URL:</strong> <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; max-width: 250px; vertical-align: bottom;">${escapeHtml(getStreamHost(job.url))}</span></div>
                <div><strong>Interval:</strong> ${job.interval_seconds}s</div>
                ${timeWindowInfo}
                ${job.start_datetime ? `<div><strong>Start:</strong> ${formatDateTimeNoSeconds(job.start_datetime)}</div>` : ''}
                ${job.end_datetime ? `<div><strong>End:</strong> ${formatDateTimeNoSeconds(job.end_datetime)}</div>` : '<div><strong>Ongoing capture</strong></div>'}
                ${nextCaptureInfo}
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

async function showJobDetails(jobId) {
    try {
        const [job, capturesData] = await Promise.all([
            apiRequest(`/jobs/${jobId}`),
            apiRequest('/captures/', { query: { job_id: jobId, page: 1, page_size: 1, sort_order: 'desc' } })
        ]);
        
        const modal = document.getElementById('job-details-modal');
        const content = document.getElementById('job-details-content');
        const title = document.getElementById('job-details-title');
        
        // Update modal title with job name
        title.textContent = `${job.name} - Details`;
        
        let latestImageHtml = '';
        if (capturesData.captures && capturesData.captures.length > 0) {
            latestImageHtml = `
                <div style="margin: 1.5rem 0;">
                    <img src="${API_BASE}/captures/${capturesData.captures[0].id}/image" alt="Latest capture" style="max-width: 100%; border-radius: 0.5rem; border: 1px solid var(--border-color);">
                </div>
            `;
        }
        
        // End datetime will be set by initializeEditTimePickers if present
        
        // Determine status display
        let statusLabel, statusClass;
        if (job.warning_message && job.status !== 'disabled' && job.status !== 'completed') {
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
                    <div style="display: flex; align-items: start; gap: 0.5rem;">
                        <div>
                            <strong>Time Window Enabled</strong>
                            <p style="margin-top: 0.25rem; font-size: 0.875rem;">Captures only happen between <strong>${job.time_window_start}</strong> and <strong>${job.time_window_end}</strong> each day.</p>
                            ${job.time_window_start > job.time_window_end ? '<p style="margin-top: 0.25rem; font-size: 0.75rem; opacity: 0.8;">⏰ This window spans midnight (e.g., captures from evening to early morning)</p>' : ''}
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
        
        content.innerHTML = `
            <div style="padding: 1.5rem;">
                ${latestImageHtml}
                
                ${job.warning_message && job.status !== 'disabled' && job.status !== 'completed' ? `
                <div class="info-box" style="margin: 1rem 0; border-left-color: var(--warning-color);">
                    <div style="display: flex; align-items: start; gap: 0.5rem;">
                        <span style="font-size: 1.25rem;">⚠</span>
                        <div>
                            <strong>Capture Warning</strong>
                            <p style="margin-top: 0.25rem; font-size: 0.875rem;">${escapeHtml(job.warning_message)}</p>
                            <p style="margin-top: 0.5rem; font-size: 0.75rem; opacity: 0.8;">Verify settings for the job. The job will continue attempting captures in case this is a temporary issue.</p>
                        </div>
                    </div>
                </div>
                ` : ''}
                
                ${timeWindowHtml}
                
                <div class="job-info" style="margin-bottom: 1rem;">
                    <div><strong>Status:</strong> <span class="job-status ${statusClass}">${statusLabel}</span></div>
                    <div><strong>Start:</strong> ${formatDateTimeNoSeconds(job.start_datetime)}</div>
                    ${nextCaptureHtml}
                    ${lastCaptureHtml}
                </div>
                
                <div class="job-info" style="margin-bottom: 1.5rem; padding-top: 0.5rem; border-top: 1px solid var(--border-color);">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <strong>Captures:</strong> 
                        <a href="#" onclick="event.stopPropagation(); viewJobCaptures(${job.id}); return false;" 
                           style="color: var(--primary-color); text-decoration: none;"
                           title="View captures">
                            ${job.capture_count}
                        </a>
                        <button class="btn-icon" onclick="event.stopPropagation(); manualCapture(${job.id}, '${escapeHtml(job.name)}')" title="Take Snapshot" style="padding: 0.25rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                                <circle cx="12" cy="13" r="4"></circle>
                            </svg>
                        </button>
                        <button class="btn-icon" onclick="event.stopPropagation(); closeModal('job-details-modal'); openCompareModal(${job.id})" title="Compare Captures" style="padding: 0.25rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <rect x="2" y="2" width="20" height="20" rx="2"/>
                                <path d="M12 2v20"/>
                                <circle cx="7.5" cy="7.5" r="1.5"/>
                                <path d="M6 18l3-4 2 2 4-5 3 4"/>
                                <rect x="12" y="2" width="10" height="20" rx="2" fill="currentColor" opacity="0.15" stroke="none"/>
                            </svg>
                        </button>
                        <button class="btn-icon" onclick="event.stopPropagation(); closeModal('job-details-modal'); performMaintenanceScan(${job.id}, '${escapeHtml(job.name)}')" title="Sync" style="padding: 0.25rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="23 4 23 10 17 10"></polyline>
                                <polyline points="1 20 1 14 7 14"></polyline>
                                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                            </svg>
                        </button>
                    </div>
                    <div><strong>Storage:</strong> ${formatBytes(job.storage_size)}</div>
                    <div><strong>Path:</strong> ${escapeHtml(job.capture_path)}</div>
                </div>

                <h4 style="margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid var(--border-color);">Job Settings</h4>

                <div class="form-group" style="margin-bottom: 1.5rem;">
                    <label>Stream URL *</label>
                    <div style="display: flex; gap: 0.5rem; align-items: center;">
                        <input type="text" id="edit_url" class="form-control" value="${escapeHtml(job.url)}" required style="flex: 1;">
                        <button type="button" class="btn btn-secondary" onclick="previewStream('edit_url', 'edit-preview-result')" style="white-space: nowrap; display: flex; align-items: center; gap: 0.35rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                <circle cx="12" cy="12" r="3"></circle>
                            </svg>
                            Preview
                        </button>
                    </div>
                    <small style="color: var(--text-secondary);">HTTP or RTSP stream URL</small>
                    <div id="edit-preview-result" class="test-result"></div>
                </div>

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>End Date & Time</label>
                    <input type="datetime-local" id="edit_end_datetime" class="form-control">
                    <small style="color: var(--text-secondary);">Leave empty for ongoing capture</small>
                </div>
                
                <div class="form-group" style="margin-bottom: 1rem;">
                    <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; margin-bottom: 0.5rem;">
                        <input type="checkbox" id="edit_time_window_enabled" ${job.time_window_enabled ? 'checked' : ''} style="cursor: pointer;" onchange="toggleEditTimeWindow()">
                        <span><strong>Enable Daily Time Window</strong></span>
                    </label>
                    <small style="color: var(--text-secondary); display: block; margin-left: 1.5rem;">Restrict captures to specific hours each day</small>
                </div>
                
                <div id="edit-time-window-fields" style="display: ${job.time_window_enabled ? 'block' : 'none'}; margin-bottom: 1rem; margin-left: 1.5rem;">
                    <div style="display: flex; gap: 1rem;">
                        <div style="flex: 1;">
                            <label>Window Start Time</label>
                            <div class="time-picker-container">
                                <input type="time" id="edit_time_window_start_time" class="form-control">
                            </div>
                            <input type="hidden" id="edit_time_window_start">
                        </div>
                        <div style="flex: 1;">
                            <label>Window End Time</label>
                            <div class="time-picker-container">
                                <input type="time" id="edit_time_window_end_time" class="form-control">
                            </div>
                            <input type="hidden" id="edit_time_window_end">
                        </div>
                    </div>
                    <small style="color: var(--text-secondary); display: block; margin-top: 0.5rem;">Can span midnight (e.g., 22:00 to 02:00)</small>
                </div>

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>Capture Interval (seconds) *</label>
                    <input type="number" id="edit_interval_seconds" class="form-control" value="${job.interval_seconds}" min="10" required>
                    <small style="color: var(--text-secondary);">Minimum 10 seconds</small>
                </div>

                <div class="form-row" style="margin-bottom: 1.5rem;">
                    <div class="form-group flex-1">
                        <label>Timelapse FPS</label>
                        <input type="number" id="edit_framerate" class="form-control" value="30" min="1" max="120" required>
                        <small style="color: var(--text-secondary);">Frames per second for generated timelapse videos</small>
                    </div>
                    <div class="form-group" style="flex: 0 0 120px;">
                        <label>Warning After</label>
                        <input type="number" id="edit_warning_threshold" class="form-control" value="${job.warning_threshold || 3}" min="1" max="50">
                        <small>consecutive failures</small>
                    </div>
                </div>

                <div class="duration-estimate" id="edit-duration-estimate"></div>

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; margin-bottom: 0.5rem;">
                        <input type="checkbox" id="edit_auto_build_enabled" ${job.auto_build_enabled ? 'checked' : ''} style="cursor: pointer;" onchange="toggleEditAutoBuildFields()">
                        <span><strong>Enable Auto-Build</strong></span>
                    </label>
                    <small style="color: var(--text-secondary); display: block; margin-left: 1.5rem;">Automatically build timelapse videos on a recurring schedule</small>
                </div>

                <div id="edit-auto-build-fields" style="display: ${job.auto_build_enabled ? 'block' : 'none'}; margin-bottom: 1rem; margin-left: 1.5rem;">
                    <div class="form-group" style="margin-bottom: 0.75rem;">
                        <label>Build Interval</label>
                        <div class="auto-build-presets">
                            <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 1)">Hourly</button>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 6)">6 Hours</button>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 24)">Daily</button>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 168)">Weekly</button>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="setAutoBuildInterval('edit_auto_build_interval_hours', 720)">Monthly</button>
                        </div>
                        <div style="display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem;">
                            <input type="number" id="edit_auto_build_interval_hours" class="form-control" min="1" max="8760" value="${job.auto_build_interval_hours || 168}" style="width: 100px;">
                            <small style="color: var(--text-secondary);">hours</small>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group flex-1">
                            <label>FPS</label>
                            <input type="number" id="edit_auto_build_fps" class="form-control" min="1" max="120" value="${job.auto_build_fps || 30}">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group flex-1">
                            <label>Quality</label>
                            <select id="edit_auto_build_quality" class="form-control">
                                <option value="low" ${job.auto_build_quality === 'low' ? 'selected' : ''}>Low</option>
                                <option value="medium" ${(!job.auto_build_quality || job.auto_build_quality === 'medium') ? 'selected' : ''}>Medium</option>
                                <option value="high" ${job.auto_build_quality === 'high' ? 'selected' : ''}>High</option>
                                <option value="lossless" ${job.auto_build_quality === 'lossless' ? 'selected' : ''}>Lossless</option>
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

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>Tags</label>
                    <div class="tag-picker" id="edit-job-tags"></div>
                </div>

                <input type="hidden" id="edit_start_datetime" value="${job.start_datetime}">
                
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: space-between; align-items: center; padding-top: 1rem; border-top: 2px solid var(--border-color);">
                    <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;">
                        <button class="btn btn-primary" onclick="event.stopPropagation(); closeModal('job-details-modal'); showProcessVideoModal(${job.id}, '${escapeHtml(job.name)}')">
                            Build Timelapse
                        </button>
                        ${job.status !== 'completed' ? 
                            `<button class="btn btn-secondary" onclick="confirmCompleteJob(${job.id}, '${escapeHtml(job.name)}')">Complete</button>` : ''
                        }
                        ${job.status === 'active' || job.status === 'sleeping' ? 
                            `<button class="btn btn-warning" onclick="confirmDisableJob(${job.id}, '${escapeHtml(job.name)}')">Disable</button>` :
                            job.status === 'disabled' ?
                            `<button class="btn btn-success" onclick="confirmEnableJob(${job.id}, '${escapeHtml(job.name)}')">Enable</button>` : ''
                        }
                        <button class="btn-icon" onclick="duplicateJob(${job.id})" title="Duplicate Job" style="padding: 0.5rem;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                        <button class="btn-icon" onclick="closeModal('job-details-modal'); deleteJob(${job.id}, '${escapeHtml(job.name)}')" title="Delete Job" style="padding: 0.5rem;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="3 6 5 6 21 6"></polyline>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                <line x1="10" y1="11" x2="10" y2="17"></line>
                                <line x1="14" y1="11" x2="14" y2="17"></line>
                            </svg>
                        </button>
                    </div>
                    <button id="save-job-btn" class="btn btn-purple" onclick="saveJobChanges(${job.id})" style="font-weight: 600;" disabled>
                        Save
                    </button>
                </div>
            </div>
        `;
        
        showModal('job-details-modal');
        
        // Initialize custom time pickers for edit modal
        initializeEditTimePickers(job);
        
        // Initialize edit overlay section
        initEditJobOverlay(job);
        
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
    }
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
        capture_path: {},
        naming_pattern: {},
        time_window_enabled: { parse: 'bool' },
        time_window_start: {},
        time_window_end: {},
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
    
    // Auto-detect stream type from URL
    const stream_type = values.job_url.toLowerCase().startsWith('rtsp://') ? 'rtsp' : 'http';
    
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
        url: values.job_url,
        stream_type: stream_type,
        start_datetime: datetimeLocalToISO(values.start_datetime),
        end_datetime: values.end_datetime ? datetimeLocalToISO(values.end_datetime) : null,
        interval_seconds: values.interval_seconds,
        framerate: values.framerate,
        capture_path: values.capture_path,
        naming_pattern: values.naming_pattern,
        warning_threshold: values.warning_threshold || 3,
        time_window_enabled: values.time_window_enabled,
        time_window_start: values.time_window_enabled ? values.time_window_start : null,
        time_window_end: values.time_window_enabled ? values.time_window_end : null,
        auto_build_enabled: values.auto_build_enabled,
        auto_build_interval_hours: values.auto_build_interval_hours || 168,
        auto_build_fps: values.auto_build_fps || 30,
        auto_build_quality: values.auto_build_quality || 'medium',
        auto_build_resolution: values.auto_build_resolution || '1920x1080',
        auto_build_text_overlay: values.auto_build_enabled ? JSON.stringify(readOverlayConfig('create-ab')) : null,
        tag_ids: getSelectedTagIds('create-job-tags')
    };
    
    try {
        await apiRequest('/jobs/', { method: 'POST', body: formData });
        
        closeModal('create-job-modal');
        document.getElementById('create-job-form').reset();
        loadJobs();
        showNotification(`Job "${formData.name}" created successfully!`);
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
        'edit_stream_type',
        'edit_warning_threshold',
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
        () => updateJobStatus(jobId, 'disabled', jobName),
        { closeModalId: 'job-details-modal' }
    );
}

function confirmEnableJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to enable the job "${jobName}"? The job will start capturing images according to its schedule.`,
        () => updateJobStatus(jobId, 'active', jobName),
        { closeModalId: 'job-details-modal' }
    );
}

function confirmCompleteJob(jobId, jobName) {
    confirmAction(
        `Are you sure you want to complete the job "${jobName}"? This will set the job's end time to now and mark it as completed.`,
        () => completeJob(jobId, jobName),
        { closeModalId: 'job-details-modal' }
    );
}

async function completeJob(jobId, jobName) {
    try {
        await apiRequest(`/jobs/${jobId}`, {
            method: 'PATCH',
            body: { status: 'completed', end_datetime: new Date().toISOString() }
        });
        loadJobs();
        showNotification(`Job "${jobName}" completed successfully`);
    } catch (error) {
        console.error('Failed to complete job:', error);
        showNotification('Failed to complete job', 'error');
    }
}

async function updateJobStatus(jobId, status, jobName) {
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { status } });
        loadJobs();
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
        closeModal('job-details-modal');
        loadJobs();
        showNotification('End time updated successfully');
    } catch (error) {
        console.error('Failed to update end time:', error);
        showNotification(error.message || 'Failed to update end time', 'error');
    }
}

async function updateJobUrl(jobId) {
    const url = document.getElementById('edit_url').value.trim();
    
    if (!url) {
        showNotification('URL cannot be empty', 'error');
        return;
    }
    
    const stream_type = url.toLowerCase().startsWith('rtsp://') ? 'rtsp' : 'http';
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: { url, stream_type } });
        showNotification('Stream URL updated successfully');
        showJobDetails(jobId);
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
        showJobDetails(jobId);
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
    const url = document.getElementById('edit_url').value.trim();
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
    
    // Auto-detect stream type from URL
    const stream_type = url.toLowerCase().startsWith('rtsp://') ? 'rtsp' : 'http';
    
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
        auto_build_text_overlay: JSON.stringify(readOverlayConfig('edit-ab'))
    };
    
    try {
        await apiRequest(`/jobs/${jobId}`, { method: 'PATCH', body: updateData });
        await loadJobs();
        closeModal('job-details-modal');
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
                loadJobs();
                showNotification(`Job "${jobName}" and all captures deleted successfully`);
            } catch (error) {
                console.error('Failed to delete job:', error);
                showNotification(`Failed to delete job "${jobName}"`, 'error');
            }
        }
    );
}

async function testUrl() {
    previewStream('job_url', 'test-result');
}

async function previewStream(urlInputId, resultDivId) {
    const url = document.getElementById(urlInputId).value;
    const resultDiv = document.getElementById(resultDivId);
    
    if (!url) {
        showNotification('Please enter a URL first', 'warning');
        return;
    }
    
    resultDiv.innerHTML = '<p style="color: var(--text-secondary);">Loading preview...</p>';
    resultDiv.className = 'test-result';
    
    try {
        const result = await apiRequest('/jobs/test-url', { method: 'POST', query: { url } });
        
        if (result.success) {
            resultDiv.className = 'test-result';
            resultDiv.innerHTML = `
                <img src="${result.image_data}" alt="Preview capture" style="max-width: 100%; margin-top: 10px; border: 1px solid var(--border-color); border-radius: 4px;">
            `;
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
    clearTagFilter('video-tag-filter-wrap');
    videoFavoritesOnly = false;
    const favBtn = document.getElementById('video-fav-filter');
    if (favBtn) favBtn.classList.remove('active');
    filterVideos();
}

function filterVideos(opts = {}) {
    const search = (document.getElementById('video-search').value || '').toLowerCase();
    const yearFilter = document.getElementById('video-year-filter').value;
    const monthFilter = document.getElementById('video-month-filter').value;
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
    
    if (videoFavoritesOnly) {
        filtered = filtered.filter(v => v.is_favorite);
    }
    
    if (selectedTags.length > 0) {
        filtered = filtered.filter(v => v.tags && selectedTags.every(tid => v.tags.some(t => t.id === tid)));
    }
    
    // Show/hide reset button
    const hasFilters = search || yearFilter || monthFilter !== '' || videoFavoritesOnly || selectedTags.length > 0;
    document.getElementById('video-filter-reset').style.display = hasFilters ? '' : 'none';
    
    const countEl = document.getElementById('video-count');
    if (filtered.length !== allVideos.length) {
        countEl.textContent = `${filtered.length} of ${allVideos.length}`;
    } else {
        countEl.textContent = `${allVideos.length} videos`;
    }
    
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
                <div class="video-gallery-job">${video.job_name ? escapeHtml(video.job_name) : 'No job'}${video.build_source === 'auto' ? ' <span class="auto-build-badge">Auto</span>' : ''}</div>
                ${video.tags && video.tags.length ? `<div class="card-tags">${video.tags.map(t => tagChipHTML(t, true)).join('')}</div>` : ''}
            </div>
        </div>`;
    }).join('');
    
    container.insertAdjacentHTML('beforeend', html);
}

async function openVideoDetail(videoId) {
    try {
        const video = await apiRequest(`/videos/${videoId}`);
        
        const modal = document.getElementById('video-detail-modal');
        const title = document.getElementById('video-detail-title');
        const meta = document.getElementById('video-detail-meta');
        const actions = document.getElementById('video-detail-actions');
        const player = document.getElementById('video-detail-player');
        const source = document.getElementById('video-detail-source');
        
        title.textContent = video.name;
        
        // Set up video player
        if (video.status === 'completed') {
            source.src = `${API_BASE}/videos/${video.id}/download`;
            player.load();
            player.style.display = 'block';
        } else {
            player.style.display = 'none';
        }
        
        // Build metadata in 3 dense rows of 4 columns each
        let metaHtml = '';
        
        // Row 1: Job, Duration, Size, Status
        const jobVal = video.job_name
            ? (video.job_id
                ? `<a href="#" class="job-link" onclick="event.preventDefault(); closeVideoDetail(); navigateToJob(${video.job_id})">${escapeHtml(video.job_name)}</a>`
                : escapeHtml(video.job_name))
            : 'None';
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
        if (video.status === 'completed') {
            actionsHtml += `<a href="${API_BASE}/videos/${video.id}/download" class="btn btn-primary btn-sm">Download</a>`;
            actionsHtml += `<button class="btn btn-secondary btn-sm" onclick="showSharePanel(${video.id})">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
                Share</button>`;
        }
        actionsHtml += `<button class="btn btn-danger btn-sm" onclick="deleteVideoFromDetail(${video.id}, '${escapeHtml(video.name)}')">Delete</button>`;
        actions.innerHTML = actionsHtml;
        
        // Load shared links for this video
        const shareContainer = document.getElementById('video-detail-share');
        if (shareContainer) shareContainer.innerHTML = '';
        
        modal.classList.add('active');
    } catch (error) {
        console.error('Failed to load video details:', error);
    }
}

function closeVideoDetail() {
    const modal = document.getElementById('video-detail-modal');
    const player = document.getElementById('video-detail-player');
    player.pause();
    player.currentTime = 0;
    modal.classList.remove('active');
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
            } catch (error) {
                showNotification(`Failed to delete video "${videoName}"`, 'error');
            }
        }
    );
}

// ── Shared Links ──────────────────────────────────────────────────────────

async function showSharePanel(videoId) {
    const container = document.getElementById('video-detail-share');
    if (!container) return;

    container.innerHTML = `
        <div class="share-panel">
            <div class="share-panel-header">
                <strong>Shared Links</strong>
            </div>
            <div class="share-create-row">
                <select id="share-expiry" class="form-control" style="max-width:160px;font-size:0.8rem;">
                    <option value="">Never expires</option>
                    <option value="1">1 hour</option>
                    <option value="24">24 hours</option>
                    <option value="168">7 days</option>
                    <option value="720">30 days</option>
                </select>
                <button class="btn btn-purple btn-sm" onclick="createSharedLink(${videoId})">Create Link</button>
            </div>
            <div id="share-links-list" class="share-links-list">
                <span style="color:var(--text-secondary);font-size:0.8rem;">Loading...</span>
            </div>
        </div>
    `;

    await loadSharedLinks(videoId);
}

async function loadSharedLinks(videoId) {
    const list = document.getElementById('share-links-list');
    if (!list) return;
    try {
        const links = await apiRequest('/shared/', { query: { video_id: videoId } });
        if (links.length === 0) {
            list.innerHTML = '<span style="color:var(--text-secondary);font-size:0.8rem;">No shared links yet</span>';
            return;
        }
        list.innerHTML = links.map(link => {
            const url = `${window.location.origin}/shared/${link.token}`;
            const expiry = link.expires_at
                ? `Expires ${formatDateTimeNoSeconds(link.expires_at)}`
                : 'Never expires';
            const isExpired = link.expires_at && new Date(link.expires_at) < new Date();
            return `
                <div class="share-link-item ${isExpired ? 'expired' : ''}">
                    <div class="share-link-url">
                        <input type="text" value="${url}" readonly onclick="this.select()">
                        <button class="btn btn-secondary btn-sm" onclick="copyShareLink(this, '${url}')" title="Copy">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        </button>
                    </div>
                    <div class="share-link-meta">
                        <span>${expiry}${isExpired ? ' (expired)' : ''}</span>
                        <button class="tag-action-btn tag-action-delete" onclick="revokeSharedLink(${link.id}, ${link.video_id})" title="Revoke">
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        list.innerHTML = '<span style="color:#ef4444;font-size:0.8rem;">Failed to load links</span>';
    }
}

async function createSharedLink(videoId) {
    const expirySelect = document.getElementById('share-expiry');
    const expiryHours = expirySelect?.value ? parseInt(expirySelect.value) : null;
    try {
        await apiRequest('/shared/', {
            method: 'POST',
            body: { video_id: videoId, expires_in_hours: expiryHours }
        });
        await loadSharedLinks(videoId);
        showNotification('Shared link created');
    } catch (error) {
        showNotification(error.message || 'Failed to create link', 'error');
    }
}

function copyShareLink(btn, url) {
    navigator.clipboard.writeText(url).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '✓';
        setTimeout(() => { btn.innerHTML = orig; }, 1500);
    });
}

async function revokeSharedLink(linkId, videoId) {
    try {
        await apiRequest(`/shared/${linkId}`, { method: 'DELETE' });
        await loadSharedLinks(videoId);
        showNotification('Link revoked');
    } catch (error) {
        showNotification('Failed to revoke link', 'error');
    }
}

async function showProcessVideoModal(jobId, jobName) {
    try {
        // Reset form
        document.getElementById('process-video-form').reset();
        document.getElementById('use_range').checked = false;
        document.getElementById('capture-range').style.display = 'none';
        document.getElementById('video-duration-estimate').innerHTML = '';
        document.getElementById('available-range-info').style.display = 'none';
        // Reset text overlay
        const buildOverlayContainer = document.getElementById('build-overlay-container');
        if (buildOverlayContainer) buildOverlayContainer.innerHTML = '';
        window._overlayPreviewPath = null;
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
            document.getElementById('video_output_path').value = '/timelapses';
            
            // Populate job dropdown
            await populateJobSelector();
            
            // Disable create button until a job is selected
            const createBtn = document.getElementById('create-video-btn');
            if (createBtn) {
                setButtonState(createBtn, true);
            }
        }
        
        showModal('process-video-modal');
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
        document.getElementById('video-duration-estimate').innerHTML = '';
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
        const thumbUrl = `${API_BASE}/captures/${cap.id}/thumbnail`;
        img.src = thumbUrl;
        img._originalSrc = thumbUrl;
        document.getElementById('job-preview-label').textContent = `Latest capture: ${formatDateTime(cap.captured_at)}`;
        previewImage.style.display = 'flex';
        if (previewPlaceholder) previewPlaceholder.style.display = 'none';
        // Store file_path for text overlay preview
        window._overlayPreviewPath = cap.file_path;
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
    document.getElementById('video_output_path').value = '/timelapses';
    
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
    document.getElementById('capture-range').style.display = 'none';
    
    // Calculate and display initial duration
    updateVideoDurationEstimate();
    
    // Enable the create button now that a job is loaded
    const createBtn = document.getElementById('create-video-btn');
    if (createBtn) {
        createBtn.disabled = false;
        createBtn.style.opacity = '1';
        createBtn.style.cursor = 'pointer';
    }
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
    if (useRange && window.firstCaptureTime && window.lastCaptureTime) {
        const startTimeInput = document.getElementById('video_start_datetime');
        const endTimeInput = document.getElementById('video_end_datetime');
        
        if (startTimeInput?.value && endTimeInput?.value) {
            const customStart = new Date(startTimeInput.value);
            const customEnd = new Date(endTimeInput.value);
            
            if (customEnd < window.firstCaptureTime || customStart > window.lastCaptureTime) {
                document.getElementById('video-duration-estimate').innerHTML = 
                    '<p style="color: var(--danger-color); font-weight: 600;"><strong>Warning:</strong> Selected time range is outside available captures!</p>' +
                    '<p style="color: var(--danger-color); font-size: 0.875rem;">Available: ' + 
                    formatDateTime(window.firstCaptureTime.toISOString()) + ' - ' + 
                    formatDateTime(window.lastCaptureTime.toISOString()) + '</p>';
                
                if (createBtn) {
                    setButtonState(createBtn, true);
                }
                return;
            }
        }
    }
    
    if (captureCount === 0) {
        const message = useRange 
            ? '<p style="color: var(--danger-color); font-weight: 600;"><strong>Warning:</strong> No captures in selected time range!</p>'
            : '<p style="color: var(--text-secondary);">No captures available for this job yet.</p>';
        document.getElementById('video-duration-estimate').innerHTML = message;
        
        // Disable create button when no captures
        if (createBtn) {
            setButtonState(createBtn, true);
        }
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
        <p style="font-size: 0.875rem; color: var(--text-secondary);">${captureCount} captures at ${framerate} FPS</p>
        <p>${minutes}m ${seconds}s</p>
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
    const label = opts.label || 'Text Overlay';
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
            <div class="form-group">
                <label>Overlay Text</label>
                <input type="text" id="${prefix}-overlay-text" class="form-control" value="{job_name}" placeholder="{job_name} - {date} {time}"${inputEvent}>
                <small style="color: var(--text-secondary);">Variables: <code>{job_name}</code> <code>{date}</code> <code>{time}</code> <code>{datetime}</code> <code>{frame}</code> <code>{total_frames}</code></small>
            </div>
            <div class="form-row" style="gap:0.5rem; align-items:flex-start;">
                <div style="flex:1; min-width:0; display:flex; flex-direction:column; gap:0.4rem;">
                    <div class="form-row" style="gap:0.5rem;">
                        <div class="form-group flex-1">
                            <label>Font</label>
                            <select id="${prefix}-overlay-font" class="form-control"${inputEvent}>${fontOpts}</select>
                        </div>
                        <div class="form-group" style="width: 65px;">
                            <label>Size</label>
                            <input type="number" id="${prefix}-overlay-size" class="form-control" value="48" min="8" max="200"${inputEvent}>
                        </div>
                        <div class="form-group" style="width: 45px;">
                            <label>Bold</label>
                            <div style="display:flex;align-items:center;height:32px;">
                                <input type="checkbox" id="${prefix}-overlay-bold"${onchangeAttr}>
                            </div>
                        </div>
                        <div class="form-group" style="width: 50px;">
                            <label>Color</label>
                            <input type="color" id="${prefix}-overlay-color" value="#FFFFFF" class="form-control" style="padding:2px;height:32px;"${inputEvent}>
                        </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.35rem;">
                        <input type="checkbox" id="${prefix}-overlay-bg" checked${onchangeAttr}>
                        <span style="font-size:0.8rem;">BG</span>
                        <input type="color" id="${prefix}-overlay-bg-color" value="#000000" class="form-control" style="padding:2px;height:24px;width:32px;"${inputEvent}>
                        <input type="range" id="${prefix}-overlay-bg-opacity" min="0" max="100" value="50" style="flex:1; height:16px;"${inputEvent}>
                        <span id="${prefix}-overlay-opacity-label" style="width:28px;text-align:right;font-size:0.7rem;color:var(--text-secondary);">50%</span>
                    </div>
                </div>
                <div class="form-group" style="margin:0;">
                    <label>Position</label>
                    <div class="overlay-position-grid" id="${prefix}-overlay-grid">${gridBtns}</div>
                </div>
            </div>`;

    const previewHtml = showPreview ? `
            <div class="overlay-preview-panel" id="${prefix}-overlay-preview-panel">
                <img id="${prefix}-overlay-preview-img" alt="Overlay preview" onclick="openOverlayLightbox(this)" style="border-radius: var(--radius-lg); border: 1px solid var(--border-color); display: none;" title="Click to enlarge">
                <div id="${prefix}-overlay-preview-placeholder" style="width:100%; aspect-ratio:16/9; background:var(--surface-color); border:1px dashed var(--border-color); border-radius:var(--radius-lg); display:flex; align-items:center; justify-content:center; color:var(--text-secondary); font-size:0.8rem;">
                    Loading preview…
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
        </div>
        <div id="${prefix}-overlay-fields" style="display: none;">
            ${fieldsInner}
        </div>`;
}

function openOverlayLightbox(imgEl) {
    if (!imgEl || !imgEl.src) return;
    const lb = document.createElement('div');
    lb.className = 'overlay-lightbox';
    lb.innerHTML = `<img src="${imgEl.src}" alt="Overlay preview">`;
    lb.addEventListener('click', () => lb.remove());
    document.addEventListener('keydown', function esc(e) {
        if (e.key === 'Escape') { lb.remove(); document.removeEventListener('keydown', esc); }
    });
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
        fields.style.display = cb.checked ? 'block' : 'none';
        if (cb.checked && !_overlayFontsLoaded) loadOverlayFonts();
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
        font_size: parseInt(document.getElementById(`${prefix}-overlay-size`)?.value) || 48,
        bold: document.getElementById(`${prefix}-overlay-bold`)?.checked || false,
        color: document.getElementById(`${prefix}-overlay-color`)?.value || '#FFFFFF',
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
    if (fields) fields.style.display = config.enabled ? 'block' : 'none';
    if (el('text')) el('text').value = config.text || '';
    if (el('font')) el('font').value = config.font || 'DejaVu Sans';
    if (el('size')) el('size').value = config.font_size || 48;
    if (el('bold')) el('bold').checked = !!config.bold;
    if (el('color')) el('color').value = config.color || '#FFFFFF';
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
        label: 'Text Overlay',
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
    const imagePath = window._overlayPreviewPath;
    if (!config || !imagePath) { resetOverlayPreview(); return; }

    try {
        const resp = await fetch(`${API_BASE}/videos/text-overlay-preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Referer': window.location.href },
            body: JSON.stringify({ image_path: imagePath, config, job_name: window._overlayJobName || 'Sample Job' })
        });
        if (!resp.ok) throw new Error('Preview failed');
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
        label: 'Text Overlay',
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
        label: 'Text Overlay',
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

    try {
        // Use the job's latest capture for the preview
        const capsData = await apiRequest('/captures/', { query: { job_id: job.id, page_size: 1, sort_order: 'desc' } });
        if (capsData.captures && capsData.captures.length > 0) {
            const cap = capsData.captures[0];
            img._filePath = cap.file_path;
            img._originalSrc = `${API_BASE}/captures/${cap.id}/thumbnail`;
            img.src = img._originalSrc;
            img.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
            // If overlay is already enabled, render preview
            const cb = document.getElementById(`${prefix}-overlay-enabled`);
            if (cb && cb.checked) {
                setTimeout(() => updateGenericOverlayPreview(prefix, job), 100);
            }
        } else {
            // No captures — try to fetch from the job's URL
            showOverlayPreviewPlaceholder(prefix, 'No captures yet', job.url);
        }
    } catch (e) {
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

    if (placeholder) placeholder.innerHTML = '<div style="font-size:0.8rem; color:var(--text-secondary);">Fetching…</div>';

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
    // Need either file_path or base64
    const hasFile = img._filePath;
    const hasBase64 = img._base64;
    if (!hasFile && !hasBase64) return;

    const jobName = prefix === 'create-ab'
        ? (document.getElementById('job_name')?.value || 'New Job')
        : (window._editOverlayJob?.name || 'Sample Job');

    try {
        const body = { config, job_name: jobName };
        if (hasFile) body.image_path = img._filePath;
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
    // Delegate to URL-based previewer if we have base64 but no file
    if (!img._filePath && img._base64) {
        return updateOverlayFromUrl(prefix);
    }
    if (!img._filePath) return;
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
            body: JSON.stringify({ image_path: img._filePath, config, job_name: job.name || 'Sample Job' })
        });
        if (!resp.ok) throw new Error('Preview failed');
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
        output_path: document.getElementById('video_output_path').value.trim() || null,
        start_time: useRange ? datetimeLocalToISO(document.getElementById('video_start_datetime').value) : null,
        end_time: useRange ? datetimeLocalToISO(document.getElementById('video_end_datetime').value) : null,
        text_overlay: readOverlayConfig('build')
    };
    
    try {
        await apiRequest('/videos/', { method: 'POST', body: formData });
        closeModal('process-video-modal');
        document.getElementById('process-video-form').reset();
        switchView('videos');
        showNotification('Video processing started');
    } catch (error) {
        console.error('Failed to process video:', error);
        showNotification(`Failed to start processing: ${error.message || 'Unknown error'}`, 'error');
    }
}

function playVideo(videoId, videoName) {
    openVideoDetail(videoId);
}

function closeVideoPlayer() {
    closeVideoDetail();
}

function navigateToJob(jobId) {
    // Switch to jobs view
    switchView('jobs');
    // Open job details modal
    setTimeout(() => showJobDetails(jobId), 100);
}

async function deleteVideo(videoId, videoName) {
    deleteVideoFromDetail(videoId, videoName);
}

// Modal management
function showModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.add('active');
    
    // Always scroll to top when opening any modal
    const modalContent = modal.querySelector('.modal-content');
    if (modalContent) {
        modalContent.scrollTop = 0;
    }
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
    
    // Clear form when closing create job modal
    if (modalId === 'create-job-modal') {
        document.getElementById('create-job-form').reset();
        document.getElementById('test-result').innerHTML = '';
        // Reset datetime to now for next time modal opens
        setDefaultStartTime();
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
    }
}

// Close the topmost active modal on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        // Check custom modals first (video detail, confirm dialog)
        const videoDetail = document.getElementById('video-detail-modal');
        if (videoDetail && videoDetail.classList.contains('active')) {
            closeVideoDetail();
            return;
        }
        
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

function showCreateJobModal() {
    // Reset the form to clear any previous values
    document.getElementById('create-job-form').reset();
    
    // Clear test results and estimates
    document.getElementById('test-result').innerHTML = '';
    document.getElementById('duration-estimate').innerHTML = '';
    
    // Set default datetime to now
    setDefaultStartTime();
    
    // Set default values for capture path and naming pattern
    document.getElementById('capture_path').value = '/captures';
    document.getElementById('naming_pattern').value = '{job_name}_{num:06d}_{timestamp}';
    
    // Set initial min for end date
    updateEndDateMin();
    
    // Reset time window
    document.getElementById('time_window_enabled').checked = false;
    document.getElementById('time-window-fields').style.display = 'none';
    
    // Reset auto-build
    document.getElementById('auto_build_enabled').checked = false;
    document.getElementById('auto-build-fields').style.display = 'none';
    
    showModal('create-job-modal');
    
    // Render tag picker
    renderTagPicker('create-job-tags');
    
    // Trigger initial duration estimate with default values
    setTimeout(() => {
        updateDurationEstimate();
    }, 100);
}

async function duplicateJob(jobId) {
    try {
        const job = await apiRequest(`/jobs/${jobId}`);
        
        closeModal('job-details-modal');
        
        // Open create modal and pre-fill with job data
        document.getElementById('create-job-form').reset();
        document.getElementById('test-result').innerHTML = '';
        document.getElementById('duration-estimate').innerHTML = '';
        
        // Pre-fill fields from source job
        document.getElementById('job_name').value = `${job.name} (Copy)`;
        document.getElementById('job_url').value = job.url;
        document.getElementById('interval_seconds').value = job.interval_seconds;
        document.getElementById('framerate').value = job.framerate || 30;
        document.getElementById('warning_threshold').value = job.warning_threshold || 3;
        document.getElementById('capture_path').value = '/captures';
        document.getElementById('naming_pattern').value = job.naming_pattern || '{job_name}_{num:06d}_{timestamp}';
        
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
            twFields.style.display = '';
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
            twFields.style.display = 'none';
        }
        
        // Copy auto-build settings
        const abEnabled = document.getElementById('auto_build_enabled');
        const abFields = document.getElementById('auto-build-fields');
        if (job.auto_build_enabled) {
            abEnabled.checked = true;
            abFields.style.display = 'block';
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
                } catch (e) {
                    console.warn('Failed to parse auto_build_text_overlay for duplicate:', e);
                }
            }
        } else {
            abEnabled.checked = false;
            abFields.style.display = 'none';
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

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
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
        
        displayElement.innerHTML = `<h4>Estimated Video Duration @ ${framerate} FPS (Ongoing)${windowNote}</h4>`;
        
        durations.forEach(dur => {
            const captures = calculateCaptures(dur.seconds);
            displayElement.innerHTML += `
                <div class="duration-row">
                    <strong>${dur.label}</strong>
                    <div class="duration-table">
                        <div class="duration-item">
                            <div class="duration-fps">Captures</div>
                            <div class="duration-time">${captures.toLocaleString()}</div>
                        </div>
                        <div class="duration-item">
                            <div class="duration-fps">Duration</div>
                            <div class="duration-time">${formatDuration(captures / framerate)}</div>
                        </div>
                    </div>
                </div>
            `;
        });
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
                await showJobDetails(jobId);
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
    modal.classList.add('active');
    
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
                <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="closeMaintenance()">Close</button>
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
                <button class="btn btn-primary" style="margin-top: 1.5rem;" onclick="closeMaintenance()">Close</button>
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
                <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
                    <button class="btn btn-secondary" onclick="closeMaintenance()">Cancel</button>
                    <button class="btn btn-primary" onclick="confirmMaintenanceSubmit(${jobId}, '${escapeHtml(jobName)}')">
                        Submit
                    </button>
                </div>
                ` : `
                <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
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
                <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="closeMaintenance()">Close</button>
            </div>
        `;
        showNotification('Maintenance failed', 'error');
    }
}

function confirmMaintenanceCleanup(jobId, jobName) {
    confirmAction(
        `Are you absolutely sure you want to remove ${maintenanceData.missing_count} database record(s) for missing files? This action cannot be undone.`,
        () => performMaintenanceCleanup(jobId, jobName)
    );
}

function confirmMaintenanceImport(jobId, jobName) {
    confirmAction(
        `Import ${maintenanceData.orphaned_count} orphaned file(s) into the database? Timestamps will be extracted from the files.`,
        () => performMaintenanceImport(jobId, jobName)
    );
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
                <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="closeMaintenance()">Close</button>
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
                <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="closeMaintenance()">Close</button>
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

function populateHourOptions(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    
    // Clear all existing options
    while (select.options.length > 0) {
        select.remove(0);
    }
    
    for (let i = 0; i < 24; i++) {
        const option = document.createElement('option');
        option.value = i.toString().padStart(2, '0');
        option.textContent = i.toString().padStart(2, '0');
        select.appendChild(option);
    }
    
    // Default to 00 if no value is set
    if (!select.value) {
        select.value = '00';
    }
}

function populateMinuteOptions(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    
    // Clear all existing options
    while (select.options.length > 0) {
        select.remove(0);
    }
    
    for (let i = 0; i < 60; i++) {
        const option = document.createElement('option');
        option.value = i.toString().padStart(2, '0');
        option.textContent = i.toString().padStart(2, '0');
        select.appendChild(option);
    }
    
    // Default to 00 if no value is set
    if (!select.value) {
        select.value = '00';
    }
}

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
        purple: style.getPropertyValue('--accent-purple').trim() || '#8b5cf6',
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
                legend: { position: 'bottom', labels: { color: colors.textSecondary, padding: 16, usePointStyle: true, pointStyleWidth: 12 } },
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
                `App Data (${formatBytes(appUsed)})`,
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
                legend: { position: 'bottom', labels: { color: colors.textSecondary, padding: 16, usePointStyle: true, pointStyleWidth: 12 } },
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
                    labels: { color: colors.textSecondary, usePointStyle: true, pointStyleWidth: 12, padding: 16 }
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

    // Load webhook settings
    loadWebhookSettings();
    loadTagManager();
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

function markWebhookDirty() {
    webhookDirty = true;
    const btn = document.getElementById('webhook-save-btn');
    if (btn) btn.disabled = false;
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
        updateWebhookToggleState();
        webhookDirty = false;
        const btn = document.getElementById('webhook-save-btn');
        if (btn) btn.disabled = true;
    } catch (error) {
        console.error('Failed to load webhook settings:', error);
    }
}

async function saveWebhookSettings() {
    const url = document.getElementById('webhook-url').value.trim();
    const template = document.getElementById('webhook-template').value.trim();
    const defaultTemplate = '{"title": "{title}", "message": "{message}"}';

    const settings = {
        webhook_enabled: document.getElementById('webhook-enabled').checked && !!url,
        webhook_url: url,
        webhook_payload_template: template || defaultTemplate,
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
        <button class="btn btn-sm btn-purple" onclick="saveTagEdit(${tagId})">Save</button>
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

    // Store callback and containerId for toggle/inline-create to reference
    container._tagOnToggle = onToggle;
    container._tagPickerId = containerId;

    let html = allTags.map(tag => {
        const isSelected = selectedTagIds.includes(tag.id);
        return `<span class="tag-chip ${isSelected ? 'selected' : ''}" data-tag-id="${tag.id}"
            style="background:${tag.color}22;color:${tag.color};border:1px solid ${tag.color}44;cursor:pointer;opacity:${isSelected ? '1' : '0.4'};"
            onclick="toggleTagInPicker(this)">
            <span style="width:6px;height:6px;border-radius:50%;background:${tag.color};display:inline-block;"></span>
            ${escapeHtml(tag.name)}</span>`;
    }).join('');

    // Inline create button
    html += `<span class="tag-chip tag-inline-create" onclick="showInlineTagCreate('${containerId}')" title="Create new tag"
        style="cursor:pointer;opacity:0.5;border:1px dashed var(--border-color);color:var(--text-secondary);background:transparent;">＋ New</span>`;

    container.innerHTML = html;
}

function showInlineTagCreate(containerId) {
    const container = document.getElementById(containerId);
    if (!container || container.querySelector('.tag-inline-form')) return;

    const createBtn = container.querySelector('.tag-inline-create');
    if (createBtn) createBtn.remove();

    const form = document.createElement('div');
    form.className = 'tag-inline-form';
    form.innerHTML = `
        <input type="text" class="form-control" placeholder="Tag name..." maxlength="50" style="width:100px;font-size:0.75rem;padding:0.2rem 0.4rem;">
        <div class="color-swatch-row" style="gap:2px;">${TAG_COLORS.slice(0, 10).map(c =>
            `<span class="color-swatch${c === '#6366f1' ? ' selected' : ''}" style="background:${c};width:16px;height:16px;" data-color="${c}" onclick="selectSwatch(this)"></span>`
        ).join('')}</div>
        <button class="btn btn-purple btn-sm" style="font-size:0.7rem;padding:0.15rem 0.5rem;" onclick="submitInlineTag('${containerId}')">Add</button>
        <button class="btn btn-secondary btn-sm" style="font-size:0.7rem;padding:0.15rem 0.4rem;" onclick="cancelInlineTagCreate('${containerId}')">✕</button>
    `;
    container.appendChild(form);
    form.querySelector('input').focus();
    form.querySelector('input').addEventListener('keydown', e => {
        if (e.key === 'Enter') submitInlineTag(containerId);
        if (e.key === 'Escape') cancelInlineTagCreate(containerId);
    });
}

function cancelInlineTagCreate(containerId) {
    const container = document.getElementById(containerId);
    const onToggle = container?._tagOnToggle || null;
    renderTagPicker(containerId, getSelectedTagIds(containerId), onToggle);
}

async function submitInlineTag(containerId) {
    const container = document.getElementById(containerId);
    const form = container?.querySelector('.tag-inline-form');
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
        // Fire callback for the newly added tag
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

// Nav Warning Badge
function updateJobWarningBadge(jobs) {
    const jobsLink = document.querySelector('.nav-link[data-view="jobs"]');
    if (!jobsLink) return;

    // Remove existing badge
    const existing = jobsLink.querySelector('.nav-warning-badge');
    if (existing) existing.remove();

    // Check if any active/sleeping job has a warning
    const hasWarnings = jobs && jobs.some(j => j.warning_message && j.status !== 'disabled' && j.status !== 'completed');
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
    controlsId: 'captures-selection-controls',
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
    modal.classList.add('active');
    
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
                <button class="btn btn-secondary" style="margin-top: 1rem;" onclick="closeMaintenance()">Close</button>
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
                <button class="btn btn-primary" style="margin-top: 1.5rem;" onclick="closeMaintenance()">Close</button>
            </div>
        `;
        return;
    }
    
    const groupsHtml = data.orphaned_groups.map(group => {
        const typeLabel = group.type === 'both' ? 'Files + DB Records' 
            : group.type === 'database' ? 'DB Records Only' 
            : 'Files Only';
        const typeBadgeColor = group.type === 'both' ? '#e74c3c' 
            : group.type === 'database' ? '#3498db' 
            : '#e67e22';
        
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
            <div style="display: flex; align-items: center; justify-content: space-between; padding: 0.75rem; background: var(--bg-color); border-radius: 0.375rem; margin-bottom: 0.5rem;">
                <div style="flex: 1; min-width: 0;">
                    <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                        <strong>${escapeHtml(group.original_job_name)}</strong>
                        <span style="font-size: 0.7rem; padding: 0.1rem 0.4rem; border-radius: 0.25rem; background: ${typeBadgeColor}; color: white;">${typeLabel}</span>
                    </div>
                    <div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.2rem;">
                        ${details}
                    </div>
                </div>
                <button class="btn btn-danger" style="padding: 0.3rem 0.75rem; font-size: 0.85rem; white-space: nowrap; margin-left: 0.5rem;" 
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
            <div style="margin-bottom: 1rem;">
                <strong style="color: #e67e22;">⚠ Orphaned Captures Found</strong>
                <div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                    ${data.orphaned_groups.length} group${data.orphaned_groups.length > 1 ? 's' : ''} · ${summaryParts.join(' · ')}
                </div>
            </div>
            <div style="max-height: 400px; overflow-y: auto;">
                ${groupsHtml}
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border-color);">
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
        document.getElementById('capture-detail-job').innerHTML = `<a href="#" onclick="showJobDetails(${capture.job_id}); closeModal('capture-preview-modal'); return false;" style="color: var(--primary-color); text-decoration: underline;">${escapeHtml(capture.job_name || 'Unknown Job')}</a>`;
        document.getElementById('capture-detail-time').textContent = formatDateTime(capture.captured_at);
        document.getElementById('capture-detail-size').textContent = formatBytes(capture.file_size);
        document.getElementById('capture-detail-path').textContent = capture.file_path;
        
        document.getElementById('capture-preview-modal').classList.add('active');
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

function toggleVideoFavoritesFilter() {
    videoFavoritesOnly = !videoFavoritesOnly;
    document.getElementById('video-fav-filter').classList.toggle('active', videoFavoritesOnly);
    if (videoFavoritesOnly) {
        document.getElementById('video-filter-reset').style.display = '';
    }
    loadVideos();
}

// Helper to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function viewJobCaptures(jobId) {
    // Close job details modal if open
    const modal = document.getElementById('job-details-modal');
    if (modal) {
        modal.classList.remove('active');
    }
    
    // Set the job filter in state AND dropdown BEFORE switching view
    capturesState.jobFilter = jobId;
    capturesState.currentPage = 1;
    
    // Ensure dropdown is populated and set to the correct value
    const jobSelect = document.getElementById('captures-job-filter');
    if (jobSelect && jobSelect.options.length === 1) {
        // Need to populate dropdown first
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
    switchView('captures', true);
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
