"""
Unit tests for the application updater mechanism.
"""

from unittest.mock import patch, MagicMock
from updater import (
    parse_version_tuple,
    compare_versions,
    check_for_updates,
    is_git_repo,
)


class TestVersionParsing:
    def test_parse_standard_version(self):
        assert parse_version_tuple("0.2.0") == (0, 2, 0)
        assert parse_version_tuple("v0.2.0") == (0, 2, 0)
        assert parse_version_tuple("v1.4.12") == (1, 4, 12)

    def test_compare_versions(self):
        # newer version available
        assert compare_versions("0.2.0", "0.2.1") is True
        assert compare_versions("0.2.0", "v0.3.0") is True
        assert compare_versions("v0.2.0", "1.0.0") is True

        # same or older version
        assert compare_versions("0.2.0", "0.2.0") is False
        assert compare_versions("0.2.0", "v0.2.0") is False
        assert compare_versions("0.2.5", "0.2.1") is False
        assert compare_versions("1.0.0", "0.9.9") is False


class TestCheckForUpdates:
    @patch("urllib.request.urlopen")
    def test_update_available(self, mock_urlopen):
        # Mock GitHub API response
        mock_response = MagicMock()
        mock_response.read.return_value = b"""{
            "tag_name": "v0.3.0",
            "name": "CANopen Studio v0.3.0",
            "html_url": "https://github.com/s-celles/canopen-studio/releases/tag/v0.3.0",
            "body": "Added new features and bugfixes.",
            "published_at": "2026-10-01T12:00:00Z",
            "assets": [
                {
                    "name": "CANopen-Studio-v0.3.0-Windows-Setup.exe",
                    "browser_download_url": "https://github.com/.../CANopen-Studio-v0.3.0-Windows-Setup.exe",
                    "size": 25000000
                }
            ]
        }"""
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        available, info = check_for_updates(current_version="0.2.0")
        assert available is True
        assert info is not None
        assert info["tag_name"] == "v0.3.0"
        assert info["setup_asset"] is not None
        assert info["setup_asset"]["name"] == "CANopen-Studio-v0.3.0-Windows-Setup.exe"

    @patch("urllib.request.urlopen")
    def test_already_latest_version(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"""{
            "tag_name": "v0.2.0",
            "name": "v0.2.0",
            "html_url": "https://github.com/s-celles/canopen-studio/releases/tag/v0.2.0",
            "body": "Release notes",
            "assets": []
        }"""
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        available, info = check_for_updates(current_version="0.2.0")
        assert available is False
        assert info is not None
        assert info["tag_name"] == "v0.2.0"

    @patch("urllib.request.urlopen")
    def test_network_error_handled_gracefully(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection timed out")

        available, info = check_for_updates(current_version="0.2.0")
        assert available is False
        assert info is None

    @patch("urllib.request.urlopen")
    def test_http_404_no_releases_handled_gracefully(self, mock_urlopen):
        import urllib.error

        # First call (releases/latest) raises 404
        # Second call (releases list) returns empty list b"[]"
        resp_list = MagicMock()
        resp_list.read.return_value = b"[]"
        resp_list.__enter__.return_value = resp_list

        err_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        mock_urlopen.side_effect = [err_404, resp_list]

        available, info = check_for_updates(current_version="0.2.0")
        assert available is False
        assert info is not None
        assert info.get("no_releases") is True

    @patch("urllib.request.urlopen")
    def test_http_403_rate_limit_handled_gracefully(self, mock_urlopen):
        import urllib.error

        err_403 = urllib.error.HTTPError("url", 403, "rate limit exceeded", {}, None)
        mock_urlopen.side_effect = err_403

        available, info = check_for_updates(current_version="0.2.0")
        assert available is False
        assert info is not None
        assert info.get("rate_limited") is True

    def test_is_git_repo(self):
        # In current repo, .git exists
        assert is_git_repo() is True
