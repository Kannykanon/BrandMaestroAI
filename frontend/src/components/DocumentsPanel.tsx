import React, { useState, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import api from '../services/api';

const DocumentsPanel = () => {
  const { user } = useOutletContext<any>();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [contentType, setContentType] = useState('press_release');
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  
  // Hardcoded list for now since app.js used mock state, but typically this would fetch from an API
  const [uploadedDocs, setUploadedDocs] = useState<{ filename: string; contentType: string; date: string; status: string }[]>([]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.currentTarget.classList.add('hover');
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.currentTarget.classList.remove('hover');
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.currentTarget.classList.remove('hover');
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) return alert("Please select a file.");

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('business_id', user.business_id);
    formData.append('content_type', contentType);

    setIsUploading(true);
    try {
      await api.post('/documents/top-performing', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      alert('Document vectorized successfully!');
      
      setUploadedDocs(prev => [{
        filename: selectedFile.name,
        contentType,
        date: new Date().toLocaleDateString(),
        status: 'Processing (Vectorized)'
      }, ...prev]);
      
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <section className="dashboard-panel active">
      <div className="generator-split">
        <div className="content-card">
          <h2>Upload Top-Performing Copy</h2>
          <p className="card-subtitle">Feed your brand\'s highest converting marketing assets into the memory bank. We accept PDF, DOCX, and TXT files.</p>
          
          <form onSubmit={handleUpload}>
            <div className="input-group">
              <label>Target Content Profile to Train</label>
              <select value={contentType} onChange={e => setContentType(e.target.value)}>
                <option value="press_release">Press Releases</option>
                <option value="social">Social / Premiere Campaigns</option>
                <option value="trailer_copy">Trailer & Campaign Copy</option>
                <option value="talent_bio">Talent Bios</option>
                <option value="synopsis">Show / Title Synopses</option>
                <option value="blog">Behind-the-Scenes Posts</option>
                <option value="proposal">Pitch / Greenlight Decks</option>
              </select>
            </div>

            <div 
              className="upload-dropzone" 
              onDragOver={handleDragOver} 
              onDragLeave={handleDragLeave} 
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <i className="fa-solid fa-cloud-arrow-up"></i>
              <p>Drag and drop a document here</p>
              <span>or click to browse local files</span>
              <input 
                type="file" 
                className="hidden" 
                ref={fileInputRef} 
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)} 
              />
            </div>

            {selectedFile && (
              <div className="selected-file-badge">
                <i className="fa-solid fa-file-pdf"></i>
                <span>{selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)</span>
                <i className="fa-solid fa-xmark remove" onClick={(e) => { e.stopPropagation(); setSelectedFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; }}></i>
              </div>
            )}

            <button type="submit" className={`btn btn-primary btn-block ${isUploading ? 'loading' : ''}`} disabled={isUploading || !selectedFile} style={{ marginTop: '24px' }}>
              <i className="fa-solid fa-server"></i>
              <span>{isUploading ? 'Processing...' : 'Upload and Process Reference Document'}</span>
            </button>
          </form>
        </div>

        <div className="content-card">
          <div className="card-header">
            <h2><i className="fa-regular fa-folder-open"></i> Indexed Documents</h2>
          </div>
          <table className="custom-table">
            <thead>
              <tr>
                <th>Document Name</th>
                <th>Trained Profile</th>
                <th>Ingestion Date</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {uploadedDocs.length === 0 ? (
                <tr>
                  <td colSpan={4} className="table-empty">
                    <i className="fa-regular fa-folder"></i>
                    <p>No guideline documents vectorized yet. Use the upload card to sync brand guidelines.</p>
                  </td>
                </tr>
              ) : (
                uploadedDocs.map((doc, i) => (
                  <tr key={i}>
                    <td><strong>{doc.filename}</strong></td>
                    <td><span className="badge badge-accent">{doc.contentType.toUpperCase()}</span></td>
                    <td>{doc.date}</td>
                    <td><span className="score-badge">{doc.status}</span></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};

export default DocumentsPanel;
