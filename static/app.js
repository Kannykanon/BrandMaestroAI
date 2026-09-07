// --- GLOBAL APPLICATION STATE ---
let appState = {
    token: localStorage.getItem('bg_access_token') || '',
    user: null,
    activePanel: 'dashboard',
    selectedFile: null,
    generations: [],
    uploadedDocs: [],
    // For quick mock tables and histories
    sessionGenerationsCount: 0,
    sessionDocsCount: 0
};

// API Base URL config (Relative routes work on same origin)
const API_BASE = window.location.origin;

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', () => {
    // Check if user has a token already
    if (appState.token) {
        initSession();
    } else {
        showAuthGate();
    }
});

// Setup drag and drop for documents upload panel
const dropzone = document.getElementById('upload-dropzone');
if (dropzone) {
    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropzone.classList.add('hover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropzone.classList.remove('hover');
        }, false);
    });

    dropzone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFileInput(files[0]);
        }
    });
}

// --- AUTHENTICATION & SESSION MANAGEMENT ---

function showAuthGate() {
    document.getElementById('auth-gate').style.display = 'flex';
    document.getElementById('app-layout').classList.add('hidden');
}

function hideAuthGate() {
    document.getElementById('auth-gate').style.display = 'none';
    document.getElementById('app-layout').classList.remove('hidden');
}

function switchAuthTab(tabName) {
    document.querySelectorAll('.auth-tab').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));

    if (tabName === 'login') {
        document.getElementById('tab-login').classList.add('active');
        document.getElementById('form-login').classList.add('active');
    } else {
        document.getElementById('tab-register').classList.add('active');
        document.getElementById('form-register').classList.add('active');
    }
}

async function handleLogin(e) {
    e.preventDefault();
    const usernameInput = document.getElementById('login-username').value;
    const passwordInput = document.getElementById('login-password').value;
    const submitBtn = document.getElementById('btn-login-submit');

    const params = new URLSearchParams();
    params.append('username', usernameInput);
    params.append('password', passwordInput);

    submitBtn.classList.add('loading');
    submitBtn.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/users/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: params
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Failed to authenticate');
        }

        const data = await response.json();
        appState.token = data.access_token;
        localStorage.setItem('bg_access_token', appState.token);
        showToast('Login successful', 'success');
        await initSession();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.classList.remove('loading');
        submitBtn.disabled = false;
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const first_name = document.getElementById('reg-firstname').value;
    const last_name = document.getElementById('reg-lastname').value;
    const username = document.getElementById('reg-username').value;
    const email = document.getElementById('reg-email').value;
    const password = document.getElementById('reg-password').value;
    const submitBtn = document.getElementById('btn-register-submit');

    submitBtn.classList.add('loading');
    submitBtn.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/users/create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ first_name, last_name, username, email, password })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Registration failed');
        }

        showToast('Account registered! Please sign in.', 'success');
        switchAuthTab('login');
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.classList.remove('loading');
        submitBtn.disabled = false;
    }
}

function handleLogout() {
    appState.token = '';
    appState.user = null;
    localStorage.removeItem('bg_access_token');
    showToast('Signed out successfully', 'info');
    showAuthGate();
}

async function initSession() {
    try {
        // Fetch current user details
        const response = await fetch(`${API_BASE}/users/me`, {
            headers: {
                'Authorization': `Bearer ${appState.token}`
            }
        });

        if (!response.ok) {
            throw new Error('Session expired');
        }

        appState.user = await response.json();

        // Update user interfaces
        document.getElementById('display-user-fullname').innerText = `${appState.user.first_name} ${appState.user.last_name}`;
        document.getElementById('display-business-id').innerText = appState.user.business_id;

        hideAuthGate();
        switchPanel('dashboard');

        // Initial dashboard data loading
        loadRecentGenerations();
        loadUploadedDocsList();
        loadMemoryPatterns('blog'); // Default load blog patterns
    } catch (err) {
        localStorage.removeItem('bg_access_token');
        appState.token = '';
        showAuthGate();
        showToast('Session expired, please sign in.', 'error');
    }
}

// --- DASHBOARD PANEL NAVIGATION ---

