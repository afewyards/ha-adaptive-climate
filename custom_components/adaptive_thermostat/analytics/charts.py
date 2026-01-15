"""Chart generation for weekly reports using Pillow.

Generates PNG chart images for attachment to notifications.
"""
from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Chart dimensions
DEFAULT_WIDTH = 600
DEFAULT_HEIGHT = 300

# Colors
COLOR_BACKGROUND = (255, 255, 255)  # White
COLOR_TEXT = (51, 51, 51)  # Dark gray
COLOR_BAR_PRIMARY = (66, 133, 244)  # Google blue
COLOR_BAR_SECONDARY = (219, 68, 55)  # Google red
COLOR_BAR_SUCCESS = (15, 157, 88)  # Google green
COLOR_BAR_WARNING = (244, 180, 0)  # Google yellow
COLOR_GRID = (224, 224, 224)  # Light gray

# Comfort score color thresholds
COMFORT_GOOD = 80  # Green if >= 80
COMFORT_OK = 60  # Yellow if >= 60, else red


def _get_comfort_color(score: float) -> tuple[int, int, int]:
    """Get color for comfort score.

    Args:
        score: Comfort score 0-100

    Returns:
        RGB color tuple
    """
    if score >= COMFORT_GOOD:
        return COLOR_BAR_SUCCESS
    elif score >= COMFORT_OK:
        return COLOR_BAR_WARNING
    else:
        return COLOR_BAR_SECONDARY


