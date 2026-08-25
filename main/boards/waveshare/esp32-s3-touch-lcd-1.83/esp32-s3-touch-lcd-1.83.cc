#include "wifi_board.h"
#include "display/lcd_display.h"
#include "chatti_face_display.h"
#include "codecs/box_audio_codec.h"
#include "application.h"
#include "button.h"
#include "led/single_led.h"
#include "mcp_server.h"
#include "config.h"
#include "power_save_timer.h"
#include "axp2101.h"
#include "i2c_device.h"

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#include <driver/i2c_master.h>
#include <driver/spi_master.h>
#include "settings.h"

#include <esp_lcd_touch_cst816s.h>
#include <esp_lvgl_port.h>
#include <lvgl.h>

#define TAG "WaveshareEsp32s3TouchLCD1inch83"

class Pmic : public Axp2101 {
public:
    Pmic(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : Axp2101(i2c_bus, addr) {
        WriteReg(0x22, 0b110); // PWRON > OFFLEVEL as POWEROFF Source enable
        WriteReg(0x27, 0x10);  // hold 4s to power off

        // Disable All DCs but DC1
        WriteReg(0x80, 0x01);
        // Disable All LDOs
        WriteReg(0x90, 0x00);
        WriteReg(0x91, 0x00);

        // Set DC1 to 3.3V
        WriteReg(0x82, (3300 - 1500) / 100);

        // Set ALDO1 to 3.3V
        WriteReg(0x92, (3300 - 500) / 100);

        // Enable ALDO1(MIC)
        WriteReg(0x90, 0x01);

        WriteReg(0x64, 0x02); // CV charger voltage setting to 4.1V

        WriteReg(0x61, 0x02); // set Main battery precharge current to 50mA
        WriteReg(0x62, 0x08); // set Main battery charger current to 400mA ( 0x08-200mA, 0x09-300mA, 0x0A-400mA )
        WriteReg(0x63, 0x01); // set Main battery term charge current to 25mA
    }
};

/*
 * chatti: mute silences the loudspeaker, nothing else.
 *
 * Muting must not stop the conversation - questions still go to the server and
 * the answer still appears on screen, it is simply not read out. Refusing to
 * enable the output achieves that: BoxAudioCodec::Write() is guarded by
 * output_enabled_ and returns the sample count regardless, so the decoder keeps
 * running and the subtitle timing is unaffected.
 *
 * Deliberately not SetOutputVolume(0): that persists to NVS, so a reboot while
 * muted would come back permanently silent with the button showing "Laut".
 */
class ChattiAudioCodec : public BoxAudioCodec {
public:
    using BoxAudioCodec::BoxAudioCodec;

    void SetMuted(bool muted) {
        muted_ = muted;
        if (muted_) {
            BoxAudioCodec::EnableOutput(false);  // silence mid-sentence too
        }
    }

    virtual void EnableOutput(bool enable) override {
        if (enable && muted_) {
            return;
        }
        BoxAudioCodec::EnableOutput(enable);
    }

private:
    bool muted_ = false;
};

class WaveshareEsp32s3TouchLCD1inch83 : public WifiBoard {
private:
    i2c_master_bus_handle_t i2c_bus_;
    Pmic* pmic_ = nullptr;
    Button boot_button_;
    Display* display_;
    PowerSaveTimer* power_save_timer_;
    bool muted_ = false;

    void InitializePowerSaveTimer() {
        // chatti: third argument is seconds_to_shutdown, -1 disables it.
        // Upstream powered the board off after 300 s, which is wrong for a
        // device that lives on the desk on permanent USB-C power. Dimming
        // after 60 s stays - it only lowers the backlight.
        power_save_timer_ = new PowerSaveTimer(-1, 60, -1);
        power_save_timer_->OnEnterSleepMode([this]() {
            GetDisplay()->SetPowerSaveMode(true);
            GetBacklight()->SetBrightness(20); });
        power_save_timer_->OnExitSleepMode([this]() {
            GetDisplay()->SetPowerSaveMode(false);
            GetBacklight()->RestoreBrightness(); });
        power_save_timer_->OnShutdownRequest([this](){ 
            pmic_->PowerOff(); });
        power_save_timer_->SetEnabled(true);
    }

