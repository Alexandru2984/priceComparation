from tempfile import TemporaryDirectory

from django.test import override_settings
from django.test.runner import DiscoverRunner


class IsolatedMediaTestRunner(DiscoverRunner):
    """Keep uploaded test fixtures out of the private production media directory."""

    def setup_test_environment(self, **kwargs):
        self._media_directory = TemporaryDirectory(prefix="pricematch-test-media-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_directory.name)
        self._media_override.enable()
        try:
            return super().setup_test_environment(**kwargs)
        except BaseException:
            self._media_override.disable()
            self._media_directory.cleanup()
            raise

    def teardown_test_environment(self, **kwargs):
        try:
            return super().teardown_test_environment(**kwargs)
        finally:
            self._media_override.disable()
            self._media_directory.cleanup()
