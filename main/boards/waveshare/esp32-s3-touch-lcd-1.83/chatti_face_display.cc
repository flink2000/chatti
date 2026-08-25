#include "chatti_face_display.h"

#include "assets/lang_config.h"
#include "lvgl_font.h"
#include "lvgl_theme.h"
#include "material_symbols.h"

#include <esp_log.h>
#include <esp_random.h>

#include <cinttypes>
#include <cstring>
#include <tuple>
#include <utility>

#define TAG "ChattiFace"

namespace chatti {

namespace {

// Eye colour and canvas. The face owns the whole screen.
constexpr uint32_t kBackgroundColor = 0x000000;
constexpr uint32_t kEyeColor = 0x2FE3FF;
// The controls are secondary and must not pull attention from the eyes, so
// they sit in a dark grey that is legible but easy to overlook. A switched-off
// channel shifts towards warm - the same signal for both buttons.
constexpr uint32_t kControlColor = 0x2E3640;
constexpr uint32_t kControlOffColor = 0x5E4A4A;
// What the device is doing has to be readable at a glance, so the state text
// keeps the brighter grey the controls used to share with it.
constexpr uint32_t kStateTextColor = 0x3E4854;

// Two square touch targets, centred at the bottom. 40 px is about the smallest
// that can be hit reliably with a fingertip on this panel.
constexpr int32_t kControlSize = 40;
constexpr int32_t kControlGap = 8;
// The symbol font exists in 30 px and 14 px only, and 14 would be a different
// design rather than a slightly smaller icon, so the text symbol is scaled.
// LV_SCALE_NONE is 256, so this is 88 %.
constexpr int32_t kSubtitleIconScale = 226;
// The symbol font has no "subtitles off" glyph - material_symbols.h has no
// closed-caption symbol at all - so the off state is drawn: a stroke across the
// bubble, running the same way as the one built into the volume-off glyph.
constexpr int32_t kSlashInset = 9;
constexpr int32_t kSlashWidth = 3;

// How long the controls stay on screen before they retire again. They are
// settings, not part of the conversation; once seen they are in the way, and
// the face is meant to have the screen to itself.
//
// Two durations, because the two ways in are not the same thing. Showing up on
// their own when the device falls idle is an offer, and a short one. Being
// asked for by BOOT is a decision - the user wants to reach a control, so the
// window is longer than the moment it takes to aim at a 40 px button.
constexpr uint32_t kControlsIdleMs = 10000;
constexpr uint32_t kControlsRequestedMs = 15000;
// The spoken answer gets full contrast; the transcript of the question is only
// an echo of what the device understood, so it stays quieter.
constexpr uint32_t kSpokenTextColor = 0xF2F6F8;
constexpr uint32_t kTranscriptColor = 0x6E7C88;

// Text area. Whenever there is text it owns everything above the bottom row -
// the eyes fade out for it rather than share the screen. In landscape that is
// not a luxury: 240 px of height cannot carry eyes and several lines at once,
// and splitting it left both halves too small to be any good.
constexpr int32_t kSubtitleMargin = 16;
constexpr int32_t kSubtitleTop = 14;
constexpr int32_t kSubtitleBottomReserve = 46;

// How long the last sentence stays readable after the audio has finished.
constexpr uint32_t kSubtitleHoldMs = 2500;
// Fallback for notifications that do not carry their own duration.
constexpr uint32_t kNotificationHoldMs = 3000;

// A long answer does not fit on 284 x 240 px, so it scrolls instead of running
// off the screen. Fallback pace, used when the sentence length is unknown (the
// transcript, or a server that does not send duration_ms): loop until replaced.
constexpr int32_t kScrollSpeedPxPerSec = 22;
constexpr uint32_t kScrollStartDelayMs = 1800;
constexpr uint32_t kScrollRestartDelayMs = 2200;

// When the server reports the speaking length, the scroll runs once, timed to
// the voice: a short lead-in to read the first lines, then down to the bottom,
// arriving there just before the sentence ends.
constexpr uint32_t kPacedLeadInMs = 900;
constexpr uint32_t kPacedTailMs = 500;

// Horizontal distance of each eye centre from the screen centre. Wider than
// the portrait 46 because the turned screen is 44 px broader: keeping the old
// value would have left the pair huddled in the middle of a wide stage.
constexpr int32_t kEyeSpread = 54;
// Corner radius; large enough to read as a rounded shape, small enough to stay
// rectangular when the eye is squashed shut.
constexpr int32_t kEyeRadius = 24;
// Height an eye collapses to while blinking.
constexpr int32_t kBlinkHeight = 6;

constexpr uint32_t kStateTransitionMs = 260;
constexpr uint32_t kGazeTransitionMs = 420;
// How long the eyes take to step aside for text, and to come back afterwards.
// Slower than a state change: this reads as the face making room, and a quick
// fade at the same pace as a blink would look like a glitch instead.
constexpr uint32_t kEyeFadeMs = 360;

// ASR takes 13-25 s, LLM + TTS around 30 s on the local GPU. Both waiting states
// are derived rather than reported, so both need a way out if nothing arrives.
constexpr uint32_t kWaitingTimeoutMs = 180000;

uint32_t RandomRange(uint32_t min_value, uint32_t max_value) {
    return min_value + (esp_random() % (max_value - min_value + 1));
}

void SetEyeHeight(void* obj, int32_t value) {
    lv_obj_set_height(static_cast<lv_obj_t*>(obj), value);
}

void SetEyeWidth(void* obj, int32_t value) {
    lv_obj_set_width(static_cast<lv_obj_t*>(obj), value);
}

void SetTranslateX(void* obj, int32_t value) {
    lv_obj_set_style_translate_x(static_cast<lv_obj_t*>(obj), value, 0);
}

void SetTranslateY(void* obj, int32_t value) {
    lv_obj_set_style_translate_y(static_cast<lv_obj_t*>(obj), value, 0);
}

void SetOpa(void* obj, int32_t value) {
    lv_obj_set_style_opa(static_cast<lv_obj_t*>(obj), static_cast<lv_opa_t>(value), 0);
}

void AnimateTo(lv_obj_t* obj, lv_anim_exec_xcb_t exec_cb, int32_t from, int32_t to,
               uint32_t duration_ms) {
    lv_anim_t anim;
    lv_anim_init(&anim);
    lv_anim_set_var(&anim, obj);
    lv_anim_set_exec_cb(&anim, exec_cb);
    lv_anim_set_values(&anim, from, to);
    lv_anim_set_duration(&anim, duration_ms);
    lv_anim_set_path_cb(&anim, lv_anim_path_ease_in_out);
    lv_anim_start(&anim);
}

}  // namespace

const FaceDisplay::EyeGeometry& FaceDisplay::GeometryFor(FaceState state) {
    // On a 284 x 240 stage the eyes sit slightly above centre so the bottom row
    // does not crowd them. They no longer have to make room for text: as soon as
    // a sentence is on screen they fade out entirely (see ApplyState), which is
    // why thinking and speaking share the resting offset instead of climbing
    // out of the way. Their offsets only ever show with subtitles switched off.
    //
    //                                       width height offset_y blinks looks_around
    static const EyeGeometry kBooting     = {  62,    84,    -18,    true, false };
    static const EyeGeometry kIdle        = {  62,    84,    -12,    true,  true };
    static const EyeGeometry kConnecting  = {  62,    30,    -12,   false, false };
    static const EyeGeometry kListening   = {  70,    98,    -12,    true, false };
    // Waiting for the transcript: round and looking down, as if replaying what
    // it just heard. Deliberately distinct from thinking, which looks up.
    static const EyeGeometry kHearing     = {  64,    64,     10,    true,  true };
    // Narrowed, and drifting slowly so the long LLM wait looks busy rather than
    // frozen. With subtitles on this is invisible - the transcript is up.
    static const EyeGeometry kThinking    = {  58,    44,    -12,   false,  true };
    // Small and still. Only ever seen with the text switched off, when the shape
    // of the eyes is the sole signal that an answer is being read out.
    static const EyeGeometry kSpeaking    = {  44,    54,    -12,    true, false };

    switch (state) {
        case FaceState::kIdle:       return kIdle;
        case FaceState::kConnecting: return kConnecting;
        case FaceState::kListening:  return kListening;
        case FaceState::kHearing:    return kHearing;
        case FaceState::kThinking:   return kThinking;
        case FaceState::kSpeaking:   return kSpeaking;
        case FaceState::kBooting:
        default:                     return kBooting;
    }
}

const char* FaceDisplay::LabelFor(FaceState state) {
    // Shown in the bottom row while a turn is running. The xiaozhi status text
    // is unusable here - it reads SPEAKING for the whole LLM wait - so the face
    // labels its own derived states. User facing, hence localized: the FACE_*
    // keys live in main/assets/locales/<lang>/language.json like every other
    // string on screen.
    switch (state) {
        case FaceState::kConnecting: return Lang::Strings::FACE_CONNECTING;
        case FaceState::kListening:  return Lang::Strings::FACE_LISTENING;
        case FaceState::kHearing:    return Lang::Strings::FACE_HEARING;
        case FaceState::kThinking:   return Lang::Strings::FACE_THINKING;
        case FaceState::kSpeaking:   return Lang::Strings::FACE_SPEAKING;
        default:                     return "";
    }
}

FaceDisplay::FaceDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                         int width, int height, int offset_x, int offset_y, bool mirror_x,
                         bool mirror_y, bool swap_xy)
    : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y, mirror_x, mirror_y,
                    swap_xy) {
    geometry_ = GeometryFor(FaceState::kBooting);
}