function switchPanel(panelId) {
    appState.activePanel = panelId;

    // Switch Sidebar Navigation Active state
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.getElementById(`nav-${panelId}`).classList.add('active');

    // Toggle Section Panel visibilities
    document.querySelectorAll('.dashboard-panel').forEach(panel => panel.classList.remove('active'));
    document.getElementById(`panel-${panelId}`).classList.add('active');

    // Update Page Header Titles
    const titleMap = {
        'dashboard': { t: 'Dashboard Overview', s: 'Track and configure your brand voice rules, generation capacity, and memory metrics.' },
        'generator': { t: 'Content Synthesizer', s: 'Generate high-fidelity marketing collateral tailored using deep brand voice RAG filters.' },
        'documents': { t: 'Guidelines & Reference Documents', s: 'Manage reference text sources mapped to feed the Brand Memory vectors.' },
        'patterns': { t: 'Active Model Memory & Synapses', s: 'Explore brand-aligned guidelines and restrictions synthesized directly from human review loops.' }
    };

    document.getElementById('page-title').innerText = titleMap[panelId].t;
    document.getElementById('page-subtitle').innerText = titleMap[panelId].s;

    if (panelId === 'patterns') {
        // Auto-refresh memory patterns on switch
        loadMemoryPatterns('blog');
    }
}

// --- FORMAT SELECTOR CARD TABS ---
function selectFormatCard(input) {
    document.querySelectorAll('.format-card').forEach(card => card.classList.remove('active'));
    input.closest('.format-card').classList.add('active');
}

// --- REAL-TIME SSE CONTENT STREAM GENERATION ---

async function triggerGeneration(e) {
    e.preventDefault();

    const contentType = document.querySelector('input[name="content_type"]:checked').value;
    const topic = document.getElementById('gen-topic').value;
    const formatType = document.getElementById('gen-format-type').value;
    const useSearch = document.getElementById('gen-use-search').checked;

    const submitBtn = document.getElementById('btn-generate-submit');
    const statusBadge = document.getElementById('generation-status-badge');
    const consoleDiv = document.getElementById('stream-console');
    const outputBox = document.getElementById('generated-output-box');
    const renderedDiv = document.getElementById('rendered-content-text');
    const feedbackBox = document.getElementById('feedback-card-container');

    // Reset output UIs
    consoleDiv.innerHTML = '';
    renderedDiv.innerHTML = '';
    outputBox.classList.add('hidden');
    feedbackBox.classList.add('hidden');

    submitBtn.disabled = true;
    submitBtn.classList.add('loading');
    statusBadge.innerText = 'Initializing';
    statusBadge.className = 'output-status running';

    logConsole('Connecting to BrandMuse AI content generator pipeline...', 'highlight');

    try {
        const response = await fetch(`${API_BASE}/conversation/generate/stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${appState.token}`
            },
            body: JSON.stringify({
                business_id: appState.user.business_id,
                content_type: contentType,
                topic: topic,
                format_type: formatType,
                use_search: useSearch
            })
        });

        if (!response.ok) {
            throw new Error('Failed to initiate stream request');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let generationId = '';
        let fullGeneratedText = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');

            // Save last unfinished item in buffer
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const chunk = JSON.parse(line);

                    // First chunk returns the generated UUID
                    if (chunk.generation_id && !generationId) {
                        generationId = chunk.generation_id;
                        document.getElementById('feedback-generation-id').value = generationId;
                        logConsole(`Generation ID established: ${generationId}`);
                        continue;
                    }

                    // LangGraph returns chunks like {"node_name": {"content": "..."}}
                    // We need to unwrap the inner state update object
                    let stateUpdate = chunk;
                    const keys = Object.keys(chunk);
                    if (keys.length === 1 && typeof chunk[keys[0]] === 'object' && chunk[keys[0]] !== null) {
                        stateUpdate = chunk[keys[0]];
                    }

                    // Process graph execution states
                    if (stateUpdate.research) {
                        logConsole(`[Search Synthesis] Researched contexts: ${stateUpdate.research.substring(0, 100)}...`);
                    }
                    if (stateUpdate.creative_angle) {
                        statusBadge.innerText = 'Creating Angle';
                        logConsole(`[Creative Director] Angle: ${stateUpdate.creative_angle}`, 'highlight');
                    }
                    if (stateUpdate.status) {
                        statusBadge.innerText = stateUpdate.status.toUpperCase();
                    }
                    if (stateUpdate.score) {
                        logConsole(`[Auditor Evaluator] Compliance Score: ${stateUpdate.score} / 10`);
                    }
                    if (stateUpdate.content) {
                        // Render generated markdown/text to screen
                        fullGeneratedText = stateUpdate.content;
                        try {
                            if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
                                renderedDiv.innerHTML = marked.parse(fullGeneratedText);
                            } else if (typeof marked !== 'undefined' && typeof marked === 'function') {
                                renderedDiv.innerHTML = marked(fullGeneratedText);
                            } else {
                                renderedDiv.innerText = fullGeneratedText;
                            }
                        } catch (renderErr) {
                            logConsole(`Render error: ${renderErr.message}`, 'error');
                            renderedDiv.innerText = fullGeneratedText;
                        }
                        outputBox.classList.remove('hidden');
                        // Scroll to output
                        renderedDiv.scrollTop = renderedDiv.scrollHeight;
                    }
                    if (stateUpdate.feedback) {
                        logConsole(`[Auditor Feedback] Revisions: ${stateUpdate.feedback}`);
                    }
                } catch (jsonErr) {
                    // Raw logs or text line fallback
                    logConsole(line);
                }
            }
        }

        // Completion
        statusBadge.innerText = 'Completed';
        statusBadge.className = 'output-status';
        logConsole('Content synthesis completed successfully!', 'success');

        // Show human quality feedback form
        feedbackBox.classList.remove('hidden');

        // Save to active session generations list for stats
        appState.sessionGenerationsCount++;
        document.getElementById('stat-generations-count').innerText = appState.sessionGenerationsCount;

        // Push generation object to our history helper array
        const timestamp = new Date().toLocaleTimeString();
        appState.generations.unshift({
            id: generationId,
            topic: topic,
            contentType: contentType,
            time: timestamp,
            status: 'completed',
            score: 'Pending human verification'
        });
        updateGenerationsListHTML();

    } catch (err) {
        logConsole(`Error: ${err.message}`, 'error');
        statusBadge.innerText = 'Failed';
        statusBadge.className = 'output-status';
        showToast(err.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.classList.remove('loading');
    }
}

