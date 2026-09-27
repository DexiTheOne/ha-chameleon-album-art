# Chameleon with Album Art

A Home Assistant custom integration that applies colors from images or a media player's current album artwork to RGB lights. This project is based on [Chameleon by MKSG-MugunthKumar](https://github.com/MKSG-MugunthKumar/ha-chameleon).

## Features

- Extract a palette from images in `/config/www/chameleon/` and distribute it across selected lights.
- Select **Album Art** as the Chameleon light effect to use the configured media player's current artwork.
- Refresh album art colors when the media player's artwork changes while that effect is active.
- Choose colors and native WLED transitions with the companion controls.
- WLED devices use their native transition style and the Transition slider duration for scene, Album Art, manual color, and power changes. The WLED Transition Style control offers Fade, Fairy Dust, Swipe Right/Left, Push Right/Left, Outside In, and Inside Out. One-LED segments use Fade. Non-WLED lights receive new colors immediately. A duration of 0 seconds is sent to WLED as 0.
- The transition duration and WLED style persist across Home Assistant restarts. Repeated Random selections choose a different scene when more than one is available.
- **Only Interesting Colors** ranks saturated colors first, then genuine dark/muted colors and white, then skin-like tones, with gray as a last fallback. Within each tier, colors covering more of the artwork rank higher. It selects the strongest available tier instead of permanently excluding dark or skin-like colors. When that tier contains visible chromatic colors, near-black shadows are omitted unless they cover at least 15% of the image, so substantial dark backgrounds remain eligible; dark-only artwork keeps its colors. With **Coverage Based Assignment** enabled, selected colors receive light slots proportionally to their measured coverage, rounded to whole lights. Tiny details are not guaranteed a light, and random assignment changes which lights get those slots, not their proportions. When white covers at least 70% of the image, white receives priority and most lights stay white with up to two high-ranked accent lights. Entirely black artwork leaves the lights unchanged. Skin-like classification is a color heuristic, not face detection.
- Enable **Normalize Brightness** in Chameleon's Configure dialog to brighten dim image colors without boosting saturation or changing the light brightness setting.
- **Coverage Based Assignment** is a separate switch that controls color counts, using measured image area. It starts off to preserve existing assignment behavior. It works independently of Only Interesting Colors; changes apply on the next artwork/image scene update.
- Use the **Randomize Color Assignment** switch on the Chameleon device to shuffle which light receives each palette color whenever **Random** is selected or valid Album Art is updated. Named image scenes keep their configured light order.
- Turn on **Send Palette to WLED** in Configure to fill all three color slots on every segment of a configured WLED device with colors from the current image. Secondary slots prefer hues at least 20 degrees apart from the primary, with neutral shades treated as a separate family. With two hue families, the contrasting secondary repeats in slot three; single-family images repeat source colors. Weighted light counts do not force duplicate hues into secondary slots. Chameleon never invents new hues or leaves old colors there. It sends these colors only when every segment already uses a color-slot palette with a palette-aware effect, preserving the selected effect and palette. Otherwise the light receives its ordinary Chameleon color. The switch starts off and does not affect non-WLED lights or manual colors.

Album artwork is streamed into anonymous temporary storage and decoded in a worker before color extraction. There is no fixed download-size cutoff; covers are reduced to at most 1024 pixels per side. Generic or incorrect MIME headers are accepted when the content decodes as an image. Only Interesting Colors ranks useful hues and image coverage before normalization. Brightness normalization lifts dim swatches to an 80% RGB output level while preserving saturation and leaving brighter colors at their original level. Nearly neutral swatches stay neutral rather than amplifying compression noise into artificial hues. PNG, JPEG, WebP, GIF, BMP and TIFF are supported by Pillow; animated images use the first frame. Temporary downloads are closed after processing. Decoder resource protections, available disk space and a 60-second download timeout still apply. Album Art stays selected until you choose another effect, a manual color, or turn the light off. A new cover transitions to its palette over the selected WLED transition time and then holds those colors. If artwork is unavailable or the player exposes a recognized placeholder, the existing colors remain in place and the Chameleon light reports `last_error`; a later artwork change retries automatically. Chameleon accepts the artwork exposed by the configured media player, including Eversolo's Squeeze Connect artwork. Random selects image scenes only.

Normalize Brightness is off by default for existing setups. When enabled, chromatic colors keep their hue and saturation. Only colors below 80% RGB value are brightened to that level; brighter colors keep their original level. Nearly neutral shades remain gray at their original level or the same minimum, rather than becoming fully bright white. Manual colors are left as chosen. Different LED models and hues can still appear to have different physical brightness; exact visual matching requires calibration for each light.

All RGB channels are clamped to integer values from 0 through 255 before sending a light command, including manual-color commands. This prevents out-of-range RGB service data. A bulb's narrower physical color gamut can still cause it to approximate a requested color.

