# Chameleon with Album Art

A Home Assistant custom integration that applies colors from images or a media player's current album artwork to RGB lights. This project is based on [Chameleon by MKSG-MugunthKumar](https://github.com/MKSG-MugunthKumar/ha-chameleon).

## Features

- Extract a palette from images in `/config/www/chameleon/` and distribute it across selected lights.
- Select **Album Art** as the Chameleon light effect to use the configured media player's current artwork.
- Refresh album art colors when the media player's artwork changes while that effect is active.
- Choose static colors or animated transitions with the companion transition controls.
- Enable **Normalize Brightness** in Chameleon's Configure dialog to turn extracted image colors into bright, vibrant LED colors without changing the light brightness setting.
- Use the **Randomize Color Assignment** switch on the Chameleon device to shuffle which light receives each palette color whenever **Random** is selected. Named scenes and Album Art keep their configured light order.

Album artwork is kept in memory and is limited to 10 MB per download. If artwork is unavailable, the last successfully applied light colors remain in place and the Chameleon light reports `last_error`.

Normalize Brightness is off by default for existing setups. When enabled, each extracted RGB color keeps its hue, reaches full RGB value, and gains a saturation floor if it is muted. Neutral black and gray become white because they have no meaningful hue. The same adjustment is applied to colors between animation steps. Manual colors are left as chosen. Different LED models and hues can still appear to have different physical brightness; exact visual matching requires calibration for each light.

All RGB channels are clamped to integer values from 0 through 255 before sending a light command, including animation and manual-color commands. This prevents out-of-range RGB service data. A bulb's narrower physical color gamut can still cause it to approximate a requested color.

Randomize Color Assignment is a switch in the Chameleon device's Controls section. Existing setups keep their current on/off value when updating. New setups start with it off. With it on, each Random selection chooses an image and a new light-to-palette assignment. Static scenes assign one palette color per light; animated scenes use that shuffled light order for their starting colors. Reapplying the selected scene due to a transition/style change keeps the current assignment until Random is selected again.

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

A transition of `0` applies a static palette. A value above `0` animates through the palette. The Chameleon light supports native Home Assistant brightness and effect controls; `number.chameleon_*_transition` and `select.chameleon_*_transition_style` control animation.

The Album Art effect reads the selected media player's `entity_picture`. It supports HTTP(S) artwork URLs and Home Assistant relative URLs. It applies colors in the configured light order and updates when `entity_picture` changes. The effect does not react to audio beats or waveform data.

## Development

The repository root contains `hacs.json` and one integration under `custom_components/chameleon`, following [HACS's integration repository layout](https://www.hacs.xyz/docs/publish/integration/). Tests are in `tests/`. Run them with a Python environment containing the `test` dependencies from `pyproject.toml`:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

## Credits

Based on [upstream Chameleon](https://github.com/MKSG-MugunthKumar/ha-chameleon) by MKSG-MugunthKumar. Album art support and this standalone repository layout were developed for this fork.
