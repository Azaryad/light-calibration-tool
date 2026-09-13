function formatSeconds(value) {
  if (!Number.isFinite(value)) return "-";
  return `${value.toFixed(3)}s`;
}

function updateCardDuration(card) {
  const startInput = card.querySelector('[data-adjust-value][data-adjust-kind="start"]');
  const finishInput = card.querySelector('[data-adjust-value][data-adjust-kind="finish"]');
  const output = card.querySelector("[data-duration-output]");
  if (!startInput || !finishInput || !output) return;

  const start = Number.parseFloat(startInput.value);
  const finish = Number.parseFloat(finishInput.value);
  output.textContent = formatSeconds(finish - start);
}

document.querySelectorAll("[data-manual-window]").forEach((windowEl) => {
  const card = windowEl.closest(".manual-card");
  const video = windowEl.querySelector("video");
  const output = windowEl.querySelector("[data-time-output]");
  const kind = windowEl.dataset.kind;
  const input = card.querySelector(`[data-adjust-value][data-adjust-kind="${kind}"]`);
  const windowStart = Number.parseFloat(windowEl.dataset.windowStart) || 0;
  const fps = Number.parseFloat(windowEl.dataset.fps) || 30;

  windowEl.querySelectorAll("[data-frame-step]").forEach((button) => {
    button.addEventListener("click", () => {
      video.pause();
      const playButton = windowEl.querySelector("[data-toggle-play]");
      if (playButton) playButton.textContent = "Play";
      const direction = Number.parseFloat(button.dataset.frameStep) || 0;
      const next = video.currentTime + direction / fps;
      video.currentTime = Math.max(0, Math.min(6, next));
    });
  });

  const playButton = windowEl.querySelector("[data-toggle-play]");
  if (playButton) {
    playButton.addEventListener("click", () => {
      if (video.paused) {
        video.play();
        playButton.textContent = "Pause";
      } else {
        video.pause();
        playButton.textContent = "Play";
      }
    });

    video.addEventListener("pause", () => {
      playButton.textContent = "Play";
    });

    video.addEventListener("ended", () => {
      playButton.textContent = "Play";
    });
  }

  video.addEventListener("click", () => {
    if (playButton) playButton.click();
  });

  const setButton = windowEl.querySelector("[data-set-frame]");
  setButton.addEventListener("click", () => {
    const originalTime = windowStart + video.currentTime;
    input.value = originalTime.toFixed(6);
    output.textContent = formatSeconds(originalTime);
    updateCardDuration(card);
  });
});

document.querySelectorAll(".manual-card").forEach(updateCardDuration);