    void InitializeCodecI2c() {
        // Initialize I2C peripheral
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = I2C_NUM_0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));
    }

    void InitializeAxp2101() {
        ESP_LOGI(TAG, "Init AXP2101");
        pmic_ = new Pmic(i2c_bus_, 0x34);
    }

    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_CLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH*  DISPLAY_HEIGHT*  sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeButtons() {
        // chatti: BOOT no longer talks. Tapping the face does that now (see
        // InitializeMuteControl), which is the gesture people reach for anyway
        // on a device whose whole front is a touchscreen.
        //
        // What is left here is the one thing the screen cannot offer: bringing
        // the two controls back. They retire after a few seconds so the face has
        // the screen to itself, and since the tap became the talk button there
        // was no way left to ask for them.
        boot_button_.OnClick([this]() {
            // Nothing in this board ever called WakeUp(), so once the 60 s dim
            // kicked in the display stayed dim for good. That went unnoticed
            // while the 300 s shutdown still cut things short.
            power_save_timer_->WakeUp();
            auto& app = Application::GetInstance();
            // Kept: while the device is still starting there is no conversation
            // to have and no control worth showing, but there may well be a
            // server it can never reach - and then this is the way back into
            // setup. The 4 s hold below covers the same need once it is up.
            if (app.GetDeviceState() == kDeviceStateStarting) {
                ESP_LOGI(TAG, "BOOT during startup: opening setup mode");
                EnterWifiConfigMode();
                return;
            }
            ESP_LOGI(TAG, "BOOT pressed: showing controls");
            static_cast<chatti::FaceDisplay*>(display_)->ShowControls();
        });

        // chatti: the upstream double-click toggled the AEC mode. Removed with
        // the talk handler - AEC is on for good on this board, the gesture was
        // undiscoverable, and while a double-click handler is registered the
        // button library has to wait out the double-click window before it can
        // report a single one. BOOT has to answer immediately now that its only
        // job is putting two icons on screen.

        // chatti: hold BOOT for 4 s to reopen the setup hotspot.
        //
        // A short click already does this while the device is still starting up
        // (see above), which covers "the server was never reachable". It does not
        // cover the common case: the device is up and idle, but the PC moved to a
        // different address. There was no way back into setup then short of
        // erasing NVS over USB.
        //
        // EnterWifiConfigMode() is upstream (wifi_board.cc:195) and already deals
        // with every state - it closes a running conversation gracefully before
        // switching. So this is only the missing trigger, not new machinery.
        boot_button_.OnLongPress([this]() {
            power_save_timer_->WakeUp();
            ESP_LOGI(TAG, "BOOT held: reopening setup mode");
            EnterWifiConfigMode();
        });

        // chatti: no power-off handler here on purpose.
        //
        // The AXP2101 does it in hardware: registers 0x22/0x27 above enable
        // "PWRON > OFFLEVEL as POWEROFF", so holding the PWR button switches
        // the board off without any firmware involvement. Verified on the
        // device. The PWR button cannot be read from software anyway - per the
        // schematic it only reaches the PMIC PWRON pin, and the GPIO 41 that
        // config.h calls PWR_BUTTON_GPIO carries SYS_OUT, a supply rail.
    }

    void InitializeMuteControl() {
        auto face = static_cast<chatti::FaceDisplay*>(display_);
        face->OnMuteToggled([this](bool muted) {
            muted_ = muted;
            // Output only. Recording, recognition and the answer text all keep
            // working - the answer is just not spoken.
            static_cast<ChattiAudioCodec*>(GetAudioCodec())->SetMuted(muted);
            ESP_LOGI(TAG, "Speaker %s", muted ? "muted" : "live");
        });
        // chatti: a tap on the face is the talk button. Press once to start
        // talking, tap again when finished.
        //
        // This handler used to live on BOOT. It moved here unchanged, and the
        // reasoning behind it is the part worth keeping: upstream routes a press
        // to ToggleChatState(), which picks the listening mode from
        // GetDefaultListeningMode() - and that returns kListeningModeRealtime
        // whenever AEC is on. Realtime means the device streams continuously
        // while the *server* decides where the sentence ends, so a manual stop
        // has no meaning and the device just sits in "listening". Worse, a second
        // press in that state calls CloseAudioChannel() and tears the WebSocket
        // down mid-sentence. StartListening() selects kListeningModeManualStop,
        // where the server waits for our explicit stop - real push-to-talk.
        //
        // The tap reaches us only when it missed the two controls; LVGL does not
        // bubble events, so changing a setting never starts a conversation.
        face->OnScreenTapped([this]() {
            // If the backlight has dimmed (PowerSaveTimer, 60 s) the tap has to
            // undim it too - only the board can, the timer is not visible from
            // the display. It wakes and acts on the same tap on purpose: someone
            // reaching for a dark screen wants to talk, not to admire it.
            power_save_timer_->WakeUp();
            auto& app = Application::GetInstance();
            auto state = app.GetDeviceState();
            ESP_LOGI(TAG, "Face tapped: device state=%d, muted=%d", (int)state, (int)muted_);
            if (state == kDeviceStateStarting) {
                return;  // nothing to talk to yet; BOOT opens setup instead
            }
            if (state == kDeviceStateListening) {
                app.StopListening();
                return;
            }
            if (state == kDeviceStateSpeaking) {
                app.AbortSpeaking(kAbortReasonNone);
                return;
            }
            app.StartListening();
        });
    }

    void InitializeDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        // 液晶屏控制IO初始化
        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = 0;
        io_config.pclk_hz = 24 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        // 初始化液晶屏驱动芯片
        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));
        esp_lcd_panel_reset(panel);
        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, true);
        // esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
        esp_lcd_panel_disp_on_off(panel, true);
        // chatti: robot face instead of the xiaozhi chat UI (see chatti_face_display.h)
        display_ = new chatti::FaceDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
    }

    void InitializeTouch() {
        esp_lcd_touch_handle_t tp;
        // Deliberately NOT DISPLAY_WIDTH/HEIGHT: the display is turned by a
        // quarter but the touch panel underneath it is not, so it still reports
        // raw portrait coordinates. Those raw limits are what the mirroring
        // below subtracts from - see the comment in config.h.
        esp_lcd_touch_config_t tp_cfg = {
            .x_max = TOUCH_RAW_X_MAX,
            .y_max = TOUCH_RAW_Y_MAX,
            .rst_gpio_num = GPIO_NUM_39,
            .int_gpio_num = GPIO_NUM_13,
            .levels = {
                .reset = 0,
                .interrupt = 0,
            },
            .flags = {
                .swap_xy = TOUCH_SWAP_XY,
                .mirror_x = TOUCH_MIRROR_X,
                .mirror_y = TOUCH_MIRROR_Y,
            },
        };
        esp_lcd_panel_io_handle_t tp_io_handle = NULL;
        esp_lcd_panel_io_i2c_config_t tp_io_config = {};
        tp_io_config.dev_addr = ESP_LCD_TOUCH_IO_I2C_CST816S_ADDRESS;
        tp_io_config.scl_speed_hz = 400 * 1000;
        tp_io_config.control_phase_bytes = 1;
        tp_io_config.dc_bit_offset = 0;
        tp_io_config.lcd_cmd_bits = 8;
        tp_io_config.lcd_param_bits = 0;
        tp_io_config.flags.disable_control_phase = 1;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(i2c_bus_, &tp_io_config, &tp_io_handle));
        ESP_LOGI(TAG, "Initialize touch controller");
        ESP_ERROR_CHECK(esp_lcd_touch_new_i2c_cst816s(tp_io_handle, &tp_cfg, &tp));
        const lvgl_port_touch_cfg_t touch_cfg = {
            .disp = lv_display_get_default(),
            .handle = tp,
        };
        lvgl_port_add_touch(&touch_cfg);
        ESP_LOGI(TAG, "Touch panel initialized successfully");
    }

    // 初始化工具
    void InitializeTools() {
        auto &mcp_server = McpServer::GetInstance();
        mcp_server.AddTool("self.system.reconfigure_wifi",
            "End this conversation and enter WiFi configuration mode.\n"
            "**CAUTION** You must ask the user to confirm this action.",
            PropertyList(), [this](const PropertyList& properties) {
                EnterWifiConfigMode();
                return true;
            });
    }

