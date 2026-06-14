// ── State ─────────────────────────────────────────────────────────────────────
let player      = null;
let clipStart   = CLIP_START;
let clipEnd     = CLIP_END;
let loopEnabled = true;
let ccVisible   = true;
let speed       = 1.0;
let repCount    = 0;
let pollId      = null;
let saveDebouce = null;

// ── YouTube iframe API ────────────────────────────────────────────────────────
window.onYouTubeIframeAPIReady = function () {
  player = new YT.Player('yt-player', {
    videoId: VIDEO_ID,
    playerVars: {
      start:           Math.floor(clipStart),
      end:             Math.ceil(clipEnd),
      cc_load_policy:  0,
      controls:        1,
      rel:             0,
      modestbranding:  1,
      iv_load_policy:  3,
      origin:          window.location.origin
    },
    events: {
      onReady:       onPlayerReady,
      onStateChange: onPlayerStateChange
    }
  });
};

function onPlayerReady(e) {
  e.target.setPlaybackRate(speed);
  startPolling();
  updateSaveBtn();
}

function onPlayerStateChange(e) {
  // YT fires ENDED when it hits the 'end' playerVar; we also catch it in poll
  if (e.data === YT.PlayerState.ENDED && loopEnabled) {
    doLoop();
  }
}

function startPolling() {
  if (pollId) clearInterval(pollId);
  pollId = setInterval(tick, 200);
}

function tick() {
  if (!player || typeof player.getPlayerState !== 'function') return;
  try {
    if (player.getPlayerState() !== YT.PlayerState.PLAYING) return;

    const t = player.getCurrentTime();

    // Loop check: 300 ms lookahead to prevent micro-overshoot
    if (loopEnabled && t >= clipEnd - 0.3) {
      doLoop();
      return;
    }

    // Subtitle update
    if (ccVisible) updateSubtitle(t);

  } catch (_) {}
}

function doLoop() {
  repCount++;
  document.getElementById('rep-count').textContent = repCount;
  updateSaveBtn();
  player.seekTo(clipStart, true);
  player.playVideo();
}

// ── Subtitles ─────────────────────────────────────────────────────────────────
function updateSubtitle(t) {
  const el = document.getElementById('subtitle-text');
  if (!el) return;

  const line = TRANSCRIPT.find(
    l => t >= l.start && t < l.start + l.duration + 0.5
  );
  el.textContent = line ? cleanText(line.text) : '';
}

function cleanText(s) {
  // Remove HTML entities that youtube-transcript-api sometimes leaves
  const tmp = document.createElement('div');
  tmp.innerHTML = s;
  return tmp.textContent || s;
}

// ── Controls ──────────────────────────────────────────────────────────────────
function seek(delta) {
  if (!player) return;
  const t = Math.max(clipStart, Math.min(clipEnd - 0.5, player.getCurrentTime() + delta));
  player.seekTo(t, true);
}

function setSpeed(rate) {
  speed = rate;
  if (player) player.setPlaybackRate(rate);
  document.querySelectorAll('.speed-btn').forEach(b => {
    b.classList.toggle('active', parseFloat(b.dataset.speed) === rate);
  });
}

function toggleLoop() {
  loopEnabled = !loopEnabled;
  const btn = document.getElementById('loop-btn');
  btn.classList.toggle('active', loopEnabled);
  btn.textContent = loopEnabled ? '↺ Loop' : '↺ Off';
}

function toggleCC() {
  ccVisible = !ccVisible;
  const overlay = document.getElementById('subtitle-overlay');
  if (overlay) overlay.style.display = ccVisible ? 'flex' : 'none';
  const btn = document.getElementById('cc-btn');
  btn.classList.toggle('active', ccVisible);
  if (!ccVisible) document.getElementById('subtitle-text').textContent = '';
}

// ── Clip time editing ─────────────────────────────────────────────────────────
function setClipStart() {
  if (!player) return;
  clipStart = Math.max(0, player.getCurrentTime());
  document.getElementById('start-display').textContent = fmtTime(clipStart);
  persistClipTimes();
}

function setClipEnd() {
  if (!player) return;
  clipEnd = player.getCurrentTime();
  document.getElementById('end-display').textContent = fmtTime(clipEnd);
  persistClipTimes();
}

function persistClipTimes() {
  clearTimeout(saveDebouce);
  saveDebouce = setTimeout(() => {
    fetch(`/api/clips/${CLIP_ID}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_sec: clipStart, end_sec: clipEnd })
    }).catch(() => {});
  }, 1000);
}

// ── Progress ──────────────────────────────────────────────────────────────────
function saveProgress() {
  if (repCount === 0) {
    alert('まず練習してからSaveしてください');
    return;
  }
  const btn = document.getElementById('save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  fetch(`/api/practice/${CLIP_ID}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repetitions: repCount })
  })
    .then(r => r.json())
    .then(() => {
      const newTotal = TOTAL_REPS + repCount;
      document.getElementById('total-reps-display').textContent = newTotal;
      btn.textContent = '✓ Saved!';
      btn.classList.add('saved');
      repCount = 0;
      document.getElementById('rep-count').textContent = 0;
      setTimeout(() => {
        btn.textContent = 'Save Progress (0×)';
        btn.classList.remove('saved');
        btn.disabled = false;
      }, 2000);
    })
    .catch(() => {
      btn.textContent = 'Error — retry';
      btn.disabled = false;
    });
}

function updateSaveBtn() {
  const btn = document.getElementById('save-btn');
  if (btn && !btn.classList.contains('saved')) {
    btn.textContent = `Save Progress (${repCount}×)`;
  }
}

// ── Transcript click-to-seek ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tr-line').forEach(el => {
    el.addEventListener('click', () => {
      const t = parseFloat(el.dataset.t);
      if (!isNaN(t) && player) {
        player.seekTo(t, true);
        player.playVideo();
      }
    });
  });
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
}
