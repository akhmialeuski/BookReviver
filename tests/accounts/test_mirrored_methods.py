"""Tests that the methods of ``BaseUserManager`` that ``UserManager`` mirrors are the ones it was written against."""

import hashlib
import inspect

import pytest
from fastapi_users import BaseUserManager

# SHA-256 of the source of each method in fastapi-users 15.0.5, which ``UserManager`` copies step by step so that the
# hashing of a password runs in a worker thread
MIRRORED_METHODS: dict[str, str] = {
    'create': '3d1328456778ffce42a3ec59f4ba5611468084c7336a533d07da4573df472cb7',
    'oauth_callback': 'e61f264ed933951e21d329de1dadf1dbf3b511d848ecf7f6046e13e54842135c',
    'forgot_password': '785379634e5059ec0baccd44c8fdd98fd0034bb5fb5952b733db62f73a456595',
    'reset_password': 'f0c5df8b888017290be39b6cb3ea30567d28392e573a069875972729af73cb3e',
    'authenticate': 'e453ea340730cf50c47dfa131f0708706fcaaf1d196c5dcb93c07dc023903188',
    '_update': '1c49e7a79f770662558dc24d0ae60074ff4c03b60f83e23e82a02bc75f34936b',
}


class TestMirroredMethods:
    """Tests for the source of the methods of ``BaseUserManager`` that ``UserManager`` mirrors."""

    @pytest.mark.parametrize('method', MIRRORED_METHODS)
    def test_upstream_method_is_unchanged(self, method: str) -> None:
        """Verify the installed fastapi-users has the source of the method that ``UserManager`` was compared with.

        A failure means fastapi-users changed the method: compare it with the override in ``UserManager`` again, carry
        over what changed, and put the new hash in ``MIRRORED_METHODS``.

        :param method: Name of the method of ``BaseUserManager``.
        :type method: str
        """
        source = inspect.getsource(getattr(BaseUserManager, method))

        digest = hashlib.sha256(source.encode()).hexdigest()

        assert digest == MIRRORED_METHODS[method], (
            f'BaseUserManager.{method} of fastapi-users differs from the one UserManager.{method} mirrors: '
            f'compare the two again, then record {digest}'
        )
