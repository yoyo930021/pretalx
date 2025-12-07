import io
import base64

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView
from django_context_decorator import context

from pretalx.cfp.views.event import LoggedInEventPageMixin
from pretalx.person.forms.otp import OTPDisableForm, OTPSetupForm
from pretalx.person.models import OTPDevice


@method_decorator(login_required, name="dispatch")
class OTPManageView(LoggedInEventPageMixin, TemplateView):
    """
    View for managing two-factor authentication settings.

    Shows current 2FA status and allows users to enable/disable it.
    """
    template_name = "cfp/event/user_otp_manage.html"

    @context
    @cached_property
    def otp_device(self):
        """Get or create OTP device for current user."""
        try:
            return self.request.user.otp_device
        except OTPDevice.DoesNotExist:
            return None

    @context
    def otp_enabled(self):
        """Check if OTP is currently enabled."""
        return self.otp_device and self.otp_device.enabled

    @context
    def has_backup_codes(self):
        """Check if user has backup codes."""
        return self.otp_device and len(self.otp_device.backup_codes) > 0


@method_decorator(login_required, name="dispatch")
class OTPSetupView(LoggedInEventPageMixin, FormView):
    """
    View for setting up two-factor authentication.

    Generates QR code and verifies initial setup.
    """
    template_name = "cfp/event/user_otp_setup.html"
    form_class = OTPSetupForm

    def dispatch(self, request, *args, **kwargs):
        # Check if OTP is already enabled
        try:
            if request.user.otp_device.enabled:
                messages.info(request, _("Two-factor authentication is already enabled."))
                return redirect("cfp:event.user.otp", event=request.event.slug)
        except OTPDevice.DoesNotExist:
            pass
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def otp_device(self):
        """Get or create OTP device for setup."""
        device, created = OTPDevice.objects.get_or_create(user=self.request.user)
        if created or not device.secret:
            # Generate new secret if device is new or doesn't have a secret
            import pyotp
            device.secret = pyotp.random_base32()
            device.save()
        return device

    @context
    def provisioning_uri(self):
        """Get the provisioning URI for QR code."""
        event_name = getattr(self.request, 'event', None)
        issuer_name = f"pretalx - {event_name.name}" if event_name else "pretalx"
        return self.otp_device.get_provisioning_uri(issuer_name=issuer_name)

    @context
    def qr_code_data(self):
        """Generate QR code as base64-encoded PNG."""
        # Create QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(self.provisioning_uri)
        qr.make(fit=True)

        # Generate image
        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.read()).decode()

        return f"data:image/png;base64,{img_base64}"

    @context
    def secret_key(self):
        """Get the secret key for manual entry."""
        return self.otp_device.secret

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['otp_device'] = self.otp_device
        return kwargs

    def form_valid(self, form):
        # Enable OTP and generate backup codes
        backup_codes = form.save()

        # Store backup codes in session for one-time display
        self.request.session['new_backup_codes'] = backup_codes
        self.request.session['backup_codes_event'] = self.request.event.slug

        messages.success(
            self.request,
            _("Two-factor authentication has been enabled successfully!")
        )
        self.request.user.log_action("pretalx.user.otp.enable")

        return redirect("cfp:event.user.otp.backup_codes", event=self.request.event.slug)


@method_decorator(login_required, name="dispatch")
class OTPBackupCodesView(LoggedInEventPageMixin, TemplateView):
    """
    View to display backup codes after setup or regeneration.

    Shows codes only once from session, then clears them.
    """
    template_name = "cfp/event/user_otp_backup_codes.html"

    def dispatch(self, request, *args, **kwargs):
        # Check if user has OTP enabled
        try:
            if not request.user.otp_device.enabled:
                messages.error(request, _("Two-factor authentication is not enabled."))
                return redirect("cfp:event.user.otp", event=request.event.slug)
        except OTPDevice.DoesNotExist:
            messages.error(request, _("Two-factor authentication is not set up."))
            return redirect("cfp:event.user.otp", event=request.event.slug)

        # Check if backup codes are in session
        if 'new_backup_codes' not in request.session:
            messages.info(request, _("Backup codes have already been displayed."))
            return redirect("cfp:event.user.otp", event=request.event.slug)

        return super().dispatch(request, *args, **kwargs)

    @context
    def backup_codes(self):
        """Get backup codes from session."""
        codes = self.request.session.get('new_backup_codes', [])
        # Format codes for better readability (groups of 4)
        return [f"{code[:4]}-{code[4:]}" if len(code) == 8 else code for code in codes]

    def post(self, request, *args, **kwargs):
        """Clear backup codes from session after user confirms."""
        if 'new_backup_codes' in request.session:
            del request.session['new_backup_codes']
        if 'backup_codes_event' in request.session:
            del request.session['backup_codes_event']

        messages.success(request, _("You can now use two-factor authentication."))
        return redirect("cfp:event.user.view", event=request.event.slug)


@method_decorator(login_required, name="dispatch")
class OTPRegenerateBackupCodesView(LoggedInEventPageMixin, TemplateView):
    """
    View to regenerate backup codes.
    """
    template_name = "cfp/event/user_otp_regenerate.html"

    def dispatch(self, request, *args, **kwargs):
        # Check if OTP is enabled
        try:
            if not request.user.otp_device.enabled:
                messages.error(request, _("Two-factor authentication is not enabled."))
                return redirect("cfp:event.user.otp", event=request.event.slug)
        except OTPDevice.DoesNotExist:
            messages.error(request, _("Two-factor authentication is not set up."))
            return redirect("cfp:event.user.otp", event=request.event.slug)

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Regenerate backup codes."""
        otp_device = request.user.otp_device
        new_codes = otp_device.generate_backup_codes()

        # Store in session for display
        request.session['new_backup_codes'] = new_codes
        request.session['backup_codes_event'] = request.event.slug

        messages.success(request, _("New backup codes have been generated."))
        request.user.log_action("pretalx.user.otp.backup_codes.regenerate")

        return redirect("cfp:event.user.otp.backup_codes", event=request.event.slug)


@method_decorator(login_required, name="dispatch")
class OTPDisableView(LoggedInEventPageMixin, FormView):
    """
    View for disabling two-factor authentication.

    Requires password confirmation for security.
    """
    template_name = "cfp/event/user_otp_disable.html"
    form_class = OTPDisableForm

    def dispatch(self, request, *args, **kwargs):
        # Check if OTP is enabled
        try:
            if not request.user.otp_device.enabled:
                messages.info(request, _("Two-factor authentication is not enabled."))
                return redirect("cfp:event.user.otp", event=request.event.slug)
        except OTPDevice.DoesNotExist:
            messages.info(request, _("Two-factor authentication is not set up."))
            return redirect("cfp:event.user.otp", event=request.event.slug)

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        # Disable OTP
        form.save()

        messages.success(
            self.request,
            _("Two-factor authentication has been disabled.")
        )
        self.request.user.log_action("pretalx.user.otp.disable")

        return redirect("cfp:event.user.otp", event=self.request.event.slug)
