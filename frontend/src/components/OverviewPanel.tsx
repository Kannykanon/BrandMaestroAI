import React from 'react';
import { useNavigate } from 'react-router-dom';

const OverviewPanel = () => {
  const navigate = useNavigate();

  return (
    <section className="dashboard-panel active">
      <div className="stats-grid">
        <div className="stat-card">
          <div className="icon-wrapper"><i className="fa-solid fa-shield-halved"></i></div>
          <div className="stat-details">
            <h3>Brand Integrity</h3>
            <div className="value">98.4%</div>
            <p className="trend positive"><i className="fa-solid fa-arrow-trend-up"></i> +1.2% this week</p>
          </div>
        </div>
        <div className="stat-card">
          <div className="icon-wrapper"><i className="fa-solid fa-bolt"></i></div>
          <div className="stat-details">
            <h3>Content Generations</h3>
            <div className="value">0</div>
            <p className="trend"><i className="fa-regular fa-clock"></i> Active session counters</p>
          </div>
        </div>
        <div className="stat-card">
          <div className="icon-wrapper"><i className="fa-solid fa-file-shield"></i></div>
          <div className="stat-details">
            <h3>RAG Documents</h3>
            <div className="value">0</div>
            <p className="trend positive"><i className="fa-solid fa-circle-check"></i> Vector indexing online</p>
          </div>
        </div>
      </div>

      <div className="dashboard-content-split">
        {/* Generation History & Quick Start */}
        <div className="content-card">
          <div className="card-header">
            <h2><i className="fa-regular fa-clock"></i> Recent Generations & Synthesis</h2>
            <button className="btn btn-secondary btn-sm" onClick={() => navigate('/dashboard/generator')}>Create New</button>
          </div>
          <div className="history-list">
            <div className="empty-state">
              <i className="fa-regular fa-folder-open"></i>
              <p>No generations made yet during this session. Head over to the Content Generator to begin.</p>
            </div>
          </div>
        </div>

        {/* Side card: Brand Profile Summary */}
        <div className="content-card secondary-card">
          <div className="card-header">
            <h2><i className="fa-solid fa-brain"></i> Memory Synthesis</h2>
          </div>
          <div className="brand-voice-preview">
            <div className="preview-item">
              <span className="label">Brand Voice Tone</span>
              <div className="progress-bar-container">
                <div className="progress-bar-fill" style={{ width: '85%' }}></div>
              </div>
              <span className="percentage">85% Professional / Confident</span>
            </div>
            <div className="preview-item">
              <span className="label">Approved Rules Cached</span>
              <span className="count">0 rules active</span>
            </div>
            <div className="preview-item">
              <span className="label">Target Audience Alignment</span>
              <div className="progress-bar-container">
                <div className="progress-bar-fill" style={{ width: '92%' }}></div>
              </div>
              <span className="percentage">92% B2B Executive Tone</span>
            </div>
          </div>
          <div className="info-alert">
            <i className="fa-solid fa-circle-info"></i>
            <p>BrandMuse AI automatically synthesizes feedback to continuously align generated outputs with your top-performing uploaded documents.</p>
          </div>
        </div>
      </div>
    </section>
  );
};

export default OverviewPanel;