function logConsole(message, type = '') {
    const consoleDiv = document.getElementById('stream-console');
    const entry = document.createElement('div');
    entry.className = `console-log ${type}`;

    const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    entry.innerHTML = `<span class="timestamp">[${timestamp}]</span> ${message}`;

    consoleDiv.appendChild(entry);
    consoleDiv.scrollTop = consoleDiv.scrollHeight;
}

// --- HUMAN FEEDBACK SUBMISSION ---

function updateScoreSlider(slider) {
    document.getElementById('display-feedback-score').innerText = `${slider.value} / 10`;
}

function updateApproveBadge(checkbox) {
    const badge = checkbox.nextElementSibling;
    if (checkbox.checked) {
        badge.innerText = 'APPROVED';
        badge.className = 'approve-label-badge approved';
    } else {
        badge.innerText = 'REJECTED';
        badge.className = 'approve-label-badge';
    }
}

async function submitFeedback(e) {
    e.preventDefault();

    const generationId = document.getElementById('feedback-generation-id').value;
    const humanApproved = document.getElementById('feedback-human-approved').checked;
    const humanScore = parseFloat(document.getElementById('feedback-human-score').value);
    const humanFeedback = document.getElementById('feedback-comments').value;
    const contentType = document.querySelector('input[name="content_type"]:checked').value;

    const submitBtn = document.getElementById('btn-submit-feedback');
    submitBtn.disabled = true;
    submitBtn.querySelector('span').innerText = 'Committing feedback...';

    try {
        const response = await fetch(`${API_BASE}/conversation/feedback`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${appState.token}`
            },
            body: JSON.stringify({
                generation_id: generationId,
                business_id: appState.user.business_id,
                content_type: contentType,
                human_approved: humanApproved,
                human_score: humanScore,
                human_feedback: humanFeedback
            })
        });

        if (!response.ok) {
            throw new Error('Failed to register feedback');
        }

        showToast('Feedback submitted! Model alignment retrained.', 'success');

        // Hide feedback container and clear comments
        document.getElementById('feedback-card-container').classList.add('hidden');
        document.getElementById('feedback-comments').value = '';

        // Update status in history
        const genRecord = appState.generations.find(g => g.id === generationId);
        if (genRecord) {
            genRecord.score = `${humanScore}/10 (${humanApproved ? 'Approved' : 'Rejected'})`;
            updateGenerationsListHTML();
        }

    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.querySelector('span').innerText = 'Commit Feedback to Model Memory';
    }
}

// --- DOCUMENT REFERENCE RAG UPLOADER ---

function triggerFileInput() {
    document.getElementById('upload-file-input').click();
}

function handleFileSelect(e) {
    if (e.target.files.length > 0) {
        handleFileInput(e.target.files[0]);
    }
}

function handleFileInput(file) {
    appState.selectedFile = file;
    document.getElementById('selected-file-name').innerText = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
    document.getElementById('selected-file-badge').classList.remove('hidden');
}

function clearSelectedFile() {
    appState.selectedFile = null;
    document.getElementById('upload-file-input').value = '';
    document.getElementById('selected-file-badge').classList.add('hidden');
}

async function uploadDocument(e) {
    e.preventDefault();
    if (!appState.selectedFile) {
        showToast('Please select a file to upload', 'error');
        return;
    }

    const contentType = document.getElementById('upload-content-type').value;
    const submitBtn = document.getElementById('btn-upload-submit');

    const formData = new FormData();
    formData.append('file', appState.selectedFile);
    formData.append('business_id', appState.user.business_id);
    formData.append('content_type', contentType);

    submitBtn.disabled = true;
    submitBtn.querySelector('span').innerText = 'Processing & Vectorizing...';

    try {
        const response = await fetch(`${API_BASE}/documents/documents/top-performing`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${appState.token}`
            },
            body: formData
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Upload failed');
        }

        const data = await response.json();
        showToast('Document vectorized successfully!', 'success');

        // Add to mock session documents table
        appState.sessionDocsCount++;
        document.getElementById('stat-docs-count').innerText = appState.sessionDocsCount;

        appState.uploadedDocs.unshift({
            filename: appState.selectedFile.name,
            contentType: contentType,
            date: new Date().toLocaleDateString(),
            status: 'Processing (Vectorized)'
        });

        updateUploadedDocsTableHTML();
        clearSelectedFile();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.querySelector('span').innerText = 'Upload and Process Reference Document';
    }
}

