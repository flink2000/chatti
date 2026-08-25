#ifndef CHATTI_FACE_DISPLAY_H
#define CHATTI_FACE_DISPLAY_H

#include "display/lcd_display.h"

#include <lvgl.h>

#include <functional>

namespace chatti {

/*
 * Visible states of the robot face.
 *
 * xiaozhi has no state for waiting on the server, and its device state is
 * actively misleading here: the server sends "stt" and "tts start" back to back
 * (server sendAudioHandle.py:339-343) the moment the transcript is ready, so the
 * device enters kDeviceStateSpeaking roughly 30 s before a single sample is
 * played. FaceDisplay therefore ignores that state and reads the server's own
 * messages, which arrive as SetChatMessage() calls:
 *
 *   stop listening       -> kHearing   ASR is running        (~13-25 s)
 *   "stt"      = user    -> kThinking  LLM + TTS are running (~30 s)
 *   "sentence_start"     -> kSpeaking  audio starts NOW
 *     = assistant
 *   "tts stop"           -> kIdle
 */
enum class FaceState {
    kBooting,
    kIdle,
    kConnecting,
    kListening,
    kHearing,
    kThinking,
    kSpeaking,
};

/*
 * Replaces the xiaozhi chat UI with two procedurally animated robot eyes.
 *
 * Everything is plain LVGL objects driven by lv_anim - no sprite frames, so the
 * face stays cheap in RAM and can react to state changes at any moment.
 */
class FaceDisplay : public SpiLcdDisplay {
public:
    FaceDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel, int width,
                int height, int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                bool swap_xy);
    virtual ~FaceDisplay();

    virtual void SetupUI() override;
    virtual void SetStatus(const char* status) override;
    virtual void SetTheme(Theme* theme) override;
    using LvglDisplay::ShowNotification;  // keep the std::string overload visible
    virtual void ShowNotification(const char* notification, int duration_ms = 3000) override;

    // Mute control. The board owns what muting actually does; the display only
    // owns the button and its appearance.
    void OnMuteToggled(std::function<void(bool muted)> callback);
    bool muted() const { return muted_; }

    // A tap on the face itself - anywhere that is not one of the two controls.
    // The display owns none of what this means: the board wakes the backlight
    // and starts or ends the conversation, neither of which it can see from here.
    void OnScreenTapped(std::function<void()> callback);

    // Bring the two controls back for a moment. Since the tap became the talk
    // button this is the only way to ask for them, and the board hangs it on
    // BOOT - which now does nothing else.
    void ShowControls();

    // Not chat bubbles here - these carry the server's progress signals.
    virtual void SetChatMessage(const char* role, const char* content) override;
    virtual void SetSpokenSentence(const char* text, uint32_t duration_ms) override;

    // The chat UI is gone, so everything that would drive it becomes a no-op.
    virtual void SetEmotion(const char* emotion) override;
    virtual void ClearChatMessages() override;
    virtual void UpdateStatusBar(bool update_all = false) override;
    virtual void SetPreviewImage(std::unique_ptr<LvglImage> image) override;

private:
    struct EyeGeometry {
        int32_t width;
        int32_t height;
        int32_t offset_y;  // applied as translate_y, so gaze can own translate_x
        bool blinks;
        bool looks_around;
    };

    static const EyeGeometry& GeometryFor(FaceState state);
    static const char* LabelFor(FaceState state);

    void SetFaceState(FaceState state);
    void ApplyState();  // must be called with the LVGL lock held
    bool HasVisibleSubtitle() const;  // text on screen -> the eyes step aside
    void ApplyFont();   // rebinds the theme font; see SetTheme()
    void ShowSubtitle(const char* text, bool spoken, uint32_t duration_ms);
    void UpdateSubtitleScroll();  // restarts the scroll if the text overflows
    void ShowNotificationLocked(const char* text, int duration_ms);
    void ApplyMuteAppearance();
    void ApplySubtitleAppearance();
    void ApplyBottomRow();   // controls when idle, state text while busy
    void RevealControls(uint32_t visible_ms);  // show them, restart their window
    static void MuteClickedCallback(lv_event_t* event);
    static void SubtitlesClickedCallback(lv_event_t* event);
    static void ScreenTappedCallback(lv_event_t* event);
    static void ControlsHideCallback(lv_timer_t* timer);
    void Blink();
    void LookAt(int32_t offset_x, uint32_t duration_ms);

    static void BlinkTimerCallback(lv_timer_t* timer);
    static void GazeTimerCallback(lv_timer_t* timer);
    static void SubtitleClearCallback(lv_timer_t* timer);

    lv_obj_t* eye_left_ = nullptr;
    lv_obj_t* eye_right_ = nullptr;
    lv_obj_t* subtitle_area_ = nullptr;  // clips the text; never overlaps the controls
    lv_obj_t* subtitle_label_ = nullptr;
    lv_obj_t* controls_row_ = nullptr;   // holds both buttons, bottom left
    lv_obj_t* mute_button_ = nullptr;
    lv_obj_t* mute_icon_ = nullptr;
    lv_obj_t* subtitle_button_ = nullptr;
    lv_obj_t* subtitle_icon_ = nullptr;
    lv_obj_t* subtitle_slash_ = nullptr;  // drawn; the font has no "off" glyph
    lv_obj_t* state_label_ = nullptr;

    lv_timer_t* blink_timer_ = nullptr;
    lv_timer_t* gaze_timer_ = nullptr;
    lv_timer_t* subtitle_clear_timer_ = nullptr;
    lv_timer_t* controls_timer_ = nullptr;

    FaceState state_ = FaceState::kBooting;
    EyeGeometry geometry_{};
    int32_t gaze_offset_ = 0;
    uint32_t waiting_since_ms_ = 0;
    // Speaking length of the sentence on screen, 0 when unknown. Drives the
    // scroll pace so the text follows the voice instead of a fixed timer.
    uint32_t subtitle_duration_ms_ = 0;
    // Set while the last sentence is still being read: the eyes must not grow
    // back over the text before it disappears.
    bool apply_state_deferred_ = false;
    bool muted_ = false;
    // Whether question and answer are written on the screen. On by default, and
    // forced back on whenever the speaker is muted - a silent device with no
    // text would say nothing at all.
    bool subtitles_enabled_ = true;
    // Whether the controls' visibility window is still open. They are settings,
    // not part of the conversation, so they retire after a while and come back
    // on a tap or when a new turn is ready to start.
    bool controls_visible_ = true;
    // False until the device has finished connecting, checking for updates and
    // activating. Nothing belongs in the bottom row before that.
    bool ready_ = false;
    std::function<void(bool)> on_mute_toggled_;
    std::function<void()> on_screen_tapped_;
};

}  // namespace chatti

#endif  // CHATTI_FACE_DISPLAY_H
