import React, { useState, useRef, useEffect } from 'react';
import { useOutletContext } from 'react-router-dom';
import api from '../services/api';
import { marked } from 'marked';

const GeneratorPanel = () => {
  const { user } = useOutletContext<any>();
  const [contentType, setContentType] = useState('press_release');
  const [topic, setTopic] = useState('');
  // On by default: the Parallel Search call in the researcher node is the
  // live partner integration, and starting the checkbox unticked meant the
  // default path through the UI never made one.
  const [useSearch, setUseSearch] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);

  const [status, setStatus] = useState('Idle');
  const [logs, setLogs] = useState<{ time: string, msg: string, type: string }[]>([]);
  const [generationId, setGenerationId] = useState('');
  const [generatedText, setGeneratedText] = useState('');

  const [showFeedback, setShowFeedback] = useState(false);
  const [humanApproved, setHumanApproved] = useState(true);
  const [humanScore, setHumanScore] = useState(8.5);
  const [humanFeedback, setHumanFeedback] = useState('');
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [quotaError, setQuotaError] = useState<{ limit: number; used: number; reset_date: string } | null>(null);

  const consoleEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Auto-scroll to bottom of logs
    if (consoleEndRef.current) {
      consoleEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, generatedText]);

  const addLog = (msg: string, type = '') => {
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    setLogs(prev => [...prev, { time, msg, type }]);
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsGenerating(true);
    setLogs([]);
    setGeneratedText('');
    setShowFeedback(false);
    setGenerationId('');
    setQuotaError(null);
    setStatus('Initializing');
    addLog('Connecting to BrandMaestro AI content generator pipeline...', 'highlight');

    try {
      const token = localStorage.getItem('bg_access_token');
      const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/conversation/generate/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          business_id: user.business_id,
          content_type: contentType,
          topic,
          use_search: useSearch
        })
      });

      if (response.status === 429) {
        const body = await response.json().catch(() => ({}));
        const detail = body?.detail;
        if (detail?.code === 'QUOTA_EXHAUSTED') {
          setQuotaError(detail);
          setStatus('Limit Reached');
          setIsGenerating(false);
          return;
        }
      }

      if (!response.ok) throw new Error('Failed to initiate stream request');
      if (!response.body) throw new Error('ReadableStream not supported in this browser.');

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let genId = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Save last unfinished item in buffer

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const chunk = JSON.parse(line);
            if (chunk.generation_id && !genId) {
              genId = chunk.generation_id;
              setGenerationId(genId);
              addLog(`Generation ID established: ${genId}`);
              continue;
            }

            let stateUpdate = chunk;
            const keys = Object.keys(chunk);
            if (keys.length === 1 && typeof chunk[keys[0]] === 'object' && chunk[keys[0]] !== null) {
              stateUpdate = chunk[keys[0]];
            }

            if (stateUpdate.research) addLog(`[Search Synthesis] Researched contexts: ${stateUpdate.research.substring(0, 100)}...`);
            if (stateUpdate.creative_angle) {
              setStatus('Creating Angle');
              addLog(`[Creative Director] Angle: ${stateUpdate.creative_angle}`, 'highlight');
            }
            if (stateUpdate.status) setStatus(stateUpdate.status.toUpperCase());
            if (stateUpdate.score !== undefined) addLog(`[Auditor Evaluator] Compliance Score: ${stateUpdate.score} / 10`);
            if (stateUpdate.content) setGeneratedText(stateUpdate.content);
            if (stateUpdate.feedback) addLog(`[Auditor Feedback] Revisions: ${stateUpdate.feedback}`);
          } catch (err) {
            addLog(line); // Fallback for raw lines
          }
        }
      }

      setStatus('Completed');
      addLog('Content synthesis completed successfully!', 'success');
      setShowFeedback(true);
    } catch (err: any) {
      addLog(`Error: ${err.message}`, 'error');
      setStatus('Failed');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleFeedback = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmittingFeedback(true);
    try {
      await api.post('/conversation/feedback', {
        generation_id: generationId,
        business_id: user.business_id,
        content_type: contentType,
        human_approved: humanApproved,
        human_score: humanScore,
        human_feedback: humanFeedback
      });
      alert('Feedback submitted! Model alignment retrained.');
      setShowFeedback(false);
      setHumanFeedback('');
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to submit feedback');
    } finally {
      setIsSubmittingFeedback(false);
    }
  };

  return (
    <section className="dashboard-panel active">
      <div className="generator-split">
        {/* Form Controls */}
        <div className="content-card">
          <h2>Configure Creative Output</h2>
          <p className="card-subtitle">Define parameters to generate on-brand corporate messaging.</p>

          {quotaError && (
            <div className="quota-exhausted-banner">
              <div className="quota-icon"><i className="fa-solid fa-circle-exclamation"></i></div>
              <div className="quota-body">
                <strong>Daily Generation Limit Reached</strong>
                <p>
                  You've used <span className="quota-count">{quotaError.used}&thinsp;/&thinsp;{quotaError.limit}</span> generations today.
                  Your quota resets tomorrow on <span className="quota-reset">{quotaError.reset_date}</span>.
                </p>
              </div>
            </div>
          )}

          <form onSubmit={handleGenerate}>
            <div className="input-group">
              <label>Content Format Profile</label>
              <div className="format-cards">
                <label className={`format-card ${contentType === 'press_release' ? 'active' : ''}`}>
                  <input type="radio" value="press_release" checked={contentType === 'press_release'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-newspaper"></i>
                  <span>Press Release</span>
                </label>
                <label className={`format-card ${contentType === 'social' ? 'active' : ''}`}>
                  <input type="radio" value="social" checked={contentType === 'social'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-brands fa-instagram"></i>
                  <span>Premiere / Social Campaign</span>
                </label>
                <label className={`format-card ${contentType === 'trailer_copy' ? 'active' : ''}`}>
                  <input type="radio" value="trailer_copy" checked={contentType === 'trailer_copy'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-clapperboard"></i>
                  <span>Trailer / Campaign Copy</span>
                </label>
                <label className={`format-card ${contentType === 'talent_bio' ? 'active' : ''}`}>
                  <input type="radio" value="talent_bio" checked={contentType === 'talent_bio'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-user-tie"></i>
                  <span>Talent Bio</span>
                </label>
                <label className={`format-card ${contentType === 'synopsis' ? 'active' : ''}`}>
                  <input type="radio" value="synopsis" checked={contentType === 'synopsis'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-film"></i>
                  <span>Show / Title Synopsis</span>
                </label>
                <label className={`format-card ${contentType === 'blog' ? 'active' : ''}`}>
                  <input type="radio" value="blog" checked={contentType === 'blog'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-pen-nib"></i>
                  <span>Behind-the-Scenes Post</span>
                </label>
                <label className={`format-card ${contentType === 'proposal' ? 'active' : ''}`}>
                  <input type="radio" value="proposal" checked={contentType === 'proposal'} onChange={e => setContentType(e.target.value)} />
                  <i className="fa-solid fa-file-contract"></i>
                  <span>Pitch / Greenlight Deck</span>
                </label>
              </div>
            </div>

            <div className="input-group">
              <label>Core Subject / Campaign Topic</label>
              <input type="text" required value={topic} onChange={e => setTopic(e.target.value)} placeholder="e.g. Next-Generation RAG Optimization" />
            </div>


            <div className="input-row toggle-row">
              <div className="toggle-container">
                <span className="toggle-label"><i className="fa-solid fa-globe"></i> Activate Real-Time Web Search</span>
                <label className="switch">
                  <input type="checkbox" checked={useSearch} onChange={e => setUseSearch(e.target.checked)} />
                  <span className="slider"></span>
                </label>
              </div>
            </div>

            <button type="submit" className={`btn btn-primary btn-block ${isGenerating ? 'loading' : ''}`} disabled={isGenerating}>
              <i className="fa-solid fa-wand-magic-sparkles"></i>
              <span>{isGenerating ? 'Synthesizing...' : 'Initiate Stream Generation'}</span>
            </button>
          </form>
        </div>

        {/* Live Output Terminal */}
        <div className="content-card output-card">
          <div className="output-header">
            <h2><i className="fa-solid fa-terminal"></i> Synthesizer Output Stream</h2>
            <div className={`output-status ${isGenerating ? 'running' : ''}`}>{status}</div>
          </div>

          <div className="stream-console">
            {logs.length === 0 && !isGenerating && (
              <span className="placeholder-text"><i className="fa-solid fa-arrow-left"></i> Configure inputs and trigger the generation stream.</span>
            )}
            {logs.map((log, idx) => (
              <div key={idx} className={`console-log ${log.type}`}>
                <span className="timestamp">[{log.time}]</span> {log.msg}
              </div>
            ))}
            <div ref={consoleEndRef} />
          </div>

          {generatedText && (
            <div className="generated-output-box">
              <div className="content-rendered-header">
                <h3>Generated Draft</h3>
                <button className="btn btn-secondary btn-sm" onClick={() => { navigator.clipboard.writeText(generatedText); alert("Copied!"); }}><i className="fa-regular fa-copy"></i> Copy</button>
              </div>
              <div className="rendered-markdown" dangerouslySetInnerHTML={{ __html: marked.parse(generatedText) }} />
            </div>
          )}

          {showFeedback && (
            <div className="feedback-card">
              <div className="card-header-accent">
                <h3><i className="fa-regular fa-thumbs-up"></i> Quality Inspection & Learning Feedback</h3>
                <p>Submit scores to retrain and refine the underlying Brand Voice agent.</p>
              </div>
              <form onSubmit={handleFeedback}>
                <div className="feedback-metrics-row">
                  <div className="feedback-approve-switch">
                    <span className="label">Integrity Status:</span>
                    <div className="approve-badge-toggle">
                      <label className="approve-toggle">
                        <input type="checkbox" checked={humanApproved} onChange={e => setHumanApproved(e.target.checked)} />
                        <span className={`approve-label-badge ${humanApproved ? 'approved' : ''}`}>{humanApproved ? 'APPROVED' : 'REJECTED'}</span>
                      </label>
                    </div>
                  </div>
                  <div className="feedback-score-slider">
                    <div className="slider-header">
                      <span className="label">Tone Compliance Score:</span>
                      <span className="score-value">{humanScore} / 10</span>
                    </div>
                    <input type="range" min="0" max="10" step="0.5" value={humanScore} onChange={e => setHumanScore(parseFloat(e.target.value))} />
                  </div>
                </div>

                <div className="input-group">
                  <label>Directives / Corrections / Voice Notes</label>
                  <textarea rows={3} required value={humanFeedback} onChange={e => setHumanFeedback(e.target.value)} placeholder="Describe matches or deviations..."></textarea>
                </div>

                <button type="submit" className={`btn btn-accent btn-block ${isSubmittingFeedback ? 'loading' : ''}`} disabled={isSubmittingFeedback}>
                  <i className="fa-solid fa-square-poll-horizontal"></i>
                  <span>Commit Feedback to Model Memory</span>
                </button>
              </form>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default GeneratorPanel;
