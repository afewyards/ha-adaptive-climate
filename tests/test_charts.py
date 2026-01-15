"""Tests for chart generation."""
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from custom_components.adaptive_thermostat.analytics.charts import (
    ChartGenerator,
    save_chart_to_www,
    cleanup_old_charts,
    _get_comfort_color,
    COLOR_BAR_SUCCESS,
    COLOR_BAR_WARNING,
    COLOR_BAR_SECONDARY,
)


def test_get_comfort_color_good():
    """Test comfort color for good scores (>= 80)."""
    assert _get_comfort_color(80) == COLOR_BAR_SUCCESS
    assert _get_comfort_color(95) == COLOR_BAR_SUCCESS
    assert _get_comfort_color(100) == COLOR_BAR_SUCCESS


def test_get_comfort_color_ok():
    """Test comfort color for OK scores (60-79)."""
    assert _get_comfort_color(60) == COLOR_BAR_WARNING
    assert _get_comfort_color(70) == COLOR_BAR_WARNING
    assert _get_comfort_color(79) == COLOR_BAR_WARNING


def test_get_comfort_color_poor():
    """Test comfort color for poor scores (< 60)."""
    assert _get_comfort_color(0) == COLOR_BAR_SECONDARY
    assert _get_comfort_color(30) == COLOR_BAR_SECONDARY
    assert _get_comfort_color(59) == COLOR_BAR_SECONDARY


def test_chart_generator_without_pillow():
    """Test ChartGenerator handles missing Pillow gracefully."""
    with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
        # Force re-check of Pillow availability
        gen = ChartGenerator()
        gen._pillow_available = False

        assert gen.available is False

        # Should return None when Pillow not available
        result = gen.create_bar_chart({"zone": 50.0}, "Test", "%")
        assert result is None


def test_chart_generator_bar_chart():
    """Test bar chart generation with mocked Pillow."""
    # Create mock Image and ImageDraw
    mock_image = MagicMock()
    mock_draw = MagicMock()
    mock_buffer = MagicMock()

    with patch("custom_components.adaptive_thermostat.analytics.charts.ChartGenerator._check_pillow", return_value=True):
        gen = ChartGenerator()
        gen._pillow_available = True

        with patch("PIL.Image.new", return_value=mock_image) as mock_new:
            with patch("PIL.ImageDraw.Draw", return_value=mock_draw):
                with patch("PIL.ImageFont.truetype", side_effect=OSError):
                    with patch("PIL.ImageFont.load_default") as mock_font:
                        # Call create_bar_chart
                        data = {
                            "Living Room": 45.5,
                            "Bedroom": 30.2,
                            "Kitchen": 55.8,
                        }

                        result = gen.create_bar_chart(data, "Zone Activity", "%", max_value=100)

                        # Verify image was created with correct dimensions
                        mock_new.assert_called_once()
                        assert mock_new.call_args[0][0] == "RGB"
                        assert mock_new.call_args[0][1] == (600, 300)  # Default dimensions


def test_chart_generator_empty_data():
    """Test chart generator handles empty data."""
    gen = ChartGenerator()
    gen._pillow_available = True

    # Create mock to avoid actual Pillow import
    with patch.object(gen, "_pillow_available", True):
        # Empty data should return None
        result = gen.create_bar_chart({}, "Empty Test", "%")
        assert result is None


def test_chart_generator_comfort_chart():
    """Test comfort score chart generation."""
    mock_image = MagicMock()
    mock_draw = MagicMock()

    with patch("custom_components.adaptive_thermostat.analytics.charts.ChartGenerator._check_pillow", return_value=True):
        gen = ChartGenerator()
        gen._pillow_available = True

        with patch("PIL.Image.new", return_value=mock_image):
            with patch("PIL.ImageDraw.Draw", return_value=mock_draw):
                with patch("PIL.ImageFont.truetype", side_effect=OSError):
                    with patch("PIL.ImageFont.load_default"):
                        data = {
                            "Living Room": 85.0,  # Good (green)
                            "Bedroom": 70.0,  # OK (yellow)
                            "Garage": 45.0,  # Poor (red)
                        }

                        result = gen.create_comfort_chart(data, "Comfort Scores")

                        # Verify draw calls were made for rectangles (bars)
                        assert mock_draw.rectangle.called


def test_chart_generator_comparison_chart():
    """Test comparison chart generation."""
    mock_image = MagicMock()
    mock_draw = MagicMock()

    with patch("custom_components.adaptive_thermostat.analytics.charts.ChartGenerator._check_pillow", return_value=True):
        gen = ChartGenerator()
        gen._pillow_available = True

        with patch("PIL.Image.new", return_value=mock_image):
            with patch("PIL.ImageDraw.Draw", return_value=mock_draw):
                with patch("PIL.ImageFont.truetype", side_effect=OSError):
                    with patch("PIL.ImageFont.load_default"):
                        current = {"Living Room": 45.0, "Bedroom": 30.0}
                        previous = {"Living Room": 50.0, "Bedroom": 35.0}

                        result = gen.create_comparison_chart(
                            current, previous, "Week over Week"
                        )

                        # Verify bars were drawn
                        assert mock_draw.rectangle.called


@pytest.mark.asyncio
async def test_save_chart_to_www():
    """Test saving chart to www directory."""
    mock_hass = MagicMock()
    mock_hass.config.path.return_value = "/config"

    chart_bytes = b"PNG_IMAGE_DATA"

    with patch("pathlib.Path.mkdir") as mock_mkdir:
        with patch("pathlib.Path.write_bytes") as mock_write:
            url = await save_chart_to_www(mock_hass, chart_bytes, "test_chart.png")

            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
            mock_write.assert_called_once_with(chart_bytes)
            assert url == "/local/adaptive_thermostat/test_chart.png"


@pytest.mark.asyncio
async def test_save_chart_to_www_error():
    """Test save_chart_to_www handles errors gracefully."""
    mock_hass = MagicMock()
    mock_hass.config.path.return_value = "/config"

    chart_bytes = b"PNG_IMAGE_DATA"

    with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
        url = await save_chart_to_www(mock_hass, chart_bytes, "test_chart.png")

        assert url is None


@pytest.mark.asyncio
async def test_cleanup_old_charts():
    """Test cleanup of old chart files."""
    from datetime import datetime, timedelta
    from pathlib import Path

    mock_hass = MagicMock()
    mock_hass.config.path.return_value = "/config"

    # Create mock files
    old_file = MagicMock(spec=Path)
    old_file.stat.return_value.st_mtime = (datetime.now() - timedelta(weeks=6)).timestamp()

    new_file = MagicMock(spec=Path)
    new_file.stat.return_value.st_mtime = datetime.now().timestamp()

    mock_www_dir = MagicMock(spec=Path)
    mock_www_dir.exists.return_value = True
    mock_www_dir.glob.return_value = [old_file, new_file]

    with patch("pathlib.Path.__truediv__", return_value=mock_www_dir):
        with patch("pathlib.Path.exists", return_value=True):
            await cleanup_old_charts(mock_hass, keep_weeks=4)

            # Old file should be unlinked, new file should not
            old_file.unlink.assert_called_once()
            new_file.unlink.assert_not_called()


def test_chart_dimensions():
    """Test custom chart dimensions."""
    gen = ChartGenerator(width=800, height=400)

    assert gen.width == 800
    assert gen.height == 400