Randomize Color Assignment is a switch in the Chameleon device's Controls section. Existing setups keep their current on/off value when updating. New setups start with it off. With it on, each Random selection chooses an image and a new light-to-palette assignment, and each valid Album Art update chooses a new assignment. Scenes assign one palette color per light. Reapplying the selected scene due to a transition/style change keeps the current assignment. Unavailable or default artwork does not reshuffle the lights.

## Install with HACS

This repository is intended to be added to HACS as a **custom Integration repository** after it is published:

1. Remove the existing HACS Chameleon repository entry or download so only one source manages `custom_components/chameleon`. Keep the Chameleon integration configuration entry until the replacement is installed.
2. In HACS, open **Custom repositories** and add this repository's URL as an **Integration**.
3. Download Chameleon from HACS and restart Home Assistant.
4. In **Settings → Devices & services → Chameleon**, open **Configure** and select an **Album Art Media Player**.
5. Turn on the Chameleon light and choose its **Album Art** effect. Change tracks to verify that the light colors update.

HACS installs `custom_components/chameleon` directly from this repository; no file copying, extra package, or separate card is required. The existing configuration entry should remain available because the integration domain is still `chameleon`. Back up Home Assistant before switching sources, and check the configuration entry and entities after restart.

For manual installation, copy `custom_components/chameleon` into `/config/custom_components/` and restart Home Assistant.

## Use

Add scenes by placing `.jpg`, `.jpeg`, or `.png` files in `/config/www/chameleon/`. Call `chameleon.refresh_scenes` after changing files. The Chameleon light's effect list contains these scenes, **Random**, and **Album Art** when a media player is configured.

The **Animation** switch controls continuous palette cycling independently of transition time. With Animation off, scene and album-art changes fade to their new palette over the configured transition and then stay static. A transition of `0` applies immediately and disables continuous cycling until raised again. The Chameleon light supports native Home Assistant brightness and effect controls; `number.chameleon_*_transition` sets fade duration and `select.chameleon_*_transition_style` sets the cycling style.

The Album Art effect reads the selected media player's `entity_picture`. It supports HTTP(S) artwork URLs and Home Assistant relative URLs. It applies colors in the configured light order and updates when `entity_picture` or track metadata changes. The effect does not react to audio beats or waveform data.

## Development

The repository root contains `hacs.json` and one integration under `custom_components/chameleon`, following [HACS's integration repository layout](https://www.hacs.xyz/docs/publish/integration/). Tests are in `tests/`. Run them with a Python environment containing the `test` dependencies from `pyproject.toml`:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

## Credits

Based on [upstream Chameleon](https://github.com/MKSG-MugunthKumar/ha-chameleon) by MKSG-MugunthKumar. Album art support and this standalone repository layout were developed for this fork.

### Scene controls

Each Chameleon device provides **Choose Random Scene** and **Scene** in its Controls section.

- **Choose Random Scene** turns on the configured lights and applies a random image palette. Pressing it again chooses a different image when at least two scenes are available. It preserves the current brightness and transition settings and uses Chameleon's WLED main-light handling. With only one image, that scene is reused; with no images, the button is unavailable.
- **Scene** mirrors the Chameleon light's effect list and current effect. Selecting an option applies it through the light's normal turn-on path. Random resolves to the actual chosen image name; the selected scene stays visible while the light is off or showing a manual color, and a bare turn-on restores that scene. After a restart, the dropdown defaults to Random until a scene is chosen; this does not turn the lights on. Image refreshes update the dropdown automatically.

For the Common Area setup, the default entity IDs are `button.chameleon_common_area_choose_random_scene` and `select.chameleon_common_area_scene`. Dashboard cards and automation actions can use these in place of `script.random_colors` and the template scene select. Home Assistant may adjust an ID if it is already occupied. Existing helpers and their consumers can be migrated after installing the update.

### Light Entities

The Chameleon device includes a toggle and brightness multiplier slider for each
configured light in its Configuration panel. Both controls use only the light’s name. WLED
master lights are omitted; configured individual segments have their own controls.
Turning a light’s toggle off immediately turns the light off and excludes it from later
Chameleon scene, palette, brightness, and power commands. Turning it back on
restores participation in the active group scene. These settings survive restarts.

Brightness is a 0–100% multiplier of group brightness: a group at 80% and a light
at 50% produce 40% brightness. Defaults are on and 100%. A 0% multiplier
keeps the light dark while leaving it enabled for palette updates.

### Transition queue and Random WLED style

Lighting moves share a queue of up to ten requests, including the active
transition. Random-scene button presses and manual scene selections run in
arrival order after the previous transition duration finishes. Further explicit
requests are discarded while the queue is full. Album-art changes retain only
the newest waiting cover, leaving the current transition uninterrupted.
Unused covers are not downloaded. Downloaded artwork is released after palette
and optional white-coverage extraction; palette data remains available.

The WLED Transition Style select also offers **Random**, which samples one of
the seven non-fade styles for each palette update. Shutdown reuses that choice.
The existing device-wide Fade guard remains for WLED devices containing a
single-LED segment. Other lights and WLED fallback commands use the configured
transition duration where supported. Queues are cleared on integration unload.
