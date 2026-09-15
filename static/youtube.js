// YouTube Studio: the optional YouTube Automation panel.
//
// Kept apart from app.js on purpose. It uses only app.js's shared helpers
// (appState, API_BASE, showToast, escapeHTML) and talks to the /youtube routes.
// If this file fails to load, the marketing panels are unaffected.
(function () {
    'use strict';

    const state = {
        tab: 'scripts',
        status: null,
        voices: [],
        voiceProvider: null,
        characters: [],
        styles: [],
        characterId: null,
        projectId: null,
        pollTimer: null,
        characterPollTimer: null,
        fileUrls: {},
    };

    const BUSY = new Set(['planning', 'voicing', 'drawing', 'rendering']);
    const TABS = ['scripts', 'characters', 'styles', 'projects'];
    const FORMAT_LABELS = { long_form: 'Long-form 16:9', short: 'Short 9:16' };
    const SHOT_TYPES = ['narration', 'dialogue', 'two_character', 'cutaway'];

    // --- HTTP -----------------------------------------------------------------

    async function api(path, options = {}) {
        const headers = { Authorization: `Bearer ${appState.token}`, ...(options.headers || {}) };
        if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
        const response = await fetch(`${API_BASE}/youtube${path}`, { ...options, headers });
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            let detail = body.detail;
            if (Array.isArray(detail)) detail = detail.map(d => d.msg).join('; ');
            else if (detail && typeof detail === 'object') detail = detail.message;
            const error = new Error(detail || `Request failed (${response.status})`);
            error.status = response.status;
            error.body = body;
            throw error;
        }
        return response.status === 204 ? null : response.json();
    }

    // app.js's escapeHTML is for text content and leaves quotes alone. Values
    // placed inside attributes use attr(); values passed to inline handlers use
    // jsArg(), which JSON-encodes them first so a quote cannot end the string.
    function attr(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function jsArg(value) {
        return attr(JSON.stringify(value));
    }

    function toastError(err) {
        showToast(escapeHTML(err.message || String(err)), 'error');
    }

    function approvalBadge(label) {
        return label === 'human'
            ? '<span class="badge badge-human" title="A person approved this script">Human-approved</span>'
            : '<span class="badge badge-enforcer" title="Approved by the enforcer; no person has reviewed it">Enforcer-approved · not reviewed</span>';
    }

    function formatSeconds(seconds) {
        if (seconds == null) return '—';
        const s = Math.round(seconds);
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }

    function money(value) {
        return value == null ? '—' : `$${Number(value).toFixed(2)}`;
    }

    function formatBytes(bytes) {
        if (!bytes) return '—';
        return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
    }

    // Files need the bearer token, which <audio> and <img> cannot send. Ask for
    // a signed link first; if the store cannot sign one, download through the
    // API and use a local object URL. The cache key includes a version that
    // changes when the stored file changes, so a redrawn image is refetched.
    async function fileUrl(path, version = '') {
        const cacheKey = `${path}#${version}`;
        if (state.fileUrls[cacheKey]) return state.fileUrls[cacheKey];
        const link = await api(`${path}?as_link=true`);
        let url = link.url;
        if (!url) {
            const response = await fetch(`${API_BASE}/youtube${path}`, {
                headers: { Authorization: `Bearer ${appState.token}` },
            });
            if (!response.ok) throw new Error('File is not available');
            url = URL.createObjectURL(await response.blob());
        }
        Object.keys(state.fileUrls).forEach(key => {
            if (key.startsWith(`${path}#`) && key !== cacheKey) forgetKey(key);
        });
        state.fileUrls[cacheKey] = url;
        return url;
    }

    function forgetKey(key) {
        if (state.fileUrls[key] && state.fileUrls[key].startsWith('blob:')) URL.revokeObjectURL(state.fileUrls[key]);
        delete state.fileUrls[key];
    }

    function forgetFiles(prefix) {
        Object.keys(state.fileUrls).forEach(key => {
            if (!prefix || key.startsWith(prefix)) forgetKey(key);
        });
    }

    // Fill every <img data-yt-src="path" data-yt-version="v"> inside root.
    function hydrateImages(root) {
        root.querySelectorAll('img[data-yt-src]').forEach(async img => {
            try {
                img.src = await fileUrl(img.dataset.ytSrc, img.dataset.ytVersion || '');
            } catch (err) {
                img.alt = 'Image unavailable';
            }
        });
    }

    function imgTag(path, version, alt) {
        return `<img data-yt-src="${attr(path)}" data-yt-version="${attr(version || '')}" alt="${attr(alt)}" loading="lazy">`;
    }

    async function play(path, version = '') {
        try {
            const player = document.getElementById('yt-player');
            player.src = await fileUrl(path, version);
            await player.play();
        } catch (err) {
            toastError(err);
        }
    }

    async function download(path, filename, version = '') {
        try {
            const link = document.createElement('a');
            link.href = await fileUrl(path, version);
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
        } catch (err) {
            toastError(err);
        }
    }

    // --- Tabs -----------------------------------------------------------------

    function showTab(tab) {
        state.tab = tab;
        TABS.forEach(name => {
            document.getElementById(`yt-view-${name}`).classList.toggle('hidden', name !== tab);
            document.getElementById(`yt-tab-${name}`).classList.toggle('active', name === tab);
        });
        if (tab === 'scripts') loadScripts();
        if (tab === 'characters') loadCharacters();
        if (tab === 'styles') loadStyles();
        if (tab === 'projects') loadProjects();
    }

    async function open() {
        try {
            state.status = await api('/status');
            const status = state.status;
            const notice = document.getElementById('yt-notice');
            const messages = [];
            if (status.storage && !status.storage.configured) {
                messages.push(`File storage is not configured (missing ${escapeHTML((status.storage.missing || []).join(', '))}).`);
            } else if (status.storage && status.storage.provider === 'local') {
                messages.push('Files are stored on this server’s disk (development storage).');
            }
            if (status.images && !status.images.configured) {
                messages.push(`Image generation is not configured${status.images.missing && status.images.missing.length
                    ? ` (missing ${escapeHTML(status.images.missing.join(', '))})` : ''}; character sheets and storyboards are unavailable.`);
            }
            notice.innerHTML = messages.join(' ');
            notice.classList.toggle('hidden', messages.length === 0);
        } catch (err) {
            toastError(err);
        }
        showTab(state.tab);
    }

    // --- Scripts ----------------------------------------------------------------

    async function loadScripts() {
        const body = document.getElementById('yt-scripts-body');
        body.innerHTML = '<tr><td colspan="5" class="table-empty"><i class="fa-solid fa-spinner fa-spin"></i></td></tr>';
        try {
            const { scripts } = await api('/scripts');
            if (!scripts.length) {
                body.innerHTML = `<tr><td colspan="5" class="table-empty"><i class="fa-regular fa-folder-open"></i>
                    <p>No approved scripts yet. Generate a <strong>Script</strong> in the Content Generator and approve it.</p></td></tr>`;
                return;
            }
            body.innerHTML = scripts.map(s => `
                <tr>
                    <td><strong>${escapeHTML(s.topic)}</strong><div class="yt-muted">${escapeHTML(s.preview)}${s.preview.length >= 240 ? '…' : ''}</div></td>
                    <td>${approvalBadge(s.approval)}</td>
                    <td>${s.word_count}</td>
                    <td><select id="yt-format-${attr(s.generation_id)}">
                        <option value="long_form">Long-form 16:9</option>
                        <option value="short">Short 9:16</option>
                    </select></td>
                    <td><button class="btn btn-primary btn-sm" onclick="ytStudio.createProject(${jsArg(s.generation_id)}, this)">
                        <i class="fa-solid fa-clapperboard"></i> <span>Create project</span></button></td>
                </tr>`).join('');
        } catch (err) {
            body.innerHTML = `<tr><td colspan="5" class="table-empty"><p>${escapeHTML(err.message)}</p></td></tr>`;
        }
    }

    async function createProject(generationId, button) {
        const format = document.getElementById(`yt-format-${generationId}`).value;
        button.disabled = true;
        try {
            const project = await api('/projects', {
                method: 'POST', body: JSON.stringify({ generation_id: generationId, format }),
            });
            showToast('Project created — planning shots', 'success');
            await api(`/projects/${project.id}/plan`, { method: 'POST' });
            state.projectId = project.id;
            showTab('projects');
        } catch (err) {
            toastError(err);
        } finally {
            button.disabled = false;
        }
    }

    // --- Voices and characters ----------------------------------------------------

    async function loadVoices() {
        if (state.voices.length) return;
        const body = await api('/voices');
        state.voices = body.voices;
        state.voiceProvider = body.provider;
        const hint = document.getElementById('yt-preview-hint');
        if (!state.voices.some(v => v.has_preview)) {
            hint.innerHTML = `No voice samples yet. <a href="#" onclick="ytStudio.generatePreviews(); return false;">Generate samples</a> (runs in the background, a few minutes).`;
        } else {
            hint.textContent = '';
        }
    }

    function voiceLabel(voice) {
        const gender = voice.gender ? `, ${voice.gender}` : '';
        return `${voice.name} (${voice.language}${gender})`;
    }

    function voiceOptions(selectedId) {
        return state.voices.map(v =>
            `<option value="${attr(v.id)}" ${v.id === selectedId ? 'selected' : ''}>${escapeHTML(voiceLabel(v))}</option>`
        ).join('');
    }

    function sheetBadge(c) {
        const labels = {
            none: ['badge-neutral', c.faces && c.faces.length ? 'no sheet' : 'no face'],
            generating: ['badge-neutral', 'generating…'],
            ready: ['badge-enforcer', 'sheet to approve'],
            approved: ['badge-approved', 'sheet approved'],
            failed: ['badge-accent', 'sheet failed'],
        };
        const [cls, text] = labels[c.sheet_status] || labels.none;
        return `<span class="badge ${cls}">${escapeHTML(text)}</span>`;
    }

    async function loadCharacters() {
        const body = document.getElementById('yt-characters-body');
        if (!body.children.length) {
            body.innerHTML = '<tr><td colspan="5" class="table-empty"><i class="fa-solid fa-spinner fa-spin"></i></td></tr>';
        }
        try {
            await loadVoices();
            const voiceSelect = document.getElementById('yt-char-voice');
            if (!voiceSelect.options.length) voiceSelect.innerHTML = voiceOptions(null);
            const { characters } = await api('/characters');
            state.characters = characters;
            if (!characters.length) {
                body.innerHTML = '<tr><td colspan="5" class="table-empty"><p>No characters yet. Add a narrator and one character per speaker.</p></td></tr>';
            } else {
                body.innerHTML = characters.map(c => `
                    <tr>
                        <td><strong>${escapeHTML(c.name)}</strong></td>
                        <td><select onchange="ytStudio.changeVoice(${c.id}, this.value)">
                            ${c.voice_id ? '' : '<option value="" selected>— choose —</option>'}${voiceOptions(c.voice_id)}</select>
                            ${c.voice_id ? `<button class="btn btn-secondary btn-sm" title="Hear this voice" onclick="ytStudio.previewVoice(${jsArg(c.voice_provider || state.voiceProvider)}, ${jsArg(c.voice_id)})"><i class="fa-solid fa-play"></i></button>` : ''}</td>
                        <td>${sheetBadge(c)} <button class="btn btn-secondary btn-sm" onclick="ytStudio.openCharacter(${c.id})">Face &amp; sheet</button></td>
                        <td class="yt-muted">${escapeHTML(c.style_notes || '')}</td>
                        <td><button class="btn btn-sm btn-danger" title="Delete character" onclick="ytStudio.deleteCharacter(${c.id})"><i class="fa-regular fa-trash-can"></i></button></td>
                    </tr>`).join('');
            }
            if (state.characterId) renderCharacter(characters.find(c => c.id === state.characterId));
        } catch (err) {
            body.innerHTML = `<tr><td colspan="5" class="table-empty"><p>${escapeHTML(err.message)}</p></td></tr>`;
        }
    }

    async function createCharacter(event) {
        event.preventDefault();
        try {
            await api('/characters', {
                method: 'POST',
                body: JSON.stringify({
                    name: document.getElementById('yt-char-name').value.trim(),
                    voice_id: document.getElementById('yt-char-voice').value,
                    voice_provider: state.voiceProvider,
                    style_notes: document.getElementById('yt-char-notes').value.trim() || null,
                }),
            });
            document.getElementById('yt-character-form').reset();
            showToast('Character added', 'success');
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function changeVoice(characterId, voiceId) {
        if (!voiceId) return;
        try {
            await api(`/characters/${characterId}`, {
                method: 'PATCH', body: JSON.stringify({ voice_id: voiceId, voice_provider: state.voiceProvider }),
            });
            showToast('Voice updated. Projects using this character will re-voice their lines.', 'success');
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function deleteCharacter(characterId) {
        if (!confirm('Delete this character? Projects using it will need recasting.')) return;
        try {
            await api(`/characters/${characterId}`, { method: 'DELETE' });
            if (state.characterId === characterId) closeCharacter();
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    function previewVoice(provider, voiceId) {
        const voice = state.voices.find(v => v.id === voiceId);
        if (voice && !voice.has_preview) {
            showToast('No sample for this voice yet — generate samples first.', 'info');
            return;
        }
        play(`/voices/${encodeURIComponent(provider)}/${encodeURIComponent(voiceId)}/preview`);
    }

    function previewSelectedVoice() {
        const voiceId = document.getElementById('yt-char-voice').value;
        if (voiceId) previewVoice(state.voiceProvider, voiceId);
    }

    async function generatePreviews() {
        try {
            await api('/voices/previews', { method: 'POST', body: JSON.stringify({ provider: state.voiceProvider }) });
            showToast('Generating voice samples in the background. Reopen this tab in a few minutes.', 'success');
            state.voices = [];
        } catch (err) {
            toastError(err);
        }
    }

    // --- Faces and character sheets --------------------------------------------------

    function openCharacter(characterId) {
        state.characterId = characterId;
        renderCharacter(state.characters.find(c => c.id === characterId));
        document.getElementById('yt-character-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function closeCharacter() {
        state.characterId = null;
        if (state.characterPollTimer) clearTimeout(state.characterPollTimer);
        document.getElementById('yt-character-detail').classList.add('hidden');
    }

    function renderCharacter(c) {
        const card = document.getElementById('yt-character-detail');
        if (state.characterPollTimer) clearTimeout(state.characterPollTimer);
        if (!c) {
            closeCharacter();
            return;
        }
        const imagesReady = !state.status || !state.status.images || state.status.images.configured;
        const generating = c.sheet_status === 'generating';
        card.classList.remove('hidden');
        card.innerHTML = `
            <div class="yt-detail-head">
                <div><h2>${escapeHTML(c.name)}: face &amp; character sheet</h2>
                    <p class="card-subtitle">Upload 1–5 clear photos of this character's face. A sheet is generated from them and, once you approve it, used for every scene.</p></div>
                <button class="btn btn-secondary btn-sm" onclick="ytStudio.closeCharacter()">Close</button>
            </div>

            <label class="yt-check">
                <input type="checkbox" ${c.rights_confirmed ? 'checked' : ''} onchange="ytStudio.confirmRights(${c.id}, this.checked)">
                <span>I have the right to use this face: it is AI-generated, an illustration, or a real person who has agreed to be animated.
                Animating real people without their consent is not allowed.</span>
            </label>

            <div class="yt-section-title">Face photos (${c.faces.length}/5)</div>
            <div class="yt-thumbs">
                ${c.faces.map(f => `<div class="yt-thumb">${imgTag(`/characters/${c.id}/images/${f.id}`, f.id, `${c.name} photo`)}
                    <button class="btn btn-sm btn-danger" title="Remove photo" onclick="ytStudio.deleteCharacterImage(${c.id}, ${f.id})"><i class="fa-solid fa-xmark"></i></button></div>`).join('')}
            </div>
            <div class="yt-actions" style="margin-top:10px">
                <input type="file" id="yt-face-input" accept="image/jpeg,image/png,image/webp" class="hidden" onchange="ytStudio.uploadFace(${c.id}, this)">
                <button class="btn btn-secondary btn-sm" ${c.rights_confirmed && c.faces.length < 5 ? '' : 'disabled'}
                    title="${c.rights_confirmed ? '' : 'Confirm you have the right to use this face first'}"
                    onclick="document.getElementById('yt-face-input').click()"><i class="fa-solid fa-upload"></i> <span>Upload photo</span></button>
            </div>

            <div class="yt-section-title">Character sheet ${sheetBadge(c)}</div>
            ${c.sheet_error ? `<p class="yt-error">${escapeHTML(c.sheet_error)}</p>` : ''}
            ${c.sheet ? `<div class="yt-sheet">${imgTag(`/characters/${c.id}/images/${c.sheet.id}`, c.sheet.id, `${c.name} character sheet`)}</div>` : ''}
            <div class="yt-actions" style="margin-top:10px">
                <button class="btn btn-secondary btn-sm" ${c.rights_confirmed && c.faces.length && !generating && imagesReady ? '' : 'disabled'}
                    onclick="ytStudio.generateSheet(${c.id})"><i class="fa-solid fa-wand-magic-sparkles"></i>
                    <span>${c.sheet ? 'Regenerate sheet' : 'Generate sheet'}</span></button>
                ${c.sheet && !c.sheet.approved ? `<button class="btn btn-primary btn-sm" onclick="ytStudio.approveSheet(${c.id})"><i class="fa-solid fa-check"></i> <span>Approve sheet</span></button>` : ''}
                ${imagesReady ? '' : '<span class="yt-muted">Image generation is not configured.</span>'}
            </div>`;
        hydrateImages(card);
        if (generating) {
            state.characterPollTimer = setTimeout(() => {
                if (state.tab === 'characters' && state.characterId === c.id) loadCharacters();
            }, 4000);
        }
    }

    async function confirmRights(characterId, confirmed) {
        try {
            await api(`/characters/${characterId}/rights`, { method: 'POST', body: JSON.stringify({ confirmed }) });
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function uploadFace(characterId, input) {
        const file = input.files && input.files[0];
        input.value = '';
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        try {
            await api(`/characters/${characterId}/faces`, { method: 'POST', body: form });
            showToast('Photo added', 'success');
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function deleteCharacterImage(characterId, imageId) {
        if (!confirm('Remove this photo? The character sheet will need approving again.')) return;
        try {
            await api(`/characters/${characterId}/images/${imageId}`, { method: 'DELETE' });
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function generateSheet(characterId) {
        try {
            await api(`/characters/${characterId}/sheet`, { method: 'POST' });
            showToast('Generating the character sheet…', 'success');
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    async function approveSheet(characterId) {
        try {
            await api(`/characters/${characterId}/sheet/approve`, { method: 'POST' });
            showToast('Sheet approved', 'success');
            loadCharacters();
        } catch (err) {
            toastError(err);
        }
    }

    // --- Styles ---------------------------------------------------------------------

    async function loadStyles() {
        const list = document.getElementById('yt-styles-list');
        try {
            state.styles = (await api('/styles')).styles;
            if (!state.styles.length) {
                list.innerHTML = '<p class="yt-muted">No styles yet. Projects without one use a cinematic photorealistic look.</p>';
                return;
            }
            list.innerHTML = state.styles.map(s => `
                <div class="yt-style-item">
                    <div class="yt-thumb">${s.has_reference ? imgTag(`/styles/${s.id}/reference`, s.reference_version, `${s.name} reference`) : '<span class="yt-muted" style="padding:8px;display:block">No reference image</span>'}</div>
                    <div>
                        <strong>${escapeHTML(s.name)}</strong>
                        <textarea id="yt-style-prompt-${s.id}" maxlength="2000">${escapeHTML(s.prompt)}</textarea>
                        <div class="yt-actions" style="margin-top:6px">
                            <button class="btn btn-secondary btn-sm" onclick="ytStudio.saveStyle(${s.id})"><i class="fa-regular fa-floppy-disk"></i> <span>Save look</span></button>
                            <input type="file" id="yt-style-ref-${s.id}" accept="image/jpeg,image/png,image/webp" class="hidden" onchange="ytStudio.uploadStyleReference(${s.id}, this)">
                            <button class="btn btn-secondary btn-sm" onclick="document.getElementById('yt-style-ref-${s.id}').click()"><i class="fa-solid fa-image"></i> <span>${s.has_reference ? 'Replace' : 'Add'} reference image</span></button>
                            ${s.has_reference ? `<button class="btn btn-secondary btn-sm" onclick="ytStudio.removeStyleReference(${s.id})">Remove image</button>` : ''}
                        </div>
                    </div>
                    <button class="btn btn-sm btn-danger" title="Delete style" onclick="ytStudio.deleteStyle(${s.id})"><i class="fa-regular fa-trash-can"></i></button>
                </div>`).join('');
            hydrateImages(list);
        } catch (err) {
            list.innerHTML = `<p class="yt-error">${escapeHTML(err.message)}</p>`;
        }
    }

    async function createStyle(event) {
        event.preventDefault();
        try {
            await api('/styles', {
                method: 'POST',
                body: JSON.stringify({
                    name: document.getElementById('yt-style-name').value.trim(),
                    prompt: document.getElementById('yt-style-prompt').value.trim(),
                }),
            });
            document.getElementById('yt-style-form').reset();
            showToast('Style added', 'success');
            loadStyles();
        } catch (err) {
            toastError(err);
        }
    }

    async function saveStyle(styleId) {
        try {
            await api(`/styles/${styleId}`, {
                method: 'PATCH', body: JSON.stringify({ prompt: document.getElementById(`yt-style-prompt-${styleId}`).value.trim() }),
            });
            showToast('Style saved. Storyboards using it will need approving again.', 'success');
            loadStyles();
        } catch (err) {
            toastError(err);
        }
    }

    async function uploadStyleReference(styleId, input) {
        const file = input.files && input.files[0];
        input.value = '';
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        try {
            await api(`/styles/${styleId}/reference`, { method: 'POST', body: form });
            loadStyles();
        } catch (err) {
            toastError(err);
        }
    }

    async function removeStyleReference(styleId) {
        try {
            await api(`/styles/${styleId}/reference`, { method: 'DELETE' });
            loadStyles();
        } catch (err) {
            toastError(err);
        }
    }

    async function deleteStyle(styleId) {
        if (!confirm('Delete this style? Projects using it fall back to the default look.')) return;
        try {
            await api(`/styles/${styleId}`, { method: 'DELETE' });
            loadStyles();
        } catch (err) {
            toastError(err);
        }
    }

    // --- Projects -------------------------------------------------------------------

    async function loadProjects() {
        const body = document.getElementById('yt-projects-body');
        try {
            const { projects } = await api('/projects');
            if (!projects.length) {
                body.innerHTML = '<tr><td colspan="6" class="table-empty"><p>No projects yet. Create one from an approved script.</p></td></tr>';
            } else {
                body.innerHTML = projects.map(p => `
                    <tr>
                        <td><strong>${escapeHTML(p.topic)}</strong></td>
                        <td>${escapeHTML(FORMAT_LABELS[p.format] || p.format)}</td>
                        <td>${approvalBadge(p.approval)}</td>
                        <td><span class="output-status ${BUSY.has(p.status) ? 'running' : ''}">${escapeHTML(p.status)}</span></td>
                        <td>${formatSeconds(p.duration_s)}${p.duration_is_estimate && p.duration_s ? ' <span class="yt-muted">est.</span>' : ''}</td>
                        <td><button class="btn btn-secondary btn-sm" onclick="ytStudio.openProject(${p.id})">Open</button></td>
                    </tr>`).join('');
            }
            if (state.projectId) openProject(state.projectId);
        } catch (err) {
            body.innerHTML = `<tr><td colspan="6" class="table-empty"><p>${escapeHTML(err.message)}</p></td></tr>`;
        }
    }

    function stopPolling() {
        if (state.pollTimer) clearTimeout(state.pollTimer);
        state.pollTimer = null;
    }

    async function openProject(projectId) {
        stopPolling();
        state.projectId = projectId;
        const card = document.getElementById('yt-project-detail');
        card.classList.remove('hidden');
        try {
            await loadVoices();
            state.characters = (await api('/characters')).characters;
            state.styles = (await api('/styles')).styles;
            const project = await api(`/projects/${projectId}`);
            renderProject(project);
            if (BUSY.has(project.status)) {
                state.pollTimer = setTimeout(() => {
                    if (state.tab === 'projects' && state.projectId === projectId) loadProjects();
                }, 4000);
            }
        } catch (err) {
            card.innerHTML = `<p class="yt-error">${escapeHTML(err.message)}</p>`;
        }
    }

    function renderStoryboard(p, busy) {
        const sb = p.storyboard;
        const allImages = p.shots.length > 0 && sb.images_done === p.shots.length;
        const canApprove = allImages && p.has_audio && !sb.approved && !busy;
        const approveHint = !allImages ? 'Every shot needs an image' : (!p.has_audio ? 'Voice the script first' : '');
        const styleOptions = ['<option value="">Default look</option>']
            .concat(state.styles.map(s => `<option value="${s.id}" ${s.id === sb.style_id ? 'selected' : ''}>${escapeHTML(s.name)}</option>`))
            .join('');

        return `
            <div class="yt-section-title">Storyboard ${sb.approved ? '<span class="badge badge-approved">approved</span>' : ''}</div>
            <div class="yt-actions">
                <label class="yt-muted" for="yt-project-style">Style</label>
                <select id="yt-project-style" ${busy ? 'disabled' : ''} onchange="ytStudio.setProjectStyle(${p.id}, this.value)">${styleOptions}</select>
                <span class="yt-muted">${sb.images_done}/${p.shots.length} images · spent ${money(sb.spent_usd)}${sb.estimate_remaining_usd ? ` · about ${money(sb.estimate_remaining_usd)} to finish` : ''}</span>
            </div>
            <div class="yt-actions" style="margin:10px 0 14px">
                <button class="btn btn-secondary btn-sm" ${busy || !sb.image_provider_configured || !p.shots.length ? 'disabled' : ''}
                    onclick="ytStudio.generateStoryboard(${p.id}, false)"><i class="fa-regular fa-images"></i>
                    <span>${allImages ? 'All shots drawn' : (sb.images_done ? 'Draw missing shots' : 'Draw storyboard')}</span></button>
                ${sb.images_done ? `<button class="btn btn-secondary btn-sm" ${busy || !sb.image_provider_configured ? 'disabled' : ''} onclick="ytStudio.generateStoryboard(${p.id}, true)"><i class="fa-solid fa-rotate"></i> <span>Redraw all</span></button>` : ''}
                <button class="btn btn-primary btn-sm" ${canApprove ? '' : 'disabled'} title="${attr(approveHint)}"
                    onclick="ytStudio.approveStoryboard(${p.id})"><i class="fa-solid fa-check"></i> <span>${sb.approved ? 'Storyboard approved' : 'Approve storyboard'}</span></button>
                ${sb.image_provider_configured ? '' : '<span class="yt-muted">Image generation is not configured.</span>'}
            </div>
            ${p.shots.length ? `<div class="yt-storyboard ${p.format === 'short' ? 'short' : ''}">${p.shots.map(s => `
                <div class="yt-frame">
                    <div class="yt-frame-image">${s.has_image
                        ? imgTag(`/projects/${p.id}/shots/${s.id}/image`, s.image_version, `Shot ${s.position}`)
                        : `<span>${busy && p.status === 'drawing' ? 'Drawing…' : 'Not drawn yet'}</span>`}</div>
                    <div class="yt-frame-body">
                        <div class="yt-frame-head">
                            <strong>#${s.position} ${escapeHTML(s.speaker)}</strong>
                            <select ${busy ? 'disabled' : ''} onchange="ytStudio.changeShotType(${p.id}, ${s.id}, this.value)">
                                ${SHOT_TYPES.map(t => `<option value="${t}" ${t === s.shot_type ? 'selected' : ''}>${t.replace('_', ' ')}</option>`).join('')}
                            </select>
                        </div>
                        <div class="yt-muted">“${escapeHTML(s.text)}”</div>
                        ${s.image_error ? `<div class="yt-error">${escapeHTML(s.image_error)}</div>` : ''}
                        <textarea id="yt-visual-${s.id}" ${busy ? 'disabled' : ''} maxlength="2000" title="What the shot shows">${escapeHTML(s.visual || '')}</textarea>
                        <div class="yt-actions">
                            <button class="btn btn-secondary btn-sm" ${busy || !sb.image_provider_configured ? 'disabled' : ''} onclick="ytStudio.redrawShot(${p.id}, ${s.id})">
                                <i class="fa-solid fa-paintbrush"></i> <span>${s.has_image ? 'Save &amp; redraw' : 'Save &amp; draw'}</span></button>
                        </div>
                    </div>
                </div>`).join('')}</div>` : ''}`;
    }

    function videoSection(p, busy) {
        const video = p.video || { renders: [], estimate: {} };
        const est = video.estimate || {};
        const sb = p.storyboard;
        const latest = video.renders[0];
        const canRender = sb.approved && !busy && !est.error && est.provider_configured;
        const hint = est.error ? est.error
            : (!est.provider_configured ? `The avatar provider ${est.avatar_provider} is not configured`
                : (!sb.approved ? 'Approve the storyboard first' : ''));
        const animation = est.error ? '' : (est.lip_sync
            ? `${est.talking_shots} talking shot${est.talking_shots === 1 ? '' : 's'} (${est.talking_seconds}s, up to ${est.max_talking_seconds}s each) animated with ${escapeHTML(est.avatar_provider)}`
            : 'Talking shots use the still image (no lip sync)');
        const cost = est.error ? '' : (est.new_clips
            ? `about ${money(est.avatar_cost_usd)} for ${est.new_clips} new clip${est.new_clips === 1 ? '' : 's'}${est.over_budget ? ` <span class="yt-warning">over the ${money(est.budget_usd)} budget</span>` : ''}`
            : (est.lip_sync ? 'no new clips to pay for' : 'free'));
        const renderRow = r => `
            <tr>
                <td>#${r.id} ${r.current ? '<span class="badge badge-approved">current</span>' : '<span class="badge badge-neutral" title="The storyboard changed after this render">out of date</span>'}</td>
                <td>${r.created_at ? escapeHTML(new Date(r.created_at).toLocaleString()) : '—'}</td>
                <td>${formatSeconds(r.duration_s)}</td>
                <td>${formatBytes(r.size_bytes)}</td>
                <td>${escapeHTML(r.avatar_provider || '')}</td>
                <td><div class="yt-actions">
                    <button class="btn btn-secondary btn-sm" title="Watch" onclick="ytStudio.watchRender(${p.id}, ${r.id}, ${jsArg(r.version)})"><i class="fa-solid fa-play"></i></button>
                    <button class="btn btn-secondary btn-sm" title="Download MP4" onclick="ytStudio.download('/projects/${p.id}/renders/${r.id}/video', 'project-${p.id}-render-${r.id}.mp4', ${jsArg(r.version)})"><i class="fa-solid fa-download"></i></button>
                </div></td>
            </tr>`;

        return `
            <div class="yt-section-title">Video ${latest && latest.current ? '<span class="badge badge-approved">rendered</span>' : ''}</div>
            <p class="yt-muted">${animation}${cost ? ` · ${cost}` : ''}${est.video_seconds ? ` · ${formatSeconds(est.video_seconds)} long` : ''}</p>
            <div class="yt-actions" style="margin:10px 0 14px">
                <button class="btn btn-primary btn-sm" ${canRender ? '' : 'disabled'} title="${attr(hint)}"
                    onclick="ytStudio.renderVideo(${p.id})"><i class="fa-solid fa-film"></i>
                    <span>${p.status === 'rendering' ? 'Rendering…' : (latest ? 'Render again' : 'Render video')}</span></button>
                ${p.status === 'rendering' ? '<span class="yt-muted">This takes a few minutes; the page updates when it finishes.</span>' : ''}
            </div>
            ${latest ? `
            <div class="yt-video ${p.format === 'short' ? 'short' : ''}" id="yt-video-${p.id}">
                <button class="yt-video-poster" title="Watch" onclick="ytStudio.watchRender(${p.id}, ${latest.id}, ${jsArg(latest.version)})">
                    ${imgTag(`/projects/${p.id}/renders/${latest.id}/thumbnail`, latest.version, 'Video thumbnail')}
                    <span class="yt-video-play"><i class="fa-solid fa-play"></i></span>
                </button>
            </div>
            <div class="documents-table-wrapper"><table class="data-table">
                <thead><tr><th>Render</th><th>Made</th><th>Length</th><th>Size</th><th>Avatar</th><th></th></tr></thead>
                <tbody>${video.renders.map(renderRow).join('')}</tbody>
            </table></div>` : ''}`;
    }

    function renderProject(p) {
        const card = document.getElementById('yt-project-detail');
        const busy = BUSY.has(p.status);
        const castReady = p.cast.length > 0 && p.cast.every(c => c.has_voice);
        const characterOptions = selected => ['<option value="">— unassigned —</option>']
            .concat(state.characters.map(c =>
                `<option value="${c.id}" ${c.id === selected ? 'selected' : ''}>${escapeHTML(c.name)}${c.voice_id ? '' : ' (no voice)'}</option>`))
            .join('');

        card.innerHTML = `
            <div class="yt-detail-head">
                <div>
                    <h2>${escapeHTML(p.topic)}</h2>
                    <div class="yt-actions">
                        ${approvalBadge(p.approval)}
                        <span class="badge badge-neutral">${escapeHTML(FORMAT_LABELS[p.format] || p.format)}</span>
                        <span class="output-status ${busy ? 'running' : ''}">${escapeHTML(p.status)}</span>
                        <span class="yt-muted">${formatSeconds(p.duration_s)}${p.duration_is_estimate && p.duration_s ? ' estimated' : ''}</span>
                    </div>
                    ${p.warnings.map(w => `<p class="yt-warning"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHTML(w)}</p>`).join('')}
                    ${p.error ? `<p class="yt-error"><i class="fa-solid fa-circle-exclamation"></i> ${escapeHTML(p.error)}</p>` : ''}
                </div>
                <div class="yt-actions">
                    <button class="btn btn-secondary btn-sm" ${busy ? 'disabled' : ''} onclick="ytStudio.planProject(${p.id}, ${p.shots.length > 0})">
                        <i class="fa-solid fa-list-ol"></i> <span>${p.shots.length ? 'Re-plan' : 'Plan shots'}</span></button>
                    <button class="btn btn-primary btn-sm" ${busy || !castReady ? 'disabled' : ''}
                        title="${castReady ? '' : 'Give every speaker a character with a voice first'}"
                        onclick="ytStudio.voiceProject(${p.id}, false)">
                        <i class="fa-solid fa-microphone"></i> <span>${p.has_audio ? 'Update voices' : 'Voice script'}</span></button>
                    <button class="btn btn-sm btn-danger" ${busy ? 'disabled' : ''} onclick="ytStudio.deleteProject(${p.id})" title="Delete project">
                        <i class="fa-regular fa-trash-can"></i></button>
                </div>
            </div>

            ${p.has_audio ? `
                <div class="yt-section-title">Voice track</div>
                <div class="yt-actions">
                    <button class="btn btn-secondary btn-sm" onclick="ytStudio.play('/projects/${p.id}/audio', ${jsArg(p.duration_s)})"><i class="fa-solid fa-play"></i> <span>Play</span></button>
                    <button class="btn btn-secondary btn-sm" onclick="ytStudio.download('/projects/${p.id}/audio', 'project-${p.id}-voice.wav', ${jsArg(p.duration_s)})"><i class="fa-solid fa-download"></i> <span>Download WAV</span></button>
                </div>` : ''}

            <div class="yt-section-title">Cast</div>
            ${p.cast.length ? `
            <div class="documents-table-wrapper"><table class="data-table">
                <thead><tr><th>Speaker</th><th>Character</th><th>Planner’s note</th></tr></thead>
                <tbody>${p.cast.map(c => `
                    <tr>
                        <td><strong>${escapeHTML(c.speaker)}</strong></td>
                        <td><select ${busy ? 'disabled' : ''} onchange="ytStudio.castSpeaker(${p.id}, ${jsArg(c.speaker)}, this.value)">${characterOptions(c.character_id)}</select>
                            ${c.character_id && !c.has_voice ? '<span class="yt-warning">no voice</span>' : ''}</td>
                        <td class="yt-muted">${escapeHTML(c.description || '')}</td>
                    </tr>`).join('')}</tbody>
            </table></div>
            ${state.characters.length ? '' : '<p class="yt-muted">Create characters in the Characters tab, then assign them here.</p>'}` :
            `<p class="yt-muted">${busy ? 'Planning…' : 'Plan the shots to see who speaks.'}</p>`}

            ${p.shots.length ? `
            <div class="yt-section-title">Shots (${p.shots.length})</div>
            <div class="documents-table-wrapper"><table class="data-table">
                <thead><tr><th>#</th><th>Speaker</th><th>Type</th><th>Line</th><th></th></tr></thead>
                <tbody>${p.shots.map(s => `
                    <tr>
                        <td>${s.position}</td>
                        <td><a href="#" class="yt-speaker-link" title="Change who speaks this line" onclick="ytStudio.changeSpeaker(${p.id}, ${s.id}, ${jsArg(s.speaker)}); return false;">${escapeHTML(s.speaker)}</a></td>
                        <td><span class="badge badge-neutral">${escapeHTML(s.shot_type.replace('_', ' '))}</span></td>
                        <td class="yt-shot-text">${escapeHTML(s.text)}${s.delivery ? `<div class="yt-muted">(${escapeHTML(s.delivery)})</div>` : ''}</td>
                        <td>${s.has_audio ? `<button class="btn btn-secondary btn-sm" title="Play ${formatSeconds(s.duration_s)}" onclick="ytStudio.play('/projects/${p.id}/shots/${s.id}/audio', ${jsArg(s.duration_s)})"><i class="fa-solid fa-play"></i></button>` : ''}</td>
                    </tr>`).join('')}</tbody>
            </table></div>
            ${renderStoryboard(p, busy)}
            ${videoSection(p, busy)}` : ''}
        `;
        hydrateImages(card);
    }

    async function planProject(projectId, replanning) {
        if (replanning && !confirm('Re-planning replaces the shots and discards their audio and images. Continue?')) return;
        try {
            await api(`/projects/${projectId}/plan`, { method: 'POST' });
            forgetFiles(`/projects/${projectId}`);
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function voiceProject(projectId, force) {
        try {
            await api(`/projects/${projectId}/voice`, { method: 'POST', body: JSON.stringify({ force }) });
            showToast('Voicing started. This page updates when it finishes.', 'success');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function castSpeaker(projectId, speaker, characterId) {
        try {
            const project = await api(`/projects/${projectId}/cast`, {
                method: 'PUT',
                body: JSON.stringify({ assignments: { [speaker]: characterId ? Number(characterId) : null } }),
            });
            renderProject(project);
        } catch (err) {
            toastError(err);
            openProject(projectId);
        }
    }

    async function changeSpeaker(projectId, shotId, current) {
        const speaker = prompt('Who speaks this line? Use NARRATOR for narration.', current);
        if (!speaker || speaker.trim().toUpperCase() === current) return;
        try {
            renderProject(await api(`/projects/${projectId}/shots/${shotId}`, {
                method: 'PATCH', body: JSON.stringify({ speaker }),
            }));
        } catch (err) {
            toastError(err);
        }
    }

    async function setProjectStyle(projectId, styleId) {
        try {
            renderProject(await api(`/projects/${projectId}`, {
                method: 'PATCH', body: JSON.stringify({ style_id: styleId ? Number(styleId) : null }),
            }));
        } catch (err) {
            toastError(err);
            openProject(projectId);
        }
    }

    async function generateStoryboard(projectId, force) {
        if (force && !confirm('Redraw every shot? This replaces all images and costs the full storyboard price again.')) return;
        try {
            await api(`/projects/${projectId}/storyboard`, { method: 'POST', body: JSON.stringify({ force }) });
            showToast('Drawing the storyboard. Images appear as they finish.', 'success');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function changeShotType(projectId, shotId, shotType) {
        try {
            renderProject(await api(`/projects/${projectId}/shots/${shotId}`, {
                method: 'PATCH', body: JSON.stringify({ shot_type: shotType }),
            }));
        } catch (err) {
            toastError(err);
            openProject(projectId);
        }
    }

    async function redrawShot(projectId, shotId) {
        const textarea = document.getElementById(`yt-visual-${shotId}`);
        try {
            await api(`/projects/${projectId}/shots/${shotId}`, {
                method: 'PATCH', body: JSON.stringify({ visual: textarea.value }),
            });
            await api(`/projects/${projectId}/shots/${shotId}/image`, { method: 'POST' });
            showToast('Redrawing this shot…', 'success');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function approveStoryboard(projectId) {
        try {
            await api(`/projects/${projectId}/storyboard/approve`, { method: 'POST' });
            showToast('Storyboard approved', 'success');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function renderVideo(projectId) {
        const start = confirmOverBudget => api(`/projects/${projectId}/render`, {
            method: 'POST', body: JSON.stringify({ confirm_over_budget: confirmOverBudget }),
        });
        try {
            try {
                await start(false);
            } catch (err) {
                if (err.status !== 409 || !err.body || !err.body.detail || !err.body.detail.estimate) throw err;
                if (!confirm(`${err.message}\n\nRender anyway?`)) return;
                await start(true);
            }
            showToast('Rendering started. This page updates when the video is ready.', 'success');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    async function watchRender(projectId, renderId, version) {
        const holder = document.getElementById(`yt-video-${projectId}`);
        if (!holder) return;
        try {
            const url = await fileUrl(`/projects/${projectId}/renders/${renderId}/video`, version);
            holder.innerHTML = `<video controls autoplay playsinline src="${attr(url)}"></video>`;
            holder.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            toastError(err);
        }
    }

    async function deleteProject(projectId) {
        if (!confirm('Delete this project with its audio, images and videos? The marketing script is not affected.')) return;
        try {
            await api(`/projects/${projectId}`, { method: 'DELETE' });
            forgetFiles(`/projects/${projectId}`);
            state.projectId = null;
            document.getElementById('yt-project-detail').classList.add('hidden');
            loadProjects();
        } catch (err) {
            toastError(err);
        }
    }

    window.ytStudio = {
        open, showTab, loadScripts, createProject,
        loadCharacters, createCharacter, changeVoice, deleteCharacter,
        previewVoice, previewSelectedVoice, generatePreviews,
        openCharacter, closeCharacter, confirmRights, uploadFace, deleteCharacterImage, generateSheet, approveSheet,
        loadStyles, createStyle, saveStyle, uploadStyleReference, removeStyleReference, deleteStyle,
        loadProjects, openProject, planProject, voiceProject, castSpeaker, changeSpeaker, deleteProject,
        setProjectStyle, generateStoryboard, changeShotType, redrawShot, approveStoryboard,
        renderVideo, watchRender,
        play, download,
    };
})();
