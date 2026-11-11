/**
 * A muted, looping video that only plays while somebody can see it.
 *
 * Used twice: behind the conversation panel, and as the FACE in the dial.
 *
 *   * nothing is downloaded until it is first shown;
 *   * the file is read once into memory (a Blob URL), so every loop replays
 *     from RAM — no re-requests, no seeking over HTTP, and it works offline
 *     because the service worker answers that one request from its cache;
 *   * it pauses whenever it is not shown, the tab is hidden, or JARVIS is in
 *     standby, so a video nobody is looking at costs no decoding at all;
 *   * with reduced motion it shows a still instead of playing: the video's
 *     first frame (`reducedFrame: true`), or nothing, leaving whatever still
 *     image the page put underneath it (`reducedFrame: false`).
 *
 * The element gets the class `is-ready` only while it is actually showing
 * picture, so CSS can fade it in over a still fallback and fade it back out
 * when it is paused or fails.
 *
 * Muted with no audio track, so Chrome lets it autoplay without a gesture;
 * if a browser still refuses, the still underneath simply stays.
 */
export default class LoopVideo {
  constructor(video, { src, reducedFrame = true } = {}) {
    this.video = video;
    this.src = src;
    this.reducedFrame = reducedFrame;
    this.open = false;
    this.reduced = false;
    this.standby = false;
    this.loading = null;
    if (!video || !src) return;

    // Set as properties too: the attribute alone is not always enough for
    // Chrome's autoplay check when the source is assigned from script.
    video.muted = true;
    video.defaultMuted = true;
    video.loop = true;
    video.playsInline = true;
    video.controls = false;
    video.disablePictureInPicture = true;
    video.disableRemotePlayback = true;

    video.addEventListener('playing', () => video.classList.add('is-ready'));
    video.addEventListener('pause', () => {
      // A paused video falls back to the still underneath — except under
      // reduced motion with `reducedFrame`, where the frame IS the still.
      if (!(this.reduced && this.reducedFrame)) video.classList.remove('is-ready');
    });
    video.addEventListener('error', () => video.classList.remove('is-ready'));
    video.addEventListener('loadeddata', () => {
      if (this.reduced && this.reducedFrame) video.classList.add('is-ready');
    });

    document.addEventListener('visibilitychange', () => this._sync());
  }

  /** True while the place it lives in is on screen. */
  setOpen(open) {
    if (this.open === !!open) return;
    this.open = !!open;
    this._sync();
  }

  setReducedMotion(reduced) {
    this.reduced = !!reduced;
    if (this.video && !(reduced && this.reducedFrame) && this.video.paused) {
      this.video.classList.remove('is-ready');
    }
    this._sync();
  }

  setStandby(standby) {
    this.standby = !!standby;
    this._sync();
  }

  get shouldPlay() {
    return this.open && !this.reduced && !this.standby && !document.hidden;
  }

  async _sync() {
    const video = this.video;
    if (!video || !this.src) return;
    if (!this.shouldPlay) {
      if (!video.paused) video.pause();
      // Reduced motion may still want the picture, just not the movement.
      if (this.open && this.reduced && this.reducedFrame) this._load();
      return;
    }
    await this._load();
    if (!this.shouldPlay) return;           // hidden again while loading
    try {
      await video.play();
    } catch {
      /* autoplay refused or source unusable — the still stays */
    }
  }

  /** Loads the file once; later calls reuse the same promise. */
  _load() {
    if (this.loading) return this.loading;
    const video = this.video;
    this.loading = (async () => {
      try {
        const response = await fetch(this.src);
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        video.src = URL.createObjectURL(blob);
      } catch {
        // No fetch/Blob support or the request failed: stream it directly.
        video.src = this.src;
      }
      video.load();
    })();
    return this.loading;
  }
}
