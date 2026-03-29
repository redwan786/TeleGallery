import { X, Upload, FileImage, FileVideo, CheckCircle2, AlertCircle } from 'lucide-react';
import { useState, useRef, useCallback } from 'react';
import { useConfig } from '@/context/ConfigContext';
import { getApi, getApiHeaders } from '@/lib/api';

const MAX_BOT_API_SIZE = 50 * 1024 * 1024; // 50 MB

interface UploadFile {
  file: File;
  progress: number;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

export default function UploadModal({ onClose, onUploaded }: { onClose: () => void; onUploaded: () => void }) {
  const { credentials } = useConfig();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const items = Array.from(newFiles).map(file => ({
      file,
      progress: 0,
      status: 'pending' as const,
    }));
    setFiles(prev => [...prev, ...items]);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  }, [addFiles]);

  const uploadSmallFile = async (file: File, index: number) => {
    const api = getApi(credentials);
    const form = new FormData();
    form.append('file', file);
    await api.post('/api/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        const pct = Math.round((e.loaded * 100) / (e.total || 1));
        setFiles(prev => prev.map((f, j) => j === index ? { ...f, progress: pct } : f));
      },
    });
  };

  const uploadLargeFile = (file: File, index: number): Promise<void> => {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('file', file);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${credentials.backendUrl}/api/upload/large`);

      // Set auth headers
      const headers = getApiHeaders(credentials);
      Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));

      // Track upload progress (sending to server)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          // Upload to server is first 50%, Telegram upload is next 50%
          const pct = Math.round((e.loaded * 50) / e.total);
          setFiles(prev => prev.map((f, j) => j === index ? { ...f, progress: pct } : f));
        }
      };

      // Process SSE response for Telegram upload progress
      let buffer = '';
      xhr.onreadystatechange = () => {
        if (xhr.readyState >= 3 && xhr.responseText) {
          const newData = xhr.responseText.substring(buffer.length);
          buffer = xhr.responseText;
          
          const lines = newData.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.substring(6));
                if (event.type === 'progress') {
                  // Map Telegram progress (0-100) to our 50-100 range
                  const pct = 50 + Math.round(event.percent / 2);
                  setFiles(prev => prev.map((f, j) => j === index ? { ...f, progress: pct } : f));
                } else if (event.type === 'done') {
                  setFiles(prev => prev.map((f, j) => j === index ? { ...f, status: 'done', progress: 100 } : f));
                  resolve();
                } else if (event.type === 'error') {
                  reject(new Error(event.error));
                }
              } catch {}
            }
          }
        }
      };

      xhr.onerror = () => reject(new Error('Network error'));
      xhr.ontimeout = () => reject(new Error('Upload timeout'));
      xhr.timeout = 600000; // 10 min timeout for large files

      xhr.send(form);
    });
  };

  const startUpload = async () => {
    setUploading(true);
    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== 'pending') continue;
      setFiles(prev => prev.map((f, j) => j === i ? { ...f, status: 'uploading' } : f));
      try {
        const file = files[i].file;
        if (file.size > MAX_BOT_API_SIZE && credentials.apiId && credentials.apiHash) {
          await uploadLargeFile(file, i);
        } else {
          await uploadSmallFile(file, i);
          setFiles(prev => prev.map((f, j) => j === i ? { ...f, status: 'done', progress: 100 } : f));
        }
      } catch (err: any) {
        setFiles(prev => prev.map((f, j) => j === i ? { ...f, status: 'error', error: err.message } : f));
      }
    }
    setUploading(false);
    onUploaded();
  };

  const done = files.filter(f => f.status === 'done').length;
  const total = files.length;
  const hasLargeFiles = files.some(f => f.file.size > MAX_BOT_API_SIZE);
  const missingPyrogram = hasLargeFiles && (!credentials.apiId || !credentials.apiHash);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm">
      <div className="bg-card border border-tg-divider rounded-2xl w-full max-w-lg mx-4 shadow-xl animate-fade-in">
        <div className="flex items-center justify-between p-4 border-b border-tg-divider">
          <h2 className="font-medium text-foreground">Upload photos & videos</h2>
          <button onClick={onClose} className="p-1 rounded-full hover:bg-tg-hover-bg"><X className="w-5 h-5 text-muted-foreground" /></button>
        </div>

        <div
          onDragOver={e => e.preventDefault()}
          onDrop={handleDrop}
          className="p-8 flex flex-col items-center gap-3 border-b border-tg-divider"
        >
          <Upload className="w-10 h-10 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Drag & drop files here, or</p>
          <button onClick={() => inputRef.current?.click()} className="tg-btn-primary text-sm">
            Browse files
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept="image/*,video/*"
            className="hidden"
            onChange={e => e.target.files && addFiles(e.target.files)}
          />
        </div>

        {missingPyrogram && (
          <div className="mx-4 mt-3 p-3 rounded-lg bg-destructive/10 border border-destructive/20">
            <p className="text-xs text-destructive">
              ⚠️ 50MB+ ফাইল আপলোড করতে API ID ও API Hash দরকার। Settings থেকে যোগ করুন।
            </p>
          </div>
        )}

        {files.length > 0 && (
          <div className="max-h-60 overflow-auto p-4 space-y-2">
            {total > 1 && <p className="text-xs text-muted-foreground mb-2">{done}/{total} uploaded</p>}
            {files.map((f, i) => (
              <div key={i} className="flex items-center gap-3 text-sm">
                {f.file.type.startsWith('video') ? <FileVideo className="w-4 h-4 text-muted-foreground shrink-0" /> : <FileImage className="w-4 h-4 text-muted-foreground shrink-0" />}
                <span className="truncate flex-1 text-foreground">
                  {f.file.name}
                  {f.file.size > MAX_BOT_API_SIZE && (
                    <span className="ml-1 text-xs text-primary">(Pyrogram)</span>
                  )}
                </span>
                {f.status === 'uploading' && (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-8 text-right">{f.progress}%</span>
                    <div className="w-20 h-1.5 bg-secondary rounded-full overflow-hidden">
                      <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${f.progress}%` }} />
                    </div>
                  </div>
                )}
                {f.status === 'done' && <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />}
                {f.status === 'error' && <AlertCircle className="w-4 h-4 text-destructive shrink-0" />}
              </div>
            ))}
          </div>
        )}

        <div className="p-4 flex justify-end gap-2 border-t border-tg-divider">
          <button onClick={onClose} className="tg-btn-outline text-sm">Cancel</button>
          <button
            onClick={startUpload}
            disabled={files.length === 0 || uploading || missingPyrogram}
            className="tg-btn-primary text-sm disabled:opacity-50"
          >
            {uploading ? `Uploading ${done}/${total}...` : `Upload ${total} file${total !== 1 ? 's' : ''}`}
          </button>
        </div>
      </div>
    </div>
  );
}