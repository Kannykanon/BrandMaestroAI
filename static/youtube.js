// YouTube Studio: the optional YouTube Automation panel.
//
// Kept apart from app.js on purpose. It uses only app.js's shared helpers
// (appState, API_BASE, showToast, escapeHTML) and talks to the /youtube routes.
// If this file fails to load, the marketing panels are unaffected.
(function () {
    'use strict';

    const state = {
        tab: 'scripts',
        voices: [],
        voiceProvider: null,
        characters: [],
        projectId: null,
        pollTimer: null,
        audioUrls: {},
    };

    const BUSY = new Set(['planning', 'voicing']);
    const FORMAT_LABELS = { long_form: 'Long-form 16:9', short: 'Short 9:16' };

    // --- HTTP -----------------------------------------------------------------

    async function api(path, options = {}) {
        const headers = { Authorization: `Bearer ${appState.token}`, ...(options.headers || {}) };
        if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
        const response = await fetch(`${API_BASE}/youtube${path}`, { ...options, headers });
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            const detail = Array.isArray(body.detail) ? body.detail.map(d => d.msg).join('; ') : body.detail;
            throw new Error(detail || `Request failed (${response.status})`);
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

    // Audio needs the bearer token, which an <audio> element cannot send. Ask
    // for a signed link first; if the store cannot sign one, download through
    // the API and play a local object URL.
    async function audioUrl(path) {
        if (state.audioUrls[path]) return state.audioUrls[path];
        const link = await api(`${path}?as_link=true`);
        let url = link.url;
        if (!url) {
            const response = await fetch(`${API_BASE}/youtube${path}`, {
                headers: { Authorization: `Bearer ${appState.token}` },
            });
            if (!response.ok) throw new Error('Audio is not available');
            url = URL.createObjectURL(await response.blob());
        }
        state.audioUrls[path] = url;
        return url;
    }

    function forgetAudio(prefix) {
        Object.keys(state.audioUrls).forEach(path => {
            if (!prefix || path.startsWith(prefix)) {
                if (state.audioUrls[path].startsWith('blob:')) URL.revokeObjectURL(state.audioUrls[path]);
                delete state.audioUrls[path];
            }
        });
    }

    async function play(path) {
        try {
            const player = document.getElementById('yt-player');
            player.src = await audioUrl(path);
            await player.play();
        } catch (err) {
            toastError(err);
        }
    }

    async function download(path, filename) {
        try {
            const link = document.createElement('a');
            link.href = await audioUrl(path);
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
        ['scripts', 'characters', 'projects'].forEach(name => {
            document.getElementById(`yt-view-${name}`).classList.toggle('hidden', name !== tab);
            document.getElementById(`yt-tab-${name}`).classList.toggle('active', name === tab);
        });
        if (tab === 'scripts') loadScripts();
        if (tab === 'characters') loadCharacters();
        if (tab === 'projects') loadProjects();
    }

    async function open() {
        try {
            const status = await api('/status');
            const notice = document.getElementById('yt-notice');
            const messages = [];
            if (status.storage && !status.storage.configured) {
                messages.push(`File storage is not configured (missing ${escapeHTML((status.storage.missing || []).join(', '))}).`);
            } else if (status.storage && status.storage.provider === 'local') {
                messages.push('Files are stored on this server’s disk (development storage).');
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

    async function loadCharacters() {
        const body = document.getElementById('yt-characters-body');
        body.innerHTML = '<tr><td colspan="4" class="table-empty"><i class="fa-solid fa-spinner fa-spin"></i></td></tr>';
        try {
            await loadVoices();
            document.getElementById('yt-char-voice').innerHTML = voiceOptions(null);
            const { characters } = await api('/characters');
            state.characters = characters;
            if (!characters.length) {
                body.innerHTML = '<tr><td colspan="4" class="table-empty"><p>No characters yet. Add a narrator and one character per speaker.</p></td></tr>';
                return;
            }
            body.innerHTML = characters.map(c => `
                <tr>
                    <td><strong>${escapeHTML(c.name)}</strong></td>
                    <td><select onchange="ytStudio.changeVoice(${c.id}, this.value)">
                        ${c.voice_id ? '' : '<option value="" selected>— choose —</option>'}${voiceOptions(c.voice_id)}</select>
                        ${c.voice_id ? `<button class="btn btn-secondary btn-sm" title="Hear this voice" onclick="ytStudio.previewVoice(${jsArg(c.voice_provider || state.voiceProvider)}, ${jsArg(c.voice_id)})"><i class="fa-solid fa-play"></i></button>` : ''}</td>
                    <td class="yt-muted">${escapeHTML(c.style_notes || '')}</td>
                    <td><button class="btn btn-sm btn-danger" title="Delete character" onclick="ytStudio.deleteCharacter(${c.id})"><i class="fa-regular fa-trash-can"></i></button></td>
                </tr>`).join('');
        } catch (err) {
            body.innerHTML = `<tr><td colspan="4" class="table-empty"><p>${escapeHTML(err.message)}</p></td></tr>`;
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
            if (!state.characters.length) {
                await loadVoices();
                state.characters = (await api('/characters')).characters;
            }
            const project = await api(`/projects/${projectId}`);
            renderProject(project);
            if (BUSY.has(project.status)) {
                state.pollTimer = setTimeout(() => {
                    if (state.tab === 'projects' && state.projectId === projectId) {
                        forgetAudio(`/projects/${projectId}`);
                        loadProjects();
                    }
                }, 4000);
            }
        } catch (err) {
            card.innerHTML = `<p class="yt-error">${escapeHTML(err.message)}</p>`;
        }
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
                    <button class="btn btn-secondary btn-sm" onclick="ytStudio.play('/projects/${p.id}/audio')"><i class="fa-solid fa-play"></i> <span>Play</span></button>
                    <button class="btn btn-secondary btn-sm" onclick="ytStudio.download('/projects/${p.id}/audio', 'project-${p.id}-voice.wav')"><i class="fa-solid fa-download"></i> <span>Download WAV</span></button>
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
                <thead><tr><th>#</th><th>Speaker</th><th>Type</th><th>Line</th><th>On screen</th><th></th></tr></thead>
                <tbody>${p.shots.map(s => `
                    <tr>
                        <td>${s.position}</td>
                        <td><a href="#" class="yt-speaker-link" title="Change who speaks this line" onclick="ytStudio.changeSpeaker(${p.id}, ${s.id}, ${jsArg(s.speaker)}); return false;">${escapeHTML(s.speaker)}</a></td>
                        <td><span class="badge badge-neutral">${escapeHTML(s.shot_type.replace('_', ' '))}</span></td>
                        <td class="yt-shot-text">${escapeHTML(s.text)}${s.delivery ? `<div class="yt-muted">(${escapeHTML(s.delivery)})</div>` : ''}</td>
                        <td class="yt-muted">${escapeHTML(s.visual || '')}</td>
                        <td>${s.has_audio ? `<button class="btn btn-secondary btn-sm" title="Play ${formatSeconds(s.duration_s)}" onclick="ytStudio.play('/projects/${p.id}/shots/${s.id}/audio')"><i class="fa-solid fa-play"></i></button>` : ''}</td>
                    </tr>`).join('')}</tbody>
            </table></div>` : ''}
        `;
    }

    async function planProject(projectId, replanning) {
        if (replanning && !confirm('Re-planning replaces the shots and discards their audio. Continue?')) return;
        try {
            await api(`/projects/${projectId}/plan`, { method: 'POST' });
            forgetAudio(`/projects/${projectId}`);
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
            forgetAudio(`/projects/${projectId}`);
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
            forgetAudio(`/projects/${projectId}`);
        } catch (err) {
            toastError(err);
        }
    }

    async function deleteProject(projectId) {
        if (!confirm('Delete this project and its audio? The marketing script is not affected.')) return;
        try {
            await api(`/projects/${projectId}`, { method: 'DELETE' });
            forgetAudio(`/projects/${projectId}`);
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
        loadProjects, openProject, planProject, voiceProject, castSpeaker, changeSpeaker, deleteProject,
        play, download,
    };
})();
