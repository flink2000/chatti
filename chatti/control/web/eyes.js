/* The chatti face, rebuilt for the browser.
 *
 * Every number here comes from the firmware
 * (main/boards/waveshare/esp32-s3-touch-lcd-1.83/chatti_face_display.cc) so the
 * panel shows the same face as the device rather than a lookalike:
 *
 *   stage          284 x 240, eye centres fixed at x = 88 / 196, y = 120
 *                  (the device is used on its side; the panel is turned a
 *                  quarter counter-clockwise, see boards/.../config.h)
 *   radius         24 px, clamped by the browser to half the shorter edge,
 *                  which is what turns a blinking eye into a capsule
 *   idle           62 x 84, offset -12, blinks, looks around +-22
 *   thinking       58 x 44, offset -12, no blink, looks around +-14
 *   connecting     62 x 30, offset -12, still
 *   blink          height -> 6 px in 90 ms, back in 120 ms, ease-in-out
 *   blink interval random 2200..5000 ms
 *   gaze           420 ms, ease-in-out, interval random 2800..6000 ms
 *   state change   260 ms, ease-in-out
 */

const BLINK_HEIGHT = 6;

const GEOMETRY = {
  idle:       { w: 62, h: 84, offsetY: -12, blinks: true,  gaze: 22 },
  thinking:   { w: 58, h: 44, offsetY: -12, blinks: false, gaze: 14 },
  connecting: { w: 62, h: 30, offsetY: -12, blinks: false, gaze: 0 },
  // Not a firmware state — a switched-off device draws nothing at all. This is
  // the closed lid of the blink, held: the same shape the eye already passes
  // through, so "off" reads as sleeping rather than as a rendering fault.
  off:        { w: 62, h: BLINK_HEIGHT, offsetY: -12, blinks: false, gaze: 0 },
};

const BLINK_CLOSE_MS = 90;
const BLINK_OPEN_MS = 120;
const GAZE_MS = 420;
const STATE_MS = 260;

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

export function createFace(root) {
  const eyes = [root.querySelector('.eye-left'), root.querySelector('.eye-right')];
  let state = 'connecting';
  let geometry = GEOMETRY[state];
  let gazeX = 0;
  let blinkTimer = null;
  let gazeTimer = null;

  function paint(durationMs, height) {
    for (const eye of eyes) {
      eye.style.transitionDuration = `${durationMs}ms`;
      eye.style.width = `${geometry.w}px`;
      eye.style.height = `${height ?? geometry.h}px`;
      eye.style.transform =
        `translate(-50%, -50%) translate(${gazeX}px, ${geometry.offsetY}px)`;
    }
  }

  function blink() {
    if (!geometry.blinks) return;
    paint(BLINK_CLOSE_MS, BLINK_HEIGHT);
    setTimeout(() => paint(BLINK_OPEN_MS), BLINK_CLOSE_MS);
  }

  function scheduleBlink() {
    clearTimeout(blinkTimer);
    blinkTimer = setTimeout(() => {
      blink();
      scheduleBlink();
    }, randomBetween(2200, 5000));
  }

  function scheduleGaze() {
    clearTimeout(gazeTimer);
    gazeTimer = setTimeout(() => {
      if (geometry.gaze > 0) {
        // Keep drawing until the target differs, so every tick is visible —
        // same rule as the firmware.
        const options = [-geometry.gaze, 0, geometry.gaze].filter((v) => v !== gazeX);
        gazeX = options[Math.floor(Math.random() * options.length)];
        paint(GAZE_MS);
      }
      scheduleGaze();
    }, randomBetween(2800, 6000));
  }

  function setState(next) {
    if (!GEOMETRY[next] || next === state) return;
    state = next;
    geometry = GEOMETRY[next];
    if (geometry.gaze === 0) gazeX = 0;
    paint(STATE_MS);
    scheduleBlink();
    scheduleGaze();
  }

  paint(0);
  scheduleBlink();
  scheduleGaze();

  return { setState, getState: () => state };
}
