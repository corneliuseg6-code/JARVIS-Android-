/**
 * The HUD clock, in the band above the dial.
 *
 * Fixed to Europe/London, as asked, rather than the phone's own zone — so it
 * stays right whether the phone is in Bucharest or anywhere else, and it
 * follows British summer time on its own because Intl does the work.
 *
 * It ticks on a timer aligned to the next whole second rather than every
 * 1000ms from an arbitrary start, so the seconds change when the second
 * changes instead of drifting a little further off with each minute.
 */
const ZONE = 'Europe/London';

export default class Clock {
  constructor({ language = 'ro' } = {}) {
    this.timeEl = document.getElementById('hud-time');
    this.dateEl = document.getElementById('hud-date');
    this.timer = 0;
    this.language = language;
    this._build();
    this.start();
    // A phone sleeping through a minute leaves a stale reading on screen;
    // coming back is the moment to fix it.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.stop();
      else this.start();
    });
  }

  _build() {
    const options = { timeZone: ZONE, hour12: false };
    try {
      this.timeFmt = new Intl.DateTimeFormat(this.language, {
        ...options, hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
      this.dateFmt = new Intl.DateTimeFormat(this.language, {
        ...options, weekday: 'short', day: 'numeric', month: 'short',
      });
    } catch {
      // A browser that does not know the zone or the locale still gets a
      // clock, just the device's own.
      this.timeFmt = new Intl.DateTimeFormat(undefined, {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
      });
      this.dateFmt = new Intl.DateTimeFormat(undefined, {
        weekday: 'short', day: 'numeric', month: 'short',
      });
    }
  }

  setLanguage(language) {
    this.language = language || 'ro';
    this._build();
    this.tick();
  }

  start() {
    this.stop();
    this.tick();
    const align = () => {
      // Fire just after the next whole second, then keep re-aligning.
      const delay = 1000 - (Date.now() % 1000) + 8;
      this.timer = setTimeout(() => { this.tick(); align(); }, delay);
    };
    align();
  }

  stop() {
    if (this.timer) { clearTimeout(this.timer); this.timer = 0; }
  }

  tick() {
    const now = new Date();
    if (this.timeEl) this.timeEl.textContent = this.timeFmt.format(now);
    if (this.dateEl) this.dateEl.textContent = this.dateFmt.format(now);
  }
}
