import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import App from './App.tsx';
import {AuthProvider} from './lib/AuthContext';
import './index.css';

// 서비스 워커: 앱 화면 파일만 저장해 오프라인에서도 앱이 열리게 한다(센서·명령은 항상 서버로).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* 설치 기능만 없을 뿐 앱은 그대로 동작 */ });
  });
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
