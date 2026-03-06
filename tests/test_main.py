"""Тесты для jenkins_logs_parser.main."""

import configparser
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import jenkins

from jenkins_logs_parser.main import (
    create_default_config,
    create_jenkins_server,
    get_job_build_history,
    get_logs,
    parse_build_numbers,
    save_logs_to_file,
    show_logs_in_lnav,
)


# ---------------------------------------------------------------------------
# create_default_config
# ---------------------------------------------------------------------------

class TestCreateDefaultConfig(unittest.TestCase):

    def test_has_required_sections(self):
        config = create_default_config()
        self.assertIn('jenkins', config)
        self.assertIn('logs', config)
        self.assertIn('proxy', config)

    def test_jenkins_defaults(self):
        config = create_default_config()
        self.assertIn('url', config['jenkins'])
        self.assertIn('username', config['jenkins'])
        self.assertIn('token', config['jenkins'])
        self.assertEqual(config['jenkins']['token'], '')

    def test_proxy_url_empty_by_default(self):
        config = create_default_config()
        self.assertEqual(config['proxy']['url'], '')


# ---------------------------------------------------------------------------
# create_jenkins_server
# ---------------------------------------------------------------------------

def _make_config(token='mytoken', proxy_url=''):
    config = configparser.ConfigParser()
    config['jenkins'] = {
        'url': 'https://jenkins.example.com',
        'username': 'user',
        'token': token,
    }
    config['proxy'] = {'url': proxy_url}
    return config


class TestCreateJenkinsServer(unittest.TestCase):

    def test_raises_when_token_missing(self):
        config = _make_config(token='')
        with self.assertRaises(ValueError):
            create_jenkins_server(config)

    @patch('jenkins_logs_parser.main.jenkins.Jenkins')
    def test_ssl_verification_disabled(self, mock_jenkins_cls):
        mock_server = MagicMock()
        mock_jenkins_cls.return_value = mock_server
        config = _make_config()
        create_jenkins_server(config)
        self.assertFalse(mock_server._session.verify)

    @patch('jenkins_logs_parser.main.jenkins.Jenkins')
    def test_no_proxy_when_not_configured(self, mock_jenkins_cls):
        mock_server = MagicMock()
        mock_jenkins_cls.return_value = mock_server
        config = _make_config(proxy_url='')
        create_jenkins_server(config)
        # proxies should not have been set explicitly
        self.assertNotIn(
            call._session.__setitem__,
            mock_server.mock_calls,
        )
        # Verify that .proxies was never assigned (attribute not set to a dict)
        self.assertNotIsInstance(
            mock_server._session.proxies,
            dict,
        )

    @patch('jenkins_logs_parser.main.jenkins.Jenkins')
    def test_proxy_configured_from_config(self, mock_jenkins_cls):
        mock_server = MagicMock()
        mock_jenkins_cls.return_value = mock_server
        config = _make_config(proxy_url='http://proxy.example.com:3128')
        create_jenkins_server(config)
        self.assertEqual(
            mock_server._session.proxies,
            {
                'http': 'http://proxy.example.com:3128',
                'https': 'http://proxy.example.com:3128',
            },
        )

    @patch('jenkins_logs_parser.main.jenkins.Jenkins')
    def test_get_version_called(self, mock_jenkins_cls):
        mock_server = MagicMock()
        mock_jenkins_cls.return_value = mock_server
        config = _make_config()
        create_jenkins_server(config)
        mock_server.get_version.assert_called_once()


# ---------------------------------------------------------------------------
# get_job_build_history
# ---------------------------------------------------------------------------

class TestGetJobBuildHistory(unittest.TestCase):

    def test_returns_set_of_build_numbers(self):
        server = MagicMock()
        server.get_job_info.return_value = {
            'builds': [{'number': 1}, {'number': 2}, {'number': 3}]
        }
        result = get_job_build_history(server, 'my-job')
        self.assertEqual(result, {1, 2, 3})

    def test_raises_on_not_found(self):
        server = MagicMock()
        server.get_job_info.side_effect = jenkins.NotFoundException()
        with self.assertRaises(ValueError):
            get_job_build_history(server, 'missing-job')


# ---------------------------------------------------------------------------
# parse_build_numbers
# ---------------------------------------------------------------------------