class ChartGenerator:
    """Generate PNG charts using Pillow."""

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> None:
        """Initialize the chart generator.

        Args:
            width: Chart width in pixels
            height: Chart height in pixels
        """
        self.width = width
        self.height = height
        self._pillow_available = self._check_pillow()

    def _check_pillow(self) -> bool:
        """Check if Pillow is available.

        Returns:
            True if Pillow can be imported
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
            return True
        except ImportError:
            _LOGGER.warning("Pillow not available, chart generation disabled")
            return False

    @property
    def available(self) -> bool:
        """Check if chart generation is available."""
        return self._pillow_available

    def create_bar_chart(
        self,
        data: dict[str, float],
        title: str,
        unit: str = "%",
        max_value: float | None = None,
    ) -> bytes | None:
        """Create a horizontal bar chart.

        Args:
            data: Dictionary of label -> value
            title: Chart title
            unit: Unit for values (e.g., "%", "kWh")
            max_value: Maximum value for scale (auto if None)

        Returns:
            PNG image as bytes, or None if generation failed
        """
        if not self._pillow_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        if not data:
            return None

        # Create image
        img = Image.new("RGB", (self.width, self.height), COLOR_BACKGROUND)
        draw = ImageDraw.Draw(img)

        # Try to load a font, fall back to default
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except (OSError, IOError):
            font_title = ImageFont.load_default()
            font_label = ImageFont.load_default()

        # Layout
        margin = 20
        title_height = 30
        bar_height = 25
        bar_spacing = 10
        label_width = 120
        value_width = 60

        # Draw title
        draw.text((margin, margin), title, fill=COLOR_TEXT, font=font_title)

        # Calculate bar area
        bar_area_top = margin + title_height
        bar_area_left = margin + label_width
        bar_area_width = self.width - margin * 2 - label_width - value_width

        # Determine scale
        if max_value is None:
            max_value = max(data.values()) if data.values() else 100
        if max_value <= 0:
            max_value = 100

        # Draw bars
        y = bar_area_top
        for label, value in data.items():
            # Truncate label if too long
            display_label = label[:15] + "..." if len(label) > 15 else label

            # Draw label
            draw.text((margin, y + 5), display_label, fill=COLOR_TEXT, font=font_label)

            # Calculate bar width
            bar_width = int((value / max_value) * bar_area_width)
            bar_width = max(0, min(bar_width, bar_area_width))

            # Draw bar
            bar_color = COLOR_BAR_PRIMARY
            draw.rectangle(
                [bar_area_left, y, bar_area_left + bar_width, y + bar_height],
                fill=bar_color,
            )

            # Draw value
            value_text = f"{value:.1f}{unit}"
            draw.text(
                (bar_area_left + bar_area_width + 10, y + 5),
                value_text,
                fill=COLOR_TEXT,
                font=font_label,
            )

            y += bar_height + bar_spacing

        # Save to bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def create_comfort_chart(
        self,
        data: dict[str, float],
        title: str = "Comfort Scores",
    ) -> bytes | None:
        """Create a bar chart with color-coded comfort scores.

        Args:
            data: Dictionary of zone -> comfort score (0-100)
            title: Chart title

        Returns:
            PNG image as bytes, or None if generation failed
        """
        if not self._pillow_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        if not data:
            return None

        # Create image
        img = Image.new("RGB", (self.width, self.height), COLOR_BACKGROUND)
        draw = ImageDraw.Draw(img)

        # Try to load a font
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except (OSError, IOError):
            font_title = ImageFont.load_default()
            font_label = ImageFont.load_default()

        # Layout
        margin = 20
        title_height = 30
        bar_height = 25
        bar_spacing = 10
        label_width = 120
        value_width = 60

        # Draw title
        draw.text((margin, margin), title, fill=COLOR_TEXT, font=font_title)

        # Calculate bar area
        bar_area_top = margin + title_height
        bar_area_left = margin + label_width
        bar_area_width = self.width - margin * 2 - label_width - value_width

        # Draw bars with color coding
        y = bar_area_top
        for label, score in data.items():
            # Truncate label
            display_label = label[:15] + "..." if len(label) > 15 else label

            # Draw label
            draw.text((margin, y + 5), display_label, fill=COLOR_TEXT, font=font_label)

            # Calculate bar width (score is 0-100)
            bar_width = int((score / 100.0) * bar_area_width)
            bar_width = max(0, min(bar_width, bar_area_width))

            # Get color based on score
            bar_color = _get_comfort_color(score)

            # Draw bar
            draw.rectangle(
                [bar_area_left, y, bar_area_left + bar_width, y + bar_height],
                fill=bar_color,
            )

            # Draw score value
            draw.text(
                (bar_area_left + bar_area_width + 10, y + 5),
                f"{score:.0f}",
                fill=COLOR_TEXT,
                font=font_label,
            )

            y += bar_height + bar_spacing

        # Save to bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def create_comparison_chart(
        self,
        current: dict[str, float],
        previous: dict[str, float],
        title: str,
        unit: str = "",
    ) -> bytes | None:
        """Create a side-by-side comparison bar chart.

        Args:
            current: Current week's values
            previous: Previous week's values
            title: Chart title
            unit: Unit for values

        Returns:
            PNG image as bytes, or None if generation failed
        """
        if not self._pillow_available:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        if not current:
            return None

        # Create image
        img = Image.new("RGB", (self.width, self.height), COLOR_BACKGROUND)
        draw = ImageDraw.Draw(img)

        # Try to load a font
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except (OSError, IOError):
            font_title = ImageFont.load_default()
            font_label = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Layout
        margin = 20
        title_height = 30
        legend_height = 20
        bar_height = 12
        group_spacing = 15
        label_width = 100

        # Draw title
        draw.text((margin, margin), title, fill=COLOR_TEXT, font=font_title)

        # Draw legend
        legend_y = margin + title_height
        draw.rectangle([margin, legend_y, margin + 15, legend_y + 10], fill=COLOR_BAR_PRIMARY)
        draw.text((margin + 20, legend_y - 2), "This week", fill=COLOR_TEXT, font=font_small)
        draw.rectangle([margin + 100, legend_y, margin + 115, legend_y + 10], fill=COLOR_BAR_SECONDARY)
        draw.text((margin + 120, legend_y - 2), "Last week", fill=COLOR_TEXT, font=font_small)

        # Calculate bar area
        bar_area_top = legend_y + legend_height + 10
        bar_area_left = margin + label_width
        bar_area_width = self.width - margin * 2 - label_width - 60

        # Determine scale
        all_values = list(current.values()) + list(previous.values())
        max_value = max(all_values) if all_values else 100
        if max_value <= 0:
            max_value = 100

        # Draw grouped bars
        y = bar_area_top
        for label in current.keys():
            # Truncate label
            display_label = label[:12] + "..." if len(label) > 12 else label

            # Draw label
            draw.text((margin, y + 5), display_label, fill=COLOR_TEXT, font=font_label)

            # Current week bar
            curr_value = current.get(label, 0)
            curr_width = int((curr_value / max_value) * bar_area_width)
            draw.rectangle(
                [bar_area_left, y, bar_area_left + curr_width, y + bar_height],
                fill=COLOR_BAR_PRIMARY,
            )

            # Previous week bar (below)
            prev_value = previous.get(label, 0)
            prev_width = int((prev_value / max_value) * bar_area_width)
            draw.rectangle(
                [bar_area_left, y + bar_height + 2, bar_area_left + prev_width, y + bar_height * 2 + 2],
                fill=COLOR_BAR_SECONDARY,
            )

            # Draw values
            draw.text(
                (bar_area_left + bar_area_width + 5, y),
                f"{curr_value:.1f}{unit}",
                fill=COLOR_TEXT,
                font=font_small,
            )
            draw.text(
                (bar_area_left + bar_area_width + 5, y + bar_height + 2),
                f"{prev_value:.1f}{unit}",
                fill=COLOR_TEXT,
                font=font_small,
            )

            y += bar_height * 2 + group_spacing

        # Save to bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


async def save_chart_to_www(
    hass: HomeAssistant,
    chart_bytes: bytes,
    filename: str,
) -> str | None:
    """Save chart image to www directory.

    Args:
        hass: Home Assistant instance
        chart_bytes: PNG image bytes
        filename: Filename (without path)

    Returns:
        URL path for the image (/local/adaptive_thermostat/filename), or None on error
    """
    try:
        # Get www directory path
        www_dir = Path(hass.config.path("www")) / "adaptive_thermostat"

        # Create directory if needed
        www_dir.mkdir(parents=True, exist_ok=True)

        # Write file
        file_path = www_dir / filename
        file_path.write_bytes(chart_bytes)

        _LOGGER.debug("Chart saved to %s", file_path)

        # Return URL path
        return f"/local/adaptive_thermostat/{filename}"

    except (OSError, IOError) as e:
        _LOGGER.error("Failed to save chart: %s", e)
        return None


async def cleanup_old_charts(
    hass: HomeAssistant,
    keep_weeks: int = 4,
) -> None:
    """Remove chart images older than keep_weeks.

    Args:
        hass: Home Assistant instance
        keep_weeks: Number of weeks of charts to keep
    """
    try:
        from datetime import datetime, timedelta

        www_dir = Path(hass.config.path("www")) / "adaptive_thermostat"
        if not www_dir.exists():
            return

        cutoff = datetime.now() - timedelta(weeks=keep_weeks)

        for file_path in www_dir.glob("weekly_*.png"):
            try:
                # Get file modification time
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime < cutoff:
                    file_path.unlink()
                    _LOGGER.debug("Removed old chart: %s", file_path)
            except (OSError, IOError) as e:
                _LOGGER.warning("Failed to remove old chart %s: %s", file_path, e)

    except Exception as e:
        _LOGGER.error("Failed to cleanup old charts: %s", e)
