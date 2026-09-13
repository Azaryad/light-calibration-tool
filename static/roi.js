class RoiSelector {
  constructor(block) {
    this.block = block;
    this.image = block.querySelector("[data-roi-image]");
    this.canvas = block.querySelector("[data-roi-canvas]");
    this.ctx = this.canvas.getContext("2d");
    this.inputs = {
      x: block.querySelector("[data-roi-x]"),
      y: block.querySelector("[data-roi-y]"),
      w: block.querySelector("[data-roi-w]"),
      h: block.querySelector("[data-roi-h]")
    };
    this.dragging = false;
    this.mode = "draw";
    this.handle = null;
    this.start = null;
    this.startBox = null;
    this.box = null;

    this.image.addEventListener("load", () => this.initialize());
    if (this.image.complete) this.initialize();
    this.canvas.addEventListener("pointerdown", (event) => this.onPointerDown(event));
    this.canvas.addEventListener("pointermove", (event) => this.onPointerMove(event));
    this.canvas.addEventListener("pointerleave", () => this.updateCursor(null));
    window.addEventListener("pointerup", () => this.onPointerUp());
    block.querySelector("[data-reset-roi]").addEventListener("click", () => this.reset());
    window.addEventListener("resize", () => this.render());
  }

  initialize() {
    const width = this.image.naturalWidth;
    const height = this.image.naturalHeight;
    const size = Math.round(Math.min(width, height) * 0.08);
    this.box = {
      x: Math.round((width - size) / 2),
      y: Math.round(height * 0.78),
      w: size,
      h: size
    };
    this.render();
  }

  reset() {
    this.initialize();
  }

  render() {
    if (!this.image.naturalWidth || !this.box) return;
    const rect = this.image.getBoundingClientRect();
    this.canvas.width = Math.round(rect.width);
    this.canvas.height = Math.round(rect.height);
    this.canvas.style.width = `${rect.width}px`;
    this.canvas.style.height = `${rect.height}px`;
    this.draw();
    this.writeInputs();
  }

  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const box = this.toCanvasBox(this.box);
    ctx.strokeStyle = "#e11d48";
    ctx.lineWidth = 2;
    ctx.fillStyle = "rgba(225, 29, 72, 0.16)";
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.strokeRect(box.x, box.y, box.w, box.h);

    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#e11d48";
    this.cornerPoints(box).forEach((point) => {
      ctx.beginPath();
      ctx.rect(point.x - 4, point.y - 4, 8, 8);
      ctx.fill();
      ctx.stroke();
    });
  }

  onPointerDown(event) {
    if (!this.box) return;
    this.dragging = true;
    this.canvas.setPointerCapture(event.pointerId);
    this.start = this.canvasPointToImage(event);
    this.startBox = { ...this.box };

    const hit = this.hitTest(event);
    this.mode = hit.mode;
    this.handle = hit.handle;
    if (this.mode === "draw") {
      this.box = { x: this.start.x, y: this.start.y, w: 1, h: 1 };
    }
    this.draw();
  }

  onPointerMove(event) {
    if (!this.dragging) {
      this.updateCursor(this.hitTest(event));
      return;
    }

    const current = this.canvasPointToImage(event);
    if (this.mode === "move") {
      const dx = current.x - this.start.x;
      const dy = current.y - this.start.y;
      this.box = this.constrainBox({
        x: this.startBox.x + dx,
        y: this.startBox.y + dy,
        w: this.startBox.w,
        h: this.startBox.h
      });
    } else if (this.mode === "resize") {
      this.box = this.resizeBox(this.startBox, current, this.handle);
    } else {
      const x1 = Math.min(this.start.x, current.x);
      const y1 = Math.min(this.start.y, current.y);
      const x2 = Math.max(this.start.x, current.x);
      const y2 = Math.max(this.start.y, current.y);
      this.box = this.constrainBox({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 });
    }

    this.draw();
    this.writeInputs();
  }

  onPointerUp() {
    if (!this.dragging) return;
    this.dragging = false;
    this.box = this.constrainBox(this.box);
    this.draw();
    this.writeInputs();
  }

  hitTest(event) {
    if (!event || !this.box) return { mode: "draw", handle: null };
    const canvasPoint = this.eventCanvasPoint(event);
    const canvasBox = this.toCanvasBox(this.box);
    const corners = this.cornerPoints(canvasBox);
    const names = ["nw", "ne", "se", "sw"];
    for (let i = 0; i < corners.length; i += 1) {
      if (Math.abs(canvasPoint.x - corners[i].x) <= 8 && Math.abs(canvasPoint.y - corners[i].y) <= 8) {
        return { mode: "resize", handle: names[i] };
      }
    }
    if (
      canvasPoint.x >= canvasBox.x &&
      canvasPoint.x <= canvasBox.x + canvasBox.w &&
      canvasPoint.y >= canvasBox.y &&
      canvasPoint.y <= canvasBox.y + canvasBox.h
    ) {
      return { mode: "move", handle: null };
    }
    return { mode: "draw", handle: null };
  }

  updateCursor(hit) {
    if (!hit) {
      this.canvas.style.cursor = "crosshair";
      return;
    }
    if (hit.mode === "move") {
      this.canvas.style.cursor = "move";
    } else if (hit.mode === "resize") {
      this.canvas.style.cursor = hit.handle === "nw" || hit.handle === "se" ? "nwse-resize" : "nesw-resize";
    } else {
      this.canvas.style.cursor = "crosshair";
    }
  }

  resizeBox(startBox, current, handle) {
    let left = startBox.x;
    let top = startBox.y;
    let right = startBox.x + startBox.w;
    let bottom = startBox.y + startBox.h;

    if (handle.includes("n")) top = current.y;
    if (handle.includes("s")) bottom = current.y;
    if (handle.includes("w")) left = current.x;
    if (handle.includes("e")) right = current.x;

    const x1 = Math.min(left, right);
    const y1 = Math.min(top, bottom);
    const x2 = Math.max(left, right);
    const y2 = Math.max(top, bottom);
    return this.constrainBox({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 });
  }

  constrainBox(box) {
    const width = this.image.naturalWidth;
    const height = this.image.naturalHeight;
    const w = Math.max(2, Math.min(Math.round(box.w), width));
    const h = Math.max(2, Math.min(Math.round(box.h), height));
    const x = Math.max(0, Math.min(Math.round(box.x), width - w));
    const y = Math.max(0, Math.min(Math.round(box.y), height - h));
    return { x, y, w, h };
  }

  canvasPointToImage(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * (this.image.naturalWidth / rect.width);
    const y = (event.clientY - rect.top) * (this.image.naturalHeight / rect.height);
    return {
      x: Math.round(Math.max(0, Math.min(this.image.naturalWidth - 1, x))),
      y: Math.round(Math.max(0, Math.min(this.image.naturalHeight - 1, y)))
    };
  }

  eventCanvasPoint(event) {
    const rect = this.canvas.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top
    };
  }

  toCanvasBox(box) {
    const scaleX = this.canvas.width / this.image.naturalWidth;
    const scaleY = this.canvas.height / this.image.naturalHeight;
    return {
      x: box.x * scaleX,
      y: box.y * scaleY,
      w: box.w * scaleX,
      h: box.h * scaleY
    };
  }

  cornerPoints(box) {
    return [
      { x: box.x, y: box.y },
      { x: box.x + box.w, y: box.y },
      { x: box.x + box.w, y: box.y + box.h },
      { x: box.x, y: box.y + box.h }
    ];
  }

  writeInputs() {
    if (!this.box) return;
    this.inputs.x.value = Math.round(this.box.x);
    this.inputs.y.value = Math.round(this.box.y);
    this.inputs.w.value = Math.round(this.box.w);
    this.inputs.h.value = Math.round(this.box.h);
  }
}

document.querySelectorAll("[data-roi-block]").forEach((block) => new RoiSelector(block));