// --- LEARNING MEMORY PATTERN RETRIEVAL ---

async function loadMemoryPatterns(contentType) {
    // Switch active buttons state
    ['blog', 'social', 'ad', 'proposal'].forEach(t => {
        const btn = document.getElementById(`btn-memory-${t}`);
        if (btn) {
            btn.classList.remove('active');
        }
    });
    const activeBtn = document.getElementById(`btn-memory-${contentType}`);
    if (activeBtn) activeBtn.classList.add('active');

    const approvedList = document.getElementById('approved-patterns-list');
    const rejectedList = document.getElementById('rejected-patterns-list');

    approvedList.innerHTML = '<div class="empty-state"><i class="fa-solid fa-spinner fa-spin"></i><p>Querying guidelines databases...</p></div>';
    rejectedList.innerHTML = '<div class="empty-state"><i class="fa-solid fa-spinner fa-spin"></i><p>Querying alignment metrics...</p></div>';

    try {
        const response = await fetch(`${API_BASE}/conversation/patterns/${appState.user.business_id}/${contentType}`, {
            headers: {
                'Authorization': `Bearer ${appState.token}`
            }
        });

        if (!response.ok) {
            throw new Error('Failed to query patterns library');
        }

        const data = await response.json();

        // Update counts in dashboard sidebar card
        const approvedPatterns = data.patterns?.approved || [];
        const rejectedPatterns = data.patterns?.rejected || [];

        document.getElementById('display-approved-rules-count').innerText = `${approvedPatterns.length} rules active`;

        // Render Approved patterns
        if (approvedPatterns.length === 0) {
            approvedList.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-shield-heart"></i>
                    <p>No verified style guideposts synthesized for "${contentType.toUpperCase()}" outputs yet. Submit positive human score feedback to build memory rules.</p>
                </div>`;
        } else {
            approvedList.innerHTML = approvedPatterns.map(p => `
                <div class="pattern-item approved">
                    <div class="angle"><i class="fa-solid fa-circle-check" style="color: var(--accent-green)"></i> ${p.angle || 'General Style Alignment'}</div>
                    <div class="feedback">${p.feedback}</div>
                </div>
            `).join('');
        }

        // Render Rejected patterns
        if (rejectedPatterns.length === 0) {
            rejectedList.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-ban"></i>
                    <p>No rejection thresholds mapped for "${contentType.toUpperCase()}" outputs yet. Mark unsatisfactory content as rejected to define red-lines.</p>
                </div>`;
        } else {
            rejectedList.innerHTML = rejectedPatterns.map(p => `
                <div class="pattern-item rejected">
                    <div class="angle"><i class="fa-solid fa-circle-xmark" style="color: var(--primary-red)"></i> ${p.angle || 'Voice Red-line Exception'}</div>
                    <div class="feedback">${p.feedback}</div>
                </div>
            `).join('');
        }

    } catch (err) {
        approvedList.innerHTML = `<div class="empty-state"><p>Error: ${err.message}</p></div>`;
        rejectedList.innerHTML = `<div class="empty-state"><p>Error: ${err.message}</p></div>`;
    }
}

