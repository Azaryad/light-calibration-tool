document.querySelectorAll("[data-trim-block]").forEach((block) => {
  const input = block.querySelector("[data-trim-input]");
  const previewImage = block.querySelector("[data-preview-frame]");
  const previewButton = block.querySelector("[data-preview-time]");
  const loadPlayerButton = block.querySelector("[data-load-player]");
  const useCurrentButton = block.querySelector("[data-use-current-time]");
  const playerSlot = block.querySelector("[data-player-slot]");

  let video = null;

  const currentValue = () => {
    const value = Number.parseFloat(input.value);
    return Number.isFinite(value) && value >= 0 ? value : 0;
  };

  const updatePreview = () => {
    const url = new URL(previewButton.dataset.frameUrl, window.location.origin);
    url.searchParams.set("t", currentValue().toFixed(3));
    previewImage.src = url.toString();
  };

  previewButton.addEventListener("click", updatePreview);

  loadPlayerButton.addEventListener("click", () => {
    if (!video) {
      video = document.createElement("video");
      video.className = "trim-video";
      video.controls = true;
      video.preload = "none";
      playerSlot.replaceChildren(video);
      useCurrentButton.disabled = false;
    }
    video.src = `${loadPlayerButton.dataset.videoUrl}#t=${currentValue().toFixed(3)}`;
    video.load();
  });

  useCurrentButton.addEventListener("click", () => {
    if (!video) return;
    input.value = video.currentTime.toFixed(3);
    updatePreview();
  });

  input.addEventListener("change", () => {
    if (video) {
      video.currentTime = currentValue();
    }
  });
});

const applyAllButton = document.querySelector("[data-apply-all-trim]");
if (applyAllButton) {
  applyAllButton.addEventListener("click", () => {
    const globalInput = document.querySelector("[data-global-trim]");
    const value = Number.parseFloat(globalInput.value);
    if (!Number.isFinite(value) || value < 0) return;
    document.querySelectorAll("[data-trim-input]").forEach((input) => {
      input.value = value.toFixed(3);
    });
  });
}

