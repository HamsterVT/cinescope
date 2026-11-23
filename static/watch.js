(() => {
    const player = document.querySelector('[data-movie-player]');
    if (!player) return;

    const video = player.querySelector('video');
    const overlay = player.querySelector('[data-player-overlay]');
    const error = document.querySelector('[data-film-error]');
    const errorMessage = error.querySelector('[data-film-error-message]');
    const defaultError = errorMessage.textContent;
    const status = document.querySelector('[data-save-status]');
    const seekControls = document.querySelector('[data-seek-controls]');
    const csrf = document.querySelector('#csrf-form input')?.value;
    const saved = Number(player.dataset.resumeSeconds) || 0;
    let lastAttempt = 0;
    let lastPosition;
    let lastEvent = 0;
    let resumeAt = 0;
    let watched = false;

    overlay.hidden = false;

    function savePosition() {
        if (!csrf || !watched || video.seeking || !Number.isFinite(video.duration)) return;
        const duration = Math.ceil(video.duration);
        const position = video.ended ? duration : Math.floor(video.currentTime);
        if (position < 2 || duration < 1 || position === lastPosition) return;

        lastAttempt = Date.now();
        lastPosition = position;
        const eventId = Math.max(lastAttempt, lastEvent + 1);
        lastEvent = eventId;
        fetch(player.dataset.progressUrl, {
            method: 'POST',
            keepalive: true,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
            body: JSON.stringify({position, duration, observed_at: eventId}),
        }).then(response => {
            if (!response.ok) throw new Error();
            if (eventId === lastEvent) status.textContent = 'Позиция сохранена в «Моих просмотрах».';
        }).catch(() => {
            if (eventId !== lastEvent) return;
            lastPosition = undefined;
            status.textContent = 'Позиция пока не сохранилась. Продолжите просмотр для повторной попытки.';
        });
    }

    function restore() {
        if (resumeAt > 0 && Number.isFinite(video.duration)) {
            if (resumeAt < video.duration - 8) video.currentTime = resumeAt;
            resumeAt = 0;
        }
    }

    function showPlaybackError() {
        const code = video.error?.code;
        if (code === 1) return;
        errorMessage.textContent = {
            2: 'Соединение с видеохостом прервалось. Обновите страницу или откройте видео отдельно.',
            3: 'Браузер не смог воспроизвести этот файл. Попробуйте открыть видео отдельно.',
            4: 'Браузеру не удалось открыть файл с видеохоста. Обновите страницу или откройте видео отдельно.',
        }[code] || defaultError;
        overlay.hidden = true;
        error.hidden = false;
    }

    function play(position) {
        resumeAt = position;
        error.hidden = true;
        overlay.hidden = true;
        if (video.readyState >= 1) {
            if (!position) video.currentTime = 0;
            restore();
        }
        video.focus({preventScroll: true});
        video.play().catch(reason => {
            if (reason.name === 'NotAllowedError') overlay.hidden = false;
            else if (reason.name !== 'AbortError') showPlaybackError();
        });
    }

    player.querySelector('[data-play-film]').addEventListener('click', () => play(0));
    player.querySelector('[data-resume-film]')?.addEventListener('click', () => play(saved));
    video.addEventListener('loadedmetadata', restore);
    video.addEventListener('playing', () => { watched = true; seekControls.hidden = false; });
    video.addEventListener('error', showPlaybackError);
    video.querySelector('source').addEventListener('error', showPlaybackError);
    video.addEventListener('timeupdate', () => { if (Date.now() - lastAttempt >= 12000) savePosition(); });
    video.addEventListener('pause', savePosition);
    video.addEventListener('ended', savePosition);
    video.addEventListener('seeked', savePosition);
    window.addEventListener('pagehide', savePosition);
    document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(); });

    seekControls.addEventListener('click', event => {
        const button = event.target.closest('[data-seek]');
        if (!button || !Number.isFinite(video.duration)) return;
        video.currentTime = Math.min(video.duration, Math.max(0, video.currentTime + Number(button.dataset.seek)));
    });

    if (player.hasAttribute('data-auto-resume')) play(saved);
})();
