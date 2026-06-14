// ── Admin: Manual clip add ────────────────────────────────────────────────────
async function addManualClip(event) {
  event.preventDefault();
  const form = event.target;
  const btn  = form.querySelector('button[type="submit"]');

  const topics = Array.from(form.querySelectorAll('input[name="topic"]:checked'))
    .map(e => e.value);

  const payload = {
    url:        form.url.value.trim(),
    title:      form.title.value.trim(),
    channel:    form.channel.value.trim(),
    start_sec:  parseFloat(form.start_sec.value) || 0,
    end_sec:    parseFloat(form.end_sec.value)   || 90,
    difficulty: form.difficulty.value,
    topics
  };

  btn.disabled = true;
  btn.textContent = '追加中...';

  try {
    const resp = await fetch('/api/clips', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload)
    });
    const result = await resp.json();

    if (result.error) {
      alert('エラー: ' + result.error);
    } else {
      const tLines = result.transcript_lines;
      alert(
        `追加しました！\nTranscript: ${tLines} 行${tLines === 0 ? '\n(字幕が見つかりませんでした)' : ''}\nPending タブで確認・承認してください。`
      );
      form.reset();
      location.reload();
    }
  } catch (e) {
    alert('失敗: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '追加する (Pending へ)';
  }
}

// ── Admin: AI search ──────────────────────────────────────────────────────────
async function runAISearch(event) {
  event.preventDefault();
  const query      = document.getElementById('search-query').value.trim();
  const resultsDiv = document.getElementById('search-results');
  const btn        = event.target.querySelector('button[type="submit"]');

  btn.disabled    = true;
  btn.textContent = 'Searching… (30秒ほどかかります)';
  resultsDiv.innerHTML = '<div class="loading">🔍 YouTube を検索中・AI で評価中...</div>';

  try {
    const resp = await fetch('/api/search', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query, max_results: 5 })
    });
    const data = await resp.json();

    if (data.error) {
      resultsDiv.innerHTML = `<div class="error">${escHtml(data.error)}</div>`;
      return;
    }
    renderSearchResults(data.results || []);
  } catch (e) {
    resultsDiv.innerHTML = `<div class="error">${escHtml(e.message)}</div>`;
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Search & Evaluate →';
  }
}

function renderSearchResults(results) {
  const div = document.getElementById('search-results');
  if (!results.length) {
    div.innerHTML = '<p class="loading">候補が見つかりませんでした。別のキーワードを試してください。</p>';
    return;
  }

  div.innerHTML = results
    .map((r, i) => {
      const diff    = r.difficulty || 'C1';
      const topics  = (r.topics || []).map(t => `<span class="topic-tag">${escHtml(t)}</span>`).join('');
      const start   = fmtTime(r.start_sec  || 0);
      const end     = fmtTime(r.end_sec    || 90);
      const suitable = r.ai_suitable !== false;
      const dataAttr = escAttr(JSON.stringify(r));

      return `
      <div class="search-result-card ${suitable ? '' : 'unsuitable'}">
        <img src="${escAttr(r.thumbnail)}" class="result-thumb" alt="">
        <div class="result-info">
          <div class="result-meta">
            <span class="badge badge-${diff.toLowerCase()}">${escHtml(diff)}</span>
            ${topics}
          </div>
          <h4>${escHtml(r.title)}</h4>
          <p class="channel">${escHtml(r.channel)}</p>
          ${r.ai_note ? `<p class="ai-note">${escHtml(r.ai_note)}</p>` : ''}
          <p class="clip-time">Suggested clip: ${start} → ${end}</p>
        </div>
        <div class="result-actions">
          ${suitable
            ? `<button onclick='addSearchResult(${i})' class="btn btn-success btn-sm">+ Add</button>`
            : `<span class="unsuitable-label">非推奨</span>
               <button onclick='addSearchResult(${i})' class="btn btn-ghost btn-sm">Add anyway</button>`
          }
        </div>
      </div>`;
    })
    .join('');

  // Store results globally for the onclick callbacks
  window._searchResults = results;
}

async function addSearchResult(index) {
  const result = window._searchResults?.[index];
  if (!result) return;

  try {
    const resp = await fetch('/api/save-search-result', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(result)
    });
    const data = await resp.json();
    if (data.error) {
      alert(data.error);
    } else {
      alert('Pending に追加しました！ページをリロードして確認してください。');
    }
  } catch (e) {
    alert('失敗: ' + e.message);
  }
}

// ── Admin: Clip status actions ────────────────────────────────────────────────
async function approveClip(id) {
  const resp = await fetch(`/api/clips/${id}/approve`, { method: 'POST' });
  if (resp.ok) {
    document.getElementById(`clip-${id}`)?.remove();
    updateTabCount('pending', -1);
    updateTabCount('approved', 1);
  }
}

async function rejectClip(id) {
  const resp = await fetch(`/api/clips/${id}/reject`, { method: 'POST' });
  if (resp.ok) {
    document.getElementById(`clip-${id}`)?.remove();
  }
}

async function deleteClip(id) {
  if (!confirm('このクリップを完全に削除しますか？')) return;
  const resp = await fetch(`/api/clips/${id}`, { method: 'DELETE' });
  if (resp.ok) {
    document.getElementById(`clip-${id}`)?.remove();
  }
}

function updateTabCount(tab, delta) {
  const btn = document.querySelector(`[data-tab="${tab}"] .tab-count`);
  if (!btn) return;
  const n = parseInt(btn.textContent || '0', 10) + delta;
  btn.textContent = Math.max(0, n);
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab)
  );
  document.querySelectorAll('.tab-panel').forEach(p =>
    p.classList.toggle('active', p.id === `tab-${tab}`)
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtTime(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(s) {
  return String(s).replace(/'/g, '&#39;').replace(/"/g, '&quot;');
}
