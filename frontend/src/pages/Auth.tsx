import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';

const Auth = () => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  // Login Form State
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');

  // Register Form State
  const [regFirstName, setRegFirstName] = useState('');
  const [regLastName, setRegLastName] = useState('');
  const [regUsername, setRegUsername] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMsg('');
    try {
      const params = new URLSearchParams();
      params.append('username', loginUsername);
      params.append('password', loginPassword);

      const response = await api.post('/users/login', params, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
      });
      
      localStorage.setItem('bg_access_token', response.data.access_token);
      // Ensure page reload so App.tsx can read token again, or use Context. 
      // For this simplified version, window.location.href handles the re-evaluation of Auth.
      window.location.href = '/dashboard';
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || 'Failed to authenticate');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMsg('');
    try {
      const response = await api.post('/users/create', {
        first_name: regFirstName,
        last_name: regLastName,
        username: regUsername,
        email: regEmail,
        password: regPassword
      });
      localStorage.setItem('bg_access_token', response.data.access_token);
      window.location.href = '/dashboard';
      
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || 'Registration failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="auth-wrapper" id="auth-gate">
      <div className="auth-panel-left">
        <div className="auth-panel-brand">
          <span className="logo-icon"><i className="fa-solid fa-wand-magic-sparkles"></i></span>
          <span className="logo-text">BrandMuse<span>AI</span></span>
        </div>
        <div className="auth-panel-hero">
          <h2 className="auth-panel-headline">Your brand voice.<br />Every piece of content.</h2>
          <p className="auth-panel-sub">Upload your best content once. BrandMuse AI learns your unique voice and generates perfectly aligned ads, blogs, proposals, and emails with a single prompt.</p>
          <ul className="auth-feature-list">
            <li><i className="fa-solid fa-check"></i> One-time brand voice setup</li>
            <li><i className="fa-solid fa-check"></i> Real-time streaming generation</li>
            <li><i className="fa-solid fa-check"></i> Self-improving from feedback</li>
            <li><i className="fa-solid fa-check"></i> Ads &middot; Blogs &middot; Proposals &middot; Emails</li>
          </ul>
        </div>
      </div>

      <div className="auth-panel-right">
        <div className="auth-card">
          <div className="auth-header">
            <div className="logo">
              <span className="logo-icon"><i className="fa-solid fa-wand-magic-sparkles"></i></span>
              <span className="logo-text">BrandMuse<span>AI</span></span>
            </div>
            <p className="auth-subtitle">Sign in to your workspace</p>
          </div>

          {errorMsg && <div style={{ color: 'red', marginBottom: '1rem', textAlign: 'center' }}>{errorMsg}</div>}

          <div className="auth-tabs">
            <button className={`auth-tab ${activeTab === 'login' ? 'active' : ''}`} onClick={() => setActiveTab('login')}>Sign In</button>
            <button className={`auth-tab ${activeTab === 'register' ? 'active' : ''}`} onClick={() => setActiveTab('register')}>Register</button>
          </div>

          {activeTab === 'login' ? (
            <form className="auth-form active" onSubmit={handleLogin}>
              <div className="input-group">
                <label><i className="fa-regular fa-user"></i> Username</label>
                <input type="text" required value={loginUsername} onChange={e => setLoginUsername(e.target.value)} />
              </div>
              <div className="input-group">
                <label><i className="fa-solid fa-lock"></i> Password</label>
                <input type="password" required value={loginPassword} onChange={e => setLoginPassword(e.target.value)} />
              </div>
              <button type="submit" className={`btn btn-primary btn-block ${isLoading ? 'loading' : ''}`} disabled={isLoading}>
                <span>{isLoading ? 'Signing in...' : 'Sign In to Dashboard'}</span>
                <i className="fa-solid fa-arrow-right-to-bracket"></i>
              </button>
            </form>
          ) : (
            <form className="auth-form active" onSubmit={handleRegister}>
              <div className="form-row">
                <div className="input-group">
                  <label>First Name</label>
                  <input type="text" required value={regFirstName} onChange={e => setRegFirstName(e.target.value)} />
                </div>
                <div className="input-group">
                  <label>Last Name</label>
                  <input type="text" required value={regLastName} onChange={e => setRegLastName(e.target.value)} />
                </div>
              </div>
              <div className="input-group">
                <label><i className="fa-regular fa-user"></i> Username</label>
                <input type="text" required value={regUsername} onChange={e => setRegUsername(e.target.value)} />
              </div>
              <div className="input-group">
                <label><i className="fa-regular fa-envelope"></i> Email Address</label>
                <input type="email" required value={regEmail} onChange={e => setRegEmail(e.target.value)} />
              </div>
              <div className="input-group">
                <label><i className="fa-solid fa-lock"></i> Password</label>
                <input type="password" required value={regPassword} onChange={e => setRegPassword(e.target.value)} />
              </div>
              <button type="submit" className={`btn btn-primary btn-block ${isLoading ? 'loading' : ''}`} disabled={isLoading}>
                <span>{isLoading ? 'Creating...' : 'Create Account'}</span>
                <i className="fa-solid fa-user-plus"></i>
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};

export default Auth;
