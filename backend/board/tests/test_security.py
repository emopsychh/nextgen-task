"""Security-focused tests: signed attachment URLs, stream tokens, SSRF host guard."""

from __future__ import annotations

from types import SimpleNamespace

from django.core import signing
from django.test import TestCase, override_settings

from board.serializers import ATTACHMENT_SIGN_SALT, sign_attachment_id
from board.stream import STREAM_TOKEN_SALT, mint_stream_token
from portals.bitrix import _host_is_public


class AttachmentSignatureTests(TestCase):
    def test_round_trip(self):
        token = sign_attachment_id(42)
        loaded = signing.loads(token, salt=ATTACHMENT_SIGN_SALT, max_age=3600)
        self.assertEqual(loaded, 42)

    def test_tampered_token_rejected(self):
        token = sign_attachment_id(42)
        tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        with self.assertRaises(signing.BadSignature):
            signing.loads(tampered, salt=ATTACHMENT_SIGN_SALT, max_age=3600)

    def test_wrong_salt_rejected(self):
        token = sign_attachment_id(42)
        with self.assertRaises(signing.BadSignature):
            signing.loads(token, salt="some.other.salt", max_age=3600)

    def test_expired_token_rejected(self):
        token = sign_attachment_id(42)
        with self.assertRaises(signing.SignatureExpired):
            signing.loads(token, salt=ATTACHMENT_SIGN_SALT, max_age=-1)


class StreamTokenTests(TestCase):
    def _user(self):
        return SimpleNamespace(portal_id=3, bitrix_user=SimpleNamespace(bitrix_id="77"))

    def test_mint_round_trip(self):
        token = mint_stream_token(self._user())
        data = signing.loads(token, salt=STREAM_TOKEN_SALT, max_age=3600)
        self.assertEqual(data, {"p": 3, "u": "77"})

    def test_stream_token_wrong_salt_rejected(self):
        token = mint_stream_token(self._user())
        with self.assertRaises(signing.BadSignature):
            signing.loads(token, salt=ATTACHMENT_SIGN_SALT, max_age=3600)


class SsrfHostGuardTests(TestCase):
    def test_public_fqdn_allowed(self):
        self.assertTrue(_host_is_public("acme.bitrix24.ru"))

    def test_public_ip_allowed(self):
        self.assertTrue(_host_is_public("8.8.8.8"))

    def test_localhost_blocked(self):
        self.assertFalse(_host_is_public("localhost"))

    def test_loopback_ip_blocked(self):
        self.assertFalse(_host_is_public("127.0.0.1"))

    def test_private_ranges_blocked(self):
        for host in ("10.0.0.5", "192.168.1.1", "172.16.0.1"):
            self.assertFalse(_host_is_public(host), host)

    def test_link_local_metadata_blocked(self):
        self.assertFalse(_host_is_public("169.254.169.254"))

    def test_internal_tlds_blocked(self):
        self.assertFalse(_host_is_public("db.internal"))
        self.assertFalse(_host_is_public("service.local"))

    def test_bare_hostname_without_dot_blocked(self):
        self.assertFalse(_host_is_public("intranet"))

    def test_empty_blocked(self):
        self.assertFalse(_host_is_public(""))
