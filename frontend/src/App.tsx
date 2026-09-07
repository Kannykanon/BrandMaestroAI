import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Auth from './pages/Auth';
import DashboardLayout from './pages/DashboardLayout';
import OverviewPanel from './components/OverviewPanel';
import GeneratorPanel from './components/GeneratorPanel';
import DocumentsPanel from './components/DocumentsPanel';
import MemoryPanel from './components/MemoryPanel';

function App() {
  // Simple auth check for now. In a real app, use Context or Redux.
  const isAuthenticated = !!localStorage.getItem('bg_access_token');

  return (
    <BrowserRouter>
      <Routes>
        {/* Landing Page Route - For now we redirect to Auth or Dashboard */}
        <Route path="/" element={isAuthenticated ? <Navigate to="/dashboard" /> : <Navigate to="/auth" />} />
        
        {/* Auth Route */}
        <Route path="/auth" element={!isAuthenticated ? <Auth /> : <Navigate to="/dashboard" />} />

        {/* Dashboard Layout and Nested Routes */}
        <Route path="/dashboard" element={isAuthenticated ? <DashboardLayout /> : <Navigate to="/auth" />}>
          <Route index element={<OverviewPanel />} />
          <Route path="generator" element={<GeneratorPanel />} />
          <Route path="documents" element={<DocumentsPanel />} />
          <Route path="memory" element={<MemoryPanel />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
