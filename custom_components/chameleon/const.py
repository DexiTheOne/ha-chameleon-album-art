"""Constants for the Chameleon integration."""

from typing import Final

# Integration domain
DOMAIN: Final = "chameleon"

# Default values
DEFAULT_NAME: Final = "Chameleon"
DEFAULT_TRANSITION: Final = 5  # default fade duration per color, seconds

# Image directory (hardcoded per design decision)
IMAGE_DIRECTORY: Final = "/config/www/chameleon"

# Supported image extensions
SUPPORTED_EXTENSIONS: Final = (".jpg", ".jpeg", ".png")

# Configuration keys
CONF_LIGHT_ENTITY: Final = "light_entity"  # Deprecated, kept for migration
CONF_LIGHT_ENTITIES: Final = "light_entities"  # New: list of light entities
CONF_MEDIA_PLAYER_ENTITY: Final = "media_player_entity"
CONF_NORMALIZE_BRIGHTNESS: Final = "normalize_brightness"
DEFAULT_NORMALIZE_BRIGHTNESS: Final = False
CONF_INTERESTING_COLORS: Final = "interesting_colors"
DEFAULT_INTERESTING_COLORS: Final = False
CONF_SEND_PALETTE_TO_WLED: Final = "send_palette_to_wled"
DEFAULT_SEND_PALETTE_TO_WLED: Final = False
CONF_RANDOMIZE_COLOR_ASSIGNMENT: Final = "randomize_color_assignment"
DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT: Final = False
CONF_TRANSITION: Final = "transition"
CONF_TRANSITION_STYLE: Final = "transition_style"
CONF_WLED_BLEND_STYLE: Final = "wled_blend_style"
CONF_ANIMATION_ENABLED: Final = "animation_enabled"
DEFAULT_ANIMATION_ENABLED: Final = True

# Light hosts scene effects, select the transition style, number the transition
# duration, and switches animation and Random-scene color assignment.
PLATFORMS: Final = ["light", "select", "number", "switch"]

# Services
SERVICE_APPLY_SCENE: Final = "apply_scene"
SERVICE_REFRESH_SCENES: Final = "refresh_scenes"

# Attributes
ATTR_SCENE_NAME: Final = "scene_name"
ATTR_MODE: Final = "mode"

# Special scene options
SCENE_OFF: Final = "Off"  # Turn off all lights
SCENE_RANDOM: Final = "Random"  # Pick a random scene
SCENE_ALBUM_ART: Final = "Album Art"  # Live palette from a configured media player

# Album-art downloads are bounded so a broken or hostile URL cannot consume
# unbounded memory inside Home Assistant.
MAX_ALBUM_ART_BYTES: Final = 10 * 1024 * 1024

# Color extraction
DEFAULT_COLOR_COUNT: Final = 8  # Number of colors to extract for palette
DEFAULT_QUALITY: Final = 10  # Color extraction quality (1 = highest, 10 = fastest)

# Animation transition
# A "transition" is the seconds-long fade duration between successive scene
# colors. Transition of 0 applies a scene immediately and stops the
# animation loop. Range chosen for usable slider precision: 100 positions over
# 0-10s with 0.1s step. 10s is enough for most ambient-lighting use cases.
MIN_TRANSITION: Final = 0
MAX_TRANSITION: Final = 10

# Static apply transition: the per-call transition seconds we use for one-shot
# scene application (when transition = 0). Kept short for snappy feel.
STATIC_TRANSITION_TIME: Final = 0.1

# Transition styles (used by ChameleonTransitionStyleSelect)
TRANSITION_STYLE_SYNC: Final = "synchronized"
TRANSITION_STYLE_STAGGERED: Final = "staggered"
TRANSITION_STYLE_WLED: Final = "wled"
TRANSITION_STYLES: Final = [TRANSITION_STYLE_SYNC, TRANSITION_STYLE_STAGGERED, TRANSITION_STYLE_WLED]
DEFAULT_TRANSITION_STYLE: Final = TRANSITION_STYLE_STAGGERED  # More natural-looking by default
WLED_BLEND_STYLES: Final = {
    "fade": 0,
    "fairy_dust": 1,
    "swipe_right": 2,
    "swipe_left": 3,
    "push_right": 16,
    "push_left": 17,
    "outside_in": 4,
    "inside_out": 5,
}
DEFAULT_WLED_BLEND_STYLE: Final = "outside_in"

# Brightness
# Brightness of 0 means "off" — equivalent to selecting the Off scene.
DEFAULT_BRIGHTNESS: Final = 100
MIN_BRIGHTNESS: Final = 0
MAX_BRIGHTNESS: Final = 100