// --- MOCK DATA RENDER HELPERS ---

function updateGenerationsListHTML() {
    const container = document.getElementById('history-container');
    if (appState.generations.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-wand-magic-sparkles" style="color: var(--primary-red);"></i>
                <p>No content generated yet.</p>
                <button class="btn btn-primary btn-sm" onclick="switchPanel('generator')" style="margin-top:8px;">
                    <i class="fa-solid fa-bolt"></i> <span>Generate your first content</span>
                </button>
            </div>`;
        return;
    }

    container.innerHTML = appState.generations.map(gen => `
        <div class="history-item">
            <div class="history-info">
                <div class="title">${gen.topic}</div>
                <div class="meta">
                    <span><i class="fa-solid fa-layer-group"></i> ${gen.contentType.toUpperCase()}</span>
                    <span><i class="fa-regular fa-id-card"></i> ${gen.id.substring(0, 8)}...</span>
                </div>
            </div>
            <div class="history-score">
                <div class="score-badge ${gen.status === 'pending' ? 'pending' : ''}">${gen.score}</div>
                <div class="time">${gen.time}</div>
            </div>
        </div>
    `).join('');
}

function updateUploadedDocsTableHTML() {
    const tableBody = document.getElementById('table-documents-body');
    if (appState.uploadedDocs.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="4" class="table-empty">
                    <i class="fa-regular fa-folder"></i>
                    <p>No guideline documents vectorized yet. Use the upload card to sync brand guidelines.</p>
                </td>
            </tr>`;
        return;
    }

    tableBody.innerHTML = appState.uploadedDocs.map(doc => `
        <tr>
            <td><strong>${doc.filename}</strong></td>
            <td><span class="badge badge-accent">${doc.contentType.toUpperCase()}</span></td>
            <td>${doc.date}</td>
            <td><span class="score-badge">${doc.status}</span></td>
        </tr>
    `).join('');
}

function loadRecentGenerations() {
    appState.generations = [];
    updateGenerationsListHTML();
}

function loadUploadedDocsList() {
    appState.uploadedDocs = [];
    updateUploadedDocsTableHTML();
}

// --- UTILITY CLIENT-SIDE HELPERS ---

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let icon = 'fa-circle-info';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-circle-exclamation';

    toast.innerHTML = `
        <i class="fa-solid ${icon}"></i>
        <span>${message}</span>
    `;

    container.appendChild(toast);

    // Auto-remove toast after 4s
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function copyBusinessId() {
    const bizId = document.getElementById('display-business-id').innerText;
    navigator.clipboard.writeText(bizId).then(() => {
        showToast('Business ID copied to clipboard', 'success');
    });
}

function copyGeneratedText() {
    const outputText = document.getElementById('rendered-content-text').innerText;
    navigator.clipboard.writeText(outputText).then(() => {
        showToast('Generated text copied to clipboard', 'success');
    });
}
