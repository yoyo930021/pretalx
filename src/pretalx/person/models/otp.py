import secrets

import pyotp
from django.db import models
from django.utils.translation import gettext_lazy as _


class OTPDevice(models.Model):
    """
    Two-Factor Authentication device using Time-based One-Time Passwords (TOTP).

    This model stores the secret key for generating OTPs compatible with
    authenticator apps like Google Authenticator, Authy, etc.
    """

    user = models.OneToOneField(
        to="person.User",
        on_delete=models.CASCADE,
        related_name="otp_device",
        verbose_name=_("User"),
    )
    secret = models.CharField(
        max_length=32,
        verbose_name=_("Secret Key"),
        help_text=_("Base32-encoded secret key for TOTP generation"),
    )
    enabled = models.BooleanField(
        default=False,
        verbose_name=_("Enabled"),
        help_text=_("Whether two-factor authentication is active for this user"),
    )
    created = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Created"),
    )
    last_used = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Used"),
    )
    backup_codes = models.JSONField(
        default=list,
        verbose_name=_("Backup Codes"),
        help_text=_("One-time backup codes for account recovery"),
    )

    class Meta:
        verbose_name = _("OTP Device")
        verbose_name_plural = _("OTP Devices")

    def __str__(self):
        return f"OTP Device for {self.user.email}"

    @classmethod
    def create_for_user(cls, user):
        """Create a new OTP device with a random secret for the given user."""
        secret = pyotp.random_base32()
        return cls.objects.create(user=user, secret=secret)

    def verify_token(self, token, valid_window=1):
        """
        Verify a TOTP token.

        Args:
            token: The 6-digit token to verify
            valid_window: Number of intervals before/after to check (default: 1)
                         Allows for minor clock drift

        Returns:
            bool: True if token is valid, False otherwise
        """
        if not token or not self.enabled:
            return False

        totp = pyotp.TOTP(self.secret)
        return totp.verify(token, valid_window=valid_window)

    def verify_backup_code(self, code):
        """
        Verify and consume a backup code.

        Args:
            code: The backup code to verify

        Returns:
            bool: True if code is valid and consumed, False otherwise
        """
        if not code or code not in self.backup_codes:
            return False

        # Remove the used backup code
        self.backup_codes.remove(code)
        self.save(update_fields=['backup_codes'])
        return True

    def get_provisioning_uri(self, issuer_name="pretalx"):
        """
        Get the provisioning URI for QR code generation.

        Args:
            issuer_name: Name to display in authenticator app

        Returns:
            str: otpauth:// URI that can be encoded as QR code
        """
        totp = pyotp.TOTP(self.secret)
        return totp.provisioning_uri(
            name=self.user.email,
            issuer_name=issuer_name
        )

    def generate_backup_codes(self, count=10):
        """
        Generate new backup codes.

        Args:
            count: Number of backup codes to generate (default: 10)

        Returns:
            list: The generated backup codes
        """
        self.backup_codes = [
            secrets.token_hex(4).upper()  # 8-character hex codes
            for _ in range(count)
        ]
        self.save(update_fields=['backup_codes'])
        return self.backup_codes

    def get_current_token(self):
        """
        Get the current valid TOTP token. Useful for testing/debugging only.
        DO NOT expose this in production!
        """
        totp = pyotp.TOTP(self.secret)
        return totp.now()