FaceDisplay::~FaceDisplay() {
    DisplayLockGuard lock(this);
    if (blink_timer_ != nullptr) {
        lv_timer_delete(blink_timer_);
        blink_timer_ = nullptr;
    }
    if (gaze_timer_ != nullptr) {
        lv_timer_delete(gaze_timer_);
        gaze_timer_ = nullptr;
    }
    if (subtitle_clear_timer_ != nullptr) {
        lv_timer_delete(subtitle_clear_timer_);
        subtitle_clear_timer_ = nullptr;
    }
}

void FaceDisplay::SetupUI() {
    if (setup_ui_called_) {
        ESP_LOGW(TAG, "SetupUI() called multiple times, skipping duplicate call");
        return;
    }

    // Deliberately does NOT call LcdDisplay::SetupUI(): the whole point of the
    // face is to replace the xiaozhi chat UI, not to sit on top of it. All base
    // class widgets stay null, and every base method that would touch them is
    // either null-guarded upstream or overridden below.
    Display::SetupUI();
    DisplayLockGuard lock(this);

    auto screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(kBackgroundColor), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);

    for (int i = 0; i < 2; i++) {
        lv_obj_t* eye = lv_obj_create(screen);
        lv_obj_remove_flag(eye, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_remove_flag(eye, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_border_width(eye, 0, 0);
        lv_obj_set_style_pad_all(eye, 0, 0);
        lv_obj_set_style_radius(eye, kEyeRadius, 0);
        lv_obj_set_style_bg_color(eye, lv_color_hex(kEyeColor), 0);
        lv_obj_set_style_bg_opa(eye, LV_OPA_COVER, 0);
        lv_obj_set_size(eye, geometry_.width, geometry_.height);
        // Aligned once. LVGL keeps the alignment when the size changes, so the
        // eye stays centred while it squashes - no y correction needed.
        lv_obj_align(eye, LV_ALIGN_CENTER, i == 0 ? -kEyeSpread : kEyeSpread, 0);

        (i == 0 ? eye_left_ : eye_right_) = eye;
    }

    // A clipping window for the text. Without it a long answer runs straight
    // over the status line and off the bottom of the screen; children of an
    // LVGL object are clipped to it unless OVERFLOW_VISIBLE is set.
    subtitle_area_ = lv_obj_create(screen);
    lv_obj_remove_flag(subtitle_area_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(subtitle_area_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(subtitle_area_, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(subtitle_area_, 0, 0);
    lv_obj_set_style_pad_all(subtitle_area_, 0, 0);
    lv_obj_set_style_radius(subtitle_area_, 0, 0);

    // The sentence being spoken. M4 will animate this label word by word; for
    // now the whole sentence appears at once when its audio starts.
    subtitle_label_ = lv_label_create(subtitle_area_);
    lv_label_set_long_mode(subtitle_label_, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(subtitle_label_, width_ - 2 * kSubtitleMargin);
    lv_obj_set_height(subtitle_label_, LV_SIZE_CONTENT);
    lv_obj_set_style_text_align(subtitle_label_, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(subtitle_label_, lv_color_hex(kSpokenTextColor), 0);
    lv_label_set_text(subtitle_label_, "");
    lv_obj_align(subtitle_label_, LV_ALIGN_TOP_MID, 0, 0);

    // Two controls, centred at the very bottom: speaker, and whether the words
    // are written on screen. Icons only - the words "Laut"/"Stumm" next to a
    // symbol that already says it were noise. Each button carries a touch area
    // far larger than its glyph, otherwise it is hard to hit with a finger.
    controls_row_ = lv_obj_create(screen);
    lv_obj_remove_flag(controls_row_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(controls_row_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(controls_row_, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(controls_row_, 0, 0);
    lv_obj_set_style_pad_all(controls_row_, 0, 0);
    lv_obj_set_style_radius(controls_row_, 0, 0);
    lv_obj_set_size(controls_row_, 2 * kControlSize + kControlGap, kControlSize);
    lv_obj_align(controls_row_, LV_ALIGN_BOTTOM_MID, 0, -2);
    lv_obj_set_flex_flow(controls_row_, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(controls_row_, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(controls_row_, kControlGap, 0);

    auto make_button = [this](lv_event_cb_t handler) {
        lv_obj_t* button = lv_obj_create(controls_row_);
        lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(button, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_bg_opa(button, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(button, 0, 0);
        lv_obj_set_style_pad_all(button, 0, 0);
        lv_obj_set_style_radius(button, 0, 0);
        lv_obj_set_size(button, kControlSize, kControlSize);
        lv_obj_add_event_cb(button, handler, LV_EVENT_CLICKED, this);
        lv_obj_t* icon = lv_label_create(button);
        lv_obj_remove_flag(icon, LV_OBJ_FLAG_CLICKABLE);  // the click is the button's
        lv_obj_center(icon);
        return std::make_pair(button, icon);
    };

    std::tie(mute_button_, mute_icon_) = make_button(MuteClickedCallback);
    std::tie(subtitle_button_, subtitle_icon_) = make_button(SubtitlesClickedCallback);

    // A touch smaller than the speaker beside it. The glyph is a bitmap, so the
    // only way to resize it is a transform; the pivot has to move to the middle
    // or the icon shrinks towards its top left corner and stops being centred.
    lv_obj_set_style_transform_scale_x(subtitle_icon_, kSubtitleIconScale, 0);
    lv_obj_set_style_transform_scale_y(subtitle_icon_, kSubtitleIconScale, 0);
    lv_obj_set_style_transform_pivot_x(subtitle_icon_, lv_pct(50), 0);
    lv_obj_set_style_transform_pivot_y(subtitle_icon_, lv_pct(50), 0);

    // The stroke that marks the text as switched off. Drawn rather than picked
    // from the font, and kept in the button's own coordinates - hence the fixed
    // size instead of letting the line size itself to its points.
    static const lv_point_precise_t kSlashPoints[] = {
        {kSlashInset, kSlashInset},
        {kControlSize - kSlashInset, kControlSize - kSlashInset},
    };
    subtitle_slash_ = lv_line_create(subtitle_button_);
    lv_line_set_points(subtitle_slash_, kSlashPoints, 2);
    lv_obj_remove_flag(subtitle_slash_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(subtitle_slash_, kControlSize, kControlSize);
    lv_obj_align(subtitle_slash_, LV_ALIGN_TOP_LEFT, 0, 0);
    lv_obj_set_style_line_width(subtitle_slash_, kSlashWidth, 0);
    lv_obj_set_style_line_rounded(subtitle_slash_, true, 0);

    // Same slot as the mute row: exactly one of the two is ever visible.
    // Idle -> the mute control, anything else -> what the device is doing.
    // Same slot as the controls, and also where the boot messages go: those
    // used to land in the text area, which sits at eye height while the device
    // is still starting, so "Verbindung zum Netzwerk" was written across the
    // face. Wrapping, because those messages are longer than a state word.
    state_label_ = lv_label_create(screen);
    lv_label_set_long_mode(state_label_, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(state_label_, width_ - 2 * kSubtitleMargin);
    lv_obj_set_style_text_align(state_label_, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(state_label_, lv_color_hex(kStateTextColor), 0);
    lv_label_set_text(state_label_, "");
    lv_obj_align(state_label_, LV_ALIGN_BOTTOM_MID, 0, -10);

    // Both hidden until the device is actually up: Wi-Fi scan, OTA check and
    // activation all run first. Revealed on the first STANDBY, exactly where
    // upstream used to flash "Version x.y.z".
    lv_obj_add_flag(controls_row_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(state_label_, LV_OBJ_FLAG_HIDDEN);

    ApplyFont();
    ApplyMuteAppearance();
    ApplySubtitleAppearance();
    ApplyBottomRow();

    // A tap anywhere brings the controls back. It has to sit on the screen
    // itself: the buttons are hidden while the row is away, and LVGL does not
    // deliver input to hidden objects, so nothing else would ever see the touch.
    lv_obj_add_flag(screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(screen, ScreenTappedCallback, LV_EVENT_CLICKED, this);

    blink_timer_ = lv_timer_create(BlinkTimerCallback, RandomRange(2200, 5000), this);
    gaze_timer_ = lv_timer_create(GazeTimerCallback, RandomRange(2800, 6000), this);
    controls_timer_ = lv_timer_create(ControlsHideCallback, kControlsIdleMs, this);
    lv_timer_pause(controls_timer_);
    // Persistent but normally paused, so there is never a dangling one-shot.
    subtitle_clear_timer_ = lv_timer_create(SubtitleClearCallback, kSubtitleHoldMs, this);
    lv_timer_pause(subtitle_clear_timer_);

    // Deliberately not kIdle: idle means "connected and waiting", and that is
    // not true yet. ApplyState() renders the booting geometry.
    ApplyState();
}

void FaceDisplay::SetFaceState(FaceState state) {
    if (state == state_) {
        return;
    }
    ESP_LOGI(TAG, "Face state %d -> %d", static_cast<int>(state_), static_cast<int>(state));

    // Going idle straight after speaking is the one case where the eyes must
    // wait. The answer stays readable for another moment, and full size eyes
    // would grow right back over it.
    bool hold_for_subtitle = state == FaceState::kIdle && state_ == FaceState::kSpeaking &&
                             subtitle_label_ != nullptr &&
                             lv_label_get_text(subtitle_label_)[0] != '\0';

    apply_state_deferred_ = false;
    state_ = state;
    if (state == FaceState::kHearing || state == FaceState::kThinking) {
        waiting_since_ms_ = lv_tick_get();
    }

    if (hold_for_subtitle) {
        lv_timer_reset(subtitle_clear_timer_);
        lv_timer_resume(subtitle_clear_timer_);
        apply_state_deferred_ = true;
        return;  // SubtitleClearCallback finishes the transition
    }

    if (subtitle_label_ != nullptr) {
        // Any other transition takes the screen over right away. Listening in
        // particular starts a new turn and must drop the previous answer.
        lv_timer_pause(subtitle_clear_timer_);
        if (state == FaceState::kListening || state == FaceState::kIdle) {
            lv_label_set_text(subtitle_label_, "");
            subtitle_duration_ms_ = 0;
        }
    }

    if (state == FaceState::kIdle) {
        // Back at the start of a conversation: show the controls again, and let
        // them time out from here rather than from whenever they last appeared.
        RevealControls(kControlsIdleMs);
    } else {
        ApplyBottomRow();
    }
    ApplyState();
}

void FaceDisplay::ApplyFont() {
    // LVGL styles keep a raw lv_font_t pointer. Assets::ApplyConfig() swaps the
    // fonts inside the theme and then calls SetTheme() to let every widget
    // rebind - a widget that misses this crashes on the freed font as soon as it
    // is redrawn (PC 0x0 in lv_font_get_glyph_width).
    if (current_theme_ == nullptr) {
        return;
    }
    auto theme = static_cast<LvglTheme*>(current_theme_);
    if (theme->text_font() == nullptr || theme->text_font()->font() == nullptr) {
        return;
    }
    const lv_font_t* font = theme->text_font()->font();
    lv_obj_set_style_text_font(lv_screen_active(), font, 0);
    if (subtitle_label_ != nullptr) {
        lv_obj_set_style_text_font(subtitle_label_, font, 0);
    }
    if (state_label_ != nullptr) {
        lv_obj_set_style_text_font(state_label_, font, 0);
    }
    // Larger glyphs for the two controls: the small icon font was hard to read
    // at a glance, and these are the only controls on the screen.
    //
    // Every own label must be rebound here, without exception. LVGL styles hold
    // a raw lv_font_t*, and Assets::ApplyConfig() swaps the fonts inside the
    // theme and then calls SetTheme() so that each widget picks up the new
    // pointer. A label missed here keeps pointing at freed memory and takes the
    // device down in lv_font_get_glyph_width - see lvgl_display.cc:60.
    auto icon = theme->large_icon_font() != nullptr && theme->large_icon_font()->font() != nullptr
                    ? theme->large_icon_font()->font()
                    : (theme->icon_font() != nullptr ? theme->icon_font()->font() : nullptr);
    if (icon != nullptr) {
        for (lv_obj_t* label : {mute_icon_, subtitle_icon_}) {
            if (label != nullptr) {
                lv_obj_set_style_text_font(label, icon, 0);
            }
        }
    }
}

void FaceDisplay::ApplyBottomRow() {
    if (controls_row_ == nullptr || state_label_ == nullptr) {
        return;
    }

    // Exactly one thing lives in the bottom slot at a time. Which one depends on
    // three questions, in this order: is the device usable yet, is a turn
    // running, and is the controls' visibility window still open.
    const char* text = nullptr;
    if (!ready_) {
        // Boot messages, put here by ShowNotificationLocked. Empty means the
        // device is still silent about what it is doing, and then so are we.
        text = lv_label_get_text(state_label_);
    } else if (state_ != FaceState::kIdle) {
        text = LabelFor(state_);
        lv_label_set_text(state_label_, text);
    }

    bool show_text = text != nullptr && text[0] != '\0';
    bool show_controls = ready_ && state_ == FaceState::kIdle && controls_visible_;

    if (show_controls) {
        lv_obj_remove_flag(controls_row_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(controls_row_, LV_OBJ_FLAG_HIDDEN);
    }
    if (show_text) {
        lv_obj_remove_flag(state_label_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(state_label_, LV_OBJ_FLAG_HIDDEN);
    }
}

void FaceDisplay::RevealControls(uint32_t visible_ms) {
    // Restarting the timer on every reveal is the point: the window is measured
    // from the moment the controls became visible, not from boot.
    controls_visible_ = true;
    if (controls_timer_ != nullptr) {
        lv_timer_set_period(controls_timer_, visible_ms);
        lv_timer_reset(controls_timer_);
        lv_timer_resume(controls_timer_);
    }
    ApplyBottomRow();
}

void FaceDisplay::ShowControls() {
    DisplayLockGuard lock(this);
    // Called from the button task, not from LVGL - hence the lock, which the
    // internal callers already hold.
    if (!ready_) {
        return;  // nothing belongs in the bottom row before the device is up
    }
    ESP_LOGI(TAG, "Controls requested by button");
    RevealControls(kControlsRequestedMs);
}

void FaceDisplay::ControlsHideCallback(lv_timer_t* timer) {
    auto self = static_cast<FaceDisplay*>(lv_timer_get_user_data(timer));
    lv_timer_pause(timer);
    self->controls_visible_ = false;
    ESP_LOGI(TAG, "Controls hidden, window elapsed");
    self->ApplyBottomRow();
}

void FaceDisplay::ScreenTappedCallback(lv_event_t* event) {
    auto self = static_cast<FaceDisplay*>(lv_event_get_user_data(event));
    // Reaches us only when the touch missed both buttons - either because they
    // are hidden, or because the tap landed next to them. LVGL does not bubble
    // events by default, so a tap meant for mute never arrives here.
    //
    // This used to bring the controls back. It does not any more: the tap is
    // the talk button now, and one gesture cannot both start a conversation and
    // mean "let me change a setting". BOOT asks for the controls instead.
    if (self->on_screen_tapped_) {
        self->on_screen_tapped_();
    }
}

void FaceDisplay::ApplyMuteAppearance() {
    if (mute_icon_ == nullptr) {
        return;
    }
    lv_label_set_text(mute_icon_, muted_ ? MATERIAL_SYMBOLS_VOLUME_OFF
                                         : MATERIAL_SYMBOLS_VOLUME_UP);
    lv_obj_set_style_text_color(
        mute_icon_, lv_color_hex(muted_ ? kControlOffColor : kControlColor), 0);
}

void FaceDisplay::ApplySubtitleAppearance() {
    if (subtitle_icon_ == nullptr) {
        return;
    }
    lv_color_t colour = lv_color_hex(subtitles_enabled_ ? kControlColor : kControlOffColor);
    lv_label_set_text(subtitle_icon_, MATERIAL_SYMBOLS_CHAT_BUBBLE);
    lv_obj_set_style_text_color(subtitle_icon_, colour, 0);
    if (subtitle_slash_ != nullptr) {
        lv_obj_set_style_line_color(subtitle_slash_, colour, 0);
        if (subtitles_enabled_) {
            lv_obj_add_flag(subtitle_slash_, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_remove_flag(subtitle_slash_, LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (subtitle_area_ != nullptr) {
        // Hiding the clipping window takes the text with it and leaves every
        // timer and scroll animation alone, so switching back mid-answer picks
        // the sentence up where the voice currently is.
        if (subtitles_enabled_) {
            lv_obj_remove_flag(subtitle_area_, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(subtitle_area_, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

void FaceDisplay::MuteClickedCallback(lv_event_t* event) {
    // Runs in the LVGL task, which already holds the lock.
    auto self = static_cast<FaceDisplay*>(lv_event_get_user_data(event));
    self->muted_ = !self->muted_;
    ESP_LOGI(TAG, "Mute toggled: %s", self->muted_ ? "muted" : "live");
    if (self->muted_ && !self->subtitles_enabled_) {
        // Silent and wordless at the same time means the device stops answering
        // in any perceivable way. Muting therefore always brings the text back.
        self->subtitles_enabled_ = true;
        ESP_LOGI(TAG, "Subtitles forced on by mute");
    }
    self->ApplyMuteAppearance();
    self->ApplySubtitleAppearance();
    self->ApplyState();  // the eyes make room for text only while there is text
    self->ApplyBottomRow();
    if (self->on_mute_toggled_) {
        self->on_mute_toggled_(self->muted_);
    }
}

void FaceDisplay::SubtitlesClickedCallback(lv_event_t* event) {
    auto self = static_cast<FaceDisplay*>(lv_event_get_user_data(event));
    if (self->muted_ && self->subtitles_enabled_) {
        // Same reason as above, from the other side: while the speaker is off
        // the text is the only output left, so it cannot be switched off too.
        ESP_LOGI(TAG, "Subtitles stay on while muted");
        return;
    }
    self->subtitles_enabled_ = !self->subtitles_enabled_;
    ESP_LOGI(TAG, "Subtitles toggled: %s", self->subtitles_enabled_ ? "on" : "off");
    self->ApplySubtitleAppearance();
    self->ApplyState();
}

void FaceDisplay::OnMuteToggled(std::function<void(bool)> callback) {
    on_mute_toggled_ = std::move(callback);
}

void FaceDisplay::OnScreenTapped(std::function<void()> callback) {
    on_screen_tapped_ = std::move(callback);
}

void FaceDisplay::ShowSubtitle(const char* text, bool spoken, uint32_t duration_ms) {
    if (subtitle_label_ == nullptr) {
        return;
    }
    lv_timer_pause(subtitle_clear_timer_);
    subtitle_duration_ms_ = duration_ms;
    lv_obj_set_style_text_color(
        subtitle_label_, lv_color_hex(spoken ? kSpokenTextColor : kTranscriptColor), 0);
    lv_label_set_text(subtitle_label_, text);
    // Not just UpdateSubtitleScroll(): text going up is what sends the eyes
    // away, and ApplyState() is what decides that. It updates the scroll too.
    ApplyState();
}

void FaceDisplay::UpdateSubtitleScroll() {
    if (subtitle_label_ == nullptr || subtitle_area_ == nullptr) {
        return;
    }

    // Never leave an old scroll running.
    lv_anim_delete(subtitle_label_, nullptr);
    lv_obj_set_style_translate_y(subtitle_label_, 0, 0);
    // Centred to begin with. The area is now the full height of the screen, so
    // a one-line answer pinned to its top edge would float in a lot of nothing.
    lv_obj_align(subtitle_label_, LV_ALIGN_CENTER, 0, 0);

    // The label grows with its content, so its height is only known once LVGL
    // has laid it out.
    lv_obj_update_layout(subtitle_area_);
    int32_t overflow = lv_obj_get_height(subtitle_label_) - lv_obj_get_height(subtitle_area_);
    if (overflow <= 0) {
        return;  // fits, nothing to scroll
    }

    // Taller than the window: centring would cut the first line off, so it goes
    // back to the top and scrolls from there.
    lv_obj_align(subtitle_label_, LV_ALIGN_TOP_MID, 0, 0);

    uint32_t lead_in = kScrollStartDelayMs;
    uint32_t scroll_ms = (overflow * 1000) / kScrollSpeedPxPerSec;
    uint32_t repeat_count = LV_ANIM_REPEAT_INFINITE;

    if (subtitle_duration_ms_ > 0) {
        // Paced by the voice: one single pass that reaches the bottom just
        // before the sentence is finished. No looping - looping only makes
        // sense while the wait has no known end.
        lead_in = kPacedLeadInMs;
        uint32_t overhead = kPacedLeadInMs + kPacedTailMs;
        if (subtitle_duration_ms_ > overhead) {
            scroll_ms = subtitle_duration_ms_ - overhead;
        } else {
            // Very short sentence: skip the lead-in rather than race.
            lead_in = 0;
            scroll_ms = subtitle_duration_ms_;
        }
        repeat_count = 0;
    }

    lv_anim_t anim;
    lv_anim_init(&anim);
    lv_anim_set_var(&anim, subtitle_label_);
    lv_anim_set_exec_cb(&anim, SetTranslateY);
    lv_anim_set_values(&anim, 0, -overflow);
    lv_anim_set_duration(&anim, scroll_ms);
    lv_anim_set_delay(&anim, lead_in);
    lv_anim_set_repeat_delay(&anim, kScrollRestartDelayMs);
    lv_anim_set_repeat_count(&anim, repeat_count);
    lv_anim_set_path_cb(&anim, lv_anim_path_linear);
    lv_anim_start(&anim);

    ESP_LOGI(TAG, "Subtitle overflows by %" PRId32 " px, scrolling over %" PRIu32 " ms (%s)",
             overflow, scroll_ms, subtitle_duration_ms_ > 0 ? "paced by voice" : "free running");
}

void FaceDisplay::SubtitleClearCallback(lv_timer_t* timer) {
    auto self = static_cast<FaceDisplay*>(lv_timer_get_user_data(timer));
    lv_timer_pause(timer);

    if (self->subtitle_label_ != nullptr) {
        lv_label_set_text(self->subtitle_label_, "");
        self->subtitle_duration_ms_ = 0;
    }
    if (self->apply_state_deferred_) {
        self->apply_state_deferred_ = false;
        if (self->state_ == FaceState::kIdle) {
            // This is the path back from a finished answer: SetFaceState left
            // early to let the last sentence stay readable, so the reveal that
            // normally happens on entering idle has to happen here instead.
            self->RevealControls(kControlsIdleMs);
        } else {
            self->ApplyBottomRow();
        }
    }
    // Unconditional, unlike the block above: the screen is empty now, so the
    // eyes are due back whether or not a state change was waiting on it. This
    // also stops a scroll still in progress.
    self->ApplyState();
}

bool FaceDisplay::HasVisibleSubtitle() const {
    return subtitles_enabled_ && subtitle_label_ != nullptr &&
           lv_label_get_text(subtitle_label_)[0] != '\0';
}

void FaceDisplay::ApplyState() {
    if (eye_left_ == nullptr) {
        return;
    }

    const EyeGeometry& target = GeometryFor(state_);

    // The eyes and the text never share the stage. Whenever a sentence is up,
    // the eyes fade out and it gets the whole screen above the bottom row; when
    // the text goes, they come back. In portrait they used to slide aside and
    // shrink instead, but 240 px of height cannot carry both, and half a face
    // over half an answer served neither.
    //
    // Note this asks the label, not the state: a notification during idle is
    // text too, and used to be written straight across the open eyes.
    bool eyes_hidden = HasVisibleSubtitle();
    lv_obj_t* eyes[] = {eye_left_, eye_right_};

    for (lv_obj_t* eye : eyes) {
        // Drop any blink or gaze animation still in flight, otherwise it would
        // fight the transition and leave the eye at the wrong size.
        lv_anim_delete(eye, nullptr);

        AnimateTo(eye, SetEyeWidth, lv_obj_get_width(eye), target.width, kStateTransitionMs);
        AnimateTo(eye, SetEyeHeight, lv_obj_get_height(eye), target.height, kStateTransitionMs);
        AnimateTo(eye, SetTranslateY, lv_obj_get_style_translate_y(eye, LV_PART_MAIN),
                  target.offset_y, kStateTransitionMs);
        AnimateTo(eye, SetOpa, lv_obj_get_style_opa(eye, LV_PART_MAIN),
                  eyes_hidden ? LV_OPA_TRANSP : LV_OPA_COVER, kEyeFadeMs);
    }

    geometry_ = target;

    if (subtitle_area_ != nullptr) {
        // One fixed area for every state, now that nothing has to be dodged.
        lv_obj_set_size(subtitle_area_, width_ - 2 * kSubtitleMargin,
                        height_ - kSubtitleTop - kSubtitleBottomReserve);
        lv_obj_align(subtitle_area_, LV_ALIGN_TOP_MID, 0, kSubtitleTop);
        UpdateSubtitleScroll();
    }

    if (!target.looks_around) {
        LookAt(0, kStateTransitionMs);
    }
}

void FaceDisplay::Blink() {
    lv_obj_t* eyes[] = {eye_left_, eye_right_};
    for (lv_obj_t* eye : eyes) {
        lv_anim_t anim;
        lv_anim_init(&anim);
        lv_anim_set_var(&anim, eye);
        lv_anim_set_exec_cb(&anim, SetEyeHeight);
        lv_anim_set_values(&anim, geometry_.height, kBlinkHeight);
        lv_anim_set_duration(&anim, 90);
        lv_anim_set_reverse_duration(&anim, 120);
        lv_anim_set_path_cb(&anim, lv_anim_path_ease_in_out);
        lv_anim_start(&anim);
    }
}

void FaceDisplay::LookAt(int32_t offset_x, uint32_t duration_ms) {
    gaze_offset_ = offset_x;
    lv_obj_t* eyes[] = {eye_left_, eye_right_};
    for (lv_obj_t* eye : eyes) {
        AnimateTo(eye, SetTranslateX, lv_obj_get_style_translate_x(eye, LV_PART_MAIN), offset_x,
                  duration_ms);
    }
}

void FaceDisplay::BlinkTimerCallback(lv_timer_t* timer) {
    // Runs inside the LVGL task, which already holds the lock.
    auto self = static_cast<FaceDisplay*>(lv_timer_get_user_data(timer));
    if (self->geometry_.blinks) {
        self->Blink();
    }
    lv_timer_set_period(timer, RandomRange(2200, 5000));
}

void FaceDisplay::GazeTimerCallback(lv_timer_t* timer) {
    auto self = static_cast<FaceDisplay*>(lv_timer_get_user_data(timer));

    // Both waiting states are derived, not reported by xiaozhi, so they need
    // their own way out if the server never answers.
    if ((self->state_ == FaceState::kHearing || self->state_ == FaceState::kThinking) &&
        lv_tick_elaps(self->waiting_since_ms_) > kWaitingTimeoutMs) {
        ESP_LOGW(TAG, "No answer within %" PRIu32 " ms, returning to idle", kWaitingTimeoutMs);
        self->SetFaceState(FaceState::kIdle);
        return;
    }

    if (self->geometry_.looks_around) {
        // Pick a different direction than the current one so every tick is visible.
        int32_t reach = self->state_ == FaceState::kThinking ? 14 : 22;
        const int32_t targets[] = {-reach, 0, reach};
        int32_t next = self->gaze_offset_;
        while (next == self->gaze_offset_) {
            next = targets[esp_random() % 3];
        }
        self->LookAt(next, kGazeTransitionMs);
    }

    lv_timer_set_period(timer, RandomRange(2800, 6000));
}

void FaceDisplay::SetStatus(const char* status) {
    if (status == nullptr) {
        return;
    }

    DisplayLockGuard lock(this);

    // xiaozhi drives the display through localized status strings; comparing
    // against the Lang::Strings constants keeps application.cc untouched.
    if (strcmp(status, Lang::Strings::LISTENING) == 0) {
        SetFaceState(FaceState::kListening);
    } else if (strcmp(status, Lang::Strings::CONNECTING) == 0) {
        SetFaceState(FaceState::kConnecting);
    } else if (strcmp(status, Lang::Strings::SPEAKING) == 0) {
        // NOT speaking yet. The server sends "tts start" immediately after the
        // transcript, long before the first audio chunk. Only sentence_start,
        // which arrives as SetChatMessage("assistant", ...), means sound.
        if (state_ != FaceState::kSpeaking) {
            SetFaceState(FaceState::kThinking);
        }
    } else if (strcmp(status, Lang::Strings::STANDBY) == 0) {
        // First standby means activation finished and the device is ready -
        // only now does the bottom row make sense at all.
        bool first = !ready_;
        ready_ = true;
        // Standby right after listening is not idle either - the recording
        // stopped and the server is transcribing.
        SetFaceState(state_ == FaceState::kListening ? FaceState::kHearing : FaceState::kIdle);
        if (first) {
            // SetFaceState returns early when the state does not change, and it
            // is precisely the first standby that may find the face already
            // idle. Without this the controls stayed hidden until the next
            // transition - which meant pressing BOOT once before they appeared.
            ESP_LOGI(TAG, "Device ready, revealing controls");
            RevealControls(kControlsIdleMs);
        }
    } else {
        // Boot and error messages have no face state. They go into the text
        // area, not to the bottom - the bottom belongs to the mute control.
        ShowNotificationLocked(status, kNotificationHoldMs);
    }
}

void FaceDisplay::SetChatMessage(const char* role, const char* content) {
    if (role == nullptr || content == nullptr || content[0] == '\0') {
        return;
    }

    DisplayLockGuard lock(this);

    if (strcmp(role, "user") == 0) {
        // "stt": the transcript is done and the LLM has just been handed the
        // text (server receiveAudioHandle.py:98). This is the real start of
        // thinking - roughly 30 s before anything is heard. Showing what was
        // understood makes that long wait informative instead of blank.
        ESP_LOGI(TAG, "Transcript received, LLM is working");
        SetFaceState(FaceState::kThinking);
        // The wait has no known end, so the transcript loops.
        ShowSubtitle(content, false, 0);
    } else if (strcmp(role, "assistant") == 0) {
        // Reached only if the server sends no duration_ms; SetSpokenSentence()
        // is the normal path for a spoken sentence.
        SetFaceState(FaceState::kSpeaking);
        ShowSubtitle(content, true, 0);
    }
}

void FaceDisplay::SetSpokenSentence(const char* text, uint32_t duration_ms) {
    if (text == nullptr || text[0] == '\0') {
        return;
    }
    DisplayLockGuard lock(this);
    // "sentence_start", sent right before the matching audio is streamed
    // (server sendAudioHandle.py:43-45). Sound starts now, and duration_ms says
    // for how long - which is exactly what the scroll needs.
    SetFaceState(FaceState::kSpeaking);
    ShowSubtitle(text, true, duration_ms);
}

void FaceDisplay::ShowNotification(const char* notification, int duration_ms) {
    if (notification == nullptr) {
        return;
    }
    DisplayLockGuard lock(this);
    ShowNotificationLocked(notification, duration_ms);
}

void FaceDisplay::ShowNotificationLocked(const char* text, int duration_ms) {
    // Before the device is ready these are boot messages - the network status,
    // "Chatti-DE - V 1.0.1". They belong at the bottom: while booting, the
    // text area starts at eye height (geometry table, subtitle_top 108 against
    // eyes centred at 142), so putting them there wrote them across the face.
    if (!ready_ && state_label_ != nullptr) {
        lv_label_set_text(state_label_, text);
        ApplyBottomRow();
        return;  // no self-clearing: the next message replaces this one, and
                 // becoming ready hands the slot to the controls anyway
    }

    // Once running, a notification is transient and belongs in the text area,
    // where it must clear itself again.
    if (subtitle_label_ == nullptr) {
        return;
    }
    subtitle_duration_ms_ = 0;
    lv_obj_set_style_text_color(subtitle_label_, lv_color_hex(kTranscriptColor), 0);
    lv_label_set_text(subtitle_label_, text);
    ApplyState();  // a notification is text: the eyes step aside for it too
    lv_timer_set_period(subtitle_clear_timer_, duration_ms > 0 ? duration_ms
                                                              : kNotificationHoldMs);
    lv_timer_reset(subtitle_clear_timer_);
    lv_timer_resume(subtitle_clear_timer_);
}

void FaceDisplay::SetTheme(Theme* theme) {
    DisplayLockGuard lock(this);

    // Keep current_theme_ in sync, but do not let LcdDisplay::SetTheme loose on
    // widgets this class never created.
    Display::SetTheme(theme);

    ApplyFont();
    // The face owns its palette; the theme only supplies the font.
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(kBackgroundColor), 0);
}

void FaceDisplay::SetEmotion(const char* emotion) {}

void FaceDisplay::ClearChatMessages() {}

void FaceDisplay::UpdateStatusBar(bool update_all) {}

void FaceDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image) {}

}  // namespace chatti