public:
    WaveshareEsp32s3TouchLCD1inch83()
        // chatti: 4 s instead of the iot_button default, so the setup mode below
        // cannot be opened by accident in the middle of a conversation.
        : boot_button_(BOOT_BUTTON_GPIO, false, 4000) {
        InitializePowerSaveTimer();
        InitializeCodecI2c();
        InitializeAxp2101();
        InitializeSpi();
        InitializeDisplay();
        InitializeTouch();
        InitializeButtons();
        InitializeMuteControl();
        InitializeTools();
        GetBacklight()->RestoreBrightness();
    }

    virtual AudioCodec* GetAudioCodec() override {
        static ChattiAudioCodec audio_codec(
            i2c_bus_, 
            AUDIO_INPUT_SAMPLE_RATE, 
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK, 
            AUDIO_I2S_GPIO_BCLK, 
            AUDIO_I2S_GPIO_WS, 
            AUDIO_I2S_GPIO_DOUT, 
            AUDIO_I2S_GPIO_DIN,
            AUDIO_CODEC_PA_PIN, 
            AUDIO_CODEC_ES8311_ADDR, 
            AUDIO_CODEC_ES7210_ADDR, 
            AUDIO_INPUT_REFERENCE);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }

    virtual Backlight* GetBacklight() override {
        static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }

    virtual bool GetBatteryLevel(int &level, bool &charging, bool &discharging) override {
        static bool last_discharging = false;
        charging = pmic_->IsCharging();
        discharging = pmic_->IsDischarging();
        if (discharging != last_discharging)
        {
            power_save_timer_->SetEnabled(discharging);
            last_discharging = discharging;
        }

        level = pmic_->GetBatteryLevel();
        return true;
    }

    virtual void SetPowerSaveLevel(PowerSaveLevel level) override {
        if (level != PowerSaveLevel::LOW_POWER) {
            power_save_timer_->WakeUp();
        }
        // chatti: keep the radio reachable while idle.
        //
        // LOW_POWER ends up as esp_wifi_set_ps(WIFI_PS_MAX_MODEM) in
        // managed_components/78__esp-wifi-connect/wifi_station.cc:313, and the
        // station then sleeps through the broadcast ARP requests it would have
        // to answer first. Measured 2026-08-16 with the display on and the eyes
        // moving: 0 of 20 pings answered and the host's ARP entry aged out, so
        // nothing on the LAN could tell "idle" from "switched off" - which is
        // exactly what the desk panel needs to know.
        //
        // BALANCED (WIFI_PS_MIN_MODEM) wakes at every DTIM beacon instead;
        // measured round trips of 164-362 ms in that state, reliably answered.
        // The saving it gives up is meaningless here: this board has no battery
        // and lives on permanent USB-C power (decision 9). Display dimming is a
        // separate mechanism and stays as it is.
        //
        // 2026-08-16, second round: BALANCED was still not enough. Two failures
        // traced back to the same cause, and both are about *broadcast* frames,
        // which an access point only sends right after a DTIM beacon - a station
        // that dozes between beacons drops them whenever the link is marginal:
        //
        //   * No IP address. The device associated at -78 dBm and then sat there
        //     for 50 s with no `sta ip:` line at all. A DHCP OFFER is broadcast.
        //   * No server found. Our announcement (chatti/control/app/announce.py)
        //     is multicast, and the whole point of it is that the device hears it
        //     without asking.
        //
        // So the radio simply stays awake. There is nothing to save here, and
        // every remaining discovery mechanism depends on hearing broadcasts.
        WifiBoard::SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    }
};

DECLARE_BOARD(WaveshareEsp32s3TouchLCD1inch83);
