import React, { useEffect, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import api from '../services/api';

const DashboardLayout = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    const fetchUser = async () => {
      try {
        const res = await api.get('/users/me');
        setUser(res.data);
      } catch (err) {
        // Token expired or invalid
        localStorage.removeItem('bg_access_token');
        navigate('/auth');
      }
    };
    fetchUser();
  }, [navigate]);

  const handleLogout = () => {
    localStorage.removeItem('bg_access_token');
    window.location.href = '/auth';
  };

  const copyBusinessId = () => {
    if (user?.business_id) {
      navigator.clipboard.writeText(user.business_id);
      alert('Business ID copied to clipboard');
    }
  };

  // Determine Title based on route
  const getPageTitle = () => {
    if (location.pathname.includes('generator')) return { t: 'Content Synthesizer', s: 'Generate high-fidelity marketing collateral tailored using deep brand voice RAG filters.' };
    if (location.pathname.includes('documents')) return { t: 'Guidelines & Reference Documents', s: 'Manage reference text sources mapped to feed the Brand Memory vectors.' };
    if (location.pathname.includes('memory')) return { t: 'Active Model Memory & Synapses', s: 'Explore brand-aligned guidelines and restrictions synthesized directly from human review loops.' };
    return { t: 'Dashboard Overview', s: 'Track and configure your brand voice rules, generation capacity, and memory metrics.' };
  };

  const { t, s } = getPageTitle();

  if (!user) return <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center' }}>Loading Workspace...</div>;

  return (
    <div className="app-container" id="app-layout">
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="logo">
            <span className="logo-icon"><i className="fa-solid fa-wand-magic-sparkles"></i></span>
            <span className="logo-text">BrandMuse<span>AI</span></span>
          </div>
        </div>
        
        <div className="user-profile-badge">
          <div className="avatar"><i className="fa-solid fa-circle-user"></i></div>
          <div className="profile-details">
            <div className="username">{user.first_name} {user.last_name}</div>
            <div className="user-tier"><span className="badge badge-accent">Enterprise</span></div>
          </div>
        </div>

        <nav className="sidebar-menu">
          <button className={`nav-item ${location.pathname === '/dashboard' ? 'active' : ''}`} onClick={() => navigate('/dashboard')}>
            <i className="fa-solid fa-chart-line"></i>
            <span>Dashboard</span>
          </button>
          <button className={`nav-item ${location.pathname.includes('generator') ? 'active' : ''}`} onClick={() => navigate('/dashboard/generator')}>
            <i className="fa-solid fa-pen-nib"></i>
            <span>Content Generator</span>
          </button>
          <button className={`nav-item ${location.pathname.includes('documents') ? 'active' : ''}`} onClick={() => navigate('/dashboard/documents')}>
            <i className="fa-solid fa-file-arrow-up"></i>
            <span>Brand Documents</span>
          </button>
          <button className={`nav-item ${location.pathname.includes('memory') ? 'active' : ''}`} onClick={() => navigate('/dashboard/memory')}>
            <i className="fa-solid fa-brain"></i>
            <span>Learning Memory</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <button className="btn-logout" onClick={handleLogout}>
            <i className="fa-solid fa-power-off"></i>
            <span>Sign Out</span>
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        {/* Header */}
        <header className="top-header">
          <div className="page-title-area">
            <h1>{t}</h1>
            <p className="subtitle">{s}</p>
          </div>
          <div className="header-actions">
            <div className="business-id-selector">
              <span className="label">Active Business ID:</span>
              <div className="business-id-field">
                <span className="value">{user.business_id}</span>
                <button className="btn-copy" onClick={copyBusinessId} title="Copy Business ID">
                  <i className="fa-regular fa-copy"></i>
                </button>
              </div>
            </div>
            <div className="status-indicator">
              <span className="pulse"></span>
              <span className="status-text">Connected</span>
            </div>
          </div>
        </header>

        <Outlet context={{ user }} />
      </main>
    </div>
  );
};

export default DashboardLayout;