class TestParseBuildNumbers(unittest.TestCase):

    def _make_server(self, builds):
        server = MagicMock()
        server.get_job_info.return_value = {
            'builds': [{'number': n} for n in builds]
        }
        return server

    def test_latest_returns_max(self):
        server = self._make_server([1, 2, 5, 3])
        result = parse_build_numbers('latest', 'job', server)
        self.assertEqual(result, [5])

    def test_single_build(self):
        server = self._make_server([1, 2, 3])
        result = parse_build_numbers('2', 'job', server)
        self.assertEqual(result, [2])

    def test_comma_separated(self):
        server = self._make_server([1, 2, 3, 4])
        result = parse_build_numbers('1,3', 'job', server)
        self.assertEqual(result, [3, 1])

    def test_range(self):
        server = self._make_server(range(1, 11))
        result = parse_build_numbers('3-5', 'job', server)
        self.assertEqual(result, [5, 4, 3])

    def test_reverse_range_handled(self):
        server = self._make_server(range(1, 11))
        result = parse_build_numbers('5-3', 'job', server)
        self.assertEqual(result, [5, 4, 3])

    def test_mixed_comma_and_range(self):
        server = self._make_server(range(1, 11))
        result = parse_build_numbers('1,3-5', 'job', server)
        self.assertEqual(result, [5, 4, 3, 1])

    def test_invalid_build_raises(self):
        server = self._make_server([1, 2, 3])
        with self.assertRaises(ValueError):
            parse_build_numbers('99', 'job', server)

    def test_invalid_range_format_raises(self):
        server = self._make_server([1, 2, 3])
        with self.assertRaises(ValueError):
            parse_build_numbers('a-b', 'job', server)

    def test_non_numeric_raises(self):
        server = self._make_server([1, 2, 3])
        with self.assertRaises(ValueError):
            parse_build_numbers('abc', 'job', server)

    def test_empty_builds_raises(self):
        server = MagicMock()
        server.get_job_info.return_value = {'builds': []}
        with self.assertRaises(ValueError):
            parse_build_numbers('latest', 'job', server)


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------

class TestGetLogs(unittest.TestCase):

    def test_returns_logs_for_builds(self):
        server = MagicMock()
        server.get_build_console_output.side_effect = lambda job, num: f"log-{num}"
        result = get_logs(server, 'job', [1, 2])
        self.assertEqual(result, ['log-1', 'log-2'])

    def test_skips_not_found_build(self):
        server = MagicMock()

        def side_effect(job, num):
            if num == 2:
                raise jenkins.NotFoundException()
            return f"log-{num}"

        server.get_build_console_output.side_effect = side_effect
        result = get_logs(server, 'job', [1, 2, 3])
        self.assertEqual(result, ['log-1', 'log-3'])


# ---------------------------------------------------------------------------
# save_logs_to_file
# ---------------------------------------------------------------------------

class TestSaveLogsToFile(unittest.TestCase):

    def test_saves_combined_logs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            save_logs_to_file(['line1', 'line2'], 'prefix/job', tmpdir)
            log_file = Path(tmpdir) / 'prefix' / 'prefix_job.log'
            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding='utf-8')
            self.assertIn('line1', content)
            self.assertIn('line2', content)
            self.assertIn('--- END OF BUILD ---', content)

    def test_no_logs_does_not_create_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            save_logs_to_file([], 'prefix/job', tmpdir)
            log_file = Path(tmpdir) / 'prefix' / 'prefix_job.log'
            self.assertFalse(log_file.exists())

    def test_job_without_prefix_uses_other(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            save_logs_to_file(['data'], 'standalone-job', tmpdir)
            log_file = Path(tmpdir) / 'other' / 'standalone-job.log'
            self.assertTrue(log_file.exists())


# ---------------------------------------------------------------------------
# show_logs_in_lnav (Linux/macOS path)
# ---------------------------------------------------------------------------

@unittest.skipIf(sys.platform == 'win32', 'Unix-specific test')
class TestShowLogsInLnavUnix(unittest.TestCase):

    @patch('jenkins_logs_parser.main.subprocess.run')
    def test_calls_lnav_with_stdin(self, mock_run):
        show_logs_in_lnav(['log line 1', 'log line 2'])
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ['lnav', '-'])
        self.assertIn(b'log line 1', kwargs['input'])
        self.assertIn(b'log line 2', kwargs['input'])

    @patch('jenkins_logs_parser.main.subprocess.run',
           side_effect=FileNotFoundError)
    def test_lnav_not_found_prints_error(self, _mock_run):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_logs_in_lnav(['log'])
        self.assertIn('lnav', buf.getvalue())

    @patch('jenkins_logs_parser.main.subprocess.run')
    def test_empty_logs_skips_lnav(self, mock_run):
        show_logs_in_lnav([])
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# show_logs_in_lnav (Windows path)
# ---------------------------------------------------------------------------

class TestShowLogsInLnavWindows(unittest.TestCase):

    @patch('jenkins_logs_parser.main.sys')
    @patch('jenkins_logs_parser.main.subprocess.run')
    @patch('jenkins_logs_parser.main.Path.unlink')
    def test_windows_opens_notepad(self, mock_unlink, mock_run, mock_sys):
        mock_sys.platform = 'win32'
        show_logs_in_lnav(['hello windows'])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], 'notepad.exe')
        self.assertTrue(cmd[1].endswith('.log'))

    @patch('jenkins_logs_parser.main.sys')
    @patch('jenkins_logs_parser.main.subprocess.run')
    @patch('jenkins_logs_parser.main.Path.unlink')
    def test_windows_empty_logs(self, mock_unlink, mock_run, mock_sys):
        mock_sys.platform = 'win32'
        show_logs_in_lnav([])
        mock_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
