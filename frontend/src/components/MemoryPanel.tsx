import React, { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import api from '../services/api';

const MemoryPanel = () => {
  const { user } = useOutletContext<any>();
  const [contentType, setContentType] = useState('press_release');
  const [approvedPatterns, setApprovedPatterns] = useState<any[]>([]);
  const [rejectedPatterns, setRejectedPatterns] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  useEffect(() => {
    const fetchPatterns = async () => {
      setIsLoading(true);
      setErrorMsg('');
      try {
        const res = await api.get(`/conversation/patterns/${user.business_id}/${contentType}`);
        setApprovedPatterns(res.data.patterns?.approved || []);
        setRejectedPatterns(res.data.patterns?.rejected || []);
      } catch (err: any) {
        setErrorMsg(err.message || 'Failed to query patterns library');
      } finally {
        setIsLoading(false);
      }
    };
    fetchPatterns();
  }, [contentType, user.business_id]);

  return (
    <section className="dashboard-panel active">
      <div className="card-header-accent" style={{ marginBottom: '24px', borderRadius: '12px' }}>
        <h2><i className="fa-solid fa-brain"></i> Active Brand Memory & Stylistic Rules</h2>
        <p>This panel displays the exact guardrails the AI enforces during generation. These rules are synthesized autonomously from your human feedback and top-performing documents.</p>
      </div>

      <div className="filter-tabs">
        <button className={`filter-btn ${contentType === 'press_release' ? 'active' : ''}`} onClick={() => setContentType('press_release')}>Press Voice</button>
        <button className={`filter-btn ${contentType === 'social' ? 'active' : ''}`} onClick={() => setContentType('social')}>Social Voice</button>
        <button className={`filter-btn ${contentType === 'trailer_copy' ? 'active' : ''}`} onClick={() => setContentType('trailer_copy')}>Trailer Voice</button>
        <button className={`filter-btn ${contentType === 'talent_bio' ? 'active' : ''}`} onClick={() => setContentType('talent_bio')}>Talent Bio Voice</button>
      </div>

      <div className="generator-split">
        {/* Approved Core Guidelines */}
        <div className="content-card">
          <div className="card-header">
            <h3><i className="fa-solid fa-shield-heart"></i> Core Voice Identity & Requirements</h3>
            <span className="badge badge-accent">{approvedPatterns.length} rules active</span>
          </div>
          <div className="patterns-list">
            {isLoading && <div className="empty-state"><i className="fa-solid fa-spinner fa-spin"></i><p>Querying guidelines databases...</p></div>}
            {!isLoading && errorMsg && <div className="empty-state"><p>Error: {errorMsg}</p></div>}
            {!isLoading && !errorMsg && approvedPatterns.length === 0 && (
              <div className="empty-state">
                <i className="fa-solid fa-shield-heart"></i>
                <p>No verified style guideposts synthesized for "{contentType.toUpperCase()}" outputs yet. Submit positive human score feedback to build memory rules.</p>
              </div>
            )}
            {!isLoading && !errorMsg && approvedPatterns.map((p, i) => (
              <div key={i} className="pattern-item approved">
                <div className="angle"><i className="fa-solid fa-circle-check" style={{ color: 'var(--accent-green)' }}></i> {p.angle || 'General Style Alignment'}</div>
                <div className="feedback">{p.feedback}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Rejected Boundaries */}
        <div className="content-card">
          <div className="card-header">
            <h3><i className="fa-solid fa-ban"></i> Enforcement Boundaries & Red-lines</h3>
          </div>
          <div className="patterns-list">
            {isLoading && <div className="empty-state"><i className="fa-solid fa-spinner fa-spin"></i><p>Querying alignment metrics...</p></div>}
            {!isLoading && errorMsg && <div className="empty-state"><p>Error: {errorMsg}</p></div>}
            {!isLoading && !errorMsg && rejectedPatterns.length === 0 && (
              <div className="empty-state">
                <i className="fa-solid fa-ban"></i>
                <p>No rejection thresholds mapped for "{contentType.toUpperCase()}" outputs yet. Mark unsatisfactory content as rejected to define red-lines.</p>
              </div>
            )}
            {!isLoading && !errorMsg && rejectedPatterns.map((p, i) => (
              <div key={i} className="pattern-item rejected">
                <div className="angle"><i className="fa-solid fa-circle-xmark" style={{ color: 'var(--primary-red)' }}></i> {p.angle || 'Voice Red-line Exception'}</div>
                <div className="feedback">{p.feedback}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default MemoryPanel;
