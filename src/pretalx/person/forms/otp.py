from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from pretalx.person.models import OTPDevice, User


class OTPLoginForm(forms.Form):
    """
    Form for OTP verification during login.

    This form is displayed as part of the login flow when the user
    has two-factor authentication enabled.
    """

    label = _("Two-Factor Authentication")

    otp_token = forms.CharField(
        max_length=6,
        min_length=6,
        label=_("Authentication Code"),
        required=True,
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
            'placeholder': '000000',
            'class': 'form-control',
        }),
        help_text=_("Enter the 6-digit code from your authenticator app"),
    )

    use_backup_code = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.otp_device = None

        if user:
            try:
                self.otp_device = user.otp_device
            except OTPDevice.DoesNotExist:
                pass

    def clean_otp_token(self):
        """Validate the OTP token."""
        token = self.cleaned_data.get('otp_token', '').strip()

        if not self.user or not self.otp_device:
            raise ValidationError(_("OTP verification not available for this user."))

        if not self.otp_device.enabled:
            raise ValidationError(_("Two-factor authentication is not enabled for this account."))

        # Check if using backup code
        use_backup = self.data.get('use_backup_code') == 'true'

        if use_backup:
            # Verify backup code
            if not self.otp_device.verify_backup_code(token):
                raise ValidationError(_("Invalid backup code."))
        else:
            # Verify TOTP
            if not self.otp_device.verify_token(token):
                raise ValidationError(
                    _("Invalid authentication code. Please check your authenticator app and try again.")
                )

        return token

    def save(self):
        """Record OTP usage."""
        if self.otp_device:
            self.otp_device.last_used = timezone.now()
            self.otp_device.save(update_fields=['last_used'])


class OTPSetupForm(forms.Form):
    """
    Form for setting up OTP two-factor authentication.

    Used when a user wants to enable 2FA for their account.
    """

    verification_code = forms.CharField(
        max_length=6,
        min_length=6,
        label=_("Verification Code"),
        required=True,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
            'placeholder': '000000',
            'class': 'form-control',
        }),
        help_text=_("Enter the 6-digit code from your authenticator app to verify the setup"),
    )

    def __init__(self, *args, otp_device=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.otp_device = otp_device

    def clean_verification_code(self):
        """Verify the setup token."""
        code = self.cleaned_data.get('verification_code', '').strip()

        if not self.otp_device:
            raise ValidationError(_("No OTP device found for verification."))

        if not self.otp_device.verify_token(code):
            raise ValidationError(
                _("Verification failed. Please ensure you scanned the QR code correctly.")
            )

        return code

    def save(self):
        """Enable the OTP device and generate backup codes."""
        if self.otp_device:
            self.otp_device.enabled = True
            self.otp_device.save(update_fields=['enabled'])
            # Generate backup codes
            return self.otp_device.generate_backup_codes()
        return []


class OTPDisableForm(forms.Form):
    """
    Form for disabling OTP two-factor authentication.

    Requires password confirmation for security.
    """

    password = forms.CharField(
        label=_("Password"),
        required=True,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password',
            'class': 'form-control',
        }),
        help_text=_("Enter your password to confirm disabling two-factor authentication"),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        """Verify the user's password."""
        password = self.cleaned_data.get('password')

        if not self.user:
            raise ValidationError(_("User not found."))

        if not self.user.check_password(password):
            raise ValidationError(_("Incorrect password."))

        return password

    def save(self):
        """Disable the OTP device."""
        if self.user:
            try:
                otp_device = self.user.otp_device
                otp_device.enabled = False
                otp_device.backup_codes = []
                otp_device.save(update_fields=['enabled', 'backup_codes'])
            except OTPDevice.DoesNotExist:
                pass
