/**
 * The looping video behind the expanded messages panel.
 *
 * It lives only inside the chat dock and only plays while the dock is open:
 *
 *   * nothing is downloaded until the panel is opened for the first time;
 *   * the file is read once into memory (a Blob URL), so every loop replays
 *     from RAM — no re-requests, no seeking over HTTP, and it works offline
 *     because the service worker answers that one request from its cache;
 *   * it pauses the moment the panel closes, the tab is hidden, the screen
 *     goes immersive, or JARVIS goes to standby, so a closed panel costs
 *     nothing at all — no decoding, no compositing;
 *   * with reduced motion on it shows a single still frame instead of playing.
 *
 * It is muted with no audio track, so Chrome lets it autoplay without a
 * gesture; if a browser still refuses, the panel simply stays as it was.
 */
const SRC = '/media/chat-bg.mp4';

export default class DockVideo {
  constructor(video) {
    this.video = video;
    this.open = false;
    this.reduced = false;
    this.standby = false;
    this.loading = null;
    this.objectUrl = null;
    if (!video) return;

    // Set as properties too: the attribute alone is not always enough for
    // Chrome's autoplay check when the source is assigned from script.
    video.muted = true;
    video.defaultMuted = true;
    video.loop = true;
    video.playsInline = true;
    video.disablePictureInPicture = true;
    video.disableRemotePlayback = true;

    video.addEventListener('playing', () => video.classList.add('is-ready'));
    video.addEventListener('loadeddata', () => {
      if (this.reduced) video.classList.add('is-ready');
    });

    document.addEventListener('visibilitychange', () => this._sync());
  }

  /** Called whenever the messages panel opens or closes. */
  setOpen(open) {
    this.open = !!open;
    this._sync();
  }

  setReducedMotion(reduced) {
    this.reduced = !!reduced;
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
    if (!video) return;
    if (!this.shouldPlay) {
      if (!video.paused) video.pause();
      // Reduced motion still deserves the picture, just not the movement.
      if (this.open && this.reduced) this._load();
      return;
    }
    await this._load();
    if (!this.shouldPlay) return;           // closed again while loading
    try {
      await video.play();
    } catch {
      /* autoplay refused or source unusable — the panel looks as before */
    }
  }

  /** Loads the file once; later calls reuse the same promise. */
  _load() {
    if (this.loading) return this.loading;
    const video = this.video;
    this.loading = (async () => {
      try {
        const response = await fetch(SRC);
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        this.objectUrl = URL.createObjectURL(blob);
        video.src = this.objectUrl;
      } catch {
        // No fetch/Blob support or the request failed: stream it directly.
        video.src = SRC;
      }
      video.load();
    })();
    return this.loading;
  }
}
