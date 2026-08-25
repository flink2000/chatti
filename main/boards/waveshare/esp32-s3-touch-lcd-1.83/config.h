#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>
#include <driver/spi_master.h>

#define AUDIO_INPUT_SAMPLE_RATE 24000
#define AUDIO_OUTPUT_SAMPLE_RATE 24000

#define AUDIO_INPUT_REFERENCE    true

#define AUDIO_I2S_GPIO_MCLK GPIO_NUM_16
#define AUDIO_I2S_GPIO_WS GPIO_NUM_45
#define AUDIO_I2S_GPIO_BCLK GPIO_NUM_9
#define AUDIO_I2S_GPIO_DIN GPIO_NUM_10
#define AUDIO_I2S_GPIO_DOUT GPIO_NUM_8

#define AUDIO_CODEC_PA_PIN      GPIO_NUM_46
#define AUDIO_CODEC_I2C_SDA_PIN GPIO_NUM_15
#define AUDIO_CODEC_I2C_SCL_PIN GPIO_NUM_14
#define AUDIO_CODEC_ES8311_ADDR ES8311_CODEC_DEFAULT_ADDR
#define AUDIO_CODEC_ES7210_ADDR  ES7210_CODEC_DEFAULT_ADDR

#define BOOT_BUTTON_GPIO        GPIO_NUM_0
#define PWR_BUTTON_GPIO         GPIO_NUM_41

#define DISPLAY_SPI_MODE        3
#define DISPLAY_CS_PIN          GPIO_NUM_5
#define DISPLAY_MOSI_PIN        GPIO_NUM_7
#define DISPLAY_MISO_PIN        GPIO_NUM_NC
#define DISPLAY_CLK_PIN         GPIO_NUM_6
#define DISPLAY_DC_PIN          GPIO_NUM_4
#define DISPLAY_RST_PIN         GPIO_NUM_38

// The panel itself is portrait, 240 x 284. chatti is used lying on its side,
// so the picture is turned 90 degrees counter-clockwise and everything above
// the driver - LVGL, the face, the touch - works in a 284 x 240 landscape frame.
//
// How the numbers below come about, because they are not obvious:
//
//   The ST7789 has a 240 x 320 frame memory of which this panel shows
//   240 columns x 284 rows, starting at row 0 - which is why the untouched
//   portrait build needs no offset at all.
//
//   swap_xy (MADCTL MV) maps the UI x axis onto the panel's row axis and UI y
//   onto its column axis. Precedent for that mapping in this repo:
//   boards/surfer-c3-1.14tft/config.h, a 135 x 240 panel whose landscape gaps
//   are likewise the portrait ones swapped.
//
//   mirror_y (MADCTL MY) reverses the row axis, so row address 0 now addresses
//   the far end of the 320-row memory and the visible window moves to rows
//   36..319. Hence OFFSET_X 36 - it is an x offset because after the swap the
//   UI x axis IS the row axis. Without it the picture sits 36 px off and the
//   last 36 px are never drawn.
//
// To turn the other way (clockwise) instead, three values change together:
// MIRROR_X true, MIRROR_Y false, OFFSET_X 0 - and the touch flags further down
// have to follow, or the screen turns and the finger does not.
#define DISPLAY_WIDTH           284
#define DISPLAY_HEIGHT          240
#define DISPLAY_MIRROR_X        false
#define DISPLAY_MIRROR_Y        true
#define DISPLAY_SWAP_XY         true

#define DISPLAY_OFFSET_X        36
#define DISPLAY_OFFSET_Y        0

// The touch panel is glued to the unturned display, so it keeps reporting
// portrait coordinates and has to be turned separately.
//
// Two traps here. First, esp_lcd_touch mirrors BEFORE it swaps
// (managed_components/espressif__esp_lcd_touch/esp_lcd_touch.c:88-106), and it
// mirrors against these maxima - so they must stay the RAW portrait limits and
// must not be swapped along with the display. Second, they are inclusive
// maxima, not sizes.
#define TOUCH_RAW_X_MAX         239
#define TOUCH_RAW_Y_MAX         283
#define TOUCH_SWAP_XY           1
#define TOUCH_MIRROR_X          0
#define TOUCH_MIRROR_Y          1

#define DISPLAY_BACKLIGHT_PIN   GPIO_NUM_40
#define DISPLAY_BACKLIGHT_OUTPUT_INVERT false

#endif // _BOARD_CONFIG_H_
